"""AP1 low-conflict SLC raise helpers and DI_cruiseSet cruise-set source.

Not a product, no warranty, driver remains responsible, comply with local law.
"""

from collections import defaultdict
from types import SimpleNamespace

import pytest

from openpilot.common.conversions import Conversions as CV
from openpilot.selfdrive.car.tesla.slc_raise import (
  Ap1RaiseHoldoff,
  Ap1SlcLimitGuard,
  ap1_cruise_ms,
  apply_slc_raise_after_min,
  cluster_display_kph,
  cruise_set_mph,
  is_ap1,
  lift_cruise_ms,
  merge_vcruise_with_slc,
  next_5_ms,
  prev_5_ms,
  V_CRUISE_UNSET_MS,
)

MPH = CV.MPH_TO_MS
CRUISING = 5.0


def test_is_ap1_fingerprint():
  from openpilot.selfdrive.car.tesla.values import CAR
  assert is_ap1(CAR.TESLA_AP1_MODELS) is True
  assert is_ap1(CAR.TESLA_AP2_MODELS) is False
  assert is_ap1(None) is False
  assert is_ap1("something_else") is False


def test_lift_cruise_raises_into_faster_zone():
  # 30 zone set 36 mph → enter 45 zone with +6 offset → 51 mph
  out = lift_cruise_ms(36 * MPH, 51 * MPH, 45 * MPH, CRUISING)
  assert out == pytest.approx(51 * MPH)


def test_lift_cruise_does_not_raise_when_desired_lower():
  # Lowering is left to the existing min() merge in frogpilot_vcruise
  out = lift_cruise_ms(51 * MPH, 36 * MPH, 30 * MPH, CRUISING)
  assert out == pytest.approx(51 * MPH)


def test_lift_cruise_no_op_when_target_missing():
  assert lift_cruise_ms(36 * MPH, 51 * MPH, 0.0, CRUISING) == pytest.approx(36 * MPH)
  assert lift_cruise_ms(36 * MPH, 51 * MPH, 3.0, CRUISING) == pytest.approx(36 * MPH)


def test_merge_raise_sticks_when_di_at_or_above_cruising():
  """Regression: DI≈11.5 + limit 25 + offset 5 → 30, not collapse to 11.5.

  Pre-fix lift-before-min undid the raise whenever DI_cruiseSet >= CRUISING_SPEED.
  """
  di = 11.5 * MPH
  slc_target = 25 * MPH
  slc_desired = 30 * MPH  # 25 + 5 offset
  # CSC inactive: csc_target mirrors pre-lift DI seed (same as frogpilot_vcruise)
  out = merge_vcruise_with_slc(di, di, slc_desired, slc_target, CRUISING,
                               csc_controlling_speed=False)
  assert out == pytest.approx(30 * MPH)

  # Same for DI just above CRUISING (~11.18 mph) — the log oscillation case
  di_hi = 11.5 * MPH
  assert merge_vcruise_with_slc(di_hi, di_hi, slc_desired, slc_target, CRUISING) == pytest.approx(30 * MPH)

  # DI below CRUISING also raises (was already OK pre-fix)
  di_lo = 10.0 * MPH
  assert merge_vcruise_with_slc(di_lo, di_lo, slc_desired, slc_target, CRUISING) == pytest.approx(30 * MPH)


def test_merge_still_lowers_when_di_above_slc():
  """DI=60 in a 25+5 zone must still min-cap down to 30."""
  di = 60 * MPH
  slc_target = 25 * MPH
  slc_desired = 30 * MPH
  out = merge_vcruise_with_slc(di, di, slc_desired, slc_target, CRUISING)
  assert out == pytest.approx(30 * MPH)


def test_merge_csc_still_caps_after_raise():
  """Active CSC curve target must still cap below the SLC raise."""
  di = 11.5 * MPH
  slc_target = 25 * MPH
  slc_desired = 30 * MPH
  csc = 20 * MPH
  out = merge_vcruise_with_slc(di, csc, slc_desired, slc_target, CRUISING,
                               csc_controlling_speed=True)
  assert out == pytest.approx(20 * MPH)

  # Inactive CSC must NOT cap with the DI seed
  out_off = apply_slc_raise_after_min(11.5 * MPH, slc_desired, slc_target, CRUISING,
                                      False, di)
  assert out_off == pytest.approx(30 * MPH)


def test_cluster_display_only_lifts():
  cluster_kph = 36 * CV.MPH_TO_KPH
  assert cluster_display_kph(cluster_kph, 51 * MPH) == pytest.approx(51 * CV.MPH_TO_KPH)
  # CSC slowdown must not lower the displayed set
  assert cluster_display_kph(cluster_kph, 30 * MPH) == pytest.approx(cluster_kph)



def test_carcontroller_feeds_das_acc_speed_limit_overlay_with_04_scale():
  """OP set is written to DAS_accSpeedLimit; DBC factor must be 0.4 (not 0.2).

  Route 21 cluster digital set=60 was packing raised 30 with factor 0.2 (raw 150 → IC@0.4 = 60).
  """
  from pathlib import Path
  src = (Path(__file__).resolve().parents[1] / "carcontroller.py").read_text()
  assert "cruise_set_mph(float(hud.setSpeed)) if enabled else None" in src
  assert "cruise_set_mph=None," not in src  # must not hardcode stock-only
  cluster_src = (Path(__file__).resolve().parents[1] / "cluster.py").read_text()
  assert 'v["DAS_accSpeedLimit"] = float(h.cruise_set_mph)' in cluster_src
  # parents: tests -> tesla -> car -> selfdrive -> repo root
  dbc = (Path(__file__).resolve().parents[4] / "opendbc" / "tesla_can.dbc").read_text()
  assert 'SG_ DAS_accSpeedLimit : 0|10@1+ (0.4,0)' in dbc
  assert 'SG_ DAS_accSpeedLimit : 0|10@1+ (0.2,0)' not in dbc


def test_cruise_set_mph_conversion():
  assert cruise_set_mph(None) is None
  assert cruise_set_mph(0.0) is None
  assert cruise_set_mph(51 * MPH) == pytest.approx(51.0, abs=0.05)


def test_cruise_set_mph_rejects_unset_sentinel():
  """Route 23: pcmCruise standstill DI set=0 → VCruiseHelper UNSET must not hit IC."""
  assert cruise_set_mph(V_CRUISE_UNSET_MS) is None
  assert cruise_set_mph(V_CRUISE_UNSET_MS * 0.99) is None
  # Legitimate highway set still packs
  assert cruise_set_mph(80 * MPH) == pytest.approx(80.0, abs=0.1)


def test_cluster_display_prefers_plan_over_unset():
  """UNSET (255 kph) must not win over a real frogpilot plan digit."""
  fp = 46 * MPH
  assert cluster_display_kph(255.0, fp) == pytest.approx(46 * CV.MPH_TO_KPH)
  # Normal lift still works
  assert cluster_display_kph(30 * CV.MPH_TO_KPH, 46 * MPH) == pytest.approx(46 * CV.MPH_TO_KPH)


def test_merge_holdoff_skips_raise_so_di_wins():
  """raise_holdoff skips SLC lift in merge helper (DI path alone still mins).

  Tip/sticky authority: frogpilot_vcruise must NOT leave the plan on this DI
  result after a tip — Ap1RaiseHoldoff tip_ms supplies the software set instead.
  """
  di = 10.0 * MPH
  out = merge_vcruise_with_slc(di, di, 30 * MPH, 25 * MPH, CRUISING, raise_holdoff=True)
  assert out == pytest.approx(10.0 * MPH)
  assert merge_vcruise_with_slc(di, di, 30 * MPH, 25 * MPH, CRUISING, raise_holdoff=False) == pytest.approx(30 * MPH)


def test_ap1_raise_holdoff_engage_set_still_sticky_not_raise():
  """UP/DN engage → sticky vEgo; RWD engage → allow raise."""
  h = Ap1RaiseHoldoff()
  vego = 27.0 * MPH
  # disengaged
  allow, sticky = h.update(False, False, False, False, 25 * MPH, vego)
  assert allow is True and sticky is None
  # DN engage edge
  allow, sticky = h.update(True, True, False, False, 25 * MPH, vego)
  assert allow is False
  assert sticky == pytest.approx(vego)
  assert h.sticky_vego is True
  # RWD engage from disengage
  h2 = Ap1RaiseHoldoff()
  h2.update(False, False, False, False, 25 * MPH, vego)
  allow, sticky = h2.update(True, False, False, True, 25 * MPH, vego)
  assert allow is True and sticky is None


