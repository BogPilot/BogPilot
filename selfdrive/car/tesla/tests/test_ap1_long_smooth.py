"""AP1 comfort-band accel slew (long_smooth.py) and its CarController wiring."""
import math
import random

import pytest

from openpilot.selfdrive.car.tesla.long_smooth import (
  AP1_BRAKE_BYPASS_ACCEL, AP1_BRAKE_HARD_ACCEL, AP1_BRAKE_ONSET_JERK, AP1_BRAKE_URGENT_ACCEL, AP1_COMFORT_JERK_LIMIT,
  AP1_DRIVE_RELEASE_BRAKE_JERK, AP1_DRIVE_RELEASE_JERK, AP1_DRIVE_RELEASE_RAMP_S,
  AP1_FALL_JERK, AP1_FULL_JERK_LIMIT, AP1_JERK_BLEND_START, AP1_JERK_LIMIT_NARROW_RATE, AP1_RISE_JERK, AP1_SLEW_MIN_SPEED,
  AP1_LAUNCH_JERK_MAX, ap1_fcw,
  Ap1AccelSmoother, Ap1JerkLimit, ap1_brake_ramp_jerk, ap1_brake_urgent, ap1_jerk_limit_target,
)
from openpilot.selfdrive.car.tesla.values import CarControllerParams

UP = AP1_RISE_JERK * 0.01
DN = AP1_FALL_JERK * 0.01
DN_BRAKE = AP1_DRIVE_RELEASE_BRAKE_JERK * 0.01  # e3574753 drive release rate
ON = AP1_BRAKE_ONSET_JERK * 0.01
V = 10.0


def _run(seq, v=V, active=True):
  s = Ap1AccelSmoother()
  return [s.update(a, active, v) for a in seq]


def test_steady_and_normal_ramps_pass_unchanged():
  up = [0.01 * i for i in range(100)]  # 1 m/s^3
  assert _run(up) == pytest.approx(up)
  down = [-0.02 * i for i in range(100)]  # 2 m/s^3 into braking
  assert _run(down) == pytest.approx(down)
  assert _run([0.3] * 50) == pytest.approx([0.3] * 50)


def test_step_up_is_slewed():
  out = _run([0.0] + [0.5] * 30)
  assert out[1] == pytest.approx(UP)
  assert out[10] == pytest.approx(10 * UP)
  assert out[-1] == pytest.approx(0.5)


def test_brake_blip_reversal_is_smoothed():
  # 02a 158.2: +0.48 -> -0.44 (one plan step) -> -0.16
  seq = [0.48] * 5 + [-0.44] * 5 + [-0.16] * 100
  out = _run(seq)
  assert 0.48 - DN_BRAKE < out[5] < 0.48  # soft start of the drive release
  assert min(out) >= -0.16 - 1e-9  # the -0.44 blip is not passed on
  assert out[-1] == pytest.approx(-0.16)


@pytest.mark.parametrize("target", [AP1_BRAKE_HARD_ACCEL, -2.5, -3.5])
def test_hard_braking_is_never_delayed(target):
  assert _run([0.4, target])[1] == target
  assert _run([-0.2, target])[1] == target
  assert _run([1.5, target])[1] == target


@pytest.mark.parametrize("target", [-0.5, -0.9, -1.5, -1.95])
def test_moderate_braking_ramps_in(target):
  out = _run([0.0] + [target] * 100)
  assert out[1] == pytest.approx(-ap1_brake_ramp_jerk(target) * 0.01)
  assert out[1] > target
  assert out[-1] == pytest.approx(target)


def test_comfort_band_braking_ramps_at_onset_jerk():
  out = _run([0.0] + [-0.45] * 30)
  assert out[1] == pytest.approx(-ON)
  assert out[10] == pytest.approx(-10 * ON)
  n = math.ceil(0.45 / ON - 1e-9)
  assert out[n - 1] > -0.45
  assert out[n] == pytest.approx(-0.45)


