"""One-time import of NotAutopilot (NAP) settings on the first BogPilot boot.

NotAutopilot (https://github.com/NotAutopilot/openpilot, MIT) is the Pre-AP / AP1 Tesla fork many
legacy Tesla owners run before BogPilot. When an owner switches a device from NAP to BogPilot without
a factory reset, NAP's settings are still on disk:

  - /data/params/d/NAP*        typed NAP params (BOOL "1"/"0", INT str(int), FLOAT str(float))
  - /data/nap_params.json      NAP's JSON fallback config (NAPConf)
  - /data/params/d/GitRemote   still names NotAutopilot until manager_init overwrites it

The hook runs at the very top of manager_init, before params.clear_all(): Params::clearAll deletes every
file in /data/params/d that is not a known key, which includes all NAP* keys.

BogPilot cannot read NAP* keys through Params (the prebuilt params_pyx.so raises on unknown keys),
so this module reads the raw files and maps them to BogPilot file toggles in /data/params_bogpilot.
NAP key names, defaults and the pedal calibration sanity rule follow NAP's
opendbc/car/tesla/preap/nap_params.py and nap_conf.py (nap-release e99f4dc, nap-ap1 da5a320).

Rules (tests in frogpilot/system/tests/test_nap_migration.py enforce them):
  - No NAP evidence: write nothing at all, not even a marker.
  - Never delete or edit anything NAP left behind. Every NAP file is byte-copied to
    /data/params_bogpilot/nap_backup/ first (an existing backup is never overwritten).
  - A BogPilot file toggle is written only if that file does not exist yet, so anything the owner
    already set wins.
  - No Params writes at all (no CarModel, no ForceFingerprint, no FrogPilot toggles).
  - The car type is never forced. NAP's NAPCarType / NAPForcePreAP is recorded in the status file only;
    the TeslaCarProfile override file belongs to the owner (Settings > BogPilot > Vehicle Settings) and the Tesla car code
    decides AP1 vs Pre-AP itself, so an AP1 car that once ran nap-release is not pinned to Pre-AP.
  - Never touch set speed, top speed, cruise, stalk or stalk-pull behaviour (NAPFollowDistance and
    double_pull_window_ms are deliberately not imported), and never turn on OccViz or YOLO.
  - VINs are never logged or copied into a toggle (NAPRadarDonorVin is backed up only).
  - One time: NAPMigrationDone marks it finished. Delete that file to run it again.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path("/data")
PARAMS_DIR = DATA_DIR / "params" / "d"
BOGPILOT_DIR = DATA_DIR / "params_bogpilot"
NAP_JSON_NAME = "nap_params.json"

MARKER_NAME = "NAPMigrationDone"
STATUS_NAME = "NAPMigrationStatus"
BACKUP_DIR_NAME = "nap_backup"
LOG_NAME = "migration.log"
# Owner-chosen Tesla profile override (auto / ap1 / preap), written only by the settings UI, never here.
CAR_PROFILE_NAME = "TeslaCarProfile"

STATUS_VERSION = 1

# Target names that must never be written by this module (enforced at write time and in tests).
FORBIDDEN_TARGET_RE = re.compile(r"speed|stalk|pull|cruise|occviz|yolo", re.IGNORECASE)

# Values that must never appear in the log or the status file.
SECRET_NAP_KEYS = frozenset({"NAPRadarDonorVin"})
SECRET_JSON_KEYS = frozenset({"radar_donor_vin"})
VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")

# NAP params backed up but deliberately not imported, with the reason shown in the log/status.
NOT_IMPORTED: dict[str, str] = {
  "NAPFollowDistance": "follow distance comes from the stalk dial in BogPilot; not imported",
  "NAPPedalProfile": "unused by NAP's code",
  "NAPForcePreAP": "recorded in the status file only; the car type is never forced",
  "NAPCarType": "recorded in the status file only; the car type is never forced",
  "NAPActiveProfile": "NAP runtime state, not a setting",
  "NAPScriptRunning": "NAP runtime state, not a setting",
  "NAPEpasRiskAccepted": "one-time EPAS flasher consent; BogPilot asks again if it ever flashes",
  "NAPRadarReadVin": "NAP radar VIN read request, runtime state",
  "NAPRadarVinReadStatus": "NAP radar VIN read status, runtime state",
  "NAPRadarDonorVin": "contains a VIN: backed up only, never copied or logged",
  "NAPRadarIgnoreHwFail": "hides radar hardware faults; review it by hand, not imported",
  "NAPiBoosterEnabled": "iBooster braking is unfinished in NAP; not imported",
  "NAPBrakeFactor": "declared but unused by NAP's code",
}

NAP_JSON_NOT_IMPORTED: dict[str, str] = {
  "double_pull_window_ms": "stalk double-pull timing; BogPilot never changes stalk behaviour",
  "accel_profile": "see suggestions (BogPilot Acceleration Profile is not changed automatically)",
  "pedal_profile": "unused by NAP's code",
  "pedal_min": "raw ADC range, NAP-internal",
  "pedal_max": "raw ADC range, NAP-internal",
  "radar_ignore_hw_fail": "hides radar hardware faults; review it by hand, not imported",
  "radar_donor_vin": "contains a VIN: backed up only, never copied or logged",
}

# NAP accel_profile -> FrogPilot AccelerationProfile (0 standard, 1 eco, 2 sport, 3 sport+). Suggested only.
ACCEL_PROFILE_SUGGESTION = {"Chill": "Eco", "Standard": "Standard", "MadMax": "Sport"}


@dataclass(frozen=True)
class Mapping:
  target: str            # file under /data/params_bogpilot
  nap_key: str | None    # /data/params/d/<nap_key>
  json_key: str | None   # key in /data/nap_params.json (fallback when the param file is missing)
  kind: str              # "bool" | "int" | "float"
  lo: float | None = None
  hi: float | None = None
  allowed: tuple[int, ...] | None = None


MAPPINGS: tuple[Mapping, ...] = (
  Mapping("PreAPPedalEnabled", "NAPPedalEnabled", "use_pedal", "bool"),
  Mapping("PreAPPedalCanBus", "NAPPedalCanBus", "pedal_can_zero", "int", allowed=(0, 2)),
  Mapping("PreAPPedalCalibDone", "NAPPedalCalibDone", "pedal_calibrated", "bool"),
  Mapping("PreAPPedalCalibMin", "NAPPedalCalibMin", "pedal_calib_min", "float", -50.0, 150.0),
  Mapping("PreAPPedalCalibMax", "NAPPedalCalibMax", "pedal_calib_max", "float", -50.0, 150.0),
  Mapping("PreAPPedalCalibZero", "NAPPedalCalibZero", "pedal_calib_zero", "float", -50.0, 150.0),
  Mapping("PreAPPedalCalibFactor", "NAPPedalCalibFactor", "pedal_calib_factor", "float", 0.001, 100.0),
  Mapping("PreAPAdaptiveAccel", "NAPAdaptiveAccel", None, "bool"),
  Mapping("PreAPRadarEnabled", "NAPRadarEnabled", "radar_enabled", "bool"),
  Mapping("PreAPRadarBehindNosecone", "NAPRadarBehindNosecone", "radar_behind_nosecone", "bool"),
  Mapping("PreAPRadarOffset", "NAPRadarOffset", "radar_offset", "float", -5.0, 5.0),
  Mapping("PreAPRadarHud", "NAPRadarHud", None, "bool"),
  Mapping("PreAPRadarUpsideDown", "NAPRadarUpsideDown", "radar_upside_down", "bool"),
  Mapping("PreAPRadarEpasType", "NAPRadarEpasType", "radar_epas_type", "int", 0, 4),
  Mapping("PreAPRadarPosition", "NAPRadarPosition", "radar_position", "int", 0, 2),
)

# NAP defaults for the pedal calibration sanity rule (nap_conf.DEFAULT_CONFIG).
NAP_DEFAULT_CALIB_ZERO = 0.0
NAP_DEFAULT_CALIB_FACTOR = 1.0

NAP_CAR_TYPE_TO_BOGPILOT = {0: "auto", 1: "preap", 2: "ap1"}


@dataclass
class Paths:
  params_dir: Path = PARAMS_DIR
  bogpilot_dir: Path = BOGPILOT_DIR
  data_dir: Path = DATA_DIR

  @property
  def nap_json(self) -> Path:
    return self.data_dir / NAP_JSON_NAME

  @property
  def backup_dir(self) -> Path:
    return self.bogpilot_dir / BACKUP_DIR_NAME


# ---------------------------------------------------------------- reading NAP leftovers

def _read_text(path: Path, limit: int = 4096) -> str | None:
  try:
    with open(path, "rb") as f:
      return f.read(limit).decode("utf-8", errors="replace").strip()
  except OSError:
    return None


def _parse_bool(text: str | None) -> bool | None:
  if text is None:
    return None
  t = text.strip().lower()
  if t in ("1", "true", "on"):
    return True
  if t in ("0", "false", "off"):
    return False
  return None


def _parse_number(text, kind: str):
  if text is None or isinstance(text, bool) and kind != "bool":
    return None
  try:
    value = float(text)
  except (TypeError, ValueError):
    return None
  if value != value or value in (float("inf"), float("-inf")):
    return None
  if kind == "int":
    if value != int(value):
      return None
    return int(value)
  return value


def nap_param_files(paths: Paths) -> list[Path]:
  try:
    return sorted(p for p in paths.params_dir.iterdir() if p.name.startswith("NAP") and p.is_file())
  except OSError:
    return []


def load_nap_json(paths: Paths) -> dict | None:
  try:
    with open(paths.nap_json) as f:
      data = json.load(f)
    return data if isinstance(data, dict) else None
  except (OSError, ValueError):
    return None


def old_nap_checkouts(paths: Paths) -> list[Path]:
  """Other /data/<dir> checkouts that still hold NAP's scripts (e.g. a renamed /data/openpilot)."""
  found = []
  try:
    for child in sorted(paths.data_dir.iterdir()):
      if child.name == "openpilot" or not child.is_dir():
        continue
      if (child / "scripts" / "nap").is_dir():
        found.append(child)
  except OSError:
    pass
  return found


