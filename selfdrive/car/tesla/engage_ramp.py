"""AP1 engage / lat-resume steering blend.

Before this, the engage soft-start (actuator_plan.AP1_ENGAGE_SOFT_START_FRAMES,
also re-armed by the hands-pause resume in hso.Ap1DriverYield) held the
measured wheel for 300 ms and then sent the planner angle on the next 0x488.
The only bound on that step was apply_std_steer_angle_limits, whose DOWN table
(toward centre) allows 5-7 deg per 50 Hz frame at 5-10 m/s. Routes 55-58
(new_1013d / new_1013e) show 150-580 deg/s command steps right at the end of
the hold.

Ap1EngageSteerRamp starts at the measured wheel when the planner path takes
over (end of soft-start, or lat active again after a lat-inactive gap) and
blends to the live planner angle with a smoothstep over a duration scaled by
the angle error (RAMP_MIN_S..RAMP_MAX_S). The output still goes through the
existing rate limit and the +/-20 deg clip around the measured wheel, so it
can only be slower than before and stays inside the unchanged panda limits.
Driver override wins at once: hands pause / yield takes the NONE branch
regardless of the request, and the ramp is cancelled; the resume then
re-arms soft-start and this ramp.

Pure Python; no CAN, params, or panda change. AP1 only (carcontroller).
"""

from openpilot.common.numpy_fast import clip, interp

RAMP_MIN_S = 0.3
RAMP_MAX_S = 1.0
# Error (deg) -> ramp duration. <= 2 deg: 0.3 s, >= 30 deg: 1.0 s.
RAMP_ERR_BP = (2.0, 30.0)
# Below this error there is nothing to blend.
RAMP_MIN_ERR_DEG = 1.0


def ramp_duration(err_deg: float) -> float:
  return float(clip(interp(abs(err_deg), RAMP_ERR_BP, (RAMP_MIN_S, RAMP_MAX_S)), RAMP_MIN_S, RAMP_MAX_S))


def smoothstep(x: float) -> float:
  x = clip(x, 0.0, 1.0)
  return x * x * (3.0 - 2.0 * x)


class Ap1EngageSteerRamp:
  def __init__(self, dt: float = 0.01):
    self.dt = dt
    self.active = False
    self.start_deg = 0.0
    self.duration = RAMP_MIN_S
    self.t = 0.0
    self.prev_path = False

  def cancel(self):
    self.active = False

  def update(self, path_active: bool, measured_deg: float, desired_deg: float) -> float:
    """Return the angle to request this control step.

    path_active: the planner angle would be sent this step (lat active, no
    soft-start hold, no hands pause / yield). Rising edge starts the ramp.
    """
    if not path_active:
      self.active = False
      self.prev_path = False
      return desired_deg
    if not self.prev_path:
      err = desired_deg - measured_deg
      self.active = abs(err) > RAMP_MIN_ERR_DEG
      self.start_deg = float(measured_deg)
      self.duration = ramp_duration(err)
      self.t = 0.0
    self.prev_path = True
    if not self.active:
      return desired_deg
    self.t += self.dt
    w = smoothstep(self.t / self.duration)
    if self.t >= self.duration:
      self.active = False
    return self.start_deg + (desired_deg - self.start_deg) * w
