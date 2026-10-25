"""Pre-AP Model S car state (CAR.TESLA_PREAP_MODELS only).

MIT License. Ported from NotAutopilot (github.com/NotAutopilot/openpilot,
opendbc car/tesla/preap/carstate.py and engagement.py at opendbc cc39c7f),
which builds on Tinkla / BogGyver Pre-AP work. Copyright (c) NotAutopilot
contributors, BogGyver, and the openpilot authors.

Pre-AP has no AP ECU, so there is no DAS_control cruise state to follow. The
engagement latch here mirrors panda/board/safety/safety_tesla_preap.h so host
and panda agree on when lateral control is allowed:

  engage:    STW_ACTN_RQ SpdCtrlLvr_Stat rising edge to 2 (RWD pull) with
             gear D, all doors closed, no brake, no steering disengage
  disengage: brake, hands-on level >= 2 or EPAS rejecting (codes 6-9 with
             eacStatus 0), gear not D, any door open, gas rising edge (unless
             DISABLE_DISENGAGE_ON_GAS), or a FWD push (1) more than 600 ms
             after the engage pull (NAP CANCEL_ECHO_WINDOW_MS)

Pedal longitudinal (only when the panda param has ENABLE_PEDAL, see
preap_pedal.py): gas comes from the Comma Pedal GAS_SENSOR (0x552,
tesla_preap_pedal.dbc BO_ 1362) like the panda, because DI_pedalPos then shows
the pedal output, not the driver's foot. cruiseState.enabled reports stock cruise
(DI_cruiseState, tesla_can.dbc VAL_ 872) so controlsd asks for a cancel; the
latch goes to cs.preap_latch and interface.py turns it into buttonEnable /
buttonCancel. Lever up/down become accelCruise/decelCruise for the set speed.

Differences from NAP: no double pull, no VirtualDAS, and brakePressed is
reported (NAP suppresses it to keep lateral on).

All addresses are bus 0 (CANBUS.chassis) in opendbc/tesla_can.dbc:
0x155 ESP_B, 0x108 DI_torque1, 0x118 DI_torque2, 0x20A BrakeMessage,
0x370 EPAS_sysStatus, 0x00E STW_ANGLHP_STAT, 0x368 DI_state, 0x45 STW_ACTN_RQ,
0x318 GTW_carState. The same set NAP's Pre-AP parser reads.
"""

from cereal import car
from openpilot.common.conversions import Conversions as CV
from openpilot.common.realtime import DT_CTRL
from panda import ALTERNATIVE_EXPERIENCE as PANDA_ALT_EXP
from openpilot.selfdrive.car.tesla.preap_pedal import lever_buttons, stock_cc_active, stock_cc_cancelable

ButtonType = car.CarState.ButtonEvent.Type

GearShifter = car.CarState.GearShifter

HANDS_ON_DISENGAGE_LEVEL = 2     # NAP HANDS_ON_DISENGAGE_LEVEL, panda PREAP_HANDS_ON_DISENGAGE_LEVEL
CANCEL_ECHO_WINDOW_S = 0.6       # NAP CANCEL_ECHO_WINDOW_MS, panda PREAP_CANCEL_ECHO_WINDOW_US
STEER_THRESHOLD = 1              # NAP values.STEER_THRESHOLD (Nm, torsion bar)
LEVER_FWD = 1                    # tesla_can.dbc VAL_ 69 SpdCtrlLvr_Stat
LEVER_RWD = 2
GEAR_D_RAW = 4                   # tesla_can.dbc VAL_ 280 DI_gear
DOOR_OPEN_RAW = 1                # tesla_can.dbc VAL_ 792 DOOR_STATE_* / BOOT_STATE
EPAS_REJECT_CODES = (6, 7, 8, 9)  # EAC_ERROR_HIGH_ANGLE_REQ .. _RATE_SAFETY (NAP)
# tesla_can.dbc VAL_ 280 DI_gear, VAL_ 880 EPAS_eacStatus, VAL_ 872 DI_speedUnits.
# Raw values so this module needs no CANDefine (testable without the parser).
GEAR_RAW_MAP = {1: GearShifter.park, 2: GearShifter.reverse, 3: GearShifter.neutral, 4: GearShifter.drive}
EAC_STATUS_NAMES = {0: "EAC_INHIBITED", 1: "EAC_AVAILABLE", 2: "EAC_ACTIVE", 3: "EAC_FAULT", 4: "SNA"}
SPEED_UNITS_KPH = 1
DOORS = ("DOOR_STATE_FL", "DOOR_STATE_FR", "DOOR_STATE_RL", "DOOR_STATE_RR", "DOOR_STATE_FrontTrunk", "BOOT_STATE")

