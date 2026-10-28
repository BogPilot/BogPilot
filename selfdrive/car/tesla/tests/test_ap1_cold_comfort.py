"""ColdComfortRegen: default-off toggle, comfort/t_follow bias, capacity-clipped early brake.

Add-only: the early request is min(plan, early). Comfort only drops, t_follow only
rises. STOP_DISTANCE at v = 0 is unchanged. The 5 C regen power ceiling is unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

_ACCEL_MIN = -3.5
_ACCEL_MAX = 2.0
if "openpilot.selfdrive.car.interfaces" not in sys.modules:
  _iface = MagicMock()
  _iface.ACCEL_MIN = _ACCEL_MIN
  _iface.ACCEL_MAX = _ACCEL_MAX
  sys.modules["openpilot.selfdrive.car.interfaces"] = _iface

_solver_mod = "openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.c_generated_code.acados_ocp_solver_pyx"
if _solver_mod not in sys.modules:
  _solver_ns = MagicMock()
  _solver_ns.AcadosOcpSolverCython = MagicMock(name="AcadosOcpSolverCython")
  sys.modules[_solver_mod] = _solver_ns

if "casadi" not in sys.modules:
  sys.modules["casadi"] = MagicMock()

from openpilot.selfdrive.car.tesla import regen_aware as ra  # noqa: E402
from openpilot.selfdrive.car.tesla import regen_brake as rb  # noqa: E402
from openpilot.selfdrive.car.tesla.values import CAR  # noqa: E402
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (  # noqa: E402
  STOP_DISTANCE,
  comfort_distance_delta,
  desired_follow_distance,
)

DT = 0.05
ROOT = Path(__file__).resolve().parents[4]


def test_toggle_default_off(tmp_path, monkeypatch):
  path = tmp_path / "ColdComfortRegen"
  monkeypatch.setattr(rb, "_cold_comfort_path", path)
  assert rb.cold_comfort_regen_enabled(CAR.TESLA_AP1_MODELS) is False
  assert rb.cold_comfort_regen_enabled(CAR.TESLA_AP1_MODELS, stored="") is False
  assert rb.cold_comfort_regen_enabled(CAR.TESLA_AP1_MODELS, stored="0") is False
  assert rb.cold_comfort_regen_enabled(CAR.TESLA_AP1_MODELS, stored="maybe") is False
  assert rb.cold_comfort_regen_enabled(CAR.TESLA_AP1_MODELS, stored="1") is True
  assert rb.cold_comfort_regen_enabled(CAR.TESLA_AP1_MODELS, stored=b"on") is True
  assert rb.cold_comfort_regen_enabled(CAR.TESLA_AP2_MODELS, stored="1") is False
  assert rb.cold_comfort_regen_enabled("HONDA_CIVIC", stored="1") is False
  rb.set_cold_comfort_regen(True, path=path)
  assert rb.cold_comfort_regen_enabled(CAR.TESLA_AP1_MODELS) is True
  rb.set_cold_comfort_regen(False, path=path)
  assert rb.cold_comfort_regen_enabled(CAR.TESLA_AP1_MODELS) is False


def test_off_leaves_comfort_and_follow_unchanged():
  for amb, plateau in ((13.0, False), (0.0, False), (20.0, True), (None, True)):
    assert rb.apply_cold_comfort_brake(rb.AP1_REGEN_COMFORT_BRAKE, amb, plateau, False) == rb.AP1_REGEN_COMFORT_BRAKE
    assert rb.apply_cold_comfort_brake(rb.STOCK_COMFORT_BRAKE, amb, plateau, False) == rb.STOCK_COMFORT_BRAKE
    assert rb.apply_cold_t_follow(1.45, amb, plateau, False) == 1.45
  assert rb.apply_cold_t_follow(0.0, 0.0, True, True) == 0.0


def test_cool_band_is_a_nudge_and_hard_cold_eases_further():
  assert rb.HARD_COLD_AMBIENT_C == ra.COLD_AMBIENT_C == 5.0
  assert rb.COOL_AMBIENT_C == 15.0
  base = rb.AP1_REGEN_COMFORT_BRAKE
  assert rb.cold_comfort_level(13.0, False) == "cool"
  assert rb.cold_comfort_level(15.0, False) == "cool"
  assert rb.cold_comfort_level(15.01, False) == "none"
  assert rb.cold_comfort_level(5.01, False) == "cool"
  assert rb.cold_comfort_level(5.0, False) == "limited"
  assert rb.cold_comfort_level(20.0, True) == "limited"
  assert rb.cold_comfort_level(None, False) == "none"

  cool = rb.apply_cold_comfort_brake(base, 13.0, False, True)
  limited = rb.apply_cold_comfort_brake(base, 4.0, False, True)
  plateau = rb.apply_cold_comfort_brake(base, 22.0, True, True)
  warm = rb.apply_cold_comfort_brake(base, 22.0, False, True)
  assert cool == base - rb.COOL_COMFORT_DROP
  assert limited == base - rb.LIMITED_COMFORT_DROP == 0.7
  assert plateau == 0.7
  assert warm == base
  assert cool < base
  assert limited < cool

  assert rb.apply_cold_t_follow(1.45, 13.0, False, True) == 1.45 + rb.COOL_T_FOLLOW_ADD
  assert rb.apply_cold_t_follow(1.45, 4.0, False, True) == 1.45 + rb.LIMITED_T_FOLLOW_ADD
  assert rb.apply_cold_t_follow(2.9, 4.0, False, True) == 3.0
  assert rb.apply_cold_t_follow(1.45, 22.0, False, True) == 1.45


def test_bias_never_raises_comfort_or_shrinks_follow():
  for base in (0.7, 1.0, 2.5):
    for amb, plateau in ((13.0, False), (0.0, True), (25.0, False)):
      got = rb.apply_cold_comfort_brake(base, amb, plateau, True)
      assert got <= base + 1e-9
      assert got >= rb.COLD_COMFORT_FLOOR - 1e-9
      assert rb.apply_cold_t_follow(1.2, amb, plateau, True) >= 1.2


def test_stopped_gap_unchanged():
  for cb in (0.95, 0.7, 1.0, 2.5):
    assert comfort_distance_delta(0.0, cb) == 0.0
    assert desired_follow_distance(0.0, 0.0, 1.45 + 0.40, comfort_brake=cb) == STOP_DISTANCE
    assert desired_follow_distance(0.0, 0.0, 2.0, comfort_brake=cb) == STOP_DISTANCE


def test_cool_does_not_change_power_ceiling():
  e = ra.RegenPowerEstimator()
  e.update(10.0, 0.0, 0.0, 0.0, 13.0, DT)
  assert e.ceiling(13.0) == ra.P_NOMINAL_KW
  assert e.p_kw == ra.P_NOMINAL_KW
  assert e.ceiling(5.0) == ra.P_COLD_KW
  assert e.ceiling(20.0) == ra.P_NOMINAL_KW


def test_early_request_is_capacity_not_a_fixed_floor():
  # High speed, low estimated power: well under 1.0 m/s^2, equal to capacity.
  decel = ra.cold_comfort_decel(25.0, 10.0)
  cap = ra.regen_capacity(25.0, 10.0)
  assert decel == cap
  assert 0.0 < decel < 0.5
  # Nominal highway power is the gentle target, still not above capacity.
  decel_nom = ra.cold_comfort_decel(19.0, 30.0)
  assert decel_nom <= ra.regen_capacity(19.0, 30.0) + 1e-9
  assert decel_nom < 1.0


def _step(early, n, v, d, v_lead, a_lead, p, a_plan=0.0):
  out = (a_plan, 0.0)
  for _ in range(n):
    out = early.update(v, a_plan, True, d, v_lead, a_lead, p, DT)
  return out


def test_closing_decelerating_lead_ramps_in_and_stays_inside_capacity():
  early = ra.ColdComfortEarly()
  v, d, v_lead, a_lead, p = 18.8, 46.0, 18.4, -0.25, 30.0
  cap = ra.regen_capacity(v, p)
  # Not yet persisted.
  out, a_early = _step(early, int(0.40 / DT), v, d, v_lead, a_lead, p)
  assert out == 0.0 and a_early == 0.0
  assert early.active is False
  # Persist, then a bit of onset jerk.
  out, a_early = _step(early, int(0.80 / DT), v, d, v_lead, a_lead, p)
  assert early.active is True
  assert a_early < -0.05
  assert out == a_early
  assert a_early >= -cap - 1e-9
  # Settled request matches the capacity clip, not -1.0.
  out, a_early = _step(early, int(3.0 / DT), v, d, v_lead, a_lead, p)
  assert abs(a_early - -ra.cold_comfort_decel(v, p)) < 1e-6
  assert a_early > -1.0
  assert a_early >= -cap - 1e-9


def test_matched_or_far_or_stopped_lead_does_not_arm():
  early = ra.ColdComfortEarly()
  # Speeds matched, lead only creeping slower: closing threshold not met.
  _step(early, int(2.0 / DT), 18.8, 46.0, 18.7, -0.25, 30.0)
  assert early.active is False
  # Closing, but the lead is not decelerating (a stopped lead is RegenAwareStop's job).
  early.reset()
  _step(early, int(2.0 / DT), 18.8, 40.0, 0.2, 0.0, 30.0)
  assert early.active is False
  # Decelerating, but too far away.
  early.reset()
  _step(early, int(2.0 / DT), 18.8, 18.8 * 5.0, 18.0, -0.4, 30.0)
  assert early.active is False
  # Lead pulling away.
  early.reset()
  _step(early, int(2.0 / DT), 18.8, 40.0, 20.0, -0.4, 30.0)
  assert early.active is False


def test_hold_then_release_and_hard_plan_passes():
  early = ra.ColdComfortEarly()
  v, d, p = 18.8, 46.0, 30.0
  _step(early, int(1.2 / DT), v, d, 18.4, -0.25, p)
  assert early.active is True
  # Condition drops. Hold keeps the floor, still capacity-clipped.
  cap = ra.regen_capacity(v, p)
  out, a_early = _step(early, int(0.40 / DT), v, d, 19.5, 0.2, p, a_plan=-0.05)
  assert early.active is True
  assert a_early < -0.05
  assert a_early >= -cap - 1e-9
  assert out == min(-0.05, a_early)
  # Past the hold, the request releases.
  _step(early, int(1.5 / DT), v, d, 19.5, 0.2, p)
  assert early.active is False
  # A hard plan is never delayed, including before the early request exists.
  early.reset()
  out, _ = early.update(v, -3.5, True, d, 18.4, -0.30, p, DT)
  assert out == -3.5


def test_low_capacity_hold_is_not_a_fixed_one_mps():
  early = ra.ColdComfortEarly()
  v, p = 25.0, 10.0
  cap = ra.regen_capacity(v, p)
  _step(early, int(2.0 / DT), v, 40.0, 24.0, -0.4, p)
  assert early.active is True
  _, a_early = _step(early, int(2.0 / DT), v, 40.0, 24.0, -0.4, p)
  assert -cap - 1e-6 <= a_early < -0.05
  assert a_early > -1.0


def test_early_never_reduces_braking():
  early = ra.ColdComfortEarly()
  rng = np.random.default_rng(1)
  v = 16.0
  for i in range(400):
    a_plan = float(rng.uniform(-3.5, 1.0))
    d = float(rng.uniform(5.0, 80.0))
    v_lead = float(rng.uniform(0.0, 20.0))
    a_lead = float(rng.uniform(-2.0, 0.5))
    out, _ = early.update(v, a_plan, True, d, v_lead, a_lead, 30.0, DT)
    assert out <= a_plan + 1e-9


def test_reset_clears_the_floor():
  early = ra.ColdComfortEarly()
  _step(early, int(1.5 / DT), 18.8, 46.0, 18.4, -0.3, 30.0)
  assert early.a_cmd < 0.0
  early.reset()
  out, a_early = early.update(18.8, 0.2, False, 46.0, 18.4, -0.3, 30.0, DT)
  assert early.active is False
  assert a_early == 0.0
  assert out == 0.2


def test_not_a_params_key_and_profile_leaves_it_off():
  from openpilot.selfdrive.car.tesla import ap1_defaults as ap1
  variables = (ROOT / "frogpilot/common/frogpilot_variables.py").read_text()
  assert '("ColdComfortRegen"' not in variables
  assert "ColdComfortRegen" in ap1.FILE_TOGGLE_KEYS
  assert "ColdComfortRegen" not in ap1.AP1_FILE_TOGGLE_PROFILE


def test_wiring_is_add_only_and_leaves_safety_alone():
  planner = (ROOT / "selfdrive/controls/lib/longitudinal_planner.py").read_text()
  following = (ROOT / "frogpilot/controls/lib/frogpilot_following.py").read_text()
  safety = (ROOT / "panda/board/safety/safety_tesla.h").read_text()
  iface = (ROOT / "selfdrive/car/interfaces.py").read_text()
  vehicle = (ROOT / "frogpilot/ui/qt/offroad/vehicle_settings.cc").read_text()
  assert "cold_comfort_regen_enabled" in planner
  assert "apply_cold_comfort_brake" in planner
  assert "ColdComfortEarly" in planner
  assert "accel_clip[0] = min(accel_clip[0], self.cold_a_early)" in planner
  assert "cold_comfort_regen_enabled" in following
  assert "apply_cold_t_follow" in following
  assert "ap1_comfort_brake" in following
  assert "ColdComfort" not in safety
  assert "ACCEL_MIN = -3.5" in iface
  assert 'param == "ColdComfortRegen"' in vehicle
  assert "FileToggleControl(param, false, title, desc, icon)" in vehicle
  long_mpc = (ROOT / "selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py").read_text()
  assert "STOP_DISTANCE" in long_mpc
