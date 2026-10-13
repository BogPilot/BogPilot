"""AP1 standstill experiment: standby DAS_control while disengaged (default off).

Tinkla tesla_unity_dev LONG_module.py 246-255 sends accState 4, setSpeed 0 and
standby limits while disengaged in drive. BogPilot sends accelMax 0 (not
Tinkla's +1.8) behind the AP1StandstillEngage file toggle (default ON on the
bogpilot-tesla-standstill test branch; write 0 to turn off) and panda
TESLA_FLAG_AP1_STANDSTILL. Frames are packed from the DBC and run through
libpanda. Not a driving validation: only a car test shows whether the DI
accepts a 0 mph engage with these values.
"""
from collections import deque
from types import SimpleNamespace

import pytest
from panda import Panda

from openpilot.selfdrive.car.tesla import toggles
from openpilot.selfdrive.car.tesla.actuator_plan import (
  ACC_ON, AP1_STANDBY_ACCEL_MAX, AP1_STANDBY_ACCEL_MIN, AP1_STANDBY_JERK_MAX, AP1_STANDBY_JERK_MIN,
  DAS_CONTROL_CHASSIS, ap1_standby_allowed, build_actuator_plan,
)
from openpilot.selfdrive.car.tesla.safety_flags import flags_for_candidate
from openpilot.selfdrive.car.tesla.teslacan import TeslaCAN
from openpilot.selfdrive.car.tesla.tests.test_ap1_gas_neutral import DbcPacker, decode_accel
from openpilot.selfdrive.car.tesla.values import CAR

AP1 = Panda.FLAG_TESLA_AP1 | Panda.FLAG_TESLA_LONG_CONTROL


def _plan(**kw):
  args = dict(frame=1, lat_active=False, hands_on_fault=False, openpilot_longitudinal_control=True,
              enabled=False, long_active=False, measured_angle_deg=0.0, requested_angle_deg=0.0,
              last_angle_deg=0.0, v_ego=0.0, accel=0.0, acc_state=ACC_ON, das_counters=deque([3, 4]),
              pcm_cancel=False, chassis_das_only=True)
  args.update(kw)
  return build_actuator_plan(**args)


def _frames(plan):
  can = TeslaCAN(DbcPacker(), DbcPacker())
  out = []
  for c in plan.longitudinal:
    jl = {} if c.jerk_min is None else {"jerk_min": c.jerk_min, "jerk_max": c.jerk_max}
    out.extend(can.create_longitudinal_commands(c.acc_state, c.target_speed, c.min_accel, c.max_accel, c.counter,
                                                chassis_only=True, **jl))
  return out


@pytest.fixture
def lp():
  from panda.tests.libpanda import libpanda_py
  l = libpanda_py.libpanda

  def init(param, allowed=False):
    l.set_timer(1000)
    assert l.set_safety_hooks(Panda.SAFETY_TESLA, param) == 0
    l.set_controls_allowed(allowed)
  return SimpleNamespace(l=l, py=libpanda_py, init=init,
                         tx=lambda f: bool(l.safety_tx_hook(libpanda_py.make_CANPacket(f[0], f[3], bytes(f[2])))))


def test_toggle_default_on_test_branch_and_ap1_only(tmp_path, monkeypatch):
  # bogpilot-tesla-standstill test branch: file missing means ON.
  monkeypatch.setattr(toggles, "_ap1_standstill_path", tmp_path / "AP1StandstillEngage")
  assert toggles.ap1_standstill_engage(CAR.TESLA_AP1_MODELS) is True
  for v in ("0", "false", "off", " off\n"):
    (tmp_path / "AP1StandstillEngage").write_text(v)
    assert toggles.ap1_standstill_engage(CAR.TESLA_AP1_MODELS) is False, v
  (tmp_path / "AP1StandstillEngage").write_text("1")
  assert toggles.ap1_standstill_engage(CAR.TESLA_AP1_MODELS) is True
  assert toggles.ap1_standstill_engage(CAR.TESLA_MODELS_RAVEN) is False
  for v in ("0", "false", "off"):
    assert toggles.ap1_standstill_engage(CAR.TESLA_AP1_MODELS, v) is False
  for v in ("1", "on", ""):
    assert toggles.ap1_standstill_engage(CAR.TESLA_AP1_MODELS, v) is True


def test_interface_sets_panda_bit_when_on():
  src = (__import__("pathlib").Path(toggles.__file__).parent / "interface.py").read_text()
  assert "ap1_standstill_engage(candidate)" in src
  assert "flags_for_candidate(candidate, fingerprint, standstill=standstill)" in src


def test_safety_flag_bit():
  assert Panda.FLAG_TESLA_AP1_STANDSTILL == 16
  assert flags_for_candidate(CAR.TESLA_AP1_MODELS, {})[0] == AP1
  assert flags_for_candidate(CAR.TESLA_AP1_MODELS, {}, standstill=True)[0] == AP1 | 16
  assert flags_for_candidate(CAR.TESLA_MODELS_RAVEN, {}, standstill=True)[0] & 16 == 0


def test_gate():
  assert ap1_standby_allowed(True, True, False, True, False)
  for args in [(False, True, False, True, False), (True, False, False, True, False), (True, True, True, True, False),
               (True, True, False, False, False), (True, True, False, True, True)]:
    assert not ap1_standby_allowed(*args), args


def test_standby_plan_shape_and_counters():
  p = _plan(standby=True)
  assert p.longitudinal_addrs == (DAS_CONTROL_CHASSIS,)
  assert [c.counter for c in p.longitudinal] == [3, 4]
  c = p.longitudinal[0]
  assert (c.acc_state, c.target_speed, c.min_accel, c.max_accel) == (ACC_ON, 0.0, AP1_STANDBY_ACCEL_MIN, AP1_STANDBY_ACCEL_MAX)
  assert (c.jerk_min, c.jerk_max) == (AP1_STANDBY_JERK_MIN, AP1_STANDBY_JERK_MAX)
  assert AP1_STANDBY_ACCEL_MAX <= 0.0
  assert _plan(standby=False).longitudinal == ()
  assert _plan(standby=True, chassis_das_only=False).longitudinal == ()


def test_engaged_plan_identical_with_or_without_standby():
  for kw in (dict(enabled=True, long_active=True, accel=0.8, v_ego=12.0),
             dict(enabled=True, long_active=True, accel=-1.5, v_ego=12.0),
             dict(enabled=True, long_active=False, gas_pressed=True, v_ego=12.0)):
    a = _plan(das_counters=deque([1, 2]), **kw)
    b = _plan(das_counters=deque([1, 2]), standby=True, **kw)
    assert a == b, kw


def test_packed_standby_frames_vs_panda(lp):
  frames = _frames(_plan(standby=True))
  assert decode_accel(frames[0][2]) == (340, 375)  # -1.4, 0.0 m/s^2
  lp.init(AP1)
  assert not any(lp.tx(f) for f in frames)  # flag off: rejected
  lp.init(AP1 | Panda.FLAG_TESLA_AP1_STANDSTILL)
  assert all(lp.tx(f) for f in frames)
