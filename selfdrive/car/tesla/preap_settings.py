"""Pre-AP Tesla settings and the TeslaProfileStatus file.

Copyright (c) 2026 BogPilot contributors. MIT License.
Setting names and ranges follow NotAutopilot (github.com/NotAutopilot/openpilot,
MIT, nap-release e99f4dc / opendbc cc39c7f): NAPPedalEnabled, NAPPedalCanBus,
NAPRadar*, which came from Tinkla (BogGyver, MIT).

Read order for each setting (first valid value wins):
  1. /data/params_bogpilot/PreAP<Name>  plain text written by the BogPilot
     first-boot import (frogpilot/system/nap_migration.py) or by the owner
  2. /data/params_bogpilot/NAPImport.json  (nap_import.py snapshot)
  3. NAP default
An out-of-range or unparsable value at one level falls through to the next.

Stdlib only. No identifiers are read or written here (the radar donor VIN is
read only by the radar sender, from TeslaRadarDonorVin).
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

from openpilot.selfdrive.car.tesla.nap_import import DEFAULT_OUT_DIR, load_nap_import

STATUS_FILE = "TeslaProfileStatus"


@dataclass(frozen=True)
class Setting:
  name: str
  preap_file: str
  nap_key: str | None
  kind: str            # bool | int | float
  default: object
  lo: float | None = None
  hi: float | None = None
  allowed: tuple | None = None


SETTINGS: tuple[Setting, ...] = (
  Setting("pedal_enabled", "PreAPPedalEnabled", "NAPPedalEnabled", "bool", False),
  Setting("pedal_can_bus", "PreAPPedalCanBus", "NAPPedalCanBus", "int", 2, allowed=(0, 2)),
  Setting("pedal_calib_done", "PreAPPedalCalibDone", None, "bool", False),
  Setting("pedal_calib_min", "PreAPPedalCalibMin", None, "float", None, -50.0, 150.0),
  Setting("pedal_calib_max", "PreAPPedalCalibMax", None, "float", None, -50.0, 150.0),
  Setting("pedal_calib_zero", "PreAPPedalCalibZero", None, "float", None, -50.0, 150.0),
  Setting("pedal_calib_factor", "PreAPPedalCalibFactor", None, "float", None, 0.001, 100.0),
  Setting("adaptive_accel", "PreAPAdaptiveAccel", None, "bool", False),
  Setting("radar_enabled", "PreAPRadarEnabled", "NAPRadarEnabled", "bool", False),
  Setting("radar_behind_nosecone", "PreAPRadarBehindNosecone", "NAPRadarBehindNosecone", "bool", False),
  Setting("radar_offset", "PreAPRadarOffset", "NAPRadarOffset", "float", 0.0, -5.0, 5.0),
  Setting("radar_hud", "PreAPRadarHud", None, "bool", False),
  Setting("radar_upside_down", "PreAPRadarUpsideDown", "NAPRadarUpsideDown", "bool", False),
  Setting("radar_epas_type", "PreAPRadarEpasType", "NAPRadarEpasType", "int", 0, 0, 4),
  Setting("radar_position", "PreAPRadarPosition", "NAPRadarPosition", "int", 0, 0, 2),
)


def _coerce(s: Setting, raw):
  """Return a valid typed value or None."""
  try:
    if s.kind == "bool":
      if isinstance(raw, bool):
        return raw
      text = str(raw).strip().lower()
      if text in ("1", "true"):
        return True
      if text in ("0", "false"):
        return False
      return None
    if isinstance(raw, bool):
      return None
    if s.kind == "int":
      val = float(str(raw).strip())
      if val != int(val):
        return None
      val = int(val)
    else:
      val = float(str(raw).strip())
      if val != val or val in (float("inf"), float("-inf")):
        return None
  except (TypeError, ValueError):
    return None
  if s.allowed is not None and val not in s.allowed:
    return None
  if s.lo is not None and val < s.lo:
    return None
  if s.hi is not None and val > s.hi:
    return None
  return val


def load_preap_settings(bogpilot_dir=DEFAULT_OUT_DIR) -> tuple[dict, dict]:
  """Return (values, sources). sources[name] is 'preap_file', 'nap_import' or 'default'."""
  base = Path(bogpilot_dir)
  imported = load_nap_import(base) or {}
  values, sources = {}, {}
  for s in SETTINGS:
    val = None
    try:
      val = _coerce(s, (base / s.preap_file).read_text())
    except (OSError, UnicodeDecodeError):
      val = None
    if val is not None:
      values[s.name], sources[s.name] = val, "preap_file"
      continue
    if s.nap_key is not None and s.nap_key in imported:
      val = _coerce(s, imported[s.nap_key])
      if val is not None:
        values[s.name], sources[s.name] = val, "nap_import"
        continue
    values[s.name], sources[s.name] = s.default, "default"
  return values, sources


_SOURCE_TO_STATUS = {"fixed": "manual", "override": "manual", "existing": "carmodel", "nap": "nap", "can": "auto"}
_REASONS = {
  "fixed": "FINGERPRINT set",
  "override": "Tesla Car Type set in Settings",
  "existing": "existing car model",
  "nap": "imported from NotAutopilot",
}


def profile_status(platform, source, settings=None, can_reason=None, pedal_bus_learned=None) -> dict:
  from openpilot.selfdrive.car.tesla.preap_profile import AP1_PLATFORM, PREAP_PLATFORM
  profile = {PREAP_PLATFORM: "preap", AP1_PLATFORM: "ap1"}.get(platform, "unknown")
  if source == "can":
    reason = can_reason or ("AP ECU messages seen" if profile == "ap1" else "no AP ECU messages")
  else:
    reason = _REASONS.get(source, "no Tesla profile")

  if profile != "preap" or settings is None:
    pedal = radar = "n/a"
  else:
    if not settings.get("pedal_enabled"):
      pedal = "off"
    else:
      bus = pedal_bus_learned if pedal_bus_learned in (0, 2) else settings.get("pedal_can_bus", 2)
      how = "learned" if pedal_bus_learned in (0, 2) else "set"
      from openpilot.selfdrive.car.tesla.preap_pedal import pedal_calibration
      if pedal_calibration(settings) is None:
        long_txt = "calibration missing, stock cruise"
      elif pedal_bus_learned not in (0, 2):
        long_txt = "pedal not seen at fingerprint, stock cruise"
      else:
        long_txt = "openpilot longitudinal when the openpilot-long toggle is on (pedal, regen only)"
      pedal = f"on, bus {bus} ({how}); {long_txt}"
    if not settings.get("radar_enabled"):
      radar = "off"
    else:
      radar = f"on, epas type {settings.get('radar_epas_type', 0)}, position {settings.get('radar_position', 0)}"
  return {"profile": profile, "source": _SOURCE_TO_STATUS.get(source, "auto"), "reason": reason,
          "pedal": pedal, "radar": radar}


def write_profile_status(status: dict, bogpilot_dir=DEFAULT_OUT_DIR) -> bool:
  """Atomically write TeslaProfileStatus. Best effort, never raises."""
  try:
    base = Path(bogpilot_dir)
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / (STATUS_FILE + ".tmp")
    tmp.write_text(json.dumps(status, sort_keys=True))
    os.replace(tmp, base / STATUS_FILE)
    return True
  except Exception:
    return False
