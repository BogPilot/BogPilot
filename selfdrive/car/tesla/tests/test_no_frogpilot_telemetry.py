"""BogPilot sends nothing to FrogPilot's servers: telemetry, stats, device registration,
issue reports, weather proxy, theme submission and Sentry crash reports are all gone."""
import ast
import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
PY_DIRS = ("frogpilot", "selfdrive", "system", "common", "tools")
SELF = Path(__file__).resolve()


def _py_files():
  for d in PY_DIRS:
    for p in (ROOT / d).rglob("*.py"):
      if p.resolve() != SELF and "/third_party/" not in str(p):
        yield p


def test_removed_files_are_gone():
  for rel in ("frogpilot/system/frogpilot_telemetry.py", "frogpilot/system/frogpilot_stats.py",
              "frogpilot/assets/city_lookup.sqlite"):
    assert not (ROOT / rel).exists(), rel


def test_no_frogpilot_api_or_telemetry_references():
  banned = ("frogpilot.com/api", "api.frogpilot.com", "FROGPILOT_API", "get_frogpilot_api_info", "send_stats",
            "register_device", "frogpilot.system.frogpilot_telemetry", "run_frogpilot_telemetry",
            "frogpilot.system.frogpilot_stats", "city_lookup", "CITY_LOOKUP_PATH", "ingest.us.sentry.io",
            "ingest.sentry.io")
  hits = []
  for p in _py_files():
    text = p.read_text(errors="ignore")
    hits += [f"{p.relative_to(ROOT)}: {b}" for b in banned if b in text]
  assert not hits, hits


def test_no_telemetry_process():
  tree = ast.parse((ROOT / "system/manager/process_config.py").read_text())
  names = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
  assert "frogpilot_telemetry" not in names
  assert "frogpilot.system.frogpilot_telemetry" not in names


def test_telemetry_toggle_off_and_hidden():
  src = (ROOT / "frogpilot/common/frogpilot_variables.py").read_text()
  assert '("FrogPilotTelemetry", "0", 4, "0")' in src  # level 4 > max TuningLevel 3 -> hidden in the UI
  assert "toggle.frogpilot_telemetry = False" in src


def test_theme_submission_disabled():
  src = (ROOT / "frogpilot/system/the_pond/the_pond.py").read_text()
  start = src.index('"/api/themes/submit"')
  body = src[start:start + 800]
  assert "403" in body and "requests." not in body


def _load_sentry():
  fake_vars = types.ModuleType("openpilot.frogpilot.common.frogpilot_variables")
  fake_vars.ERROR_LOGS_PATH = Path("/nonexistent")
  fake_sdk = types.ModuleType("sentry_sdk")
  fake_sdk.init = mock.Mock(name="sentry_sdk.init")
  fake_log = types.ModuleType("openpilot.common.swaglog")
  fake_log.cloudlog = mock.Mock()
  with mock.patch.dict(sys.modules, {"sentry_sdk": fake_sdk, "openpilot.common.swaglog": fake_log,
                                     "openpilot.frogpilot.common.frogpilot_variables": fake_vars}):
    spec = importlib.util.spec_from_file_location("bp_sentry_under_test", ROOT / "system/sentry.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
  return mod, fake_sdk


def test_sentry_never_initialises():
  mod, sdk = _load_sentry()
  assert mod.init(mod.SentryProject.SELFDRIVE) is False
  assert mod.init(mod.SentryProject.SELFDRIVE_NATIVE) is False
  sdk.init.assert_not_called()
  assert len(mod.SentryProject) == 2  # both members still exist for manager.py / tombstoned.py
  for member in mod.SentryProject:
    assert "sentry.io" not in member.value and "@" not in member.value