def _e357_reference(start, target, n=300):
  """e3574753 smoother (drive release at 5 m/s^3, no soft start) for comparison."""
  out, last = [], start
  for _ in range(n):
    if target >= last:
      last = min(target, last + UP)
    elif target <= AP1_BRAKE_HARD_ACCEL:
      last = target
    else:
      j = ap1_brake_ramp_jerk(target)
      if last > 0.0:
        j = max(AP1_DRIVE_RELEASE_BRAKE_JERK, j)
      last = max(target, last - j * 0.01)
    out.append(last)
  return out


def test_lead_pickup_step_e1_drive_release_then_regen_ramp():
  # 0000002e 16:09:22.12 ET: aTarget +1.37 -> -0.88 in one plan step (86 m lead).
  out = _run([1.37] * 5 + [-0.88] * 100)
  assert 1.37 - DN_BRAKE < out[5] < 1.37  # soft start
  k0 = next(i for i, o in enumerate(out) if o <= 0.0)
  assert 0.25 <= (k0 - 4) * 0.01 <= 0.4
  for a, b in zip(out[k0 + 1:], out[k0 + 2:]):
    assert b >= a - ON - 1e-12  # regen ramps in no faster than the onset jerk
  k1 = next(i for i, o in enumerate(out) if o <= -0.88 + 1e-9)
  assert (k1 - 4) * 0.01 <= 0.86  # e3574753: 0.72
  ref = _e357_reference(1.37, -0.88)
  k1_ref = next(i for i, o in enumerate(ref) if o <= -0.88 + 1e-9)
  assert (k1 - 5) - k1_ref <= 13  # at most ~0.13 s later than e3574753


def test_pure_lift_is_gentle_s_curve():
  # Lift to the hold (0 = set speed): no regen, 2.0 m/s^3 peak, soft start and end.
  out = _run([1.3] * 5 + [0.0] * 150)
  d = [(a - b) / 0.01 for a, b in zip(out[4:], out[5:], strict=False)]  # release jerk per step
  assert max(d) <= AP1_DRIVE_RELEASE_JERK + 1e-9
  assert d[0] <= AP1_DRIVE_RELEASE_JERK * 0.01 / AP1_DRIVE_RELEASE_RAMP_S + 1e-9  # soft start
  k0 = next(i for i, o in enumerate(out) if o <= 1e-9)
  assert 0.7 <= (k0 - 4) * 0.01 <= 0.9  # e3574753: 0.26 s
  assert min(out) >= 0.0  # never undershoots into regen
  assert d[k0 - 5] < 1.0  # taper at the end
  for a, b in zip(d[:k0 - 6], d[1:k0 - 5], strict=False):  # (the arrival step lands exactly on the request)
    assert abs(b - a) <= 1.2 * AP1_DRIVE_RELEASE_JERK / AP1_DRIVE_RELEASE_RAMP_S * 0.01  # no jerk steps


def test_partial_lift_stops_at_request():
  out = _run([1.0] * 5 + [0.4] * 100)
  assert min(out) >= 0.4 - 1e-9
  assert out[-1] == pytest.approx(0.4)


def test_lift_into_regen_has_no_jerk_step_at_zero():
  out = _run([1.0] * 5 + [-0.3] * 150)
  k0 = next(i for i, o in enumerate(out) if o <= 0.0)
  before = (out[k0 - 2] - out[k0 - 1]) / 0.01
  after = (out[k0] - out[k0 + 1]) / 0.01
  assert before == pytest.approx(after, abs=0.25)  # blends back to the onset jerk at 0
  assert after == pytest.approx(AP1_BRAKE_ONSET_JERK)


@pytest.mark.parametrize("target", [-1.2, -1.5, -1.95])
def test_urgent_ish_braking_from_drive_has_no_soft_start(target):
  out = _run([1.0] * 5 + [target] * 5)
  assert out[5] == pytest.approx(1.0 - max(AP1_DRIVE_RELEASE_BRAKE_JERK, ap1_brake_ramp_jerk(target)) * 0.01)


