"""PreAP* files win over NAPImport.json; TeslaProfileStatus has no identifiers."""
import json
import tempfile
import unittest
from pathlib import Path

from openpilot.selfdrive.car.tesla.preap_settings import (STATUS_FILE, load_preap_settings, profile_status,
                                                         write_profile_status)


class TestPreapSettings(unittest.TestCase):
  def setUp(self):
    self._tmp = tempfile.TemporaryDirectory()
    self.d = Path(self._tmp.name)

  def tearDown(self):
    self._tmp.cleanup()

  def _nap(self, **kw):
    (self.d / "NAPImport.json").write_text(json.dumps({"version": 1, **kw}))

  def test_defaults(self):
    v, s = load_preap_settings(self.d)
    self.assertFalse(v["pedal_enabled"])
    self.assertEqual(v["pedal_can_bus"], 2)
    self.assertEqual(v["radar_epas_type"], 0)
    self.assertTrue(all(x == "default" for x in s.values()))

  def test_nap_import_fallback(self):
    self._nap(NAPPedalEnabled=True, NAPPedalCanBus=0, NAPRadarEnabled=True, NAPRadarEpasType=3)
    v, s = load_preap_settings(self.d)
    self.assertTrue(v["pedal_enabled"])
    self.assertEqual(v["pedal_can_bus"], 0)
    self.assertEqual(v["radar_epas_type"], 3)
    self.assertEqual(s["pedal_can_bus"], "nap_import")

  def test_preap_files_win(self):
    self._nap(NAPPedalEnabled=True, NAPPedalCanBus=0, NAPRadarPosition=2)
    (self.d / "PreAPPedalEnabled").write_text("0")
    (self.d / "PreAPPedalCanBus").write_text("2\n")
    (self.d / "PreAPRadarOffset").write_text("0.35")
    (self.d / "PreAPPedalCalibFactor").write_text("1.5")
    v, s = load_preap_settings(self.d)
    self.assertFalse(v["pedal_enabled"])
    self.assertEqual(v["pedal_can_bus"], 2)
    self.assertAlmostEqual(v["radar_offset"], 0.35)
    self.assertAlmostEqual(v["pedal_calib_factor"], 1.5)
    self.assertEqual(v["radar_position"], 2)
    self.assertEqual(s["pedal_enabled"], "preap_file")
    self.assertEqual(s["radar_position"], "nap_import")

  def test_bad_file_falls_through(self):
    self._nap(NAPPedalCanBus=0, NAPRadarEpasType=1)
    (self.d / "PreAPPedalCanBus").write_text("1")      # not 0|2
    (self.d / "PreAPRadarEpasType").write_text("9")    # out of 0..4
    (self.d / "PreAPPedalEnabled").write_text("maybe")
    v, s = load_preap_settings(self.d)
    self.assertEqual(v["pedal_can_bus"], 0)
    self.assertEqual(v["radar_epas_type"], 1)
    self.assertFalse(v["pedal_enabled"])
    self.assertNotEqual(s["pedal_enabled"], "preap_file")

  def test_status_preap(self):
    v, _ = load_preap_settings(self.d)
    v = {**v, "pedal_enabled": True, "radar_enabled": True, "radar_epas_type": 2}
    st = profile_status("TESLA_PREAP_MODELS", "can", v, pedal_bus_learned=0)
    self.assertEqual(st["profile"], "preap")
    self.assertEqual(st["source"], "auto")
    self.assertIn("bus 0 (learned)", st["pedal"])
    self.assertIn("epas type 2", st["radar"])
    self.assertEqual(set(st), {"profile", "source", "reason", "pedal", "radar"})

  def test_status_ap1_and_unknown(self):
    self.assertEqual(profile_status("TESLA_AP1_MODELS", "existing")["source"], "carmodel")
    self.assertEqual(profile_status("TESLA_AP1_MODELS", "override")["source"], "manual")
    self.assertEqual(profile_status("TESLA_AP1_MODELS", "nap")["source"], "nap")
    st = profile_status("MOCK", "existing")
    self.assertEqual(st["profile"], "unknown")
    self.assertEqual(st["pedal"], "n/a")

  def test_write_status(self):
    st = profile_status("TESLA_PREAP_MODELS", "nap", load_preap_settings(self.d)[0])
    self.assertTrue(write_profile_status(st, self.d))
    self.assertEqual(json.loads((self.d / STATUS_FILE).read_text()), st)
    self.assertFalse((self.d / (STATUS_FILE + ".tmp")).exists())


if __name__ == "__main__":
  unittest.main()
