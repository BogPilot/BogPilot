"""First-boot NotAutopilot settings import (frogpilot/system/nap_migration.py)."""
import json
import os
from pathlib import Path

import pytest

from openpilot.frogpilot.system import nap_migration as nm

FAKE_VIN = "FAKEVXN0000000000"  # synthetic, shaped like a VIN only


@pytest.fixture
def paths(tmp_path):
  p = nm.Paths(params_dir=tmp_path / "data" / "params" / "d", bogpilot_dir=tmp_path / "data" / "params_bogpilot",
               data_dir=tmp_path / "data")
  p.params_dir.mkdir(parents=True)
  # upstream / BogPilot params that every device has; must never be touched
  (p.params_dir / "LongitudinalPersonality").write_text("1")
  (p.params_dir / "IsMetric").write_text("0")
  return p


def put(p, key, value):
  (p.params_dir / key).write_text(value)


def nap_release_device(p):
  """What nap-release leaves behind on a Pre-AP car with pedal + radar."""
  put(p, "GitRemote", "https://github.com/NotAutopilot/openpilot.git")
  put(p, "GitBranch", "nap-release")
  put(p, "NAPForcePreAP", "1")
  put(p, "NAPPedalEnabled", "1")
  put(p, "NAPPedalCanBus", "0")
  put(p, "NAPPedalCalibDone", "1")
  put(p, "NAPPedalCalibMin", "-2.5")
  put(p, "NAPPedalCalibMax", "98.0")
  put(p, "NAPPedalCalibZero", "11.25")
  put(p, "NAPPedalCalibFactor", "1.6")
  put(p, "NAPRadarEnabled", "1")
  put(p, "NAPRadarBehindNosecone", "1")
  put(p, "NAPRadarOffset", "0.3")
  put(p, "NAPAdaptiveAccel", "0")
  put(p, "NAPFollowDistance", "6")
  put(p, "NAPPedalProfile", "4")
  put(p, "NAPEpasRiskAccepted", "1")
  put(p, "NAPRadarDonorVin", FAKE_VIN)
  put(p, "NAPRadarIgnoreHwFail", "1")
  put(p, "NAPiBoosterEnabled", "1")
  (p.data_dir / "nap_params.json").write_text(json.dumps({
    "double_pull_window_ms": 750, "use_pedal": True, "accel_profile": "MadMax", "radar_donor_vin": FAKE_VIN}))


def snapshot(d: Path):
  return {str(f.relative_to(d)): f.read_bytes() for f in sorted(d.rglob("*")) if f.is_file()}


def bp(p, name):
  return (p.bogpilot_dir / name).read_text()


# ------------------------------------------------------------------ NAP absent

def test_no_nap_writes_nothing(paths):
  before = snapshot(paths.data_dir)
  assert nm.migrate(paths)["result"] == "no_nap"
  assert not paths.bogpilot_dir.exists()
  assert snapshot(paths.data_dir) == before


def test_no_nap_with_other_bogpilot_toggles(paths):
  paths.bogpilot_dir.mkdir()
  (paths.bogpilot_dir / "DeveloperHUD").write_text("1")
  put(paths, "GitRemote", "https://github.com/BogPilot/openpilot.git")
  put(paths, "GitBranch", "nap-lookalike")  # supporting evidence only, never enough alone
  before = snapshot(paths.data_dir)
  assert nm.migrate(paths)["result"] == "no_nap"
  assert snapshot(paths.data_dir) == before


# ------------------------------------------------------------------ NAP present

def test_nap_release_import(paths):
  nap_release_device(paths)
  status = nm.migrate(paths, now="2026-01-01T00:00:00Z")
  assert status["result"] == "imported"
  assert not (paths.bogpilot_dir / nm.CAR_PROFILE_NAME).exists()  # car type is never forced
  assert bp(paths, "PreAPPedalEnabled") == "1"
  assert bp(paths, "PreAPPedalCanBus") == "0"
  assert bp(paths, "PreAPPedalCalibDone") == "1"
  assert float(bp(paths, "PreAPPedalCalibZero")) == 11.25
  assert float(bp(paths, "PreAPPedalCalibFactor")) == 1.6
  assert float(bp(paths, "PreAPPedalCalibMin")) == -2.5
  assert float(bp(paths, "PreAPPedalCalibMax")) == 98.0
  assert bp(paths, "PreAPRadarEnabled") == "1"
  assert bp(paths, "PreAPRadarBehindNosecone") == "1"
  assert float(bp(paths, "PreAPRadarOffset")) == 0.3
  assert bp(paths, "PreAPAdaptiveAccel") == "0"
  assert bp(paths, nm.MARKER_NAME) == "1"
  saved = json.loads(bp(paths, nm.STATUS_NAME))
  assert saved["nap_car_type"] == "preap"
  assert "TeslaCarProfile" not in saved["imported"]
  assert "NAPFollowDistance" in saved["skipped"]
  assert "nap_params.json:double_pull_window_ms" in saved["skipped"]
  assert "Sport" in saved["suggestions"]["AccelerationProfile"]


