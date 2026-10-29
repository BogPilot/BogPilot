#!/usr/bin/env python3
"""replay_ap1.py on a tiny synthetic AP1 log (built here; no real drive data, no PII).

Timeline (seconds): 0-1 panda in elm327 (not replayed), 1 panda -> tesla 10,
3 stock cruise ENABLED (engage), openpilot steers 3-5 with one over-rate frame
at 4.0, 5 brake pressed while moving (disengage). Stock 0x488 comes from bus 2
the whole time. In the "old controller" variant openpilot keeps sending type
NONE 0x488 after the disengage, which drops the stock frame (2026-10-04 bug
class) and must be flagged.
"""
import bz2
import os
import tempfile
import unittest
from pathlib import Path

from panda.tests.safety_replay import replay_ap1 as R

DT = 0.01  # one can event per 10 ms
RX_PERIODS = {(0x2b9, 2): 0.04, (0x370, 0): 0.04, (0x108, 0): 0.01, (0x118, 0): 0.01, (0x20a, 0): 0.02,
              (0x368, 0): 0.1, (0x318, 0): 0.1, (0x488, 2): 0.02}
SPEED = 10.0  # m/s


def _speed(v):
  raw = int(round((v / 0.447 + 25) / 0.05))
  return bytes([0, 0, raw & 0xFF, (raw >> 8) & 0x0F, 0, 0])


def _steer(angle_deg, typ):
  raw = int(round(angle_deg * 10)) + 16384
  return bytes([(raw >> 8) & 0x7F, raw & 0xFF, typ << 6, 0])


def _rx_frame(addr, bus, t):
  if addr == 0x118:
    return _speed(SPEED)
  if addr == 0x20a:
    return bytes([(2 if t >= 5.0 else 1) << 2, 0, 0, 0, 0, 0, 0, 0])  # driverBrakeStatus
  if addr == 0x368:
    return bytes([0, (2 if 3.0 <= t < 5.0 else 1) << 4, 0, 0, 0, 0, 0, 0])  # DI_cruiseState
  if addr == 0x370:
    raw = 8192  # wheel at 0 deg
    return bytes([0, 0, 0, 0, (raw >> 8) & 0x3F, raw & 0xFF, 0, 0])
  if addr == 0x488:
    return _steer(0, 0)  # stock DAS, type NONE
  return bytes(8)


def build_log(old_controller=False, duration=7.0):
  log = R.load_log_schema()
  out = []

  ev = log.Event.new_message(logMonoTime=1)
  ev.init("initData").version = "synthetic"
  out.append(ev.to_bytes())
  ev = log.Event.new_message(logMonoTime=1)
  cp = ev.init("carParams")
  sc = cp.init("safetyConfigs", 1)
  sc[0].safetyModel = "tesla"
  sc[0].safetyParam = 10
  out.append(ev.to_bytes())

  t0 = 10_000_000_000
  nxt = dict.fromkeys(RX_PERIODS, 0.0)
  n = int(duration / DT)
  for i in range(n):
    t = i * DT
    mono = t0 + int(t * 1e9)
    if i % 10 == 0:
      ev = log.Event.new_message(logMonoTime=mono)
      ps = ev.init("pandaStates", 1)
      ps[0].safetyModel = "tesla" if t >= 1.0 else "elm327"
      ps[0].safetyParam = 10 if t >= 1.0 else 0
      ps[0].controlsAllowed = 3.0 < t <= 5.0  # sampled just before the can event at the same time
      out.append(ev.to_bytes())
    frames = []
    for (addr, bus), period in RX_PERIODS.items():
      if t + 1e-9 >= nxt[(addr, bus)]:
        frames.append((addr, bus, _rx_frame(addr, bus, t)))
        nxt[(addr, bus)] += period
    ev = log.Event.new_message(logMonoTime=mono)
    can = ev.init("can", len(frames))
    for c, (addr, bus, dat) in zip(can, frames, strict=True):
      c.address, c.src, c.dat = addr, bus, dat
    out.append(ev.to_bytes())

    sends = []
    if i % 2 == 0:
      if 3.02 <= t < 5.0:
        angle = 9.0 if abs(t - 4.0) < 0.005 else 0.0  # one 9 deg jump at 4.0 s: over the AP1 rate limit
        sends.append((0x488, 0, _steer(angle, 1)))
      elif old_controller and t >= 5.0:
        sends.append((0x488, 0, _steer(0, 0)))
    ev = log.Event.new_message(logMonoTime=mono + 500_000)
    sc = ev.init("sendcan", len(sends))
    for c, (addr, bus, dat) in zip(sc, sends, strict=True):
      c.address, c.src, c.dat = addr, bus, dat
    out.append(ev.to_bytes())
  return b"".join(out)


