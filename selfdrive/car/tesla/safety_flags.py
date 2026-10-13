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