@pytest.mark.parametrize("target,extra_steps", [(-0.3, 25), (-0.5, 13), (-0.9, 13), (-1.0, 13)])
def test_lift_into_braking_is_bounded_vs_e3574753(target, extra_steps):
  out = _run([1.3] * 5 + [target] * 200)[5:]
  ref = _e357_reference(1.3, target, 200)
  k = next(i for i, o in enumerate(out) if o <= target + 1e-9)
  k_ref = next(i for i, o in enumerate(ref) if o <= target + 1e-9)
  assert k - k_ref <= extra_steps


def test_release_state_resets_when_request_rises():
  s = Ap1AccelSmoother()
  s.update(1.0, True, V)
  for _ in range(20):
    s.update(0.0, True, V)
  assert s.release_j > 0.0
  s.update(1.0, True, V)
  assert s.release_j == 0.0
  first = s.last - s.update(0.0, True, V)
  assert first == pytest.approx(AP1_DRIVE_RELEASE_JERK / AP1_DRIVE_RELEASE_RAMP_S * 0.01 * 0.01)


@pytest.mark.parametrize("start,target,limit_s", [
  (0.0, -0.9, 0.46), (0.0, -1.0, 0.51), (0.0, -1.5, 0.31), (0.0, -1.95, 0.27),
  # From drive: the release soft start / join adds up to ~0.13 s to -1.0 (e3574753: 0.81 / 0.91).
  (1.5, -1.0, 0.95), (1.5, -1.5, 0.61), (2.0, -1.0, 1.04),
])
def test_time_to_reach_braking_is_bounded(start, target, limit_s):
  s = Ap1AccelSmoother()
  s.update(start, True, V)
  for i in range(1, 200):
    if s.update(target, True, V) <= target + 1e-9:
      break
  assert i * 0.01 <= limit_s


def test_ramp_jerk_blend():
  assert ap1_brake_ramp_jerk(0.0) == AP1_BRAKE_ONSET_JERK
  assert ap1_brake_ramp_jerk(AP1_BRAKE_URGENT_ACCEL) == AP1_BRAKE_ONSET_JERK
  assert ap1_brake_ramp_jerk(AP1_BRAKE_HARD_ACCEL) == AP1_FULL_JERK_LIMIT
  xs = [0.2 - 0.001 * i for i in range(2600)]
  ys = [ap1_brake_ramp_jerk(x) for x in xs]
  for y0, y1 in zip(ys, ys[1:]):
    assert y0 - 1e-12 <= y1 <= y0 + 0.01  # monotonic, no step


def test_urgent_passes_falling_through_but_not_rising():
  s = Ap1AccelSmoother()
  s.update(0.5, True, V)
  assert s.update(-0.9, True, V, urgent=True) == -0.9
  assert s.update(0.5, True, V, urgent=True) == pytest.approx(-0.9 + UP)


def test_release_from_braking_is_slewed():
  out = _run([-2.0, -0.2, -0.2])
  assert out[1] == pytest.approx(-2.0 + UP)


def test_property_bounded_and_hard_band_never_less_braking():
  rnd = random.Random(7)
  s = Ap1AccelSmoother()
  a = 0.0
  for _ in range(20000):
    a = rnd.uniform(-3.5, 2.0) if rnd.random() < 0.1 else max(-3.5, min(2.0, a + rnd.uniform(-0.3, 0.3)))
    prev = s.last
    out = s.update(a, True, V)
    if prev is not None:
      assert min(prev, a) - 1e-12 <= out <= max(prev, a) + 1e-12
      assert out <= prev + UP + 1e-12
      if a <= AP1_BRAKE_HARD_ACCEL:
        assert out <= a + 1e-12
      elif a < prev and prev <= 0:
        assert out == pytest.approx(max(a, prev - ap1_brake_ramp_jerk(a) * 0.01))
      elif a < prev:
        # Drive release: falls, never past the request, never faster than the
        # brake-intent release / regen ramp jerk.
        jmax = max(AP1_DRIVE_RELEASE_BRAKE_JERK, ap1_brake_ramp_jerk(a))
        assert max(a, prev - jmax * 0.01) - 1e-12 <= out < prev


