"""Tesla curve speed: reach the CSC target by the curve, not in it.

Drive review on both the default model and Firehose: FrogPilot's curve speed
controller (CSC) lowers its target gradually and the planner only brakes
gently toward it, so the car arrived at the tightest part of a curve still
well above the CSC speed (about 0.29 g at 29 mph against a 25 mph target).

Two Tesla-only changes, both gated on the car brand:

1. TESLA_CSC_MAX_LAT_ACCEL caps the learned CSC cornering limit. A lower
   learned value still wins; learning and calibration are unchanged.
2. TeslaCurveBrake, a look-ahead curve brake that runs only while CSC is
   limiting speed. For every node on the model's predicted path it computes
   the steady decel needed to reach sqrt(limit / curvature) TESLA_CURVE_LEAD_S
   before that node and requests the strongest one. Requests weaker than the
   deadband are ignored, the request is clipped to the AP1 regen range and it
   is jerk limited on onset and release. Nodes where the model itself plans to
   be slower than MIN_MODEL_SPEED are skipped (yaw rate / speed near a stop is
   a false sharp curve).

Safety: the planner uses min(plan, request), so this can only ADD braking.
Panda safety is not touched.
"""
from __future__ import annotations

import numpy as np

TESLA_CSC_MAX_LAT_ACCEL = 2.0  # m/s^2, cap on the learned CSC cornering limit
TESLA_CURVE_LEAD_S = 0.5       # s, reach the curve speed this long before the node

DEADBAND = 0.15                # m/s^2, ignore weaker requests
A_MIN = -1.2                   # m/s^2, stays in the AP1 regen range (no friction thunk)
ONSET_JERK = 1.5               # m/s^3
RELEASE_JERK = 2.0             # m/s^3
MIN_MODEL_SPEED = 5.0          # m/s, skip nodes the model plans to drive slower than this
MIN_CURVATURE = 1e-4           # 1/m, straighter than this never limits speed
MIN_DISTANCE = 1.0             # m, avoid dividing by ~0 for the nearest nodes


def tesla_csc_lateral_accel(learned: float, cap: float = TESLA_CSC_MAX_LAT_ACCEL) -> float:
  """Learned CSC cornering limit with the Tesla cap applied (lower value wins)."""
  return min(float(learned), float(cap))


def curve_brake_request(v_ego, t_idxs, x_path, v_path, yaw_rate_path,
                        max_lat_accel=TESLA_CSC_MAX_LAT_ACCEL, lead_s=TESLA_CURVE_LEAD_S) -> float:
  """Steady decel (<= 0, m/s^2) needed to reach the curve speed of every path node
  lead_s before the car gets there. Returns 0.0 when no node needs braking."""
  v_ego = float(v_ego)
  if v_ego <= 0.0 or max_lat_accel <= 0.0:
    return 0.0
  t = np.asarray(t_idxs, dtype=float)
  x = np.asarray(x_path, dtype=float)
  v = np.asarray(v_path, dtype=float)
  yaw = np.asarray(yaw_rate_path, dtype=float)
  n = min(len(t), len(x), len(v), len(yaw))
  if n == 0:
    return 0.0
  t, x, v, yaw = t[:n], x[:n], v[:n], yaw[:n]

  valid = v >= MIN_MODEL_SPEED
  curvature = np.abs(yaw) / np.clip(v, MIN_MODEL_SPEED, None)
  valid &= curvature > MIN_CURVATURE
  if not np.any(valid):
    return 0.0

  v_curve = np.sqrt(max_lat_accel / np.where(valid, curvature, 1.0))
  # Distance left to slow down: up to the node, minus the lead time at current speed.
  dist = x - v_ego * lead_s
  valid &= (v_curve < v_ego) & (t > 0.0)
  if not np.any(valid):
    return 0.0
  dist = np.clip(dist, MIN_DISTANCE, None)
  decel = (v_ego ** 2 - v_curve ** 2) / (2.0 * dist)
  return -float(np.max(np.where(valid, decel, 0.0)))


class TeslaCurveBrake:
  """Deadbanded, clipped and jerk-limited curve brake request (<= 0, m/s^2)."""

  def __init__(self, dt: float = 0.05):
    self.dt = dt
    self.a_request = 0.0
    self.a_raw = 0.0

  def reset(self):
    self.a_request = 0.0
    self.a_raw = 0.0

  def update(self, csc_active, v_ego, t_idxs, x_path, v_path, yaw_rate_path,
             max_lat_accel=TESLA_CSC_MAX_LAT_ACCEL) -> float:
    if csc_active:
      a = curve_brake_request(v_ego, t_idxs, x_path, v_path, yaw_rate_path, max_lat_accel)
      a = 0.0 if a > -DEADBAND else max(a, A_MIN)
    else:
      a = 0.0
    self.a_raw = a
    if a < self.a_request:
      self.a_request = max(a, self.a_request - ONSET_JERK * self.dt)
    else:
      self.a_request = min(a, self.a_request + RELEASE_JERK * self.dt)
    return self.a_request


def tesla_curve_brake_for(car_name, dt: float = 0.05):
  """TeslaCurveBrake for Tesla, None for every other car."""
  return TeslaCurveBrake(dt) if str(car_name) == "tesla" else None
