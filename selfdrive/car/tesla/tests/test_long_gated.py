"""DAS_control is planned only while long control is active.

Not a product, no warranty, driver remains responsible, comply with local law.
dashcamOnly remains true. This is not a driving validation and does not enable
openpilot longitudinal control.
"""

from collections import deque
from pathlib import Path

from openpilot.common.conversions import Conversions as CV
from openpilot.selfdrive.car.tesla.actuator_plan import (
  DAS_CONTROL_CHASSIS,
  DAS_CONTROL_POWERTRAIN,
  build_actuator_plan,
  longitudinal_command_allowed,
)
from openpilot.selfdrive.car.tesla.teslacan import TeslaCAN
from openpilot.selfdrive.car.tesla.values import CANBUS

ROOT = Path(__file__).resolve().parents[4]
# Chassis and powertrain DAS_control addresses. safety_tesla.h and the DBCs.


class _Packer:
  def __init__(self):
    self.calls = []

  def make_can_msg(self, name, bus, values):
    self.calls.append((name, bus, dict(values)))
    return (0, 0, bytes(8), bus)


def _plan(counters=None, **kwargs):
  if counters is None:
    counters = deque([4])
  args = dict(
    frame=0,
    lat_active=False,
    hands_on_fault=False,
    openpilot_longitudinal_control=False,
    enabled=False,
    long_active=False,
    measured_angle_deg=12.5,
    requested_angle_deg=40.0,
    last_angle_deg=1.0,
    v_ego=10.0,
    accel=1.0,
    acc_state=2,
    das_counters=counters,
    pcm_cancel=False,
  )
  args.update(kwargs)
  return build_actuator_plan(**args), counters


def _assert_inactive_lateral(plan):
  # Interceptor: disengaged lateral plans no 0x488 at all.
  assert plan.steer is None
  assert plan.apply_angle_last == 12.5


def test_long_param_false_plans_no_das_control():
  counters = deque([4])
  plan, _ = _plan(counters, openpilot_longitudinal_control=False, enabled=True, long_active=True)
  assert longitudinal_command_allowed(False, True, True) is False
  assert plan.longitudinal == ()
  assert list(counters) == [4]
  _assert_inactive_lateral(plan)


def test_long_param_true_but_not_long_active_plans_no_das_control():
  # enabled false is not long-active even if longActive is true.
  cases = (
    dict(enabled=True, long_active=False),
    dict(enabled=False, long_active=True),
    dict(enabled=False, long_active=False),
  )
  for gates in cases:
    counters = deque([4])
    plan, _ = _plan(counters, openpilot_longitudinal_control=True, **gates)
    assert longitudinal_command_allowed(True, gates["enabled"], gates["long_active"]) is False
    assert plan.longitudinal == ()
    assert list(counters) == [4]
    _assert_inactive_lateral(plan)


def test_long_active_plans_previous_das_control_contents():
  """Active contents captured before the gate, for v_ego=10 and accel=1.

  target_speed = max(10 + 1 * ACCEL_TO_SPEED_MULTIPLIER, 0) = 13
  min_accel = 0, max_accel = 1, acc_state and counter passed through.
  DAS_setSpeed is that speed in km/h. No fixed set-speed was introduced.
  """
  counters = deque([4])
  plan, _ = _plan(
    counters,
    openpilot_longitudinal_control=True,
    enabled=True,
    long_active=True,
    v_ego=10.0,
    accel=1.0,
    acc_state=2,
  )
  assert longitudinal_command_allowed(True, True, True) is True
  assert len(plan.longitudinal) == 1
  assert list(counters) == []
  cmd = plan.longitudinal[0]
  assert cmd.acc_state == 2
  assert cmd.counter == 4
  assert cmd.target_speed == 13.0
  assert cmd.min_accel == 0
  assert cmd.max_accel == 1.0
  _assert_inactive_lateral(plan)

  chassis = _Packer()
  powertrain = _Packer()
  messages = TeslaCAN(chassis, powertrain).create_longitudinal_commands(
    cmd.acc_state, cmd.target_speed, cmd.min_accel, cmd.max_accel, cmd.counter)
  assert len(messages) == 2
  assert [name for name, _, _ in chassis.calls] == ["DAS_control", "DAS_control"]
  assert [name for name, _, _ in powertrain.calls] == ["DAS_control", "DAS_control"]
  assert chassis.calls[-1][1] == CANBUS.chassis
  assert powertrain.calls[-1][1] == CANBUS.powertrain

  can_dbc = (ROOT / "opendbc/tesla_can.dbc").read_text()
  pt_dbc = (ROOT / "opendbc/tesla_powertrain.dbc").read_text()
  assert f"BO_ {DAS_CONTROL_CHASSIS} DAS_control:" in can_dbc
  assert f"BO_ {DAS_CONTROL_POWERTRAIN} DAS_control:" in pt_dbc
  assert DAS_CONTROL_CHASSIS == 0x2B9
  assert DAS_CONTROL_POWERTRAIN == 0x2BF
  sender = (ROOT / "selfdrive/car/tesla/teslacan.py").read_text()
  assert "checksum(0x2b9" in sender

  for packed in (chassis.calls[-1][2], powertrain.calls[-1][2]):
    assert packed["DAS_setSpeed"] == 13.0 * CV.MS_TO_KPH
    assert packed["DAS_accelMin"] == 0
    assert packed["DAS_accelMax"] == 1.0
    assert packed["DAS_accState"] == 2


