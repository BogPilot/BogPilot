"""StateStream telemetry: subscriber count, Wi-Fi link stats and the 60 s counter summary.

Sender-thread only (never the host loop). No new dependencies: Wi-Fi comes from
/proc/net/wireless and /sys/class/net/<iface>/statistics (plain file reads), and the
subscriber count from a ZMQ socket monitor on the PUB socket we already own.

The summary carries counts, rates and timings only: never positions, routes, IPs or tokens.
"""
from __future__ import annotations

import math
from pathlib import Path

SUMMARY_PERIOD = 60.0   # s between summary log lines
WIFI_PERIOD = 5.0       # s between Wi-Fi samples (one small /proc read)
GAP_S = 0.5             # inter-snapshot gap worth counting
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
    if level > 0:            # some drivers report unsigned 8-bit dBm
      level -= 256
    return {"link": int(link), "level": int(level), "noise": None if noise <= -256 or noise == 0 else int(noise),
            "discardRetry": int(num[6]), "missedBeacon": int(num[8])}
  return None


def _read_int(p: Path) -> int | None:
  try:
    return int(p.read_text().strip())
  except (OSError, ValueError):
    return None


class WifiSampler:
  def __init__(self, iface: str = "wlan0", proc: Path = PROC_WIRELESS, sys_net: Path = SYS_NET):
    self.iface, self.proc, self.sys_net = iface, proc, sys_net
    self.reset()
    self._stats0 = None

  def reset(self) -> None:
    self.samples = 0
    self.levels: list[int] = []
    self.links: list[int] = []
    self.last: dict | None = None
    self.unavailable = 0

  def _iface_stats(self) -> dict:
    d = self.sys_net / self.iface / "statistics"
    return {k: _read_int(d / k) for k in ("tx_errors", "tx_dropped", "rx_dropped", "tx_bytes")}

  def sample(self) -> None:
    try:
      w = parse_proc_wireless(self.proc.read_text(), self.iface)
    except OSError:
      w = None
    if w is None:
      self.unavailable += 1
      return
    self.samples += 1
    self.levels.append(w["level"])
    self.links.append(w["link"])
    self.last = w

  def window(self) -> dict:
    """Wi-Fi stats for the summary window, then reset the window."""
    st = self._iface_stats()
    prev, self._stats0 = self._stats0, st
    delta = {}
    for k, v in st.items():
      p = prev.get(k) if prev else None
      delta[k] = (v - p) if (v is not None and p is not None and v >= p) else None
    out = {"samples": self.samples, "unavailable": self.unavailable}
    if self.levels:
      out.update(levelMin=min(self.levels), levelMax=max(self.levels), levelMean=round(sum(self.levels) / len(self.levels), 1),
                 linkMin=min(self.links), linkMean=round(sum(self.links) / len(self.links), 1))
      if self.last:
        out.update(level=self.last["level"], link=self.last["link"], noise=self.last["noise"],
                   missedBeacon=self.last["missedBeacon"], discardRetry=self.last["discardRetry"])
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
