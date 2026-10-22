"""AP1 regen capacity learner (cold pack / high SoC) and regen-to-friction blend.

Problem. With a cold pack (or a full pack) the BMS accepts less regen, so the
DI makes up the rest of a DAS_control brake request with the friction brakes.
The friction pickup then lands at a lower decel than on a warm drive, and the
request crosses it at the normal regen ramp jerk, which feels like a step.

Measured on this car (internal replay data, not committed): after a 7.6 h soak
at 50-52 F, lift-off regen was 45-53 % of warm from 1.9 to 3.8 min and back to
100 % by about 17 min. Across the logged drives: below ~54 F regen starts near 45 % and
recovers over 10-15 min; at or above ~59 F it is normal. The driver reports
that from the 20s F full recovery can take 20-30+ min. The pack temperature,
SoC and the BMS regen limit are not on the buses the comma taps, so the limit
is learned from what the car actually delivers.

1. Online sampling (primary). On every manual one-pedal lift-off (accelerator
   at 0, no brake pedal, openpilot longitudinal not active, SAMPLE_V_MIN..
   SAMPLE_V_MAX) the DI applies all the regen it is allowed, so the measured
   decel (-aEgo) against the warm lift-off decel at that speed (warm_decel) is
   the available fraction. Sampling starts SAT_SETTLE_S after the lift; each
   lift-off (or each EVENT_MAX_S of a long one) gives one value, the p90 of
   decel / warm decel, i.e. its plateau. Asymmetric update: a value below the
   estimate closes ALPHA_DROP of the gap at once (only from lift-offs sampled
   for >= EVENT_MIN_S, long enough to reach the plateau), a value above closes
   ALPHA_RISE (any length). No temperature signal is used, so it also covers
   reduced regen at a high state of charge. A value below EVENT_FLOOR (or the
   cold prior, if lower) is not used: on a downhill the decel is low for a
   reason other than regen.
   Not used: 0x108 rear-DI torque (on this dual-motor car it stayed at
   76-102 % of warm while the car's decel was 45-53 %) and openpilot braking
   (the DI tops up with friction to the request, so achieved decel says
   nothing about the regen share).
2. Prior (start of drive and fallback). Before the first sample the estimate
   comes from the outside air temperature (0x208) at drive start and the time
   since start, ramping linearly from start_frac to 1.0 over recover_min
   (PRIOR_* tables). Between samples the estimate drifts up at the prior's
   recovery slope (at least DRIFT_MIN_PER_MIN), never above 1.0.
3. Blend. When the estimate is below BLEND_OFF_FRAC, a falling DAS_control
   request that has passed BLEND_START_FRAC of the estimated regen decel is
   deepened at no more than BLEND_JERK until it is BLEND_BAND past the
   estimated regen limit, so the DI's friction top-up builds in gradually.
   Past the band it catches up at BLEND_CATCHUP_JERK. The ramped request never
   trails the smoothed request by more than BLEND_MAX_LAG.
   Requests at or below long_smooth.AP1_BRAKE_HARD_ACCEL, FCW and the
   LongControl stopping state pass through on the same step. Warm (estimate
   >= BLEND_OFF_FRAC) the request is untouched.

4. Planner. The regen-aware stop shaper's regen power estimate is capped at
   P_NOMINAL_KW x estimate (longitudinal_planner.py), so a known stop starts its
   gentle regen-only onset earlier when regen is reduced.

Panda safety, ACCEL_MIN/MAX and the MPC are unchanged.

File toggle (AP1 only, default on; the learner always runs and is logged):
  echo 0 > /data/params_bogpilot/ColdRegenBlend   # off
Tuning: the PRIOR_* tables and the constants below. Bands colder than ~45 F
are the driver's report, not measured on this car yet.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

# Warm full one-pedal lift-off decel (m/s^2, -aEgo p90 per event) on this car: manual lift-offs
# at >= 59 F and >= 10 min into the drive (n = 11, 11, 4, 3 events per speed band).
WARM_DECEL_BP = [8.0, 12.0, 16.0, 21.5]
WARM_DECEL_V = [1.89, 1.93, 1.37, 1.00]

# --- prior: (ambient F) -> start fraction, minutes to full ---
# 45-56 F measured (soaked drives), >= 59 F measured normal, < 45 F driver-reported.
PRIOR_AMB_F = [15.0, 25.0, 35.0, 45.0, 52.0, 56.0, 59.0]
PRIOR_START_FRAC = [0.20, 0.25, 0.32, 0.40, 0.45, 0.70, 1.0]
PRIOR_RECOVER_MIN = [40.0, 32.0, 22.0, 16.0, 15.0, 8.0, 0.0]
DRIFT_MIN_PER_MIN = 0.01       # 1 %/min when the prior has no slope (warm, high SoC)
FRAC_MIN = 0.15

# --- online sampling ---
SAMPLE_V_MIN = 6.0             # m/s, above the regen fade band
SAMPLE_V_MAX = 25.0            # top of the measured warm envelope
SAT_SETTLE_S = 1.0             # wait after the lift before sampling
EVENT_MIN_S = 2.0              # sampled time a lift-off needs before it may lower the estimate
EVENT_MAX_S = 6.0              # long lift-offs update every this many seconds
ALPHA_DROP = 0.8               # share of the gap closed by one lift-off below the estimate
ALPHA_RISE = 0.5               # ... above the estimate
RATIO_MAX = 1.15
EVENT_FLOOR = 0.35             # lower lift-off values are taken as grade (downhill), unless the prior is lower
AMBIENT_WAIT_S = 5.0           # wait this long for 0x208 before using no prior

# --- blend ---
BLEND_OFF_FRAC = 0.95
BLEND_START_FRAC = 0.8
BLEND_JERK = 1.0               # m/s^3 through the regen -> friction handoff band
BLEND_BAND = 0.3               # m/s^2 past the estimated regen limit
BLEND_MAX_LAG = 0.25           # m/s^2, never this much shallower than the request
BLEND_CATCHUP_JERK = 3.0       # m/s^3 back to the request after the band
BLEND_MIN_SPEED = 1.0
HARD_ACCEL = -2.0              # same as long_smooth.AP1_BRAKE_HARD_ACCEL

# --- persistence (resume after a short stop) ---
STATE_PATH = Path("/data/params_bogpilot/ColdRegenState")
STATE_SAVE_S = 60.0
RESUME_FULL_MIN = 15.0         # gap shorter than this: resume the saved estimate
RESUME_NONE_MIN = 120.0        # gap longer than this: prior only

_TOGGLE_PATH = Path("/data/params_bogpilot/ColdRegenBlend")


def cold_regen_enabled(fingerprint, stored: str | None = None) -> bool:
  from openpilot.selfdrive.car.tesla.values import CAR
  if fingerprint != CAR.TESLA_AP1_MODELS:
    return False
  if stored is None:
    try:
      stored = _TOGGLE_PATH.read_text().strip() if _TOGGLE_PATH.is_file() else ""
    except OSError:
      stored = ""
  return stored.strip().lower() not in ("0", "false", "off")


def c_to_f(c: float) -> float:
  return c * 9.0 / 5.0 + 32.0


def warm_decel(v: float) -> float:
  """Warm full one-pedal regen decel (m/s^2, positive) at speed v, flat outside the table."""
  return float(np.interp(v, WARM_DECEL_BP, WARM_DECEL_V))


# Low-speed regen fade (same as regen_aware.FADE_*).
FADE_BP = [0.9, 2.2, 4.7]
FADE_V = [0.0, 0.38, 1.0]


def regen_decel_cap(v: float, frac: float) -> float:
  """Estimated one-pedal regen decel (m/s^2, positive) available at speed v."""
  return float(np.interp(v, FADE_BP, FADE_V)) * frac * warm_decel(v)


def prior_start(ambient_c: float | None) -> tuple[float, float]:
  """(start fraction, minutes to full) for the outside air at drive start."""
  if ambient_c is None or ambient_c != ambient_c or ambient_c <= -39.0:  # 0x208 raw 0 = -40 C: not valid
    return 1.0, 0.0
  f = c_to_f(ambient_c)
  return float(np.interp(f, PRIOR_AMB_F, PRIOR_START_FRAC)), float(np.interp(f, PRIOR_AMB_F, PRIOR_RECOVER_MIN))


def prior_fraction(ambient_c: float | None, minutes: float) -> float:
  f0, rec = prior_start(ambient_c)
  if rec <= 0.0 or f0 >= 1.0:
    return 1.0
  return float(min(1.0, f0 + (1.0 - f0) * max(minutes, 0.0) / rec))


def prior_slope_per_min(ambient_c: float | None) -> float:
  f0, rec = prior_start(ambient_c)
  slope = (1.0 - f0) / rec if rec > 0.0 else 0.0
  return max(slope, DRIFT_MIN_PER_MIN)


class RegenCapacityLearner:
  """Estimate of available one-pedal regen as a fraction of warm full regen (0..1)."""

  def __init__(self, state_path: Path | None = STATE_PATH, now_wall=None):
    self.state_path = state_path
    self.frac = 1.0
    self.initialized = False
    self.t = 0.0                  # s since start
    self.ambient0 = None
    self.slope = DRIFT_MIN_PER_MIN
    self.lift_t = 0.0             # s since a manual lift-off began
    self.ev_t = 0.0               # s sampled in the current lift-off window
    self.ev_ratios: list[float] = []
    self.events = 0               # lift-off windows sampled this drive
    self.samples = 0              # windows that changed the estimate
    self.first_sample_t = None
    self.sampling = False
    self.last_ratio = float("nan")
    self.last_save_t = 0.0
    self._resume = self._load(now_wall)

  # -- persistence --
  def _load(self, now_wall):
    if self.state_path is None:
      return None
    try:
      txt = self.state_path.read_text().split()
      frac, wall = float(txt[0]), float(txt[1])
    except (OSError, ValueError, IndexError):
      return None
    now = time.time() if now_wall is None else now_wall
    gap_min = (now - wall) / 60.0
    if now < 1.6e9 or wall < 1.6e9 or not (0.0 <= gap_min < 7 * 24 * 60) or not (0.0 < frac <= 1.0):
      return None
    return frac, gap_min

  def save(self, now_wall=None):
    if self.state_path is None or not self.initialized:
      return
    now = time.time() if now_wall is None else now_wall
    if now < 1.6e9:
      return
    try:
      self.state_path.parent.mkdir(parents=True, exist_ok=True)
      self.state_path.write_text(f"{self.frac:.3f} {now:.0f}\n")
    except OSError:
      pass

  def _init(self, ambient_c):
    self.ambient0 = ambient_c
    self.frac = prior_fraction(ambient_c, 0.0)
    self.slope = prior_slope_per_min(ambient_c)
    if self._resume is not None:
      saved, gap = self._resume
      w = float(np.clip((RESUME_NONE_MIN - gap) / (RESUME_NONE_MIN - RESUME_FULL_MIN), 0.0, 1.0))
      # Resume never assumes more regen than the saved value aged at the prior's slope.
      aged = min(1.0, saved + self.slope * gap)
      self.frac = w * aged + (1.0 - w) * self.frac
    self.initialized = True

  def _apply_event(self):
    """One lift-off window: its plateau (p90 of decel / warm decel) updates the estimate."""
    if len(self.ev_ratios) < 10:
      return
    r = float(np.percentile(self.ev_ratios, 90))
    self.last_ratio = r
    if r >= self.frac:
      # Any lift-off shows at least this much regen (short ones too): rise smoothly.
      self.frac += ALPHA_RISE * (min(r, 1.0) - self.frac)
    elif self.ev_t >= EVENT_MIN_S and r >= min(EVENT_FLOOR, prior_start(self.ambient0)[0]):
      # A drop needs a lift-off long enough to reach the plateau: react fast.
      self.frac += ALPHA_DROP * (r - self.frac)
    else:
      return
    self.samples += 1
    if self.first_sample_t is None:
      self.first_sample_t = self.t

  # -- main step --
  def update(self, v, a_ego, gas_pressed, brake_pressed, long_active, ambient_c, dt, now_wall=None):
    self.t += dt
    if not self.initialized:
      if ambient_c is None or ambient_c != ambient_c:
        if self.t < AMBIENT_WAIT_S:
          return self.frac
        ambient_c = None
      self._init(ambient_c)

    lift = (SAMPLE_V_MIN <= v <= SAMPLE_V_MAX and not gas_pressed and not brake_pressed and not long_active and
            a_ego is not None and a_ego == a_ego)
    self.lift_t = self.lift_t + dt if lift else 0.0
    if self.lift_t >= SAT_SETTLE_S:
      if not self.sampling:
        self.sampling = True
        self.events += 1
        self.ev_t = 0.0
        self.ev_ratios = []
      self.ev_t += dt
      self.ev_ratios.append(float(np.clip(-a_ego / warm_decel(v), 0.0, RATIO_MAX)))
      if self.ev_t >= EVENT_MAX_S:
        self._apply_event()            # long lift-off: update now and start a new window
        self.ev_t = 0.0
        self.ev_ratios = []
    else:
      if self.sampling:
        self._apply_event()
        self.sampling = False
      # Recovery drift between samples.
      self.frac = min(1.0, self.frac + self.slope * dt / 60.0)

    self.frac = float(np.clip(self.frac, FRAC_MIN, 1.0))
    if self.t - self.last_save_t >= STATE_SAVE_S:
      self.last_save_t = self.t
      self.save(now_wall)
    return self.frac


class ColdRegenBlend:
  """Jerk-limited deepening through the regen -> friction handoff band."""

  def __init__(self):
    self.last = None
    self.active = False           # output trails the request this step

  def reset(self):
    self.last = None
    self.active = False

  def update(self, target, active, v_ego, frac, urgent=False, dt=0.01):
    target = float(target)
    if not active or v_ego < BLEND_MIN_SPEED or urgent or target <= HARD_ACCEL or self.last is None:
      self.last = target if active else None
      self.active = False
      return target
    out = target
    if target < self.last:
      cap = regen_decel_cap(v_ego, frac)
      knee = -BLEND_START_FRAC * cap
      floor = -(cap + BLEND_BAND)
      if frac < BLEND_OFF_FRAC and target < knee and self.last > floor:
        # Inside the handoff band: deepen at BLEND_JERK from where the band starts.
        out = max(target, min(self.last, knee) - BLEND_JERK * dt)
      elif self.active:
        # Past the band (or warm again) while still trailing: catch up, no step.
        out = max(target, self.last - BLEND_CATCHUP_JERK * dt)
      out = min(out, target + BLEND_MAX_LAG)
    self.active = out != target
    self.last = out
    return out