def test_ap1_engaged_dn_does_not_arm_holdoff():
  """Engaged DN tips software set; does not arm a raise-holdoff mute timer."""
  h = Ap1RaiseHoldoff()
  set30 = 30 * MPH
  h.update(True, False, False, False, 25 * MPH, 20 * MPH, current_set_ms=set30)
  allow, sticky = h.update(True, True, False, False, 25 * MPH, 20 * MPH, current_set_ms=set30)
  assert sticky is None
  assert h.tip_ms == pytest.approx(29 * MPH)
  assert h.tip_dir == -1
  assert allow is False  # tip authority blocks raise — not a DECEL holdoff flag
  assert not hasattr(h, "holdoff") or getattr(h, "holdoff", False) is False


def test_ap1_tip_authority_until_pull_or_disengage():
  """After DN tip, SLC raise does not reclaim until RWD pull; disengage clears tip."""
  dt = 0.05
  slc = 25 * MPH
  set30 = 30 * MPH
  h = Ap1RaiseHoldoff()
  h.update(True, False, False, False, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  allow, _ = h.update(True, True, False, False, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  assert h.tip_ms == pytest.approx(29 * MPH)
  assert allow is False
  # Limit rise must NOT clear tip / reclaim raise
  allow, _ = h.update(True, False, False, False, 40 * MPH, 20 * MPH, current_set_ms=29 * MPH, dt=dt)
  assert h.tip_ms == pytest.approx(29 * MPH)
  assert allow is False
  # RWD pull clears tip and re-allows SLC raise
  allow, sticky = h.update(True, False, False, True, 40 * MPH, 20 * MPH, current_set_ms=29 * MPH, dt=dt)
  assert h.tip_ms == 0.0
  assert allow is True and sticky is None
  # Tip then disengage clears
  h.update(True, False, False, False, 40 * MPH, 20 * MPH, current_set_ms=46 * MPH, dt=dt)
  h.update(True, True, False, False, 40 * MPH, 20 * MPH, current_set_ms=46 * MPH, dt=dt)
  assert h.tip_ms > 0
  h.update(False, False, False, False, 40 * MPH, 20 * MPH, current_set_ms=46 * MPH, dt=dt)
  assert h.tip_ms == 0.0



def test_ap1_raise_holdoff_recent_stalk_gap_sticky_and_rwd():
  """drive24: buttonEvents clear 80–240 ms before enabled rises — still classify.

  Press DN/UP → clear buttons → enable 80–200 ms later → sticky (no raise).
  Mirror for RWD → allow raise. Same-frame press+enable still covered above.
  """
  dt = 0.05
  vego = 27.0 * MPH
  slc = 25 * MPH

  # DN → gap ~150 ms (3 ticks) → enable rising → sticky
  h = Ap1RaiseHoldoff()
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, True, False, False, slc, vego, dt=dt)  # DN press
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, False, False, False, slc, vego, dt=dt)  # ~150 ms later
  allow, sticky = h.update(True, False, False, False, slc, vego, dt=dt)
  assert allow is False
  assert sticky == pytest.approx(vego)
  assert h.sticky_vego is True

  # UP → gap ~200 ms (4 ticks) → sticky
  h = Ap1RaiseHoldoff()
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, False, True, False, slc, vego, dt=dt)  # UP press
  for _ in range(4):
    h.update(False, False, False, False, slc, vego, dt=dt)
  allow, sticky = h.update(True, False, False, False, slc, vego, dt=dt)
  assert allow is False and sticky == pytest.approx(vego)

  # RWD → gap ~100 ms → allow raise
  h = Ap1RaiseHoldoff()
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, False, False, True, slc, vego, dt=dt)  # RWD
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, False, False, False, slc, vego, dt=dt)
  allow, sticky = h.update(True, False, False, False, slc, vego, dt=dt)
  assert allow is True and sticky is None

  # Both DN then later RWD: most recent wins → RWD raise
  h = Ap1RaiseHoldoff()
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, True, False, False, slc, vego, dt=dt)  # DN first
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, False, False, True, slc, vego, dt=dt)  # RWD more recent
  h.update(False, False, False, False, slc, vego, dt=dt)
  allow, sticky = h.update(True, False, False, False, slc, vego, dt=dt)
  assert allow is True and sticky is None


def test_next_5_ms_snap():
  assert next_5_ms(30 * MPH) == pytest.approx(35 * MPH)
  assert next_5_ms(36 * MPH) == pytest.approx(40 * MPH)
  assert next_5_ms(35 * MPH) == pytest.approx(40 * MPH)
  assert next_5_ms(39 * MPH) == pytest.approx(40 * MPH)
  assert next_5_ms(40 * MPH) == pytest.approx(45 * MPH)


