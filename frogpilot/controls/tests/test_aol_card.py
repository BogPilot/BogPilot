"""FrogPilotCard Always On Lateral gating (port of FrogPilot-Staging d83b149b |= -> &= fix).

Toggle off: AOL must never be enabled, even while cruise is engaged (the old |= enabled it).
Toggle on: needs main/LKAS state or cruise engaged, plus the usual gates.
Also checks the BogPilot AP1 toggle profile keeps AOL off.
"""
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[3]


class _GS:
  park, drive, reverse, neutral = "park", "drive", "reverse", "neutral"


def _load_card(monkeypatch, tmp_path):
  fv = types.ModuleType("openpilot.frogpilot.common.frogpilot_variables")
  fv.ERROR_LOGS_PATH = tmp_path
  fv.GearShifter = _GS
  fv.NON_DRIVING_GEARS = (_GS.park, _GS.reverse, _GS.neutral)
  fv.params = MagicMock()
  fv.params_memory = MagicMock()
  fv.params_memory.get_bool.return_value = False
  ev = types.ModuleType("openpilot.selfdrive.controls.lib.events")
  ev.ET = N(IMMEDIATE_DISABLE="immediateDisable")
  dh = types.ModuleType("openpilot.selfdrive.controls.lib.drive_helpers")
  dh.CRUISE_LONG_PRESS = 50
  sf = types.ModuleType("openpilot.selfdrive.car.tesla.stalk_follow")
  sf.ap1_stalk_commands = MagicMock(return_value=[])
  tv = types.ModuleType("openpilot.selfdrive.car.tesla.values")
  tv.CAR = N(TESLA_AP1_MODELS="TESLA_AP1_MODELS")
  for name, mod in ((fv.__name__, fv), (ev.__name__, ev), (dh.__name__, dh), (sf.__name__, sf), (tv.__name__, tv)):
    monkeypatch.setitem(sys.modules, name, mod)
  spec = importlib.util.spec_from_file_location("_fp_card_under_test", ROOT / "frogpilot/controls/frogpilot_card.py")
  m = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(m)
  return m


def _toggles(aol, use_lkas=False, main=False, lkas=False):
  return N(always_on_lateral_set=aol, use_lkas_for_aol=use_lkas, always_on_lateral_main=aol and main,
           always_on_lateral_lkas=aol and lkas, always_on_lateral_pause_speed=0, frogs_go_moo=False,
           experimental_mode_via_distance=False, force_coast_via_distance=False, pause_lateral_via_distance=False,
           pause_longitudinal_via_distance=False, traffic_mode_via_distance=False, conditional_experimental_mode=False)


def _step(card_mod, toggles, cruise_enabled, cruise_available=True, fingerprint="TOYOTA"):
  car = N(CP=N(carName="toyota", carFingerprint=fingerprint), frogpilot_toggles=toggles)
  card = card_mod.FrogPilotCard(car)
  cs = N(cruiseState=N(enabled=cruise_enabled, available=cruise_available), gearShifter=_GS.drive, brakePressed=False,
         gasPressed=False, vEgo=20.0, standstill=False, buttonEvents=[])
  fcs = N(alwaysOnLateralAllowed=True, distancePressed=False)
  sm = MagicMock()
  data = {"frogpilotPlan": N(lateralCheck=True), "liveCalibration": N(calPerc=100),
          "controlsState": N(alertType=""), "frogpilotControlsState": N(alertType=""), "carControl": N(longActive=False)}
  sm.__getitem__.side_effect = data.__getitem__
  sm.updated = {"frogpilotPlan": False}
  card.update(cs, fcs, sm)
  return fcs.alwaysOnLateralEnabled


@pytest.mark.parametrize("use_lkas", [False, True])
@pytest.mark.parametrize("cruise_enabled", [False, True])
def test_toggle_off_never_enables_aol(monkeypatch, tmp_path, use_lkas, cruise_enabled):
  m = _load_card(monkeypatch, tmp_path)
  assert _step(m, _toggles(False, use_lkas=use_lkas), cruise_enabled) is False


def test_toggle_on_main_mode(monkeypatch, tmp_path):
  m = _load_card(monkeypatch, tmp_path)
  assert _step(m, _toggles(True, main=True), cruise_enabled=False) is True
  assert _step(m, _toggles(True, main=False), cruise_enabled=False) is False   # was True with |=
  assert _step(m, _toggles(True, main=False), cruise_enabled=True) is True
  assert _step(m, _toggles(True, main=True), cruise_enabled=False, cruise_available=False) is False


def test_toggle_on_lkas_mode(monkeypatch, tmp_path):
  m = _load_card(monkeypatch, tmp_path)
  assert _step(m, _toggles(True, use_lkas=True, lkas=True), cruise_enabled=False) is True
  assert _step(m, _toggles(True, use_lkas=True, lkas=False), cruise_enabled=False) is False


def test_ap1_profile_keeps_aol_off():
  spec = importlib.util.spec_from_file_location("_ap1_defaults", ROOT / "selfdrive/car/tesla/ap1_defaults.py")
  m = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(m)
  assert m.AP1_PARAM_PROFILE["AlwaysOnLateral"] == "0"
