#!/usr/bin/env python3
import math
import numpy as np

import cereal.messaging as messaging
from openpilot.common.conversions import Conversions as CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.selfdrive.car.interfaces import ACCEL_MIN, ACCEL_MAX
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import T_IDXS as T_IDXS_MPC
from openpilot.selfdrive.controls.lib.drive_helpers import V_CRUISE_MAX, V_CRUISE_UNSET, CONTROL_N, get_accel_from_plan
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.fcw_gate import planner_fcw
from openpilot.selfdrive.car.tesla.regen_brake import ap1_comfort_brake
from openpilot.selfdrive.car.tesla.regen_aware import (RegenPowerEstimator, RegenStopShaper, lead_stop_distance,
                                                        model_stop_distance, regen_aware_enabled)
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import STOP_DISTANCE
from openpilot.selfdrive.car.tesla.throttle_gate import ap1_throttle_gate_for, apply_cut

from openpilot.frogpilot.common.frogpilot_variables import MINIMUM_LATERAL_ACCELERATION

LON_MPC_STEP = 0.2  # first step is 0.2s
A_CRUISE_MAX_VALS = [2.0, 1.6, 0.8, 0.6]
A_CRUISE_MAX_BP = [0., 10.0, 25., 40.]
CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]
ALLOW_THROTTLE_THRESHOLD = 0.4
MIN_ALLOW_THROTTLE_SPEED = 2.5

# Lookup table for turns
_A_TOTAL_MAX_V = [1.7, 3.2]
_A_TOTAL_MAX_BP = [20., 40.]


def get_max_accel(v_ego):
  return float(np.interp(v_ego, A_CRUISE_MAX_BP, A_CRUISE_MAX_VALS))

def get_coast_accel(pitch):
  return np.sin(pitch) * -5.65 - 0.3  # fitted from data using xx/projects/allow_throttle/compute_coast_accel.py


def limit_accel_in_turns(v_ego, angle_steers, a_target, CP):
  """
  This function returns a limited long acceleration allowed, depending on the existing lateral acceleration
  this should avoid accelerating when losing the target in turns
  """
  # FIXME: This function to calculate lateral accel is incorrect and should use the VehicleModel
  # The lookup table for turns should also be updated if we do this
  a_total_max = np.interp(v_ego, _A_TOTAL_MAX_BP, _A_TOTAL_MAX_V)
  a_y = v_ego ** 2 * angle_steers * CV.DEG_TO_RAD / (CP.steerRatio * CP.wheelbase)

  if abs(a_y) > MINIMUM_LATERAL_ACCELERATION:
    a_x_allowed = math.sqrt(max(a_total_max ** 2 - a_y ** 2, 0.))
  else:
    a_x_allowed = a_target[1]

  return [a_target[0], min(a_target[1], a_x_allowed)]


