"""Standstill engage hold (a recorded drive: brake pedal thunk after engage)."""
from openpilot.selfdrive.car.tesla.long_smooth import (Ap1AccelSmoother, AP1_STANDSTILL_ENGAGE_ACCEL,
                                                       AP1_STANDSTILL_ENGAGE_V)


def run(frames, v=0.0):
  """frames: (target, active, urgent[, fcw]) per 100 Hz step."""
  s = Ap1AccelSmoother()
  out = []
  for f in frames:
    target, active, urgent = f[:3]
    fcw = f[3] if len(f) > 3 else False
    out.append(s.update(target, active, v, urgent=urgent, fcw=fcw))
  return out


def stop_ramp(n, start=-0.008):
  return [start - 0.0082 * i for i in range(n)]


def test_route_a5_standstill_engage_holds_standby_level():
  # Disengaged, then engage: first frame state "off" (-0.008), then stopping ramp to -2.0.
  ramp = stop_ramp(250)
  frames = [(0.0, False, False)] * 5 + [(ramp[0], True, False)] + [(max(a, -2.0), True, True) for a in ramp[1:]]
  out = run(frames)
  assert out[5] == AP1_STANDSTILL_ENGAGE_ACCEL
  i_pass = next(i for i, a in enumerate(ramp) if a <= AP1_STANDSTILL_ENGAGE_ACCEL) + 5
  assert all(o == AP1_STANDSTILL_ENGAGE_ACCEL for o in out[5:i_pass])
  # After the ramp passes the hold level the request is the ramp again.
  assert all(abs(o - max(a, -2.0)) < 1e-9 for o, a in zip(out[i_pass:], ramp[i_pass - 5:]))
  assert min(out) >= -2.0


def test_green_light_engage_at_standstill_unchanged():
  # A recorded drive: standstill engage straight into a launch (+0.51).
  frames = [(0.0, False, False)] * 3 + [(0.51, True, False)] + [(0.51 + 0.01 * i, True, False) for i in range(20)]
  out = run(frames)
  assert out[3:] == [f[0] for f in frames[3:]]


def test_engage_first_frame_negative_then_pid_released_next_frame():
  # Frame 1 still shows "off" (hold armed, equals the standby accelMin); frame 2 is pid, not stopping.
  frames = [(0.0, False, False), (-0.01, True, False), (0.05, True, False), (0.10, True, False)]
  out = run(frames)
  assert out[1] == AP1_STANDSTILL_ENGAGE_ACCEL
  assert out[2:] == [0.05, 0.10]


def test_launch_while_holding_releases():
  ramp = stop_ramp(40)
  frames = [(0.0, False, False), (ramp[0], True, False)] + [(a, True, True) for a in ramp[1:]]
  frames += [(ramp[-1] + 0.05, True, False), (0.2, True, False)]
  out = run(frames)
  assert out[-3] == AP1_STANDSTILL_ENGAGE_ACCEL
  assert out[-2:] == [frames[-2][0], 0.2]


def test_moving_engage_unchanged():
  frames = [(0.0, False, False), (-0.01, True, True), (-0.02, True, True)]
  out = run(frames, v=AP1_STANDSTILL_ENGAGE_V + 0.5)
  assert AP1_STANDSTILL_ENGAGE_ACCEL not in out


def test_fcw_not_held():
  frames = [(0.0, False, False), (-0.5, True, True, True), (-3.5, True, True, True)]
  out = run(frames)
  assert out[1:] == [-0.5, -3.5]


def test_only_once_per_engagement():
  ramp = stop_ramp(250)
  eng = [(ramp[0], True, False)] + [(max(a, -2.0), True, True) for a in ramp[1:]]
  frames = [(0.0, False, False)] + eng + [(-2.0, True, True)] * 20
  out = run(frames)
  assert out[-1] == -2.0 and out.count(AP1_STANDSTILL_ENGAGE_ACCEL) < 200
