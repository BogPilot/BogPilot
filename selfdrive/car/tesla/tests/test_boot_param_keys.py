"""Keys written at manager startup must exist in common/params.cc.

The comma 3X raises UnknownKeyName and never finishes boot if a default
param is missing from that list. This does not boot the device.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _registered_keys() -> set[str]:
  text = (ROOT / "common/params.cc").read_text()
  return set(re.findall(r'\{"([^"]+)"', text))


def _quoted_keys(path: Path, anchor: str, end: str) -> list[str]:
  text = path.read_text()
  chunk = text[text.index(anchor):text.index(end, text.index(anchor))]
  return re.findall(r'\("([A-Za-z][^"]*)"', chunk)


def test_manager_startup_params_are_registered():
  registered = _registered_keys()
  manager_keys = _quoted_keys(
    ROOT / "system/manager/manager.py",
    "default_params: list",
    "if not PC:",
  )
  frogpilot_keys = _quoted_keys(
    ROOT / "frogpilot/common/frogpilot_variables.py",
    "frogpilot_default_params",
    "misc_tuning_levels",
  )
  startup = manager_keys + frogpilot_keys
  missing = sorted({k for k in startup if k not in registered})
  assert not missing, "manager startup writes unregistered params: " + ", ".join(missing)


def test_prebuilt_params_module_contains_startup_keys():
  """The comma skips compile when `prebuilt` exists and loads this .so."""
  if not (ROOT / "prebuilt").is_file():
    return
  blob = (ROOT / "common/params_pyx.so").read_bytes()
  registered = _registered_keys()
  startup = _quoted_keys(
    ROOT / "system/manager/manager.py",
    "default_params: list",
    "if not PC:",
  ) + _quoted_keys(
    ROOT / "frogpilot/common/frogpilot_variables.py",
    "frogpilot_default_params",
    "misc_tuning_levels",
  )
  missing = sorted({k for k in startup if k.encode() not in blob})
  assert not missing, "prebuilt params_pyx.so has no key: " + ", ".join(missing)