def test_inactive_resets_and_passes_through():
  s = Ap1AccelSmoother()
  s.update(0.0, True, V)
  assert s.update(1.0, False, V) == 1.0
  assert s.last is None
  assert s.update(0.8, True, V) == 0.8  # first active frame seeds


def test_gas_neutral_outputs_zero_and_release_ramps_from_zero():
  s = Ap1AccelSmoother()
  s.update(0.6, True, V)
  assert s.update(0.6, True, V, gas_neutral=True) == 0.0
  assert s.update(0.6, True, V) == pytest.approx(UP)


def test_low_speed_passes_through():
  s = Ap1AccelSmoother()
  s.update(-2.0, True, 0.0)
  assert s.update(0.3, True, AP1_SLEW_MIN_SPEED - 0.01) == 0.3
  assert s.update(0.8, True, AP1_SLEW_MIN_SPEED + 0.5) == pytest.approx(0.3 + UP)


# --- Jerk fields -------------------------------------------------------------

def test_full_jerk_matches_existing_constant():
  assert AP1_FULL_JERK_LIMIT == CarControllerParams.JERK_LIMIT_MAX == -CarControllerParams.JERK_LIMIT_MIN


@pytest.mark.parametrize("a", [AP1_BRAKE_BYPASS_ACCEL, -0.8, -2.0, -3.5])
def test_jerk_full_at_or_below_bypass(a):
  assert ap1_jerk_limit_target(a) == AP1_FULL_JERK_LIMIT


@pytest.mark.parametrize("a", [AP1_JERK_BLEND_START, -0.1, 0.0, 0.5, 2.0])
def test_jerk_comfort_in_band(a):
  assert ap1_jerk_limit_target(a) == AP1_COMFORT_JERK_LIMIT


def test_jerk_blend_is_continuous_and_monotonic():
  xs = [AP1_BRAKE_BYPASS_ACCEL - 0.1 + 0.001 * i for i in range(400)]
  ys = [ap1_jerk_limit_target(x) for x in xs]
  for y0, y1 in zip(ys, ys[1:]):
    assert y1 <= y0 + 1e-12
    assert abs(y1 - y0) < 0.05  # no step in the limit itself


def test_jerk_widens_immediately_and_narrows_at_rate():
  j = Ap1JerkLimit()
  assert j.update(0.2, True, V) == (-(AP1_FULL_JERK_LIMIT - AP1_JERK_LIMIT_NARROW_RATE * 0.01),
                                    AP1_FULL_JERK_LIMIT - AP1_JERK_LIMIT_NARROW_RATE * 0.01)
  for _ in range(100):
    lo, hi = j.update(0.2, True, V)
  assert (lo, hi) == (-AP1_COMFORT_JERK_LIMIT, AP1_COMFORT_JERK_LIMIT)
  assert j.update(-0.6, True, V) == (-AP1_FULL_JERK_LIMIT, AP1_FULL_JERK_LIMIT)  # same step


def test_jerk_full_immediately_when_urgent():
  j = Ap1JerkLimit()
  for _ in range(200):
    j.update(0.1, True, V)
  assert j.update(0.1, True, V, urgent=True) == (-AP1_FULL_JERK_LIMIT, AP1_FULL_JERK_LIMIT)


def test_brake_urgent_from_carcontrol():
  from cereal import car
  CC = car.CarControl.new_message()
  assert not ap1_brake_urgent(CC.as_reader())
  CC.hudControl.visualAlert = car.CarControl.HUDControl.VisualAlert.fcw
  assert ap1_brake_urgent(CC.as_reader())
  CC = car.CarControl.new_message()
  CC.actuators.longControlState = car.CarControl.Actuators.LongControlState.stopping
  assert ap1_brake_urgent(CC.as_reader())
  CC.actuators.longControlState = car.CarControl.Actuators.LongControlState.pid
  CC.hudControl.visualAlert = car.CarControl.HUDControl.VisualAlert.steerRequired
  assert not ap1_brake_urgent(CC.as_reader())


