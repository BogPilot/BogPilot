"""Unit tests for BogPilot last-known wall-clock restore/save."""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from openpilot.common import bogpilot_clock as bc



# Fixed "real" time well after min_date (AGNOS epoch is ~2023 / systemd mtime).
_REAL_EPOCH = datetime.datetime(2026, 10, 6, 18, 30, 0).timestamp()
_BOGUS_EPOCH = datetime.datetime(2023, 11, 1, 0, 0, 0).timestamp()


@pytest.fixture(autouse=True)
def _reset_timer(tmp_path, monkeypatch):
  path = tmp_path / "LastKnownTime"
  monkeypatch.setattr(bc, "_last_known_path", path)
  bc.reset_save_timer_for_tests()
  yield
  bc.reset_save_timer_for_tests()


def test_missing_file_restore_is_noop(tmp_path):
  path = tmp_path / "LastKnownTime"
  set_fn = MagicMock()
  assert bc.maybe_restore_last_known_time(
    path=path, now_epoch=_BOGUS_EPOCH, time_valid=False, set_time_fn=set_fn,
  ) is False
  set_fn.assert_not_called()


def test_bogus_epoch_restores_from_file(tmp_path):
  path = tmp_path / "LastKnownTime"
  assert bc.write_last_known_time(_REAL_EPOCH, path=path)
  set_fn = MagicMock()
  assert bc.maybe_restore_last_known_time(
    path=path, now_epoch=_BOGUS_EPOCH, time_valid=False, set_time_fn=set_fn,
  ) is True
  set_fn.assert_called_once()
  got = set_fn.call_args[0][0]
  assert isinstance(got, datetime.datetime)
  # Aware UTC, so timed.set_time never reads it as local time.
  assert got.utcoffset() == datetime.timedelta(0)
  assert abs(got.timestamp() - _REAL_EPOCH) < 2


def test_already_correct_is_noop(tmp_path):
  path = tmp_path / "LastKnownTime"
  # Saved a few minutes behind "now"; valid clock at/ahead of saved -> no-op.
  assert bc.write_last_known_time(_REAL_EPOCH - 120, path=path)
  set_fn = MagicMock()
  assert bc.maybe_restore_last_known_time(
    path=path, now_epoch=_REAL_EPOCH, time_valid=True, set_time_fn=set_fn,
  ) is False
  set_fn.assert_not_called()


def test_valid_but_far_behind_restores(tmp_path):
  path = tmp_path / "LastKnownTime"
  assert bc.write_last_known_time(_REAL_EPOCH, path=path)
  set_fn = MagicMock()
  behind = _REAL_EPOCH - 600
  assert bc.maybe_restore_last_known_time(
    path=path, now_epoch=behind, time_valid=True, set_time_fn=set_fn,
  ) is True
  set_fn.assert_called_once()


def test_implausible_saved_is_noop(tmp_path):
  path = tmp_path / "LastKnownTime"
  old_epoch = datetime.datetime(2020, 1, 1).timestamp()
  path.write_text(f"{int(old_epoch)}\n")
  set_fn = MagicMock()
  assert bc.maybe_restore_last_known_time(
    path=path, now_epoch=_BOGUS_EPOCH, time_valid=False, set_time_fn=set_fn,
  ) is False
  set_fn.assert_not_called()


def test_save_noop_when_invalid(tmp_path):
  path = tmp_path / "LastKnownTime"
  assert bc.maybe_save_last_known_time(
    path=path, now_epoch=_BOGUS_EPOCH, mono=0.0, time_valid=False,
  ) is False
  assert not path.exists()


def test_save_writes_and_rate_limits(tmp_path):
  path = tmp_path / "LastKnownTime"
  assert bc.maybe_save_last_known_time(
    path=path, now_epoch=_REAL_EPOCH, mono=100.0, time_valid=True,
  ) is True
  assert path.is_file()
  first = path.read_text().strip()
  assert bc.maybe_save_last_known_time(
    path=path, now_epoch=_REAL_EPOCH + 10, mono=100.0 + 30, time_valid=True,
  ) is False
  assert path.read_text().strip() == first
  assert bc.maybe_save_last_known_time(
    path=path, now_epoch=_REAL_EPOCH + 400, mono=100.0 + bc.SAVE_INTERVAL_S + 1, time_valid=True,
  ) is True
  assert int(path.read_text().strip()) == int(_REAL_EPOCH + 400)


def test_force_save_bypasses_interval(tmp_path):
  path = tmp_path / "LastKnownTime"
  assert bc.maybe_save_last_known_time(
    path=path, now_epoch=_REAL_EPOCH, mono=1.0, time_valid=True,
  ) is True
  assert bc.maybe_save_last_known_time(
    force=True, path=path, now_epoch=_REAL_EPOCH + 5, mono=2.0, time_valid=True,
  ) is True
  assert int(path.read_text().strip()) == int(_REAL_EPOCH + 5)