def test_not_imported_keys(paths):
  nap_release_device(paths)
  nm.migrate(paths)
  names = {f.name for f in paths.bogpilot_dir.iterdir()}
  for bad in ("PreAPiBoosterEnabled", "PreAPBrakeFactor", "PreAPRadarIgnoreHwFail", "PreAPFollowDistance"):
    assert bad not in names
  # no Params written: nothing new in /data/params/d
  assert not any(n.startswith(("PreAP", "Tesla")) for n in os.listdir(paths.params_dir))


def test_detect_by_json_only(paths):
  (paths.data_dir / "nap_params.json").write_text(json.dumps({"use_pedal": True, "pedal_can_zero": True,
                                                              "radar_enabled": False, "radar_offset": 0.5}))
  status = nm.migrate(paths)
  assert status["result"] == "imported"
  assert bp(paths, "PreAPPedalEnabled") == "1"
  assert bp(paths, "PreAPPedalCanBus") == "0"
  assert bp(paths, "PreAPRadarEnabled") == "0"
  assert float(bp(paths, "PreAPRadarOffset")) == 0.5
  assert nm.migrate(paths)["result"] == "already_done"
  assert json.loads(bp(paths, nm.STATUS_NAME))["nap_car_type"] is None  # nothing said what car it is


def test_param_file_wins_over_json(paths):
  put(paths, "NAPPedalEnabled", "0")
  (paths.data_dir / "nap_params.json").write_text(json.dumps({"use_pedal": True}))
  nm.migrate(paths)
  assert bp(paths, "PreAPPedalEnabled") == "0"


def test_detect_by_git_remote_only(paths):
  put(paths, "GitRemote", "https://github.com/NotAutopilot/openpilot")
  status = nm.migrate(paths)
  assert status["result"] == "imported"
  assert status["imported"] == {}
  assert (paths.bogpilot_dir / nm.MARKER_NAME).exists()


@pytest.mark.parametrize("nap_car_type,force,expected", [
  ("0", "1", "auto"), ("1", "0", "preap"), ("2", "1", "ap1"), (None, "1", "preap"), (None, "0", "auto"),
])
def test_car_type(paths, nap_car_type, force, expected):
  if nap_car_type is not None:
    put(paths, "NAPCarType", nap_car_type)
  put(paths, "NAPForcePreAP", force)
  status = nm.migrate(paths)
  assert status["nap_car_type"] == expected
  assert not (paths.bogpilot_dir / nm.CAR_PROFILE_NAME).exists()


def test_nap_ap1_radar_extras(paths):
  put(paths, "NAPCarType", "2")
  put(paths, "NAPRadarHud", "1")
  put(paths, "NAPRadarUpsideDown", "1")
  put(paths, "NAPRadarEpasType", "3")
  put(paths, "NAPRadarPosition", "2")
  nm.migrate(paths)
  assert bp(paths, "PreAPRadarHud") == "1"
  assert bp(paths, "PreAPRadarUpsideDown") == "1"
  assert bp(paths, "PreAPRadarEpasType") == "3"
  assert bp(paths, "PreAPRadarPosition") == "2"


def test_out_of_range_and_garbage_skipped(paths):
  put(paths, "NAPRadarEpasType", "9")
  put(paths, "NAPPedalCanBus", "1")
  put(paths, "NAPRadarOffset", "nan")
  put(paths, "NAPRadarEnabled", "maybe")
  status = nm.migrate(paths)
  for name in ("PreAPRadarEpasType", "PreAPPedalCanBus", "PreAPRadarOffset", "PreAPRadarEnabled"):
    assert not (paths.bogpilot_dir / name).exists()
  for key in ("NAPRadarEpasType", "NAPPedalCanBus", "NAPRadarOffset", "NAPRadarEnabled"):
    assert key in status["skipped"]


def test_pedal_calib_sanity(paths):
  put(paths, "NAPPedalCalibDone", "1")  # flag set but values never persisted
  nm.migrate(paths)
  assert bp(paths, "PreAPPedalCalibDone") == "0"


def test_existing_toggle_wins(paths):
  nap_release_device(paths)
  paths.bogpilot_dir.mkdir()
  (paths.bogpilot_dir / "PreAPPedalEnabled").write_text("0")
  status = nm.migrate(paths)
  assert bp(paths, "PreAPPedalEnabled") == "0"
  assert "PreAPPedalEnabled" in status["kept"]
  assert bp(paths, "PreAPRadarEnabled") == "1"  # the rest still imports


