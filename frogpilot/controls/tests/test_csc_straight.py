"""BogPilot CSCStraightFix: persistence-gated Curve Speed Controller.

Straight road: the planner curve flag flickers for a frame or two with a curve speed far above
the set speed. Stock CSC took over on every flicker and seeded its target at vEgo (brief
hesitation). With the fix CSC never takes over there and the target never dips.
Real curve: CSC still takes over (after HOLD_S) and slows to the same curve speed.
"""
import importlib.util
import sys
import types
from types import SimpleNamespace as N
from unittest.mock import MagicMock

import pytest

from openpilot.frogpilot.controls.lib import csc_straight_gate as g
from openpilot.frogpilot.controls.lib.csc_straight_gate import (HOLD_S, OFF_MARGIN, ON_MARGIN, RELEASE_S, CSCStraightGate,
                                                                csc_curve_speed, csc_seed, csc_straight_fix_enabled)

DT = 0.05
REF = 24.6          # m/s, 55 mph set speed
STRAIGHT_K = 1 / 540.0     # gentle bend the model predicts on a straight road: ~1.04 m/s^2 at 23.7 m/s, csc ~ 32.9 m/s
REAL_K = 1 / 144.0        # typical real curve (~144 m radius): csc = sqrt(2 * 144) ~ 17 m/s


# --- pure gate ---

def test_constants():
  assert (ON_MARGIN, OFF_MARGIN, HOLD_S, RELEASE_S) == (0.5, 0.25, 0.5, 0.3)


def test_flicker_with_fast_curve_speed_never_detects():
  gate = CSCStraightGate()
  cs = csc_curve_speed(2.0, STRAIGHT_K)
  assert cs > REF
  for i in range(400):
    assert not gate.update(True, i % 7 in (0, 1), cs, REF, DT)


def test_short_bursts_below_target_never_detect():
  gate = CSCStraightGate()
  for i in range(400):
    # 0.45 s bursts (9 frames) of a real-looking curve speed, then a gap: still not HOLD_S
    assert not gate.update(True, i % 12 < 9, 17.0, REF, DT)


def test_real_curve_detects_after_hold():
  gate = CSCStraightGate()
  frames = [gate.update(True, True, 17.0, REF, DT) for _ in range(20)]
  first = frames.index(True)
  assert first == round(HOLD_S / DT) - 1
  assert all(frames[first:])


def test_hysteresis_and_release():
  gate = CSCStraightGate()
  for _ in range(10):
    gate.update(True, True, REF - 1.0, REF, DT)
  assert gate.active
  # inside the hysteresis band (below target - OFF_MARGIN): stays on
  for _ in range(40):
    assert gate.update(True, True, REF - 0.3, REF, DT)
  # brief planner dropouts shorter than RELEASE_S: stays on
  for i in range(40):
    assert gate.update(True, i % 4 != 0, REF - 1.0, REF, DT)
  # curve over: releases after RELEASE_S
  out = [gate.update(True, False, float("inf"), REF, DT) for _ in range(10)]
  assert out.index(False) == round(RELEASE_S / DT) - 1


@pytest.mark.parametrize("why", ["blinker", "disengaged"])
def test_hard_gates_release_at_once(why):
  gate = CSCStraightGate()
  for _ in range(10):
    gate.update(True, True, 17.0, REF, DT)
  assert gate.active
  assert not gate.update(False, True, 17.0, REF, DT)
  assert gate.on_timer == 0.0


def test_seed_value():
  assert csc_seed(REF, 17.0) == 17.0
  assert csc_seed(REF, 30.0) == REF
  assert csc_curve_speed(2.0, 0.0) > 1000  # straight path: finite, no ZeroDivisionError


@pytest.mark.parametrize("stored,expected", [(None, True), ("", True), ("1", True), ("on", True),
                                             ("0", False), ("off", False), ("false", False), (b"0\n", False)])
def test_toggle(tmp_path, monkeypatch, stored, expected):
  path = tmp_path / "CSCStraightFix"
  if stored is not None:
    path.write_bytes(stored if isinstance(stored, bytes) else stored.encode())
  monkeypatch.setattr(g, "_csc_straight_fix_path", path)
  assert csc_straight_fix_enabled() is expected


# --- FrogPilotVCruise integration (real vcruise + CSC, stubbed heavy deps) ---

