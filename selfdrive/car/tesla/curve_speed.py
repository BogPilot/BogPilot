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
   before that node and requests the strongest one. The request is capped so
   the car lands on the lowest curve speed ahead (allowing for actuator lag and
   the release ramp) and goes to 0 once it is there, so it does not keep
   braking below the target. Braking starts past the deadband (debounced for
   weak requests) and stops only when the request falls back under
   DEADBAND_OFF (hysteresis, no on/off chatter). The request is clipped to the
   AP1 regen range and jerk limited on onset and release. Nodes where the model
   itself plans to be slower than MIN_MODEL_SPEED are skipped (yaw rate / speed
   near a stop is a false sharp curve).

Safety: the planner uses min(plan, request), so this can only ADD braking.
Panda safety is not touched.
"""
from __future__ import annotations

import numpy as np

TESLA_CSC_MAX_LAT_ACCEL = 2.0  # m/s^2, cap on the learned CSC cornering limit
TESLA_CURVE_LEAD_S = 0.5       # s, reach the curve speed this long before the node

DEADBAND = 0.15                # m/s^2, a request must be this strong to start braking
DEADBAND_OFF = 0.08            # m/s^2, once braking, keep going until it is weaker than this
ON_DEBOUNCE_S = 0.3            # s, a weak request must stay past DEADBAND this long to start
STRONG_REQUEST = 0.3           # m/s^2, a request this strong starts at once
MIN_ON_S = 0.5                 # s, once started, the deadband cannot switch it off sooner
A_MIN = -1.2                   # m/s^2, stays in the AP1 regen range (no friction thunk)
ONSET_JERK = 1.5               # m/s^3
RELEASE_JERK = 1.2             # m/s^3
TARGET_MARGIN = 0.0            # m/s, land this far above the curve speed (lag + ramp are already allowed for)
ACTUATOR_LAG_S = 0.3           # s, delay + lag between the request and the car's decel
MIN_MODEL_SPEED = 5.0          # m/s, skip nodes the model plans to drive slower than this
MIN_CURVATURE = 1e-4           # 1/m, straighter than this never limits speed
MIN_DISTANCE = 5.0             # m, floor on the braking distance (a node inside the lead distance
                               # must not ask for a huge decel)


def tesla_csc_lateral_accel(learned: float, cap: float = TESLA_CSC_MAX_LAT_ACCEL) -> float:
  """Learned CSC cornering limit with the Tesla cap applied (lower value wins)."""
  return min(float(learned), float(cap))


def _path_curve_speeds(t_idxs, x_path, v_path, yaw_rate_path, max_lat_accel):
  """(t, x, v_curve, valid) for the model path nodes; v_curve = sqrt(limit / curvature)."""
  t = np.asarray(t_idxs, dtype=float)
  x = np.asarray(x_path, dtype=float)
  v = np.asarray(v_path, dtype=float)
  yaw = np.asarray(yaw_rate_path, dtype=float)
  n = min(len(t), len(x), len(v), len(yaw))
  t, x, v, yaw = t[:n], x[:n], v[:n], yaw[:n]
  valid = v >= MIN_MODEL_SPEED
  curvature = np.abs(yaw) / np.clip(v, MIN_MODEL_SPEED, None)
  valid &= (curvature > MIN_CURVATURE) & (t > 0.0)
  v_curve = np.sqrt(max_lat_accel / np.where(valid, curvature, 1.0))
  return t, x, v_curve, valid


def curve_target_speed(t_idxs, x_path, v_path, yaw_rate_path, max_lat_accel=TESLA_CSC_MAX_LAT_ACCEL) -> float:
  """CSC's curve speed (m/s) for this model path: the node with the most lateral accel and the
  same curvature formula as calculate_road_curvature(). inf when nothing limits speed."""
  v = np.asarray(v_path, dtype=float)
  yaw = np.asarray(yaw_rate_path, dtype=float)
  n = min(len(v), len(yaw))
  if n == 0 or max_lat_accel <= 0.0:
    return float('inf')
  v, yaw = v[:n], yaw[:n]
  i = int(np.argmax(np.abs(yaw * v)))
  curvature = abs(yaw[i] * v[i]) / max(v[i], 1.0) ** 2
  if curvature <= MIN_CURVATURE:
    return float('inf')
  return float(np.sqrt(max_lat_accel / curvature))


def curve_brake_request(v_ego, t_idxs, x_path, v_path, yaw_rate_path,
                        max_lat_accel=TESLA_CSC_MAX_LAT_ACCEL, lead_s=TESLA_CURVE_LEAD_S) -> float:
  """Steady decel (<= 0, m/s^2) needed to reach the curve speed of every path node
  lead_s before the car gets there. Returns 0.0 when no node needs braking."""
  v_ego = float(v_ego)
  if v_ego <= 0.0 or max_lat_accel <= 0.0:
    return 0.0
  _, x, v_curve, valid = _path_curve_speeds(t_idxs, x_path, v_path, yaw_rate_path, max_lat_accel)
  if not np.any(valid):
    return 0.0
  # Distance left to slow down: up to the node, minus the lead time at current speed.
  dist = x - v_ego * lead_s
  valid &= v_curve < v_ego
  if not np.any(valid):
    return 0.0
  dist = np.clip(dist, MIN_DISTANCE, None)
  decel = (v_ego ** 2 - v_curve ** 2) / (2.0 * dist)
  return -float(np.max(np.where(valid, decel, 0.0)))


def landing_decel(speed_gap: float) -> float:
  """Strongest decel (>= 0, m/s^2) that still lands the car TARGET_MARGIN above the curve speed
  when it is speed_gap above it: the speed shed during the actuator lag plus the release ramp
  (a * ACTUATOR_LAG_S + a^2 / (2 * RELEASE_JERK)) must fit inside the gap."""
  g = float(speed_gap) - TARGET_MARGIN
  if not np.isfinite(g):
    return float('inf')
  if g <= 0.0:
    return 0.0
  return RELEASE_JERK * (np.sqrt(ACTUATOR_LAG_S ** 2 + 2.0 * g / RELEASE_JERK) - ACTUATOR_LAG_S)


class TeslaCurveBrake:
  """Curve brake request (<= 0, m/s^2): capped so the car lands on the curve speed instead of
  braking below it, deadband with hysteresis, clipped to the regen range and jerk limited."""

  def __init__(self, dt: float = 0.05):
    self.dt = dt
    self.reset()

  def reset(self):
    self.a_request = 0.0
    self.a_raw = 0.0
    self.braking = False     # past the deadband (with hysteresis)
    self.on_timer = 0.0      # time a weak request has been past DEADBAND while not braking
    self.on_time = 0.0       # time since braking started
    self.v_target = float('inf')
    self.landing_cap = float('inf')

  @property
  def at_target(self) -> bool:
    return self.landing_cap == 0.0

  def update(self, csc_active, v_ego, t_idxs, x_path, v_path, yaw_rate_path,
             max_lat_accel=TESLA_CSC_MAX_LAT_ACCEL) -> float:
    if csc_active:
      raw = curve_brake_request(v_ego, t_idxs, x_path, v_path, yaw_rate_path, max_lat_accel)
      self.v_target = curve_target_speed(t_idxs, x_path, v_path, yaw_rate_path, max_lat_accel)
    else:
      raw = 0.0
      self.v_target = float('inf')

    # Release at the curve speed: never ask for more than lands the car on it (0 once there).
    self.landing_cap = landing_decel(float(v_ego) - self.v_target)
    a = max(raw, A_MIN, -self.landing_cap)

    # Deadband with hysteresis: start past DEADBAND (held ON_DEBOUNCE_S, or at once past
    # STRONG_REQUEST), keep going until weaker than DEADBAND_OFF, but at least MIN_ON_S.
    if self.braking:
      self.on_time += self.dt
      if a > -DEADBAND_OFF and (self.on_time >= MIN_ON_S - 1e-6 or not csc_active):
        self.braking = False
    else:
      self.on_timer = self.on_timer + self.dt if a <= -DEADBAND else 0.0
      if self.on_timer >= ON_DEBOUNCE_S - 1e-6 or a <= -STRONG_REQUEST:
        self.braking, self.on_time, self.on_timer = True, 0.0, 0.0

    a = min(a, 0.0) if self.braking else 0.0
    self.a_raw = a
    if a < self.a_request:
      self.a_request = max(a, self.a_request - ONSET_JERK * self.dt)
    else:
      self.a_request = min(a, self.a_request + RELEASE_JERK * self.dt)
    return self.a_request


def tesla_curve_brake_for(car_name, dt: float = 0.05):
  """TeslaCurveBrake for Tesla, None for every other car."""
  return TeslaCurveBrake(dt) if str(car_name) == "tesla" else None
