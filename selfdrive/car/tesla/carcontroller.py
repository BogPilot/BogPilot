from cereal import car
from opendbc.can.packer import CANPacker
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.car.interfaces import CarControllerBase
from openpilot.selfdrive.car.tesla.actuator_plan import (
  ap1_standby_allowed,
  AP1_ENGAGE_SOFT_START_FRAMES,
  DAS_CONTROL_POWERTRAIN,
  ap1_gas_neutral,
  ap1_should_send_hold_clear,
  build_actuator_plan,
  longitudinal_command_allowed,
)
from openpilot.selfdrive.car.tesla.cluster import (
  CLUSTER_BUS, ClusterController, HudInputs, path_from_model_v2,
)
from openpilot.selfdrive.car.tesla.hso import Ap1DriverYield, ap1_lat_active, ap1_steering_pressed
from openpilot.selfdrive.car.tesla.long_smooth import Ap1AccelSmoother, Ap1JerkLimit, ap1_brake_urgent, ap1_fcw
from openpilot.selfdrive.car.tesla.steer_counter import Ap1SteerCounterSync
from openpilot.selfdrive.car.tesla.teslacan import TeslaCAN
from openpilot.selfdrive.car.tesla.slc_raise import cruise_set_mph
from openpilot.selfdrive.car.tesla.toggles import enable_ic_integration
from panda import Panda
from openpilot.selfdrive.car.tesla.values import DBC, CANBUS, CAR

VisualAlert = car.CarControl.HUDControl.VisualAlert
AudibleAlert = car.CarControl.HUDControl.AudibleAlert