PARSER_MESSAGES = [
  # Frequency 0 for all, same as NAP: the Pre-AP harness timing varies by
  # year, so a missing frame must not flip canValid. The panda RX checks
  # still enforce frequency on the safety-relevant frames.
  ("ESP_B", 0),
  ("DI_torque1", 0),
  ("DI_torque2", 0),
  ("BrakeMessage", 0),
  ("EPAS_sysStatus", 0),
  ("STW_ANGLHP_STAT", 0),
  ("DI_state", 0),
  ("STW_ACTN_RQ", 0),
  ("GTW_carState", 0),
]


def epas_rejecting(eac_status: int, eac_error_code: int) -> bool:
  return int(eac_status) == 0 and int(eac_error_code) in EPAS_REJECT_CODES


class PreapEngagement:
  """Host copy of the panda Pre-AP engagement latch. Pure, no CAN."""

  def __init__(self):
    self.enabled = False
    self.t = 0.0
    self.engage_t = None
    self.lever_pull_prev = False
    self.gas_prev = False
    self.steer_dis_prev = False

  def update(self, dt, lever, gear_raw, doors_open, brake, gas, steering_disengage, disengage_on_gas=True):
    self.t += dt
    if steering_disengage and not self.steer_dis_prev:
      self.enabled = False
    self.steer_dis_prev = steering_disengage
    if gear_raw != GEAR_D_RAW or doors_open or brake:
      self.enabled = False
    if disengage_on_gas and gas and not self.gas_prev:
      self.enabled = False
    self.gas_prev = gas

    pull = lever == LEVER_RWD
    if pull and not self.lever_pull_prev:
      if gear_raw == GEAR_D_RAW and not doors_open and not brake and not steering_disengage:
        self.enabled = True
        self.engage_t = self.t
    elif lever == LEVER_FWD:
      if self.engage_t is None or (self.t - self.engage_t) > CANCEL_ECHO_WINDOW_S:
        self.enabled = False
    self.lever_pull_prev = pull
    return self.enabled


