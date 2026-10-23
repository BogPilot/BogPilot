"""timed: GPS time is compared and set in UTC regardless of the device timezone.

Regression for the clock landing exactly the UTC offset behind (4 h in EDT) when GPS
corrected it: GPS ms were turned into a local-naive datetime and handed to
`TZ=UTC date -s`. Seen in rlogs as clocks.wallTimeNanos - gps unixTimestampMillis = -14400 s.
"""
import datetime
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

for _m in ("timezonefinder", "cereal.messaging", "openpilot.common.params", "openpilot.common.swaglog"):
  sys.modules.setdefault(_m, MagicMock())

from openpilot.system import timed  # noqa: E402
from openpilot.common import bogpilot_clock as bc  # noqa: E402

UTC = datetime.timezone.utc
# 2026-10-18 23:28:22 UTC == 19:28:22 EDT
GPS_MS = int(datetime.datetime(2026, 10, 18, 23, 28, 22, 500000, tzinfo=UTC).timestamp() * 1000)


@pytest.fixture(params=["America/New_York", "UTC", "Asia/Kolkata"])
def device_tz(request):
  old = os.environ.get("TZ")
  os.environ["TZ"] = request.param
  time.tzset()
  yield request.param
  if old is None:
    os.environ.pop("TZ", None)
  else:
    os.environ["TZ"] = old
  time.tzset()


def _parse_cmd(cmd):
  assert cmd.startswith("TZ=UTC date -s '") and cmd.endswith("'")
  s = cmd[len("TZ=UTC date -s '"):-1]
  return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)


def test_gps_time_is_utc_in_any_timezone(device_tz):
  t = timed.gps_time_from_llk(GPS_MS)
  assert t.utcoffset() == datetime.timedelta(0)
  assert (t.hour, t.minute, t.second) == (23, 28, 22)
  assert abs(t.timestamp() - GPS_MS / 1000) < 1e-6


def test_date_command_sets_true_utc(device_tz):
  cmd = timed.date_set_command(timed.gps_time_from_llk(GPS_MS))
  assert abs(_parse_cmd(cmd).timestamp() - GPS_MS / 1000) < 1e-3


def test_old_formula_was_offset_by_local_utc_offset():
  # Document the bug: local-naive time interpreted as UTC is off by the UTC offset.
  old = os.environ.get("TZ")
  os.environ["TZ"] = "America/New_York"
  time.tzset()
  try:
    local_naive = datetime.datetime.fromtimestamp(GPS_MS / 1000.)
    as_utc = local_naive.replace(tzinfo=UTC).timestamp()
    assert round(as_utc - GPS_MS / 1000) == -4 * 3600
    new = _parse_cmd(timed.date_set_command(timed.gps_time_from_llk(GPS_MS))).timestamp()
    assert abs(new - GPS_MS / 1000) < 1e-3
  finally:
    if old is None:
      os.environ.pop("TZ", None)
    else:
      os.environ["TZ"] = old
    time.tzset()


def test_set_time_noop_when_clock_already_correct(device_tz, monkeypatch):
  run = MagicMock()
  monkeypatch.setattr(timed.subprocess, "run", run)
  gps = timed.gps_time_from_llk(GPS_MS)
  timed.set_time(gps, now=gps + datetime.timedelta(seconds=3))
  run.assert_not_called()


@pytest.mark.parametrize("offset_s", [-4 * 3600, -34 * 60, -3 * 365 * 86400, 120])
def test_set_time_corrects_wrong_clock_to_gps(device_tz, monkeypatch, offset_s):
  run = MagicMock()
  monkeypatch.setattr(timed.subprocess, "run", run)
  gps = timed.gps_time_from_llk(GPS_MS)
  timed.set_time(gps, now=gps + datetime.timedelta(seconds=offset_s))
  run.assert_called_once()
  assert abs(_parse_cmd(run.call_args[0][0]).timestamp() - GPS_MS / 1000) < 1e-3


def test_clock_already_4h_behind_is_fixed_not_kept(monkeypatch):
  # A device left 4 h behind by the old code: GPS must now pull it forward to true UTC.
  run = MagicMock()
  monkeypatch.setattr(timed.subprocess, "run", run)
  gps = timed.gps_time_from_llk(GPS_MS)
  timed.set_time(gps, now=gps - datetime.timedelta(hours=4))
  assert abs(_parse_cmd(run.call_args[0][0]).timestamp() - GPS_MS / 1000) < 1e-3


def test_naive_input_is_treated_as_utc(device_tz):
  naive = datetime.datetime(2026, 10, 18, 23, 28, 22, 500000)
  assert abs(_parse_cmd(timed.date_set_command(naive)).timestamp() - GPS_MS / 1000) < 1e-3


def test_last_known_restore_sets_saved_utc(device_tz, tmp_path, monkeypatch):
  # LastKnownTime fallback -> real timed.set_time -> date -s gets the saved epoch exactly.
  path = tmp_path / "LastKnownTime"
  saved = GPS_MS // 1000
  assert bc.write_last_known_time(saved, path=path)
  run = MagicMock()
  monkeypatch.setattr(timed.subprocess, "run", run)
  bogus_now = datetime.datetime(2023, 11, 1, tzinfo=UTC).timestamp()
  assert bc.maybe_restore_last_known_time(path=path, now_epoch=bogus_now, time_valid=False,
                                          set_time_fn=timed.set_time) is True
  run.assert_called_once()
  assert abs(_parse_cmd(run.call_args[0][0]).timestamp() - saved) < 1e-3


def test_saved_stamp_is_utc_epoch(device_tz, tmp_path):
  # Shutdown / periodic saves write time.time() (UTC epoch); reading it back is tz-independent.
  path = tmp_path / "LastKnownTime"
  assert bc.write_last_known_time(GPS_MS / 1000, path=path)
  assert bc.read_last_known_epoch(path) == GPS_MS // 1000
  got = bc.read_last_known_time(path)
  assert (got.hour, got.minute, got.second) == (23, 28, 22)


def test_timed_main_uses_utc_gps_helper():
  src = open("system/timed.py", encoding="utf-8").read()
  assert "gps_time_from_llk(llk.unixTimestampMillis)" in src
  assert "fromtimestamp(llk.unixTimestampMillis / 1000.)" not in src
