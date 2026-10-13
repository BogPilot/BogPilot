import collections

from openpilot.selfdrive.car.tesla.actuator_plan import build_actuator_plan
from openpilot.selfdrive.car.tesla.engage_ramp import (Ap1EngageSteerRamp, RAMP_MAX_S, RAMP_MIN_S, ramp_duration,
                                                        smoothstep)


def test_duration_scales_with_error():
  assert ramp_duration(0.5) == RAMP_MIN_S
  assert ramp_duration(2.0) == RAMP_MIN_S
  assert RAMP_MIN_S < ramp_duration(15.0) < RAMP_MAX_S
  assert ramp_duration(-90.0) == RAMP_MAX_S


def test_smoothstep_ends():
  assert smoothstep(0.0) == 0.0 and smoothstep(1.0) == 1.0 and smoothstep(2.0) == 1.0


def test_ramp_starts_at_measured_and_reaches_desired():
  r = Ap1EngageSteerRamp()
  outs = [r.update(True, 10.0, 0.0) for _ in range(int(ramp_duration(10.0) / 0.01) + 2)]
  assert abs(outs[0] - 10.0) < 0.1
  assert all(b <= a + 1e-9 for a, b in zip(outs, outs[1:]))
  assert outs[-1] == 0.0 and not r.active


def test_ramp_tracks_moving_desired_at_end():
  r = Ap1EngageSteerRamp()
  for i in range(200):
    out = r.update(True, 5.0, -5.0 + 0.01 * i)
  assert out == -5.0 + 0.01 * 199


def test_small_error_no_ramp():
  r = Ap1EngageSteerRamp()
  assert r.update(True, 0.5, 0.0) == 0.0 and not r.active


def test_path_inactive_cancels_and_rearms():
  r = Ap1EngageSteerRamp()
  r.update(True, 20.0, 0.0)
  assert r.active
  # Driver override / hands pause: path inactive, ramp cancelled at once.
  assert r.update(False, 20.0, 0.0) == 0.0 and not r.active
  # Lat back: new ramp from the measured wheel at that moment.
  out = r.update(True, -8.0, 0.0)
  assert r.active and abs(out + 8.0) < 0.1


def _plan(lat, meas, req, last, v, hands=0):
  return build_actuator_plan(0, lat, False, True, True, False, meas, req, last, v, 0.0, 0, collections.deque(), False,
                             chassis_das_only=True, epas_error="EAC_ERROR_IDLE", eac_fault=False,
                             hands_on_level=hands, eac_status="EAC_ACTIVE")


def test_ramp_never_steps_more_than_unramped_and_driver_wins():
  for v in (1.0, 4.5, 10.0, 20.0):
    r = Ap1EngageSteerRamp()
    last_a = last_b = 15.0
    steps_a, steps_b = [], []
    for _ in range(150):
      a = _plan(True, 15.0, r.update(True, 15.0, 0.0), last_a, v)
      b = _plan(True, 15.0, 0.0, last_b, v)
      steps_a.append(abs(a.apply_angle_last - last_a))
      steps_b.append(abs(b.apply_angle_last - last_b))
      last_a, last_b = a.apply_angle_last, b.apply_angle_last
    # Peak step only gets smaller, and the ramp still lands on the target.
    assert max(steps_a) <= max(steps_b) + 1e-9
    assert abs(last_a) < 1e-6
  # Hands at the pause level: NONE at the measured wheel regardless of the ramp.
  p = _plan(True, 7.0, 3.0, 3.0, 10.0, hands=2)
  assert p.steer is not None and p.steer.enabled is False and p.steer.angle_deg == 7.0