def test_ap1_plans_0x2b9_when_long_active_and_not_0x2bf():
  counters = deque([4])
  plan, _ = _plan(
    counters,
    openpilot_longitudinal_control=True,
    enabled=True,
    long_active=True,
    chassis_das_only=True,
    v_ego=10.0,
    accel=1.0,
  )
  assert len(plan.longitudinal) == 1
  assert plan.longitudinal_addrs == (DAS_CONTROL_CHASSIS,)
  assert DAS_CONTROL_POWERTRAIN not in plan.longitudinal_addrs
  assert plan.longitudinal[0].target_speed == 13.0
  assert list(counters) == []

  chassis = _Packer()
  powertrain = _Packer()
  messages = TeslaCAN(chassis, powertrain).create_longitudinal_commands(
    plan.longitudinal[0].acc_state,
    plan.longitudinal[0].target_speed,
    plan.longitudinal[0].min_accel,
    plan.longitudinal[0].max_accel,
    plan.longitudinal[0].counter,
    chassis_only=True,
  )
  assert len(messages) == 1
  assert [name for name, _, _ in chassis.calls] == ["DAS_control", "DAS_control"]
  assert chassis.calls[-1][1] == CANBUS.chassis
  assert powertrain.calls == []
  assert chassis.calls[-1][2]["DAS_setSpeed"] == 13.0 * CV.MS_TO_KPH
  assert chassis.calls[-1][2]["DAS_setSpeed"] not in (0, 145)

  controller = (ROOT / "selfdrive/car/tesla/carcontroller.py").read_text()
  assert "chassis_das_only" in controller
  assert "CAR.TESLA_AP1_MODELS" in controller
  assert "chassis_only=chassis_only" in controller
  header = (ROOT / "panda/board/safety/safety_tesla.h").read_text()
  assert "addr == (tesla_powertrain ? 0x2bf : 0x2b9)" in header


def test_ap1_inactive_plans_no_das_control():
  for gates in (
    dict(openpilot_longitudinal_control=True, enabled=True, long_active=False),
    dict(openpilot_longitudinal_control=True, enabled=False, long_active=True),
    dict(openpilot_longitudinal_control=False, enabled=True, long_active=True),
  ):
    counters = deque([4])
    plan, _ = _plan(counters, chassis_das_only=True, **gates)
    assert plan.longitudinal == ()
    assert plan.longitudinal_addrs == ()
    assert DAS_CONTROL_POWERTRAIN not in plan.longitudinal_addrs
    assert DAS_CONTROL_CHASSIS not in plan.longitudinal_addrs
    assert list(counters) == [4]


def test_non_ap1_active_still_plans_chassis_and_powertrain():
  plan, _ = _plan(openpilot_longitudinal_control=True, enabled=True, long_active=True)
  assert plan.longitudinal_addrs == (DAS_CONTROL_CHASSIS, DAS_CONTROL_POWERTRAIN)
  assert 0x2B9 in plan.longitudinal_addrs
  assert 0x2BF in plan.longitudinal_addrs
