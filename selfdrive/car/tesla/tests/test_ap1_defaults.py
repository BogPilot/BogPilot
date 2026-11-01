"""One-time AP1 toggle profile (selfdrive/car/tesla/ap1_defaults.py)."""
import re
from pathlib import Path

import pytest

from cereal import car
from openpilot.common.params import Params
from openpilot.frogpilot.common.frogpilot_variables import BOGPILOT_THEME, EXCLUDED_KEYS, frogpilot_default_params
from openpilot.selfdrive.car.tesla import ap1_defaults as ap1
from openpilot.selfdrive.car.tesla.values import CAR

ROOT = Path(__file__).resolve().parents[4]
TABLE = {k: (v.decode() if isinstance(v, bytes) else v) for k, v, _, _ in frogpilot_default_params}

# The deny-list the profile was built against.
REQUIRED_DENY = {
  "GithubSshKeys", "GithubUsername", "MapboxPublicKey", "MapboxSecretKey", "AMapKey1", "AMapKey2", "WeatherToken",
  "SecOCKey", "SecOCKeys", "KonikDongleId", "DiscordUsername", "GsmApn", "FrogPilotStats",
  "FavoriteDestinations", "MapsSelected", "SearchInput", "RecordFront",
  "CarMake", "CarModel", "CarModelName", "ForceFingerprint",
  "SteerRatio", "SteerDelay", "SteerFriction", "SteerKP", "SteerLatAccel", "LongitudinalActuatorDelay", "StartAccel",
  "StopAccel", "StoppingDecelRate", "VEgoStarting", "VEgoStopping", "LiveDelay",
  "ThemesDownloaded", "ShownToggleDescriptions", "ExperimentalModeConfirmed", "UpdatedToggles", "MinimumBackupSize",
  "SshEnabled", "TetheringEnabled", "GsmRoaming", "NoUploads", "NoLogging", "DisableOnroadUploads", "UseKonikServer",
  "HigherBitrate", "DeviceManagement", "AutomaticUpdates", "IsMetric", "LastKnownTime", "Model",
}


@pytest.fixture
def env(tmp_path):
  params = Params(str(tmp_path / "params"))
  for key, value in TABLE.items():
    params.put(key, value)  # what manager.py writes on a fresh device
  bp_dir = tmp_path / "params_bogpilot"
  return params, bp_dir


def run(params, bp_dir):
  return ap1.apply_ap1_defaults_once(params, bogpilot_dir=bp_dir, default_table=TABLE, refresh=False)


def test_profile_keys_exist_in_table_and_params():
  probe = Params()
  for key in ap1.AP1_PARAM_PROFILE:
    assert key in TABLE, key
    probe.check_key(key)  # a key unknown to params.cc would crash the prebuilt params_pyx.so


def test_profile_only_lists_changes():
  for key, value in ap1.AP1_PARAM_PROFILE.items():
    if key in ap1.EXPLICIT_DEFAULT_KEYS:
      assert ap1.values_equal(value, TABLE[key]), f"{key} is listed as an explicit default but differs"
      continue
    assert not ap1.values_equal(value, TABLE[key]), f"{key} equals the table default"


def test_offset_keys_exist():
  probe = Params()
  assert list(ap1.AP1_SPEED_OFFSETS_MPH) == [f"Offset{i}" for i in range(1, 8)]
  for key in ap1.AP1_SPEED_OFFSETS_MPH:
    assert key in TABLE, key
    probe.check_key(key)
    assert key not in ap1.AP1_PARAM_PROFILE  # offsets only come from the unit-aware table


def test_offset_bands_match_settings_labels():
  src = (ROOT / "frogpilot/ui/qt/offroad/longitudinal_settings.cc").read_text()
  bands = dict(re.findall(r'\{"(Offset[1-7])", tr\("Speed Offset \((\d+–\d+) mph\)"\)', src))
  assert bands == {"Offset1": "0–24", "Offset2": "25–34", "Offset3": "35–44", "Offset4": "45–54",
                   "Offset5": "55–64", "Offset6": "65–74", "Offset7": "75–99"}


