"""AP1 resume hold after a driver steering override.

Once EPAS hands reach level 2+ while engaged, 0x488 stays type NONE until the
hands level has been 0 for AP1_RESUME_HOLD_S (any level 1+ restarts the
count), then lateral resumes through the measured-angle soft-start. The border
stays grey (steerOverride) during the hold. Not engaged: no hold at all.

CarController is run with stub CANPacker / CANParser (the opendbc .so files
here are AArch64).

Not a product, no warranty, driver remains responsible, comply with local law.
"""

import ast
import importlib
import sys
import types
from collections import deque
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.car.tesla.actuator_plan import (
  ACC_ON,
  AP1_ENGAGE_SOFT_START_FRAMES,
  EAC_ACTIVE,
  STEERING_CONTROL_ANGLE,
  STEERING_CONTROL_NONE,
  build_actuator_plan,
)
from openpilot.selfdrive.car.tesla.hso import (
  AP1_CONTROL_HZ,
  AP1_RESUME_HOLD_FRAMES,
  AP1_RESUME_HOLD_S,
  Ap1DriverYield,
)


def test_hold_constant():
  assert AP1_RESUME_HOLD_S == 0.3
  assert AP1_CONTROL_HZ == 100
  assert AP1_RESUME_HOLD_FRAMES == 30


def _run_yield(y, enabled, level, n):
  out = []
  for _ in range(n):
    out.append(y.update(enabled, level))
  return out


def test_hold_not_elapsed_stays_yielded():
  y = Ap1DriverYield()
  assert _run_yield(y, True, 3, 5) == [True] * 5
  # Driver eases off to level 1: still their input, no countdown.
  assert all(_run_yield(y, True, 1, 300))
  # Level 0 but shorter than the hold: still yielded.
  assert all(_run_yield(y, True, 0, AP1_RESUME_HOLD_FRAMES - 1))
  assert y.resumed is False


def test_re_press_restarts_timer():
  for re_press in (1, 3):
    y = Ap1DriverYield()
    _run_yield(y, True, 3, 3)
    _run_yield(y, True, 0, AP1_RESUME_HOLD_FRAMES - 1)
    assert y.update(True, re_press) is True
    # The full hold is needed again from here.
    assert all(_run_yield(y, True, 0, AP1_RESUME_HOLD_FRAMES - 1))
    assert y.update(True, 0) is False
    assert y.resumed is True


def test_hold_elapsed_resumes_once():
  y = Ap1DriverYield()
  _run_yield(y, True, 3, 3)
  states = _run_yield(y, True, 0, AP1_RESUME_HOLD_FRAMES)
  assert states[:-1] == [True] * (AP1_RESUME_HOLD_FRAMES - 1)
  assert states[-1] is False
  assert y.resumed is True
  assert y.update(True, 0) is False
  assert y.resumed is False


def test_level_1_alone_does_not_start_a_hold():
  y = Ap1DriverYield()
  assert not any(_run_yield(y, True, 1, 200))


def test_disengaged_never_yields_and_disengage_clears_at_once():
  y = Ap1DriverYield()
  assert not any(_run_yield(y, False, 3, 50))
  _run_yield(y, True, 3, 3)
  assert y.update(False, 0) is False
  # Re-engage with hands off: no leftover hold.
  assert y.update(True, 0) is False


def _plan(**kwargs):
  args = dict(
    frame=0, lat_active=True, hands_on_fault=False, openpilot_longitudinal_control=True,
    enabled=True, long_active=True, measured_angle_deg=6.0, requested_angle_deg=30.0,
    last_angle_deg=6.0, v_ego=15.0, accel=0.2, acc_state=ACC_ON, das_counters=deque([3]),
    pcm_cancel=False, chassis_das_only=True, eac_status=EAC_ACTIVE, hands_on_level=0,
  )
  args.update(kwargs)
  return build_actuator_plan(**args)


def test_plan_driver_yield_is_measured_none_and_keeps_long():
  plan = _plan(driver_yield=True)
  assert plan.steer.control_type == STEERING_CONTROL_NONE
  assert plan.steer.angle_deg == 6.0
  assert plan.cancel is False
  assert len(plan.longitudinal) == 1
  # Wins over the latched-code / not-ACTIVE measured ANGLE recovery too.
  plan = _plan(driver_yield=True, eac_status="EAC_AVAILABLE", epas_error="EAC_ERROR_HANDS_ON")
  assert plan.steer.control_type == STEERING_CONTROL_NONE
  # Disengaged: still no 0x488 at all.
  plan = _plan(driver_yield=True, enabled=False, long_active=False, lat_active=False)
  assert plan.steer is None
  # Non-AP1 ignores it.
  plan = _plan(driver_yield=True, chassis_das_only=False)
  assert plan.steer.control_type == STEERING_CONTROL_ANGLE