def test_jerk_full_when_inactive_or_gas_neutral():
  j = Ap1JerkLimit()
  for _ in range(200):
    out = j.update(0.1, False, V)
  assert out == (-AP1_FULL_JERK_LIMIT, AP1_FULL_JERK_LIMIT)
  j = Ap1JerkLimit()
  for _ in range(200):
    j.update(0.1, True, V)
  assert j.update(0.1, True, V, gas_neutral=True) == (-AP1_FULL_JERK_LIMIT, AP1_FULL_JERK_LIMIT)


def test_jerk_fields_pack_inside_dbc_range():
  # DAS_jerkMin 9 bit (0.03, -15.232), DAS_jerkMax 8 bit (0.059, 0)
  for v in (-AP1_COMFORT_JERK_LIMIT, -AP1_FULL_JERK_LIMIT):
    assert 0 <= round((v + 15.232) / 0.03) < 512
  for v in (AP1_COMFORT_JERK_LIMIT, AP1_FULL_JERK_LIMIT):
    assert 0 <= round(v / 0.059) < 256


# --- CarController wiring (stub packer, same pattern as test_ap1_resume_hold) ---

from openpilot.selfdrive.car.tesla.tests.test_ap1_resume_hold import cc_mod  # noqa: E402,F401


def _das(cc_mod, accels, v=V, gas=False, fcw_from=None, standstill=False):
  import ast
  from collections import deque
  from types import SimpleNamespace
  from cereal import car
  from openpilot.selfdrive.car.tesla.values import CAR
  ctl = cc_mod.CarController("tesla_can", SimpleNamespace(
    carFingerprint=CAR.TESLA_AP1_MODELS, openpilotLongitudinalControl=True), None)
  ctl.cluster = None
  sent = []
  for i, a in enumerate(accels):
    CC = car.CarControl.new_message()
    CC.enabled = True
    CC.latActive = True
    CC.longActive = True
    CC.actuators.accel = a
    if fcw_from is not None and i >= fcw_from:
      CC.hudControl.visualAlert = car.CarControl.HUDControl.VisualAlert.fcw
    CS = SimpleNamespace(
      out=SimpleNamespace(steeringAngleDeg=0.0, vEgo=v, gasPressed=gas, standstill=standstill),
      steer_warning="EAC_ERROR_IDLE", hands_on_level=0, eac_fault=False, eac_status="EAC_ACTIVE",
      acc_state=4, das_control_counters=deque([i % 8]), msg_stw_actn_req={}, cluster_stock={},
    )
    out, can = ctl.update(CC.as_reader(), CS, i * 10_000_000, None)
    das = [dict(ast.literal_eval(m[2].decode())) for m in can if m[0] == "DAS_control"]
    sent.append((out.accel, das[0]["DAS_accelMin"], das[0]["DAS_accelMax"], das[0]["DAS_jerkMin"], das[0]["DAS_jerkMax"]))
  return sent


def test_controller_slews_gas_step_into_das_control(cc_mod):  # noqa: F811
  sent = _das(cc_mod, [0.0, 0.5, 0.5])
  assert sent[1][2] == pytest.approx(UP)
  assert sent[1][0] == pytest.approx(UP)


def test_controller_hard_brake_reaches_das_control_same_step(cc_mod):  # noqa: F811
  sent = _das(cc_mod, [0.2, -2.0])
  assert sent[1][1] == pytest.approx(-2.0, abs=0.04)
  assert sent[1][2] == 0
  assert sent[1][3:] == (-AP1_FULL_JERK_LIMIT, AP1_FULL_JERK_LIMIT)


def test_controller_moderate_brake_ramps_in(cc_mod):  # noqa: F811
  sent = _das(cc_mod, [0.0] + [-0.9] * 50)
  assert sent[1][0] == pytest.approx(-ON)
  assert sent[1][1] == pytest.approx(-ON, abs=0.04)
  assert sent[-1][0] == pytest.approx(-0.9)


def test_controller_fcw_brake_reaches_das_control_same_step(cc_mod):  # noqa: F811
  sent = _das(cc_mod, [0.3, -1.2], fcw_from=1)
  assert sent[1][0] == pytest.approx(-1.2)
  assert sent[1][1] == pytest.approx(-1.2, abs=0.04)
  assert sent[1][3:] == (-AP1_FULL_JERK_LIMIT, AP1_FULL_JERK_LIMIT)