def detect_nap(paths: Paths) -> list[str]:
  """Evidence that NAP ran on this device. Empty list = never ran NAP (or it was factory reset)."""
  evidence = []
  files = nap_param_files(paths)
  if files:
    evidence.append(f"{len(files)} NAP settings in /data/params")
  if paths.nap_json.is_file():
    evidence.append("/data/nap_params.json")
  remote = _read_text(paths.params_dir / "GitRemote") or ""
  if "notautopilot" in remote.lower():
    evidence.append("GitRemote names NotAutopilot")
  checkouts = old_nap_checkouts(paths)
  if checkouts:
    evidence.append("old NAP checkout in /data: " + ", ".join(c.name for c in checkouts))
  if not evidence:
    return []
  # Supporting evidence only (never enough on its own).
  branch = _read_text(paths.params_dir / "GitBranch") or ""
  if branch.startswith("nap-"):
    evidence.append(f"GitBranch {branch[:40]}")
  return evidence


def _nap_value(m: Mapping, paths: Paths, nap_json: dict | None):
  """(value, source) for one mapping, or (None, reason). Param file first, then JSON, like NAPConf."""
  if m.nap_key:
    text = _read_text(paths.params_dir / m.nap_key)
    if text is not None:
      value = _parse_bool(text) if m.kind == "bool" else _parse_number(text, m.kind)
      if value is None:
        return None, f"unreadable value in {m.nap_key}"
      return value, m.nap_key
  if m.json_key and nap_json is not None and m.json_key in nap_json:
    raw = nap_json[m.json_key]
    if m.json_key == "pedal_can_zero":
      flag = raw if isinstance(raw, bool) else _parse_bool(str(raw))
      if flag is None:
        return None, "unreadable pedal_can_zero in nap_params.json"
      return (0 if flag else 2), f"nap_params.json:{m.json_key}"
    if m.kind == "bool":
      value = raw if isinstance(raw, bool) else _parse_bool(str(raw))
    else:
      value = _parse_number(raw, m.kind)
    if value is None:
      return None, f"unreadable {m.json_key} in nap_params.json"
    return value, f"nap_params.json:{m.json_key}"
  return None, "not set in NAP (NAP default applies; BogPilot default used)"


