"""Disengaged Tesla actuator plan (AP1 interceptor).

CarController.update is not constructed here. opendbc/can/packer_pyx.so is
AArch64, and importing CarController also pulls parser_pyx and msgq. update
calls build_actuator_plan and then TeslaCAN. Those two are what this file runs.

While disengaged, the plan must not emit DAS_steeringControl (0x488) or
DAS_control (0x2b9). A type-NONE 0x488 sent the whole time disengaged replaced
stock Mobileye commands on AP1.

Not a product, no warranty, driver remains responsible, comply with local law.
This does not enable lateral or longitudinal control.
"""

import re
from collections import deque
from pathlib import Path

from openpilot.selfdrive.car.tesla.actuator_plan import STEERING_CONTROL_ANGLE, build_actuator_plan
from openpilot.selfdrive.car.tesla.platform import long_control_allowed
from openpilot.selfdrive.car.tesla.values import TeslaPlatform

TESLA = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[4]


def _plan(counters=None, **kwargs):
  if counters is None:
    counters = deque([1, 2, 3])
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
    v_ego=15.0,
    accel=2.0,
    acc_state=2,
    das_counters=counters,
    pcm_cancel=False,
  )
  args.update(kwargs)
  return build_actuator_plan(**args), counters


def test_disengaged_plans_no_steering_and_no_long_command():
  counters = deque([1, 2, 3])
  plan, _ = _plan(counters)

  # Interceptor: no 0x488 while disengaged so stock Mobileye keeps the chassis.
  assert plan.steer is None
  assert plan.apply_angle_last == 12.5  # tracks measured for next engage
  assert plan.longitudinal == ()
  assert plan.longitudinal_addrs == ()
  assert plan.cancel is False
  assert list(counters) == [1, 2, 3]


def test_odd_frame_sends_no_steering_frame():
  plan, counters = _plan(frame=1)
  assert plan.steer is None
  assert plan.longitudinal == ()
  assert plan.cancel is False
  assert plan.apply_angle_last == 1.0
  assert list(counters) == [1, 2, 3]


def test_hands_on_fault_plans_no_steering_and_cancels():
  plan, _ = _plan(lat_active=True, hands_on_fault=True, requested_angle_deg=30.0, measured_angle_deg=5.0)
  assert plan.steer is None
  assert plan.apply_angle_last == 5.0
  # frame 0 and the fault forces cancel, which is the existing stalk-cancel path.
  assert plan.cancel is True
  assert plan.longitudinal == ()


def test_active_lateral_is_unchanged_when_engaged():
  # Guards the extraction. Not an enablement of driving.
  plan, _ = _plan(lat_active=True, measured_angle_deg=0.0, requested_angle_deg=10.0, last_angle_deg=0.0, v_ego=0.0, accel=0.0)
  assert plan.steer is not None
  assert plan.steer.enabled is True
  assert plan.steer.control_type == STEERING_CONTROL_ANGLE
  assert plan.steer.angle_deg == 10.0
  assert plan.longitudinal == ()


def test_no_friction_brake_or_ibooster_apply():
  assert long_control_allowed(TeslaPlatform.preap) is False
  packed = set()
  for name in ("carcontroller.py", "teslacan.py", "actuator_plan.py"):
    text = (TESLA / name).read_text()
    assert "iBooster" not in text
    assert "friction" not in text
    packed.update(re.findall(r'make_can_msg\("([^"]+)"', text))
  assert packed == {"DAS_steeringControl", "STW_ACTN_RQ", "DAS_control"}
  header = (ROOT / "panda/board/safety/safety_tesla.h").read_text()
  assert "BrakeMessage" in header
  assert "{0x20a, 0, 8" in header
  assert "{0x1f8, 0, 8" in header
  # Those ids are RX checks, not TX.
  tx = header.split("RxCheck tesla_rx_checks")[0]
  assert "0x20a" not in tx
  assert "0x1f8" not in tx