def test_owner_profile_values():
  # Sport acceleration, Eco+ deceleration (enum 2 for both; see longitudinal_settings.cc)
  assert ap1.AP1_PARAM_PROFILE["AccelerationProfile"] == "2"
  assert ap1.AP1_PARAM_PROFILE["DecelerationProfile"] == "2"
  src = (ROOT / "frogpilot/ui/qt/offroad/longitudinal_settings.cc").read_text()
  assert 'accelerationProfiles{tr("Standard"), tr("Eco"), tr("Sport"), tr("Sport+")}' in src
  assert 'decelerationProfiles{tr("Standard"), tr("Eco"), tr("Eco+")}' in src
  # chimes muted; FrogPilot telemetry is removed outright (table default 0, hidden), so not in the profile
  assert ap1.values_equal(ap1.AP1_PARAM_PROFILE["EngageVolume"], 0)
  assert ap1.values_equal(ap1.AP1_PARAM_PROFILE["DisengageVolume"], 0)
  assert "FrogPilotTelemetry" not in ap1.AP1_PARAM_PROFILE
  assert TABLE["FrogPilotTelemetry"] == "0"
  assert ap1.AP1_SPEED_OFFSETS_MPH == {"Offset1": 4, "Offset2": 5, "Offset3": 5, "Offset4": 5,
                                       "Offset5": 7, "Offset6": 8, "Offset7": 10}


def test_theme_profile():
  assert ap1.BOGPILOT_THEME == BOGPILOT_THEME
  for key in ap1.THEME_KEYS:
    assert ap1.AP1_PARAM_PROFILE[key] == BOGPILOT_THEME, key
    assert key in ap1.EXPLICIT_DEFAULT_KEYS, key
    assert TABLE[key] == BOGPILOT_THEME, key
  assert ap1.AP1_PARAM_PROFILE["PersonalizeOpenpilot"] == "1"
  assert ap1.AP1_PARAM_PROFILE["RandomThemes"] == "0"
  assert set(ap1.STOCK_LOOK_THEME_VALUES) == set(ap1.THEME_KEYS) - {"CustomDistanceIcons"}
  assert "none" not in ap1.STOCK_LOOK_THEME_VALUES["WheelIcon"]
  assert ap1.THEME_MARKER_NAME == "AP1ThemeBogPilot2"


