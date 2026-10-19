"""BogPilot last-known wall-clock persistence.

comma devices lose their RTC when powered off and boot into a fixed AGNOS /
systemd epoch (often ~Nov 2023) until GPS or NTP sets the clock. Route
directory names and rlog wall-clock stamps then collide across boots.

This module:
- Saves the current UTC unix time to /data/params_bogpilot/LastKnownTime about
  every 5 minutes once the system clock is valid (same durable dir as other
  BogPilot file-backed prefs; July params_pyx.so cannot store new Params keys).
- Also saves once on each onroad->offroad transition and when manager exits
  (clean shutdown / reboot / SIGTERM), so the restored time is close to when
  the device actually went down. The periodic save stays as a backup for a
  sudden power loss with no clean exit.
- At manager boot, if the clock is still on the bogus epoch (or far behind the
  saved stamp), restores from that file so logs start near the last drive.

GPS/NTP via system.timed still correct afterward. We never overwrite a clock
that is already valid and not behind the saved stamp. Device-global (not
car-specific). First boot after install stays on the bogus epoch until one
drive (or any valid-time period) saves a stamp.
"""
from __future__ import annotations

import datetime
import os
import time
from pathlib import Path

from openpilot.common.time import min_date, system_time_valid

LAST_KNOWN_DIR = Path("/data/params_bogpilot")
LAST_KNOWN_PATH = LAST_KNOWN_DIR / "LastKnownTime"
# Tests may override the path via this module attribute.
_last_known_path = LAST_KNOWN_PATH

SAVE_INTERVAL_S = 5 * 60
# Restore when a "valid" clock is still this far behind the saved stamp.
BEHIND_TOLERANCE_S = 60.0
# Reject saved stamps past this year (corrupt / mistyped file).
_MAX_YEAR = 2099

_last_save_mono: float | None = None


def read_last_known_epoch(path: Path | None = None) -> float | None:
  """Return saved unix epoch seconds (UTC), or None if missing/invalid."""
  target = path if path is not None else _last_known_path
  try:
    if not target.is_file():
      return None
    text = target.read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    return None
  if not text:
    return None
  try:
    epoch = float(text.split()[0])
    if epoch < 0:
      return None
    return epoch
  except (ValueError, OverflowError):
    pass
  try:
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    dt = datetime.datetime.fromisoformat(iso)
    if dt.tzinfo is not None:
      dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return (dt - datetime.datetime(1970, 1, 1)).total_seconds()
  except ValueError:
    return None


def read_last_known_time(path: Path | None = None) -> datetime.datetime | None:
  """Return the saved UTC-naive datetime, or None if missing/invalid."""
  epoch = read_last_known_epoch(path)
  if epoch is None:
    return None
  try:
    return datetime.datetime.utcfromtimestamp(epoch)
  except (ValueError, OverflowError, OSError):
    return None


def write_last_known_time(
  when: datetime.datetime | float | None = None,
  path: Path | None = None,
) -> bool:
  """Atomically write unix-epoch seconds (UTC) to the last-known file."""
  target = path if path is not None else _last_known_path
  if when is None:
    epoch = time.time()
  elif isinstance(when, (int, float)):
    epoch = float(when)
  else:
    if when.tzinfo is not None:
      when = when.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    epoch = (when - datetime.datetime(1970, 1, 1)).total_seconds()
  try:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
      f.write(f"{int(epoch)}\n")
      f.flush()
      # Durable before a poweroff that may follow right after this save.
      os.fsync(f.fileno())
    os.replace(tmp, target)
    return True
  except OSError:
    return False


def saved_epoch_plausible(epoch: float) -> bool:
  """True if saved stamp is after min_date and not absurdly far ahead."""
  try:
    saved = datetime.datetime.utcfromtimestamp(epoch)
  except (ValueError, OverflowError, OSError):
    return False
  # min_date() is local-naive from systemd mtime; compare via local fromtimestamp
  # of the same epoch so the bogus-epoch floor matches system_time_valid().
  try:
    saved_local = datetime.datetime.fromtimestamp(epoch)
  except (ValueError, OverflowError, OSError):
    return False
  if saved_local <= min_date():
    return False
  if saved.year > _MAX_YEAR:
    return False
  return True


