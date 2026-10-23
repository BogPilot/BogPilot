"""StateStream: non-blocking side channel inside an existing process.

Host-loop cost: one config flag read (a bool), a rate check, and payload.build()
copying scalars out of the host's SubMaster. JSON encoding and the ZMQ send run
on a daemon thread fed by a tiny queue; a full queue drops the snapshot
(put_nowait), so the host loop never waits on the network or the GIL-heavy
encode. The thread also re-reads the config file every CONFIG_PERIOD seconds,
so the host loop never touches the filesystem.

Adds NO msgq readers: it only reads the host SubMaster. Position values go only
to the ZMQ socket, never to cloudlog/swaglog.

No subscriber connected (ZMQ socket monitor, see telemetry.ClientTracker): the host loop
skips payload.build and the sender skips JSON entirely. Every SUMMARY_PERIOD s the sender
thread logs one "state_stream_summary" cloudlog event (counts/rates/timings/Wi-Fi only).
"""
from __future__ import annotations

import json
import queue
import threading
import time

from openpilot.common.swaglog import cloudlog

from openpilot.system.state_stream import config as scfg
from openpilot.system.state_stream import payload
from openpilot.system.state_stream import route as sroute
from openpilot.system.state_stream import telemetry as tel

CONFIG_PERIOD = 2.0
ROUTE_TOPIC = "route"
ROUTE_PER_LOOP = 2      # route chunks per sender-loop pass, so a chunk burst never fills SNDHWM
ROUTE_IDLE_WAIT = 0.02  # sender queue wait while route chunks are pending