def _in_range(m: Mapping, value) -> bool:
  if m.kind == "bool":
    return True
  if m.allowed is not None and value not in m.allowed:
    return False
  if m.lo is not None and value < m.lo:
    return False
  if m.hi is not None and value > m.hi:
    return False
  return True


def _format(m: Mapping, value) -> str:
  if m.kind == "bool":
    return "1" if value else "0"
  if m.kind == "int":
    return str(int(value))
  return repr(float(value))


def choose_car_type(paths: Paths) -> tuple[str | None, str]:
  car_type = _parse_number(_read_text(paths.params_dir / "NAPCarType"), "int")
  if car_type in NAP_CAR_TYPE_TO_BOGPILOT:
    return NAP_CAR_TYPE_TO_BOGPILOT[car_type], f"NAPCarType={car_type}"
  force = _parse_bool(_read_text(paths.params_dir / "NAPForcePreAP"))
  if force is True:
    return "preap", "NAPForcePreAP=1 (NAP was forcing Pre-AP)"
  if force is False:
    return "auto", "NAPForcePreAP=0"
  return None, "no NAP car type setting"


def plan(paths: Paths) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
  """What NAP says, mapped. Returns (values {target: text}, sources {target: src}, skipped {key: reason})."""
  nap_json = load_nap_json(paths)
  values: dict[str, str] = {}
  sources: dict[str, str] = {}
  skipped: dict[str, str] = {}

  raw: dict[str, object] = {}
  for m in MAPPINGS:
    value, src = _nap_value(m, paths, nap_json)
    if value is None:
      if not src.startswith("not set"):
        skipped[m.nap_key or m.json_key or m.target] = src
      continue
    if not _in_range(m, value):
      skipped[m.nap_key or m.json_key or m.target] = f"value out of range ({value}); BogPilot default kept"
      continue
    raw[m.target] = value
    values[m.target] = _format(m, value)
    sources[m.target] = src

  # NAP only trusts its calibration-done flag if zero or factor moved off the defaults.
  if raw.get("PreAPPedalCalibDone") is True:
    zero = raw.get("PreAPPedalCalibZero", NAP_DEFAULT_CALIB_ZERO)
    factor = raw.get("PreAPPedalCalibFactor", NAP_DEFAULT_CALIB_FACTOR)
    if zero == NAP_DEFAULT_CALIB_ZERO and factor == NAP_DEFAULT_CALIB_FACTOR:
      values["PreAPPedalCalibDone"] = "0"
      sources["PreAPPedalCalibDone"] += " (NAP sanity check failed: calibration values are still defaults; recalibrate)"

  for p in nap_param_files(paths):
    if p.name in NOT_IMPORTED:
      skipped.setdefault(p.name, NOT_IMPORTED[p.name])
    elif not any(m.nap_key == p.name for m in MAPPINGS):
      skipped.setdefault(p.name, "unknown NAP setting; backed up only")
  if nap_json is not None:
    for key in nap_json:
      if key in NAP_JSON_NOT_IMPORTED:
        skipped.setdefault(f"nap_params.json:{key}", NAP_JSON_NOT_IMPORTED[key])

  for target in values:
    assert not FORBIDDEN_TARGET_RE.search(target), target
  return values, sources, skipped


