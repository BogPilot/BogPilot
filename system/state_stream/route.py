"""StateStream route section: thin + chunk the navd navRoute for the Mac.

Pure functions plus a tiny schedule object, no I/O and no logging (coordinates
must never reach cloudlog/swaglog/stdout). The host loop only copies the
coordinate floats once per new route; thinning, hashing and chunking run once
per route on the StateStream sender thread, and resends reuse the encoded bytes.

Wire (ZMQ topic b"route", one JSON object per message, each <= CHUNK_BYTES):
  chunk:   {"v":1,"route":{"h":"<8 hex>","i":<0..n-1>,"n":<n>,"pts":[[lat,lon],...]}}
  cleared: {"v":1,"route":{"cleared":true}}
"""
from __future__ import annotations

import hashlib
import json
import math

THIN_M = 9.0          # ~30 ft between kept points
CHUNK_BYTES = 1000    # target max JSON bytes per message
RESEND_S = 10.0       # full chunk set resent this often while a route is active
CLEARED_REPEATS = 3   # cleared marker sent now, then at +10 s and +20 s
DIGITS = 6            # ~0.1 m
_EARTH_R = 6371000.0
_SEP = (",", ":")


def dist_m(a, b) -> float:
  """Equirectangular distance in meters; fine at 9 m scales."""
  lat1, lon1 = math.radians(a[0]), math.radians(a[1])
  lat2, lon2 = math.radians(b[0]), math.radians(b[1])
  x = (lon2 - lon1) * math.cos(0.5 * (lat1 + lat2))
  return _EARTH_R * math.hypot(x, lat2 - lat1)


def thin(coords, min_m: float = THIN_M) -> list:
  """Keep first, then each point >= min_m from the last kept; always keep last (last gap may be short)."""
  pts = [(float(a), float(b)) for a, b in coords if math.isfinite(a) and math.isfinite(b)]
  if len(pts) <= 2:
    return pts
  out = [pts[0]]
  for p in pts[1:-1]:
    if dist_m(out[-1], p) >= min_m:
      out.append(p)
  if out[-1] != pts[-1]:
    out.append(pts[-1])   # destination exact; only this last gap may be < min_m
  return out


def _enc(obj) -> bytes:
  return json.dumps(obj, separators=_SEP).encode()


def route_hash(pts) -> str:
  h = hashlib.sha1()
  for la, lo in pts:
    h.update(f"{la:.{DIGITS}f},{lo:.{DIGITS}f};".encode())
  return h.hexdigest()[:8]


def chunk(pts, max_bytes: int = CHUNK_BYTES) -> tuple[str, list[bytes]]:
  """Return (hash, [encoded chunk messages]). Empty route -> ('', [])."""
  if not pts:
    return "", []
  rp = [[round(la, DIGITS), round(lo, DIGITS)] for la, lo in pts]
  h = route_hash(pts)
  strs = [json.dumps(p, separators=_SEP) for p in rp]
  # worst-case envelope: 5-digit index/total
  overhead = len(_enc({"v": 1, "route": {"h": h, "i": 99999, "n": 99999, "pts": []}}))
  groups, cur, size = [], [], overhead
  for i, s in enumerate(strs):
    add = len(s) + (1 if cur else 0)
    if cur and size + add > max_bytes:
      groups.append(cur)
      cur, size = [], overhead
      add = len(s)
    cur.append(i)
    size += add
  if cur:
    groups.append(cur)
  n = len(groups)
  return h, [_enc({"v": 1, "route": {"h": h, "i": k, "n": n, "pts": [rp[j] for j in g]}}) for k, g in enumerate(groups)]


CLEARED_MSG = _enc({"v": 1, "route": {"cleared": True}})


class RouteSchedule:
  """Decides which encoded route messages to send when. Sender-thread only."""

  def __init__(self):
    self.hash = ""
    self.chunks: list[bytes] = []
    self._pending: list[bytes] = []
    self._next_t = math.inf
    self._cleared_left = 0

  def set_route(self, coords, now: float) -> None:
    pts = thin(coords)
    if not pts:
      self.hash, self.chunks = "", []
      self._pending = [CLEARED_MSG]
      self._cleared_left = CLEARED_REPEATS - 1
      self._next_t = now + RESEND_S
      return
    self.hash, self.chunks = chunk(pts)
    self._cleared_left = 0
    self._pending = list(self.chunks)   # new route / reroute: send now (replaces any partial old set)
    self._next_t = now + RESEND_S

  def due(self, now: float, limit: int) -> list[bytes]:
    if not self._pending and now >= self._next_t:
      if self.chunks:
        self._pending = list(self.chunks)
        self._next_t = now + RESEND_S
      elif self._cleared_left > 0:
        self._pending = [CLEARED_MSG]
        self._cleared_left -= 1
        self._next_t = now + RESEND_S
      else:
        self._next_t = math.inf
    out, self._pending = self._pending[:limit], self._pending[limit:]
    return out

  @property
  def busy(self) -> bool:
    return bool(self._pending)
