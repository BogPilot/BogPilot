"""AP1 low-conflict speed-limit raise (no fake stalk, no panda 0x45 loosen).

When FrogPilot's Speed Limit Controller confirms a higher posted limit, lift
openpilot's cruise target to limit+offset so longitudinal accelerates into the
faster zone. DI_cruiseSet still seeds the pcmCruise set; stalk UP/DN injection
is intentionally not used.

Engage / tip policy (simplified):
  - Stalk UP/DN engage → sticky vEgo. The sticky set holds through posted-limit
    changes in BOTH directions (no auto-raise into a faster zone, no cap by a
    lower limit) until RWD pull or disengage — same as a tipped set.
  - Pull-toward (RWD / resumeCruise) engage → SLC+offset raise immediately
  - Classify engage from stalk pressed within the last ~0.8 s. Caller must
    feed continuous stalk *levels* (carstate.button_states via frogpilotCarState
    accelPressed / decelPressed / resumePressed), not 10 ms buttonEvents —
    frogpilot_vcruise samples at 20 Hz and misses short holds (drive26 RWD).
  - While engaged, stalk UP/DN only adjust the software set the same way both
    directions (+1 / next-5 up, −1 / next-lower-5 down). Tip base is the current
    software set (sticky latch / tip_ms / raised set). Sticky still wins first
    (never max(plan, SLC) while sticky — drive26/27). After RWD raise, vcruise
    floors set_hint with slc_desired (no tip seed — SLC tracking, drive 2a) so
    tips never base off DI_cruiseSet (~vEgo/2 under overlay — drive28).
    No DECEL raise holdoff. Engaged RWD/pull clears tip+sticky and re-allows raise.
  - Tipped / post-raise set is authority until stalk pull (re-latch SLC+offset)
    or disengage; SLC raise must not yank the set back up after a down tip.
    Never follow stale DI_cruiseSet under OP overlay. AP1 buttonEvents are binary
    (UP_1ST/UP_2ND both map to accelCruise), so full vs short tip comes from raw
    SpdCtrlLvr_Stat (frogpilotCarState.spdCtrlLvr): UP_2ND/DN_2ND (4/8) on the
    rising edge = full tip (next/prev 5); UP_1ST/DN_1ST (16/32) = ±1. A hold past
    TIP_HOLD_S upgrades a ±1 press to next/prev 5 once, as a fallback only.
  - Tips adjust the software set only — they must not send cancel / FWD stalk TX
    (stock DI_cruiseState can soft-lock to STANDBY after cancel + stalk spam).

IC set digit (DAS_accSpeedLimit on 0x389) tracks the OP set via the cluster
pack path. DBC factor is 0.4. cruise_set_mph rejects V_CRUISE_UNSET (255 kph)
so standstill DI set=0 cannot pack ~158 mph / cluster digital set ~90
(route 23 segments 5/8).

Not a product. No warranty. The driver remains responsible. Comply with local law.
"""

from openpilot.common.conversions import Conversions as CV

# Matches selfdrive/controls/lib/drive_helpers.V_CRUISE_UNSET (kph).
V_CRUISE_UNSET_KPH = 255.0
V_CRUISE_UNSET_MS = V_CRUISE_UNSET_KPH * CV.KPH_TO_MS
# OP cruise ceiling (drive_helpers.V_CRUISE_MAX); overlay above this is nonsense.
V_CRUISE_MAX_MPH = 145.0 * CV.KPH_TO_MPH


def is_ap1(fingerprint: str | None) -> bool:
  from openpilot.selfdrive.car.tesla.values import CAR
  return fingerprint == CAR.TESLA_AP1_MODELS


