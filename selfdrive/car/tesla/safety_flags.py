"""Tesla panda safety-param values. No CarParams and no actuation.

TESLA_FLAG_AP1 is bit 3, value 8 (Panda.FLAG_TESLA_AP1). Only CAR.TESLA_AP1_MODELS
gets that bit, plus TESLA_FLAG_LONGITUDINAL_CONTROL (2) so chassis 0x2b9 can be
sent. dashcam_only_for_candidate is false only for that candidate. POWERTRAIN is
not set unless 0x2bf is on the auxiliary powertrain bus. This module does not
see frogpilot_toggles.
"""

from panda import Panda

from openpilot.selfdrive.car.tesla.values import CANBUS, CAR

# Same bit as panda/board/safety/safety_tesla.h TESLA_FLAG_AP1.
FLAG_TESLA_AP1 = Panda.FLAG_TESLA_AP1


def _has_powertrain_das(fingerprint):
  """True when 0x2bf (DAS_control) is on the auxiliary powertrain panda bus.

  Same membership test the Tesla interface used before the AP1 flag. Chassis
  0x2bf does not count. AP1 is not expected to have this bus.
  """
  bus = CANBUS.autopilot_powertrain
  return (bus in fingerprint.keys()) and (0x2bf in fingerprint[bus].keys())


def flags_for_candidate(candidate, fingerprint, standstill=False):
  """Safety params for the Tesla safety model.

  AP1 with no 0x2bf is one config: FLAG_TESLA_AP1 | FLAG_TESLA_LONG_CONTROL
  (8|2 = 10). That is chassis DAS_control at 0x2b9. POWERTRAIN is not set on
  that config. Raven keeps bit 2 (value 4) and does not get bit 8 or the AP1
  long bit. AP2 gets neither unless 0x2bf is present. A 0x2bf powertrain
  fingerprint still adds longitudinal control and a second config with the
  powertrain bit. That second config also gets bit 8 only for AP1.
  The openpilot-long param is not decided here. interface.py sees the toggle.
  """
  if FLAG_TESLA_AP1 != 8:
    raise RuntimeError("TESLA_FLAG_AP1 must be 8")

  flags = Panda.FLAG_TESLA_RAVEN if candidate == CAR.TESLA_MODELS_RAVEN else 0
  if candidate == CAR.TESLA_AP1_MODELS:
    flags |= FLAG_TESLA_AP1
    # Chassis longitudinal is 0x2b9. Do not require 0x2bf. Do not set POWERTRAIN.
    flags |= Panda.FLAG_TESLA_LONG_CONTROL
    # Standstill experiment (AP1StandstillEngage, default off): standby 0x2b9.
    if standstill:
      flags |= Panda.FLAG_TESLA_AP1_STANDSTILL

  if _has_powertrain_das(fingerprint):
    flags |= Panda.FLAG_TESLA_LONG_CONTROL
    return (flags, flags | Panda.FLAG_TESLA_POWERTRAIN)
  return (flags,)


def dashcam_only_for_candidate(candidate):
  """True unless the candidate is CAR.TESLA_AP1_MODELS.

  AP1 is an explicit user exception for the Mobileye chassis port, not a
  Model 3 change. AP2, Raven, and every other candidate stay dashcam-only.
  This does not by itself set openpilotLongitudinalControl. AP1 chassis long
  is the interface param, and it does not require 0x2bf.
  """
  return candidate != CAR.TESLA_AP1_MODELS


# ---- Pre-AP (CAR.TESLA_PREAP_MODELS, SAFETY_TESLA_PREAP) -------------------
# Ported from NotAutopilot (NAP panda 282fae7 safety/modes/tesla_preap.h flag
# bits and opendbc cc39c7f interface.py), MIT. Never used for AP1.

# Comma Pedal GAS_SENSOR, NAP opendbc dbc/tesla_preap.dbc "BO_ 1362 GAS_SENSOR".
PEDAL_SENSOR_ADDR = 0x552
PEDAL_BUSES = (0, 2)


def learn_pedal_bus(fingerprint):
  """Bus (0 or 2) the pedal sensor was seen on during fingerprinting, else None.

  None when it was seen on neither or on both (ambiguous: keep the setting).
  NAP learns the pedal bus from the first GAS_SENSOR frame the same way.
  """
  seen = [bus for bus in PEDAL_BUSES if PEDAL_SENSOR_ADDR in (fingerprint.get(bus) or {})]
  return seen[0] if len(seen) == 1 else None


def preap_safety_param(settings, pedal_bus_learned=None, pedal_long=False):
  """SAFETY_TESLA_PREAP param from load_preap_settings() values.

  ENABLE_PEDAL is set only with pedal_long=True, which interface.py passes only
  when preap_pedal.pedal_long_bus() returned a bus (pedal enabled, calibration
  done, openpilot-long toggle on, GAS_SENSOR seen on one bus). Without it the
  panda keeps 0x551 and the 0x45 cancel blocked. CALIBRATION is never set from
  the host.
  """
  flags = 0
  if settings.get("radar_enabled"):
    flags |= Panda.FLAG_TESLA_PREAP_RADAR_EMULATION
    if settings.get("radar_behind_nosecone"):
      flags |= Panda.FLAG_TESLA_PREAP_RADAR_BEHIND_NOSECONE
  if settings.get("pedal_enabled"):
    bus = pedal_bus_learned if pedal_bus_learned in PEDAL_BUSES else settings.get("pedal_can_bus", 2)
    if bus == 0:
      flags |= Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO
    if pedal_long:
      flags |= Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL
  return flags