def test_fresh_ap1_theme_values(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  run(params, bp_dir)
  for key in ap1.THEME_KEYS:
    assert params.get(key, encoding="utf-8") == BOGPILOT_THEME, key


@pytest.mark.parametrize("old_theme_marker", [False, True])
def test_owner_device_on_stock_moves_to_bogpilot_once(env, old_theme_marker):
  # Profile already applied earlier; colors / wheel on stock (as the 293184ad step left them).
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  bp_dir.mkdir(parents=True)
  ap1.marker_path(bp_dir).write_text("1\n")
  if old_theme_marker:
    (bp_dir / "AP1ThemeBogPilot").write_text("1\n")
    for key in ("CustomIcons", "CustomSounds", "CustomSignals"):
      params.put(key, BOGPILOT_THEME)
  else:
    params.put("CustomIcons", "stock")
    params.put("CustomSounds", "stock")
    params.put("CustomSignals", "none")
  params.put("CustomColors", "stock")
  params.put("WheelIcon", "stock")
  params.put("CustomDistanceIcons", BOGPILOT_THEME)
  params.put("LeadInfo", "1")  # main profile must not run again
  written = run(params, bp_dir)
  expected = {"CustomColors", "WheelIcon"} | (set() if old_theme_marker else {"CustomIcons", "CustomSounds", "CustomSignals"})
  assert set(written) == expected
  for key in ap1.THEME_KEYS:
    assert params.get(key, encoding="utf-8") == BOGPILOT_THEME, key
  assert params.get("LeadInfo", encoding="utf-8") == "1"
  assert ap1.theme_marker_path(bp_dir).exists()
  # once only: an owner who goes back to stock keeps it
  params.put("CustomColors", "stock")
  assert run(params, bp_dir) == []
  assert params.get("CustomColors", encoding="utf-8") == "stock"


def test_theme_step_leaves_other_choices_alone(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  params.put("CustomColors", "frog")
  params.put("CustomIcons", "frog-animated")
  params.put("WheelIcon", "none")
  params.put("CustomDistanceIcons", "stock")
  params.put("CustomSounds", "Stock")
  run(params, bp_dir)
  assert params.get("CustomColors", encoding="utf-8") == "frog"
  assert params.get("CustomIcons", encoding="utf-8") == "frog-animated"
  assert params.get("WheelIcon", encoding="utf-8") == "none"
  assert params.get("CustomDistanceIcons", encoding="utf-8") == "stock"
  assert params.get("CustomSounds", encoding="utf-8") == BOGPILOT_THEME


def test_theme_step_not_for_other_cars(env):
  params, bp_dir = env
  params.put("CarModel", "TESLA_AP2_MODELS")
  params.put("CustomColors", "stock")
  assert run(params, bp_dir) == []
  assert params.get("CustomColors", encoding="utf-8") == "stock"
  assert not ap1.theme_marker_path(bp_dir).exists()


def test_no_denied_or_private_key_in_profile():
  assert REQUIRED_DENY <= ap1.NEVER_PROFILE_KEYS
  for key in ap1.AP1_PARAM_PROFILE:
    assert key not in ap1.NEVER_PROFILE_KEYS, key
    assert key not in EXCLUDED_KEYS, key
    assert not key.endswith("Stock"), key


def test_file_toggles_are_known_bogpilot_files():
  assert set(ap1.AP1_FILE_TOGGLE_PROFILE) <= set(ap1.FILE_TOGGLE_KEYS)
  assert ap1.MARKER_NAME not in ap1.FILE_TOGGLE_KEYS
  assert ap1.AP1_FILE_TOGGLE_PROFILE == {"DeveloperHUD": "1"}


def test_standing_preferences():
  # Increase Stopped Distance stays 0: not in the profile, and the table default is 0.
  assert "IncreasedStoppedDistance" not in ap1.AP1_PARAM_PROFILE
  assert TABLE["IncreasedStoppedDistance"] == "0"
  # Lead metrics off because the Developer HUD shows lead data; the lead marker is left as-is.
  assert ap1.AP1_PARAM_PROFILE["LeadInfo"] == "0"
  assert "HideLeadMarker" not in ap1.AP1_PARAM_PROFILE
  # Developer tuning level, confirmed so the first-open popup does not drop it to Minimal.
  assert ap1.AP1_PARAM_PROFILE["TuningLevel"] == "3"
  assert ap1.AP1_PARAM_PROFILE["TuningLevelConfirmed"] == "1"


def test_ap1_car_model_name_matches_platform():
  assert ap1.AP1_CAR_MODEL == str(CAR.TESLA_AP1_MODELS)


@pytest.mark.parametrize("car_model", [None, "TESLA_AP2_MODELS", "TESLA_MODELS_RAVEN", "TOYOTA_RAV4"])
def test_only_fires_for_ap1(env, car_model):
  params, bp_dir = env
  if car_model is not None:
    params.put("CarModel", car_model)
  assert run(params, bp_dir) == []
  assert not ap1.marker_path(bp_dir).exists()
  assert not ap1.theme_marker_path(bp_dir).exists()
  assert not (bp_dir / "DeveloperHUD").exists()
  for key in ap1.AP1_PARAM_PROFILE:
    assert params.get(key, encoding="utf-8") == TABLE[key]


def test_applies_on_fresh_ap1(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  written = run(params, bp_dir)
  assert set(written) == set(ap1.AP1_PARAM_PROFILE) | set(ap1.AP1_SPEED_OFFSETS_MPH) | set(ap1.AP1_FILE_TOGGLE_PROFILE)
  for key, value in ap1.AP1_PARAM_PROFILE.items():
    assert params.get(key, encoding="utf-8") == value
  for key, mph in ap1.AP1_SPEED_OFFSETS_MPH.items():
    assert params.get_int(key) == mph
  assert params.get_bool("FrogPilotTelemetry") is False
  for key in ap1.THEME_KEYS:
    assert params.get(key, encoding="utf-8") == BOGPILOT_THEME
  assert (bp_dir / "DeveloperHUD").read_text() == "1"
  assert ap1.marker_path(bp_dir).exists()
  assert ap1.theme_marker_path(bp_dir).exists()
  assert params.get("CarModel", encoding="utf-8") == ap1.AP1_CAR_MODEL
  assert params.get_int("IncreasedStoppedDistance") == 0


def test_applies_from_persistent_fingerprint(env):
  params, bp_dir = env
  cp = car.CarParams.new_message(carFingerprint=ap1.AP1_CAR_MODEL)
  params.put("CarParamsPersistent", cp.to_bytes())
  assert run(params, bp_dir)
  assert params.get("TuningLevel", encoding="utf-8") == "3"


def test_never_overwrites_owner_changes(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  params.put("RelaxedFollow", "1.5")
  params.put("CustomColors", "frog")
  params.put("DisengageVolume", "80")
  bp_dir.mkdir(parents=True)
  (bp_dir / "DeveloperHUD").write_text("0")
  written = run(params, bp_dir)
  assert "RelaxedFollow" not in written and "CustomColors" not in written and "DeveloperHUD" not in written
  assert params.get("RelaxedFollow", encoding="utf-8") == "1.5"
  assert params.get("CustomColors", encoding="utf-8") == "frog"
  assert params.get("DisengageVolume", encoding="utf-8") == "80"
  assert (bp_dir / "DeveloperHUD").read_text() == "0"
  # untouched keys still got the profile
  assert params.get("LeadInfo", encoding="utf-8") == "0"


def test_numeric_default_formats_count_as_default(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  params.put("Offset1", "5.000000")  # UI float format of the table default "5"
  params.put("DecelerationProfile", "1.000000")
  run(params, bp_dir)
  assert params.get_int("Offset1") == 4
  assert params.get("DecelerationProfile", encoding="utf-8") == "2"


def test_metric_device_gets_kph_offsets(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  params.put("IsMetric", "1")
  run(params, bp_dir)
  got = {key: params.get_int(key) for key in ap1.AP1_SPEED_OFFSETS_MPH}
  assert got == {"Offset1": 6, "Offset2": 8, "Offset3": 8, "Offset4": 8, "Offset5": 11, "Offset6": 13, "Offset7": 16}


def test_metric_ui_converted_defaults_count_as_default(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  params.put("IsMetric", "1")
  params.put("Offset1", "8")    # settings UI converted the mph default 5 to km/h (truncated)
  params.put("Offset5", "16")   # 10 mph -> 16 km/h
  params.put("Offset6", "12")   # owner's own km/h choice: kept
  run(params, bp_dir)
  assert params.get_int("Offset1") == 6
  assert params.get_int("Offset5") == 11
  assert params.get_int("Offset6") == 12


def test_imperial_owner_offset_change_kept(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  params.put("Offset3", "3")
  params.put("FrogPilotTelemetry", "0")
  written = run(params, bp_dir)
  assert "Offset3" not in written and "FrogPilotTelemetry" not in written
  assert params.get_int("Offset3") == 3


def test_confirmed_tuning_level_is_respected(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  params.put("TuningLevelConfirmed", "1")  # owner already picked Minimal (TuningLevel 0 == table default)
  written = run(params, bp_dir)
  assert "TuningLevel" not in written and "TuningLevelConfirmed" not in written
  assert params.get_int("TuningLevel") == 0


def test_runs_once(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  assert run(params, bp_dir)
  params.put("LeadInfo", TABLE["LeadInfo"])  # owner turns it back on
  (bp_dir / "DeveloperHUD").unlink()
  assert run(params, bp_dir) == []
  assert params.get("LeadInfo", encoding="utf-8") == TABLE["LeadInfo"]
  assert not (bp_dir / "DeveloperHUD").exists()


def test_reset_clears_marker_and_reapplies(env):
  params, bp_dir = env
  params.put("CarModel", ap1.AP1_CAR_MODEL)
  assert run(params, bp_dir)
  for key, value in TABLE.items():  # DoToggleReset puts the table defaults back
    if key not in EXCLUDED_KEYS and key != "CarModel":
      params.put(key, value)
  assert run(params, bp_dir) == []
  (bp_dir / "AP1ThemeBogPilot").write_text("1\n")
  ap1.clear_ap1_defaults_marker(bp_dir)
  assert not (bp_dir / "AP1ThemeBogPilot").exists()
  assert not ap1.marker_path(bp_dir).exists()
  assert not ap1.theme_marker_path(bp_dir).exists()
  assert run(params, bp_dir)
  assert params.get("LeadInfo", encoding="utf-8") == "0"


def test_hook_never_raises():
  class Broken:
    def get(self, *a, **k):
      raise RuntimeError("boom")
  assert ap1.apply_ap1_defaults_once(Broken(), bogpilot_dir=Path("/nonexistent/x"), default_table=TABLE, refresh=False) == []


def test_manager_wiring():
  src = (ROOT / "system/manager/manager.py").read_text()
  loop_end = src.index('params.remove("DoToggleResetStock")')
  hook = src.index("apply_ap1_defaults_once(params)")
  assert loop_end < hook < src.index("frogpilot_boot_functions(build_metadata")
  block = src[loop_end:hook]
  assert re.search(r"if reset_toggles:\s*\n\s*clear_ap1_defaults_marker\(\)", block)
  assert "reset_toggles_stock" not in block  # a reset to stock leaves the marker alone


def test_frogpilot_process_wiring():
  src = (ROOT / "frogpilot/frogpilot_process.py").read_text()
  block = src[src.index('if params_memory.get_bool("FrogPilotTogglesUpdated")'):]
  block = block[:block.index("frogpilot_variables.update(")]
  assert re.search(r"if not started:\s*\n\s*apply_ap1_defaults_once\(params, refresh=False\)", block)
