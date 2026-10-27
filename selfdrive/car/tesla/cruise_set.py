"""AP1 DI_cruiseSet scaling fix (file-backed, default ON).

tesla_can.dbc declares DI_state.DI_cruiseSet (0x368, bit 32, 9 bits) with
factor 0.5. On BogPilot's AP1 Model S the raw value is whole display units:
in MPH mode raw == DI_analogSpeed (mph) in standby, and under the OP overlay
it echoes the DAS_setSpeed OP sends. With factor 0.5, carState.cruiseState.speed
and controlsState.vCruise read half of the real value, and frogpilot_vcruise
receives that halved value as its v_cruise input (csc_target fallback, SLC
update_limits/override, the min() merge and the AP1 tip/raise set_hint when no
sticky/tip is active).

  # default ON when the file is missing
  echo 0 > /data/params_bogpilot/TeslaCruiseSetScaleFix   # off: keep DBC 0.5
  echo 1 > /data/params_bogpilot/TeslaCruiseSetScaleFix   # on: x2 (raw units)
  rm /data/params_bogpilot/TeslaCruiseSetScaleFix          # on (default)

Missing/empty = ON (x2). Explicit 0/false/off/no = OFF (stock factor 0.5).
Read once when CarState is constructed. KPH-mode scaling is unverified on car.

Not a product. No warranty. The driver remains responsible. Comply with local law.
"""
from __future__ import annotations

from pathlib import Path

CRUISE_SET_SCALE_FIX_PATH = Path("/data/params_bogpilot/TeslaCruiseSetScaleFix")
# Tests may override the path via this module attribute.
_cruise_set_scale_fix_path = CRUISE_SET_SCALE_FIX_PATH

# DBC factor is 0.5; the AP1 raw value is whole units, so undo the halving.
DBC_FACTOR_CORRECTION = 2.0


def cruise_set_scale_fix_enabled(stored: str | bytes | None = None) -> bool:
  """Missing/empty file = ON. Explicit 0/false/off/no = OFF. 1/true/on/yes = ON."""
  if stored is None:
    try:
      p = _cruise_set_scale_fix_path
      if not p.is_file():
        return True
      text = p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
      return True
  else:
    text = (stored.decode(errors="replace") if isinstance(stored, bytes) else str(stored)).strip()
  if not text:
    return True
  low = text.lower()
  if low in ("0", "false", "off", "no"):
    return False
  return low in ("1", "true", "on", "yes")


def cruise_set_scale(enabled: bool) -> float:
  """Multiplier applied to the DBC-decoded DI_cruiseSet value."""
  return DBC_FACTOR_CORRECTION if enabled else 1.0