def lift_cruise_ms(v_cruise_ms: float, slc_desired_ms: float, slc_target_ms: float,
                   cruising_speed_ms: float) -> float:
  """Raise v_cruise toward a confirmed higher SLC target.

  Intended to run AFTER the min() merge so a pre-lift DI/csc seed cannot undo
  the raise. Caller re-caps with CSC only when CSC is actively controlling.
  No-op when the SLC target is missing or below cruising speed.
  """
  if slc_target_ms < cruising_speed_ms or slc_desired_ms < cruising_speed_ms:
    return v_cruise_ms
  if slc_desired_ms > v_cruise_ms:
    return slc_desired_ms
  return v_cruise_ms


def apply_slc_raise_after_min(v_cruise_ms: float, slc_desired_ms: float, slc_target_ms: float,
                              cruising_speed_ms: float, csc_controlling_speed: bool,
                              csc_target_ms: float) -> float:
  """AP1 post-min raise: lift, then re-min with CSC only if CSC is active.

  Used by frogpilot_vcruise after min(targets) so DI_cruiseSet sitting in
  csc_target (CSC inactive) cannot collapse a confirmed SLC raise.
  """
  v = lift_cruise_ms(v_cruise_ms, slc_desired_ms, slc_target_ms, cruising_speed_ms)
  if csc_controlling_speed:
    v = min(v, csc_target_ms)
  return v


def merge_vcruise_with_slc(v_cruise_ms: float, csc_target_ms: float, slc_desired_ms: float,
                           slc_target_ms: float, cruising_speed_ms: float,
                           csc_controlling_speed: bool = False,
                           raise_holdoff: bool = False) -> float:
  """Full AP1 merge order: min(targets) then apply_slc_raise_after_min.

  Mirrors frogpilot_vcruise.py so unit tests cover the DI≥CRUISING_SPEED
  regression (raise must stick at 30 when DI=11.5, limit 25, offset +5).
  When raise_holdoff is True (tip/sticky authority — skip SLC lift), return min only.
  """
  targets = [csc_target_ms, v_cruise_ms, slc_desired_ms]
  v = min(t if t >= cruising_speed_ms else v_cruise_ms for t in targets)
  if raise_holdoff:
    return v
  return apply_slc_raise_after_min(
    v, slc_desired_ms, slc_target_ms, cruising_speed_ms,
    csc_controlling_speed, csc_target_ms)