def should_restore(now_epoch: float, saved_epoch: float | None, *, time_valid: bool | None = None) -> bool:
  """Whether to set the system clock from the saved stamp.

  - Missing / implausible saved -> no-op
  - Bogus epoch (!system_time_valid) -> restore
  - Valid now but far behind saved -> restore
  - Valid now at/ahead of saved -> no-op (GPS/NTP already won)
  """
  if saved_epoch is None or not saved_epoch_plausible(saved_epoch):
    return False
  valid = system_time_valid() if time_valid is None else time_valid
  if not valid:
    return True
  return now_epoch < (saved_epoch - BEHIND_TOLERANCE_S)


def maybe_restore_last_known_time(
  *,
  path: Path | None = None,
  now_epoch: float | None = None,
  time_valid: bool | None = None,
  set_time_fn=None,
) -> bool:
  """Restore the system clock from the last-known file if needed.

  Returns True if a restore was attempted (set_time_fn called). Never raises.
  set_time_fn defaults to system.timed.set_time (abs-diff; GPS can still win).
  """
  try:
    saved_epoch = read_last_known_epoch(path)
    current_epoch = time.time() if now_epoch is None else now_epoch
    if not should_restore(current_epoch, saved_epoch, time_valid=time_valid):
      return False
    assert saved_epoch is not None
    saved_dt = datetime.datetime.utcfromtimestamp(saved_epoch)
    if set_time_fn is None:
      from openpilot.system.timed import set_time
      set_time_fn = set_time
    # TZ=UTC date -s interprets this naive datetime as UTC.
    set_time_fn(saved_dt)
    return True
  except Exception:
    return False


def maybe_save_last_known_time(
  *,
  force: bool = False,
  path: Path | None = None,
  now_epoch: float | None = None,
  mono: float | None = None,
  time_valid: bool | None = None,
) -> bool:
  """Save the current time if valid and the save interval has elapsed.

  Returns True if a write happened. No-op when the clock is still bogus.
  """
  global _last_save_mono
  try:
    valid = system_time_valid() if time_valid is None else time_valid
    if not valid:
      return False
    t_mono = time.monotonic() if mono is None else mono
    if not force and _last_save_mono is not None:
      if (t_mono - _last_save_mono) < SAVE_INTERVAL_S:
        return False
    epoch = time.time() if now_epoch is None else now_epoch
    if not saved_epoch_plausible(epoch):
      return False
    if not write_last_known_time(epoch, path=path):
      return False
    _last_save_mono = t_mono
    return True
  except Exception:
    return False


def save_last_known_time_now(
  *,
  path: Path | None = None,
  now_epoch: float | None = None,
  time_valid: bool | None = None,
) -> bool:
  """Event save (going offroad / manager exit): write now, ignoring the interval.

  Same sanity gates as the periodic save (clock valid, past the min_date
  floor, not absurdly far ahead). Additionally never replaces a plausible
  saved stamp with an older one, so a clock that went backwards cannot drag
  the fallback back. Returns True if a write happened. Never raises.
  """
  try:
    valid = system_time_valid() if time_valid is None else time_valid
    if not valid:
      return False
    epoch = time.time() if now_epoch is None else now_epoch
    if not saved_epoch_plausible(epoch):
      return False
    saved = read_last_known_epoch(path)
    if saved is not None and saved_epoch_plausible(saved) and epoch < saved:
      return False
    return write_last_known_time(epoch, path=path)
  except Exception:
    return False


def reset_save_timer_for_tests() -> None:
  """Clear the in-process save interval (tests only)."""
  global _last_save_mono
  _last_save_mono = None
