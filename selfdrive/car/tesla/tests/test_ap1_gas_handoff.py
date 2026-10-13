from openpilot.selfdrive.car.tesla.long_smooth import AP1_GAS_HANDOFF_MAX, Ap1AccelSmoother


def _press(s, a_ego, v=15.0, n=20):
  for _ in range(n):
    assert s.update(0.8, True, v, gas_neutral=True, a_ego=a_ego) == 0.0


def test_release_continues_from_measured_accel_no_zero_dip():
  s = Ap1AccelSmoother()
  _press(s, 1.2)
  out = [s.update(1.5, True, 15.0) for _ in range(5)]
  assert out[0] >= 1.2 and min(out) >= 1.2


def test_release_into_lower_plan_uses_soft_release():
  s = Ap1AccelSmoother()
  _press(s, 1.2)
  first = s.update(0.0, True, 15.0)
  assert 1.1 < first < 1.2  # soft start, not a step to 0


def test_hard_brake_after_release_passes_through():
  s = Ap1AccelSmoother()
  _press(s, 1.2)
  assert s.update(-2.5, True, 15.0) == -2.5


def test_fcw_ignores_seed():
  s = Ap1AccelSmoother()
  _press(s, 1.2)
  assert s.update(-1.5, True, 15.0, urgent=True, fcw=True) == -1.5


def test_seed_clipped_and_low_speed_ignored():
  s = Ap1AccelSmoother()
  _press(s, 5.0)
  assert abs(s.update(3.0, True, 15.0) - AP1_GAS_HANDOFF_MAX) < 0.03
  s = Ap1AccelSmoother()
  _press(s, 1.0, v=0.5)
  assert s.update(0.3, True, 0.5) == 0.3
  s = Ap1AccelSmoother()
  _press(s, -1.0)
  assert s.update(0.5, True, 15.0) <= 0.5


def test_inactive_clears_seed():
  s = Ap1AccelSmoother()
  _press(s, 1.2)
  s.update(0.0, False, 15.0)
  assert s.update(1.5, True, 15.0) == 1.5  # fresh seed from target, old behavior
