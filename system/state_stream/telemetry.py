"""StateStream telemetry: subscriber count, Wi-Fi link stats and the 60 s counter summary.

Sender-thread only (never the host loop). No new dependencies: Wi-Fi comes from
/proc/net/wireless and /sys/class/net/<iface>/statistics (plain file reads), and the
subscriber count from a ZMQ socket monitor on the PUB socket we already own.

Some Wi-Fi drivers list the interface in /proc/net/wireless but leave every field 0
(status 0000, link 0, level 0, noise 0). That is "unknown", never 0 dBm. Signal then comes
from a LinkProbe, in order and only if installed: `wpa_cli -i <iface> signal_poll` (primary:
RSSI, AVG_RSSI, LINKSPEED, FREQUENCY; NOISE=9999 means null), then
`nmcli -t -f IN-USE,SIGNAL dev wifi` (signal %), else "unavailable". The probe runs in its
own short-lived daemon thread with a subprocess timeout, at most every LINK_PROBE_PERIOD s,
so the sender thread never waits on it. Network names and access-point addresses are never
requested, parsed or reported.

The summary carries counts, rates and timings only: never positions, routes, IPs or tokens.
"""
from __future__ import annotations

import math
from pathlib import Path

SUMMARY_PERIOD = 60.0   # s between summary log lines
WIFI_PERIOD = 5.0       # s between Wi-Fi samples (one small /proc read)
GAP_S = 0.5             # inter-snapshot gap worth counting
LINK_PROBE_PERIOD = 10.0  # s, min interval between wpa_cli/nmcli runs
LINK_PROBE_TIMEOUT = 1.0  # s, subprocess timeout
PROC_WIRELESS = Path("/proc/net/wireless")
SYS_NET = Path("/sys/class/net")


def parse_proc_wireless(text: str, iface: str) -> dict | None:
  """/proc/net/wireless line -> {"link": q, "level": dBm, "noise": dBm|None, "missedBeacon": n, "discardRetry": n}.

  Format (2 header lines):
    Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
     face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
     wlan0: 0000   58.  -52.  -256        0      0      0      5      0        0
  """
  for line in text.splitlines()[2:]:
    name, _, rest = line.partition(":")
    if name.strip() != iface:
      continue
    f = rest.split()
    if len(f) < 10:
      return None
    try:
      num = [float(x.rstrip(".")) for x in f[1:10]]
    except ValueError:
      return None
    link, level, noise = num[0], num[1], num[2]
    if link == 0 and level == 0 and noise == 0:
      return {"unknown": True}   # driver lists the iface but does not fill it: not 0 dBm
    if level > 0:            # some drivers report unsigned 8-bit dBm
      level -= 256
    return {"link": int(link), "level": int(level), "noise": None if noise <= -256 or noise == 0 else int(noise),
            "discardRetry": int(num[6]), "missedBeacon": int(num[8])}
  return None


def _num(text: str) -> float | None:
  try:
    return float(text.split()[0])
  except (IndexError, ValueError):
    return None


def _dbm(text: str | None) -> int | None:
  """wpa_cli dBm value; 9999 / -9999 / 0 mean "not reported" -> None."""
  v = _num(text) if text is not None else None
  if v is None or abs(v) >= 9999 or v == 0:
    return None
  return int(v)


def parse_wpa_signal_poll(text: str) -> dict | None:
  """`wpa_cli -i <iface> signal_poll` -> {"connected", "rssi_dbm", "avg_rssi_dbm", "noise_dbm",
  "linkspeed_mbps", "freq_mhz"}.

  Output is KEY=VALUE lines (RSSI, LINKSPEED, NOISE, FREQUENCY, AVG_RSSI, ...); "FAIL" when
  not associated. NOISE=9999 means not reported -> null.
  """
  if not text:
    return None
  if text.strip().upper().startswith("FAIL"):
    return {"connected": False}
  kv = {}
  for line in text.splitlines():
    k, sep, v = line.strip().partition("=")
    if sep:
      kv[k.strip().upper()] = v.strip()
  rssi = _dbm(kv.get("RSSI"))
  if rssi is None:
    return None
  ls, fr = _num(kv.get("LINKSPEED", "")), _num(kv.get("FREQUENCY", ""))
  return {"connected": True, "rssi_dbm": rssi, "avg_rssi_dbm": _dbm(kv.get("AVG_RSSI")),
          "noise_dbm": _dbm(kv.get("NOISE")), "linkspeed_mbps": int(ls) if ls is not None else None,
          "freq_mhz": int(fr) if fr is not None else None}


def parse_nmcli_wifi(text: str | None) -> dict | None:
  """`nmcli -t -f IN-USE,SIGNAL dev wifi` -> {"connected", "signal_pct"}.

  Terse output, one AP per line, e.g. `*:92`. Only the in-use line counts. Only IN-USE and
  SIGNAL are requested: no network names ever reach this code.
  """
  if text is None:
    return None
  for line in text.splitlines():
    f = line.split(":")
    if len(f) >= 2 and f[0].strip() == "*":
      try:
        pct = int(f[1])
      except ValueError:
        pct = None
      return {"connected": True, "signal_pct": pct}
  return {"connected": False} if text.strip() else None


