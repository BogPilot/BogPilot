"""AP1 comfort-band accel slew for DAS_control (CarController side only).

On a recorded drive the planner aTarget is the only source of request jitter.
CarController passes it straight through (LongControl PID gains are 0) and
DAS_control tells the DI it may follow at +/-8 m/s^3 (stock AP1 DAS sends
roughly +/-0.2 to +/-1.2). Planner jerk on that drive: p95 0.7, p99 1.9
m/s^3; the larger steps cluster around radar lead jumps and show up as
one-plan-step reversals (brake blip then back to gas, gas blip then back).

Rising requests (more accel, or a brake release) are slewed at
AP1_RISE_JERK. Falling requests are slewed as described under "Regen onset
ramp" below; a request at or below AP1_BRAKE_HARD_ACCEL, an FCW, or the
LongControl stopping state passes through on the same step. The output
always lies between the last output and the planner value. ACCEL_MIN/MAX,
the panda limits, STOP_DISTANCE and the planner are untouched.

Below AP1_SLEW_MIN_SPEED the request passes through (and seeds the slew):
the standstill hold sits at -2.0 and the launch jumps to about +0.3, which
a 2.5 m/s^3 slew would delay by ~0.9 s.

Inactive (no DAS_control) resets. The gas-neutral frame seeds 0.0, which is
what the DI last saw, so the request after a driver press ramps from 0.

Ap1JerkLimit picks the DAS_jerkMin / DAS_jerkMax magnitude sent with each
0x2b9 frame. Stock AP1 DAS sent about +/-0.2 to +/-1.2 m/s^3 on 02a (wider
with a wider accel window); openpilot always sent +/-8. In the comfort band
this sends +/-AP1_COMFORT_JERK_LIMIT. As the request sent falls from
AP1_JERK_BLEND_START to AP1_BRAKE_BYPASS_ACCEL the magnitude blends linearly
up to the full 8, and at or below AP1_BRAKE_BYPASS_ACCEL (or on an urgent
frame) it is the full 8, so real braking is never held back by the DI.
The field is always at least the regen ramp jerk being sent once the request
is past AP1_BRAKE_BYPASS_ACCEL. Widening is immediate; narrowing back to the comfort
value is rate limited (AP1_JERK_LIMIT_NARROW_RATE) so the limit itself never
steps down. Below AP1_SLEW_MIN_SPEED, while inactive, and on the gas-neutral
frame it is the full 8 (the old behavior).

Regen onset ramp (road test of 6914de87). The planner turns
a sudden drop in its plan into an overshooting step: aTarget is
2 * (v(0.55 s) - v0) / 0.55 - a0, so when the plan's current accel a0 is
+1.2 and the plan falls to about -0.2 (allowThrottle going false, or a lead
first seen at 86 m), aTarget lands near -1.1 (clipped to -0.88) in one 50 ms
step while the plan itself only reaches -0.4 to -0.6 over the next second.
6914de87 passed every falling request at or below -0.5 through on the same
step, so DAS_control went from +1.36 to -0.92 in one frame and the DI swung
motor torque from +106 to -73 Nm in about 0.3 s.

Falling requests now follow the stock DI's shape after a cruise cancel:
drive torque is released (gently since smooth3: see "Drive release" below)
while the last output is above 0, and regen ramps in (AP1_BRAKE_ONSET_JERK once at or below 0). The
regen ramp jerk grows with the depth of the request, from
AP1_BRAKE_ONSET_JERK at AP1_BRAKE_URGENT_ACCEL to the full 8 m/s^3 at
AP1_BRAKE_HARD_ACCEL, and a request at or below AP1_BRAKE_HARD_ACCEL, an FCW,
or the LongControl stopping state passes through on the same step (urgent).
Time to reach a falling request from 0: -0.9 in 0.45 s, -1.0 in 0.50 s,
-1.5 in 0.30 s, -1.95 in 0.26 s, -2.0 or below at once; from +1.5 add about
0.3 s for the drive release (worst case -1.0 from +1.5 in 0.80 s). ACCEL_MIN, the planner and panda safety are unchanged.

The jerk fields follow the ramped request (not the raw planner value), so
they widen as the request deepens rather than snapping to 8 at the planner
step; urgent frames send the full 8 immediately.

Standstill hold and launch (three recorded drives). At standstill the
request passes through unchanged (StopAccel -2.0 while stopped behind a lead),
as before 350f77fd; the -1.0 standstill hold floor that 350f77fd added for the
brake pedal "thunk" was reverted at the driver's request (he does not think
the hold pressure caused it). Ap1JerkLimit still sends jerkMax
AP1_LAUNCH_JERK_MAX (stock-like) below AP1_SLEW_MIN_SPEED; jerkMin stays at
the full 8 so braking is never limited.

Standstill engage (a recorded drive, brake pedal thunk right
after engagement). Disengaged at standstill the standby DAS_control
(actuator_plan, AP1StandstillEngage) asks for accelMin -1.4 and the DI holds
the car. On engage LongControl restarts its stopping ramp from 0 (-0.01,
-0.10 ... reaching -2.0 after 2.6 s), the DI drops its hold (hold state 3 -> 0)
and the brakes let off, then re-apply as the ramp deepens. So on the first
active frame at standstill in the LongControl stopping state the request is
held at AP1_STANDSTILL_ENGAGE_ACCEL until the ramp passes it, LongControl
leaves stopping or the request rises (launch), the car moves, or an FCW.
Engaging at a green light (not stopping) is unchanged.
"""

