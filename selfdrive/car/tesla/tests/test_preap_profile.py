"""Pre-AP / AP1 profile selection and NAP param import (preap_profile.py, nap_import.py).

Detection rule ported from NotAutopilot/opendbc 21a45cf nap_detect.py (MIT) and its
tests/test_nap_detect.py. Fixtures here are synthetic: no VINs or device data.
"""
import json

import pytest

from openpilot.selfdrive.car.tesla import nap_import
from openpilot.selfdrive.car.tesla.preap_profile import (
  AP1_PLATFORM, CHASSIS_ADDRS, PREAP_MIN_ABSENCE_NS, PREAP_PLATFORM, ApEcuObserver,
  read_profile_override, resolve_live, select_candidate,
)

MS = 1_000_000


def feed(obs, seconds, frames_fn, step_ms=10):
  t = 0
  while t <= seconds * 1000:
    obs.update(t * MS, frames_fn(t))
    t += step_ms


def chassis(src=0):
  return [(a, src) for a in CHASSIS_ADDRS]


class TestObserver:
  def test_ap1_needs_both_addresses(self):
    obs = ApEcuObserver()
    feed(obs, 0.5, lambda t: chassis() + [(0x488, 2), (0x2B9, 2)])
    assert obs.result() == AP1_PLATFORM

  def test_ap1_seen_on_bus0_relay_closed(self):
    obs = ApEcuObserver()
    feed(obs, 0.5, lambda t: chassis() + [(0x488, 0), (0x2B9, 0)])
    assert obs.result() == AP1_PLATFORM

  def test_one_ap_address_is_ambiguous(self):
    obs = ApEcuObserver()
    feed(obs, 3, lambda t: chassis() + [(0x488, 2)])
    assert obs.result() is None

  def test_preap_after_sustained_absence(self):
    obs = ApEcuObserver()
    feed(obs, 2, lambda t: chassis())
    assert obs.result() == PREAP_PLATFORM

  def test_no_preap_before_window(self):
    obs = ApEcuObserver()
    feed(obs, (PREAP_MIN_ABSENCE_NS / 1e9) * 0.5, lambda t: chassis())
    assert obs.result() is None

  def test_no_preap_without_full_tesla_chassis(self):
    obs = ApEcuObserver()
    feed(obs, 3, lambda t: [(0x370, 0), (0x108, 0)])
    assert obs.result() is None

  def test_device_echoes_never_count(self):
    # TX echoes come back with src 128 + bus. A Pre-AP device sending 0x488 / 0x2B9
    # must not turn into AP1, and must not block Pre-AP either.
    obs = ApEcuObserver()
    feed(obs, 2, lambda t: chassis() + [(0x488, 128), (0x2B9, 128), (0x488, 130), (0x2B9, 130)])
    assert obs.result() == PREAP_PLATFORM

  def test_radar_bus_ignored(self):
    obs = ApEcuObserver()
    feed(obs, 2, lambda t: chassis() + [(0x2B9, 1), (0x488, 1)])
    assert obs.result() == PREAP_PLATFORM

  def test_late_ap_frame_cancels_preap(self):
    obs = ApEcuObserver()
    feed(obs, 2, lambda t: chassis() + ([(0x2B9, 2)] if t == 1900 else []))
    assert obs.result() is None


class TestSelect:
  def sel(self, **kw):
    args = dict(candidate=None, force_fingerprint=False, toggles_car_model="MOCK", car_model_param=None,
                fixed_fingerprint="", override="auto", nap_type=None, can_result=None)
    args.update(kw)
    return select_candidate(**args)

  def test_fixed_wins(self):
    assert self.sel(fixed_fingerprint="X", override="preap", nap_type=1) == ("X", "fixed")

  def test_override_beats_car_model(self):
    assert self.sel(car_model_param=AP1_PLATFORM, toggles_car_model=AP1_PLATFORM, override="preap") == (PREAP_PLATFORM, "override")
    assert self.sel(override="ap1", can_result=PREAP_PLATFORM) == (AP1_PLATFORM, "override")

  def test_existing_ap1_install_unchanged(self):
    # CarModel set by the user, no override file: identical to the old get_car rule
    # even when NAP import or CAN would say otherwise.
    for nap, can in ((1, PREAP_PLATFORM), (None, None), (2, AP1_PLATFORM)):
      assert self.sel(car_model_param=AP1_PLATFORM, toggles_car_model=AP1_PLATFORM, nap_type=nap, can_result=can) == \
        (AP1_PLATFORM, "existing")
    assert self.sel(candidate="TESLA_AP2_MODELS", nap_type=1) == ("TESLA_AP2_MODELS", "existing")
    assert self.sel(candidate="TESLA_AP2_MODELS", force_fingerprint=True, toggles_car_model=AP1_PLATFORM,
                    car_model_param=AP1_PLATFORM) == (AP1_PLATFORM, "existing")

  def test_nap_type_before_can(self):
    assert self.sel(nap_type=1, can_result=AP1_PLATFORM) == (PREAP_PLATFORM, "nap")
    assert self.sel(nap_type=2, can_result=PREAP_PLATFORM) == (AP1_PLATFORM, "nap")

  def test_nap_auto_uses_can(self):
    assert self.sel(nap_type=0, can_result=PREAP_PLATFORM) == (PREAP_PLATFORM, "can")
    assert self.sel(nap_type=None, can_result=AP1_PLATFORM) == (AP1_PLATFORM, "can")

  def test_no_evidence_keeps_fallback(self):
    assert self.sel() == ("MOCK", "existing")
    assert self.sel(toggles_car_model=None) == ("MOCK", "existing")