class StateStreamer:
  def __init__(self, cfg_dir=None, publisher_factory=None, iface_ip=None, clock=time.monotonic,
               log_event=None, wifi=None, summary_period=tel.SUMMARY_PERIOD):
    self._cfg_dir = cfg_dir
    self._publisher_factory = publisher_factory
    self._iface_ip = iface_ip
    self._clock = clock
    self._q: queue.Queue = queue.Queue(maxsize=scfg.QUEUE_MAX)
    self._route_lock = threading.Lock()
    self._route_new = None       # latest navRoute coords [(lat, lon)], handed to the sender thread
    self._route_sched = sroute.RouteSchedule()
    self.route_sent = 0
    self.cfg = scfg.StateStreamConfig()
    self.active = False          # read by host loop; written by sender thread
    self._next_t = 0.0
    self._next_model_t = 0.0
    self.frame = 0
    self.queued = 0
    self.dropped = 0
    self.sent = 0
    self.errors = 0
    # telemetry (host-loop side counters are plain ints; the sender thread snapshots them)
    self.clients = None          # authenticated subscribers; None = unknown (keep sending), written by sender
    self.skipped = 0             # host ticks skipped because no subscriber is connected
    self.model_built = 0         # snapshots that carried the model section
    self.model_src = 0           # modelV2 updates seen by the host loop while enabled
    self.tick_s = 0.0            # host time spent in tick() (perf_counter)
    self.bytes = 0
    self.encode_s = 0.0
    self.encode_max = 0.0
    self.leads = payload.LeadTracker()
    self.host_gaps = tel.GapCounter()
    self.send_gaps = tel.GapCounter()
    self.summaries = 0
    self.last_summary: dict | None = None
    self._meta = None
    self._model_src_hz = None
    self._msrc_n0, self._msrc_t0 = 0, None
    self._log_event = log_event or (lambda name, **kw: cloudlog.event(name, **kw))
    self._wifi = wifi
    self._summary_period = summary_period
    self._stop = threading.Event()
    self._thread = threading.Thread(target=self._run, name="state_stream", daemon=True)
    self._thread.start()

  # ---------------- host loop side ----------------
  def tick(self, sm, started: bool) -> None:
    """Call once per host loop after sm.update(). Never blocks, never raises."""
    self._capture_route(sm)
    if not (self.active and started):
      self.host_gaps.reset_ref()
      return
    t_in = time.perf_counter()
    try:
      now = self._clock()
      upd = getattr(sm, "updated", None)
      if upd and upd.get("modelV2"):
        self.model_src += 1
      if self.clients == 0:
        self.skipped += 1          # nobody listening: no build, no encode
        self.host_gaps.reset_ref()
        return
      # 25% slack so a 20 Hz host loop with jitter still yields 20 Hz, not 10 Hz.
      period = 1.0 / self.cfg.hz
      if now < self._next_t - 0.25 * period:
        return
      self._next_t = max(self._next_t + period, now)
      m_period = 1.0 / self.cfg.model_hz
      include_model = now >= self._next_model_t - 0.25 * m_period
      if include_model:
        self._next_model_t = max(self._next_model_t + m_period, now)
      if self._meta is None or self._meta.get("modelSrcHz") != self._model_src_hz \
         or self._meta["hz"] != self.cfg.hz or self._meta["modelHz"] != self.cfg.model_hz:
        # replaced, never mutated: queued snapshots keep their own reference
        self._meta = {"hz": self.cfg.hz, "modelHz": self.cfg.model_hz, "modelSrcHz": self._model_src_hz}
      fresh = self.host_gaps.last is None   # first snapshot after a pause: not a gap / not a handoff
      if fresh:
        self.leads.reset()
      snap = payload.build(sm, self.frame, include_model, self._meta, self.leads)
      if include_model and "model" in snap:
        self.model_built += 1
      self.frame += 1
      self.host_gaps.mark(now)
      self._q.put_nowait((now, fresh, snap))
      self.queued += 1
    except queue.Full:
      self.dropped += 1
    except Exception:
      self.errors += 1  # never propagate into the host process
    finally:
      self.tick_s += time.perf_counter() - t_in

  def _capture_route(self, sm) -> None:
    """Copy navRoute floats only when navd published a new one (rare). Empty = cleared."""
    try:
      upd = getattr(sm, "updated", None)
      if not upd or not upd.get("navRoute"):
        return
      coords = [(c.latitude, c.longitude) for c in sm["navRoute"].coordinates]
      with self._route_lock:
        self._route_new = coords
    except Exception:
      self.errors += 1

  # ---------------- sender thread ----------------
  def _route_step(self, pub, now: float) -> None:
    with self._route_lock:
      coords, self._route_new = self._route_new, None
    if coords is not None:
      self._route_sched.set_route(coords, now)   # thin + chunk once per route
    if pub is None:
      return
    for dat in self._route_sched.due(now, ROUTE_PER_LOOP):
      try:
        pub.send(ROUTE_TOPIC, dat)
        self.route_sent += 1
      except Exception:
        self.errors += 1

  def _make_publisher(self, cfg):
    if self._publisher_factory is not None:
      return self._publisher_factory(cfg)
    from openpilot.system.hevc_stream.hevc_streamd import Publisher, iface_ipv4
    ip = self._iface_ip(cfg.iface) if self._iface_ip else iface_ipv4(cfg.iface)
    ip = scfg.safe_bind_ip(ip)
    if ip is None:
      return None
    pub = Publisher(ip, cfg.port, cfg.allow, cfg.token)
    import zmq
    pub.sock.setsockopt(zmq.SNDHWM, scfg.SNDHWM)  # applies to new connections
    return pub

  def _run(self) -> None:
    try:  # this thread only: background cores 0-2 (driving cores 4-7 untouched), like hevc_streamd
      import os
      if (os.cpu_count() or 0) >= 8:
        os.sched_setaffinity(0, {0, 1, 2})
    except (AttributeError, OSError):
      pass
    pub = None
    clients = None
    next_cfg = 0.0
    t_start = time.monotonic()
    next_wifi = t_start
    next_sum = t_start + self._summary_period
    self._sum_prev = self._counters()
    self._sum_t0, self._sum_cpu0 = t_start, time.thread_time()
    while not self._stop.is_set():
      now = time.monotonic()
      if now >= next_cfg:
        next_cfg = now + CONFIG_PERIOD
        try:
          cfg = scfg.load(self._cfg_dir)
        except Exception:
          cfg = scfg.StateStreamConfig()
        if cfg != self.cfg or (cfg.enabled and pub is None):
          if pub is not None and (not cfg.enabled or (cfg.iface, cfg.port, cfg.allow, cfg.token) != (self.cfg.iface, self.cfg.port, self.cfg.allow, self.cfg.token)):
            if clients is not None:
              clients.close()
              clients = None
            try:
              pub.close()
            except Exception:
              pass
            pub = None
          self.cfg = cfg
          if cfg.enabled and pub is None:
            try:
              pub = self._make_publisher(cfg)
            except Exception:
              pub = None
              self.errors += 1
            if pub is not None:
              clients = tel.ClientTracker(pub)
        self.active = bool(self.cfg.enabled and pub is not None)
      if clients is not None:
        try:
          if clients.poll():
            self._route_sched.resend_now()   # new subscriber gets the active route right away
          self.clients = clients.count
        except Exception:
          self.errors += 1
      else:
        self.clients = None
      if self.cfg.enabled:
        if self._wifi is None:
          self._wifi = tel.WifiSampler(self.cfg.iface)
        if now >= next_wifi:
          next_wifi = now + tel.WIFI_PERIOD
          try:
            self._wifi.sample()
            self._update_model_src_hz(now)
          except Exception:
            self.errors += 1
        if now >= next_sum:
          next_sum = now + self._summary_period
          self._summary(now, clients)
      else:
        next_sum = now + self._summary_period   # off: no log lines, fresh window when enabled
      try:
        self._route_step(pub if self.active else None, self._clock())
      except Exception:
        self.errors += 1
      try:
        t_cap, fresh, snap = self._q.get(timeout=ROUTE_IDLE_WAIT if self._route_sched.busy else 0.25)
      except queue.Empty:
        continue
      if pub is None or self.clients == 0:
        continue          # subscriber left after this snapshot was queued: don't encode it
      try:
        t0 = time.perf_counter()
        dat = json.dumps(snap, separators=(",", ":")).encode()
        pub.send(scfg.TOPIC, dat)
        dt = time.perf_counter() - t0
        self.encode_s += dt
        self.encode_max = max(self.encode_max, dt)
        self.bytes += len(dat)
        self.sent += 1
        if fresh:
          self.send_gaps.reset_ref()
        self.send_gaps.mark(t_cap)
      except Exception:
        self.errors += 1  # zmq.Again etc: drop
    if clients is not None:
      clients.close()
    if pub is not None:
      try:
        pub.close()
      except Exception:
        pass

  # ---------------- telemetry (sender thread) ----------------
  def _counters(self) -> dict:
    return {"queued": self.queued, "sent": self.sent, "dropped": self.dropped, "errors": self.errors,
            "skipped": self.skipped, "route": self.route_sent, "bytes": self.bytes, "model": self.model_built,
            "modelSrc": self.model_src, "tickS": self.tick_s, "encS": self.encode_s, "leadSwitches": self.leads.switches}

  def _update_model_src_hz(self, now: float) -> None:
    """Measured modelV2 rate on the host loop (every WIFI_PERIOD s), sent as meta.modelSrcHz."""
    n = self.model_src
    if self._msrc_t0 is not None and now > self._msrc_t0:
      dn = n - self._msrc_n0
      self._model_src_hz = round(dn / (now - self._msrc_t0), 1) if dn > 0 else None
    self._msrc_n0, self._msrc_t0 = n, now

  def _summary(self, now: float, clients) -> None:
    """One cloudlog event per window: deltas + rates. Counts/timings/Wi-Fi only, never positions."""
    try:
      cur, prev = self._counters(), self._sum_prev
      d = {k: cur[k] - prev[k] for k in cur}
      win = max(now - self._sum_t0, 1e-6)
      cpu = time.thread_time()
      sender_cpu = cpu - self._sum_cpu0
      self._sum_prev, self._sum_t0, self._sum_cpu0 = cur, now, cpu
      hg, hmax = self.host_gaps.take()
      sg, smax = self.send_gaps.take()
      ev = {
        "window_s": round(win, 1), "clients": self.clients,
        "connects": clients.connects if clients is not None else None,
        "disconnects": clients.disconnects if clients is not None else None,
        "authOk": clients.auth_ok if clients is not None else None,
        "authFail": clients.rejected if clients is not None else None,
        "queued": d["queued"], "sent": d["sent"], "dropped": d["dropped"], "errors": d["errors"],
        "skippedNoClient": d["skipped"], "routeSent": d["route"], "bytes": d["bytes"],
        "rates": {"state": round(d["sent"] / win, 2), "model": round(d["model"] / win, 2),
                  "modelSrc": round(d["modelSrc"] / win, 2), "route": round(d["route"] / win, 3),
                  "kBps": round(d["bytes"] / win / 1000.0, 2)},
        "cfg": {"hz": self.cfg.hz, "modelHz": self.cfg.model_hz},
        "encodeMsMean": round(1e3 * d["encS"] / d["sent"], 3) if d["sent"] else None,
        "encodeMsMax": round(1e3 * self.encode_max, 3),
        "tickMsMean": round(1e3 * d["tickS"] / max(d["queued"] + d["skipped"], 1), 4),
        "hostCpuPct": round(100.0 * d["tickS"] / win, 3),        # host-loop share of one core
        "senderCpuPct": round(100.0 * sender_cpu / win, 3),      # this thread's CPU, share of one core
        "gaps": {"host": hg, "hostMaxS": hmax, "send": sg, "sendMaxS": smax, "thresholdS": tel.GAP_S},
        "leadSwitches": d["leadSwitches"],
        "wifi": self._wifi.window() if self._wifi is not None else None,
        "totals": {"sent": cur["sent"], "dropped": cur["dropped"], "errors": cur["errors"], "bytes": cur["bytes"]},
      }
      self.encode_max = 0.0
      self.summaries += 1
      self.last_summary = ev
      self._log_event("state_stream_summary", **ev)
    except Exception:
      self.errors += 1

  def stop(self) -> None:
    self._stop.set()
    self._thread.join(timeout=2.0)
