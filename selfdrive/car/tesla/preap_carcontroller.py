"""Pre-AP Model S actuator frames (CAR.TESLA_PREAP_MODELS only).

MIT License. Ported from NotAutopilot (github.com/NotAutopilot/openpilot,
opendbc car/tesla/carcontroller.py Pre-AP branch and preap/teslacan.py at
opendbc cc39c7f), itself based on Tinkla / BogGyver (create_radar_VIN_msg,
Tinkla 0.6.6). Copyright (c) NotAutopilot contributors, BogGyver, and the
openpilot authors.

Sent, all on bus 0 (CANBUS.chassis), and all whitelisted by SAFETY_TESLA_PREAP:
  0x488 DAS_steeringControl  every 2 frames (50 Hz), counter (frame // 2) % 16
  0x214 EPB_epasControl      every 2 frames, EPB_epasEACAllow 1, same counter
  0x560 radar VIN fragments  every 10 frames, only with radar emulation on.
                             Consumed by the panda, never put on the car.
Not ported from NAP: 0x551 pedal (longitudinal deferred), 0x45 stock-CC
spoofing, 0x3E9 body controls, 0x2B9. The panda blocks all of them.
"""

from openpilot.common.numpy_fast import clip
from openpilot.selfdrive.car import apply_std_steer_angle_limits
from openpilot.selfdrive.car.tesla.values import CANBUS, CarControllerParams
from panda import Panda

PREAP_HANDS_LAT_PAUSE = 3   # NAP: lat_active = CC.latActive and hands_on_level < 3
PREAP_MAX_ANGLE = 360.0     # panda PREAP_MAX_ANGLE (3600 in 0.1 deg)
EPAS_CONTROL_ADDR = 0x214  # tesla_can.dbc BO_ 532 EPB_epasControl
RADAR_VIN_ADDR = 0x560      # NAP preap/teslacan.py create_radar_vin_msg, Tinkla 0.6.6


def epas_control_checksum(dat: bytes) -> int:
  # Same additive checksum as 0x488 (addr bytes + data bytes), NAP preap/teslacan.py
  return (0x14 + 0x02 + sum(dat)) & 0xFF


def create_epas_control(counter, mode):
  """0x214 EPB_epasControl, packed by hand from tesla_can.dbc BO_ 532:
  EPB_epasEACAllow 2|3@0+ (byte 0 bits 2..0), EPB_epasControlCounter 11|4@0+
  (byte 1 bits 3..0), EPB_epasControlChecksum 23|8@0+ (byte 2)."""
  dat = bytes((mode & 0x07, counter & 0x0F))
  return [EPAS_CONTROL_ADDR, 0, dat + bytes((epas_control_checksum(dat),)), CANBUS.chassis]


def create_radar_vin_msg(fragment, vin, use_radar, position, epas_type):
  """Host -> panda 0x560 fragment, Tinkla 0.6.6 packing (NAP create_radar_vin_msg).

  An empty VIN becomes 17 spaces (this-car passthrough). The VIN is never logged.
  """
  padded = ((vin or "") + " " * 17)[:17]
  if fragment == 0:
    flags = (1 if use_radar else 0) + ((position & 0x03) << 1) + ((epas_type & 0x07) << 3)
    dat = bytes((0, 1, flags, 0, 0, ord(padded[0]), ord(padded[1]), ord(padded[2])))
  elif fragment == 1:
    dat = bytes((1, *(ord(padded[i]) for i in range(3, 10))))
  else:
    dat = bytes((2, *(ord(padded[i]) for i in range(10, 17))))
  return [RADAR_VIN_ADDR, 0, dat, CANBUS.chassis]


def preap_radar_config(CP, bogpilot_dir=None):
  """Radar sender config, or None. On only when the installed safety param has
  FLAG_TESLA_PREAP_RADAR_EMULATION (set by the interface from PreAPRadarEnabled).

  Reads TeslaRadarDonorVin (device-local, nap_import.py). Never logged.
  """
  try:
    configs = list(CP.safetyConfigs)
    if not configs or not (int(configs[0].safetyParam) & Panda.FLAG_TESLA_PREAP_RADAR_EMULATION):
      return None
    from openpilot.selfdrive.car.tesla.nap_import import DEFAULT_OUT_DIR, read_donor_vin
    from openpilot.selfdrive.car.tesla.preap_settings import load_preap_settings
    base = bogpilot_dir if bogpilot_dir is not None else DEFAULT_OUT_DIR
    settings, _ = load_preap_settings(base)
    return {"vin": read_donor_vin(base), "position": settings["radar_position"],
            "epas_type": settings["radar_epas_type"]}
  except Exception:
    return None


class PreapController:
  def __init__(self, tesla_can, radar=None):
    """radar: None, or dict(vin=str, position=int, epas_type=int)."""
    self.tesla_can = tesla_can
    self.radar = radar
    self.radar_idx = 0
    self.apply_angle_last = 0.0

  def update(self, CC, CS, frame):
    sends = []
    lat_active = bool(CC.latActive) and CS.hands_on_level < PREAP_HANDS_LAT_PAUSE
    if frame % 2 == 0:
      if lat_active:
        angle = apply_std_steer_angle_limits(CC.actuators.steeringAngleDeg, self.apply_angle_last,
                                             CS.out.vEgoRaw, CarControllerParams)
        angle = clip(angle, -PREAP_MAX_ANGLE, PREAP_MAX_ANGLE)
      else:
        # Inactive: follow the measured wheel so the next engage starts there.
        angle = CS.out.steeringAngleDeg
      self.apply_angle_last = angle
      cntr = (frame // 2) % 16
      sends.append(self.tesla_can.create_steering_control(angle, lat_active, cntr))
      sends.append(create_epas_control(cntr, 1))

    if self.radar is not None and frame % 10 == 0:
      sends.append(create_radar_vin_msg(self.radar_idx, self.radar.get("vin", ""), True,
                                        int(self.radar.get("position", 0)), int(self.radar.get("epas_type", 0))))
      self.radar_idx = (self.radar_idx + 1) % 3
    return sends