# --- CarController -----------------------------------------------------------

class _FakePacker:
  def __init__(self, dbc):
    self.dbc = dbc

  def make_can_msg(self, name, bus, values):
    return [name, 0, repr(sorted(values.items())).encode(), bus]


@pytest.fixture
def cc_mod(monkeypatch):
  packer = types.ModuleType("opendbc.can.packer")
  packer.CANPacker = _FakePacker
  parser = types.ModuleType("opendbc.can.parser")
  parser.CANParser = object
  define = types.ModuleType("opendbc.can.can_define")
  define.CANDefine = object
  interfaces = types.ModuleType("openpilot.selfdrive.car.interfaces")
  interfaces.CarStateBase = type("CarStateBase", (), {})
  interfaces.CarControllerBase = type("CarControllerBase", (), {})
  interfaces.CarInterfaceBase = type("CarInterfaceBase", (), {})
  swaglog = types.ModuleType("openpilot.common.swaglog")
  swaglog.cloudlog = SimpleNamespace(exception=lambda *a, **k: None)
  for name, mod in (("opendbc.can.packer", packer), ("opendbc.can.parser", parser),
                    ("opendbc.can.can_define", define), ("openpilot.selfdrive.car.interfaces", interfaces),
                    ("openpilot.common.swaglog", swaglog)):
    monkeypatch.setitem(sys.modules, name, mod)
  names = ("openpilot.selfdrive.car.tesla.carcontroller", "openpilot.selfdrive.car.tesla.interface")
  for name in names:
    sys.modules.pop(name, None)
  yield importlib.import_module("openpilot.selfdrive.car.tesla.carcontroller")
  for name in names:
    sys.modules.pop(name, None)


class _Car:
  def __init__(self, mod, fp=None):
    from openpilot.selfdrive.car.tesla.values import CAR
    self.ctl = mod.CarController("tesla_can", SimpleNamespace(
      carFingerprint=fp or CAR.TESLA_AP1_MODELS, openpilotLongitudinalControl=True), None)
    self.ctl.cluster = None
    self.t = 0

  def step(self, enabled, hands, measured=6.0, requested=30.0, eac_status=EAC_ACTIVE):
    from cereal import car
    CC = car.CarControl.new_message()
    CC.enabled = enabled
    CC.latActive = enabled
    CC.longActive = enabled
    CC.actuators.steeringAngleDeg = requested
    CS = SimpleNamespace(
      out=SimpleNamespace(steeringAngleDeg=measured, vEgo=0.0),
      steer_warning="EAC_ERROR_IDLE", hands_on_level=hands, eac_fault=False, eac_status=eac_status,
      acc_state=ACC_ON, das_control_counters=deque(), msg_stw_actn_req={}, cluster_stock={},
    )
    _, can = self.ctl.update(CC.as_reader(), CS, self.t, None)
    self.t += 10_000_000
    for m in can:
      if m[0] == "DAS_steeringControl":
        v = dict(ast.literal_eval(m[2].decode()))
        return v["DAS_steeringControlType"], -v["DAS_steeringAngleRequest"]
    return None

  def next_steer(self, *args, **kwargs):
    """0x488 goes out every other step. Step until one is sent."""
    return self.step(*args, **kwargs) or self.step(*args, **kwargs)

  def run(self, n, *args, **kwargs):
    return [x for x in (self.step(*args, **kwargs) for _ in range(n)) if x is not None]


