#!/usr/bin/env python3
"""Canonical m/s speed settings and the one-time display-unit migration."""
import unittest

from openpilot.common.conversions import Conversions as CV
from openpilot.common.params import Params
from openpilot.frogpilot.common.frogpilot_variables import (
  SPEED_PARAMS_IN_MS,
  display_speed_steps,
  migrate_speed_params_to_ms,
)


class TestSpeedParamsMs(unittest.TestCase):
  def test_metric_flip_keeps_the_same_physical_offset(self):
    stored = 5 * CV.MPH_TO_MS
    self.assertEqual(display_speed_steps(stored, metric=False), 5)
    self.assertEqual(display_speed_steps(stored, metric=True), 8)
    # A second flip reads the same stored m/s, so 5 mph does not become 4.
    self.assertEqual(display_speed_steps(stored, metric=False), 5)

  def test_truncated_kmh_default_still_reads_as_five_mph(self):
    # The old Metric toggle stored int(5 * 1.609344) == 8 km/h.
    stored = 8 * CV.KPH_TO_MS
    self.assertEqual(display_speed_steps(stored, metric=True), 8)
    self.assertEqual(display_speed_steps(stored, metric=False), 5)

  def test_migrate_mph_once(self):
    params = Params(str(self._tmp() / "params"))
    params.put("IsMetric", "0")
    params.put("Offset1", "5")
    params.put("Offset7", "10")
    params.put("CustomCruise", "1")
    params.put("CESignalSpeed", "55")
    marker = self._tmp() / "SpeedParamsStoredInMS"
    self.assertTrue(migrate_speed_params_to_ms(params, marker_path=marker))
    self.assertAlmostEqual(params.get_float("Offset1"), 5 * CV.MPH_TO_MS, places=4)
    self.assertAlmostEqual(params.get_float("Offset7"), 10 * CV.MPH_TO_MS, places=4)
    self.assertAlmostEqual(params.get_float("CustomCruise"), CV.MPH_TO_MS, places=4)
    self.assertAlmostEqual(params.get_float("CESignalSpeed"), 55 * CV.MPH_TO_MS, places=4)
    self.assertFalse(migrate_speed_params_to_ms(params, marker_path=marker))
    self.assertAlmostEqual(params.get_float("Offset1"), 5 * CV.MPH_TO_MS, places=4)

  def test_migrate_kmh_and_leaves_missing_keys(self):
    params = Params(str(self._tmp() / "params"))
    params.put("IsMetric", "1")
    params.put("Offset1", "8")
    params.put("SetSpeedOffset", "0")
    marker = self._tmp() / "SpeedParamsStoredInMS"
    self.assertTrue(migrate_speed_params_to_ms(params, marker_path=marker))
    self.assertAlmostEqual(params.get_float("Offset1"), 8 * CV.KPH_TO_MS, places=4)
    self.assertAlmostEqual(params.get_float("SetSpeedOffset"), 0.0, places=4)
    self.assertIsNone(params.get("CESpeed"))
    self.assertEqual(display_speed_steps(params.get_float("Offset1"), metric=False), 5)

  def test_speed_keys_cover_the_old_rescale_list(self):
    self.assertEqual(
      set(SPEED_PARAMS_IN_MS),
      {
        "CESignalSpeed", "CESpeed", "CESpeedLead",
        "CustomCruise", "CustomCruiseLong",
        "MinimumLaneChangeSpeed",
        "Offset1", "Offset2", "Offset3", "Offset4", "Offset5", "Offset6", "Offset7",
        "PauseAOLOnBrake", "PauseLateralSpeed",
        "SetSpeedOffset",
      },
    )

  def _tmp(self):
    import tempfile
    from pathlib import Path
    if not hasattr(self, "_tmpdir"):
      self._tmpdir = tempfile.TemporaryDirectory()
      self.addCleanup(self._tmpdir.cleanup)
    return Path(self._tmpdir.name)


if __name__ == "__main__":
  unittest.main()
