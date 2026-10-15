"""Replay real rlogs (east drive) through StateStreamer at the host's modelV2 cadence.

Checks: payload values equal the rlog values, ~20 Hz out / ~10 Hz model, and tick()
cost is tiny vs the 50 ms frogpilot_process budget, with a real ZMQ loopback PUB.
"""
import glob
import json
import os
import statistics
import threading
import time
from pathlib import Path

import pytest

DATA = sorted(glob.glob("/workspace/realdata/east/*/rlog"))
pytestmark = pytest.mark.skipif(not DATA, reason="needs /workspace/realdata/east")
ROOT = Path(__file__).resolve().parents[3]
SERVICES = ("carState", "radarState", "modelV2", "liveLocationKalman")


class ReplaySM:
  def __init__(self):
    self.seen = {s: False for s in SERVICES}
    self.logMonoTime = {s: 0 for s in SERVICES}
    self.d = {}

  def __getitem__(self, k):
    return self.d[k]

  def feed(self, e, w):
    self.d[w] = getattr(e, w)
    self.seen[w] = True
    self.logMonoTime[w] = e.logMonoTime


def _frames(seg):
  from cereal import log  # shared schema; a second capnp.load aborts on duplicate IDs
  sm = ReplaySM()
  with open(seg, "rb") as fh:
    raw = fh.read()
  for e in log.Event.read_multiple_bytes(raw, traversal_limit_in_words=2**63 - 1):
    w = e.which()
    if w in SERVICES:
      sm.feed(e, w)
      if w == "modelV2":
        yield sm


def test_replay_values_rate_and_timing(tmp_path):
  import zmq
  import sys
  sys.path.insert(0, str(ROOT / "tools/state_stream"))
  import state_client
  from openpilot.system.hevc_stream.hevc_streamd import Publisher
  from openpilot.system.state_stream.streamer import StateStreamer

  ctx = zmq.Context()
  import socket as _s
  with _s.socket() as so:
    so.bind(("127.0.0.1", 0)); port = so.getsockname()[1]
  (tmp_path / "StateStream").write_text("1")
  s = StateStreamer(cfg_dir=tmp_path, publisher_factory=lambda cfg: Publisher("127.0.0.1", port, ctx=ctx))
  cl = state_client.Client(f"tcp://127.0.0.1:{port}", ctx=ctx)
  for _ in range(100):
    if s.active:
      break
    time.sleep(0.02)
  assert s.active
  got = []
  stop = threading.Event()

  def rx():
    while not stop.is_set():
      m = cl.recv(100)
      if m:
        got.append(m)
  th = threading.Thread(target=rx, daemon=True)
  th.start()
  time.sleep(0.3)

  # simulated clock = rlog modelV2 time, so rate limiting follows the drive
  sim = {"t": 0.0}
  s._clock = lambda: sim["t"]
  costs, expect = [], {}
  seg = DATA[len(DATA) // 2]
  t_wall0 = time.monotonic()
  for sm in _frames(seg):
    sim["t"] = sm.logMonoTime["modelV2"] / 1e9
    f = s.frame
    t = time.perf_counter()
    s.tick(sm, True)
    costs.append(time.perf_counter() - t)
    if s.frame != f:
      cs = sm["carState"]
      expect[f] = (round(cs.vEgo, 3), round(cs.steeringAngleDeg, 2), sm.logMonoTime["carState"])
    time.sleep(0.05 * 0.2)  # replay 5x real time
  time.sleep(0.5)
  stop.set(); th.join(1); s.stop(); cl.close(); ctx.term()

  drive_s = (len(costs)) / 20.0
  p50, p99, mx = statistics.median(costs) * 1e3, sorted(costs)[int(len(costs) * 0.99)] * 1e3, max(costs) * 1e3
  print(f"\nseg {os.path.basename(os.path.dirname(seg))}: {len(costs)} host frames, sent {s.sent}, dropped {s.dropped}, "
        f"recv {len(got)}; tick ms p50 {p50:.3f} p99 {p99:.3f} max {mx:.3f}; "
        f"avg bytes {statistics.mean(len(json.dumps(m)) for m in got):.0f}")
  assert s.errors == 0
  assert 0.9 * 20 * drive_s <= s.frame <= 1.05 * 20 * drive_s
  nm = sum("model" in m for m in got)
  assert 0.4 * len(got) <= nm <= 0.6 * len(got)
  assert len(got) >= 0.9 * s.sent
  for m in got:
    v, a, mono = expect[m["frame"]]
    assert m["car"]["vEgo"] == v and m["car"]["steeringAngleDeg"] == a and m["mono"]["carState"] == mono
  assert p99 < 2.0 and mx < 10.0  # host budget is 50 ms per frame
