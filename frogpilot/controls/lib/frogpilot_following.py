#!/usr/bin/env python3
import numpy as np

from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import COMFORT_BRAKE, STOP_DISTANCE, desired_follow_distance, get_jerk_factor, get_T_FOLLOW
from openpilot.selfdrive.car.tesla.stalk_follow import apply_stalk_t_follow
from openpilot.selfdrive.car.tesla.regen_aware import RegenPowerEstimator
from openpilot.selfdrive.car.tesla.regen_brake import (ap1_ambient_c, ap1_comfort_brake, apply_cold_comfort_brake,
                                                        apply_cold_t_follow, cold_comfort_regen_enabled)

from openpilot.frogpilot.common.frogpilot_variables import CITY_SPEED_LIMIT, CRUISING_SPEED, MAX_T_FOLLOW

TRAFFIC_JERK_BP = [0.0, CITY_SPEED_LIMIT]
TRAFFIC_ACCELERATION_JERK = [0.50, 0.50]
TRAFFIC_DANGER_JERK =       [0.50, 0.50]
TRAFFIC_SPEED_JERK =        [0.50, 0.50]

TRAFFIC_FOLLOW_BP = [0.0,  CRUISING_SPEED]
TRAFFIC_FOLLOW =    [0.50, 1.00]