def _run_cmd(cmd: list[str], timeout: float) -> str | None:
  import subprocess
  try:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
  except (OSError, subprocess.SubprocessError):
    return None
  return r.stdout if r.returncode == 0 else None


class LinkProbe:
  """Non-blocking wpa_cli / nmcli signal probe.

  kick(now) starts at most one daemon thread per `period`; latest() returns the last result
  ({"source": "wpa_cli"|"nmcli", ...}) or None. Tests pass threaded=False plus fake run/which.
  """

  def __init__(self, iface: str = "wlan0", period: float = LINK_PROBE_PERIOD, timeout: float = LINK_PROBE_TIMEOUT,
               run=_run_cmd, which=None, threaded: bool = True):
    import shutil
    import threading
    self.iface, self.period, self.timeout, self.run, self.threaded = iface, period, timeout, run, threaded
    which = which or shutil.which
    self.tools = [t for t in ("wpa_cli", "nmcli") if which(t)]
    self._lock = threading.Lock()
    self._thread = None
    self._next = 0.0
    self._latest: dict | None = None
    self.runs = 0
    self.failures = 0

  @property
  def available(self) -> bool:
    return bool(self.tools)

  def kick(self, now: float) -> None:
    if not self.tools or now < self._next:
      return
    if self._thread is not None and self._thread.is_alive():
      return   # previous probe still running (bounded by timeout); never wait on it
    self._next = now + self.period
    if self.threaded:
      import threading
      self._thread = threading.Thread(target=self._probe, name="bp-linkprobe", daemon=True)
      self._thread.start()
    else:
      self._probe()

  def _probe(self) -> None:
    res = None
    for tool in self.tools:
      if tool == "wpa_cli":
        r = parse_wpa_signal_poll(self.run(["wpa_cli", "-i", self.iface, "signal_poll"], self.timeout) or "")
      else:
        r = parse_nmcli_wifi(self.run(["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi"], self.timeout))
      if r is not None:
        res = dict(r, source=tool)
        break
    with self._lock:
      self.runs += 1
      if res is None:
        self.failures += 1
      self._latest = res

  def latest(self) -> dict | None:
    with self._lock:
      return self._latest


def _read_int(p: Path) -> int | None:
  try:
    return int(p.read_text().strip())
  except (OSError, ValueError):
    return None


class WifiSampler:
  def __init__(self, iface: str = "wlan0", proc: Path = PROC_WIRELESS, sys_net: Path = SYS_NET, probe=None, clock=None):
    import time
    self.iface, self.proc, self.sys_net = iface, proc, sys_net
    self.clock = clock or time.monotonic
    self.probe = probe if probe is not None else LinkProbe(iface)
    self.reset()
    self._stats0 = None

  def reset(self) -> None:
    self.samples = 0
    self.levels: list[int] = []
    self.links: list[int] = []
    self.last: dict | None = None
    self.unavailable = 0
    self.unknown = 0         # proc listed the iface with all-zero fields
    self.probe_samples = 0
    self.signals: list[int] = []
    self.pcts: list[int] = []
    self.last_probe: dict | None = None

  def _iface_stats(self) -> dict:
    d = self.sys_net / self.iface / "statistics"
    return {k: _read_int(d / k) for k in ("tx_errors", "tx_dropped", "rx_dropped", "tx_bytes")}

  def sample(self) -> None:
    try:
      w = parse_proc_wireless(self.proc.read_text(), self.iface)
    except OSError:
      w = None
    if w is not None and w.get("unknown"):
      self.unknown += 1
      w = None
    if w is None:
      self._sample_probe()
      return
    self.samples += 1
    self.levels.append(w["level"])
    self.links.append(w["link"])
    self.last = w

  def _operstate(self) -> str | None:
    try:
      return (self.sys_net / self.iface / "operstate").read_text().strip() or None
    except OSError:
      return None

  def _sample_probe(self) -> None:
    p = self.probe
    if p is None or not getattr(p, "available", False):
      self.unavailable += 1
      return
    p.kick(self.clock())         # non-blocking; result arrives on a later sample
    r = p.latest()
    if r is None:
      self.unavailable += 1
      return
    self.probe_samples += 1
    self.last_probe = r
    if r.get("rssi_dbm") is not None:
      self.signals.append(r["rssi_dbm"])
    if r.get("signal_pct") is not None:
      self.pcts.append(r["signal_pct"])

  def window(self) -> dict:
    """Wi-Fi stats for the summary window, then reset the window."""
    st = self._iface_stats()
    prev, self._stats0 = self._stats0, st
    delta = {}
    for k, v in st.items():
      p = prev.get(k) if prev else None
      delta[k] = (v - p) if (v is not None and p is not None and v >= p) else None
    out = {"samples": self.samples, "unavailable": self.unavailable, "unknown": self.unknown,
           "probeSamples": self.probe_samples}
    if self.levels:
      out.update(levelMin=min(self.levels), levelMax=max(self.levels), levelMean=round(sum(self.levels) / len(self.levels), 1),
                 linkMin=min(self.links), linkMean=round(sum(self.links) / len(self.links), 1))
      if self.last:
        out.update(level=self.last["level"], link=self.last["link"], noise=self.last["noise"],
                   missedBeacon=self.last["missedBeacon"], discardRetry=self.last["discardRetry"])
    lp = self.last_probe
    if lp is not None:
      out["linkSource"] = lp.get("source")
      out["connected"] = lp.get("connected")
      if self.signals:
        out.update(rssi_dbm_min=min(self.signals), rssi_dbm_max=max(self.signals))
      if self.pcts:
        out.update(signal_pct_min=min(self.pcts))
      for k in ("rssi_dbm", "avg_rssi_dbm", "noise_dbm", "linkspeed_mbps", "freq_mhz", "signal_pct"):
        if k in lp:
          out[k] = lp[k]
    out["operstate"] = self._operstate()
    out["source"] = "proc" if self.levels else (lp.get("source") if lp is not None else "unavailable")
    out.update({f"d_{k}": v for k, v in delta.items()})
    self.reset()
    return out