# ------------------------------------------------------------------ safety rules

def test_never_deletes_or_edits_nap_files(paths):
  nap_release_device(paths)
  before = snapshot(paths.params_dir)
  before_json = (paths.data_dir / "nap_params.json").read_bytes()
  nm.migrate(paths)
  assert snapshot(paths.params_dir) == before
  assert (paths.data_dir / "nap_params.json").read_bytes() == before_json


def test_backups(paths):
  nap_release_device(paths)
  nm.migrate(paths)
  bdir = paths.backup_dir
  for f in paths.params_dir.iterdir():
    if f.name.startswith("NAP") or f.name in ("GitRemote", "GitBranch"):
      assert (bdir / "params" / f.name).read_bytes() == f.read_bytes()
  assert (bdir / "nap_params.json").read_bytes() == (paths.data_dir / "nap_params.json").read_bytes()
  assert not (bdir / "params" / "LongitudinalPersonality").exists()


def test_backup_never_overwrites(paths):
  nap_release_device(paths)
  nm.migrate(paths)
  # re-run after the owner deletes the marker and NAP values changed in between
  (paths.bogpilot_dir / nm.MARKER_NAME).unlink()
  put(paths, "NAPRadarOffset", "0.9")
  nm.migrate(paths)
  b = paths.backup_dir / "params"
  assert (b / "NAPRadarOffset").read_text() == "0.3"
  assert (b / "NAPRadarOffset.1").read_text() == "0.9"
  assert not (b / "NAPPedalEnabled.1").exists()  # identical copy is not duplicated


def test_idempotent(paths):
  nap_release_device(paths)
  nm.migrate(paths)
  after_first = snapshot(paths.data_dir)
  for _ in range(3):
    assert nm.migrate(paths)["result"] == "already_done"
  assert snapshot(paths.data_dir) == after_first


def test_owner_change_survives_reboot(paths):
  nap_release_device(paths)
  nm.migrate(paths)
  (paths.bogpilot_dir / "PreAPRadarEnabled").write_text("0")  # owner turns radar off
  (paths.bogpilot_dir / nm.CAR_PROFILE_NAME).write_text("ap1")  # owner picks AP1 in the UI
  nm.migrate(paths)
  assert bp(paths, "PreAPRadarEnabled") == "0"
  assert bp(paths, nm.CAR_PROFILE_NAME) == "ap1"


def test_vin_never_leaks(paths):
  nap_release_device(paths)
  nm.migrate(paths)
  for f in paths.bogpilot_dir.rglob("*"):
    if f.is_file() and "params" not in f.relative_to(paths.bogpilot_dir).parts[:2] and f.name != "nap_params.json":
      assert FAKE_VIN not in f.read_text(errors="replace"), f
  assert FAKE_VIN not in (paths.backup_dir / nm.LOG_NAME).read_text()
  assert FAKE_VIN not in bp(paths, nm.STATUS_NAME)


def test_forbidden_targets():
  targets = {m.target for m in nm.MAPPINGS} | {nm.CAR_PROFILE_NAME}
  for t in targets:
    assert not nm.FORBIDDEN_TARGET_RE.search(t), t
  for word in ("SetSpeed", "TopSpeed", "StalkPull", "CruiseX", "OccViz", "LiveYOLO"):
    assert nm.FORBIDDEN_TARGET_RE.search(word)


def test_no_speed_stalk_or_viz_toggles_written(paths):
  nap_release_device(paths)
  nm.migrate(paths)
  for f in paths.bogpilot_dir.iterdir():
    assert not nm.FORBIDDEN_TARGET_RE.search(f.name), f.name


def test_old_checkout_and_epas_hint(paths):
  fw = paths.data_dir / "openpilot_nap" / "scripts" / "nap" / "firmware"
  fw.mkdir(parents=True)
  (fw / "epas.modified.bin").write_bytes(b"\0")
  status = nm.migrate(paths)
  assert status["result"] == "imported"
  assert "openpilot_nap" in status["epas_hint"]
  assert "patched" in status["epas_hint"]


def test_hook_never_raises(monkeypatch):
  def boom(*a, **k):
    raise RuntimeError("disk full")
  monkeypatch.setattr(nm, "migrate", boom)
  nm.migrate_nap_once()


def test_manager_calls_hook_before_params_are_cleared():
  # Params::clearAll deletes unknown keys (all NAP*), and manager_init overwrites GitRemote / GitBranch.
  src = (Path(__file__).resolve().parents[3] / "system" / "manager" / "manager.py").read_text()
  hook = src.index("migrate_nap_once()")
  assert hook < src.index("params.clear_all(")
  assert hook < src.index('params.put("GitRemote"')
  assert hook < src.index('params.put("GitBranch"')
