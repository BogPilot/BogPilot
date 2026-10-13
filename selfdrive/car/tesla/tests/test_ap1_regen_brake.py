"""AP1 regen-sized comfort braking: toggle, consistency, MPC obstacle wiring.

Device acados .so is AArch64. These tests mock the cython solver to exercise the
Python-side comfort_brake path and prove the lead-model consistency fix:
delta uses previous-solution ego speeds (not an assumed braking coast).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

# --- Import isolation (AArch64 .so not loadable on this host) ----------------
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

from cereal import log  # noqa: E402
from openpilot.selfdrive.car.tesla.regen_brake import (  # noqa: E402
  AP1_REGEN_COMFORT_BRAKE,
  STOCK_COMFORT_BRAKE,
  ap1_comfort_brake,
  regen_comfort_brake_enabled,
  set_regen_comfort_brake,
)
from openpilot.selfdrive.car.tesla.values import CAR  # noqa: E402
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (  # noqa: E402
  COMFORT_BRAKE,
  N,
  STOP_DISTANCE,
  T_IDXS,
  LongitudinalMpc,
  comfort_distance_delta,
  desired_follow_distance,
  get_T_FOLLOW,
  get_safe_obstacle_distance,
  get_stopped_equivalence_factor,
  lead_comfort_brake,
)


# --- Toggle / math ----------------------------------------------------------

def test_stock_comfort_brake_matches_mpc_constant():
  assert STOCK_COMFORT_BRAKE == COMFORT_BRAKE
  assert AP1_REGEN_COMFORT_BRAKE < COMFORT_BRAKE
  assert 1.0 <= AP1_REGEN_COMFORT_BRAKE <= 1.5


def test_regen_toggle_default_on_for_ap1_only(tmp_path, monkeypatch):
  import openpilot.selfdrive.car.tesla.regen_brake as rb
  path = tmp_path / "RegenComfortBrake"
  monkeypatch.setattr(rb, "_regen_comfort_path", path)

  assert regen_comfort_brake_enabled(CAR.TESLA_AP1_MODELS) is True
  assert ap1_comfort_brake(CAR.TESLA_AP1_MODELS) == AP1_REGEN_COMFORT_BRAKE
  assert regen_comfort_brake_enabled(CAR.TESLA_AP2_MODELS) is False
  assert ap1_comfort_brake("HONDA_CIVIC") == COMFORT_BRAKE


def test_regen_toggle_explicit_off_on(tmp_path, monkeypatch):
  import openpilot.selfdrive.car.tesla.regen_brake as rb
  path = tmp_path / "RegenComfortBrake"
  monkeypatch.setattr(rb, "_regen_comfort_path", path)
  set_regen_comfort_brake(False, path=path)
  assert ap1_comfort_brake(CAR.TESLA_AP1_MODELS) == COMFORT_BRAKE
  set_regen_comfort_brake(True, path=path)
  assert ap1_comfort_brake(CAR.TESLA_AP1_MODELS) == AP1_REGEN_COMFORT_BRAKE


def test_comfort_distance_delta_zero_at_stop_and_scales_with_v():
  assert comfort_distance_delta(0.0, AP1_REGEN_COMFORT_BRAKE) == 0.0
  assert comfort_distance_delta(10.0, COMFORT_BRAKE) == 0.0
  d10 = float(comfort_distance_delta(10.0, AP1_REGEN_COMFORT_BRAKE))
  d20 = float(comfort_distance_delta(20.0, AP1_REGEN_COMFORT_BRAKE))
  assert d10 > 0
  assert d20 == pytest.approx(4.0 * d10, rel=1e-6)
  assert desired_follow_distance(0.0, 0.0, 1.45, comfort_brake=AP1_REGEN_COMFORT_BRAKE) == STOP_DISTANCE


@pytest.mark.parametrize("v0_mph", [24, 35, 45])
def test_stopped_lead_desired_distance_earlier_gentler_same_gap(v0_mph):
  v0 = v0_mph * 0.44704
  t_follow = get_T_FOLLOW()
  d_stock = float(get_safe_obstacle_distance(v0, t_follow, COMFORT_BRAKE))
  d_regen = float(get_safe_obstacle_distance(v0, t_follow, AP1_REGEN_COMFORT_BRAKE))
  assert d_regen > d_stock + 5.0
  assert desired_follow_distance(0.0, 0.0, t_follow, COMFORT_BRAKE) == STOP_DISTANCE
  assert desired_follow_distance(0.0, 0.0, t_follow, AP1_REGEN_COMFORT_BRAKE) == STOP_DISTANCE


def test_non_tesla_comfort_brake_unchanged():
  assert ap1_comfort_brake("HONDA_CIVIC") == COMFORT_BRAKE
  assert ap1_comfort_brake(CAR.TESLA_AP2_MODELS) == COMFORT_BRAKE


# --- Mocked MPC helpers -----------------------------------------------------

class _FakeSolver:
  def __init__(self, *args, **kwargs):
    self.lbx0 = None
    self._stats = {"time_tot": [0.0], "time_qp": [0.0], "time_lin": [0.0], "time_sim": [0.0]}

  def reset(self):
    pass

  def cost_set(self, i, name, val):
    pass

  def set(self, i, name, val):
    pass

  def constraints_set(self, i, name, val):
    if name == "lbx":
      self.lbx0 = np.array(val, dtype=float).copy()

  def solve(self):
    return 0

  def get_stats(self, name):
    return self._stats[name]

  def get(self, i, name):
    if name == "x":
      if self.lbx0 is not None:
        return self.lbx0.copy()
      return np.zeros(3)
    if name == "u":
      return np.zeros(1)
    raise KeyError(name)


def _install_fake_solver(monkeypatch):
  import openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc as lm
  monkeypatch.setattr(lm, "AcadosOcpSolverCython", _FakeSolver)


def _toggles():
  return SimpleNamespace(
    lead_detection_probability=0.5,
    human_following=False,
    model_version="v8",
  )


def _model_with_lead(prob=0.95):
  lead = log.ModelDataV2.LeadDataV3.new_message()
  lead.prob = float(prob)
  lead.x = [0.0] * 6
  lead.v = [0.0] * 6
  model = log.ModelDataV2.new_message()
  model.leadsV3 = [lead, lead]
  return model


def _radar(d_rel, v_lead=0.0, a_lead=0.0):
  rs = log.RadarState.new_message()
  for attr in ("leadOne", "leadTwo"):
    lead = getattr(rs, attr)
    lead.dRel = float(d_rel)
    lead.vLead = float(v_lead)
    lead.vLeadK = float(v_lead)
    lead.aLeadK = float(a_lead)
    lead.aLeadTau = 1.5
    lead.status = True
    lead.modelProb = 0.95
  return rs


def _expected_lead_obstacle(d_rel, v_lead_traj, v_ego_traj, comfort_brake):
  """Python-side expected x_obstacle after comfort adjustment (stopped-equiv + delta)."""
  lead_obs = d_rel + get_stopped_equivalence_factor(v_lead_traj, comfort_brake)
  # For constant dRel extrapolate_lead: x grows with v; use same formula as MPC for v=const a=0
  # Here callers pass already-extrapolated lead x/v or constant d_rel for v_lead==v cases.
  if comfort_brake < COMFORT_BRAKE - 1e-9:
    lead_obs = lead_obs - comfort_distance_delta(v_ego_traj, comfort_brake)
  return lead_obs


def _mpc_with_solution(monkeypatch, v_ego, d_rel, comfort_brake, v_lead=None, a_lead=0.0,
                       v_solution=None):
  """Build MPC, seed v_solution (previous plan), run one update, return mpc."""
  _install_fake_solver(monkeypatch)
  mpc = LongitudinalMpc()
  mpc.mode = "acc"
  mpc.set_weights(prev_accel_constraint=False)
  if v_lead is None:
    v_lead = v_ego
  if v_solution is None:
    v_solution = np.full(N + 1, float(v_ego))
  mpc.v_solution = np.array(v_solution, dtype=float)
  # Also seed x_sol speeds so any reader of x_sol[:,1] matches
  mpc.x_sol[:, 1] = mpc.v_solution
  mpc.set_cur_state(float(v_ego), 0.0)
  z = np.zeros(N + 1)
  mpc.update(
    float(v_ego) + 5.0, _model_with_lead(), _radar(d_rel, v_lead, a_lead),
    z, z, z, z, get_T_FOLLOW(), _ACCEL_MIN, _ACCEL_MAX, _toggles(), False,
    comfort_brake=comfort_brake,
  )
  return mpc


def _lead_x_obstacle_params(mpc):
  """Return the lead-sourced x_obstacle trajectory (params[:,2]) when lead is active."""
  assert mpc.source.startswith("lead"), mpc.source
  return mpc.params[:, 2].copy()


# --- Consistency: steady follow / slower lead / danger obstacle -------------

@pytest.mark.parametrize("v", [10.0, 20.0, 30.0])
def test_steady_following_obstacle_matches_stock_every_node(monkeypatch, v):
  """(a) v_lead == v_ego: regen x_obstacle identical to stock at every node (cm)."""
  # Gap large enough that lead wins over cruise at h=0, but we compare full traj
  # of the *lead* obstacle before min-with-cruise by reconstructing from formula.
  d_rel = 5.0  # close so lead dominates chain; absolute d cancels in stock-vs-regen
  stock = _mpc_with_solution(monkeypatch, v, d_rel, COMFORT_BRAKE, v_lead=v)
  regen = _mpc_with_solution(monkeypatch, v, d_rel, AP1_REGEN_COMFORT_BRAKE, v_lead=v)

  # For steady follow, desired gap is t_follow*v + STOP for BOTH comfort brakes
  # (v^2 terms cancel). Emulated regen obstacle must match stock obstacle.
  assert stock.source.startswith("lead")
  assert regen.source.startswith("lead")
  np.testing.assert_allclose(regen.params[:, 2], stock.params[:, 2], atol=0.01)

  # Effective desired follow distance identical
  t_follow = get_T_FOLLOW()
  assert desired_follow_distance(v, v, t_follow, COMFORT_BRAKE) == pytest.approx(
    desired_follow_distance(v, v, t_follow, AP1_REGEN_COMFORT_BRAKE), abs=1e-9)


@pytest.mark.parametrize("v_ego,v_lead", [(20.0, 15.0), (30.0, 20.0), (15.0, 10.0)])
def test_slower_lead_never_less_conservative_than_stock(monkeypatch, v_ego, v_lead):
  """(b) v_lead < v_ego: regen required distance >= stock at every node."""
  d_rel = 40.0
  stock = _mpc_with_solution(monkeypatch, v_ego, d_rel, COMFORT_BRAKE, v_lead=v_lead)
  regen = _mpc_with_solution(monkeypatch, v_ego, d_rel, AP1_REGEN_COMFORT_BRAKE, v_lead=v_lead)
  assert stock.source.startswith("lead")
  assert regen.source.startswith("lead")
  # Smaller x_obstacle => larger desired gap (more conservative). Never larger.
  assert np.all(regen.params[:, 2] <= stock.params[:, 2] + 0.01)

  # Closed-form desired gap also >= stock
  t_follow = get_T_FOLLOW()
  for vv in (v_ego, (v_ego + v_lead) / 2, v_lead):
    assert desired_follow_distance(vv, v_lead, t_follow, AP1_REGEN_COMFORT_BRAKE) >= \
           desired_follow_distance(vv, v_lead, t_follow, COMFORT_BRAKE) - 1e-9


@pytest.mark.parametrize("v_ego,v_lead", [(20.0, 20.0), (20.0, 15.0), (25.0, 10.0), (15.0, 0.0)])
def test_lead_obstacle_never_larger_than_stock_when_v_lead_le_v_ego(monkeypatch, v_ego, v_lead):
  """Danger constraint: with v_lead <= v_ego, regen x_obstacle <= stock at every node."""
  d_rel = 35.0
  stock = _mpc_with_solution(monkeypatch, v_ego, d_rel, COMFORT_BRAKE, v_lead=v_lead)
  regen = _mpc_with_solution(monkeypatch, v_ego, d_rel, AP1_REGEN_COMFORT_BRAKE, v_lead=v_lead)
  # Analytic: regen - stock = scale * (v_lead^2 - v_ego^2) <= 0 when v_lead <= v_ego
  scale = 0.5 * (1.0 / AP1_REGEN_COMFORT_BRAKE - 1.0 / COMFORT_BRAKE)
  assert scale * (v_lead ** 2 - v_ego ** 2) <= 1e-9
  if stock.source.startswith("lead") and regen.source.startswith("lead"):
    assert np.all(regen.params[:, 2] <= stock.params[:, 2] + 0.01)


def test_delta_uses_solution_speeds_not_assumed_braking(monkeypatch):
  """Regression: assumed braking coast made later-node delta too small (less safe).

  With constant previous-solution speeds equal to v_ego, steady follow must match
  stock. Seeding a fake decelerating v_solution must NOT be what update invents
  when the real previous solution was constant.
  """
  v = 20.0
  d_rel = 8.0
  # Correct: previous solution was steady (constant v)
  regen_ok = _mpc_with_solution(
    monkeypatch, v, d_rel, AP1_REGEN_COMFORT_BRAKE, v_lead=v,
    v_solution=np.full(N + 1, v),
  )
  stock = _mpc_with_solution(monkeypatch, v, d_rel, COMFORT_BRAKE, v_lead=v)
  np.testing.assert_allclose(regen_ok.params[:, 2], stock.params[:, 2], atol=0.01)

  # If we wrongly used a braking coast for delta, later nodes would have smaller
  # delta → larger obstacle than stock. Prove that formula is unsafe:
  a_est = -AP1_REGEN_COMFORT_BRAKE
  v_bad = np.maximum(v + a_est * T_IDXS, 0.0)
  delta_bad = comfort_distance_delta(v_bad, AP1_REGEN_COMFORT_BRAKE)
  delta_ok = comfort_distance_delta(np.full(N + 1, v), AP1_REGEN_COMFORT_BRAKE)
  # Bad delta is smaller at later nodes → obstacle would be farther (bad)
  assert np.any(delta_bad[1:] < delta_ok[1:] - 0.5)
  # And that would make regen obstacle > stock for steady follow:
  lead_obs = d_rel + get_stopped_equivalence_factor(v, AP1_REGEN_COMFORT_BRAKE)
  obs_bad = lead_obs - delta_bad
  obs_stock = d_rel + get_stopped_equivalence_factor(v, COMFORT_BRAKE)
  assert np.any(obs_bad > obs_stock + 0.5)


# --- Stopped lead / cut-in / hard brake (a_min authority) -------------------

def test_stopped_lead_regen_obstacle_smaller_final_gap_formula_unchanged(monkeypatch):
  """(c) Stopped lead: regen obstacle smaller (earlier brake); v=0 gap unchanged."""
  v = 15.0
  d_rel = 40.0
  stock = _mpc_with_solution(monkeypatch, v, d_rel, COMFORT_BRAKE, v_lead=0.0)
  regen = _mpc_with_solution(monkeypatch, v, d_rel, AP1_REGEN_COMFORT_BRAKE, v_lead=0.0)
  assert stock.source.startswith("lead")
  assert regen.source.startswith("lead")
  assert regen.params[0, 2] < stock.params[0, 2] - 5.0
  assert desired_follow_distance(0.0, 0.0, get_T_FOLLOW(), AP1_REGEN_COMFORT_BRAKE) == STOP_DISTANCE


def test_ap1_comfort_target_is_regen_ceiling_1p0():
  # 02a: regen alone topped out near 1.0 m/s^2 at 30-35 mph before friction joined.
  assert AP1_REGEN_COMFORT_BRAKE == 1.0


@pytest.mark.parametrize("comfort", [1.0, 1.2])
def test_standstill_lead_obstacle_identical_to_stock_any_comfort(monkeypatch, comfort):
  """Stopped behind a stopped lead: x_obstacle equals stock at every node, so the
  stopped gap (STOP_DISTANCE, plus IncreasedStoppedDistance applied to dRel in
  radard) does not depend on the comfort target."""
  stock = _mpc_with_solution(monkeypatch, 0.0, 7.0, COMFORT_BRAKE, v_lead=0.0)
  regen = _mpc_with_solution(monkeypatch, 0.0, 7.0, comfort, v_lead=0.0)
  np.testing.assert_allclose(regen.params[:, 2], stock.params[:, 2], atol=1e-9)
  assert desired_follow_distance(0.0, 0.0, get_T_FOLLOW(), comfort) == STOP_DISTANCE


def test_cut_in_keeps_full_accel_min(monkeypatch):
  """(d) Close cut-in: a_min stays ACCEL_MIN for stock and regen."""
  for cb in (COMFORT_BRAKE, AP1_REGEN_COMFORT_BRAKE):
    mpc = _mpc_with_solution(monkeypatch, 20.0, 12.0, cb, v_lead=0.0)
    assert mpc.params[0, 0] == pytest.approx(_ACCEL_MIN)
    assert np.all(mpc.params[:, 0] == pytest.approx(_ACCEL_MIN))


def test_hard_braking_lead_keeps_full_accel_min(monkeypatch):
  for cb in (COMFORT_BRAKE, AP1_REGEN_COMFORT_BRAKE):
    mpc = _mpc_with_solution(
      monkeypatch, 25.0, 40.0, cb, v_lead=25.0, a_lead=-4.0)
    assert mpc.params[0, 0] == pytest.approx(_ACCEL_MIN)


# --- Wiring / panda unchanged -----------------------------------------------

def test_planner_wires_ap1_comfort_brake_only():
  from pathlib import Path
  root = Path(__file__).resolve().parents[4]
  planner = (root / "selfdrive/controls/lib/longitudinal_planner.py").read_text()
  assert "ap1_comfort_brake" in planner
  assert "comfort_brake=comfort_brake" in planner
  long_mpc = (root / "selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py").read_text()
  assert "self.v_solution" in long_mpc
  assert "a_est" not in long_mpc  # assumed braking coast removed
  assert "comfort_distance_delta" in long_mpc
  cost_c = (root / "selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/long_cost/long_cost_y_fun.c").read_text()
  assert "a4=5.;" in cost_c
  assert "a4=6.;" in cost_c
  assert "1.2" not in cost_c
  safety = (root / "panda/board/safety/safety_tesla.h").read_text()
  assert "RegenComfort" not in safety
  iface = (root / "selfdrive/car/interfaces.py").read_text()
  assert "ACCEL_MIN = -3.5" in iface


def test_following_uses_ap1_comfort_for_desired_distance():
  from pathlib import Path
  src = (Path(__file__).resolve().parents[4] / "frogpilot/controls/lib/frogpilot_following.py").read_text()
  assert "ap1_comfort_brake" in src


# --- Braking lead: lead stopping term goes to stock (route 57 16:57:44 FCW) ----

def test_lead_comfort_brake_blend_off_by_default():
  import openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc as long_mpc
  assert long_mpc.AP1_LEAD_BRAKE_BLEND is False
  assert lead_comfort_brake(AP1_REGEN_COMFORT_BRAKE, -3.0) == AP1_REGEN_COMFORT_BRAKE


def test_lead_comfort_brake_blend(monkeypatch):
  import openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc as long_mpc
  monkeypatch.setattr(long_mpc, "AP1_LEAD_BRAKE_BLEND", True)
  cb = AP1_REGEN_COMFORT_BRAKE
  assert lead_comfort_brake(cb, 0.0) == cb
  assert lead_comfort_brake(cb, 0.5) == cb  # accelerating lead: regen value
  assert lead_comfort_brake(cb, -2.0) == COMFORT_BRAKE
  assert cb < lead_comfort_brake(cb, -0.6) < COMFORT_BRAKE
  # Non-AP1 / toggle off: unchanged.
  assert lead_comfort_brake(COMFORT_BRAKE, -2.0) == COMFORT_BRAKE


@pytest.mark.parametrize("a_lead", [-0.5, -1.0, -2.0, -3.0])
def test_braking_lead_obstacle_closer_than_before_and_stock(monkeypatch, a_lead):
  """Braking lead (blend enabled): x_obstacle <= the old regen-only value and <= stock (add-only)."""
  import openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc as long_mpc
  monkeypatch.setattr(long_mpc, "AP1_LEAD_BRAKE_BLEND", True)
  v_ego, v_lead, d_rel = 7.0, 3.5, 19.0
  stock = _mpc_with_solution(monkeypatch, v_ego, d_rel, COMFORT_BRAKE, v_lead=v_lead, a_lead=a_lead)
  regen = _mpc_with_solution(monkeypatch, v_ego, d_rel, AP1_REGEN_COMFORT_BRAKE, v_lead=v_lead, a_lead=a_lead)
  assert np.all(regen.params[:, 2] <= stock.params[:, 2] + 0.01)
  # Old behaviour (regen comfort on the lead term too) would put the obstacle farther.
  lead_x = regen.lead_xv_0[:, 0]
  lead_v = regen.lead_xv_0[:, 1]
  delta = comfort_distance_delta(np.maximum(regen.v_solution, 0.0), AP1_REGEN_COMFORT_BRAKE)
  old = lead_x + get_stopped_equivalence_factor(lead_v, AP1_REGEN_COMFORT_BRAKE) - delta
  assert np.all(regen.params[:, 2] <= old + 1e-6)
