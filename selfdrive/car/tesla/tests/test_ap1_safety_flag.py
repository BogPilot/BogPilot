"""AP1 safety flag is bit 8 and only for TESLA_AP1_MODELS.

CarInterface cannot be constructed here: importing it loads opendbc parser_pyx.
flags_for_candidate is the pure helper interface.py calls. dashcam_only_for_candidate
is false only for AP1. AP1 with no 0x2bf is flags 8|2 = 10. The helper does not
see the long toggle. interface.py applies it for AP1.
"""

from pathlib import Path

from panda import Panda

from openpilot.selfdrive.car.tesla.safety_flags import FLAG_TESLA_AP1, dashcam_only_for_candidate, flags_for_candidate
from openpilot.selfdrive.car.tesla.values import CANBUS, CAR

ROOT = Path(__file__).resolve().parents[4]
INTERFACE = (ROOT / "selfdrive/car/tesla/interface.py").read_text()

POWERTRAIN_BUS = CANBUS.autopilot_powertrain


def _fp(powertrain_addrs=None, chassis_addrs=None):
  fingerprint = {}
  if chassis_addrs is not None:
    fingerprint[CANBUS.chassis] = {addr: 1 for addr in chassis_addrs}
  if powertrain_addrs is not None:
    fingerprint[POWERTRAIN_BUS] = {addr: 1 for addr in powertrain_addrs}
  return fingerprint


def test_ap1_constant_is_bit_8():
  assert FLAG_TESLA_AP1 == 8
  assert Panda.FLAG_TESLA_AP1 == 8
  assert Panda.FLAG_TESLA_POWERTRAIN == 1
  assert Panda.FLAG_TESLA_LONG_CONTROL == 2
  assert Panda.FLAG_TESLA_RAVEN == 4


def test_ap1_without_powertrain_id_is_ap1_and_long_not_powertrain():
  # No 0x2bf. Flags are TESLA_FLAG_AP1 | LONG_CONTROL = 10. Not POWERTRAIN.
  assert flags_for_candidate(CAR.TESLA_AP1_MODELS, {}) == (8 | 2,)
  assert flags_for_candidate(CAR.TESLA_AP1_MODELS, _fp(chassis_addrs=(0x45, 0x2B9, 0x488))) == (10,)
  assert (8 | 2) == 10
  for params in (
    flags_for_candidate(CAR.TESLA_AP1_MODELS, {}),
    flags_for_candidate(CAR.TESLA_AP1_MODELS, _fp(chassis_addrs=(0x45, 0x2B9, 0x488))),
  ):
    assert len(params) == 1
    assert params[0] & Panda.FLAG_TESLA_POWERTRAIN == 0
    assert params[0] & Panda.FLAG_TESLA_LONG_CONTROL
    assert params[0] & FLAG_TESLA_AP1


def test_raven_and_ap2_do_not_get_ap1_bit():
  assert flags_for_candidate(CAR.TESLA_AP2_MODELS, {}) == (0,)
  assert flags_for_candidate(CAR.TESLA_MODELS_RAVEN, {}) == (Panda.FLAG_TESLA_RAVEN,)
  assert flags_for_candidate(CAR.TESLA_MODELS_RAVEN, {})[0] & 8 == 0


def test_powertrain_branch_keeps_previous_flags_plus_ap1_only_for_ap1():
  fp = _fp(powertrain_addrs=(0x2BF,))
  # Previous AP2: LONG, then LONG|POWERTRAIN.
  assert flags_for_candidate(CAR.TESLA_AP2_MODELS, fp) == (2, 3)
  # Previous Raven: RAVEN|LONG, then that|POWERTRAIN. No bit 8.
  assert flags_for_candidate(CAR.TESLA_MODELS_RAVEN, fp) == (6, 7)
  assert all(param & 8 == 0 for param in flags_for_candidate(CAR.TESLA_MODELS_RAVEN, fp))
  # AP1 is not expected to have 0x2bf. If it did, previous flags plus bit 8.
  assert flags_for_candidate(CAR.TESLA_AP1_MODELS, fp) == (8 | 2, 8 | 2 | 1)


def test_chassis_0x2bf_does_not_select_powertrain_flags():
  fp = _fp(chassis_addrs=(0x2BF, 0x45, 0x2B9, 0x488))
  assert flags_for_candidate(CAR.TESLA_AP1_MODELS, fp) == (8 | 2,)
  assert flags_for_candidate(CAR.TESLA_AP1_MODELS, fp)[0] & Panda.FLAG_TESLA_POWERTRAIN == 0
  assert flags_for_candidate(CAR.TESLA_AP2_MODELS, fp) == (0,)


def test_dashcam_only_false_for_ap1_true_for_ap2_and_raven():
  assert dashcam_only_for_candidate(CAR.TESLA_AP1_MODELS) is False
  assert dashcam_only_for_candidate(CAR.TESLA_AP2_MODELS) is True
  assert dashcam_only_for_candidate(CAR.TESLA_MODELS_RAVEN) is True
  assert "dashcam_only_for_candidate(candidate)" in INTERFACE
  assert "flags_for_candidate(candidate, fingerprint, standstill=standstill)" in INTERFACE
  assert "ret.openpilotLongitudinalControl = False" in INTERFACE
  assert "ret.openpilotLongitudinalControl = not frogpilot_toggles.disable_openpilot_long" in INTERFACE
  # AP1 uses the same toggle. There is no literal assignment to True.
  assert "elif candidate == CAR.TESLA_AP1_MODELS:" in INTERFACE
  assert INTERFACE.count("not frogpilot_toggles.disable_openpilot_long") == 2
  assert "openpilotLongitudinalControl = True" not in INTERFACE