def test_controller_gas_neutral_frame_unchanged(cc_mod):  # noqa: F811
  sent = _das(cc_mod, [0.7, 0.7], gas=True)
  assert all(s[1] == 0 and s[2] == 0 for s in sent)


def test_controller_sends_comfort_jerk_then_full_for_braking(cc_mod):  # noqa: F811
  sent = _das(cc_mod, [0.1] * 100 + [-1.0] * 80)
  assert sent[0][3:] == (-(AP1_FULL_JERK_LIMIT - AP1_JERK_LIMIT_NARROW_RATE * 0.01), AP1_FULL_JERK_LIMIT - AP1_JERK_LIMIT_NARROW_RATE * 0.01)
  assert sent[99][3:] == (-AP1_COMFORT_JERK_LIMIT, AP1_COMFORT_JERK_LIMIT)
  # The field follows the ramped request: comfort while it is above the blend
  # start, full once it is at or below -0.5, and never narrower than before.
  for prev, cur in zip(sent[100:], sent[101:]):
    assert cur[4] >= prev[4] - 1e-9
    if cur[0] >= AP1_JERK_BLEND_START:
      assert cur[3:] == (-AP1_COMFORT_JERK_LIMIT, AP1_COMFORT_JERK_LIMIT)
    if cur[0] <= AP1_BRAKE_BYPASS_ACCEL:
      assert cur[3:] == pytest.approx((-AP1_FULL_JERK_LIMIT, AP1_FULL_JERK_LIMIT))
  assert sent[-1][0] == pytest.approx(-1.0)


def test_controller_low_speed_launch_jerk(cc_mod):  # noqa: F811
  # Below 1 m/s: stock-like jerkMax, full jerkMin (braking is never limited).
  sent = _das(cc_mod, [0.3] * 50, v=0.5)
  assert all(s[3:] == pytest.approx((CarControllerParams.JERK_LIMIT_MIN, AP1_LAUNCH_JERK_MAX), abs=0.06) for s in sent)
  sent = _das(cc_mod, [0.3] * 5, v=0.5, fcw_from=0)
  assert all(s[3:] == (CarControllerParams.JERK_LIMIT_MIN, CarControllerParams.JERK_LIMIT_MAX) for s in sent)


def test_teslacan_default_jerk_unchanged_for_other_callers():
  import inspect
  from openpilot.selfdrive.car.tesla.teslacan import TeslaCAN
  sig = inspect.signature(TeslaCAN.create_longitudinal_commands)
  assert sig.parameters["jerk_min"].default == CarControllerParams.JERK_LIMIT_MIN
  assert sig.parameters["jerk_max"].default == CarControllerParams.JERK_LIMIT_MAX


# --- Standstill hold and launch (350f77fd -1.0 hold floor reverted: StopAccel -2.0 passes) ---

def test_standstill_hold_passes_stop_accel():
  s = Ap1AccelSmoother()
  assert s.update(-2.0, True, 0.0, urgent=True) == pytest.approx(-2.0)
  s = Ap1AccelSmoother()
  s.update(-1.6, True, 0.05, urgent=True)  # stopping ramp just before standstill
  out = [s.update(-1.6 - 0.008 * i, True, 0.0, urgent=True) for i in range(150)]
  assert out == pytest.approx([max(-1.6 - 0.008 * i, -2.8) for i in range(150)])  # request as planned, no floor
  s = Ap1AccelSmoother()
  s.update(-0.5, True, 0.0, urgent=True)
  assert s.update(-2.0, True, 0.0, urgent=True) == pytest.approx(-2.0)
  assert s.update(-0.4, True, 0.0, urgent=True) == pytest.approx(-0.4)


def test_standstill_launch_passes_same_step():
  s = Ap1AccelSmoother()
  for _ in range(200):
    s.update(-2.0, True, 0.0, urgent=True)
  assert s.update(0.3, True, 0.0) == pytest.approx(0.3)