class FrogPilotFollowing:
  def __init__(self, FrogPilotPlanner):
    self.frogpilot_planner = FrogPilotPlanner

    self.following_lead = False

    self.acceleration_jerk = 0
    self.danger_jerk = 0
    self.desired_follow_distance = 0
    self.speed_jerk = 0
    self.t_follow = 0
    # ColdComfortRegen t_follow / display-distance bias. Separate from plannerd's estimator.
    self._cold_est = RegenPowerEstimator()
    self._cold_est_on = False

  def update(self, v_ego, sm, frogpilot_toggles):
    if sm["controlsState"].enabled and sm["frogpilotCarState"].trafficModeEnabled:
      self.acceleration_jerk = float(np.interp(v_ego, TRAFFIC_JERK_BP, TRAFFIC_ACCELERATION_JERK))
      self.danger_jerk = float(np.interp(v_ego, TRAFFIC_JERK_BP, TRAFFIC_DANGER_JERK))
      self.speed_jerk = float(np.interp(v_ego, TRAFFIC_JERK_BP, TRAFFIC_SPEED_JERK))
      self.t_follow = float(np.interp(v_ego, TRAFFIC_FOLLOW_BP, TRAFFIC_FOLLOW))
    elif sm["controlsState"].enabled:
      if sm["carState"].aEgo >= 0:
        self.acceleration_jerk, self.danger_jerk, self.speed_jerk = get_jerk_factor(
          frogpilot_toggles.aggressive_jerk_acceleration, frogpilot_toggles.aggressive_jerk_danger, frogpilot_toggles.aggressive_jerk_speed,
          frogpilot_toggles.standard_jerk_acceleration, frogpilot_toggles.standard_jerk_danger, frogpilot_toggles.standard_jerk_speed,
          frogpilot_toggles.relaxed_jerk_acceleration, frogpilot_toggles.relaxed_jerk_danger, frogpilot_toggles.relaxed_jerk_speed,
          frogpilot_toggles.custom_personalities, sm["controlsState"].personality
        )
      else:
        self.acceleration_jerk, self.danger_jerk, self.speed_jerk = get_jerk_factor(
          frogpilot_toggles.aggressive_jerk_deceleration, frogpilot_toggles.aggressive_jerk_danger, frogpilot_toggles.aggressive_jerk_speed_decrease,
          frogpilot_toggles.standard_jerk_deceleration, frogpilot_toggles.standard_jerk_danger, frogpilot_toggles.standard_jerk_speed_decrease,
          frogpilot_toggles.relaxed_jerk_deceleration, frogpilot_toggles.relaxed_jerk_danger, frogpilot_toggles.relaxed_jerk_speed_decrease,
          frogpilot_toggles.custom_personalities, sm["controlsState"].personality
        )

      self.t_follow = get_T_FOLLOW(
        frogpilot_toggles.aggressive_follow,
        frogpilot_toggles.standard_follow,
        frogpilot_toggles.relaxed_follow,
        frogpilot_toggles.custom_personalities, sm["controlsState"].personality
      )
    else:
      self.acceleration_jerk = 0
      self.danger_jerk = 0
      self.speed_jerk = 0
      self.t_follow = 0

    # AP1 stalk scroll. speedOffset is the mapped follow seconds, or 0.
    # Other platforms pass enabled False so their t_follow is unchanged.
    self.t_follow = apply_stalk_t_follow(
      self.t_follow,
      sm["carState"].cruiseState.speedOffset,
      sm["controlsState"].enabled and sm["carParams"].carFingerprint == "TESLA_AP1_MODELS",
      frogpilot_toggles,
    )

    self.following_lead = self.frogpilot_planner.tracking_lead and self.frogpilot_planner.lead_one.dRel < (self.t_follow * 2) * v_ego

    if self.frogpilot_planner.frogpilot_weather.weather_id != 0:
      self.t_follow = min(self.t_follow + self.frogpilot_planner.frogpilot_weather.increase_following_distance, MAX_T_FOLLOW)

    tracking = sm["controlsState"].enabled and self.frogpilot_planner.tracking_lead
    if tracking:
      if not sm["frogpilotCarState"].trafficModeEnabled and frogpilot_toggles.human_following and frogpilot_toggles.has_radar and frogpilot_toggles.model_version != "v9":
        self.update_follow_values(self.frogpilot_planner.lead_one.dRel, v_ego, self.frogpilot_planner.lead_one.vLead, frogpilot_toggles)

    # ColdComfortRegen (default off). After every other t_follow adjustment so the
    # bias is not divided away by human following. Off leaves t_follow and comfort as they were.
    fingerprint = sm["carParams"].carFingerprint
    comfort_brake = ap1_comfort_brake(fingerprint)
    if cold_comfort_regen_enabled(fingerprint):
      fcs = sm["frogpilotCarState"]
      amb = ap1_ambient_c(getattr(fcs, "ap1AmbientTempC", float("nan")))
      self._cold_est_on = True
      self._cold_est.update(v_ego, sm["carControl"].actuators.accel, getattr(fcs, "ap1MotorTorqueNm", 0.0),
                            getattr(fcs, "ap1MotorRpm", 0.0), amb, DT_MDL)
      comfort_brake = apply_cold_comfort_brake(comfort_brake, amb, self._cold_est.limited, True)
      self.t_follow = apply_cold_t_follow(self.t_follow, amb, self._cold_est.limited, True, cap=MAX_T_FOLLOW)
    elif self._cold_est_on:
      self._cold_est = RegenPowerEstimator()
      self._cold_est_on = False

    if tracking:
      self.desired_follow_distance = int(desired_follow_distance(v_ego, self.frogpilot_planner.lead_one.vLead, self.t_follow, comfort_brake=comfort_brake))
    else:
      self.desired_follow_distance = 0

  def update_follow_values(self, lead_distance, v_ego, v_lead, frogpilot_toggles):
    # Offset by FrogAi for FrogPilot for a more natural approach to a faster lead
    if v_lead > v_ego:
      distance_factor = max(lead_distance - (v_ego * self.t_follow), 1)
      accelerating_offset = float(np.clip(STOP_DISTANCE - v_ego, 1, distance_factor))

      self.acceleration_jerk /= accelerating_offset
      self.speed_jerk /= accelerating_offset
      self.t_follow /= accelerating_offset

    # Offset by FrogAi for FrogPilot for a more natural approach to a slower lead
    if v_lead < v_ego:
      distance_factor = max(lead_distance - (v_lead * self.t_follow), 1)
      braking_offset = float(np.clip(min(v_ego - v_lead, v_lead) - COMFORT_BRAKE, 1, distance_factor))

      if lead_distance >= 100:
        far_lead_offset = max(lead_distance - (v_ego * self.t_follow) - STOP_DISTANCE, 0)
        braking_offset += far_lead_offset

      self.t_follow /= braking_offset
