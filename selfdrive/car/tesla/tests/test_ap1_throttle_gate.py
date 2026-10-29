"""AP1 allowThrottle smoothing (tesla/throttle_gate.py)."""
import random

import pytest

from openpilot.selfdrive.car.tesla.throttle_gate import (
  AP1_THROTTLE_CUT_TAU, AP1_THROTTLE_RELEASE_TAU, DT_MDL, Ap1ThrottleCap, ap1_throttle_gate_for, apply_cut,
)

A_UP = DT_MDL / (AP1_THROTTLE_CUT_TAU + DT_MDL)
A_DN = DT_MDL / (AP1_THROTTLE_RELEASE_TAU + DT_MDL)


def _run(seq, **kw):
  g = Ap1ThrottleCap()
  return [g.update(x, **kw) for x in seq]


def test_only_ap1_gets_a_gate():
  assert isinstance(ap1_throttle_gate_for("TESLA_AP1_MODELS"), Ap1ThrottleCap)
  assert ap1_throttle_gate_for("TESLA_AP2_MODELS") is None
  assert ap1_throttle_gate_for("TOYOTA_RAV4") is None


def test_single_frame_blip_moves_cap_partially_and_decays():
  # 00000030 18:39:45.69 ET: allowThrottle false for one 50 ms plan step.
  out = _run([True] * 5 + [False] + [True] * 20)
  assert out[4] == 0.0
  assert out[5] == pytest.approx(A_UP)
  assert out[6] == pytest.approx(A_UP * (1 - A_DN))
  assert all(b < a for a, b in zip(out[5:], out[6:], strict=False))


def test_sustained_no_throttle_reaches_coast_cap():
  out = _run([False] * 20)
  assert out[8] >= 0.9  # ~0.45 s
  assert out[-1] > 0.99


def test_chatter_keeps_most_of_the_cap():
  # The e3574753 chatter acted as early caution (0000002e 16:09 ET).
  out = _run([True, False] * 40)
  assert min(out[20:]) > 0.6


def test_low_speed_and_reset_follow_raw_flag():
  g = Ap1ThrottleCap()
  assert g.update(False, low_speed=True) == 1.0
  assert g.update(True, low_speed=True) == 0.0
  assert g.update(False, reset=True) == 1.0
  assert g.update(True, reset=True) == 0.0


def test_cut_stays_in_unit_interval():
  rnd = random.Random(3)
  g = Ap1ThrottleCap()
  for _ in range(5000):
    c = g.update(rnd.random() < 0.5, low_speed=rnd.random() < 0.02, reset=rnd.random() < 0.01)
    assert 0.0 <= c <= 1.0


def test_apply_cut_matches_stock_at_0_and_1():
  for a_max, coast in ((2.0, -0.3), (0.5, -0.3), (-0.5, -0.3), (1.2, 0.4)):
    assert apply_cut(a_max, coast, 0.0) == a_max
    assert apply_cut(a_max, coast, 1.0) == min(a_max, coast)  # stock planner, exactly


def test_apply_cut_is_monotonic_and_never_raises_the_limit():
  for a_max, coast in ((2.0, -0.3), (0.2, -0.3), (-0.5, -0.3)):
    prev = None
    for i in range(101):
      lim = apply_cut(a_max, coast, i / 100)
      assert lim <= a_max + 1e-12
      assert lim >= min(a_max, coast) - 1e-12
      if prev is not None:
        assert lim <= prev + 1e-12
      prev = lim