class ClientTracker:
  """Counts connected ZMQ peers on our PUB socket via a socket monitor.

  Peers are keyed by fd: +1 on ACCEPTED, -1 on DISCONNECTED (libzmq reports the fd on both;
  HANDSHAKE_* events only carry a status code, so they are counted separately as
  authOk / authFail). A wrong-token or non-allowlisted peer counts only for the moment until
  ZAP drops it, which errs on the side of sending. If the publisher has no zmq socket
  (unit-test fakes), `pub.clients` is used when present, otherwise the count is unknown (None)
  and the streamer keeps sending as before.
  """

  # pyzmq compatibility (device: pyzmq 25.1.2 / libzmq 4.3.4; receiver: 27.2.0 / 4.3.5):
  # Socket.get_monitor_socket(events), zmq.utils.monitor.recv_monitor_message(sock, flags)
  # -> {"event", "value", "endpoint"}, and EVENT_ACCEPTED / EVENT_DISCONNECTED are stable
  # since pyzmq 14. EVENT_HANDSHAKE_* exist with libzmq >= 4.3 and are read via getattr.
  # From pyzmq 23 "event" is a zmq.Event IntEnum, which compares equal to the int constants.
  def __init__(self, pub):
    self.pub = pub
    self.mon = None
    self.fds: set[int] = set()
    self.connects = 0
    self.disconnects = 0
    self.rejected = 0
    self.auth_ok = 0
    sock = getattr(pub, "sock", None)
    if sock is not None:
      try:
        import zmq
        ev = zmq.EVENT_ACCEPTED | zmq.EVENT_DISCONNECTED
        for name in ("EVENT_HANDSHAKE_SUCCEEDED", "EVENT_HANDSHAKE_FAILED_AUTH",
                     "EVENT_HANDSHAKE_FAILED_PROTOCOL", "EVENT_HANDSHAKE_FAILED_NO_DETAIL"):
          ev |= getattr(zmq, name, 0)   # libzmq >= 4.3; counters only
        self.mon = sock.get_monitor_socket(ev)
      except Exception:
        self.mon = None

  @property
  def count(self) -> int | None:
    if self.mon is not None:
      return len(self.fds)
    n = getattr(self.pub, "clients", None)
    return int(n) if n is not None else None

  def poll(self) -> bool:
    """Drain monitor events (non-blocking). Returns True if a handshake succeeded (new subscriber)."""
    if self.mon is None:
      return False
    import zmq
    from zmq.utils.monitor import recv_monitor_message
    joined = False
    for _ in range(64):
      try:
        m = recv_monitor_message(self.mon, flags=zmq.NOBLOCK)
      except zmq.Again:
        break
      except Exception:
        break
      e, fd = m.get("event"), m.get("value")
      if e == zmq.EVENT_ACCEPTED:
        self.fds.add(fd)
        self.connects += 1
      elif e == zmq.EVENT_DISCONNECTED:
        if fd in self.fds:
          self.fds.discard(fd)
          self.disconnects += 1
      elif e == getattr(zmq, "EVENT_HANDSHAKE_SUCCEEDED", None):
        self.auth_ok += 1
        joined = True
      else:
        self.rejected += 1
    return joined

  def close(self) -> None:
    if self.mon is not None:
      try:
        self.pub.sock.disable_monitor()
      except Exception:
        pass
      try:
        self.mon.close(0)
      except Exception:
        pass
      self.mon = None


class GapCounter:
  def __init__(self, gap_s: float = GAP_S):
    self.gap_s = gap_s
    self.last = None
    self.n = 0
    self.max = 0.0

  def reset_ref(self) -> None:
    self.last = None

  def mark(self, t: float) -> None:
    if self.last is not None:
      dt = t - self.last
      if dt > self.gap_s:
        self.n += 1
        self.max = max(self.max, dt)
    self.last = t

  def take(self) -> tuple[int, float]:
    out = (self.n, round(self.max, 2))
    self.n, self.max = 0, 0.0
    return out


def _r(x, nd=2):
  return round(x, nd) if x is not None and math.isfinite(x) else None