def test_ap1_tip_up_plus_one_and_clears():
  """Engaged short accelCruise bumps tip +1 mph; disengage clears it (zone change does not).

  DECEL tip-downs the software set (−1) the same way (no holdoff); tip is authority.

  AP1 buttonEvents are binary (UP_1ST/UP_2ND both accelCruise). Short tip = rising
  edge + release before TIP_HOLD_S → +1 only. Cap at V_CRUISE_MAX.
  """
  dt = 0.05
  slc = 25 * MPH
  set30 = 30 * MPH  # Max Set Speed floor
  h = Ap1RaiseHoldoff()
  # Engage via RWD (allow raise), then tip-up
  h.update(False, False, False, False, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  allow, sticky = h.update(True, False, False, True, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  assert allow is True and sticky is None
  # Steady engaged, no buttons
  h.update(True, False, False, False, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  # UP rising edge → tip 31; tip authority blocks further SLC raise
  allow, sticky = h.update(True, False, True, False, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  assert allow is False
  assert h.tip_ms == pytest.approx(31 * MPH)
  # Hold UP pressed another frame (< TIP_HOLD_S): still +1 only, no second tip
  h.update(True, False, True, False, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  assert h.tip_ms == pytest.approx(31 * MPH)
  # Release then tip again → 32
  h.update(True, False, False, False, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  h.update(True, False, True, False, slc, 20 * MPH, current_set_ms=31 * MPH, dt=dt)
  assert h.tip_ms == pytest.approx(32 * MPH)

  # Engaged DECEL tip-downs software set (−1 from 32 → 31); tip authority blocks raise
  allow, sticky = h.update(True, True, False, False, slc, 20 * MPH, current_set_ms=32 * MPH, dt=dt)
  assert allow is False
  assert h.tip_ms == pytest.approx(31 * MPH)
  assert h.tip_dir == -1

  # Tip then disengage clears
  h.update(True, False, False, False, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  h.update(True, False, True, False, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  assert h.tip_ms > 0
  h.update(False, False, False, False, slc, 20 * MPH, current_set_ms=set30, dt=dt)
  assert h.tip_ms == 0.0

  # Tip then lower SLC: tip STAYS (authority until pull / disengage — drive 2a)
  h = Ap1RaiseHoldoff()
  h.update(True, False, False, False, 40 * MPH, 20 * MPH, current_set_ms=46 * MPH, dt=dt)
  h.update(True, False, True, False, 40 * MPH, 20 * MPH, current_set_ms=46 * MPH, dt=dt)
  assert h.tip_ms == pytest.approx(47 * MPH)
  h.update(True, False, False, False, 25 * MPH, 20 * MPH, current_set_ms=set30, dt=dt)
  assert h.tip_ms == pytest.approx(47 * MPH)
  # Pull clears tip → SLC tracking
  allow, _ = h.update(True, False, False, True, 25 * MPH, 20 * MPH, current_set_ms=set30, dt=dt)
  assert h.tip_ms == 0.0 and allow is True


def test_ap1_tip_up_next_5_on_long_hold():
  """Full lift: rising +1 then held ≥ TIP_HOLD_S upgrades once to next-5 from press base.

  36→40 (not 37 then +5). Short hold stays +1. Second long tip from 40→45.
  UP-engage sticky is not cleared by sustained UP after engage (only up_edge).
  """
  dt = 0.05
  slc = 35 * MPH
  set36 = 36 * MPH
  h = Ap1RaiseHoldoff()
  h.update(True, False, False, False, slc, 30 * MPH, current_set_ms=set36, dt=dt)

  # Rising edge → +1 (37)
  h.update(True, False, True, False, slc, 30 * MPH, current_set_ms=set36, dt=dt)
  assert h.tip_ms == pytest.approx(37 * MPH)

  # Hold through TIP_HOLD_S → upgrade to 40 from base 36 (not 37+ something)
  hold_frames = int(Ap1RaiseHoldoff.TIP_HOLD_S / dt) + 1
  for _ in range(hold_frames):
    h.update(True, False, True, False, slc, 30 * MPH, current_set_ms=set36, dt=dt)
  assert h.tip_ms == pytest.approx(40 * MPH)
  assert h._tip_upgraded is True

  # Further hold does not tip again
  for _ in range(5):
    h.update(True, False, True, False, slc, 30 * MPH, current_set_ms=40 * MPH, dt=dt)
  assert h.tip_ms == pytest.approx(40 * MPH)

  # Release and long-tip again: 40 → 41 then upgrade to 45
  h.update(True, False, False, False, slc, 30 * MPH, current_set_ms=40 * MPH, dt=dt)
  h.update(True, False, True, False, slc, 30 * MPH, current_set_ms=40 * MPH, dt=dt)
  assert h.tip_ms == pytest.approx(41 * MPH)
  for _ in range(hold_frames):
    h.update(True, False, True, False, slc, 30 * MPH, current_set_ms=40 * MPH, dt=dt)
  assert h.tip_ms == pytest.approx(45 * MPH)

  # Short tip only: 30 set → 31, release before hold threshold
  h2 = Ap1RaiseHoldoff()
  set30 = 30 * MPH
  h2.update(True, False, False, False, 25 * MPH, 20 * MPH, current_set_ms=set30, dt=dt)
  h2.update(True, False, True, False, 25 * MPH, 20 * MPH, current_set_ms=set30, dt=dt)
  assert h2.tip_ms == pytest.approx(31 * MPH)
  # 2 frames held (0.1 s) << 0.45 s then release
  h2.update(True, False, True, False, 25 * MPH, 20 * MPH, current_set_ms=set30, dt=dt)
  h2.update(True, False, False, False, 25 * MPH, 20 * MPH, current_set_ms=set30, dt=dt)
  assert h2.tip_ms == pytest.approx(31 * MPH)

  # UP engage sticky: sustained UP after engage must not clear sticky / tip
  h3 = Ap1RaiseHoldoff()
  vego = 27 * MPH
  h3.update(False, False, False, False, 25 * MPH, vego, dt=dt)
  h3.update(False, False, True, False, 25 * MPH, vego, dt=dt)  # UP before enable
  allow, sticky = h3.update(True, False, True, False, 25 * MPH, vego, dt=dt)  # engage rising, UP still held
  assert allow is False and sticky == pytest.approx(vego)
  # Sustained UP while sticky — still sticky, no tip upgrade path
  for _ in range(hold_frames):
    allow, sticky = h3.update(True, False, True, False, 25 * MPH, vego, current_set_ms=30 * MPH, dt=dt)
  assert allow is False and sticky == pytest.approx(vego)
  assert h3.tip_ms == 0.0


def test_offset_25mph_uses_offset2():
  """25 mph posted limit must select Offset2 (not Offset1 via 11.176 < 11.2).

  Mirrors speed_limit_controller.offset rounded mph/kph bands; also asserts the
  source uses round()+band so the 25 mph edge cannot regress to Offset1.
  """
  from pathlib import Path

  def offset_for(target_ms, is_metric, offsets):
    if is_metric:
      band = int(round(float(target_ms) * CV.MS_TO_KPH))
      highs = (29, 49, 59, 79, 99, 119, 140)
    else:
      band = int(round(float(target_ms) * CV.MS_TO_MPH))
      highs = (24, 34, 44, 54, 64, 74, 99)
    for high, off in zip(highs, offsets):
      if band <= high:
        return off
    return 0

  o1, o2, o3 = 5 * MPH, 6 * MPH, 6 * MPH
  offs = (o1, o2, o3, o3, 10 * MPH, 10 * MPH, 10 * MPH)
  assert offset_for(25 * MPH, False, offs) == pytest.approx(o2)
  assert offset_for(24 * MPH, False, offs) == pytest.approx(o1)
  assert offset_for(34 * MPH, False, offs) == pytest.approx(o2)
  assert offset_for(35 * MPH, False, offs) == pytest.approx(o3)
  assert offset_for(30 * CV.KPH_TO_MS, True, offs) == pytest.approx(o2)
  assert offset_for(29 * CV.KPH_TO_MS, True, offs) == pytest.approx(o1)
  # 11.176 m/s is exactly 25 mph — old `low < target < 11.2` wrongly took Offset1
  assert offset_for(11.176, False, offs) == pytest.approx(o2)

  src = (Path(__file__).resolve().parents[4] / "frogpilot" / "controls" / "lib" /
         "speed_limit_controller.py").read_text()
  assert "int(round(float(self.target) * CV.MS_TO_MPH))" in src
  assert "if band <= high:" in src
  assert "low < self.target < high" not in src


def test_prev_5_ms_snap():
  assert prev_5_ms(31 * MPH) == pytest.approx(30 * MPH)
  assert prev_5_ms(30 * MPH) == pytest.approx(25 * MPH)
  assert prev_5_ms(36 * MPH) == pytest.approx(35 * MPH)
  assert prev_5_ms(35 * MPH) == pytest.approx(30 * MPH)
  assert prev_5_ms(0.0) == pytest.approx(0.0)


def test_ap1_level_held_across_missed_edge_still_sticky():
  """drive25: short edge missed by events but level held → sticky on enable.

  Models 20 Hz planner ticks that never see the 10 ms press edge; continuous
  level True across the gap still arms _last_up/_dn_t. Gap 80–200 ms ≪ RECENT_S.
  """
  dt = 0.05
  vego = 19.0 * MPH
  slc = 25 * MPH

  # UP level held while disengaged (no discrete "edge event" needed), gap, enable
  h = Ap1RaiseHoldoff()
  h.update(False, False, False, False, slc, vego, dt=dt)
  # Level held for ~100 ms (2 ticks) while disengaged — arms _last_up_t every tick
  h.update(False, False, True, False, slc, vego, dt=dt)
  h.update(False, False, True, False, slc, vego, dt=dt)
  # Release level, gap ~150 ms, then enable (btn_age style 80–200 ms)
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, False, False, False, slc, vego, dt=dt)
  allow, sticky = h.update(True, False, False, False, slc, vego, dt=dt)
  assert allow is False
  assert sticky == pytest.approx(vego)
  assert h.sticky_vego is True

  # DN level while disengaged then enable ~100 ms later
  h = Ap1RaiseHoldoff()
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, True, False, False, slc, vego, dt=dt)  # DN level
  h.update(False, True, False, False, slc, vego, dt=dt)
  h.update(False, False, False, False, slc, vego, dt=dt)
  h.update(False, False, False, False, slc, vego, dt=dt)
  allow, sticky = h.update(True, False, False, False, slc, vego, dt=dt)
  assert allow is False and sticky == pytest.approx(vego)

  # Regression: UP level while !enabled must arm even if "held clear" patterns
  # previously zeroed up before update — levels stay True across the press.
  h = Ap1RaiseHoldoff()
  h.update(False, False, False, False, slc, vego, dt=dt)
  for _ in range(3):
    h.update(False, False, True, False, slc, vego, dt=dt)  # UP held disengaged
  allow, sticky = h.update(True, False, True, False, slc, vego, dt=dt)  # enable, UP still
  assert allow is False and sticky == pytest.approx(vego)


def test_ap1_decel_tip_down_no_di_chase():
  """drive25: engaged DECEL latches software set; does not track DI downward.

  Short DN → −1 mph from current set; long hold → next-lower-5. Plan must stay
  on tip_ms (e.g. 30) even when DI sits at ~vEgo/2 (cliff was 31→12→3).
  No raise-holdoff flag — tip_ms alone is authority until pull.
  """
  dt = 0.05
  slc = 25 * MPH
  set31 = 31 * MPH
  h = Ap1RaiseHoldoff()
  # Engage via RWD (allow raise), settle
  h.update(False, False, False, False, slc, 24 * MPH, current_set_ms=set31, dt=dt)
  h.update(True, False, False, True, slc, 24 * MPH, current_set_ms=set31, dt=dt)
  h.update(True, False, False, False, slc, 24 * MPH, current_set_ms=set31, dt=dt)

  # Short DECEL tip-down: 31 → 30; tip authority blocks raise (no holdoff flag)
  allow, sticky = h.update(True, True, False, False, slc, 24 * MPH, current_set_ms=set31, dt=dt)
  assert allow is False and sticky is None
  assert h.tip_ms == pytest.approx(30 * MPH)
  assert h.tip_dir == -1
  # Release before TIP_HOLD_S — stay at 30 (no further DI tracking)
  h.update(True, False, False, False, slc, 20 * MPH, current_set_ms=30 * MPH, dt=dt)
  assert h.tip_ms == pytest.approx(30 * MPH)
  allow, _ = h.update(True, False, False, False, slc, 12 * MPH, current_set_ms=30 * MPH, dt=dt)
  assert h.tip_ms == pytest.approx(30 * MPH)
  assert allow is False  # still tip authority

  # Long DECEL: base 32 → short 31, long → prev_5(32)=30
  h2 = Ap1RaiseHoldoff()
  set32 = 32 * MPH
  h2.update(True, False, False, False, slc, 24 * MPH, current_set_ms=set32, dt=dt)
  h2.update(True, True, False, False, slc, 24 * MPH, current_set_ms=set32, dt=dt)
  assert h2.tip_ms == pytest.approx(31 * MPH)
  hold_frames = int(Ap1RaiseHoldoff.TIP_HOLD_S / dt) + 1
  for _ in range(hold_frames):
    h2.update(True, True, False, False, slc, 24 * MPH, current_set_ms=set32, dt=dt)
  assert h2.tip_ms == pytest.approx(30 * MPH)  # prev_5(32)
  assert h2._tip_upgraded is True

  # Long from exact multiple: 30 → short 29, long → 25
  h3 = Ap1RaiseHoldoff()
  set30 = 30 * MPH
  h3.update(True, False, False, False, slc, 24 * MPH, current_set_ms=set30, dt=dt)
  h3.update(True, True, False, False, slc, 24 * MPH, current_set_ms=set30, dt=dt)
  assert h3.tip_ms == pytest.approx(29 * MPH)
  for _ in range(hold_frames):
    h3.update(True, True, False, False, slc, 24 * MPH, current_set_ms=set30, dt=dt)
  assert h3.tip_ms == pytest.approx(25 * MPH)
  assert h3.update(True, False, False, False, slc, 24 * MPH, current_set_ms=25 * MPH, dt=dt)[0] is False

  # UP tips up from tipped set (still tip authority); RWD pull clears tip → allow raise
  allow, _ = h3.update(True, False, True, False, slc, 24 * MPH, current_set_ms=25 * MPH, dt=dt)
  assert h3.tip_dir == 1
  assert h3.tip_ms == pytest.approx(26 * MPH)
  assert allow is False  # tip still authority
  allow, _ = h3.update(True, False, False, True, slc, 24 * MPH, current_set_ms=26 * MPH, dt=dt)
  assert h3.tip_ms == 0.0
  assert allow is True


def test_ap1_up_dn_sticky_engage_and_rwd_raise():
  """UP and DN engage → sticky vEgo; RWD engage → allow SLC raise."""
  vego = 27.0 * MPH
  slc = 25 * MPH
  for dn, up in ((True, False), (False, True)):
    h = Ap1RaiseHoldoff()
    h.update(False, False, False, False, slc, vego)
    allow, sticky = h.update(True, dn, up, False, slc, vego)
    assert allow is False
    assert sticky == pytest.approx(vego)
  h = Ap1RaiseHoldoff()
  h.update(False, False, False, False, slc, vego)
  allow, sticky = h.update(True, False, False, True, slc, vego)
  assert allow is True and sticky is None


def test_ap1_tip_plus_minus_one_and_five():
  """Engaged UP/DN: short ±1 mph; long hold → next-5 / next-lower-5."""
  dt = 0.05
  slc = 25 * MPH
  set36 = 36 * MPH
  hold_frames = int(Ap1RaiseHoldoff.TIP_HOLD_S / dt) + 1

  h = Ap1RaiseHoldoff()
  h.update(True, False, False, False, slc, 30 * MPH, current_set_ms=set36, dt=dt)
  h.update(True, False, True, False, slc, 30 * MPH, current_set_ms=set36, dt=dt)
  assert h.tip_ms == pytest.approx(37 * MPH)
  for _ in range(hold_frames):
    h.update(True, False, True, False, slc, 30 * MPH, current_set_ms=set36, dt=dt)
  assert h.tip_ms == pytest.approx(40 * MPH)

  h2 = Ap1RaiseHoldoff()
  h2.update(True, False, False, False, slc, 30 * MPH, current_set_ms=set36, dt=dt)
  h2.update(True, True, False, False, slc, 30 * MPH, current_set_ms=set36, dt=dt)
  assert h2.tip_ms == pytest.approx(35 * MPH)
  for _ in range(hold_frames):
    h2.update(True, True, False, False, slc, 30 * MPH, current_set_ms=set36, dt=dt)
  assert h2.tip_ms == pytest.approx(35 * MPH)  # prev_5(36)=35
  # From 40: short 39, long → 35
  h3 = Ap1RaiseHoldoff()
  set40 = 40 * MPH
  h3.update(True, False, False, False, slc, 30 * MPH, current_set_ms=set40, dt=dt)
  h3.update(True, True, False, False, slc, 30 * MPH, current_set_ms=set40, dt=dt)
  assert h3.tip_ms == pytest.approx(39 * MPH)
  for _ in range(hold_frames):
    h3.update(True, True, False, False, slc, 30 * MPH, current_set_ms=set40, dt=dt)
  assert h3.tip_ms == pytest.approx(35 * MPH)


def test_ap1_after_dn_tip_slc_raise_not_until_pull():
  """After DN tip below SLC+offset, allow_raise stays False until RWD pull."""
  dt = 0.05
  slc = 25 * MPH
  set30 = 30 * MPH  # SLC+offset
  h = Ap1RaiseHoldoff()
  h.update(False, False, False, False, slc, 24 * MPH, current_set_ms=set30, dt=dt)
  allow, _ = h.update(True, False, False, True, slc, 24 * MPH, current_set_ms=set30, dt=dt)
  assert allow is True
  h.update(True, False, False, False, slc, 24 * MPH, current_set_ms=set30, dt=dt)
  allow, _ = h.update(True, True, False, False, slc, 24 * MPH, current_set_ms=set30, dt=dt)
  assert h.tip_ms == pytest.approx(29 * MPH)
  assert allow is False
  for _ in range(10):
    allow, _ = h.update(True, False, False, False, slc, 24 * MPH, current_set_ms=29 * MPH, dt=dt)
    assert allow is False
    assert h.tip_ms == pytest.approx(29 * MPH)
  allow, _ = h.update(True, False, False, True, slc, 24 * MPH, current_set_ms=29 * MPH, dt=dt)
  assert allow is True and h.tip_ms == 0.0


def test_ap1_card_feeds_button_states_levels():
  """frogpilot_card AP1 path must read CI.CS.button_states for accel/decel/RWD."""
  from pathlib import Path
  src = (Path(__file__).resolve().parents[4] / "frogpilot" / "controls" /
         "frogpilot_card.py").read_text()
  assert "_apply_ap1_stalk_levels" in src
  assert "button_states" in src
  assert "ButtonType.accelCruise" in src
  assert "ButtonType.resumeCruise" in src
  assert "resumePressed" in src
  vsrc = (Path(__file__).resolve().parents[4] / "frogpilot" / "controls" / "lib" /
          "frogpilot_vcruise.py").read_text()
  assert "fp_cs.accelPressed" in vsrc
  assert "fp_cs.decelPressed" in vsrc
  assert "resumePressed" in vsrc
  assert "_ap1_accel_held" not in vsrc  # edge latch removed (was clear-before-update bug)
  assert "_ap1_resume_held" not in vsrc  # RWD now continuous level, not buttonEvents
  # Sticky latch must still beat slc_desired (drive26/27); post-pull floor is ok (drive28)
  assert "hold.sticky_vego" in vsrc
  assert "latched_vego_ms" in vsrc
  assert vsrc.index("hold.sticky_vego") < vsrc.index("max(set_hint, float(slc_desired))")


def test_ap1_short_rwd_while_enabled_clears_sticky_and_allows_raise():
  """drive26 F1: short RWD hold while sticky must clear tip/sticky → allow_raise.

  Models continuous resumePressed level seen for ~100 ms (2×50 ms ticks) while
  enabled — the buttonEvents edge-only path missed these on route 26.
  """
  dt = 0.05
  slc = 25 * MPH
  vego = 20.9 * MPH
  h = Ap1RaiseHoldoff()
  # UP sticky engage
  h.update(False, False, True, False, slc, vego, dt=dt)
  allow, sticky = h.update(True, False, True, False, slc, vego, current_set_ms=vego, dt=dt)
  assert allow is False and sticky == pytest.approx(vego)
  h.update(True, False, False, False, slc, vego, current_set_ms=vego, dt=dt)
  assert h.sticky_vego is True
  # Short RWD level while enabled (two ticks ≈ 100 ms, no long hold needed)
  allow, sticky = h.update(True, False, False, True, slc, vego, current_set_ms=vego, dt=dt)
  assert h.tip_ms == 0.0
  assert h.sticky_vego is False
  assert sticky is None
  assert allow is True
  # Level released — raise still allowed
  allow, sticky = h.update(True, False, False, False, slc, vego, current_set_ms=vego, dt=dt)
  assert allow is True and sticky is None

  # Same while tip authority is active: RWD clears tip → allow raise
  h2 = Ap1RaiseHoldoff()
  set30 = 30 * MPH
  h2.update(True, False, False, False, slc, 24 * MPH, current_set_ms=set30, dt=dt)
  h2.update(True, True, False, False, slc, 24 * MPH, current_set_ms=set30, dt=dt)
  assert h2.tip_ms == pytest.approx(29 * MPH)
  allow, _ = h2.update(True, False, False, True, slc, 24 * MPH, current_set_ms=29 * MPH, dt=dt)
  assert h2.tip_ms == 0.0 and allow is True


def test_ap1_tip_base_from_sticky_not_slc_floor():
  """drive26/27 F2: engaged tip from sticky uses latch, not max(plan, SLC+offset).

  DN from sticky ~20.9 → ~19.9 (not ~30). UP from sticky → +1 from sticky (not 31).
  """
  dt = 0.05
  slc = 25 * MPH
  vego = 20.9 * MPH
  slc_floor = 31 * MPH  # what the buggy set_hint = max(plan, slc_desired) fed

  # DN from sticky: tip base = latched vEgo (correct), not SLC floor
  h = Ap1RaiseHoldoff()
  h.update(False, False, False, True, slc, vego, dt=dt)  # arm nothing; settle
  h.update(False, True, False, False, slc, vego, dt=dt)  # DN before enable
  allow, sticky = h.update(True, True, False, False, slc, vego, current_set_ms=vego, dt=dt)
  assert allow is False and sticky == pytest.approx(vego)
  h.update(True, False, False, False, slc, vego, current_set_ms=vego, dt=dt)
  # Second DN while sticky — pass sticky latch as current_set (vcruise fix)
  allow, sticky = h.update(True, True, False, False, slc, vego, current_set_ms=vego, dt=dt)
  assert sticky is None  # tip mode clears sticky
  assert h.tip_ms == pytest.approx(vego - 1.0 * MPH)
  assert h.tip_ms == pytest.approx(19.9 * MPH)
  # Contrast: buggy SLC floor would have tipped to ~30
  h_bad = Ap1RaiseHoldoff()
  h_bad.update(True, False, False, False, slc, vego, current_set_ms=vego, dt=dt)
  h_bad.sticky_vego = True
  h_bad.latched_vego_ms = vego
  h_bad._prev_enabled = True
  allow, _ = h_bad.update(True, True, False, False, slc, vego, current_set_ms=slc_floor, dt=dt)
  assert h_bad.tip_ms == pytest.approx(30 * MPH)  # documents the bug class

  # UP from sticky: +1 from sticky, not jump to 31/32
  h2 = Ap1RaiseHoldoff()
  h2.update(False, False, True, False, slc, vego, dt=dt)
  allow, sticky = h2.update(True, False, True, False, slc, vego, current_set_ms=vego, dt=dt)
  assert sticky == pytest.approx(vego)
  h2.update(True, False, False, False, slc, vego, current_set_ms=vego, dt=dt)
  allow, sticky = h2.update(True, False, True, False, slc, vego, current_set_ms=vego, dt=dt)
  assert h2.tip_ms == pytest.approx(vego + 1.0 * MPH)
  assert h2.tip_ms == pytest.approx(21.9 * MPH)
  assert allow is False

  # After tip, further tip uses tip_ms (not SLC floor) as base
  h2.update(True, False, False, False, slc, vego, current_set_ms=h2.tip_ms, dt=dt)
  tip_before = h2.tip_ms
  h2.update(True, True, False, False, slc, vego, current_set_ms=tip_before, dt=dt)
  assert h2.tip_ms == pytest.approx(tip_before - 1.0 * MPH)


def test_ap1_vcruise_tip_base_prefers_sticky_over_slc():
  """Source: frogpilot_vcruise tip set_hint order is sticky → tip → raised/plan."""
  from pathlib import Path
  vsrc = (Path(__file__).resolve().parents[4] / "frogpilot" / "controls" / "lib" /
          "frogpilot_vcruise.py").read_text()
  # sticky latch before tip_ms before plan; post-pull floors with slc_desired
  i_sticky = vsrc.index("hold.sticky_vego")
  i_tip = vsrc.index("hold.tip_ms")
  i_plan = vsrc.index("set_hint = float(v_cruise)")
  assert i_sticky < i_tip < i_plan
  assert "set_hint = max(set_hint, float(slc_desired))" in vsrc
  # drive 2a: do NOT seed tip after raise (SLC tracking until stalk tip)
  assert "hold.tip_ms = float(v_cruise)" not in vsrc
  assert "SLC tracking mode" in vsrc


def test_ap1_tip_after_raise_not_di_half_seed():
  """drive28: after RWD raise to SLC+offset, tip DN must be raised−1, not DI≈vEgo/2.

  Logged: fp=51 dig=51 di_set/cs_v≈22; tip DN → fp/over=21 (half cliff).
  User wanted 51→50. Root cause: tip_ms cleared on raise; set_hint=post-min DI.
  """
  dt = 0.05
  slc = 45 * MPH
  raised = 51 * MPH  # 45+6
  di_half = 22.0 * MPH  # post-min DI / cs_v while overlay shows 51
  vego = 44.2 * MPH

  # Soft-sim of frogpilot_vcruise set_hint + tip seed (drive28 fix)
  def set_hint_for(hold, v_cruise_post_min, slc_desired):
    if hold.sticky_vego and float(hold.latched_vego_ms) > 0.0:
      return float(hold.latched_vego_ms)
    if float(hold.tip_ms) > 0.0:
      return float(hold.tip_ms)
    hint = float(v_cruise_post_min)
    if slc_desired is not None and float(slc_desired) >= 1.0:  # CRUISING-ish
      hint = max(hint, float(slc_desired))
    return hint

  def apply_raise_no_seed(hold, v_cruise_post_min, slc_desired):
    from openpilot.selfdrive.car.tesla.slc_raise import apply_slc_raise_after_min
    return apply_slc_raise_after_min(
      v_cruise_post_min, slc_desired, slc, 1.0 * MPH, False, v_cruise_post_min)

  # --- OLD bug path (no floor / no seed): tip from DI half ---
  h_old = Ap1RaiseHoldoff()
  h_old.update(True, False, False, False, slc, vego, current_set_ms=di_half, dt=dt)
  allow, sticky = h_old.update(True, False, False, True, slc, vego, current_set_ms=di_half, dt=dt)
  assert allow is True and sticky is None and h_old.tip_ms == 0.0
  h_old.update(True, False, False, False, slc, vego, current_set_ms=di_half, dt=dt)
  bad_hint = di_half
  allow, _ = h_old.update(True, True, False, False, slc, vego, current_set_ms=bad_hint, dt=dt)
  assert h_old.tip_ms == pytest.approx(di_half - 1.0 * MPH)  # ~21 — the cliff

  # --- NEW (2a): set_hint floors with raised; NO tip seed (SLC tracking until tip)
  h = Ap1RaiseHoldoff()
  h.update(True, False, False, False, slc, vego, current_set_ms=di_half, dt=dt)
  allow, sticky = h.update(True, False, False, True, slc, vego,
                           current_set_ms=set_hint_for(h, di_half, raised), dt=dt)
  assert allow is True and h.tip_ms == 0.0
  v = apply_raise_no_seed(h, di_half, raised)
  assert v == pytest.approx(raised)
  assert h.tip_ms == 0.0  # no seed — stay in SLC tracking
  # Release RWD; still allow_raise (tracks SLC)
  allow, sticky = h.update(True, False, False, False, slc, vego,
                           current_set_ms=set_hint_for(h, di_half, raised), dt=dt)
  assert allow is True and h.tip_ms == 0.0
  # Tip DN: set_hint floors to raised → 50 (not DI half)
  hint = set_hint_for(h, di_half, raised)
  assert hint == pytest.approx(raised)
  allow, _ = h.update(True, True, False, False, slc, vego, current_set_ms=hint, dt=dt)
  assert h.tip_ms == pytest.approx(raised - 1.0 * MPH)
  assert h.tip_ms == pytest.approx(50 * MPH)
  assert allow is False  # now tip authority
  h.update(True, False, False, False, slc, vego, current_set_ms=h.tip_ms, dt=dt)
  tip_before = h.tip_ms
  h.update(True, True, False, False, slc, vego, current_set_ms=tip_before, dt=dt)
  assert h.tip_ms == pytest.approx(tip_before - 1.0 * MPH)
  assert h.tip_ms == pytest.approx(49 * MPH)


def test_ap1_vcruise_set_hint_after_raise_floors_slc_not_di():
  """Source+policy: post-pull set_hint must max with slc_desired (not DI alone)."""
  from pathlib import Path
  vsrc = (Path(__file__).resolve().parents[4] / "frogpilot" / "controls" / "lib" /
          "frogpilot_vcruise.py").read_text()
  assert "drive28" in vsrc
  assert "vEgo/2" in vsrc
  assert "set_hint = max(set_hint, float(slc_desired))" in vsrc
  # drive 2a: raise path must not seed tip_ms (SLC tracking until stalk tip)
  assert "hold.tip_ms = float(v_cruise)" not in vsrc
  assert "tip_full" in vsrc and "spdCtrlLvr" in vsrc


def test_carstate_source_uses_di_cruise_set_not_digital():

  from pathlib import Path
  src = (Path(__file__).resolve().parents[1] / "carstate.py").read_text()
  assert 'cruiseState.speed = cp.vl["DI_state"]["DI_cruiseSet"]' in src
  assert 'cruiseState.speed = cp.vl["DI_state"]["DI_digitalSpeed"]' not in src


# Fixture duplicated lightly from test_ap1_speed_limit so this file is standalone.
@pytest.fixture
def carstate_mod(monkeypatch):
  import importlib
  import sys
  import types
  from openpilot.selfdrive.car.tesla.tests.test_ap1_speed_limit import (
    _Base, _Define, _stub_usb1,
  )
  _stub_usb1(monkeypatch)
  if "smbus2" not in sys.modules:
    smbus2 = types.ModuleType("smbus2")
    smbus2.SMBus = type("SMBus", (), {"__init__": lambda self, *a, **k: None})
    monkeypatch.setitem(sys.modules, "smbus2", smbus2)
  interfaces = types.ModuleType("openpilot.selfdrive.car.interfaces")
  interfaces.CarStateBase = _Base
  parser = types.ModuleType("opendbc.can.parser")

  class FakeParser:
    def __init__(self, dbc, messages, bus):
      self.messages = list(messages)
      self.bus = bus

  parser.CANParser = FakeParser
  define = types.ModuleType("opendbc.can.can_define")
  define.CANDefine = _Define
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.car.interfaces", interfaces)
  monkeypatch.setitem(sys.modules, "opendbc.can.parser", parser)
  monkeypatch.setitem(sys.modules, "opendbc.can.can_define", define)
  sys.modules.pop("openpilot.selfdrive.car.tesla.carstate", None)
  mod = importlib.import_module("openpilot.selfdrive.car.tesla.carstate")
  yield mod
  sys.modules.pop("openpilot.selfdrive.car.tesla.carstate", None)


def test_carstate_cruise_speed_tracks_di_cruise_set(carstate_mod):
  from openpilot.selfdrive.car.tesla.values import CAR
  from openpilot.selfdrive.car.tesla.tests.test_ap1_speed_limit import _ap1_cans, _prep_define

  CP = SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS)
  CS = carstate_mod.CarState(CP, None)
  _prep_define(CS)
  CS.can_define.dv["DI_state"]["DI_cruiseState"] = {2: "ENABLED"}

  cp, cp_cam = _ap1_cans(45.0)
  cp.vl["DI_state"]["DI_cruiseState"] = 2
  cp.vl["DI_state"]["DI_speedUnits"] = 0  # MPH
  cp.vl["DI_state"]["DI_cruiseSet"] = 36.0
  cp.vl["DI_state"]["DI_digitalSpeed"] = 41.0

  ret, _fp = CS.update(cp, cp_cam, None)
  assert ret.cruiseState.speed == pytest.approx(36.0 * MPH)
  assert ret.cruiseState.speed != pytest.approx(41.0 * MPH)

  # KPH units path
  CS.can_define.dv["DI_state"]["DI_speedUnits"] = {1: "KPH"}
  cp.vl["DI_state"]["DI_speedUnits"] = 1
  cp.vl["DI_state"]["DI_cruiseSet"] = 60.0  # kph
  cp.vl["DI_state"]["DI_digitalSpeed"] = 70.0
  ret, _fp = CS.update(cp, cp_cam, None)
  assert ret.cruiseState.speed == pytest.approx(60.0 * CV.KPH_TO_MS)


def test_ap1_tip_full_pos2_next_5_on_edge():
  """drive 2a: SpdCtrl DN_2ND/UP_2ND (tip_full) → next-5 on rising edge, no hold.

  Pos1 (tip_full=False) stays ±1. Matches stock IC briefly showing 45 then OP
  used to overwrite to 49 when pos1+pos2 collapsed into one level.
  """
  dt = 0.05
  slc = 45 * MPH
  set50 = 50 * MPH
  h = Ap1RaiseHoldoff()
  h.update(True, False, False, False, slc, 33 * MPH, current_set_ms=set50, dt=dt)

  # Pos1 DN → 49
  h.update(True, True, False, False, slc, 33 * MPH, current_set_ms=set50, dt=dt, tip_full=False)
  assert h.tip_ms == pytest.approx(49 * MPH)
  h.update(True, False, False, False, slc, 33 * MPH, current_set_ms=49 * MPH, dt=dt)

  # Reset to 50 then pos2 DN → 45 immediately
  h = Ap1RaiseHoldoff()
  h.update(True, False, False, False, slc, 33 * MPH, current_set_ms=set50, dt=dt)
  h.update(True, True, False, False, slc, 33 * MPH, current_set_ms=set50, dt=dt, tip_full=True)
  assert h.tip_ms == pytest.approx(45 * MPH)
  assert h._tip_upgraded is True
  # Short release — stays 45 (no ±1 stomp)
  h.update(True, False, False, False, slc, 33 * MPH, current_set_ms=45 * MPH, dt=dt)
  assert h.tip_ms == pytest.approx(45 * MPH)

  # Pos2 UP from 45 → 50
  h.update(True, False, True, False, slc, 33 * MPH, current_set_ms=45 * MPH, dt=dt, tip_full=True)
  assert h.tip_ms == pytest.approx(50 * MPH)

  # Mid-press: pos1 edge then tip_full becomes True → upgrade from press base
  h2 = Ap1RaiseHoldoff()
  h2.update(True, False, False, False, slc, 33 * MPH, current_set_ms=set50, dt=dt)
  h2.update(True, True, False, False, slc, 33 * MPH, current_set_ms=set50, dt=dt, tip_full=False)
  assert h2.tip_ms == pytest.approx(49 * MPH)
  h2.update(True, True, False, False, slc, 33 * MPH, current_set_ms=49 * MPH, dt=dt, tip_full=True)
  assert h2.tip_ms == pytest.approx(45 * MPH)  # prev_5 from base 50


def test_ap1_tip_stays_across_lower_zone_until_pull():
  """Manual tip is authority across SLC drop; RWD clears → allow_raise (ME30 path)."""
  dt = 0.05
  h = Ap1RaiseHoldoff()
  h.update(True, False, False, False, 45 * MPH, 30 * MPH, current_set_ms=51 * MPH, dt=dt)
  h.update(True, True, False, False, 45 * MPH, 30 * MPH, current_set_ms=51 * MPH, dt=dt, tip_full=False)
  assert h.tip_ms == pytest.approx(50 * MPH)
  # SLC 45 → 30: tip stays
  allow, _ = h.update(True, False, False, False, 30 * MPH, 27 * MPH, current_set_ms=50 * MPH, dt=dt)
  assert h.tip_ms == pytest.approx(50 * MPH) and allow is False
  # Pull → SLC tracking
  allow, _ = h.update(True, False, False, True, 30 * MPH, 27 * MPH, current_set_ms=50 * MPH, dt=dt)
  assert h.tip_ms == 0.0 and allow is True


# --- Tip-engage (sticky) holds through posted-limit changes until pull ---------

class _VCruiseSim:
  """Soft-sim of the frogpilot_vcruise AP1 block (no tip override bookkeeping).

  v_cruise into the block is post-min(DI, csc, slc_desired) like production.
  """
  DT = 0.05

  def __init__(self, offset_mph=5.0, di_mph=12.0):
    self.h = Ap1RaiseHoldoff()
    self.offset = offset_mph * MPH
    self.di = di_mph * MPH  # DI_cruiseSet ≈ vEgo/2 under OP overlay

  def step(self, enabled, slc_mph, vego_mph, up=False, dn=False, rwd=False,
           tip_full=False, csc=None):
    slc = slc_mph * MPH
    slc_desired = slc + self.offset
    targets = [self.di, self.di, slc_desired]
    v_cruise = min(t if t >= CRUISING else self.di for t in targets)
    h = self.h
    if h.sticky_vego and h.latched_vego_ms > 0.0:
      set_hint = h.latched_vego_ms
    elif h.tip_ms > 0.0:
      set_hint = h.tip_ms
    else:
      set_hint = max(v_cruise, slc_desired)
    allow, sticky = h.update(enabled, dn, up, rwd, slc, vego_mph * MPH,
                             current_set_ms=set_hint, dt=self.DT, tip_full=tip_full)
    return ap1_cruise_ms(v_cruise, sticky, h.tip_ms, allow, slc_desired, slc,
                         CRUISING, csc is not None, csc if csc is not None else v_cruise)

  def engage(self, kind, slc_mph, vego_mph):
    """Stalk pressed one tick while disengaged, released, then enable rises."""
    kw = {kind: True}
    self.step(False, slc_mph, vego_mph, **kw)
    self.step(False, slc_mph, vego_mph)
    return self.step(True, slc_mph, vego_mph)


@pytest.mark.parametrize("kind", ["up", "dn"])
def test_ap1_tip_engage_holds_through_higher_limits(kind):
  """School zone: tip-engage at 15 must not pick up 25 / 35 (+offset)."""
  sim = _VCruiseSim()
  v = sim.engage(kind, 15, 15)
  assert v == pytest.approx(15 * MPH)
  for slc_mph in (15, 25, 25, 35, 35):
    for _ in range(20):
      v = sim.step(True, slc_mph, 15)
    assert v == pytest.approx(15 * MPH)
    assert sim.h.sticky_vego is True and sim.h.tip_ms == 0.0


@pytest.mark.parametrize("kind", ["up", "dn"])
def test_ap1_tip_engage_holds_through_lower_limit(kind):
  """Tip-engage at 40 in a 40 zone; limit drops to 30 → set stays 40."""
  sim = _VCruiseSim()
  v = sim.engage(kind, 40, 40)
  assert v == pytest.approx(40 * MPH)
  for _ in range(40):
    v = sim.step(True, 30, 38)
  assert v == pytest.approx(40 * MPH)  # was min(40, 30+5) = 35 before
  assert sim.h.sticky_vego is True


def test_ap1_pull_after_tip_engage_tracks_slc_both_ways():
  """After tip-engage hold, RWD pull → posted+offset, then follows lower limits."""
  sim = _VCruiseSim()
  sim.engage("up", 15, 15)
  for _ in range(10):
    v = sim.step(True, 35, 15)
  assert v == pytest.approx(15 * MPH)
  v = sim.step(True, 35, 16, rwd=True)
  assert v == pytest.approx(40 * MPH)  # 35 + 5
  for _ in range(10):
    v = sim.step(True, 35, 20)
  assert v == pytest.approx(40 * MPH)
  assert sim.h.sticky_vego is False and sim.h.tip_ms == 0.0
  for _ in range(10):
    v = sim.step(True, 25, 30)
  assert v == pytest.approx(30 * MPH)  # follows lower limit 25 + 5
  for _ in range(10):
    v = sim.step(True, 45, 30)
  assert v == pytest.approx(50 * MPH)  # raises to 45 + 5 in SLC tracking


def test_ap1_engaged_tips_from_held_tip_engage_set():
  """After a higher limit, tips still base off the held sticky set (±1 / next-5)."""
  sim = _VCruiseSim()
  sim.engage("dn", 15, 15)
  for _ in range(10):
    sim.step(True, 25, 15)
  v = sim.step(True, 25, 15, up=True)  # pos1 UP
  assert v == pytest.approx(16 * MPH)
  sim.step(True, 25, 15)
  v = sim.step(True, 25, 15, up=True, tip_full=True)  # full tip UP
  assert v == pytest.approx(20 * MPH)
  sim.step(True, 25, 15)
  v = sim.step(True, 25, 15, dn=True)  # pos1 DN
  assert v == pytest.approx(19 * MPH)
  sim.step(True, 25, 15)
  v = sim.step(True, 25, 15, dn=True, tip_full=True)  # full tip DN
  assert v == pytest.approx(15 * MPH)
  sim.step(True, 25, 15)
  # Tipped set also holds through a higher limit until pull
  for _ in range(10):
    v = sim.step(True, 45, 15)
  assert v == pytest.approx(15 * MPH)


def test_ap1_tip_engage_csc_may_still_lower():
  """Only an active curve-speed target may temporarily lower the held set."""
  sim = _VCruiseSim()
  sim.engage("up", 40, 40)
  v = sim.step(True, 40, 40, csc=30 * MPH)
  assert v == pytest.approx(30 * MPH)
  v = sim.step(True, 40, 40)
  assert v == pytest.approx(40 * MPH)


def test_ap1_pull_engage_unchanged_tracks_slc():
  """RWD (pull) engage → posted+offset immediately; follows lower, raises higher."""
  sim = _VCruiseSim()
  v = sim.engage("rwd", 30, 20)
  assert v == pytest.approx(35 * MPH)
  assert sim.h.sticky_vego is False and sim.h.tip_ms == 0.0
  for _ in range(10):
    v = sim.step(True, 25, 25)
  assert v == pytest.approx(30 * MPH)
  for _ in range(10):
    v = sim.step(True, 40, 25)
  assert v == pytest.approx(45 * MPH)


def test_ap1_vcruise_uses_ap1_cruise_ms_without_sticky_slc_cap():
  """Source: vcruise delegates to ap1_cruise_ms; sticky no longer min()s slc_desired."""
  from pathlib import Path
  vsrc = (Path(__file__).resolve().parents[4] / "frogpilot" / "controls" / "lib" /
          "frogpilot_vcruise.py").read_text()
  assert "ap1_cruise_ms(" in vsrc
  assert "v_cruise = min(v_cruise, slc_desired)" not in vsrc
  h = Ap1RaiseHoldoff()
  assert not hasattr(h, "_prev_slc_target")
  assert not hasattr(Ap1RaiseHoldoff, "LIMIT_RISE_MS")


# --- AP1 posted-limit guard (drive 2a seg 5: 45→5 mph blip ~1.4 s) -----------

def _guard_run(g, seq, dt=0.05):
  """seq: [(limit_mph, offset_mph, seconds)] → list of (target_mph, offset_mph) per tick."""
  out = []
  for lim, off, secs in seq:
    for _ in range(int(round(secs / dt))):
      t, o = g.update(lim * MPH, off * MPH, dt)
      out.append((round(t / MPH, 2), round(o / MPH, 2)))
  return out


def test_ap1_guard_ignores_1s_5mph_blip():
  g = Ap1SlcLimitGuard()
  out = _guard_run(g, [(45, 6, 2.0), (5, 5, 1.0), (45, 6, 2.0)])
  assert all(x == (45, 6) for x in out)


def test_ap1_guard_ignores_sub15_even_if_persistent():
  g = Ap1SlcLimitGuard()
  out = _guard_run(g, [(45, 6, 1.0), (8, 5, 30.0), (10, 5, 10.0), (14, 5, 10.0)])
  assert all(x == (45, 6) for x in out)
  # Fresh start with only a bogus value → still "no limit" (0)
  g2 = Ap1SlcLimitGuard()
  assert _guard_run(g2, [(5, 5, 5.0)])[-1] == (0, 0)


def test_ap1_guard_accepts_15_school_zone():
  g = Ap1SlcLimitGuard()
  out = _guard_run(g, [(25, 5, 1.0), (15, 5, 0.05)])
  assert out[-1] == (15, 5)  # 25→15: −10, not under half → immediate
  g2 = Ap1SlcLimitGuard()
  assert _guard_run(g2, [(15, 5, 0.05)])[-1] == (15, 5)  # first limit 15 is valid


def test_ap1_guard_follows_real_45_to_30_immediately():
  g = Ap1SlcLimitGuard()
  out = _guard_run(g, [(45, 6, 1.0), (30, 6, 0.05)])
  assert out[-1] == (30, 6)  # 2a 13:57:47 ME 30 — −15 is a normal step


def test_ap1_guard_follows_real_35_to_25_immediately():
  g = Ap1SlcLimitGuard()
  out = _guard_run(g, [(35, 6, 1.0), (25, 6, 0.05)])
  assert out[-1] == (25, 6)


def test_ap1_guard_sudden_drop_needs_confirm():
  # 45→20 (−25): blip of 1 s ignored, persistent value accepted after ~2 s
  g = Ap1SlcLimitGuard()
  out = _guard_run(g, [(45, 6, 1.0), (20, 5, 1.0), (45, 6, 1.0)])
  assert all(x == (45, 6) for x in out)
  out = _guard_run(g, [(20, 5, 1.9)])
  assert out[-1] == (45, 6)
  out = _guard_run(g, [(20, 5, 0.15)])
  assert out[-1] == (20, 5)
  # 40→15 (under half) also confirms; 30→15 (exactly half) is immediate
  g2 = Ap1SlcLimitGuard()
  assert _guard_run(g2, [(40, 6, 1.0), (15, 5, 1.0)])[-1] == (40, 6)
  assert _guard_run(g2, [(15, 5, 1.0)])[-1] == (15, 5)
  g3 = Ap1SlcLimitGuard()
  assert _guard_run(g3, [(30, 6, 1.0), (15, 5, 0.05)])[-1] == (15, 5)


def test_ap1_guard_rises_and_no_limit_unchanged():
  g = Ap1SlcLimitGuard()
  assert _guard_run(g, [(25, 6, 1.0), (45, 6, 0.05)])[-1] == (45, 6)
  assert _guard_run(g, [(0, 0, 0.05)])[-1] == (0, 0)  # no limit passes through
  # A rise cancels a pending sudden drop
  g2 = Ap1SlcLimitGuard()
  _guard_run(g2, [(45, 6, 1.0), (20, 5, 1.5)])
  assert _guard_run(g2, [(50, 6, 0.05)])[-1] == (50, 6)
  assert _guard_run(g2, [(20, 5, 1.5)])[-1] == (50, 6)  # timer restarted


def test_ap1_guard_pull_engage_and_slc_tracking_use_filtered_limit():
  """SLC tracking after a pull holds 45+6 through a 5 mph blip (no dip to ~6)."""
  sim = _VCruiseSim(offset_mph=6.0)
  g = Ap1SlcLimitGuard()

  def step(raw_mph, **kw):
    t, _ = g.update(raw_mph * MPH, (5 if raw_mph < 25 else 6) * MPH, sim.DT)
    return sim.step(kw.pop("enabled", True), t / MPH, kw.pop("vego", 30), **kw)

  # Pull engage while a bogus 5 is showing → still 45+6
  step(45, enabled=False)
  step(5, enabled=False, rwd=True)
  step(5, enabled=False)
  v = step(5)
  assert v == pytest.approx(51 * MPH)
  for _ in range(20):
    v = step(5)
  assert v == pytest.approx(51 * MPH)
  for _ in range(20):
    v = step(30)  # real 45→30 still followed
  assert v == pytest.approx(36 * MPH)


def test_ap1_vcruise_guard_is_ap1_scoped():
  from pathlib import Path
  vsrc = (Path(__file__).resolve().parents[4] / "frogpilot" / "controls" / "lib" /
          "frogpilot_vcruise.py").read_text()
  i_guard = vsrc.index("self.ap1_limit_guard.update(")
  i_ap1 = vsrc.rindex('"TESLA_AP1_MODELS"', 0, i_guard)
  assert i_guard - i_ap1 < 400
  assert vsrc.index("self.ap1_limit_guard.update(") < vsrc.index("slc_desired = max(self.slc.overridden_speed")


# --- Guard is SLC-only: manual tips / tip-engage may go below 15 mph ----------

class _GuardedSim:
  """_VCruiseSim fed through Ap1SlcLimitGuard, like frogpilot_vcruise on AP1."""

  def __init__(self, offset_mph=5.0):
    self.sim = _VCruiseSim(offset_mph=offset_mph)
    self.g = Ap1SlcLimitGuard()
    self.h = self.sim.h

  def step(self, enabled, raw_slc_mph, vego_mph, **kw):
    t, _ = self.g.update(raw_slc_mph * MPH, 5 * MPH, self.sim.DT)
    return self.sim.step(enabled, t / MPH, vego_mph, **kw)

  def engage(self, kind, raw_slc_mph, vego_mph):
    self.step(False, raw_slc_mph, vego_mph, **{kind: True})
    self.step(False, raw_slc_mph, vego_mph)
    return self.step(True, raw_slc_mph, vego_mph)

  def tip(self, raw_slc_mph, vego_mph, up=False, dn=False, tip_full=False):
    v = self.step(True, raw_slc_mph, vego_mph, up=up, dn=dn, tip_full=tip_full)
    self.step(True, raw_slc_mph, vego_mph)  # release
    return v


def test_ap1_engaged_tip_down_below_15_with_guard_and_bogus_slc():
  """Tip DN from 15: full tip → 10, then −1 → 9, 8 … 1 mph; SLC blips/sub-15 ignored."""
  s = _GuardedSim()
  assert s.engage("dn", 15, 15) == pytest.approx(15 * MPH)
  v = s.tip(15, 15, dn=True, tip_full=True)
  assert v == pytest.approx(10 * MPH)
  raws = [15, 5, 8, 5, 15, 10, 15, 5, 15]  # school zone with bogus sub-15 readings
  for i, want in enumerate(range(9, 0, -1)):
    v = s.tip(raws[i], 10, dn=True)
    assert v == pytest.approx(want * MPH)
    assert s.h.tip_ms == pytest.approx(want * MPH)
    for _ in range(25):  # hold ~1.25 s while SLC reports a sub-15 value
      v = s.step(True, 5, 8)
    assert v == pytest.approx(want * MPH)
  # Existing floor: further DN (±1 or full tip) never leaves tip mode / jumps to SLC
  for full in (False, True, False):
    v = s.tip(15, 1, dn=True, tip_full=full)
    assert v == pytest.approx(1 * MPH)
    assert s.h.tip_ms > 0.0 and s.h.sticky_vego is False
  for _ in range(40):
    v = s.step(True, 15, 1)
  assert v == pytest.approx(1 * MPH)  # not 15+5 (SLC tracking) after reaching the floor
  # Tip back up from the floor still works
  assert s.tip(15, 1, up=True) == pytest.approx(2 * MPH)
  assert s.tip(15, 2, up=True, tip_full=True) == pytest.approx(5 * MPH)


def test_ap1_full_tip_down_from_5_floors_at_min_not_slc():
  s = _GuardedSim()
  s.engage("up", 25, 7)
  assert s.tip(25, 7, dn=True, tip_full=True) == pytest.approx(5 * MPH)
  v = s.tip(25, 6, dn=True, tip_full=True)
  assert v == pytest.approx(Ap1RaiseHoldoff.TIP_MIN_MS)
  for _ in range(40):
    v = s.step(True, 25, 5)
  assert v == pytest.approx(Ap1RaiseHoldoff.TIP_MIN_MS)


@pytest.mark.parametrize("kind", ["up", "dn"])
def test_ap1_tip_engage_at_9mph_holds_with_guard(kind):
  """Tip-engage at ~9 mph holds while SLC reports 25, a sub-15 value, or a blip."""
  s = _GuardedSim()
  v = s.engage(kind, 25, 9)
  assert v == pytest.approx(9 * MPH)
  for raw, secs in ((25, 1.0), (8, 3.0), (5, 0.7), (25, 1.0), (10, 5.0), (35, 1.0), (15, 2.5), (5, 1.0)):
    for _ in range(int(secs / 0.05)):
      v = s.step(True, raw, 9)
    assert v == pytest.approx(9 * MPH)
    assert s.h.sticky_vego is True
  # Guard really is active (35→15 confirmed after 2 s; later 5 ignored → keeps 15)
  assert s.g.target_ms == pytest.approx(15 * MPH)
  # Engaged tip from the 9 mph hold goes lower still
  assert s.tip(5, 9, dn=True) == pytest.approx(8 * MPH)


def test_ap1_guard_does_not_touch_tip_or_min_clamps():
  """Source: the guard only rewrites slc_target/slc_offset; tips clamp at TIP_MIN_MS."""
  import inspect
  from pathlib import Path
  from openpilot.selfdrive.car.tesla import slc_raise
  gsrc = inspect.getsource(Ap1SlcLimitGuard)
  for name in ("tip_ms", "latched_vego", "sticky", "MIN_VALID_MPH * ", "V_CRUISE"):
    assert name not in gsrc
  hsrc = inspect.getsource(Ap1RaiseHoldoff)
  assert "MIN_VALID" not in hsrc and "Ap1SlcLimitGuard" not in hsrc
  assert "TIP_MIN_MS" in hsrc
  assert Ap1RaiseHoldoff.TIP_MIN_MS == pytest.approx(1.0 * MPH)
  csrc = inspect.getsource(slc_raise.ap1_cruise_ms)
  assert "MIN_VALID" not in csrc and "guard" not in csrc.lower()
  vsrc = (Path(__file__).resolve().parents[4] / "frogpilot" / "controls" / "lib" /
          "frogpilot_vcruise.py").read_text()
  guard_line = [ln for ln in vsrc.splitlines() if "ap1_limit_guard.update(" in ln]
  assert len(guard_line) == 1
  assert guard_line[0].strip().startswith("self.slc_target, self.slc_offset = ")


def test_ap1_guard_drop_back_after_bogus_rise_is_immediate():
  # Drive 35 seg 11: limit 35, camera misread 80 for ~9.6 s, then back to 35.
  g = Ap1SlcLimitGuard()
  out = _guard_run(g, [(35, 6, 2.0), (80, 10, 9.6), (35, 6, 0.05)])
  assert out[-1] == (35, 6)  # no extra 2 s at 80 on the way back


def test_ap1_guard_drop_to_other_limit_after_rise_still_confirms():
  g = Ap1SlcLimitGuard()
  out = _guard_run(g, [(35, 6, 2.0), (80, 10, 5.0), (25, 6, 1.0)])
  assert out[-1] == (80, 10)  # 25 is not the pre-rise limit: 2 s confirm as before


def test_ap1_guard_drop_back_window_expires():
  g = Ap1SlcLimitGuard()
  out = _guard_run(g, [(25, 6, 2.0), (45, 6, 31.0), (25, 6, 1.0)])
  assert out[-1] == (45, 6)  # rise was >30 s ago: normal sudden-drop confirmation
  out = _guard_run(g, [(25, 6, 1.1)])
  assert out[-1] == (25, 6)