def test_controller_hold_then_soft_start_from_measured_then_planner(cc_mod):
  car_ = _Car(cc_mod)
  # Past the engage soft-start with hands off: planner angle.
  car_.run(AP1_ENGAGE_SOFT_START_FRAMES + 2, True, 0)
  assert car_.next_steer(True, 0)[0] == STEERING_CONTROL_ANGLE
  # Override: NONE at the measured wheel.
  assert set(car_.run(10, True, 3, measured=12.0)) == {(STEERING_CONTROL_NONE, 12.0)}
  # Driver still on the wheel at level 1, then off for less than the hold.
  assert {t for t, _ in car_.run(100, True, 1, measured=14.0)} == {STEERING_CONTROL_NONE}
  assert {t for t, _ in car_.run(AP1_RESUME_HOLD_FRAMES - 2, True, 0, measured=15.0)} == {STEERING_CONTROL_NONE}
  assert car_.ctl.ap1_yield.active is True
  # Hold elapses: ANGLE at the measured wheel (soft-start), not the far request.
  sent = car_.run(AP1_ENGAGE_SOFT_START_FRAMES + 2, True, 0, measured=15.0, requested=30.0)
  assert car_.ctl.ap1_yield.active is False
  first = [s for s in sent if s[0] == STEERING_CONTROL_ANGLE]
  assert first and first[0] == (STEERING_CONTROL_ANGLE, 15.0)
  soft = [s for s in sent[:AP1_ENGAGE_SOFT_START_FRAMES // 2 - 1] if s[0] == STEERING_CONTROL_ANGLE]
  assert soft and all(a == 15.0 for _, a in soft)
  # After the soft-start the planner angle is rate limited from the wheel.
  t, a = car_.next_steer(True, 0, measured=15.0, requested=30.0)
  assert t == STEERING_CONTROL_ANGLE
  assert 15.0 < a <= 30.0


def test_controller_re_press_during_hold_restarts(cc_mod):
  car_ = _Car(cc_mod)
  car_.run(AP1_ENGAGE_SOFT_START_FRAMES + 2, True, 0)
  car_.run(4, True, 3)
  car_.run(AP1_RESUME_HOLD_FRAMES - 10, True, 0)
  car_.run(2, True, 1)
  sent = car_.run(AP1_RESUME_HOLD_FRAMES - 2, True, 0)
  assert {t for t, _ in sent} == {STEERING_CONTROL_NONE}
  assert car_.ctl.ap1_yield.active is True


def test_controller_disengaged_unaffected(cc_mod):
  car_ = _Car(cc_mod)
  car_.run(AP1_ENGAGE_SOFT_START_FRAMES + 2, True, 0)
  car_.run(4, True, 3)
  # Disengage mid-hold: no 0x488 at once (stock Mobileye passes).
  assert car_.run(20, False, 0) == []
  assert car_.run(20, False, 3) == []
  assert car_.ctl.ap1_yield.active is False
  # Re-engage hands off: normal engage soft-start, no leftover hold.
  sent = car_.run(4, True, 0, measured=3.0)
  assert sent and all(s == (STEERING_CONTROL_ANGLE, 3.0) for s in sent)


def test_controller_level_1_without_override_keeps_steering(cc_mod):
  car_ = _Car(cc_mod)
  car_.run(AP1_ENGAGE_SOFT_START_FRAMES + 2, True, 0)
  sent = car_.run(40, True, 1)
  assert {t for t, _ in sent} == {STEERING_CONTROL_ANGLE}


def test_controller_non_ap1_has_no_hold(cc_mod):
  from openpilot.selfdrive.car.tesla.values import CAR
  car_ = _Car(cc_mod, CAR.TESLA_AP2_MODELS)
  car_.run(4, True, 2)
  assert car_.ctl.ap1_yield.active is False


# --- interface: grey border during the hold ----------------------------------

class _Events:
  def __init__(self):
    self.names = []

  def add(self, name):
    self.names.append(name)

  def to_msg(self):
    return list(self.names)


def _iface(cc_mod, yielding, hands=0, eac_status=EAC_ACTIVE):
  interface = importlib.import_module("openpilot.selfdrive.car.tesla.interface")
  from openpilot.selfdrive.car.tesla.values import CAR
  ret = SimpleNamespace(steerFaultPermanent=False, events=None)
  fake = SimpleNamespace(
    CP=SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS),
    CS=SimpleNamespace(update=lambda *a: (ret, None), eac_status=eac_status, hands_on_level=hands),
    cp=None, cp_cam=None,
    CC=SimpleNamespace(ap1_yield=SimpleNamespace(active=yielding)),
    create_common_events=lambda r: _Events(),
    _ap1_hso_events=lambda ev: ev,
  )
  fake._ap1_driver_yield_active = lambda: interface.CarInterface._ap1_driver_yield_active(fake)
  fake._ap1_epas_inhibit_alert = lambda ev, c, r: interface.CarInterface._ap1_epas_inhibit_alert(fake, ev, c, r)
  return interface, fake, ret


def test_hold_keeps_steer_override_event(cc_mod):
  from cereal import car
  EventName = car.CarEvent.EventName
  c = SimpleNamespace(enabled=True, latActive=True)
  interface, fake, ret = _iface(cc_mod, yielding=True)
  out, _ = interface.CarInterface._update(fake, c, None)
  assert EventName.steerOverride in out.events
  interface, fake, ret = _iface(cc_mod, yielding=False)
  out, _ = interface.CarInterface._update(fake, c, None)
  assert EventName.steerOverride not in out.events


def test_hold_does_not_count_as_epas_inhibit(cc_mod):
  from cereal import car
  EventName = car.CarEvent.EventName
  c = SimpleNamespace(enabled=True, latActive=True)
  for yielding, expect in ((True, False), (False, True)):
    interface, fake, ret = _iface(cc_mod, yielding=yielding, eac_status="EAC_INHIBITED")
    seen = False
    for _ in range(150):
      out, _ = interface.CarInterface._update(fake, c, None)
      seen |= EventName.steerTempUnavailableSilent in out.events
    assert seen is expect
