import json
import time
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from openpilot.system.state_stream import config as scfg
from openpilot.system.state_stream import payload
from openpilot.system.state_stream.streamer import StateStreamer

ROOT = Path(__file__).resolve().parents[3]


def _line(n=33, k=1.0):
  return NS(x=[i * k for i in range(n)], y=[0.1 * i for i in range(n)], z=[0.0] * n)


def fake_sm():
  cs = NS(vEgo=20.0, aEgo=-1.2, standstill=False, steeringAngleDeg=3.5, yawRate=0.0, leftBlinker=True, rightBlinker=True,
          leftBlindspot=False, rightBlindspot=True, brakePressed=False, gasPressed=False, gearShifter="drive", doorOpen=False)
  lead = NS(status=True, dRel=40.0, yRel=0.3, vRel=-1.0, aRel=0.0, vLead=19.0, modelProb=0.9, radar=True)
  rs = NS(leadOne=lead, leadTwo=NS(**{**lead.__dict__, "status": False}))
  md = NS(laneLines=[_line() for _ in range(4)], laneLineProbs=[0.1, 0.9, 0.9, 0.1], roadEdges=[_line(), _line()],
          roadEdgeStds=[0.5, 0.6], leadsV3=[NS(prob=0.8, x=[40.0], y=[0.2], v=[19.0])])
  m = lambda v: NS(value=v, std=[1.0, 1.0, 1.0], valid=True)
  lkf = NS(positionGeodetic=m([1.0, 2.0, 3.0]), velocityNED=m([3.0, 4.0, 0.0]), calibratedOrientationNED=m([0.0, 0.0, -1.5708]),
           positionECEF=m([0, 0, 0]), angularVelocityCalibrated=m([0, 0, 0.02]), gpsOK=True, status="valid", unixTimestampMillis=123)
  fpcs = NS(ap1HeadlightsOn=True, ap1HighBeamLever=True, ap1HighBeamOn=False)
  data = {"carState": cs, "radarState": rs, "modelV2": md, "liveLocationKalman": lkf, "frogpilotCarState": fpcs}
  return NS(seen={k: True for k in data}, logMonoTime={k: 1000 + i for i, k in enumerate(data)}, __getitem__=None, d=data)


class SM:
  def __init__(self, ns):
    self.seen, self.logMonoTime, self.d = ns.seen, ns.logMonoTime, ns.d

  def __getitem__(self, k):
    return self.d[k]


def test_config_default_off(tmp_path):
  assert scfg.load(tmp_path).enabled is False
  (tmp_path / "StateStream").write_text("1")
  (tmp_path / "StateStreamHz").write_text("99")
  c = scfg.load(tmp_path)
  assert c.enabled and c.hz == 20.0 and c.port == 8766
  assert scfg.safe_bind_ip("0.0.0.0") is None and scfg.safe_bind_ip("8.8.8.8") is None
  assert scfg.safe_bind_ip("192.168.1.5") == "192.168.1.5"


def test_payload_shape():
  out = payload.build(SM(fake_sm()), 7, include_model=True)
  assert out["v"] == 1 and out["frame"] == 7
  assert set(out["mono"]) == {"carState", "radarState", "modelV2", "liveLocationKalman", "frogpilotCarState"}
  assert out["lights"] == {"low": True, "highArmed": True, "high": False}
  c = out["car"]
  assert c["hazard"] and c["rightBlindspot"] and c["brakeLight"] and c["yawRateLkf"] == 0.02
  assert out["gps"]["speed"] == 5.0 and abs(out["gps"]["headingDeg"] - 270.0) < 0.01 and out["gps"]["valid"]
  assert out["leads"]["leadOne"][:2] == [True, 40.0]
  assert len(out["model"]["laneLines"]) == 4 and len(out["model"]["laneLines"][0]) == 11
  assert "tracks" not in out and "calib" not in out  # not subscribed by host -> not sent
  assert "model" not in payload.build(SM(fake_sm()), 8, include_model=False)
  assert len(json.dumps(out, separators=(",", ":"))) < 4000


