"""BogPilot: stop the Curve Speed Controller (CSC) from reacting to straight roads.

Stock FrogPilot hands control to CSC whenever the model's predicted path implies
more than 1.0 m/s^2 of lateral accel at the current speed (planner
road_curvature_detected). The predicted path drifts past that on straight roads,
so CSC switches on for a frame or two at a time. Each new switch-on seeds the
CSC target at vEgo, so the car stops accelerating for a moment even though the
curve speed it computed (often 70+ mph) is far above the set speed.

This gate sits between the planner flag and CSC:

* ON  only when the planner still flags a curve AND the CSC curve speed is at
  least ON_MARGIN below the target CSC would cap (the set / SLC target without
  CSC), continuously for HOLD_S.
* OFF when the planner flag drops or the curve speed climbs back within
  OFF_MARGIN of that target, continuously for RELEASE_S (hysteresis, no
  chatter). Blinker, min speed, disengage or the CSC toggle off still release
  at once, as stock.
* SEED: the first CSC target is min(target, curve speed) instead of vEgo.

The planner's road_curvature_detected is NOT changed, so Conditional
Experimental's CECurves (which filters that flag itself) behaves exactly as
before. Learning/calibration, the Tesla 2.0 m/s^2 cap and TeslaCurveBrake are
unchanged; TeslaCurveBrake just sees CSC active less often on straight roads.

File-backed toggle (same pattern as RegenComfortBrake):
  echo 0 > /data/params_bogpilot/CSCStraightFix   # off (stock FrogPilot CSC)
  echo 1 > /data/params_bogpilot/CSCStraightFix   # on
Missing file = ON. Read when openpilot starts. Panda safety is not touched.
"""
from __future__ import annotations

from pathlib import Path

ON_MARGIN = 0.5    # m/s, curve speed must be this far below the target to count as a curve
OFF_MARGIN = 0.25  # m/s, release once the curve speed is back within this of the target
HOLD_S = 0.5       # s, the curve condition must hold this long before CSC takes over
RELEASE_S = 0.3    # s, the release condition must hold this long before CSC lets go
MIN_CURVATURE = 1e-6  # 1/m, floor so a straight path never divides by zero

CSC_STRAIGHT_FIX_PATH = Path("/data/params_bogpilot/CSCStraightFix")
# Tests may override the path via this module attribute.
_csc_straight_fix_path = CSC_STRAIGHT_FIX_PATH


def csc_straight_fix_enabled(stored: str | bytes | None = None) -> bool:
  """Missing/empty file = on. 0/false/off = off. Anything else = on."""
  if stored is None:
    try:
      text = _csc_straight_fix_path.read_text(encoding="utf-8", errors="replace").strip() if _csc_straight_fix_path.is_file() else ""
    except OSError:
      text = ""
  else:
    text = (stored.decode() if isinstance(stored, bytes) else stored).strip()
  return text.lower() not in ("0", "false", "off")


def csc_curve_speed(lateral_acceleration: float, road_curvature: float) -> float:
  """CSC curve speed (m/s): sqrt(limit / |curvature|), same formula as CurveSpeedController."""
  return (max(float(lateral_acceleration), 0.0) / max(abs(float(road_curvature)), MIN_CURVATURE)) ** 0.5


def csc_seed(target: float, curve_speed: float) -> float:
  """First CSC target when it takes over: the lower of the target and the curve speed (never vEgo)."""
  return min(float(target), float(curve_speed))


class CSCStraightGate:
  """Time-based persistence + hysteresis on the CSC curve flag."""

  def __init__(self, on_margin=ON_MARGIN, off_margin=OFF_MARGIN, hold_s=HOLD_S, release_s=RELEASE_S):
    self.on_margin = on_margin
    self.off_margin = off_margin
    self.hold_s = hold_s
    self.release_s = release_s
    self.reset()

  def reset(self):
    self.active = False
    self.on_timer = 0.0
    self.off_timer = 0.0

  def update(self, allowed: bool, planner_curve: bool, curve_speed: float, target: float, dt: float) -> bool:
    """allowed: hard gates (engaged, toggle, above min speed, no blinker). planner_curve: the
    planner's road_curvature_detected. curve_speed / target in m/s. Returns whether CSC controls."""
    if not allowed:
      self.reset()
      return False

    if not self.active:
      self.off_timer = 0.0
      curve = planner_curve and curve_speed < target - self.on_margin
      self.on_timer = self.on_timer + dt if curve else 0.0
      if self.on_timer >= self.hold_s - 1e-6:
        self.active = True
    else:
      self.on_timer = 0.0
      release = (not planner_curve) or curve_speed >= target - self.off_margin
      self.off_timer = self.off_timer + dt if release else 0.0
      if self.off_timer >= self.release_s - 1e-6:
        self.active = False
        self.off_timer = 0.0
    return self.active
