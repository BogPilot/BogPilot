"""get_car() wiring for the Tesla profile, run without opendbc/msgq .so files.

get_car and _tesla_observer are lifted out of car_helpers.py with ast and run
against stand-ins, so the real control flow (original block vs Tesla choice,
CarModel writes, status write) is what gets tested.
"""
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.car.tesla import nap_import, preap_profile, preap_select, preap_settings
from openpilot.selfdrive.car.tesla.preap_profile import CHASSIS_ADDRS

SRC = Path(__file__).resolve().parents[2] / "car_helpers.py"
MS = 10_000_000


class _Params:
  def __init__(self, car_model=None):
    self.store = {} if car_model is None else {"CarModel": car_model.encode()}
    self.writes = []

  def get(self, k):
    return self.store.get(k)

  def put_nonblocking(self, k, v):
    self.writes.append(k)
    self.store[k] = v.encode()


class _Iface:
  calls = []

  @staticmethod
  def get_params(candidate, *a, **k):
    _Iface.calls.append(candidate)
    return SimpleNamespace(carFingerprint=candidate)

  @staticmethod
  def get_frogpilot_params(*a, **k):
    return SimpleNamespace()


def _batches(addrs, n):
  return [SimpleNamespace(logMonoTime=i * MS, can=[SimpleNamespace(address=a, src=0, dat=b"\0" * 8) for a in addrs])
          for i in range(n)]


def _load(monkeypatch, tmp_path, candidate, batches):
  tree = ast.parse(SRC.read_text())
  funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("get_car", "_tesla_observer")]
  assert len(funcs) == 2
  # bogpilot_dir defaults were bound at import time: point them at tmp_path
  real = nap_import.DEFAULT_OUT_DIR
  for f in ("read_profile_override", "resolve_live", "needs_more_observation", "choose", "write_status"):
    fn = getattr(preap_select, f, None) or getattr(preap_profile, f)
    fn.__defaults__ = tuple(tmp_path if d == real else d for d in fn.__defaults__)
    assert tmp_path in fn.__defaults__
  monkeypatch.setattr(preap_settings, "load_preap_settings",
                      lambda *a, **k: ({"pedal_enabled": False, "radar_enabled": False}, {}))
  monkeypatch.setattr(preap_settings, "write_profile_status",
                      lambda st, *a, **k: (tmp_path / "TeslaProfileStatus").write_text(json.dumps(st)))
  it = iter(batches)
  empty = SimpleNamespace(logMonoTime=0, can=[])

  def fingerprint(logcan, sendcan, num_pandas, observer=None):
    for b in batches[:150]:
      observer.update_can_msg(b)
    return candidate, {0: {}, 2: {}}, "", [], "can", True

  ns = dict(
    os=os, fingerprint=fingerprint, cloudlog=SimpleNamespace(event=lambda *a, **k: None, exception=lambda *a, **k: None),
    get_one_can=lambda logcan: next(it, empty), MOCK=SimpleNamespace(MOCK="MOCK"), sentry=None,
    interfaces={k: (_Iface, None, None) for k in ("MOCK", "HONDA_CIVIC", "TESLA_AP1_MODELS", "TESLA_PREAP_MODELS")},
    get_car_interface=lambda CP, FPCP: "CI",
  )
  exec(compile(ast.Module(body=funcs, type_ignores=[]), str(SRC), "exec"), ns)
  return ns["get_car"]


def _toggles(**kw):
  return SimpleNamespace(**({"force_fingerprint": False, "car_model": None, "block_user": False} | kw))


@pytest.fixture(autouse=True)
def _clean():
  _Iface.calls = []
  defaults = {f: getattr(preap_select, f).__defaults__ for f in ("needs_more_observation", "choose", "write_status")}
  defaults["resolve_live"] = preap_profile.resolve_live.__defaults__
  defaults["read_profile_override"] = preap_profile.read_profile_override.__defaults__
  yield
  for f, d in defaults.items():
    (getattr(preap_select, f, None) or getattr(preap_profile, f)).__defaults__ = d


def test_ap1_configured_device_unchanged(monkeypatch, tmp_path):
  monkeypatch.delenv("FINGERPRINT", raising=False)
  get_car = _load(monkeypatch, tmp_path, None, _batches(CHASSIS_ADDRS, 300))
  params = _Params("TESLA_AP1_MODELS")
  get_car(None, None, False, params, 1, _toggles(car_model="TESLA_AP1_MODELS"))
  assert _Iface.calls == ["TESLA_AP1_MODELS"]
  assert params.writes == []
  st = json.loads((tmp_path / "TeslaProfileStatus").read_text())
  assert st["profile"] == "ap1" and st["source"] == "carmodel"


def test_fresh_preap_install_auto(monkeypatch, tmp_path):
  monkeypatch.delenv("FINGERPRINT", raising=False)
  get_car = _load(monkeypatch, tmp_path, None, _batches(CHASSIS_ADDRS, 400))
  params = _Params()
  get_car(None, None, False, params, 1, _toggles())
  assert _Iface.calls == ["TESLA_PREAP_MODELS"]
  assert "CarModel" not in params.writes  # auto result is never persisted
  st = json.loads((tmp_path / "TeslaProfileStatus").read_text())
  assert st["profile"] == "preap" and st["source"] == "auto"


def test_fresh_ap1_install_auto(monkeypatch, tmp_path):
  monkeypatch.delenv("FINGERPRINT", raising=False)
  get_car = _load(monkeypatch, tmp_path, None, _batches(CHASSIS_ADDRS + (0x488, 0x2B9), 200))
  params = _Params()
  get_car(None, None, False, params, 1, _toggles())
  assert _Iface.calls == ["TESLA_AP1_MODELS"]


def test_unknown_car_model_treated_as_empty(monkeypatch, tmp_path):
  monkeypatch.delenv("FINGERPRINT", raising=False)
  get_car = _load(monkeypatch, tmp_path, None, _batches(CHASSIS_ADDRS, 400))
  get_car(None, None, False, _Params("NOT_A_PLATFORM"), 1, _toggles())
  assert _Iface.calls == ["TESLA_PREAP_MODELS"]


def test_other_brand_unchanged(monkeypatch, tmp_path):
  monkeypatch.delenv("FINGERPRINT", raising=False)
  get_car = _load(monkeypatch, tmp_path, "HONDA_CIVIC", _batches((0x1, 0x2), 200))
  params = _Params()
  get_car(None, None, False, params, 1, _toggles())
  assert _Iface.calls == ["HONDA_CIVIC"]
  assert params.writes == ["CarMake", "CarModel"]
  assert not (tmp_path / "TeslaProfileStatus").exists()


def test_no_match_no_tesla_is_mock(monkeypatch, tmp_path):
  monkeypatch.delenv("FINGERPRINT", raising=False)
  get_car = _load(monkeypatch, tmp_path, None, _batches((0x1,), 200))
  get_car(None, None, False, _Params(), 1, _toggles())
  assert _Iface.calls == ["MOCK"]


def test_selection_error_falls_back(monkeypatch, tmp_path):
  monkeypatch.delenv("FINGERPRINT", raising=False)
  get_car = _load(monkeypatch, tmp_path, None, _batches(CHASSIS_ADDRS, 400))
  monkeypatch.setattr(preap_select, "choose", lambda *a, **k: 1 / 0)
  get_car(None, None, False, _Params(), 1, _toggles(car_model="TESLA_AP1_MODELS"))
  assert _Iface.calls == ["TESLA_AP1_MODELS"]