class SlowPub:
  def __init__(self, delay):
    self.delay, self.msgs = delay, []

  def send(self, topic, dat):
    time.sleep(self.delay)
    self.msgs.append((topic, json.loads(dat)))

  def close(self):
    pass


def _streamer(tmp_path, pub, hz="20"):
  (tmp_path / "StateStream").write_text("1")
  (tmp_path / "StateStreamHz").write_text(hz)
  s = StateStreamer(cfg_dir=tmp_path, publisher_factory=lambda cfg: pub)
  for _ in range(100):
    if s.active:
      break
    time.sleep(0.02)
  assert s.active
  return s


def test_off_by_default_does_nothing(tmp_path):
  s = StateStreamer(cfg_dir=tmp_path, publisher_factory=lambda cfg: pytest.fail("must not bind"))
  time.sleep(0.1)
  s.tick(SM(fake_sm()), True)
  assert s.queued == 0 and not s.active
  s.stop()


def test_never_blocks_and_drops_when_sender_slow(tmp_path):
  pub = SlowPub(0.5)  # network stalled
  s = _streamer(tmp_path, pub)
  sm = SM(fake_sm())
  worst = 0.0
  for _ in range(40):
    t = time.perf_counter()
    s.tick(sm, True)
    worst = max(worst, time.perf_counter() - t)
    time.sleep(0.05)
  s.stop()
  assert worst < 0.005, worst
  assert s.dropped > 0 and s.errors == 0


def test_rate_limit_20hz_and_model_10hz(tmp_path):
  pub = SlowPub(0.0)
  s = _streamer(tmp_path, pub)
  sm = SM(fake_sm())
  t_end = time.monotonic() + 1.0
  while time.monotonic() < t_end:  # host loop at 100 Hz
    s.tick(sm, True)
    time.sleep(0.01)
  time.sleep(0.3)
  s.stop()
  n = len(pub.msgs)
  nm = sum("model" in m for _, m in pub.msgs)
  assert 17 <= n <= 22, n
  assert 8 <= nm <= 12, nm


def test_not_started_sends_nothing(tmp_path):
  pub = SlowPub(0.0)
  s = _streamer(tmp_path, pub)
  for _ in range(10):
    s.tick(SM(fake_sm()), False)
  s.stop()
  assert s.queued == 0


def test_tick_swallows_errors(tmp_path):
  s = _streamer(tmp_path, SlowPub(0.0))
  bad = SM(fake_sm())
  bad.d["carState"] = None
  s.tick(bad, True)
  s.stop()
  assert s.errors == 1


def test_zmq_loopback_with_token():
  zmq = pytest.importorskip("zmq")
  import sys
  sys.path.insert(0, str(ROOT / "tools/state_stream"))
  import state_client
  from openpilot.system.hevc_stream.hevc_streamd import Publisher
  ctx = zmq.Context()
  pub = Publisher("127.0.0.1", 18766, token="t0k", ctx=ctx)
  cl = state_client.Client("tcp://127.0.0.1:18766", "t0k", ctx=ctx)
  bad = state_client.Client("tcp://127.0.0.1:18766", "wrong", ctx=ctx)
  time.sleep(0.3)
  snap = payload.build(SM(fake_sm()), 1, True)
  got = None
  for _ in range(20):
    pub.send(scfg.TOPIC, json.dumps(snap).encode())
    got = cl.recv(100)
    if got:
      break
  assert got and got["car"]["vEgo"] == 20.0
  assert bad.recv(200) is None
  cl.close(); bad.close(); pub.close(); ctx.term()


def test_host_hook_uses_existing_submaster_only():
  src = (ROOT / "frogpilot/frogpilot_process.py").read_text()
  assert "state_streamer.tick(sm, started)" in src
  import re
  assert not re.search(r"sub_sock|SubMaster\(", (ROOT / "system/state_stream/streamer.py").read_text())
  assert not re.search(r"sub_sock|SubMaster\(", (ROOT / "system/state_stream/payload.py").read_text())
  assert "cloudlog" not in (ROOT / "system/state_stream/payload.py").read_text().split('"""')[-1]
