#!/usr/bin/env python3
"""Import NotAutopilot (NAP) settings before BogPilot wipes them.

NAP stores its settings as openpilot Params (`NAP*` keys, one file each under
/data/params/d). BogPilot's manager_init runs Params.clear_all(), and
Params::clearAll removes every file that is not a BogPilot key, so on the first
BogPilot boot the NAP files are gone. snapshot_nap_params() copies a whitelist
of them to /data/params_bogpilot/NAPImport.json first. Nothing else reads the
raw NAP files.

Key names and defaults come from NotAutopilot/openpilot nap-release
e99f4dc7508874e639bfa81ad1f1cb1dfaedea09 (common/params_keys.h) and
NotAutopilot/opendbc 21a45cf918dd5df03f9143e16eb9890a2936f5d3
(opendbc/car/tesla/preap/nap_params.py, nap_detect.py: NAPCarType
0 Auto / 1 Pre-AP / 2 AP1). NAP is MIT licensed.

PII: NAPRadarDonorVin is a VIN. It is never written to NAPImport.json, logs or
stdout. If present it is copied as-is to /data/params_bogpilot/TeslaRadarDonorVin
(device-local, read only by the Pre-AP radar config), and the JSON records only
`has_radar_donor_vin`.

Stdlib only, so manager_init can call it before anything else is imported,
and an installer can run it as a script.
"""
import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_PARAMS_DIR = Path("/data/params/d")
DEFAULT_OUT_DIR = Path("/data/params_bogpilot")
IMPORT_NAME = "NAPImport.json"
DONOR_VIN_NAME = "TeslaRadarDonorVin"
IMPORT_VERSION = 1

NAP_CAR_TYPE_AUTO = 0
NAP_CAR_TYPE_PREAP = 1
NAP_CAR_TYPE_AP1 = 2

# key -> (type, NAP default). Defaults match NAP params_keys.h / nap_params.py.
NAP_KEYS = {
  "NAPCarType": (int, NAP_CAR_TYPE_AUTO),
  "NAPForcePreAP": (bool, True),
  "NAPPedalEnabled": (bool, False),
  "NAPPedalCanBus": (int, 2),
  "NAPRadarEnabled": (bool, False),
  "NAPRadarBehindNosecone": (bool, False),
  "NAPRadarOffset": (float, 0.0),
  "NAPRadarPosition": (int, 0),
  "NAPRadarEpasType": (int, 0),
  "NAPRadarUpsideDown": (bool, False),
  "NAPFollowDistance": (int, 4),
  "NAPiBoosterEnabled": (bool, False),
}
DONOR_VIN_KEY = "NAPRadarDonorVin"


def _read_text(path: Path) -> str | None:
  try:
    return path.read_bytes().decode("utf-8", errors="ignore").strip()
  except OSError:
    return None


def _parse(kind, text: str):
  if text is None or text == "":
    return None
  try:
    if kind is bool:
      low = text.lower()
      if low in ("1", "true", "on"):
        return True
      if low in ("0", "false", "off"):
        return False
      return None
    if kind is int:
      return int(float(text))
    if kind is float:
      return float(text)
  except ValueError:
    return None
  return None


def normalize_donor_vin(text: str | None) -> str:
  """17 alphanumeric chars or ''. Same rule as NAP normalize_radar_donor_vin."""
  vin = "".join(ch for ch in (text or "").upper() if ch.isalnum())
  return vin if len(vin) == 17 else ""


def read_nap_params(params_dir: Path = DEFAULT_PARAMS_DIR) -> dict:
  """Whitelisted NAP values that exist on disk. Missing keys are omitted."""
  params_dir = Path(params_dir)
  out = {}
  for key, (kind, _default) in NAP_KEYS.items():
    value = _parse(kind, _read_text(params_dir / key))
    if value is not None:
      out[key] = value
  return out


def snapshot_nap_params(params_dir: Path = DEFAULT_PARAMS_DIR, out_dir: Path = DEFAULT_OUT_DIR,
                        force: bool = False) -> str:
  """Write NAPImport.json once. Returns written / exists / no-nap-params."""
  params_dir = Path(params_dir)
  out_dir = Path(out_dir)
  target = out_dir / IMPORT_NAME
  if target.exists() and not force:
    return "exists"
  values = read_nap_params(params_dir)
  vin = normalize_donor_vin(_read_text(params_dir / DONOR_VIN_KEY))
  if not values and not vin:
    return "no-nap-params"
  values["version"] = IMPORT_VERSION
  values["has_radar_donor_vin"] = bool(vin)
  out_dir.mkdir(parents=True, exist_ok=True)
  tmp = target.with_suffix(".tmp")
  tmp.write_text(json.dumps(values, sort_keys=True))
  os.replace(tmp, target)
  if vin:
    vin_path = out_dir / DONOR_VIN_NAME
    if force or not vin_path.exists():
      vin_tmp = vin_path.with_suffix(".tmp")
      vin_tmp.write_text(vin)
      os.chmod(vin_tmp, 0o600)
      os.replace(vin_tmp, vin_path)
  return "written"


def load_nap_import(out_dir: Path = DEFAULT_OUT_DIR) -> dict | None:
  """Imported NAP settings with NAP defaults filled in, or None when not a NAP migration."""
  try:
    raw = json.loads((Path(out_dir) / IMPORT_NAME).read_text())
  except (OSError, ValueError):
    return None
  if not isinstance(raw, dict):
    return None
  out = {}
  for key, (kind, default) in NAP_KEYS.items():
    value = raw.get(key, default)
    if kind is bool:
      value = bool(value) if isinstance(value, (bool, int)) else default
    elif kind is int:
      value = int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    elif kind is float:
      value = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    out[key] = value
  out["has_radar_donor_vin"] = bool(raw.get("has_radar_donor_vin", False))
  return out


def nap_car_type(imported: dict | None) -> int | None:
  """NAPCarType normalised like NAP: None = not NAP, unknown = Auto."""
  if imported is None:
    return None
  value = imported.get("NAPCarType", NAP_CAR_TYPE_AUTO)
  return value if value in (NAP_CAR_TYPE_AUTO, NAP_CAR_TYPE_PREAP, NAP_CAR_TYPE_AP1) else NAP_CAR_TYPE_AUTO


def read_donor_vin(out_dir: Path = DEFAULT_OUT_DIR) -> str:
  """Device-local donor VIN or ''. Never log the result."""
  return normalize_donor_vin(_read_text(Path(out_dir) / DONOR_VIN_NAME))


def main(argv=None) -> int:
  ap = argparse.ArgumentParser(description="Snapshot NotAutopilot settings for BogPilot")
  ap.add_argument("--snapshot", action="store_true", help="copy whitelisted NAP params to NAPImport.json")
  ap.add_argument("--params-dir", default=str(DEFAULT_PARAMS_DIR))
  ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
  ap.add_argument("--force", action="store_true", help="overwrite an existing snapshot")
  args = ap.parse_args(argv)
  if not args.snapshot:
    ap.print_help()
    return 0
  try:
    status = snapshot_nap_params(Path(args.params_dir), Path(args.out_dir), force=args.force)
  except Exception as e:  # best effort: never block a boot or an install
    status = f"error {type(e).__name__}"
  print(f"nap_import: {status}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