def test_should_restore_matrix():
  assert bc.should_restore(_BOGUS_EPOCH, None, time_valid=False) is False
  assert bc.should_restore(_BOGUS_EPOCH, _REAL_EPOCH, time_valid=False) is True
  assert bc.should_restore(_REAL_EPOCH, _REAL_EPOCH - 30, time_valid=True) is False
  assert bc.should_restore(_REAL_EPOCH - 600, _REAL_EPOCH, time_valid=True) is True


def test_iso_fallback_read(tmp_path):
  path = tmp_path / "LastKnownTime"
  path.write_text("2026-10-06T18:30:00Z\n")
  got = bc.read_last_known_time(path)
  assert got is not None
  assert got.year == 2026 and got.month == 10 and got.day == 6


def test_timed_source_uses_abs_diff():
  """Regression: one-sided diff cannot advance a 2023 clock from GPS.

  Upstream timed.set_time uses abs(diff); without it, a restored-but-slightly-
  stale clock (or the AGNOS epoch) can never be advanced by GPS.
  """
  src = open("system/timed.py", encoding="utf-8").read()
  assert "abs(diff)" in src
  assert "maybe_save_last_known_time" in src
  # Pure abs-threshold behavior (mirrors set_time guard).
  behind = datetime.timedelta(days=1000)
  ahead = datetime.timedelta(seconds=3)
  assert abs(behind) >= datetime.timedelta(seconds=10)
  assert abs(ahead) < datetime.timedelta(seconds=10)


def test_manager_restores_before_bootlog():
  src = open("system/manager/manager.py", encoding="utf-8").read()
  assert "maybe_restore_last_known_time" in src
  assert src.index("maybe_restore_last_known_time") < src.index("save_bootlog()")


def test_event_save_writes_without_interval(tmp_path):
  path = tmp_path / "LastKnownTime"
  # Periodic save just ran; an event save right after must still write.
  assert bc.maybe_save_last_known_time(
    path=path, now_epoch=_REAL_EPOCH, mono=1.0, time_valid=True,
  ) is True
  assert bc.save_last_known_time_now(
    path=path, now_epoch=_REAL_EPOCH + 30, time_valid=True,
  ) is True
  assert int(path.read_text().strip()) == int(_REAL_EPOCH + 30)


def test_event_save_noop_when_clock_invalid(tmp_path):
  path = tmp_path / "LastKnownTime"
  assert bc.write_last_known_time(_REAL_EPOCH, path=path)
  assert bc.save_last_known_time_now(
    path=path, now_epoch=_BOGUS_EPOCH, time_valid=False,
  ) is False
  assert int(path.read_text().strip()) == int(_REAL_EPOCH)


def test_event_save_rejects_bogus_epoch_even_if_marked_valid(tmp_path):
  path = tmp_path / "LastKnownTime"
  assert bc.write_last_known_time(_REAL_EPOCH, path=path)
  assert bc.save_last_known_time_now(
    path=path, now_epoch=datetime.datetime(2020, 1, 1).timestamp(), time_valid=True,
  ) is False
  assert int(path.read_text().strip()) == int(_REAL_EPOCH)


def test_event_save_never_goes_backwards(tmp_path):
  path = tmp_path / "LastKnownTime"
  assert bc.write_last_known_time(_REAL_EPOCH, path=path)
  assert bc.save_last_known_time_now(
    path=path, now_epoch=_REAL_EPOCH - 3600, time_valid=True,
  ) is False
  assert int(path.read_text().strip()) == int(_REAL_EPOCH)


def test_event_save_replaces_corrupt_file(tmp_path):
  path = tmp_path / "LastKnownTime"
  path.write_text("garbage\n")
  assert bc.save_last_known_time_now(
    path=path, now_epoch=_REAL_EPOCH, time_valid=True,
  ) is True
  assert int(path.read_text().strip()) == int(_REAL_EPOCH)


def test_manager_saves_on_offroad_and_exit():
  src = open("system/manager/manager.py", encoding="utf-8").read()
  assert "save_last_known_time_now" in src
  offroad = src.index("elif not started and started_prev:")
  assert src.index("save_last_known_clock()", offroad) < src.index("write_onroad_params(started, params)", offroad)
  cleanup = src.index("    manager_cleanup()\n    # BogPilot")
  assert src.index("save_last_known_clock()", cleanup) < src.index("    HARDWARE.shutdown()\n", cleanup)
