"""AP1 regen-aware early stop braking (prototype, local only).

Goal: when a stop is known early (stopped/slow lead from radarState, or the
model's predicted stop in experimental mode), begin a gentle, regen-only
deceleration earlier instead of coasting and then braking late and hard,
which pushes the last part of the stop onto the friction brakes.

Safety rule: the output is always min(plan, early), so this module can only
ADD earlier gentle braking. It never caps, delays or reduces braking that the
planner asks for (FCW / cut-in / hard stops keep the full ACCEL_MIN path).

The real BMS max-regen limit (CAN3 0x232) is not on the comma's buses, so the
available regen power is estimated:
  * nominal 30 kW (logged regen shaft power peaked at -24..-36 kW, never limited)
  * 18 kW ceiling when the outside air (0x208 GTW_ambientTemperature) is <= 5 C
  * lowered to the measured power when DI_torqueMotor (0x108) regen plateaus
    below the request for > 0.5 s above the low-speed fade band
  * recovers toward the ceiling at 0.5 kW/s
Regen decel capacity = fade(v) * P*0.9/(m*v), m = 2100 kg. The fade comes from
a recorded drive: regen torque fell from -113 Nm at 10.5 mph to -43 Nm at
5 mph and 0 at 2 mph.

References: Tinkla/Unity PCC_module.py hard-codes REGEN_DECEL_V; this replaces
the constant with a power-based estimate. File toggle (default on for AP1):
  echo 0 > /data/params_bogpilot/RegenAwareStop   # off
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

MASS_KG = 2100.0
ETA = 0.9
GEAR_RATIO = 9.73
WHEEL_RADIUS_M = 0.35

P_NOMINAL_KW = 30.0
P_COLD_KW = 18.0
P_MIN_KW = 10.0
COLD_AMBIENT_C = 5.0
P_RECOVER_KW_S = 0.5

# Low-speed regen fade (m/s -> fraction of capacity).
FADE_BP = [0.9, 2.2, 4.7]
FADE_V = [0.0, 0.38, 1.0]
A_CAP_MAX = 1.6           # never assume more regen decel than seen (~1.5 m/s^2)

# Gentle target band (m/s^2, positive = decel).
A_GENTLE_MIN = 0.6
A_GENTLE_MAX = 1.0
A_GENTLE_FLOOR = 0.3      # at highway speed 30 kW is only ~0.5 m/s^2
GENTLE_FRAC = 0.9         # stay a bit below estimated capacity
ONSET_FRAC = 0.85         # start when the required constant decel reaches this x gentle
RELEASE_FRAC = 0.4        # hysteresis
MIN_SPEED = 1.0           # m/s, below this the planner's stopping logic owns it
ONSET_JERK = 0.6          # m/s^3, ramp the added braking in smoothly
RELEASE_JERK = 1.5
STOP_LOST_HOLD_S = 0.5

LIMIT_MIN_SPEED = 4.7     # only judge limiting above the fade band
LIMIT_MARGIN = 0.2        # m/s^2
LIMIT_TIME_S = 0.5

LEAD_STOPPED_SPEED = 1.5  # m/s, lead counts as a stop target below this
MODEL_STOP_SPEED = 0.5

_TOGGLE_PATH = Path("/data/params_bogpilot/RegenAwareStop")


def regen_aware_enabled(fingerprint, stored: str | None = None) -> bool:
  from openpilot.selfdrive.car.tesla.values import CAR
  if fingerprint != CAR.TESLA_AP1_MODELS:
    return False
  if stored is None:
    try:
      stored = _TOGGLE_PATH.read_text().strip() if _TOGGLE_PATH.is_file() else ""
    except OSError:
      stored = ""
  return stored.strip().lower() not in ("0", "false", "off")


def fade(v: float) -> float:
  return float(np.interp(v, FADE_BP, FADE_V))


def regen_capacity(v: float, p_kw: float) -> float:
  """Estimated regen-only decel (m/s^2, positive) at speed v."""
  power = p_kw * 1000.0 * ETA / (MASS_KG * max(v, 0.5))
  return fade(v) * min(A_CAP_MAX, power)


def regen_decel_from_torque(motor_torque_nm: float) -> float:
  """Positive decel produced by negative motor torque."""
  return max(0.0, -motor_torque_nm) * GEAR_RATIO / WHEEL_RADIUS_M / MASS_KG


def regen_power_kw(motor_torque_nm: float, motor_rpm: float) -> float:
  """Positive regen shaft power (kW)."""
  p = motor_torque_nm * motor_rpm * 2.0 * math.pi / 60.0 / 1000.0
  return max(0.0, -p)


class RegenPowerEstimator:
  def __init__(self, p_kw: float = P_NOMINAL_KW):
    self.p_kw = p_kw
    self.limit_t = 0.0
    self.limited = False

  def ceiling(self, ambient_c) -> float:
    if ambient_c is not None and ambient_c <= COLD_AMBIENT_C:
      return P_COLD_KW
    return P_NOMINAL_KW

  def update(self, v, req_accel, motor_torque_nm, motor_rpm, ambient_c, dt):
    ceil = self.ceiling(ambient_c)
    req_decel = -req_accel
    a_meas = regen_decel_from_torque(motor_torque_nm)
    plateau = v > LIMIT_MIN_SPEED and req_decel > 0.3 and req_decel > a_meas + LIMIT_MARGIN and motor_torque_nm < 0
    self.limit_t = self.limit_t + dt if plateau else 0.0
    self.limited = self.limit_t > LIMIT_TIME_S
    if self.limited:
      measured = regen_power_kw(motor_torque_nm, motor_rpm) / ETA
      self.p_kw = max(P_MIN_KW, min(self.p_kw, measured))
    else:
      self.p_kw = min(ceil, self.p_kw + P_RECOVER_KW_S * dt)
    self.p_kw = min(self.p_kw, ceil)
    return self.p_kw


def lead_stop_distance(lead_status, d_rel, v_lead, stop_gap):
  """Distance (m) to where ego should stop behind a stopped/slow lead, else None."""
  if not lead_status or v_lead > LEAD_STOPPED_SPEED:
    return None
  return max(0.0, d_rel - stop_gap)


def model_stop_distance(positions_x, velocities_x):
  """First predicted position where model speed falls below MODEL_STOP_SPEED."""
  if positions_x is None or len(positions_x) < 2 or len(velocities_x) != len(positions_x):
    return None
  for x, vv in zip(positions_x, velocities_x):
    if vv < MODEL_STOP_SPEED:
      return max(0.0, float(x))
  return None


def gentle_target(v: float, p_kw: float) -> float:
  cap = regen_capacity(v, p_kw)
  g = min(A_GENTLE_MAX, GENTLE_FRAC * cap)
  if cap >= A_GENTLE_MIN / GENTLE_FRAC:
    g = max(g, A_GENTLE_MIN)
  return max(A_GENTLE_FLOOR, g)


A_TAIL = 0.5              # m/s^2, gentle tail inside the regen fade band


def profile_decel(v: float, p_kw: float) -> float:
  """Regen-shaped decel for speed v: follow the gentle regen-only target above the
  fade band, ease to A_TAIL inside it (where regen fades and friction would take over)."""
  if v >= FADE_BP[-1]:
    return gentle_target(v, p_kw)
  frac = (v - FADE_BP[0]) / (FADE_BP[-1] - FADE_BP[0])
  return float(np.interp(frac, [0.0, 1.0], [A_TAIL, gentle_target(FADE_BP[-1], p_kw)]))


def profile_distance(v: float, p_kw: float, n: int = 24) -> float:
  """Distance to stop from v following profile_decel (integral of u/a(u) du)."""
  if v <= 0.0:
    return 0.0
  us = np.linspace(0.0, v, n + 1)
  integrand = [u / max(profile_decel(u, p_kw), 0.05) for u in us]
  du = v / n
  return float(du * (sum(integrand) - 0.5 * (integrand[0] + integrand[-1])))


class RegenStopShaper:
  """Adds a regen-shaped stop profile that starts as soon as the stop is known
  and the profile needs the remaining distance. Output = min(plan, early)."""
  def __init__(self):
    self.active = False
    self.a_added = 0.0      # current extra-braking command (<= 0)
    self.lost_t = 0.0
    self.last_req = 0.0

  def reset(self):
    self.active = False
    self.a_added = 0.0
    self.lost_t = 0.0

  def update(self, v, a_plan, stop_dist, p_kw, dt):
    """Return (a_out, a_early). a_out = min(a_plan, a_early) always."""
    target = 0.0
    if v > MIN_SPEED and stop_dist is not None:
      self.lost_t = 0.0
      d = max(stop_dist, 0.5)
      d_prof = profile_distance(v, p_kw)
      self.last_req = d_prof / d
      if not self.active and d_prof >= ONSET_FRAC * d:
        self.active = True
      elif self.active and d_prof < RELEASE_FRAC * d:
        self.active = False
      if self.active:
        # Scale the profile so it lands on the stop point (ratio > 1 means behind schedule).
        target = -profile_decel(v, p_kw) * max(d_prof / d, 0.0)
    else:
      self.lost_t += dt
      if self.lost_t > STOP_LOST_HOLD_S or v <= MIN_SPEED:
        self.active = False
      elif self.active:
        target = self.a_added

    # Smooth onset / release of the ADDED braking only.
    if target < self.a_added:
      self.a_added = max(target, self.a_added - ONSET_JERK * dt)
    else:
      self.a_added = min(target, self.a_added + RELEASE_JERK * dt)

    a_early = self.a_added if self.a_added < 0.0 else 0.0
    if a_early >= 0.0:
      return a_plan, 0.0
    return min(a_plan, a_early), a_early