def test_standstill_fcw_or_moving_keeps_full_brake():
  s = Ap1AccelSmoother()
  assert s.update(-2.0, True, 0.0, urgent=True, fcw=True) == pytest.approx(-2.0)
  s = Ap1AccelSmoother()
  assert s.update(-2.0, True, 0.5, urgent=True) == pytest.approx(-2.0)
  s = Ap1AccelSmoother()
  s.update(0.0, True, V)
  assert s.update(-2.0, True, V) == pytest.approx(-2.0)


def test_standstill_inactive_or_gas_neutral_unchanged():
  s = Ap1AccelSmoother()
  assert s.update(-2.0, False, 0.0) == pytest.approx(-2.0)
  assert s.update(-2.0, True, 0.0, gas_neutral=True) == 0.0


def test_launch_jerk_limits_low_speed_only():
  j = Ap1JerkLimit()
  assert j.update(-2.0, True, 0.0, urgent=True) == (-AP1_FULL_JERK_LIMIT, AP1_LAUNCH_JERK_MAX)
  assert j.update(0.3, True, 0.5) == (-AP1_FULL_JERK_LIMIT, AP1_LAUNCH_JERK_MAX)
  assert j.update(-2.0, True, 0.0, urgent=True, fcw=True) == (-AP1_FULL_JERK_LIMIT, AP1_FULL_JERK_LIMIT)
  assert j.update(0.3, True, 0.5, gas_neutral=True) == (-AP1_FULL_JERK_LIMIT, AP1_FULL_JERK_LIMIT)
  # Leaving low speed narrows from full at the normal rate (no step in jerkMax).
  lo, hi = j.update(0.3, True, 1.5)
  assert hi == pytest.approx(AP1_FULL_JERK_LIMIT - AP1_JERK_LIMIT_NARROW_RATE * 0.01)
  assert 0 <= round(AP1_LAUNCH_JERK_MAX / 0.059) < 256


def test_ap1_fcw_from_carcontrol():
  from cereal import car
  CC = car.CarControl.new_message()
  assert not ap1_fcw(CC.as_reader())
  CC.actuators.longControlState = car.CarControl.Actuators.LongControlState.stopping
  assert not ap1_fcw(CC.as_reader())
  CC.hudControl.visualAlert = car.CarControl.HUDControl.VisualAlert.fcw
  assert ap1_fcw(CC.as_reader())


def test_controller_standstill_hold_sends_stop_accel(cc_mod):  # noqa: F811
  sent = _das(cc_mod, [-2.0] * 120, v=0.0, standstill=True)
  assert all(s[1] == pytest.approx(-2.0, abs=0.04) for s in sent)
  sent = _das(cc_mod, [-2.0] * 5, v=0.0, standstill=True, fcw_from=0)
  assert all(s[1] == pytest.approx(-2.0, abs=0.04) for s in sent)


def test_stop_approach_output_continuous():
  # Drive 30 18:38:59-18:39:03: stopping ramp while vEgo crosses AP1_SLEW_MIN_SPEED (launch jerkMax switch) and
  # standstill. The sent accel follows the request down to StopAccel -2.0 and holds it; jerkMax switching is the only
  # thing that changes at 1 m/s.
  s, jl = Ap1AccelSmoother(), Ap1JerkLimit()
  v, prev, req_prev = 1.3, None, None
  outs, jerks = [], []
  for i in range(500):
    req = max(-0.6 - 0.008 * i, -2.0)  # ~0.8 m/s^3 stopping ramp to StopAccel
    v = max(v - 0.01, 0.0)
    out = s.update(req, True, v, urgent=v < 0.5)
    jerks.append(jl.update(out, True, v))
    if prev is not None:
      assert abs(out - prev) <= abs(req - req_prev) + 1e-9, (i, v, prev, out)
    prev, req_prev = out, req
    outs.append(out)
  assert min(outs) == pytest.approx(-2.0)
  assert outs[-1] == pytest.approx(-2.0)
  assert all(j[0] == -AP1_FULL_JERK_LIMIT for j in jerks)  # braking jerk never limited