def suggestions(paths: Paths) -> dict[str, str]:
  out = {}
  nap_json = load_nap_json(paths) or {}
  profile = nap_json.get("accel_profile")
  if isinstance(profile, str) and profile in ACCEL_PROFILE_SUGGESTION:
    out["AccelerationProfile"] = (f"NAP used the {profile} acceleration profile; the closest BogPilot setting is "
                                  f"{ACCEL_PROFILE_SUGGESTION[profile]} (Longitudinal settings). Not changed automatically.")
  return out


def epas_hint(paths: Paths) -> str:
  """Best-effort note about NAP's EPAS flash. The patch lives in the car's EPAS, not on the comma."""
  for checkout in old_nap_checkouts(paths) + [paths.data_dir / "openpilot"]:
    fw = checkout / "scripts" / "nap" / "firmware"
    try:
      names = [p.name for p in fw.iterdir()] if fw.is_dir() else []
    except OSError:
      names = []
    if any(n.endswith(".modified.bin") for n in names):
      return f"NAP EPAS firmware images found in /data/{checkout.name}: the EPAS was probably patched with NAP. Keep that folder; it holds the stock EPAS backup."
    if names:
      return f"NAP EPAS backup folder found in /data/{checkout.name}. Keep it; it holds the stock EPAS backup."
  return "No NAP EPAS backup found on this device. If NAP patched your EPAS, that patch is in the car and still applies."


# ---------------------------------------------------------------- writing

def redact(text: str) -> str:
  return VIN_RE.sub("[redacted]", text)


def _atomic_write(path: Path, data: bytes) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
  try:
    with os.fdopen(fd, "wb") as f:
      f.write(data)
      f.flush()
      os.fsync(f.fileno())
    os.replace(tmp, path)
  except BaseException:
    try:
      os.unlink(tmp)
    except OSError:
      pass
    raise


def _same_bytes(a: Path, b: Path) -> bool:
  try:
    return a.read_bytes() == b.read_bytes()
  except OSError:
    return False