import math

from cereal import car
from openpilot.common.realtime import DT_CTRL

_FCW = car.CarControl.HUDControl.VisualAlert.fcw
_STOPPING = car.CarControl.Actuators.LongControlState.stopping

# m/s^3. Above the planner's p99 on 02a, so normal plans pass unchanged.
AP1_RISE_JERK = 2.5
# Drive release. A falling request while the last output is above 0 (drive
# torque; 0 is the hold at the set speed) is released like a foot lifting off
# the pedal. e3574753 released at 5 m/s^3 (+1.3 -> 0 in ~0.26 s), which on the
# e3574753 drives felt like regen grabbing.
#  - Pure lift (request at or above 0): the release jerk builds up to
#    AP1_DRIVE_RELEASE_JERK over AP1_DRIVE_RELEASE_RAMP_S and tapers off again
#    as the output reaches the request (S-curve): +1.3 -> 0 in about 0.8 s.
#    2.0 equals AP1_BRAKE_ONSET_JERK, so a lift that continues into regen has
#    no jerk step at 0.
#  - Lift into braking (request below 0): the release jerk blends from
#    AP1_DRIVE_RELEASE_JERK at 0 to AP1_DRIVE_RELEASE_BRAKE_JERK (the e3574753
#    rate) at AP1_DRIVE_RELEASE_BRAKE_ACCEL, with the same soft start, so a
#    lead that needs braking reaches its request at most ~0.13 s later than e3574753
#    (~0.13 s; ~0.2 s for a shallow -0.3 request). Over the last
#    AP1_DRIVE_RELEASE_JOIN_ACCEL above 0 it blends back to the onset jerk, and
#    once the output is at or below 0 the regen onset ramp takes over.
#  - Below AP1_BRAKE_URGENT_ACCEL: no soft start, at least the e3574753 rate.
#    At or below AP1_BRAKE_HARD_ACCEL, FCW and stopping pass through as before.
AP1_DRIVE_RELEASE_JERK = 2.0
AP1_DRIVE_RELEASE_BRAKE_JERK = 5.0
AP1_DRIVE_RELEASE_BRAKE_ACCEL = -0.5
AP1_DRIVE_RELEASE_RAMP_S = 0.2
# m/s^2. Lift into braking: over the last this-much of drive above 0 the
# release jerk blends back down to AP1_BRAKE_ONSET_JERK, so it meets the regen
# onset ramp at 0 without a jerk step.
AP1_DRIVE_RELEASE_JOIN_ACCEL = 0.3
AP1_FALL_JERK = AP1_DRIVE_RELEASE_JERK
# m/s^2. Jerk fields are the full 8 at or below this request (blend end).
AP1_BRAKE_BYPASS_ACCEL = -0.5
# m/s. Standstill hold, launch and creep pass through unchanged.
AP1_SLEW_MIN_SPEED = 1.0

