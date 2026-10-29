"""regen_aware.py: capacity, estimator, and the add-only stop shaper."""
import numpy as np

from openpilot.selfdrive.car.tesla import regen_aware as ra
from openpilot.selfdrive.car.tesla.values import CAR

DT = 0.05


def test_toggle():
  assert ra.regen_aware_enabled(CAR.TESLA_AP1_MODELS, stored="")
  assert not ra.regen_aware_enabled(CAR.TESLA_AP1_MODELS, stored="0")
  assert not ra.regen_aware_enabled("OTHER", stored="1")


def test_capacity_shape():
  assert ra.regen_capacity(0.5, 30) == 0.0
  assert abs(ra.regen_capacity(10.0, 30) - 30e3 * 0.9 / (2100 * 10)) < 1e-6
  assert ra.regen_capacity(5.0, 30) <= ra.A_CAP_MAX
  assert ra.regen_capacity(20.0, 18) < ra.regen_capacity(20.0, 30)


def test_cold_ceiling_and_recovery():
  e = ra.RegenPowerEstimator()
  e.update(10, 0.0, 0, 0, 2.0, DT)
  assert e.p_kw == ra.P_COLD_KW
  for _ in range(int(10 / DT)):
    e.update(10, 0.0, 0, 0, 20.0, DT)
  assert ra.P_COLD_KW < e.p_kw <= ra.P_COLD_KW + 0.5 * 10 + 1e-6


def test_limiting_detection():
  e = ra.RegenPowerEstimator()
  # request 1.5 m/s^2 at 12 m/s, torque only gives ~0.66 m/s^2 (-50 Nm), rpm ~ 3200
  for _ in range(int(0.4 / DT)):
    e.update(12, -1.5, -50, 3200, 20, DT)
  assert e.p_kw == ra.P_NOMINAL_KW
  for _ in range(int(0.4 / DT)):
    e.update(12, -1.5, -50, 3200, 20, DT)
  assert e.limited and e.p_kw < ra.P_NOMINAL_KW
  # below the fade band no limiting judgement
  e2 = ra.RegenPowerEstimator()
  for _ in range(40):
    e2.update(3.0, -1.5, -10, 800, 20, DT)
  assert e2.p_kw == ra.P_NOMINAL_KW


def test_shaper_never_reduces_braking():
  s = ra.RegenStopShaper()
  rng = np.random.default_rng(0)
  v = 12.0
  for i in range(400):
    a_plan = float(rng.uniform(-3.5, 1.0))
    d = None if i % 7 == 0 else float(rng.uniform(5, 120))
    out, _ = s.update(v, a_plan, d, 30, DT)
    assert out <= a_plan + 1e-9


def test_hard_plan_passes_immediately():
  s = ra.RegenStopShaper()
  out, _ = s.update(15.0, -3.0, 200.0, 30, DT)
  assert out == -3.0


def test_early_gentle_onset_and_stop_point():
  # 22 mph, stop 90 m ahead, plan coasting: shaper brings in gentle braking and stops near target
  s = ra.RegenStopShaper()
  v, x, stop = 9.8, 0.0, 90.0
  peak = 0.0
  started = None
  t = 0.0
  while v > ra.MIN_SPEED and t < 60:
    a, early = s.update(v, -0.1, stop - x, 30, DT)
    if early < 0 and started is None:
      started = x
    peak = min(peak, a)
    v = max(0.0, v + a * DT)
    x += v * DT
    t += DT
  assert started is not None and started < 50
  assert -1.2 < peak < -0.4
  assert abs((stop - x) - v * v / 2) < 4.0  # reaches walking speed close to the target


def test_lead_and_model_stop_distance():
  assert ra.lead_stop_distance(True, 40.0, 0.2, 6.0) == 34.0
  assert ra.lead_stop_distance(True, 40.0, 8.0, 6.0) is None
  assert ra.lead_stop_distance(False, 40.0, 0.0, 6.0) is None
  assert ra.model_stop_distance([0, 10, 20, 30], [9, 5, 0.3, 0]) == 20.0
  assert ra.model_stop_distance([0, 10], [9, 9]) is None
