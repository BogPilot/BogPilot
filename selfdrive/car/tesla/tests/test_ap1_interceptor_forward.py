"""AP1 interceptor: stock DAS frames flow unless OP is substituting.

Python-level checks for the actuator plan plus a precise document of the C
tesla_fwd_hook change. panda safety unit tests are not runnable on this x86
box (no panda/tests/safety tree in this checkout).

Confirmed by 2026-10-04 rlogs: when Tesla safety param 10 started, stock bus-2
0x488 and 0x2b9 stopped appearing on bus 0 within ~0.1s because fwd_hook
blocked them unconditionally and sendcan emitted type-NONE 0x488 the whole
time disengaged. Stock AEB/Autopilot then faulted.

Not a product, no warranty, driver remains responsible, comply with local law.
This does not make the car safe to drive.
"""

from collections import deque
from pathlib import Path

from openpilot.selfdrive.car.tesla.actuator_plan import (
  DAS_CONTROL_CHASSIS,
  DAS_CONTROL_POWERTRAIN,
  STEERING_CONTROL_ANGLE,
  build_actuator_plan,
)

ROOT = Path(__file__).resolve().parents[4]
SAFETY = (ROOT / "panda/board/safety/safety_tesla.h").read_text()


def _plan(**kwargs):
  counters = kwargs.pop("das_counters", deque([1]))
  args = dict(
    frame=0,
    lat_active=False,
    hands_on_fault=False,
    openpilot_longitudinal_control=True,
    enabled=False,
    long_active=False,
    measured_angle_deg=3.0,
    requested_angle_deg=20.0,
    last_angle_deg=1.0,
    v_ego=12.0,
    accel=0.5,
    acc_state=2,
    das_counters=counters,
    pcm_cancel=False,
    chassis_das_only=True,
  )
  args.update(kwargs)
  return build_actuator_plan(**args)


def test_disengaged_ap1_plans_no_0x488_and_no_0x2b9():
  plan = _plan()
  assert plan.steer is None
  assert plan.longitudinal == ()
  assert plan.longitudinal_addrs == ()
  assert DAS_CONTROL_CHASSIS not in plan.longitudinal_addrs
  assert DAS_CONTROL_POWERTRAIN not in plan.longitudinal_addrs


def test_engaged_lat_only_plans_0x488_not_0x2b9():
  plan = _plan(lat_active=True, enabled=True, long_active=False, measured_angle_deg=0.0,
               requested_angle_deg=5.0, last_angle_deg=0.0, v_ego=0.0)
  assert plan.steer is not None
  assert plan.steer.enabled is True
  assert plan.steer.control_type == STEERING_CONTROL_ANGLE
  assert plan.longitudinal == ()
  assert plan.longitudinal_addrs == ()


def test_engaged_long_plans_0x2b9_not_0x2bf():
  plan = _plan(lat_active=True, enabled=True, long_active=True, openpilot_longitudinal_control=True,
               measured_angle_deg=0.0, requested_angle_deg=5.0, last_angle_deg=0.0, v_ego=0.0, accel=0.0)
  assert plan.steer is not None
  assert len(plan.longitudinal) == 1
  assert plan.longitudinal_addrs == (DAS_CONTROL_CHASSIS,)
  assert DAS_CONTROL_POWERTRAIN not in plan.longitudinal_addrs


def test_fwd_hook_not_unconditional_block():
  """Document the C interceptor change. Not a compiled panda safety run."""
  # Old bug: block_msg = true for every 0x488 while safety mode is on.
  assert "block_msg = true;" not in SAFETY.split("static int tesla_fwd_hook")[1].split("static safety_config")[0]

  fwd = SAFETY.split("static int tesla_fwd_hook")[1].split("static safety_config")[0]
  assert "tesla_op_recently_sent" in fwd
  assert "TESLA_STEER_SUBSTITUTE_TIMEOUT_US" in SAFETY
  assert "TESLA_LONG_SUBSTITUTE_TIMEOUT_US" in SAFETY
  assert "tesla_steer_tx_seen" in SAFETY
  assert "tesla_long_tx_seen" in SAFETY

  # Stock copies are dropped only after a recent allowed OP TX of that address.
  assert "tesla_op_recently_sent(tesla_last_steer_tx_ts, tesla_steer_tx_seen" in fwd
  assert "tesla_op_recently_sent(tesla_last_long_tx_ts, tesla_long_tx_seen" in fwd

  # LONG flag alone must not imply an unconditional 0x2b9 drop.
  assert "tesla_longitudinal && (addr == das_control_addr) && !tesla_stock_aeb" in fwd
  long_block_region = fwd.split("tesla_longitudinal && (addr == das_control_addr)")[1].split("if(!block_msg)")[0]
  assert "tesla_op_recently_sent" in long_block_region
  assert "block_msg = true;" not in long_block_region

  # No EPAS / GTW rewrite, and no other DAS frame is dropped. The AP1 cluster
  # ids (0x399, 0x389, 0x239) are dropped only through the same recent-TX
  # rule, per address, and only with TESLA_FLAG_AP1 (panda/tests/safety/
  # test_tesla_ap1_cluster.py runs that against libpanda).
  for needle in ("0x309", "0x3a9", "0x3e9", "0x329", "0x369", "0x349",
                 "EPAS_eacStatus", "GTW_autopilot", "GTW_carConfig"):
    assert needle not in fwd
  cluster = fwd.split("tesla_ap1 && !tesla_powertrain")[1].split("if(!block_msg)")[0]
  assert "tesla_ap1_cluster_index(addr)" in cluster
  assert "tesla_op_recently_sent(tesla_ap1_cluster_tx_ts[idx], tesla_ap1_cluster_tx_seen[idx]" in cluster
  assert "block_msg = true;" not in cluster
  assert "TESLA_AP1_CLUSTER_ADDRS[TESLA_AP1_CLUSTER_LEN] = {0x399, 0x389, 0x239}" in SAFETY

  # Safety flags are not zeroed; AP1 still uses AP1|LONG.
  assert "TESLA_FLAG_AP1 = 8" in SAFETY
  assert "TESLA_FLAG_LONGITUDINAL_CONTROL = 2" in SAFETY
  assert "TESLA_AP1_STEERING_LIMITS" in SAFETY