# DAS_jerkMin/Max magnitudes (m/s^3). FULL matches CarControllerParams.JERK_LIMIT_MAX.
AP1_FULL_JERK_LIMIT = 8.0
AP1_COMFORT_JERK_LIMIT = 1.5
# m/s^2. Blend from comfort (at or above this) to full (at or below the bypass).
AP1_JERK_BLEND_START = -0.3
# (m/s^3) per s. Full -> comfort takes (8 - 1.5) / 10 = 0.65 s.
AP1_JERK_LIMIT_NARROW_RATE = 10.0

# Regen onset ramp (m/s^3). Deepening a request at or below 0 m/s^2. ISO 15622
# ACC comfort guidance is 2.5 m/s^3 above 20 m/s; the stock DI's own lift-off
# regen ramp after a cruise cancel on a recorded drive averaged about 0.6 m/s^3
# (peaks 1.0-2.6). 2.0 sits between the two.
AP1_BRAKE_ONSET_JERK = 2.0
# m/s^2. The regen ramp jerk blends from AP1_BRAKE_ONSET_JERK at this request
# to AP1_FULL_JERK_LIMIT at AP1_BRAKE_HARD_ACCEL.
AP1_BRAKE_URGENT_ACCEL = -1.0
# m/s^2. A falling request at or below this passes through on the same step.
AP1_BRAKE_HARD_ACCEL = -2.0

# Gas handoff. While the driver presses the accelerator, DAS_control carries the
# neutral frame (0). Before this, the first frame after the release restarted
# the slew from 0, so a driver at +1.2 m/s^2 saw the request drop to 0 and climb
# back at AP1_RISE_JERK (~0.5 s), felt as a brief lift. Now the measured
# CarState.aEgo during the press is latched and the first active frame after
# the release starts from it: a higher plan keeps rising from there, a lower
# plan is reached through the normal drive-release S-curve. Seed is clipped to
# [0, AP1_GAS_HANDOFF_MAX] (never a brake seed; never above the panda max of
# +2.0), and only used at or above AP1_SLEW_MIN_SPEED.
AP1_GAS_HANDOFF_MAX = 2.0
# m/s^3. DAS_jerkMax below AP1_SLEW_MIN_SPEED (stock DAS sends about 1.2 at
# standstill). DAS_jerkMin stays at AP1_FULL_JERK_LIMIT there.
AP1_LAUNCH_JERK_MAX = 1.5
# m/s^2. Standstill engage hold: the standby DAS_control accelMin
# (actuator_plan.AP1_STANDBY_ACCEL_MIN), so the request stays continuous.
AP1_STANDSTILL_ENGAGE_ACCEL = -1.4
# m/s. Below this the car counts as stopped for the engage hold.
AP1_STANDSTILL_ENGAGE_V = 0.1
# Frames after engage in which the LongControl stopping state arms the hold.
AP1_STANDSTILL_ENGAGE_FRAMES = 10


def ap1_brake_urgent(CC):
  """FCW on the HUD, or LongControl in its stopping state (it ramps itself)."""
  return bool(CC.hudControl.visualAlert == _FCW or CC.actuators.longControlState == _STOPPING)


def ap1_fcw(CC):
  """FCW on the HUD (the low-speed launch jerkMax is skipped)."""
  return bool(CC.hudControl.visualAlert == _FCW)