class LongitudinalPlanner:
  def __init__(self, CP, init_v=0.0, init_a=0.0, dt=DT_MDL):
    self.CP = CP
    self.mpc = LongitudinalMpc(dt=dt)
    # TODO remove mpc modes when TR released
    self.mpc.mode = 'acc'
    self.fcw = False
    self.dt = dt
    self.allow_throttle = True
    # AP1-only allowThrottle smoothing (tesla/throttle_gate.py); None elsewhere.
    self.throttle_cut = 0.0
    self.ap1_throttle_gate = ap1_throttle_gate_for(CP.carFingerprint)
    # AP1: at engagement the braking limit is not ramped up from 0 when a lead needs it (see update()).
    self.ap1_engage_brake = self.ap1_throttle_gate is not None
    self.ap1_engage_ramp = False
    # AP1: regen-aware early stop braking (tesla/regen_aware.py). Adds braking only.
    self.ap1_regen_aware = regen_aware_enabled(CP.carFingerprint)
    self.regen_est = RegenPowerEstimator()
    self.regen_shaper = RegenStopShaper()
    self.regen_a_early = 0.0
    self.regen_stop_dist = -1.0

    self.a_desired = init_a
    self.v_desired_filter = FirstOrderFilter(init_v, 2.0, self.dt)
    self.prev_accel_clip = [ACCEL_MIN, ACCEL_MAX]
    self.output_a_target = 0.0
    self.output_should_stop = False

    self.v_desired_trajectory = np.zeros(CONTROL_N)
    self.a_desired_trajectory = np.zeros(CONTROL_N)
    self.j_desired_trajectory = np.zeros(CONTROL_N)
    self.solverExecutionTime = 0.0

  @staticmethod
  def parse_model(model_msg, v_ego, taco_tune):
    if (len(model_msg.position.x) == ModelConstants.IDX_N and
      len(model_msg.velocity.x) == ModelConstants.IDX_N and
      len(model_msg.acceleration.x) == ModelConstants.IDX_N):
      x = np.interp(T_IDXS_MPC, ModelConstants.T_IDXS, model_msg.position.x)
      v = np.interp(T_IDXS_MPC, ModelConstants.T_IDXS, model_msg.velocity.x)
      a = np.interp(T_IDXS_MPC, ModelConstants.T_IDXS, model_msg.acceleration.x)
      j = np.zeros(len(T_IDXS_MPC))
    else:
      x = np.zeros(len(T_IDXS_MPC))
      v = np.zeros(len(T_IDXS_MPC))
      a = np.zeros(len(T_IDXS_MPC))
      j = np.zeros(len(T_IDXS_MPC))

    if taco_tune:
      max_lat_accel = np.interp(v_ego, [5, 10, 20], [1.5, 2.0, 3.0])
      curvatures = np.interp(T_IDXS_MPC, ModelConstants.T_IDXS, model_msg.orientationRate.z) / np.clip(v, 0.3, 100.0)
      max_v = np.sqrt(max_lat_accel / (np.abs(curvatures) + 1e-3)) - 2.0
      v = np.minimum(max_v, v)

    if len(model_msg.meta.disengagePredictions.gasPressProbs) > 1:
      throttle_prob = model_msg.meta.disengagePredictions.gasPressProbs[1]
    else:
      throttle_prob = 1.0
    return x, v, a, j, throttle_prob

  def update(self, sm, classic_longitudinal, frogpilot_toggles):
    mode = 'blended' if sm['controlsState'].experimentalMode else 'acc'
    if classic_longitudinal:
      self.mpc.mode = mode

    if len(sm['carControl'].orientationNED) == 3:
      accel_coast = get_coast_accel(sm['carControl'].orientationNED[1])
    else:
      accel_coast = ACCEL_MAX

    v_ego = sm['carState'].vEgo
    v_cruise = sm['frogpilotPlan'].vCruise
    v_cruise_initialized = sm['controlsState'].vCruise != V_CRUISE_UNSET

    long_control_off = sm['controlsState'].longControlState == LongCtrlState.off
    force_slow_decel = sm['controlsState'].forceDecel

    # Reset current state when not engaged, or user is controlling the speed
    reset_state = long_control_off if self.CP.openpilotLongitudinalControl else not sm['controlsState'].enabled
    # PCM cruise speed may be updated a few cycles later, check if initialized
    reset_state = reset_state or not v_cruise_initialized

    # No change cost when user is controlling the speed, or when standstill
    prev_accel_constraint = not (reset_state or sm['carState'].standstill)

    if mode == 'acc':
      accel_clip = [sm['frogpilotPlan'].minAcceleration, sm['frogpilotPlan'].maxAcceleration]
      steer_angle_without_offset = sm['carState'].steeringAngleDeg - sm['liveParameters'].angleOffsetDeg
      if not sm['frogpilotPlan'].cscControllingSpeed:
        accel_clip = limit_accel_in_turns(v_ego, steer_angle_without_offset, accel_clip, self.CP)
    else:
      accel_clip = [ACCEL_MIN, ACCEL_MAX]

    if reset_state:
      self.v_desired_filter.x = v_ego
      # Clip aEgo to cruise limits to prevent large accelerations when becoming active
      self.a_desired = np.clip(sm['carState'].aEgo, accel_clip[0], accel_clip[1])

    # Prevent divergence, smooth in current v_ego
    self.v_desired_filter.x = max(0.0, self.v_desired_filter.update(v_ego))
    x, v, a, j, throttle_prob = self.parse_model(sm['modelV2'], v_ego, frogpilot_toggles.taco_tune)
    # Don't clip at low speeds since throttle_prob doesn't account for creep
    raw_allow_throttle = throttle_prob > ALLOW_THROTTLE_THRESHOLD or v_ego <= MIN_ALLOW_THROTTLE_SPEED
    clipped_accel_coast = max(accel_coast, accel_clip[0])
    coast_limit = np.interp(v_ego, [MIN_ALLOW_THROTTLE_SPEED, MIN_ALLOW_THROTTLE_SPEED*2], [accel_clip[1], clipped_accel_coast])
    if self.ap1_throttle_gate is not None:
      # AP1: 0..1 cut fraction (tesla/throttle_gate.py) instead of a 0/1 step.
      throttle_cut = self.ap1_throttle_gate.update(raw_allow_throttle, low_speed=v_ego <= MIN_ALLOW_THROTTLE_SPEED, reset=reset_state)
      self.allow_throttle = throttle_cut < 0.5
    else:
      throttle_cut = 0.0 if raw_allow_throttle else 1.0
      self.allow_throttle = raw_allow_throttle
    accel_clip[1] = apply_cut(accel_clip[1], coast_limit, throttle_cut)
    self.throttle_cut = throttle_cut

    if force_slow_decel:
      v_cruise = 0.0

    self.mpc.set_weights(sm['frogpilotPlan'].accelerationJerk, sm['frogpilotPlan'].dangerJerk, sm['frogpilotPlan'].speedJerk, prev_accel_constraint, personality=sm['controlsState'].personality)
    self.mpc.set_cur_state(self.v_desired_filter.x, self.a_desired)
    # AP1-only: lower lead comfort brake (~regen-sized) via runtime x_obstacle
    # adjustment. Non-AP1 and toggle-off keep stock COMFORT_BRAKE. a_min unchanged.
    comfort_brake = ap1_comfort_brake(self.CP.carFingerprint)
    self.mpc.update(v_cruise, sm['modelV2'], sm['radarState'], x, v, a, j, sm['frogpilotPlan'].tFollow, accel_clip[0], accel_clip[1], frogpilot_toggles, sm['frogpilotCarState'].trafficModeEnabled, personality=sm['controlsState'].personality, comfort_brake=comfort_brake)

    self.a_desired_trajectory_full = np.interp(CONTROL_N_T_IDX, T_IDXS_MPC, self.mpc.a_solution)
    self.v_desired_trajectory = np.interp(CONTROL_N_T_IDX, T_IDXS_MPC, self.mpc.v_solution)
    self.a_desired_trajectory = np.interp(CONTROL_N_T_IDX, T_IDXS_MPC, self.mpc.a_solution)
    self.j_desired_trajectory = np.interp(CONTROL_N_T_IDX, T_IDXS_MPC[:-1], self.mpc.j_solution)

    # TODO counter is only needed because radar is glitchy, remove once radar is gone
    self.fcw, self.mpc.crash_cnt = planner_fcw(
      self.mpc.crash_cnt, sm['carState'].standstill, reset_state)
    if self.fcw:
      cloudlog.info("FCW triggered")

    # Interpolate 0.05 seconds and save as starting point for next iteration
    a_prev = self.a_desired
    self.a_desired = float(np.interp(self.dt, CONTROL_N_T_IDX, self.a_desired_trajectory))
    self.v_desired_filter.x = self.v_desired_filter.x + self.dt * (self.a_desired + a_prev) / 2.0

    action_t = frogpilot_toggles.longitudinalActuatorDelay + DT_MDL
    output_a_target_mpc, output_should_stop_mpc = get_accel_from_plan(self.v_desired_trajectory, self.a_desired_trajectory, CONTROL_N_T_IDX,
                                                                        action_t=action_t, vEgoStopping=frogpilot_toggles.vEgoStopping)
    output_a_target_e2e = sm['modelV2'].action.desiredAcceleration
    output_should_stop_e2e = sm['modelV2'].action.shouldStop

    if mode == 'acc':
      output_a_target = output_a_target_mpc
      self.output_should_stop = output_should_stop_mpc
    else:
      output_a_target = min(output_a_target_mpc, output_a_target_e2e)
      self.output_should_stop = output_should_stop_e2e or output_should_stop_mpc

    if self.ap1_engage_brake:
      # FrogPilot publishes minAcceleration = 0 while disengaged, so the 0.05/step slew below held
      # braking to ~1 m/s^2 per second after engaging behind a closing lead (route 3c seg 4, FCW).
      # AP1: during that engage ramp, if the MPC is following a lead, the braking limit goes to its
      # normal value at once. No lead (cruise / curve slowdown) and the max-accel side keep the ramp.
      if reset_state:
        self.ap1_engage_ramp = True
      elif self.ap1_engage_ramp:
        if self.mpc.source in ('lead0', 'lead1'):
          self.prev_accel_clip[0] = min(self.prev_accel_clip[0], accel_clip[0] + 0.05)
        # Ends once the braking limit has reached frogpilotPlan's (non-zero) value.
        if accel_clip[0] < 0.0 and self.prev_accel_clip[0] <= accel_clip[0] + 0.05:
          self.ap1_engage_ramp = False
    if self.ap1_regen_aware:
      output_a_target = self.ap1_regen_shape(sm, output_a_target, mode, reset_state, frogpilot_toggles)
    for idx in range(2):
      accel_clip[idx] = np.clip(accel_clip[idx], self.prev_accel_clip[idx] - 0.05, self.prev_accel_clip[idx] + 0.05)
    self.output_a_target = np.clip(output_a_target, accel_clip[0], accel_clip[1])
    self.prev_accel_clip = accel_clip

  def ap1_regen_shape(self, sm, a_plan, mode, reset_state, frogpilot_toggles):
    """Earlier gentle regen-only onset for known stops. Returns min(a_plan, early)."""
    fcs = sm['frogpilotCarState']
    v_ego = sm['carState'].vEgo
    amb = getattr(fcs, 'ap1AmbientTempC', float('nan'))
    amb = None if amb != amb else amb
    self.regen_est.update(v_ego, sm['carControl'].actuators.accel, getattr(fcs, 'ap1MotorTorqueNm', 0.0),
                          getattr(fcs, 'ap1MotorRpm', 0.0), amb, self.dt)
    if reset_state or sm['carState'].gasPressed:
      self.regen_shaper.reset()
      self.regen_a_early = 0.0
      return a_plan
    stop_gap = STOP_DISTANCE + float(getattr(frogpilot_toggles, 'increase_stopped_distance', 0.0) or 0.0)
    lead = sm['radarState'].leadOne
    d_stop = lead_stop_distance(lead.status, lead.dRel, lead.vLead, stop_gap)
    if mode == 'blended':
      # Model stops (red lights / stop signs) only where the e2e plan acts on them.
      m = sm['modelV2']
      d_model = model_stop_distance(list(m.position.x), list(m.velocity.x))
      if d_model is not None:
        d_stop = d_model if d_stop is None else min(d_stop, d_model)
    self.regen_stop_dist = -1.0 if d_stop is None else d_stop
    a_out, self.regen_a_early = self.regen_shaper.update(v_ego, a_plan, d_stop, self.regen_est.p_kw, self.dt)
    return a_out

  def publish(self, sm, pm):
    plan_send = messaging.new_message('longitudinalPlan')

    plan_send.valid = sm.all_checks(service_list=['carState', 'controlsState'])

    longitudinalPlan = plan_send.longitudinalPlan
    longitudinalPlan.modelMonoTime = sm.logMonoTime['modelV2']
    longitudinalPlan.processingDelay = (plan_send.logMonoTime / 1e9) - sm.logMonoTime['modelV2']
    longitudinalPlan.solverExecutionTime = self.mpc.solve_time

    longitudinalPlan.speeds = self.v_desired_trajectory.tolist()
    longitudinalPlan.accels = self.a_desired_trajectory.tolist()
    longitudinalPlan.jerks = self.j_desired_trajectory.tolist()

    longitudinalPlan.hasLead = self.mpc.status
    longitudinalPlan.longitudinalPlanSource = self.mpc.source
    longitudinalPlan.fcw = self.fcw

    longitudinalPlan.aTarget = float(self.output_a_target)
    longitudinalPlan.shouldStop = bool(self.output_should_stop)
    longitudinalPlan.allowBrake = True
    longitudinalPlan.allowThrottle = bool(self.allow_throttle)

    pm.send('longitudinalPlan', plan_send)