@pytest.fixture
def vcruise_mod(monkeypatch):
  fv = types.ModuleType("openpilot.frogpilot.common.frogpilot_variables")
  fv.CRUISING_SPEED, fv.DEFAULT_LATERAL_ACCELERATION, fv.PLANNER_TIME = 5, 2.0, 10.0
  fv.params = MagicMock()
  fv.params.get.return_value = None
  mpc = types.ModuleType("openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc")
  mpc.COMFORT_BRAKE = 2.5
  slc = types.ModuleType("openpilot.frogpilot.controls.lib.speed_limit_controller")
  slc.SpeedLimitController = MagicMock
  rt = types.ModuleType("openpilot.common.realtime")
  rt.DT_MDL = DT
  for mod in (fv, mpc, slc, rt):
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
  monkeypatch.delitem(sys.modules, "openpilot.frogpilot.controls.lib.curve_speed_controller", raising=False)
  root = __import__("pathlib").Path(__file__).resolve().parents[3]
  spec = importlib.util.spec_from_file_location("_fp_vcruise_under_test", root / "frogpilot/controls/lib/frogpilot_vcruise.py")
  m = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(m)
  yield m
  sys.modules.pop("openpilot.frogpilot.controls.lib.curve_speed_controller", None)


def _make(m, fix, monkeypatch, tmp_path):
  p = tmp_path / "CSCStraightFix"
  p.write_text("1" if fix else "0")
  monkeypatch.setattr(g, "_csc_straight_fix_path", p)
  planner = N(cem=N(stop_light_detected=False), model_stopped=False, model_length=200.0, road_curvature=0.0,
              road_curvature_detected=False, time_to_curve=4.0, frogpilot_weather=N(weather_id=0),
              tracking_lead=False, driving_in_curve=False, lateral_acceleration=0.0)
  return m.FrogPilotVCruise(planner), planner


def _sm(v_ego, blinker=False):
  return {"controlsState": N(enabled=True, vCruiseCluster=0.0), "carState": N(vEgoCluster=v_ego, gasPressed=False,
          leftBlinker=blinker, rightBlinker=False), "frogpilotCarState": N(accelPressed=False), "carControl": N(longActive=True)}


TOGGLES = N(force_stops=False, car_make="tesla", car_model="TESLA_AP1_MODELS", curve_speed_controller=True,
            speed_limit_controller=False, show_speed_limits=False)


def _run(vc, planner, frames, v_ego=23.7):
  """frames: list of road curvature per 0.05 s frame (0 = straight). Returns planner targets."""
  out, active = [], []
  for k in frames:
    planner.road_curvature = k
    # stock planner flag: predicted lat accel > 1.0 m/s^2 at vEgo
    planner.road_curvature_detected = k != 0 and (1 / abs(k)) ** 0.5 < v_ego
    out.append(vc.update(None, None, False, REF, v_ego, _sm(v_ego), TOGGLES))
    active.append(vc.csc_controlling_speed)
  return out, active


def _flicker(n=400):
  return [STRAIGHT_K if i % 9 in (0, 1) else 0.0 for i in range(n)]


def test_straight_flicker_stock_dips(vcruise_mod, monkeypatch, tmp_path):
  vc, planner = _make(vcruise_mod, False, monkeypatch, tmp_path)
  out, active = _run(vc, planner, _flicker())
  assert sum(active) > 50          # stock takes over on every flicker
  assert min(out) == pytest.approx(23.7)   # and seeds the target at vEgo (below the 24.6 set)


def test_straight_flicker_fixed_no_detection_no_dip(vcruise_mod, monkeypatch, tmp_path):
  vc, planner = _make(vcruise_mod, True, monkeypatch, tmp_path)
  out, active = _run(vc, planner, _flicker())
  assert not any(active)
  assert min(out) == pytest.approx(REF)


def test_real_curve_detects_and_slows(vcruise_mod, monkeypatch, tmp_path):
  frames = [0.0] * 20 + [REAL_K] * 120 + [0.0] * 20
  csc = (2.0 * 144.0) ** 0.5
  vc, planner = _make(vcruise_mod, True, monkeypatch, tmp_path)
  out, active = _run(vc, planner, frames)
  first = active.index(True)
  assert first == 20 + round(HOLD_S / DT) - 1
  assert out[first] == pytest.approx(csc, abs=0.01)       # seeded at min(set, curve speed), not vEgo
  assert min(out[20:140]) == pytest.approx(csc, abs=0.01)
  assert not active[-1] and out[-1] == pytest.approx(REF)  # released after the curve

  vc2, planner2 = _make(vcruise_mod, False, monkeypatch, tmp_path)
  out2, active2 = _run(vc2, planner2, frames)
  assert min(out2[20:140]) == pytest.approx(csc, abs=0.01)  # stock reaches the same curve speed


def test_blinker_blocks(vcruise_mod, monkeypatch, tmp_path):
  vc, planner = _make(vcruise_mod, True, monkeypatch, tmp_path)
  planner.road_curvature, planner.road_curvature_detected = REAL_K, True
  for _ in range(40):
    vc.update(None, None, False, REF, 23.7, _sm(23.7, blinker=True), TOGGLES)
    assert not vc.csc_controlling_speed
