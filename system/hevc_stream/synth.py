"""Test helper: build EncodeData Events from an Annex-B .hevc file (+ optional rlog idx).

Splits the elementary stream into access units the way encoderd emits them:
VPS/SPS/PPS go in `header` on keyframes, slice NALs go in `data`.
"""
from __future__ import annotations

KEYFRAME = 8


def nals(buf: bytes):
  i, n, starts = 0, len(buf), []
  while True:
    j = buf.find(b"\x00\x00\x01", i)
    if j < 0:
      break
    s = j - 1 if j > 0 and buf[j - 1] == 0 else j
    starts.append((s, j + 3))
    i = j + 3
  for k, (s, p) in enumerate(starts):
    e = starts[k + 1][0] if k + 1 < len(starts) else n
    yield buf[s:e], (buf[p] >> 1) & 0x3F, buf[p + 2] if p + 2 < n else 0


def access_units(buf: bytes, max_au: int | None = None):
  """Yield (header_bytes, data_bytes, is_key)."""
  hdr, cur, key, out = b"", [], False, 0
  for nal, t, b2 in nals(buf):
    if t in (32, 33, 34):  # VPS SPS PPS
      if cur:
        yield hdr, b"".join(cur), key
        out += 1
        cur, key, hdr = [], False, b""
        if max_au and out >= max_au:
          return
      hdr += nal
    elif t < 32:  # VCL
      if (b2 & 0x80) and cur:  # first_slice_segment_in_pic_flag
        yield hdr, b"".join(cur), key
        out += 1
        cur, key, hdr = [], False, b""
        if max_au and out >= max_au:
          return
      cur.append(nal)
      key = key or 16 <= t <= 21
    elif cur:
      cur.append(nal)
  if cur:
    yield hdr, b"".join(cur), key


def encode_events(log, buf: bytes, service: str = "wideRoadEncodeData", w=1928, h=1208, max_au=None,
                  idx=None, t0=1_000_000_000):
  """idx: optional list of (frameId, timestampEof, logMonoTime) from an rlog."""
  for n, (hdr, dat, key) in enumerate(access_units(buf, max_au)):
    ev = log.Event.new_message()
    fid, teof, tlog = idx[n] if idx and n < len(idx) else (n, t0 + n * 50_000_000, t0 + n * 50_000_000)
    ev.logMonoTime = tlog
    ev.valid = True
    e = ev.init(service)
    e.idx.frameId = fid
    e.idx.encodeId = n
    e.idx.timestampEof = teof
    e.idx.flags = KEYFRAME if key else 0
    e.idx.len = len(dat)
    e.idx.type = "fullHEVC"
    e.data = dat
    e.width, e.height = w, h
    if key:
      e.header = hdr
    yield ev.to_bytes(), tlog