class TestFiles:
  def test_override_file(self, tmp_path):
    assert read_profile_override(tmp_path) == "auto"
    (tmp_path / "TeslaCarProfile").write_text("PreAP\n")
    assert read_profile_override(tmp_path) == "preap"
    (tmp_path / "TeslaCarProfile").write_text("ap1")
    assert read_profile_override(tmp_path) == "ap1"
    (tmp_path / "TeslaCarProfile").write_text("ap2")
    assert read_profile_override(tmp_path) == "auto"

  def _nap_params(self, d, **values):
    d.mkdir(parents=True, exist_ok=True)
    for k, v in values.items():
      (d / k).write_text(str(v))

  def test_snapshot_and_load(self, tmp_path):
    params, out = tmp_path / "params", tmp_path / "bp"
    # Synthetic 17-char donor id (not a real VIN pattern check target).
    fake_vin = "TESTDONOR" + "0" * 8
    self._nap_params(params, NAPCarType=1, NAPPedalEnabled=1, NAPPedalCanBus=0, NAPRadarEnabled=1,
                     NAPRadarOffset="0.25", NAPRadarPosition=2, NAPRadarEpasType=3, NAPRadarDonorVin=fake_vin,
                     LongitudinalPersonality=1)
    assert nap_import.snapshot_nap_params(params, out) == "written"
    raw = json.loads((out / "NAPImport.json").read_text())
    assert fake_vin not in json.dumps(raw)
    assert raw["has_radar_donor_vin"] is True
    assert "LongitudinalPersonality" not in raw
    assert nap_import.read_donor_vin(out) == fake_vin
    loaded = nap_import.load_nap_import(out)
    assert loaded["NAPCarType"] == 1 and loaded["NAPPedalCanBus"] == 0 and loaded["NAPRadarOffset"] == 0.25
    assert loaded["NAPFollowDistance"] == 4  # NAP default filled in
    assert nap_import.nap_car_type(loaded) == 1
    # A second snapshot keeps the first one.
    self._nap_params(params, NAPCarType=2)
    assert nap_import.snapshot_nap_params(params, out) == "exists"
    assert nap_import.snapshot_nap_params(params, out, force=True) == "written"
    assert nap_import.load_nap_import(out)["NAPCarType"] == 2

  def test_not_a_nap_device(self, tmp_path):
    assert nap_import.snapshot_nap_params(tmp_path / "none", tmp_path / "bp") == "no-nap-params"
    assert not (tmp_path / "bp" / "NAPImport.json").exists()
    assert nap_import.load_nap_import(tmp_path / "bp") is None
    assert nap_import.nap_car_type(None) is None

  def test_bad_values(self, tmp_path):
    params, out = tmp_path / "params", tmp_path / "bp"
    self._nap_params(params, NAPCarType=7, NAPPedalEnabled="maybe", NAPRadarDonorVin="short")
    assert nap_import.snapshot_nap_params(params, out) == "written"
    loaded = nap_import.load_nap_import(out)
    assert nap_import.nap_car_type(loaded) == 0  # unknown means Auto, as in NAP
    assert loaded["NAPPedalEnabled"] is False
    assert loaded["has_radar_donor_vin"] is False
    assert not (out / "TeslaRadarDonorVin").exists()
    (out / "NAPImport.json").write_text("{not json")
    assert nap_import.load_nap_import(out) is None

  def test_cli_never_fails(self, tmp_path, capsys):
    assert nap_import.main(["--snapshot", "--params-dir", str(tmp_path / "x"), "--out-dir", str(tmp_path / "y")]) == 0
    assert "nap_import: no-nap-params" in capsys.readouterr().out

  def test_resolve_live(self, tmp_path):
    obs = ApEcuObserver()
    feed(obs, 2, lambda t: chassis())
    assert resolve_live(None, False, "MOCK", None, "", obs, tmp_path) == (PREAP_PLATFORM, "can")
    (tmp_path / "NAPImport.json").write_text(json.dumps({"version": 1, "NAPCarType": 2}))
    assert resolve_live(None, False, "MOCK", None, "", obs, tmp_path) == (AP1_PLATFORM, "nap")
    (tmp_path / "TeslaCarProfile").write_text("preap")
    assert resolve_live(None, False, "MOCK", None, "", obs, tmp_path) == (PREAP_PLATFORM, "override")


@pytest.mark.parametrize("seen,expected", [
  ({0: {0x488: 4, 0x2B9: 8}}, AP1_PLATFORM),
  ({2: {0x488: 4}, 0: {0x2B9: 8}}, AP1_PLATFORM),
])
def test_nap_detect_cases_ap1(seen, expected):
  # Same AP1 cases as NAP tests/test_nap_detect.py, fed as live frames.
  obs = ApEcuObserver()
  frames = [(a, b) for b, addrs in seen.items() for a in addrs]
  feed(obs, 0.2, lambda t: chassis() + frames)
  assert obs.result() == expected
