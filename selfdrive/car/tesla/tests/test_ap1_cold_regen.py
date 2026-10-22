"""cold_regen.py: prior, lift-off sampling learner, persistence, and the handoff blend."""
import tempfile
from pathlib import Path

from openpilot.selfdrive.car.tesla import cold_regen as cr
from openpilot.selfdrive.car.tesla.values import CAR

DT = 0.01
WALL = 1.8e9


def f_to_c(f):
  return (f - 32.0) * 5.0 / 9.0


def run(lr, secs, v=12.0, a=0.0, gas=False, brake=False, long_active=False, amb=None):
  for _ in range(int(round(secs / DT))):
    lr.update(v, a, gas, brake, long_active, amb, DT)
  return lr.frac


def lift_off(lr, frac_of_warm, secs, v=12.0, amb=None):
  """Driver cruising on the pedal, then lifts off for `secs` at frac_of_warm of warm regen decel."""
  run(lr, 2.0, v=v, gas=True, amb=amb)
  run(lr, secs, v=v, a=-frac_of_warm * cr.warm_decel(v), amb=amb)
  return run(lr, 0.05, v=v, gas=True, amb=amb)   # back on the pedal: the lift-off is scored


def test_toggle():
  assert cr.cold_regen_enabled(CAR.TESLA_AP1_MODELS, stored="")
  assert cr.cold_regen_enabled(CAR.TESLA_AP1_MODELS, stored="1")
  for off in ("0", "false", "OFF"):
    assert not cr.cold_regen_enabled(CAR.TESLA_AP1_MODELS, stored=off)
  assert not cr.cold_regen_enabled("OTHER", stored="1")


def test_prior():
  assert cr.prior_fraction(f_to_c(70), 0) == 1.0
  assert cr.prior_fraction(f_to_c(59), 0) == 1.0
  assert abs(cr.prior_fraction(f_to_c(52), 0) - 0.45) < 1e-6
  assert cr.prior_fraction(f_to_c(52), 15) == 1.0
  assert cr.prior_fraction(f_to_c(25), 0) < cr.prior_fraction(f_to_c(45), 0)
  assert cr.prior_start(f_to_c(25))[1] > cr.prior_start(f_to_c(52))[1]
  assert cr.prior_fraction(None, 0) == 1.0
  assert cr.prior_fraction(-40.0, 0) == 1.0          # 0x208 raw 0 is not a reading
  assert cr.prior_slope_per_min(f_to_c(70)) == cr.DRIFT_MIN_PER_MIN


def test_learner_drops_without_temperature():
  """High-SoC case: no temperature signal at all, the samples alone pull it down."""
  lr = cr.RegenCapacityLearner(state_path=None)
  run(lr, 6.0, gas=True)
  assert lr.frac == 1.0
  f1 = lift_off(lr, 0.45, 4.0)
  assert lr.events == 1 and lr.samples == 1
  assert 0.45 < f1 < 0.6
  f2 = lift_off(lr, 0.45, 4.0)
  assert 0.45 <= f2 < 0.5


def test_downhill_coast_ignored():
  lr = cr.RegenCapacityLearner(state_path=None)
  run(lr, 6.0, gas=True, amb=f_to_c(68))
  lift_off(lr, 0.05, 8.0, amb=f_to_c(68))
  assert lr.frac == 1.0


def test_short_lift_off_cannot_drop():
  lr = cr.RegenCapacityLearner(state_path=None)
  run(lr, 6.0, gas=True)
  lift_off(lr, 0.6, 1.5 + cr.SAT_SETTLE_S - 0.1)
  run(lr, 0.5, gas=True)
  assert lr.frac == 1.0 and lr.events == 1 and lr.samples == 0


def test_regen_ramp_does_not_undershoot():
  """Cold regen builds slowly after the lift: the plateau, not the ramp, sets the estimate."""
  lr = cr.RegenCapacityLearner(state_path=None)
  run(lr, 6.0, gas=True)
  run(lr, 2.0, gas=True)
  run(lr, 2.5, a=+0.1)                                  # regen not built up yet
  run(lr, 3.0, a=-0.45 * cr.warm_decel(12.0))
  run(lr, 0.5, gas=True)
  assert lr.frac > 0.42