def next_5_ms(speed_ms: float) -> float:
  """Next multiple of 5 mph above speed_ms (30→35, 36→40); exact multiples go +5."""
  mph = round(float(speed_ms) * CV.MS_TO_MPH, 4)
  return float((int(mph // 5.0) + 1) * 5.0) * CV.MPH_TO_MS


def prev_5_ms(speed_ms: float) -> float:
  """Next-lower multiple of 5 mph (31→30, 30→25, 36→35); exact multiples go −5."""
  mph = round(float(speed_ms) * CV.MS_TO_MPH, 4)
  if mph <= 0.0:
    return 0.0
  if mph % 5.0 == 0.0:
    return max(mph - 5.0, 0.0) * CV.MPH_TO_MS
  return float(int(mph // 5.0) * 5.0) * CV.MPH_TO_MS


class Ap1RaiseHoldoff:
  """AP1 raise gate: engage stalk type + tip set authority (no DECEL holdoff).

  Desired:
    UP/DN engage → sticky vEgo (no SLC raise; holds through limit changes in
      both directions until RWD pull or disengage)
    RWD (pull-toward) engage → allow raise to SLC+offset immediately
    Engage classified from stalk pressed within RECENT_S (not same-frame only)
    Caller must pass continuous stalk levels (button_states), not edge-only events
      (UP/DN/RWD via accelPressed/decelPressed/resumePressed)
    Engaged UP/DN → tip (±1 SpdCtrl 16/32 / next-5 SpdCtrl 4/8 on the rising
      edge); a hold ≥ TIP_HOLD_S upgrades a ±1 press once as a fallback
    Tip current_set_ms from sticky latch / tip_ms / raised set — sticky first
      (never SLC floor while sticky); after pull raise, set_hint floors with
      slc_desired (no tip seed; never DI half-seed)
    Tipped set is authority until RWD pull or disengage (zone changes do not
      clear tip); never follow DI_cruiseSet; SLC raise must not reclaim after tip.
    RWD pull clears tip and re-allows SLC raise (SLC tracking mode — no tip seed)
    tip_ms fed as slc.overridden_speed so Max Set Speed gas returns to the tip
  """

  RECENT_S = 0.8  # Tesla clears buttonEvents 80–240 ms before enabled rises
  TIP_STEP_MS = 1.0 * CV.MPH_TO_MS
  # Lowest software set a down tip can reach. tip_ms == 0 means "no tip", so a
  # tip to 0 used to drop into SLC tracking (raise to posted+offset). Not tied
  # to the SLC posted-limit guard (15 mph floor) — tips may go below 15 mph.
  TIP_MIN_MS = 1.0 * CV.MPH_TO_MS
  TIP_HOLD_S = 0.45  # fallback only: hold upgrades ±1 → next/prev 5 once
  V_CRUISE_MAX_MS = 145.0 * CV.KPH_TO_MS
  DT_DEFAULT = 0.05  # model tick; avoid importing realtime here

  def __init__(self):
    self.sticky_vego = False
    self.latched_vego_ms = 0.0
    self.tip_ms = 0.0
    self.tip_dir = 0  # +1 tip-up, -1 tip-down, 0 idle
    self._tip_base_ms = 0.0
    self._tip_hold_s = 0.0
    self._tip_press_active = False
    self._tip_upgraded = False
    self._prev_enabled = False
    self._prev_up = False
    self._prev_dn = False
    self._t = 0.0
    self._last_up_t = -1e9
    self._last_dn_t = -1e9
    self._last_rwd_t = -1e9

  def _clear_tip(self):
    self.tip_ms = 0.0
    self.tip_dir = 0
    self._tip_base_ms = 0.0
    self._tip_hold_s = 0.0
    self._tip_press_active = False
    self._tip_upgraded = False

  def update(self, enabled: bool, decel_pressed: bool, up_pressed: bool,
             rwd_pressed: bool, slc_target_ms: float, v_ego_ms: float,
             current_set_ms: float = 0.0, dt: float | None = None,
             tip_full: bool = False):
    """Update state.

    tip_full: True when SpdCtrl is UP_2ND (4) or DN_2ND (8) — next-5 on rising
    edge (and mid-press upgrade). False → ±1 on edge; hold ≥ TIP_HOLD_S upgrades.

    Returns (allow_raise, sticky_vego_ms|None).
    sticky_vego_ms is the latched engage speed when UP/DN sticky mode is active.
    tip_ms (attribute) is the engaged tip latch in m/s when > 0 (authority).
    """
    enabled = bool(enabled)
    rising = enabled and not self._prev_enabled
    dt = self.DT_DEFAULT if dt is None else float(dt)
    self._t += dt

    if up_pressed:
      self._last_up_t = self._t
    if decel_pressed:
      self._last_dn_t = self._t
    if rwd_pressed:
      self._last_rwd_t = self._t

    recent_up = (self._t - self._last_up_t) <= self.RECENT_S
    recent_dn = (self._t - self._last_dn_t) <= self.RECENT_S
    recent_rwd = (self._t - self._last_rwd_t) <= self.RECENT_S
    up_edge = bool(up_pressed) and not self._prev_up
    dn_edge = bool(decel_pressed) and not self._prev_dn

    tip_full = bool(tip_full)
    # Posted-limit changes never touch sticky or tip: a stalk-tip engage (sticky)
    # or engaged tip holds through higher AND lower limits until RWD pull or
    # disengage (never auto-raise into a faster zone). slc_target_ms is unused
    # here; SLC tracking (after a pull) follows limits in frogpilot_vcruise.

    # Engaged tip-up / tip-down / RES: continuous levels from caller (button_states).
    # tip_full (SpdCtrl 4/8) → next-5 / prev-5 on rising edge; else ±1.
    # Hold ≥ TIP_HOLD_S upgrades once when detent unknown. Mid-press tip_full
    # also upgrades once (DN_1ST→DN_2ND). RWD pull clears tip → SLC tracking.
    # Do not clear sticky on sustained UP after an UP-engage (only on up_edge / RWD).
    if enabled and self._prev_enabled:
      if rwd_pressed:
        self.sticky_vego = False
        self._clear_tip()
      elif up_edge:
        self.sticky_vego = False
        base = max(float(self.tip_ms), float(current_set_ms), 0.0)
        self._tip_base_ms = base
        self._tip_hold_s = 0.0
        self._tip_press_active = True
        self.tip_dir = 1
        if tip_full:
          self.tip_ms = min(next_5_ms(base), self.V_CRUISE_MAX_MS)
          self._tip_upgraded = True
        else:
          self.tip_ms = min(base + self.TIP_STEP_MS, self.V_CRUISE_MAX_MS)
          self._tip_upgraded = False
      elif dn_edge:
        self.sticky_vego = False
        base = max(float(self.tip_ms), float(current_set_ms), 0.0)
        self._tip_base_ms = base
        self._tip_hold_s = 0.0
        self._tip_press_active = True
        self.tip_dir = -1
        if tip_full:
          self.tip_ms = max(prev_5_ms(base), self.TIP_MIN_MS)
          self._tip_upgraded = True
        else:
          self.tip_ms = max(base - self.TIP_STEP_MS, self.TIP_MIN_MS)
          self._tip_upgraded = False
      if self._tip_press_active and not self._tip_upgraded:
        # Mid-press reach of pos2, or long hold without detent info
        if tip_full:
          if self.tip_dir > 0:
            nxt = next_5_ms(self._tip_base_ms)
            self.tip_ms = min(max(float(self.tip_ms), nxt), self.V_CRUISE_MAX_MS)
          elif self.tip_dir < 0:
            self.tip_ms = max(prev_5_ms(self._tip_base_ms), self.TIP_MIN_MS)
          self._tip_upgraded = True
        elif self.tip_dir > 0 and up_pressed:
          self._tip_hold_s += dt
          if self._tip_hold_s >= self.TIP_HOLD_S:
            nxt = next_5_ms(self._tip_base_ms)
            self.tip_ms = min(max(float(self.tip_ms), nxt), self.V_CRUISE_MAX_MS)
            self._tip_upgraded = True
        elif self.tip_dir < 0 and decel_pressed:
          self._tip_hold_s += dt
          if self._tip_hold_s >= self.TIP_HOLD_S:
            self.tip_ms = max(prev_5_ms(self._tip_base_ms), self.TIP_MIN_MS)
            self._tip_upgraded = True
      # Release ends hold timing (tip_ms latched value remains until clear)
      if self.tip_dir > 0 and not up_pressed:
        self._tip_press_active = False
        self._tip_hold_s = 0.0
      if self.tip_dir < 0 and not decel_pressed:
        self._tip_press_active = False
        self._tip_hold_s = 0.0

    if rising:
      # Prefer the most recent stalk in the recent window (UP/DN sticky vs RWD raise)
      candidates = []
      if recent_up:
        candidates.append(("up", self._last_up_t))
      if recent_dn:
        candidates.append(("dn", self._last_dn_t))
      if recent_rwd:
        candidates.append(("rwd", self._last_rwd_t))
      kind = max(candidates, key=lambda c: c[1])[0] if candidates else None
      if kind in ("up", "dn"):
        self.sticky_vego = True
        self.latched_vego_ms = max(float(v_ego_ms), 0.0)
        self._clear_tip()
      elif kind == "rwd":
        self.sticky_vego = False
        self._clear_tip()
      # else: no recent stalk → default allow_raise (legacy)

    if not enabled:
      self.sticky_vego = False
      self._clear_tip()

    self._prev_enabled = enabled
    self._prev_up = bool(up_pressed)
    self._prev_dn = bool(decel_pressed)

    # Tip or sticky blocks SLC raise; RWD pull clears tip so raise may re-latch.
    allow_raise = (not self.sticky_vego) and (float(self.tip_ms) <= 0.0)
    sticky = self.latched_vego_ms if self.sticky_vego else None
    return allow_raise, sticky


class Ap1SlcLimitGuard:
  """AP1 posted-limit sanity filter for SLC (drive 2a seg 5: 45→5 mph blip ~1.4 s).

  Applied in frogpilot_vcruise to the SLC target/offset it uses and publishes
  (frogpilotPlan.slcSpeedLimit[+Offset]), so SLC tracking after a pull, pull
  engage (controlsd initialize_v_cruise) and the speed-limit sign all see it.
  Tip-engage / engaged-tip holds ignore limits anyway.

    - 0 / no limit (< 1 m/s): passed through unchanged (existing SLC behaviour).
    - Posted limit below MIN_VALID_MPH (15): not a limit — keep the previous
      accepted limit. 15 itself is valid (school zones).
    - Rises and normal drops: accepted immediately (as before).
    - Sudden drop (more than SUDDEN_DROP_MPH, or below SUDDEN_DROP_RATIO of the
      accepted limit): must persist CONFIRM_S before it is accepted.
    - Except a drop straight back to the limit that was in force before a rise
      less than RETURN_WINDOW_S ago: accepted immediately (drive 35: a bogus
      35->80 rise was otherwise held 2 s longer on the way back to 35).
  The offset is the one SLC reported for the accepted limit.
  """

  MIN_VALID_MPH = 15.0
  SUDDEN_DROP_MPH = 15.0   # 45→30 / 55→40 (−15) stay immediate; 45→25 confirms
  SUDDEN_DROP_RATIO = 0.5  # 30→15 stays immediate; 40→15 confirms
  CONFIRM_S = 2.0          # 02a blip lasted ~1.4 s
  SAME_MPH = 1.0           # candidate counts as "the same" within ±1 mph
  EPS_MPH = 0.25           # kph/mph float noise around the thresholds
  RETURN_WINDOW_S = 30.0   # drop back to the pre-rise limit within this time is immediate

  def __init__(self):
    self.target_ms = 0.0
    self.offset_ms = 0.0
    self._pending_ms = 0.0
    self._pending_s = 0.0
    self._pre_rise_ms = 0.0
    self._since_rise_s = 0.0

  def _accept(self, target_ms: float, offset_ms: float):
    if self.target_ms >= 1.0 and float(target_ms) > self.target_ms + 1e-3:
      self._pre_rise_ms = self.target_ms
      self._since_rise_s = 0.0
    self.target_ms = float(target_ms)
    self.offset_ms = float(offset_ms)
    self._pending_ms = 0.0
    self._pending_s = 0.0

  def update(self, raw_target_ms: float, raw_offset_ms: float, dt: float = 0.05):
    """Returns (target_ms, offset_ms) to use for SLC."""
    raw = float(raw_target_ms)
    raw_mph = raw * CV.MS_TO_MPH
    cur_mph = self.target_ms * CV.MS_TO_MPH
    self._since_rise_s += float(dt)
    back_to_pre_rise = (self._pre_rise_ms >= 1.0 and self._since_rise_s <= self.RETURN_WINDOW_S and
                        abs(raw - self._pre_rise_ms) * CV.MS_TO_MPH <= self.SAME_MPH)
    if raw < 1.0:
      self._accept(raw, raw_offset_ms)
    elif raw_mph < self.MIN_VALID_MPH - self.EPS_MPH:
      self._pending_ms = 0.0
      self._pending_s = 0.0
    else:
      sudden = cur_mph > 0.0 and raw_mph < cur_mph and (
        (cur_mph - raw_mph) > self.SUDDEN_DROP_MPH + self.EPS_MPH or
        raw_mph < cur_mph * self.SUDDEN_DROP_RATIO - self.EPS_MPH)
      if not sudden or back_to_pre_rise:
        self._accept(raw, raw_offset_ms)
      else:
        if self._pending_s > 0.0 and abs(raw - self._pending_ms) * CV.MS_TO_MPH <= self.SAME_MPH:
          self._pending_s += float(dt)
        else:
          self._pending_ms = raw
          self._pending_s = float(dt)
        if self._pending_s >= self.CONFIRM_S - 1e-6:
          self._accept(raw, raw_offset_ms)
    return self.target_ms, self.offset_ms


def ap1_cruise_ms(v_cruise_ms: float, sticky_vego_ms: float | None, tip_ms: float,
                  allow_raise: bool, slc_desired_ms: float | None, slc_target_ms: float,
                  cruising_speed_ms: float, csc_controlling_speed: bool,
                  csc_target_ms: float) -> float:
  """AP1 set authority after Ap1RaiseHoldoff.update (used by frogpilot_vcruise).

  Sticky (stalk-tip engage) and tipped set are the driver's set: they hold
  through posted-limit changes in BOTH directions until RWD pull / disengage.
  Only an active Curve Speed Controller may lower them temporarily.
  SLC tracking (after a pull, allow_raise) keeps the post-min value (follows
  lower limits) and lifts to SLC+offset on higher limits.
  """
  if sticky_vego_ms is not None:
    v = float(sticky_vego_ms)
    if csc_controlling_speed:
      v = min(v, csc_target_ms)
    return v
  if float(tip_ms) > 0.0:
    v = float(tip_ms)
    if csc_controlling_speed:
      v = min(v, csc_target_ms)
    return v
  if allow_raise:
    return apply_slc_raise_after_min(
      v_cruise_ms, slc_desired_ms, slc_target_ms, cruising_speed_ms,
      csc_controlling_speed, csc_target_ms)
  return v_cruise_ms


def cluster_display_kph(v_cruise_cluster_kph: float, frogpilot_v_cruise_ms: float) -> float:
  """Comma / HUD set digit follows the SLC-raised planner cruise when higher.

  Never lowers the displayed set for curve-speed slowdowns — only lifts when
  frogpilotPlan.vCruise is above the DI-seeded cluster set.

  When pcmCruise standstill zeros DI set, VCruiseHelper publishes UNSET (255).
  Prefer the frogpilot plan digit over the sentinel so comma does not show ~158.
  """
  fp_kph = float(frogpilot_v_cruise_ms) * CV.MS_TO_KPH
  cluster = float(v_cruise_cluster_kph)
  if cluster >= V_CRUISE_UNSET_KPH - 0.5:
    return fp_kph if fp_kph > 0.0 else cluster
  if fp_kph > cluster:
    return fp_kph
  return cluster


def cruise_set_mph(set_speed_ms: float | None) -> float | None:
  """Convert OP set speed (m/s) to mph for DAS_accSpeedLimit (DBC factor 0.4).

  Returns None (keep stock IC set) for missing/non-positive speeds and for the
  V_CRUISE_UNSET sentinel (255 kph ≈ 158 mph) that pcmCruise publishes when
  DI_cruiseSet is 0 at standstill — packing that onto 0x389 made the cluster digital set lock ~90
  (route 23 --5/--8). Also rejects values above V_CRUISE_MAX.
  """
  if set_speed_ms is None:
    return None
  speed = float(set_speed_ms)
  if speed <= 0.0:
    return None
  # UNSET leak: hud.setSpeed = 255 kph * KPH_TO_MS
  if speed >= V_CRUISE_UNSET_MS * 0.95:
    return None
  mph = speed * CV.MS_TO_MPH
  if mph <= 0.0 or mph > V_CRUISE_MAX_MPH + 0.5:
    return None
  return mph