def update_preap(cs, cp):
  """Fill a CarState for Pre-AP. cs is the tesla CarState instance."""
  ret = car.CarState.new_message()

  ret.vEgoRaw = cp.vl["ESP_B"]["ESP_vehicleSpeed"] * CV.KPH_TO_MS
  ret.vEgo, ret.aEgo = cs.update_speed_kf(ret.vEgoRaw)
  ret.standstill = ret.vEgo < 0.1

  pedal = getattr(cs, "pedal_sensor", None)
  if pedal is not None:
    # Same rule as the panda with ENABLE_PEDAL (interceptor > PREAP_PEDAL_GAS_PRESSED).
    cp_pedal = getattr(cs, "cp_pedal", None)
    pedal.update(dict(cp_pedal.vl["GAS_SENSOR"]) if cp_pedal is not None else {}, DT_CTRL)
    ret.gas = cp.vl["DI_torque1"]["DI_pedalPos"] / 100.0
    ret.gasPressed = pedal.gas_pressed
    ret.accFaulted = pedal.faulted
    cs.motor_torque = float(cp.vl["DI_torque1"]["DI_torqueMotor"])
  else:
    # Same rule as the panda (DI_pedalPos byte != 0) so the two cannot disagree.
    ret.gas = cp.vl["DI_torque1"]["DI_pedalPos"] / 100.0
    ret.gasPressed = ret.gas > 0

  di_brake = int(cp.vl["DI_torque2"]["DI_brakePedal"]) == 1
  msg_brake = int(cp.vl["BrakeMessage"]["driverBrakeStatus"]) == 2
  ret.brake = 0
  ret.brakePressed = di_brake or msg_brake

  epas = cp.vl["EPAS_sysStatus"]
  cs.hands_on_level = int(epas["EPAS_handsOnLevel"])
  eac_status = int(epas["EPAS_eacStatus"])
  eac_error = int(epas["EPAS_eacErrorCode"])
  cs.eac_status = EAC_STATUS_NAMES.get(eac_status)
  cs.steer_warning = eac_error
  ret.steeringAngleDeg = -epas["EPAS_internalSAS"]
  ret.steeringRateDeg = -cp.vl["STW_ANGLHP_STAT"]["StW_AnglHP_Spd"]
  ret.steeringTorque = -epas["EPAS_torsionBarTorque"]
  ret.steeringPressed = cs.update_steering_pressed(abs(ret.steeringTorque) > STEER_THRESHOLD, 5)
  cs.eac_fault = cs.eac_status == "EAC_FAULT"
  ret.steerFaultPermanent = cs.eac_fault
  # EAC_INHIBITED is the normal Pre-AP idle state (NAP); not a temporary fault.
  ret.steerFaultTemporary = False
  steering_disengage = cs.hands_on_level >= HANDS_ON_DISENGAGE_LEVEL or epas_rejecting(eac_status, eac_error)

  gear_raw = int(cp.vl["DI_torque2"]["DI_gear"])
  ret.gearShifter = GEAR_RAW_MAP.get(gear_raw, GearShifter.unknown)

  ret.doorOpen = any(int(cp.vl["GTW_carState"][d]) == DOOR_OPEN_RAW for d in DOORS)
  ret.leftBlinker = cp.vl["GTW_carState"]["BC_indicatorLStatus"] == 1
  ret.rightBlinker = cp.vl["GTW_carState"]["BC_indicatorRStatus"] == 1
  # SDM1 (0x201) is not read on Pre-AP (NAP: collides with Comma Pedal setups).
  ret.seatbeltUnlatched = False
  ret.stockAeb = False

  lever = int(cp.vl["STW_ACTN_RQ"]["SpdCtrlLvr_Stat"])
  disengage_on_gas = not (int(cs.CP.alternativeExperience) & PANDA_ALT_EXP.DISABLE_DISENGAGE_ON_GAS)
  enabled = cs.preap_engagement.update(DT_CTRL, lever, gear_raw, ret.doorOpen, ret.brakePressed,
                                       ret.gasPressed, steering_disengage, disengage_on_gas)

  digital = cp.vl["DI_state"]["DI_digitalSpeed"]
  if digital != 255:  # 255 = SNA (tesla_can.dbc VAL_ 872)
    kph = int(cp.vl["DI_state"]["DI_speedUnits"]) == SPEED_UNITS_KPH
    ret.vEgoCluster = digital * (CV.KPH_TO_MS if kph else CV.MPH_TO_MS)
  # Stock cruise keeps the speed; openpilot only steers. Set speed is shown as
  # ego speed, as in NAP without pedal.
  ret.cruiseState.enabled = enabled
  ret.cruiseState.available = True
  ret.cruiseState.speed = max(ret.vEgo, 1e-3)
  ret.cruiseState.standstill = False

  cs.msg_stw_actn_req = dict(cp.vl["STW_ACTN_RQ"])
  if pedal is not None:
    di_cruise = int(cp.vl["DI_state"]["DI_cruiseState"])
    cs.preap_latch = enabled
    cs.stock_cc_active = stock_cc_active(di_cruise)
    cs.stock_cc_cancelable = stock_cc_cancelable(di_cruise)
    cs.stalk_fresh = cs.stalk_tracker.update(cs.msg_stw_actn_req, DT_CTRL).fresh
    ret.cruiseState.enabled = cs.stock_cc_active
    events = []
    for kind, pressed in lever_buttons(lever, getattr(cs, "preap_lever_prev", 0)):
      be = car.CarState.ButtonEvent.new_message()
      be.type = ButtonType.accelCruise if kind == "accel" else ButtonType.decelCruise
      be.pressed = pressed
      events.append(be)
    ret.buttonEvents = events
    cs.preap_lever_prev = lever
  return ret
