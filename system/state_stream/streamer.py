"""StateStream: non-blocking side channel inside an existing process.

Host-loop cost: one config flag read (a bool), a rate check, and payload.build()
copying scalars out of the host's SubMaster. JSON encoding and the ZMQ send run
on a daemon thread fed by a tiny queue; a full queue drops the snapshot
(put_nowait), so the host loop never waits on the network or the GIL-heavy
encode. The thread also re-reads the config file every CONFIG_PERIOD seconds,
so the host loop never touches the filesystem.

Adds NO msgq readers: it only reads the host SubMaster. Position values go only
to the ZMQ socket, never to cloudlog/swaglog.
"""
from __future__ import annotations

import json
import queue
import threading
import time

from openpilot.system.state_stream import config as scfg
from openpilot.system.state_stream import payload
from openpilot.system.state_stream import route as sroute

CONFIG_PERIOD = 2.0
ROUTE_TOPIC = "route"
ROUTE_PER_LOOP = 2      # route chunks per sender-loop pass, so a chunk burst never fills SNDHWM
ROUTE_IDLE_WAIT = 0.02  # sender queue wait while route chunks are pending


class StateStreamer:
  def __init__(self, cfg_dir=None, publisher_factory=None, iface_ip=None, clock=time.monotonic):
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
    self._stop = threading.Event()
    self._thread = threading.Thread(target=self._run, name="state_stream", daemon=True)
    self._thread.start()

  # ---------------- host loop side ----------------
  def tick(self, sm, started: bool) -> None:
    """Call once per host loop after sm.update(). Never blocks, never raises."""
    self._capture_route(sm)
    if not (self.active and started):
      return
    try:
      now = self._clock()
      # 25% slack so a 20 Hz host loop with jitter still yields 20 Hz, not 10 Hz.
      period = 1.0 / self.cfg.hz
      if now < self._next_t - 0.25 * period:
        return
      self._next_t = max(self._next_t + period, now)
      m_period = 1.0 / self.cfg.model_hz
      include_model = now >= self._next_model_t - 0.25 * m_period
      if include_model:
        self._next_model_t = max(self._next_model_t + m_period, now)
      snap = payload.build(sm, self.frame, include_model)
      self.frame += 1
      self._q.put_nowait(snap)
      self.queued += 1
    except queue.Full:
      self.dropped += 1
    except Exception:
      self.errors += 1  # never propagate into the host process

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
    next_cfg = 0.0
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
        self.active = bool(self.cfg.enabled and pub is not None)
      try:
        self._route_step(pub if self.active else None, self._clock())
      except Exception:
        self.errors += 1
      try:
        snap = self._q.get(timeout=ROUTE_IDLE_WAIT if self._route_sched.busy else 0.25)
      except queue.Empty:
        continue
      if pub is None:
        continue
      try:
        pub.send(scfg.TOPIC, json.dumps(snap, separators=(",", ":")).encode())
        self.sent += 1
      except Exception:
        self.errors += 1  # zmq.Again etc: drop
    if pub is not None:
      try:
        pub.close()
      except Exception:
        pass

  def stop(self) -> None:
    self._stop.set()
    self._thread.join(timeout=2.0)
