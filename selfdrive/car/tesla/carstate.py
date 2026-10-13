import copy
from collections import deque
from cereal import car, custom
from openpilot.common.conversions import Conversions as CV
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.car.tesla.blindspot import Ap1Blindspot, frames_from_vl_all
from openpilot.selfdrive.car.tesla.cluster import CLUSTER_ADDRS, COUNTER_SIGNALS, MSG_NAMES
from openpilot.selfdrive.car.tesla.hso import ap1_driver_input
from openpilot.selfdrive.car.tesla.stalk_follow import dtr_sample, follow_seconds, parse_stalk_raw
from openpilot.selfdrive.car.tesla.stalk_pull_hold import PULL_HOLD_ENABLED, StalkPullHold
from openpilot.selfdrive.car.tesla.stalk_fwd_hold import FWD_HOLD_ENABLED, StalkFwdHold
from openpilot.selfdrive.car.tesla.speed_limit import dashboard_speed_limit_ms
from openpilot.selfdrive.car.tesla.steer_fault import steer_fault_temporary
from openpilot.selfdrive.car.tesla.values import CAR, DBC, CANBUS, GEAR_MAP, DOORS, BUTTONS
from openpilot.selfdrive.car.interfaces import CarStateBase
from opendbc.can.parser import CANParser
from opendbc.can.can_define import CANDefine