def backup_file(src: Path, backup_dir: Path, name: str | None = None) -> Path | None:
  """Copy src into backup_dir. Never overwrites: identical copy = skip, different = name.1, .2, ..."""
  if not src.is_file():
    return None
  backup_dir.mkdir(parents=True, exist_ok=True)
  base = name or src.name
  dest = backup_dir / base
  n = 0
  while dest.exists():
    if _same_bytes(src, dest):
      return dest
    n += 1
    dest = backup_dir / f"{base}.{n}"
  shutil.copy2(src, dest)
  return dest


def backup_all(paths: Paths) -> list[str]:
  copied = []
  for p in nap_param_files(paths):
    if backup_file(p, paths.backup_dir / "params"):
      copied.append(p.name)
  if backup_file(paths.nap_json, paths.backup_dir):
    copied.append(NAP_JSON_NAME)
  for key in ("GitRemote", "GitBranch"):
    if backup_file(paths.params_dir / key, paths.backup_dir / "params"):
      copied.append(key)
  return copied


class _Log:
  def __init__(self, path: Path):
    self.path = path
    self.lines: list[str] = []

  def __call__(self, msg: str) -> None:
    self.lines.append(redact(msg))

  def flush(self) -> None:
    if not self.lines:
      return
    self.path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(self.path, "a") as f:
      for line in self.lines:
        f.write(f"{stamp} {line}\n")
    self.lines = []


def migrate(paths: Paths | None = None, now: str | None = None) -> dict:
  """Run the one-time import. Returns the status dict (result: no_nap / already_done / imported)."""
  paths = paths or Paths()
  if (paths.bogpilot_dir / MARKER_NAME).exists():
    return {"result": "already_done"}

  evidence = detect_nap(paths)
  if not evidence:
    return {"result": "no_nap"}

  log = _Log(paths.backup_dir / LOG_NAME)
  log("NotAutopilot settings found: " + "; ".join(evidence))

  copied = backup_all(paths)
  log(f"backed up {len(copied)} files to {paths.backup_dir} (originals left in place)")

  values, sources, skipped = plan(paths)
  imported: dict[str, str] = {}
  kept: list[str] = []
  for target, text in values.items():
    if FORBIDDEN_TARGET_RE.search(target):
      continue
    dest = paths.bogpilot_dir / target
    if dest.exists():
      kept.append(target)
      log(f"kept {target}: already set on this device ({sources[target]} not applied)")
      continue
    _atomic_write(dest, text.encode())
    imported[target] = text
    log(f"set {target}={text} from {sources[target]}")
  for key, reason in sorted(skipped.items()):
    log(f"not imported {key}: {reason}")

  nap_car_type, car_type_why = choose_car_type(paths)
  if nap_car_type is not None:
    log(f"NAP car type was {nap_car_type} ({car_type_why}); not forced. BogPilot picks AP1 or Pre-AP itself. "
        f"To force it, use Settings > BogPilot > Vehicle Settings > Tesla Car Type & NotAutopilot.")

  status = {
    "version": STATUS_VERSION,
    "result": "imported",
    "time": now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "evidence": evidence,
    "imported": imported,
    "kept": kept,
    "nap_car_type": nap_car_type,
    "nap_car_type_reason": car_type_why,
    "skipped": skipped,
    "suggestions": suggestions(paths),
    "epas_hint": epas_hint(paths),
  }
  for text in status["suggestions"].values():
    log("suggestion: " + text)
  log(status["epas_hint"])

  _atomic_write(paths.bogpilot_dir / STATUS_NAME, redact(json.dumps(status, indent=2, sort_keys=True)).encode())
  _atomic_write(paths.bogpilot_dir / MARKER_NAME, b"1")
  log("done; delete /data/params_bogpilot/NAPMigrationDone to run the import again")
  log.flush()
  return status


def migrate_nap_once() -> None:
  """manager_init hook. Never raises."""
  try:
    status = migrate()
    if status.get("result") == "imported":
      print(f"BogPilot: imported {len(status['imported'])} NotAutopilot settings (see /data/params_bogpilot/nap_backup/{LOG_NAME})")
  except Exception as e:  # never block boot
    try:
      print(f"BogPilot: NotAutopilot import skipped: {type(e).__name__}")
    except Exception:
      pass


if __name__ == "__main__":
  print(json.dumps(migrate(), indent=2))