def ap1_brake_ramp_jerk(target):
  """Regen ramp jerk (m/s^3) for a falling request at or below 0."""
  target = float(target)
  if target >= AP1_BRAKE_URGENT_ACCEL:
    return AP1_BRAKE_ONSET_JERK
  if target <= AP1_BRAKE_HARD_ACCEL:
    return AP1_FULL_JERK_LIMIT
  frac = (AP1_BRAKE_URGENT_ACCEL - target) / (AP1_BRAKE_URGENT_ACCEL - AP1_BRAKE_HARD_ACCEL)
  return AP1_BRAKE_ONSET_JERK + frac * (AP1_FULL_JERK_LIMIT - AP1_BRAKE_ONSET_JERK)


class Ap1AccelSmoother:
  def __init__(self, rise_jerk=AP1_RISE_JERK, fall_jerk=AP1_DRIVE_RELEASE_JERK,
               hard_brake=AP1_BRAKE_HARD_ACCEL, min_speed=AP1_SLEW_MIN_SPEED,
               brake_release_jerk=AP1_DRIVE_RELEASE_BRAKE_JERK, release_ramp_s=AP1_DRIVE_RELEASE_RAMP_S):
    self.rise_jerk = rise_jerk
    self.fall_jerk = fall_jerk
    self.brake_release_jerk = brake_release_jerk
    self.release_ramp_s = release_ramp_s
    self.hard_brake = hard_brake
    self.min_speed = min_speed
    self.last = None
    self.release_j = 0.0  # current drive-release jerk (m/s^3), 0 when not releasing
    self.gas_seed = None  # latched aEgo from the last gas-neutral frame
    self.was_active = False
    self.engage_hold = None   # standstill engage hold level, or None
    self.engage_used = False
    self.active_frames = 0
    self.prev_target = None

  def reset(self):
    self.last = None
    self.release_j = 0.0
    self.gas_seed = None
    self.was_active = False
    self.engage_hold = None
    self.engage_used = False
    self.active_frames = 0
    self.prev_target = None

  def update(self, target, active, v_ego, gas_neutral=False, urgent=False, dt=DT_CTRL, fcw=False,
             a_ego=None):
    """urgent: FCW or LongControl stopping. A falling request passes through.

    Below AP1_SLEW_MIN_SPEED (including standstill) the request passes through
    unchanged, so the standstill hold sends StopAccel as planned.
    """
    target = float(target)
    out = self._update(target, active, v_ego, gas_neutral, urgent, dt, fcw, a_ego)
    now_active = active and not gas_neutral
    self.active_frames = self.active_frames + 1 if (now_active and self.was_active) else (1 if now_active else 0)
    # The first engaged carControl still carries the previous longControlState
    # ("off"), so frame 1 arms the hold on standstill alone (it equals the
    # standby accelMin the DI already sees) and the stopping state is checked
    # from frame 2 on.
    first = self.active_frames == 1
    stopping = urgent and not fcw  # LongControl stopping state
    arm = 0 < self.active_frames <= AP1_STANDSTILL_ENGAGE_FRAMES and (stopping or (first and not fcw))
    if arm and self.engage_hold is None and v_ego < AP1_STANDSTILL_ENGAGE_V and target <= 0.0 and not self.engage_used:
      self.engage_used = True
      self.engage_hold = AP1_STANDSTILL_ENGAGE_ACCEL
    elif self.engage_hold is not None and (not active or gas_neutral or fcw or (not stopping and not first) or
                                           v_ego >= AP1_STANDSTILL_ENGAGE_V or
                                           target <= self.engage_hold or
                                           (self.prev_target is not None and target > self.prev_target + 1e-3)):
      self.engage_hold = None
    if self.engage_hold is not None:
      out = min(out, self.engage_hold)
      self.last = out
    if not now_active:
      self.engage_used = False
    self.was_active = now_active
    self.prev_target = target if active else None
    return out

  def _update(self, target, active, v_ego, gas_neutral, urgent, dt, fcw, a_ego):
    if gas_neutral:
      self.last = 0.0
      self.release_j = 0.0
      if a_ego is not None and v_ego >= self.min_speed:
        self.gas_seed = min(max(float(a_ego), 0.0), AP1_GAS_HANDOFF_MAX)
      else:
        self.gas_seed = None
      return 0.0
    if not active:
      self.last = None
      self.gas_seed = None
      return target
    if self.gas_seed is not None:
      # First active frame after a driver gas press: continue from the car's
      # measured accel instead of the neutral 0. FCW / urgent / hard braking
      # still pass through below because target <= hard_brake bypasses.
      if v_ego >= self.min_speed and not fcw:
        self.last = self.gas_seed
      self.gas_seed = None
    if self.last is None or v_ego < self.min_speed:
      self.last = target
      return target
    if target >= self.last:
      self.release_j = 0.0
      out = min(target, self.last + self.rise_jerk * dt)
    elif urgent or target <= self.hard_brake:
      self.release_j = 0.0
      out = target
    else:
      jerk = ap1_brake_ramp_jerk(target)
      if self.last > 0.0:
        jerk = self._release_jerk(target, jerk, dt)
      else:
        self.release_j = 0.0
      out = max(target, self.last - jerk * dt)
    self.last = out
    return out

  def _release_jerk(self, target, brake_jerk, dt):
    """Drive release jerk (m/s^3) for a falling request while the last output is above 0."""
    frac = min(max(target / AP1_DRIVE_RELEASE_BRAKE_ACCEL, 0.0), 1.0)
    cap = self.fall_jerk + frac * max(self.brake_release_jerk - self.fall_jerk, 0.0)
    if target < AP1_BRAKE_URGENT_ACCEL or self.release_ramp_s <= 0.0:
      # Braking that needs more than the onset ramp: no soft start.
      j = max(cap, brake_jerk)
    else:
      if target < 0.0:
        cap = max(cap, brake_jerk)
        join = min(self.last / AP1_DRIVE_RELEASE_JOIN_ACCEL, 1.0)
        cap = brake_jerk + join * (cap - brake_jerk)
      snap = cap / self.release_ramp_s
      j = min(cap, self.release_j + snap * dt)
      if target >= 0.0:
        # Pure lift: taper off as the output reaches the request (S-curve end).
        j = min(j, math.sqrt(2.0 * snap * (self.last - target)))
      j = max(j, snap * dt)
    self.release_j = j
    return j


