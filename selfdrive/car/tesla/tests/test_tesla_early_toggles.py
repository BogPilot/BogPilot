"""Tesla Early params are stored preferences. They do not change CarParams."""

from pathlib import Path

from openpilot.selfdrive.car.tesla.toggles import (
  APPLIED_TO_CAR_PARAMS,
  tesla_long_control,
  tesla_stalk_follow,
)

ROOT = Path(__file__).resolve().parents[4]


def test_long_control_stored_values_default_off():
  assert tesla_long_control(None) == "off"
  assert tesla_long_control("") == "off"
  assert tesla_long_control("off") == "off"
  assert tesla_long_control("lateral_only") == "lateral_only"
  assert tesla_long_control("full") == "full"
  assert tesla_long_control(b"full") == "full"
  for rejected in ("0", "1", "2", "preap", "ap1_s", "ap2", "on", "true"):
    assert tesla_long_control(rejected) == "off"


def test_stalk_follow_defaults_on_and_explicit_off_is_off():
  assert tesla_stalk_follow(None) is True
  assert tesla_stalk_follow("") is True
  assert tesla_stalk_follow("1") is True
  assert tesla_stalk_follow(b"1") is True
  assert tesla_stalk_follow("0") is False
  assert tesla_stalk_follow("false") is False
  assert tesla_stalk_follow("off") is False
  assert tesla_stalk_follow("maybe") is False


def test_preferences_are_not_applied_to_car_params():
  assert APPLIED_TO_CAR_PARAMS is False
  interface = (ROOT / "selfdrive/car/tesla/interface.py").read_text()
  assert "dashcam_only_for_candidate(candidate)" in interface
  assert "TeslaLongControl" not in interface
  assert "TeslaStalkFollow" not in interface
  variables = (ROOT / "frogpilot/common/frogpilot_variables.py").read_text()
  assert '("TeslaLongControl", "off", 2, "off")' not in variables
  assert '("TeslaStalkFollow", "1", 2, "1")' not in variables
  assert "toggle.tesla_long_control" not in variables
  assert "toggle.tesla_stalk_follow" not in variables
  assert "TeslaPlatform" not in variables