def replay(path, **kw):
  lp, lib = R.load_libpanda(None)
  return R.run_quiet(R.Ap1SafetyReplay(lp, lib, **kw), [Path(path)])


class TestReplayAp1(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.tmp = tempfile.TemporaryDirectory()
    cls.dir = Path(cls.tmp.name)
    cls.raw = build_log()
    (cls.dir / "rlog").write_bytes(cls.raw)
    (cls.dir / "old.rlog").write_bytes(build_log(old_controller=True))

  @classmethod
  def tearDownClass(cls):
    cls.tmp.cleanup()

  def test_starts_when_panda_enters_tesla(self):
    r = replay(self.dir / "rlog")
    self.assertEqual(len(r.active_spans), 1)
    self.assertAlmostEqual(r.active_spans[0]["t0"], 1.0, delta=0.02)
    self.assertEqual(r.param, 10)

  def test_engage_and_disengage_causes(self):
    r = replay(self.dir / "rlog")
    ev = [(x["event"], x["cause"]) for x in r.transitions]
    self.assertEqual(len(ev), 2, ev)
    self.assertEqual(ev[0][0], "ENGAGE")
    self.assertIn("STANDBY -> ENABLED", ev[0][1])
    self.assertAlmostEqual(r.transitions[0]["t"], 3.0, delta=0.11)
    self.assertEqual(ev[1][0], "DISENGAGE")
    self.assertIn("brake", ev[1][1])
    self.assertAlmostEqual(r.transitions[1]["t"], 5.0, delta=0.03)
    self.assertEqual(r.controls_agree, r.controls_samples)  # matches the synthetic pandaStates

  def test_over_rate_steer_frame_blocked_with_reason(self):
    r = replay(self.dir / "rlog")
    # the 9 deg jump at 4.00 s and the jump back at 4.02 s are both over the AP1 rate limit at 10 m/s
    self.assertEqual(sum(r.tx_blocked_controls.values()), 2)
    self.assertEqual([round(float(e["where"].split("s")[0]), 2) for e in r.tx_examples], [4.0, 4.02])
    for e in r.tx_examples:
      self.assertIn("angle rate", e["reason"])
    self.assertIn(("FAIL", "2 openpilot TX frames blocked while controls were allowed"), r.flags)

  def test_stock_forwarding_follows_substitution(self):
    r = replay(self.dir / "rlog")
    runs = [(x.state, round(x.t0), round(x.t1)) for x in r.fwd_runs[0x488]]
    self.assertEqual([s for s, _, _ in runs], ["forwarded", "blocked", "forwarded"], runs)
    self.assertEqual(r.fwd_disengaged_blocked[0x488], 0)
    self.assertEqual(r.fwd_counts[0x2b9]["blocked"], 0)  # no openpilot 0x2b9 sent

  def test_old_controller_flagged(self):
    r = replay(self.dir / "old.rlog")
    self.assertGreater(r.fwd_disengaged_blocked[0x488], 0)
    self.assertTrue(any(lvl == "FAIL" and "dropped from the chassis" in m for lvl, m in r.flags), r.flags)
    # rx-only isolates the safety code: stock frames flow again
    r = replay(self.dir / "old.rlog", rx_only=True)
    self.assertEqual(r.fwd_disengaged_blocked[0x488], 0)

  def test_compressed_inputs_match(self):
    (self.dir / "z").mkdir(exist_ok=True)
    (self.dir / "z" / "rlog.bz2").write_bytes(bz2.compress(self.raw))
    base = replay(self.dir / "rlog").as_dict()
    got = replay(self.dir / "z" / "rlog.bz2").as_dict()
    strip = lambda d: [{k: v for k, v in x.items() if k != "where"} for x in d["transitions"]]  # noqa: E731
    self.assertEqual(strip(base), strip(got))
    for k in ("tx_blocked", "fwd_counts"):
      self.assertEqual(base[k], got[k], k)
    try:
      import zstandard
    except ImportError:
      return
    (self.dir / "z" / "rlog.zst").write_bytes(zstandard.ZstdCompressor().compress(self.raw))
    got = replay(self.dir / "z" / "rlog.zst").as_dict()
    self.assertEqual(base["fwd_counts"], got["fwd_counts"])

  def test_cli_exit_codes(self):
    with open(os.devnull, "w") as devnull:
      import contextlib
      with contextlib.redirect_stdout(devnull):
        self.assertEqual(R.main([str(self.dir / "rlog"), "--summary"]), 1)  # the over-rate frame is a FAIL
        self.assertEqual(R.main([str(self.dir / "nope")]), 2)


if __name__ == "__main__":
  unittest.main()