class CarController(CarControllerBase):
  def __init__(self, dbc_name, CP, VM):
    self.CP = CP
    self.frame = 0
    self.apply_angle_last = 0
    self.packer = CANPacker(dbc_name)
    self.pt_packer = CANPacker(DBC[CP.carFingerprint]['pt'])
    self.tesla_can = TeslaCAN(self.packer, self.pt_packer)
    # AP1 only. Tinkla-style cluster frames (cluster.py).
    self.cluster = ClusterController() if CP.carFingerprint == CAR.TESLA_AP1_MODELS else None
    # Lazy SubMaster for modelV2 path only (no lead / object). None until first
    # need; False after a permanent init failure (tests without messaging).
    self._model_sm = None
    # AP1 engage soft-start: hold measured ANGLE for ~300 ms after enable.
    self.ap1_prev_enabled = False
    self.ap1_engage_frame = None
    # AP1 hands pause + resume hold (hso.Ap1DriverYield). Read by the
    # interface for the steerOverride event so the border stays grey.
    self.ap1_yield = Ap1DriverYield()
    # AP1 0x488 phase and counter follow the stock DAS (steer_counter.py).
    self.ap1_steer_sync = Ap1SteerCounterSync() if CP.carFingerprint == CAR.TESLA_AP1_MODELS else None
    # AP1 accel slew (long_smooth.py): drive release at 5 m/s^3, regen ramps in
    # at 2.0 m/s^3 (faster for deeper requests). Requests at or below -2.0 m/s^2,
    # FCW and the stopping state pass through on the same step.
    self.ap1_accel_smoother = Ap1AccelSmoother() if CP.carFingerprint == CAR.TESLA_AP1_MODELS else None
    # AP1 DAS_jerkMin/Max: +/-1.5 in the comfort band, full +/-8 once the request
    # sent is at or below -0.5 m/s^2, on urgent frames and below 1 m/s (long_smooth.Ap1JerkLimit).
    self.ap1_jerk_limit = Ap1JerkLimit() if CP.carFingerprint == CAR.TESLA_AP1_MODELS else None
    # AP1 standstill experiment: on only when the installed safety param has
    # TESLA_FLAG_AP1_STANDSTILL (interface sets it from AP1StandstillEngage).
    configs = list(getattr(CP, "safetyConfigs", []) or [])
    self.ap1_standstill = (CP.carFingerprint == CAR.TESLA_AP1_MODELS and len(configs) > 0 and
                           bool(int(getattr(configs[0], "safetyParam", 0)) & Panda.FLAG_TESLA_AP1_STANDSTILL))

  def update(self, CC, CS, now_nanos, frogpilot_toggles):
    actuators = CC.actuators

    # Model 3/Y still cancels cruise when HANDS_ON and hands_on_level >= 3.
    # AP1 does not. Tinkla HSO pauses lateral and leaves long engaged.
    ap1 = self.CP.carFingerprint == CAR.TESLA_AP1_MODELS
    hands_on_fault = (not ap1) and CS.steer_warning == "EAC_ERROR_HANDS_ON" and CS.hands_on_level >= 3
    # AP1 longitudinal is chassis 0x2b9. Do not also plan powertrain 0x2bf.
    chassis_das_only = ap1
    soft_start = False
    driver_yield = False
    if ap1:
      enabled = bool(CC.enabled)
      # Hands >= 2 starts it; level 0 for AP1_RESUME_HOLD_S ends it. Only
      # while enabled, so it never holds off disengage or a new engage.
      driver_yield = self.ap1_yield.update(enabled, CS.hands_on_level)
      if enabled and not self.ap1_prev_enabled:
        self.ap1_engage_frame = self.frame
      if self.ap1_yield.resumed:
        # Resume from the measured wheel through the engage soft-start.
        self.ap1_engage_frame = self.frame
      if not enabled:
        self.ap1_engage_frame = None
      self.ap1_prev_enabled = enabled
      if self.ap1_engage_frame is not None:
        soft_start = (self.frame - self.ap1_engage_frame) < AP1_ENGAGE_SOFT_START_FRAMES
    if ap1:
      lat_active = ap1_lat_active(CC.latActive, CS.hands_on_level) and not driver_yield
    else:
      lat_active = CC.latActive
    accel = actuators.accel
    jerk_limits = {}
    if self.ap1_accel_smoother is not None:
      gas_pressed = bool(getattr(CS.out, "gasPressed", False))
      long_allowed_now = longitudinal_command_allowed(self.CP.openpilotLongitudinalControl, CC.enabled, CC.longActive)
      gas_neutral = ap1_gas_neutral(chassis_das_only, self.CP.openpilotLongitudinalControl, CC.enabled, gas_pressed)
      # FCW or the LongControl stopping state: braking passes through unramped.
      urgent = ap1_brake_urgent(CC)
      fcw = ap1_fcw(CC)
      # Standstill: relax a deeper hold request to AP1_STANDSTILL_HOLD_ACCEL (stock DAS
      # sends accelMin 0) so the DI latches less HOLD pressure to dump at the launch.
      accel = self.ap1_accel_smoother.update(actuators.accel, long_allowed_now, CS.out.vEgo, gas_neutral=gas_neutral,
                                             urgent=urgent, standstill=bool(getattr(CS.out, "standstill", False)), fcw=fcw,
                                             a_ego=getattr(CS.out, "aEgo", None))
      jerk_min, jerk_max = self.ap1_jerk_limit.update(accel, long_allowed_now, CS.out.vEgo, gas_neutral=gas_neutral,
                                                      urgent=urgent, fcw=fcw)
      jerk_limits = {"jerk_min": jerk_min, "jerk_max": jerk_max}
    steer_tick = None
    if self.ap1_steer_sync is not None:
      steer_tick = self.ap1_steer_sync.update(getattr(CS, "stock_steer_counters", ()), self.frame)
    plan = build_actuator_plan(
      self.frame,
      lat_active,
      hands_on_fault,
      self.CP.openpilotLongitudinalControl,
      CC.enabled,
      CC.longActive,
      CS.out.steeringAngleDeg,
      actuators.steeringAngleDeg,
      self.apply_angle_last,
      CS.out.vEgo,
      accel,
      CS.acc_state,
      CS.das_control_counters,
      CC.cruiseControl.cancel,
      chassis_das_only=chassis_das_only,
      # Code 6 is AP1-only. Model 3/Y still treats it as a temporary fault
      # and does not take the measured-angle ANGLE fallback.
      epas_error=CS.steer_warning if ap1 else None,
      eac_fault=bool(CS.eac_fault) if ap1 else False,
      hands_on_level=CS.hands_on_level if ap1 else 0,
      eac_status=CS.eac_status if ap1 else None,
      soft_start=soft_start,
      driver_yield=driver_yield,
      # AP1: neutral DAS_control while the driver presses the accelerator.
      gas_pressed=bool(getattr(CS.out, "gasPressed", False)) if ap1 else False,
      steer_tick=steer_tick,
      standby=ap1_standby_allowed(self.ap1_standstill, self.CP.openpilotLongitudinalControl, CC.enabled,
                                  CS.out.gearShifter == car.CarState.GearShifter.drive,
                                  bool(getattr(CS, "stock_ap_active", False))) if (ap1 and self.ap1_standstill) else False,
    )
    self.apply_angle_last = plan.apply_angle_last
    if plan.steer is not None and self.ap1_steer_sync is not None:
      self.ap1_steer_sync.commit(plan.steer.counter, self.frame)

    can_sends = []

    if plan.steer is not None:
      can_sends.append(self.tesla_can.create_steering_control(plan.steer.angle_deg, plan.steer.enabled, plan.steer.counter))

    # Longitudinal control (in sync with stock message, about 40Hz)
    chassis_only = DAS_CONTROL_POWERTRAIN not in plan.longitudinal_addrs
    for cmd in plan.longitudinal:
      jl = jerk_limits if cmd.jerk_min is None else {"jerk_min": cmd.jerk_min, "jerk_max": cmd.jerk_max}
      can_sends.extend(self.tesla_can.create_longitudinal_commands(cmd.acc_state, cmd.target_speed, cmd.min_accel, cmd.max_accel, cmd.counter, chassis_only=chassis_only, **jl))

    # Cancel on user steering override, since there is no steering torque blending
    if plan.cancel:
      # Spam every possible counter value, otherwise it might not be accepted
      for counter in range(16):
        can_sends.append(self.tesla_can.create_action_request(CS.msg_stw_actn_req, True, CANBUS.chassis, counter))
        can_sends.append(self.tesla_can.create_action_request(CS.msg_stw_actn_req, True, CANBUS.autopilot_chassis, counter))

    # AP1 Hold clear. Tinkla HUD_module zeros DAS_gas_to_resume (bit 1 of
    # 0x349 byte 0) and sends the frame. Not sent unless longitudinal is allowed.
    long_allowed = longitudinal_command_allowed(
      self.CP.openpilotLongitudinalControl, CC.enabled, CC.longActive)
    if ap1_should_send_hold_clear(chassis_das_only, long_allowed, CS.acc_state, self.frame):
      can_sends.append(self.tesla_can.create_ap1_hold_clear())

    # AP1 cluster frames. Display only. Added after every actuator frame and
    # never read by the actuator plan above.
    if self.cluster is not None:
      can_sends.extend(self.cluster_frames(CC, CS, now_nanos))

    new_actuators = actuators.as_builder()
    new_actuators.steeringAngleDeg = self.apply_angle_last
    if self.ap1_accel_smoother is not None:
      new_actuators.accel = float(accel)

    self.frame += 1
    return new_actuators, can_sends

  def _model_path_for_cluster(self, enabled, ic_on):
    """Subscribe to modelV2 only while toggle on; use path only while engaged.

    Stale/absent modelV2 returns None so build_das_lanes falls back to
    actuator curvature + 50 m. Substitution itself is not stopped.
    """
    if not ic_on:
      return None
    if self._model_sm is False:
      return None
    try:
      if self._model_sm is None:
        import cereal.messaging as messaging
        self._model_sm = messaging.SubMaster(['modelV2'])
      self._model_sm.update(0)
      if not enabled:
        return None
      # Use the latest value even when this step did not receive a new frame.
      if not self._model_sm.seen['modelV2']:
        return None
      return path_from_model_v2(self._model_sm['modelV2'])
    except Exception:
      # Messaging unavailable (unit tests) or a bad model message: fallback.
      if self._model_sm is None:
        self._model_sm = False
      return None

  def cluster_frames(self, CC, CS, now_nanos):
    """Cluster frames for this step. A failure here turns them off for the
    rest of the drive (stock frames then flow through the panda). It must not
    stop the steering and longitudinal frames already built."""
    try:
      ic_on = enable_ic_integration(self.CP.carFingerprint)
      enabled = bool(CC.enabled)
      model_path = self._model_path_for_cluster(enabled, ic_on)
      hud = CC.hudControl
      h = HudInputs(
        enabled=enabled,
        fcw=hud.visualAlert == VisualAlert.fcw,
        steer_required=hud.visualAlert == VisualAlert.steerRequired,
        audible=hud.audibleAlert != AudibleAlert.none,
        human_steering=ap1_steering_pressed(CS.hands_on_level),
        left_lane_depart=bool(hud.leftLaneDepart),
        right_lane_depart=bool(hud.rightLaneDepart),
        left_blinker=bool(CC.leftBlinker),
        right_blinker=bool(CC.rightBlinker),
        curvature=float(CC.actuators.curvature),
        ic_integration=ic_on,
        model_path=model_path,
        # hudControl.setSpeed is the OP cruise target (m/s); may already include
        # the AP1 SLC raise via controlsd's cluster_display_kph lift. Packed
        # with DBC factor 0.4 so IC dig matches (not 2× from legacy 0.2).
        cruise_set_mph=cruise_set_mph(float(hud.setSpeed)) if enabled else None,
      )
      frames = self.cluster.update(h, CS.cluster_stock, now_nanos)
      return [[addr, 0, dat, CLUSTER_BUS] for addr, dat in frames]
    except Exception:
      cloudlog.exception("tesla ap1 cluster frames disabled")
      self.cluster = None
      return []