class CarState(CarStateBase):
  def __init__(self, CP, FPCP):
    super().__init__(CP, FPCP)
    self.button_states = {button.event_type: False for button in BUTTONS}
    self.can_define = CANDefine(DBC[CP.carFingerprint]['chassis'])

    # Needed by carcontroller
    self.msg_stw_actn_req = None
    self.hands_on_level = 0
    self.steer_warning = None
    self.eac_status = None
    self.eac_fault = False
    self.acc_state = 0
    self.das_control_counters = deque(maxlen=32)
    # DTR_Dist_Rq decision. Python only; cereal was not extended.
    self.spd_ctrl_lvr = 0
    self.stalk_follow = None
    # AP1 long RWD pull → Experimental Mode (see stalk_pull_hold.py).
    self.stalk_pull_hold = StalkPullHold()
    self.stalk_pull_toggle = False
    # AP1 long FWD hold while disengaged → Experimental Mode (stalk_fwd_hold.py).
    self.stalk_fwd_hold = StalkFwdHold()
    self.stalk_fwd_toggle = False
    # AP1 only. {addr: decoded values} for stock cluster frames (bus 2) that
    # arrived this step. Read by CarController for the cluster frames.
    self.cluster_stock = {}
    # AP1 only. Stock DAS_steeringControlCounter values (bus 2) received this
    # step, oldest first. CarController follows them (steer_counter.py).
    self.stock_steer_counters = []
    # AP1 only. Last EPB_epasEACAllow seen. None until the first 0x214 frame
    # (vl defaults to 0 before that, which would read as revoked).
    self.epb_eac_allow = None
    self.epb_eac_revoked = False
    # AP1 only. Stock 0x399 blind-spot fields with a per-side hold (blindspot.py).
    self.ap1_bsm = Ap1Blindspot()

  def update(self, cp, cp_cam, frogpilot_toggles):
    ret = car.CarState.new_message()
    fp_ret = custom.FrogPilotCarState.new_message()

    # Vehicle speed
    ret.vEgoRaw = cp.vl["ESP_B"]["ESP_vehicleSpeed"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = (ret.vEgo < 0.1)

    # Gas pedal
    ret.gas = cp.vl["DI_torque1"]["DI_pedalPos"] / 100.0
    ret.gasPressed = (ret.gas > 0)

    # Brake pedal
    ret.brake = 0
    ret.brakePressed = bool(cp.vl["BrakeMessage"]["driverBrakeStatus"] != 1)

    # Steering wheel
    epas_status = cp_cam.vl["EPAS3P_sysStatus"] if self.CP.carFingerprint == CAR.TESLA_MODELS_RAVEN else cp.vl["EPAS_sysStatus"]

    self.hands_on_level = epas_status["EPAS_handsOnLevel"]
    self.steer_warning = self.can_define.dv["EPAS_sysStatus"]["EPAS_eacErrorCode"].get(int(epas_status["EPAS_eacErrorCode"]), None)
    steer_status = self.can_define.dv["EPAS_sysStatus"]["EPAS_eacStatus"].get(int(epas_status["EPAS_eacStatus"]), None)
    self.eac_status = steer_status

    ret.steeringAngleDeg = -epas_status["EPAS_internalSAS"]
    ret.steeringRateDeg = -cp.vl["STW_ANGLHP_STAT"]["StW_AnglHP_Spd"] # This is from a different angle sensor, and at different rate
    ret.steeringTorque = -epas_status["EPAS_torsionBarTorque"]
    # Any non-zero hands level is driver input (override, grey border), as in
    # frog_ap1. AP1 EPAS reports 0, 1, 3. The AP1 lateral pause stays at
    # TinklaHandsOnLevel 2 in CarController (hso.ap1_steering_pressed).
    ap1 = self.CP.carFingerprint == CAR.TESLA_AP1_MODELS
    if ap1:
      ret.steeringPressed = ap1_driver_input(self.hands_on_level)
    else:
      ret.steeringPressed = (self.hands_on_level > 0)
    self.eac_fault = steer_status == "EAC_FAULT"
    ret.steerFaultPermanent = self.eac_fault
    if ap1:
      # EPB_epasEACAllow 0: the EPB revoked EAC. EPAS stays INHIBITED until a
      # car power cycle (the comma reboot does not clear it). Report it as a
      # permanent steer fault ("LKAS Fault: Restart the Car") instead of only
      # the silent steer-unavailable warning.
      epb = cp.vl_all.get("EPB_epasControl", {}).get("EPB_epasEACAllow", [])
      if len(epb):
        self.epb_eac_allow = int(epb[-1])
      self.epb_eac_revoked = self.epb_eac_allow == 0
      ret.steerFaultPermanent = ret.steerFaultPermanent or self.epb_eac_revoked
    # AP1: idle, code 6, and latched HANDS_ON (3) are not temporary faults.
    # Both codes stay set after hands return below 2, so they must not keep
    # latActive false. The hands pause is hands_on_level >= 2, not this flag.
    # Other non-idle names still warn. Model 3/Y still faults on code 6 and
    # still does not fault on HANDS_ON.
    ret.steerFaultTemporary = steer_fault_temporary(self.steer_warning, ap1)

    # Cruise state
    cruise_state = self.can_define.dv["DI_state"]["DI_cruiseState"].get(int(cp.vl["DI_state"]["DI_cruiseState"]), None)
    speed_units = self.can_define.dv["DI_state"]["DI_speedUnits"].get(int(cp.vl["DI_state"]["DI_speedUnits"]), None)

    acc_enabled = (cruise_state in ("ENABLED", "STANDSTILL", "OVERRIDE", "PRE_FAULT", "PRE_CANCEL"))

    ret.cruiseState.enabled = acc_enabled
    # Tinkla-aligned: cruise set is DI_cruiseSet (ACC set), not DI_digitalSpeed
    # (≈ ego). With pcmCruise, VCruiseHelper copies this into controlsState.vCruise
    # so openpilot's set tracks the stalk set. Display/ego stay on ESP vEgo.
    if speed_units == "KPH":
      ret.cruiseState.speed = cp.vl["DI_state"]["DI_cruiseSet"] * CV.KPH_TO_MS
    elif speed_units == "MPH":
      ret.cruiseState.speed = cp.vl["DI_state"]["DI_cruiseSet"] * CV.MPH_TO_MS
    ret.cruiseState.available = ((cruise_state == "STANDBY") or ret.cruiseState.enabled)
    ret.cruiseState.standstill = False # This needs to be false, since we can resume from stop without sending anything special

    # Gear
    ret.gearShifter = GEAR_MAP[self.can_define.dv["DI_torque2"]["DI_gear"].get(int(cp.vl["DI_torque2"]["DI_gear"]), "DI_GEAR_INVALID")]

    # Buttons
    buttonEvents = []
    for button in BUTTONS:
      state = (cp.vl[button.can_addr][button.can_msg] in button.values)
      if self.button_states[button.event_type] != state:
        event = car.CarState.ButtonEvent.new_message()
        event.type = button.event_type
        event.pressed = state
        buttonEvents.append(event)
      self.button_states[button.event_type] = state
    ret.buttonEvents = buttonEvents

    # Doors
    ret.doorOpen = any((self.can_define.dv["GTW_carState"][door].get(int(cp.vl["GTW_carState"][door]), "OPEN") == "OPEN") for door in DOORS)

    # Blinkers
    ret.leftBlinker = (cp.vl["GTW_carState"]["BC_indicatorLStatus"] == 1)
    ret.rightBlinker = (cp.vl["GTW_carState"]["BC_indicatorRStatus"] == 1)

    # Seatbelt
    if self.CP.carFingerprint == CAR.TESLA_MODELS_RAVEN:
      ret.seatbeltUnlatched = (cp.vl["DriverSeat"]["buckleStatus"] != 1)
    else:
      ret.seatbeltUnlatched = (cp.vl["SDM1"]["SDM_bcklDrivStatus"] != 1)

    # Blindspot. AP1 only: stock DAS_blindSpotRearLeft/Right from 0x399 on
    # cp_cam (1/2 = warning, 3 = SNA ignored, bad checksum skipped), held
    # BSM_HOLD_S per side because 0x399 is ~2.4 Hz. Other Teslas: no BSM.
    if ap1:
      ret.leftBlindspot, ret.rightBlindspot = self.ap1_bsm.update(frames_from_vl_all(cp_cam), DT_CTRL)

    # AEB
    ret.stockAeb = (cp_cam.vl["DAS_control"]["DAS_aebEvent"] == 1)

    # AP1 tip detent: raw SpdCtrlLvr_Stat (UP_2ND=4 / DN_2ND=8 vs 1ST).
    try:
      self.spd_ctrl_lvr = int(cp.vl["STW_ACTN_RQ"].get("SpdCtrlLvr_Stat") or 0)
    except Exception:
      self.spd_ctrl_lvr = 0

    # Stalk follow detent. STW_ACTN_RQ is already subscribed. No cereal field.
    # A zero timestamp is the parser default, not ACC_DIST_1.
    stw = cp.vl.get("STW_ACTN_RQ")
    ts_map = getattr(cp, "ts_nanos", {}).get("STW_ACTN_RQ", {})
    if not isinstance(stw, dict) or not isinstance(ts_map, dict):
      raw = None
    else:
      raw = dtr_sample(stw.get("DTR_Dist_Rq"), ts_map.get("DTR_Dist_Rq", 0))
    self.stalk_follow = parse_stalk_raw(raw, self.stalk_follow)
    # Existing cruiseState.speedOffset is unused on Tesla. AP1 publishes the
    # stalk follow seconds there so FrogPilotFollowing can set tFollow.
    # 0 means no ready detent. 255 holds the last seconds via parse_stalk_raw.
    if self.CP.carFingerprint == CAR.TESLA_AP1_MODELS:
      ret.cruiseState.speedOffset = follow_seconds(self.stalk_follow)

    # AP1: long RWD pull while already engaged → one Experimental Mode toggle.
    # Uses prior cruise enabled so the engage pull itself does not fire.
    # DI may bump set speed mid-hold (resume); accepted — see stalk_pull_hold.py.
    # AP1: long FWD hold while already disengaged → one Experimental Mode toggle.
    # Cancel-while-engaged is disarmed; engage mid-hold aborts (stalk_fwd_hold.py).
    self.stalk_pull_toggle = False
    self.stalk_fwd_toggle = False
    if self.CP.carFingerprint == CAR.TESLA_AP1_MODELS:
      prev = getattr(self, "out", None)
      was_engaged = bool(prev.cruiseState.enabled) if prev is not None else False
      spd = cp.vl["STW_ACTN_RQ"].get("SpdCtrlLvr_Stat")
      if PULL_HOLD_ENABLED:
        self.stalk_pull_toggle = self.stalk_pull_hold.update(spd, was_engaged, DT_CTRL)
      if FWD_HOLD_ENABLED:
        self.stalk_fwd_toggle = self.stalk_fwd_hold.update(spd, was_engaged, DT_CTRL)

    # Messages needed by carcontroller
    self.msg_stw_actn_req = copy.copy(cp.vl["STW_ACTN_RQ"])
    self.acc_state = cp_cam.vl["DAS_control"]["DAS_accState"]
    self.das_control_counters.extend(cp_cam.vl_all["DAS_control"]["DAS_controlCounter"])
    self.cluster_stock = self.new_cluster_frames(cp_cam) if ap1 else {}
    if ap1:
      steer_ctrs = cp_cam.vl_all.get("DAS_steeringControl", {}).get("DAS_steeringControlCounter", [])
      self.stock_steer_counters = [int(c) for c in steer_ctrs]

    # AP1: Mobileye fused (stock 0x399 on cp_cam / bus 2) then GTW UI map/mpp
    # into FrogPilot dashboardSpeedLimit (m/s). Cluster TX is on bus 0 and
    # never reaches cp_cam. Freq-0 UI msgs on chassis; missing → 0.
    # AP1: stock Autopilot active (stock 0x399 on bus 2, state 3/4/5). Gates the
    # standstill-experiment standby DAS_control (Tinkla: not while stock AP).
    if ap1:
      st = cp_cam.vl.get("AutopilotStatus", {}).get("autopilotStatus", 0)
      self.stock_ap_active = 3 <= int(st or 0) <= 5
    if ap1:
      fused = cp_cam.vl.get("AutopilotStatus", {}).get("DAS_fusedSpeedLimit")
      ui_map = cp.vl.get("UI_driverAssistMapData", {}).get("UI_mapSpeedLimit")
      gps = cp.vl.get("UI_gpsVehicleSpeed", {})
      fp_ret.dashboardSpeedLimit = dashboard_speed_limit_ms(
        fused, ui_map, gps.get("UI_mppSpeedLimit"), gps.get("UI_mapSpeedLimitUnits"),
      )

    if ap1:
      # Regen-aware stop braking inputs (regen_aware.py).
      fp_ret.ap1MotorTorqueNm = float(cp.vl["DI_torque1"]["DI_torqueMotor"])
      fp_ret.ap1MotorRpm = float(cp.vl["DI_torque1"]["DI_motorRPM"])
      amb = cp.vl.get("GTW_ESP1", {})
      fp_ret.ap1AmbientTempC = float(amb["GTW_ambientTemperature"]) if getattr(cp, "ts_nanos", {}).get("GTW_ESP1", {}).get("GTW_ambientTemperature", 0) else float("nan")

    return ret, fp_ret

  @staticmethod
  def new_cluster_frames(cp_cam):
    """Stock AutopilotStatus / DAS_status2 / DAS_lanes received this step.

    A frame counts as new when its counter signal has a value in vl_all for
    this update. vl holds the newest decoded values of that frame.
    """
    out = {}
    for addr in CLUSTER_ADDRS:
      name = MSG_NAMES[addr]
      if len(cp_cam.vl_all[name].get(COUNTER_SIGNALS[addr], [])):
        out[addr] = dict(cp_cam.vl[name])
    return out

  @staticmethod
  def get_can_parser(CP, FPCP):
    messages = [
      # sig_address, frequency
      ("ESP_B", 50),
      ("DI_torque1", 100),
      ("DI_torque2", 100),
      ("STW_ANGLHP_STAT", 100),
      ("EPAS_sysStatus", 25),
      ("DI_state", 10),
      ("STW_ACTN_RQ", 10),
      ("GTW_carState", 10),
      ("BrakeMessage", 50),
    ]

    if CP.carFingerprint == CAR.TESLA_MODELS_RAVEN:
      messages.append(("DriverSeat", 20))
    else:
      messages.append(("SDM1", 10))

    if CP.carFingerprint == CAR.TESLA_AP1_MODELS:
      # GTW map / mpp speed limits for dashboardSpeedLimit. Frequency 0 so a
      # car without them does not trip canValid (same pattern as cluster RX).
      messages.append(("UI_driverAssistMapData", 0))
      messages.append(("UI_gpsVehicleSpeed", 0))
      # EPB EAC allow (0x214) for the EPB revoke fault. Frequency 0, same reason.
      messages.append(("EPB_epasControl", 0))
      # 0x208 outside air temperature for regen_aware.py. Frequency 0 (no canValid effect).
      messages.append(("GTW_ESP1", 0))

    return CANParser(DBC[CP.carFingerprint]['chassis'], messages, CANBUS.chassis)

  @staticmethod
  def get_cam_can_parser(CP, FPCP):
    messages = [
      # sig_address, frequency
      ("DAS_control", 40),
    ]

    if CP.carFingerprint == CAR.TESLA_MODELS_RAVEN:
      messages.append(("EPAS3P_sysStatus", 100))

    if CP.carFingerprint == CAR.TESLA_AP1_MODELS:
      # Stock cluster frames for the AP1 cluster substitution. Frequency 0:
      # the parser never marks them missing or timed out, so they cannot
      # change canValid. A car without them just gets no cluster frames.
      for addr in CLUSTER_ADDRS:
        messages.append((MSG_NAMES[addr], 0))
      # Stock 0x488 counter for handover continuity (steer_counter.py).
      # Frequency 0: missing stock frames fall back to the old cadence.
      messages.append(("DAS_steeringControl", 0))

    return CANParser(DBC[CP.carFingerprint]['chassis'], messages, CANBUS.autopilot_chassis)