def ap1_jerk_limit_target(accel):
  """Jerk-field magnitude for a request, before the narrowing rate limit."""
  accel = float(accel)
  if accel <= AP1_BRAKE_BYPASS_ACCEL:
    return AP1_FULL_JERK_LIMIT
  if accel >= AP1_JERK_BLEND_START:
    return AP1_COMFORT_JERK_LIMIT
  frac = (AP1_JERK_BLEND_START - accel) / (AP1_JERK_BLEND_START - AP1_BRAKE_BYPASS_ACCEL)
  return AP1_COMFORT_JERK_LIMIT + frac * (AP1_FULL_JERK_LIMIT - AP1_COMFORT_JERK_LIMIT)


class Ap1JerkLimit:
  def __init__(self):
    self.last = AP1_FULL_JERK_LIMIT

  def update(self, accel, active, v_ego, gas_neutral=False, urgent=False, dt=DT_CTRL, fcw=False):
    """Returns (jerk_min, jerk_max) for DAS_control. accel is the request as sent."""
    if active and not gas_neutral and not fcw and v_ego < AP1_SLEW_MIN_SPEED:
      # Standstill hold, launch and creep: stock-like jerkMax, full jerkMin.
      self.last = AP1_FULL_JERK_LIMIT
      return -AP1_FULL_JERK_LIMIT, AP1_LAUNCH_JERK_MAX
    if gas_neutral or urgent or not active or v_ego < AP1_SLEW_MIN_SPEED:
      target = AP1_FULL_JERK_LIMIT
    else:
      target = ap1_jerk_limit_target(accel)
    if target >= self.last:
      self.last = target
    else:
      self.last = max(target, self.last - AP1_JERK_LIMIT_NARROW_RATE * dt)
    return -self.last, self.last
