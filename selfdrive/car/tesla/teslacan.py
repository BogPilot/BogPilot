import crcmod

from openpilot.common.conversions import Conversions as CV
from openpilot.selfdrive.car.tesla.actuator_plan import steering_control_type
from openpilot.selfdrive.car.tesla.values import CANBUS, CarControllerParams


class TeslaCAN:
  def __init__(self, packer, pt_packer):
    self.packer = packer
    self.pt_packer = pt_packer
    self.crc = crcmod.mkCrcFun(0x11d, initCrc=0x00, rev=False, xorOut=0xff)

  @staticmethod
  def checksum(msg_id, dat):
    # TODO: get message ID from name instead
    ret = (msg_id & 0xFF) + ((msg_id >> 8) & 0xFF)
    ret += sum(dat)
    return ret & 0xFF

  def create_steering_control(self, angle, enabled, counter):
    values = {
      "DAS_steeringAngleRequest": -angle,
      "DAS_steeringHapticRequest": 0,
      "DAS_steeringControlType": steering_control_type(enabled),
      "DAS_steeringControlCounter": counter,
    }

    data = self.packer.make_can_msg("DAS_steeringControl", CANBUS.chassis, values)[2]
    values["DAS_steeringControlChecksum"] = self.checksum(0x488, data[:3])
    return self.packer.make_can_msg("DAS_steeringControl", CANBUS.chassis, values)

  def create_action_request(self, msg_stw_actn_req, cancel, bus, counter):
    # We copy this whole message when spamming cancel
    values = {s: msg_stw_actn_req[s] for s in [
      "SpdCtrlLvr_Stat",
      "VSL_Enbl_Rq",
      "SpdCtrlLvrStat_Inv",
      "DTR_Dist_Rq",
      "TurnIndLvr_Stat",
      "HiBmLvr_Stat",
      "WprWashSw_Psd",
      "WprWash_R_Sw_Posn_V2",
      "StW_Lvr_Stat",
      "StW_Cond_Flt",
      "StW_Cond_Psd",
      "HrnSw_Psd",
      "StW_Sw00_Psd",
      "StW_Sw01_Psd",
      "StW_Sw02_Psd",
      "StW_Sw03_Psd",
      "StW_Sw04_Psd",
      "StW_Sw05_Psd",
      "StW_Sw06_Psd",
      "StW_Sw07_Psd",
      "StW_Sw08_Psd",
      "StW_Sw09_Psd",
      "StW_Sw10_Psd",
      "StW_Sw11_Psd",
      "StW_Sw12_Psd",
      "StW_Sw13_Psd",
      "StW_Sw14_Psd",
      "StW_Sw15_Psd",
      "WprSw6Posn",
      "MC_STW_ACTN_RQ",
      "CRC_STW_ACTN_RQ",
    ]}

    if cancel:
      values["SpdCtrlLvr_Stat"] = 1
      values["MC_STW_ACTN_RQ"] = counter

    data = self.packer.make_can_msg("STW_ACTN_RQ", bus, values)[2]
    values["CRC_STW_ACTN_RQ"] = self.crc(data[:7])
    return self.packer.make_can_msg("STW_ACTN_RQ", bus, values)

  def create_longitudinal_commands(self, acc_state, speed, min_accel, max_accel, cnt, chassis_only=False,
                                   jerk_min=CarControllerParams.JERK_LIMIT_MIN, jerk_max=CarControllerParams.JERK_LIMIT_MAX):
    # AP1 passes comfort-band jerk limits (long_smooth.Ap1JerkLimit). Others keep +/-8.
    messages = []
    values = {
      "DAS_setSpeed": speed * CV.MS_TO_KPH,
      "DAS_accState": acc_state,
      "DAS_aebEvent": 0,
      "DAS_jerkMin": jerk_min,
      "DAS_jerkMax": jerk_max,
      "DAS_accelMin": min_accel,
      "DAS_accelMax": max_accel,
      "DAS_controlCounter": cnt,
      "DAS_controlChecksum": 0,
    }

    # AP1 packs chassis DAS_control only (0x2b9). The default still packs
    # chassis and powertrain, which is the non-AP1 path. Checksum id stays 0x2b9.
    targets = [(self.packer, CANBUS.chassis)]
    if not chassis_only:
      targets.append((self.pt_packer, CANBUS.powertrain))
    for packer, bus in targets:
      data = packer.make_can_msg("DAS_control", bus, values)[2]
      values["DAS_controlChecksum"] = self.checksum(0x2b9, data[:7])
      messages.append(packer.make_can_msg("DAS_control", bus, values))
    return messages

  @staticmethod
  def create_ap1_hold_clear(bus=CANBUS.chassis):
    """All-zero chassis 0x349. Clears Tesla Hold's press-accelerator prompt.

    Bit layout is Tinkla earlytesla-openpilot (tesla_unity_dev,
    501c7de91b59c70510e9dc1585acfde9b7102c93)
    selfdrive/car/tesla/teslacan.py create_das_warningMatrix3:

        (DAS_gas_to_resume << 1) + (stopSignWarning << 3) + (stopLightWarning << 4)

    That is bit 1 of byte 0. HUD_module.py sets DAS_gas_to_resume = 0 before
    the send. This frame is that clear: every warning bit 0. 0x349 is not in
    tesla_can.dbc, so this does not invent a DBC signal name.
    Message shape matches can_list_to_can_capnp: address, busTime, dat, src.
    """
    return [0x349, 0, b"\x00" * 8, bus]