def test_learner_rises_and_ignores_op_and_pedals():
  lr = cr.RegenCapacityLearner(state_path=None)
  run(lr, 6.0, gas=True, amb=f_to_c(50))
  f0 = lr.frac
  assert f0 < 0.5
  run(lr, 5.0, a=-2.0, brake=True, amb=f_to_c(50))       # foot brake: no sample
  run(lr, 5.0, a=-2.0, long_active=True, amb=f_to_c(50))  # openpilot braking: no sample
  run(lr, 5.0, v=3.0, a=-2.0, amb=f_to_c(50))             # in the regen fade: no sample
  assert lr.events == 0
  f1 = lift_off(lr, 1.0, 3.0, amb=f_to_c(50))
  assert lr.samples == 1 and f1 > f0 + 0.2


def test_recovery_drift():
  lr = cr.RegenCapacityLearner(state_path=None)
  run(lr, 6.0, gas=True, amb=f_to_c(52))
  run(lr, 15 * 60, v=0.0, amb=f_to_c(52))
  assert lr.frac == 1.0


def test_warm_stays_full():
  lr = cr.RegenCapacityLearner(state_path=None)
  run(lr, 6.0, gas=True, amb=f_to_c(68))
  for frac in (1.0, 0.97, 1.1):
    lift_off(lr, frac, 3.0, amb=f_to_c(68))
  assert lr.frac >= cr.BLEND_OFF_FRAC


def test_persistence_resume_and_expiry():
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
    p = Path(d) / "ColdRegenState"
    lr = cr.RegenCapacityLearner(state_path=p, now_wall=WALL)
    run(lr, 6.0, gas=True)
    lift_off(lr, 0.45, 4.0)
    lr.save(WALL)
    saved = lr.frac
    short = cr.RegenCapacityLearner(state_path=p, now_wall=WALL + 4 * 60)
    run(short, 6.0, gas=True)
    assert saved <= short.frac < saved + 0.1
    long = cr.RegenCapacityLearner(state_path=p, now_wall=WALL + 3 * 3600)
    run(long, 6.0, gas=True)
    assert long.frac == 1.0
    p.write_text("garbage\n")
    bad = cr.RegenCapacityLearner(state_path=p, now_wall=WALL)
    run(bad, 6.0, gas=True)
    assert bad.frac == 1.0


def ramp(b, frac, a_from=0.0, a_to=-1.5, rate=-4.0, v=12.0, urgent=False):
  outs, a = [], a_from
  b.update(a, True, v, frac)
  while a > a_to:
    a = max(a_to, a + rate * DT)
    outs.append((a, b.update(a, True, v, frac, urgent=urgent)))
  for _ in range(100):
    outs.append((a, b.update(a, True, v, frac, urgent=urgent)))
  return outs


def test_blend_warm_is_passthrough():
  b = cr.ColdRegenBlend()
  assert all(o == t for t, o in ramp(b, 1.0))
  assert all(o == t for t, o in ramp(cr.ColdRegenBlend(), cr.BLEND_OFF_FRAC))


def test_blend_cold_bounded_and_converges():
  outs = ramp(cr.ColdRegenBlend(), 0.45)
  assert any(o > t for t, o in outs)
  for t, o in outs:
    assert t <= o <= t + cr.BLEND_MAX_LAG + 1e-9
  assert outs[-1][1] == outs[-1][0]
  for (_, o0), (_, o1) in zip(outs, outs[1:]):
    assert o0 - o1 <= 4.0 * DT + cr.BLEND_MAX_LAG  # never a step deeper than the request's own step


def test_blend_bypass():
  assert all(o == t for t, o in ramp(cr.ColdRegenBlend(), 0.45, urgent=True))
  b = cr.ColdRegenBlend()
  ramp(b, 0.45, a_to=-1.0)
  assert b.update(-2.5, True, 12.0, 0.45) == -2.5      # hard braking goes straight through
  assert cr.ColdRegenBlend().update(-1.0, False, 12.0, 0.45) == -1.0
  b = cr.ColdRegenBlend()
  assert all(o == t for t, o in ramp(b, 0.45, v=0.5))
