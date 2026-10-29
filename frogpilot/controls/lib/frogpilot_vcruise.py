#!/usr/bin/env python3
from openpilot.common.conversions import Conversions as CV
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import COMFORT_BRAKE

from openpilot.frogpilot.common.frogpilot_variables import CRUISING_SPEED, PLANNER_TIME
from openpilot.frogpilot.controls.lib.curve_speed_controller import CurveSpeedController
from openpilot.frogpilot.controls.lib.speed_limit_controller import SpeedLimitController
from openpilot.selfdrive.car.tesla.curve_speed import TESLA_CSC_MAX_LAT_ACCEL

class FrogPilotVCruise:
  def __init__(self, FrogPilotPlanner):
    self.frogpilot_planner = FrogPilotPlanner

    self.csc = CurveSpeedController(self)
    self.slc = SpeedLimitController()

    self.forcing_stop = False
    self.override_force_stop = False

    self.override_force_stop_timer = 0
    # AP1: engage stalk policy + engaged tip software set (tip authority until pull).
    from openpilot.selfdrive.car.tesla.slc_raise import Ap1RaiseHoldoff, Ap1SlcLimitGuard
    self.ap1_raise_holdoff = Ap1RaiseHoldoff()
    self.ap1_limit_guard = Ap1SlcLimitGuard()
    self._ap1_tip_override_active = False

  def update(self, gps_position, now, time_validated, v_cruise, v_ego, sm, frogpilot_toggles):
    force_stop = self.frogpilot_planner.cem.stop_light_detected and sm["controlsState"].enabled and frogpilot_toggles.force_stops
    force_stop &= self.frogpilot_planner.model_stopped
    force_stop &= self.override_force_stop_timer <= 0

    self.force_stop_timer = self.force_stop_timer + DT_MDL if force_stop else 0

    force_stop_enabled = self.force_stop_timer >= 1

    self.override_force_stop |= sm["carState"].gasPressed
    self.override_force_stop |= sm["frogpilotCarState"].accelPressed
    self.override_force_stop &= force_stop_enabled

    if self.override_force_stop:
      self.override_force_stop_timer = 10
    elif self.override_force_stop_timer > 0:
      self.override_force_stop_timer -= DT_MDL

    v_cruise_cluster = max(sm["controlsState"].vCruiseCluster * CV.KPH_TO_MS, v_cruise)
    v_cruise_diff = v_cruise_cluster - v_cruise

    v_ego_cluster = max(sm["carState"].vEgoCluster, v_ego)
    v_ego_diff = v_ego_cluster - v_ego

    if frogpilot_toggles.car_make == "tesla":
      self.csc.max_lateral_acceleration = TESLA_CSC_MAX_LAT_ACCEL

    # FrogsGoMoo's Curve Speed Controller
    if v_ego > CRUISING_SPEED and sm["controlsState"].enabled and self.frogpilot_planner.road_curvature_detected and frogpilot_toggles.curve_speed_controller:
      self.csc.update_target(v_ego)

      self.csc_controlling_speed = True

      self.csc_target = self.csc.target
    else:
      self.csc.log_data(v_ego, sm)

      self.csc_controlling_speed = False
      self.csc.target_set = False

      self.csc_target = v_cruise

    # Pfeiferj's Speed Limit Controller
    self.slc.frogpilot_toggles = frogpilot_toggles

    if frogpilot_toggles.speed_limit_controller:
      self.slc.update_limits(sm["frogpilotCarState"].dashboardSpeedLimit, gps_position, sm["frogpilotNavigation"].navigationSpeedLimit, now, time_validated, v_cruise, v_ego, sm)
      self.slc.update_override(v_cruise, v_cruise_diff, v_ego, v_ego_diff, sm)

      self.slc_offset = self.slc.offset
      self.slc_target = self.slc.target
      if str(getattr(frogpilot_toggles, "car_model", "") or "") == "TESLA_AP1_MODELS":
        # AP1: ignore bogus <15 mph limits, confirm sudden big drops (2a 5 mph
        # blip). Feeds SLC tracking, pull engage and the published limit.
        self.slc_target, self.slc_offset = self.ap1_limit_guard.update(
          self.slc_target, self.slc_offset, DT_MDL)
    elif frogpilot_toggles.show_speed_limits:
      self.slc.update_limits(sm["frogpilotCarState"].dashboardSpeedLimit, gps_position, sm["frogpilotNavigation"].navigationSpeedLimit, now, time_validated, v_cruise, v_ego, sm)

      self.slc_offset = 0
      self.slc_target = self.slc.target
    else:
      self.slc_offset = 0
      self.slc_target = 0

    if force_stop_enabled and not self.override_force_stop:
      self.forcing_stop |= not sm["carState"].standstill

      self.tracked_model_length = max(self.tracked_model_length - (v_ego * DT_MDL), 0)
      v_cruise = min((self.tracked_model_length // PLANNER_TIME), v_cruise)

    else:
      self.forcing_stop = False

      self.tracked_model_length = self.frogpilot_planner.model_length

      targets = [self.csc_target, v_cruise]
      slc_desired = None
      if frogpilot_toggles.speed_limit_controller:
        slc_desired = max(self.slc.overridden_speed, self.slc_target + self.slc_offset) - v_ego_diff
        targets.append(slc_desired)

      v_cruise = min([target if target >= CRUISING_SPEED else v_cruise for target in targets])
      # AP1: lift AFTER min() so pre-lift DI/csc seed cannot undo the raise.
      # Engage: UP/DN → sticky vEgo; RWD → raise. Engaged UP/DN → tip set
      # (authority until pull). No stalk TX / panda change.
      if frogpilot_toggles.speed_limit_controller and \
         str(getattr(frogpilot_toggles, "car_model", "") or "") == "TESLA_AP1_MODELS":
        from openpilot.selfdrive.car.tesla.slc_raise import ap1_cruise_ms
        enabled = bool(sm["carState"].cruiseState.enabled)
        fp_cs = sm["frogpilotCarState"]
        # Continuous UP/DN/RWD levels from button_states (via frogpilot_card).
        up = bool(fp_cs.accelPressed)
        dn = bool(fp_cs.decelPressed)
        rwd = bool(getattr(fp_cs, "resumePressed", False))
        # Tip base = current software set (sticky latch / tip / raised set).
        # NEVER use raw post-min DI alone after a pull raise — DI_cruiseSet is
        # ~vEgo/2 under OP overlay (drive28: tip DN from 51 latched ~21/24.5).
        # Sticky still wins first so engaged tip-from-sticky is not SLC-floored
        # (drive26/27 F2). When neither sticky nor tip (post-pull allow_raise),
        # floor the hint with slc_desired so tip ±1 from the raised set.
        hold = self.ap1_raise_holdoff
        if hold.sticky_vego and float(hold.latched_vego_ms) > 0.0:
          set_hint = float(hold.latched_vego_ms)
        elif float(hold.tip_ms) > 0.0:
          set_hint = float(hold.tip_ms)
        else:
          set_hint = float(v_cruise)
          if slc_desired is not None and float(slc_desired) >= CRUISING_SPEED:
            set_hint = max(set_hint, float(slc_desired))
        # SpdCtrl 4/8 = pos2 next-5; 16/32 = pos1 ±1 (drive 2a).
        spd = int(getattr(fp_cs, "spdCtrlLvr", 0) or 0)
        tip_full = spd in (4, 8)  # UP_2ND / DN_2ND
        allow_raise, sticky_vego = hold.update(
          enabled,
          dn, up, rwd, float(self.slc_target), float(v_ego),
          current_set_ms=set_hint, dt=DT_MDL, tip_full=tip_full,
        )
        tip_ms = float(self.ap1_raise_holdoff.tip_ms)
        tip_dir = int(self.ap1_raise_holdoff.tip_dir)
        if tip_ms > 0:
          # Max Set Speed gas override returns to the software tip set.
          if tip_dir < 0:
            self.slc.overridden_speed = tip_ms
          else:
            self.slc.overridden_speed = max(float(self.slc.overridden_speed), tip_ms)
          self._ap1_tip_override_active = True
        elif getattr(self, "_ap1_tip_override_active", False):
          # Tip cleared (pull / disengage): drop stale override so SLC+offset
          # can apply (drive 2a ME30 — tip seed had floored desired at 51).
          self.slc.overridden_speed = 0.0
          self._ap1_tip_override_active = False
          slc_desired = max(0.0, float(self.slc_target) + float(self.slc_offset)) - v_ego_diff
        # Sticky (UP/DN engage) and tipped set are authority: they hold through
        # posted-limit changes in BOTH directions until pull/disengage (no SLC
        # cap, no SLC raise, never follow DI_cruiseSet); only CSC may lower.
        # SLC tracking mode after RWD pull (no tip seed — 2a ME30) follows lower
        # limits and raises to SLC+offset; set_hint floors with slc_desired so
        # tips base off the raised set.
        v_cruise = ap1_cruise_ms(
          v_cruise, sticky_vego, tip_ms, allow_raise, slc_desired, self.slc_target,
          CRUISING_SPEED, self.csc_controlling_speed, self.csc_target)

    return v_cruise
