"""Route section tests. SYNTHETIC coordinates only (open Pacific, no real place); never print points."""
import json
import socket
import threading
import time
from types import SimpleNamespace as NS

from openpilot.system.state_stream import route as sroute
from openpilot.system.state_stream.streamer import StateStreamer

LAT0, LON0 = 10.0, -140.0   # made-up origin in open ocean


def synth(n=2000, step_m=3.0):
  """Wiggly synthetic path, ~step_m between raw points."""
  import math
  pts, la, lo = [], LAT0, LON0
  for i in range(n):
    hdg = 0.6 + 0.4 * math.sin(i / 150.0)
    la += step_m * math.cos(hdg) / 111320.0
    lo += step_m * math.sin(hdg) / (111320.0 * math.cos(math.radians(la)))
    pts.append((la, lo))
  return pts


def reassemble(msgs):
  by = {}
  for m in msgs:
    r = json.loads(m)["route"]
    if r.get("cleared"):
      by = {"cleared": True}
      continue
    by.setdefault(r["h"], {})[r["i"]] = (r["n"], r["pts"])
  out = {}
  for h, parts in by.items():
    if h == "cleared":
      continue
    n = next(iter(parts.values()))[0]
    if len(parts) == n:
      out[h] = [p for i in range(n) for p in parts[i][1]]
  return out


def test_thinning_spacing():
  raw = synth()
  t = sroute.thin(raw)
  assert t[0] == raw[0] and t[-1] == raw[-1]
  gaps = [sroute.dist_m(a, b) for a, b in zip(t, t[1:])]
  assert all(g >= sroute.THIN_M - 1e-6 for g in gaps[:-1])
  assert max(gaps) < sroute.THIN_M + 3.5  # raw step is 3 m, so a kept gap overshoots 9 m by < 3.5 m
  assert len(t) < len(raw) / 2.5


def test_thin_edge_cases():
  assert sroute.thin([]) == []
  assert sroute.thin([(LAT0, LON0)]) == [(LAT0, LON0)]
  assert sroute.thin([(LAT0, LON0), (float("nan"), 0.0), (LAT0 + 1e-3, LON0)]) == [(LAT0, LON0), (LAT0 + 1e-3, LON0)]


def test_chunk_sizes_and_roundtrip():
  t = sroute.thin(synth(6000))
  h, chunks = sroute.chunk(t)
  assert len(chunks) > 5
  assert all(len(c) <= 1200 for c in chunks)
  assert all(len(c) <= sroute.CHUNK_BYTES for c in chunks)
  hdr = [json.loads(c)["route"] for c in chunks]
  assert all(r["h"] == h and r["n"] == len(chunks) for r in hdr)
  assert [r["i"] for r in hdr] == list(range(len(chunks)))
  shuffled = chunks[::-1]
  got = reassemble(shuffled)[h]
  assert got == [[round(a, 6), round(b, 6)] for a, b in t]


def test_hash_changes_on_reroute():
  a = sroute.thin(synth(500))
  b = sroute.thin(synth(600))
  assert sroute.chunk(a)[0] != sroute.chunk(b)[0]
  assert sroute.chunk(a)[0] == sroute.chunk(list(a))[0]


def test_resend_timing_and_cleared():
  s = sroute.RouteSchedule()
  s.set_route(synth(3000), now=100.0)
  n = len(s.chunks)
  sent = []
  t = 100.0
  while s.busy:
    sent += s.due(t, 2); t += 0.05
  assert sent == s.chunks
  assert s.due(105.0, 99) == []
  assert s.due(109.9, 99) == []
  again = s.due(110.0, 99)
  assert again == s.chunks and len(again) == n
  assert s.due(115.0, 99) == []
  assert len(s.due(120.0, 99)) == n
  # reroute mid-cycle: immediate, replaces the old pending set
  s.due(130.0, 1)
  s.set_route(synth(2500), now=131.0)
  assert s.due(131.0, 999) == s.chunks
  # cleared: small marker now, +10, +20, then silence
  s.set_route([], now=140.0)
  assert s.due(140.0, 9) == [sroute.CLEARED_MSG]
  assert len(sroute.CLEARED_MSG) < 64
  assert s.due(145.0, 9) == []
  assert s.due(150.0, 9) == [sroute.CLEARED_MSG]
  assert s.due(160.0, 9) == [sroute.CLEARED_MSG]
  assert s.due(170.0, 9) == [] and s.due(500.0, 9) == []


class FakeSM:
  def __init__(self):
    self.seen, self.logMonoTime, self.updated, self.d = {}, {}, {}, {}

  def __getitem__(self, k):
    return self.d[k]

  def nav(self, pts):
    self.d["navRoute"] = NS(coordinates=[NS(latitude=a, longitude=b) for a, b in pts])
    self.updated = {"navRoute": True}


def test_replay_style_loopback(tmp_path):
  """Synthetic navRoute -> StateStreamer -> real ZMQ PUB on localhost -> SUB reassembles."""
  import zmq
  from openpilot.system.hevc_stream.hevc_streamd import Publisher
  ctx = zmq.Context()
  with socket.socket() as so:
    so.bind(("127.0.0.1", 0)); port = so.getsockname()[1]
  (tmp_path / "StateStream").write_text("1")
  clock = {"t": 1000.0}
  s = StateStreamer(cfg_dir=tmp_path, publisher_factory=lambda cfg: Publisher("127.0.0.1", port, ctx=ctx),
                    clock=lambda: clock["t"])
  sub = ctx.socket(zmq.SUB); sub.connect(f"tcp://127.0.0.1:{port}"); sub.setsockopt(zmq.SUBSCRIBE, b"")
  for _ in range(100):
    if s.active:
      break
    time.sleep(0.02)
  assert s.active
  time.sleep(0.3)
  got, stop = [], threading.Event()

  def rx():
    while not stop.is_set():
      if sub.poll(50):
        got.append(sub.recv_multipart())
  th = threading.Thread(target=rx, daemon=True); th.start()

  sm = FakeSM()
  raw = synth(5000)
  sm.nav(raw)
  t0 = time.perf_counter(); s.tick(sm, True); cost = time.perf_counter() - t0
  sm.updated = {}
  time.sleep(1.0)
  n = len(s._route_sched.chunks)
  first = [d for tp, d in got if tp == b"route"]
  assert len(first) == n
  h = s._route_sched.hash
  assert reassemble(first)[h] == [[round(a, 6), round(b, 6)] for a, b in sroute.thin(raw)]
  assert all(len(d) <= 1200 for d in first)
  # 10 s later on the stream clock: full resend
  clock["t"] += 10.0
  time.sleep(1.0)
  assert len([d for tp, d in got if tp == b"route"]) == 2 * n
  # cleared
  sm.nav([]); s.tick(sm, True); sm.updated = {}
  time.sleep(0.5)
  last = [d for tp, d in got if tp == b"route"][-1]
  assert json.loads(last) == {"v": 1, "route": {"cleared": True}}
  stop.set(); th.join(1); s.stop(); sub.close(0); ctx.term()
  assert s.errors == 0
  assert cost < 0.05  # one-time host copy of 5000 points
  assert not any(tp not in (b"route", b"state") for tp, _ in got)
