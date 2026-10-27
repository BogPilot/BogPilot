"""DI_cruiseSet scale fix (cruise_set.py): default ON applies x2 correction."""
from types import SimpleNamespace

import pytest

from openpilot.common.conversions import Conversions as CV
from openpilot.selfdrive.car.tesla import cruise_set
from openpilot.selfdrive.car.tesla.tests.test_ap1_slc_raise import carstate_mod  # noqa: F401  (fixture)

MPH = CV.MPH_TO_MS


@pytest.mark.parametrize("text,expected", [
  ("", True),  # empty = default ON
  ("0", False), ("off", False), ("false", False), ("no", False), ("junk", False),
  ("1", True), ("on", True), ("true", True), ("yes", True), (b"1\n", True),
])
def test_enabled_parsing(text, expected):
  assert cruise_set.cruise_set_scale_fix_enabled(text) is expected


def test_missing_file_is_on(tmp_path, monkeypatch):
  monkeypatch.setattr(cruise_set, "_cruise_set_scale_fix_path", tmp_path / "nope")
  assert cruise_set.cruise_set_scale_fix_enabled() is True
  assert cruise_set.cruise_set_scale(False) == 1.0
  assert cruise_set.cruise_set_scale(True) == 2.0


def _run(carstate_mod, raw_half_units):
  from openpilot.selfdrive.car.tesla.values import CAR
  from openpilot.selfdrive.car.tesla.tests.test_ap1_speed_limit import _ap1_cans, _prep_define
  CS = carstate_mod.CarState(SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS), None)
  _prep_define(CS)
  CS.can_define.dv["DI_state"]["DI_cruiseState"] = {2: "ENABLED"}
  cp, cp_cam = _ap1_cans(40.0)
  cp.vl["DI_state"]["DI_cruiseState"] = 2
  cp.vl["DI_state"]["DI_speedUnits"] = 0  # MPH
  cp.vl["DI_state"]["DI_cruiseSet"] = raw_half_units  # DBC-decoded (raw * 0.5)
  ret, _ = CS.update(cp, cp_cam, None)
  return ret.cruiseState.speed


def test_carstate_default_on_restores_full(carstate_mod, tmp_path, monkeypatch):
  monkeypatch.setattr(cruise_set, "_cruise_set_scale_fix_path", tmp_path / "TeslaCruiseSetScaleFix")
  # missing file -> ON -> decoded 20.0 * 2 -> 40 mph
  assert _run(carstate_mod, 20.0) == pytest.approx(40.0 * MPH)


def test_carstate_explicit_off_keeps_half(carstate_mod, tmp_path, monkeypatch):
  f = tmp_path / "TeslaCruiseSetScaleFix"
  f.write_text("0\n")
  monkeypatch.setattr(cruise_set, "_cruise_set_scale_fix_path", f)
  assert _run(carstate_mod, 20.0) == pytest.approx(20.0 * MPH)


def test_carstate_fix_on_restores_full(carstate_mod, tmp_path, monkeypatch):
  f = tmp_path / "TeslaCruiseSetScaleFix"
  f.write_text("1\n")
  monkeypatch.setattr(cruise_set, "_cruise_set_scale_fix_path", f)
  assert _run(carstate_mod, 20.0) == pytest.approx(40.0 * MPH)  # 17.88 m/s
