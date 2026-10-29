"""Gates for longitudinal-planner FCW (crash_cnt).

FrogPilot publishes frogpilotPlan.min/maxAcceleration = 0 while disengaged
(frogpilot/controls/frogpilot_planner.py). That forces the MPC to a coasting
plan, so crash_cnt latches on any closing lead. controlsd then raises
EventName.fcw ("BRAKE!" / "Risk of Collision") the instant enabled goes true.

While longitudinal control is off (reset_state), clear crash_cnt and suppress
planner FCW so engage cannot inherit a disengaged coasting false positive.
Model-based FCW (hardBrakePredicted) and stock FCW/AEB are untouched. Engaged
planner FCW (including cases where accel limits are temporarily zero) is
unchanged.
"""

from __future__ import annotations


def planner_fcw(crash_cnt: float, standstill: bool, reset_state: bool) -> tuple[bool, float]:
  """Return (fcw, crash_cnt_after_gate)."""
  if reset_state:
    return False, 0.0
  fcw = crash_cnt > 2 and not standstill
  return bool(fcw), float(crash_cnt)
