"""AP1: neutral DAS_control (0x2b9) while the driver presses the accelerator.

While the panda has seen the pedal pressed (0x108 DI_pedalPos != 0),
safety_tesla.h only accepts DAS_accelMin == DAS_accelMax == inactive_accel
(raw 375, 0.00 m/s^2). Any other accel frame is dropped. carcontroller runs
with the new carState and the previous CarControl, so the first pressed step
still had longActive=True and sent an accel request the panda rejected
(route 00000010 segment 18, twice). AP1 now sends the neutral frame (both
limits 0, set speed = vEgo) for every step the pedal is pressed and returns to
the normal request when it is released.

The 0x2b9 bytes here are packed from opendbc/tesla_can.dbc by a small Python
packer (the prebuilt CANPacker .so is not for this box) and run through
panda/tests/libpanda (the board/safety C built for x86).

Not a driving validation. The driver pedal goes to the DI directly; nothing
here can change it.
"""

import importlib
import re
import sys
import types
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.car.tesla.actuator_plan import (
  ACC_ON,
  DAS_CONTROL_CHASSIS,
  ap1_gas_neutral,
  build_actuator_plan,
)
from openpilot.selfdrive.car.tesla.teslacan import TeslaCAN

ROOT = Path(__file__).resolve().parents[4]
DBC = (ROOT / "opendbc/tesla_can.dbc").read_text()
SAFETY = (ROOT / "panda/board/safety/safety_tesla.h").read_text()

INACTIVE_ACCEL_RAW = 375  # safety_tesla.h TESLA_LONG_LIMITS.inactive_accel (0 m/s^2)


# --- DBC packer -------------------------------------------------------------

def _dbc_messages():
  msgs, cur = {}, None
  for line in DBC.splitlines():
    m = re.match(r"BO_ (\d+) (\w+): (\d+)", line)
    if m:
      cur = {"addr": int(m.group(1)), "size": int(m.group(3)), "sigs": {}}
      msgs[m.group(2)] = cur
      continue
    m = re.match(r"\s*SG_ (\w+) : (\d+)\|(\d+)@1([+-]) \(([^,]+),([^)]+)\)", line)
    if m and cur is not None:
      cur["sigs"][m.group(1)] = (int(m.group(2)), int(m.group(3)), float(m.group(5)), float(m.group(6)))
  return msgs


class DbcPacker:
  """Little-endian (@1) DBC packer, same rounding as opendbc packer.cc."""
  MSGS = _dbc_messages()

  def __init__(self, dbc_name=None):
    self.dbc_name = dbc_name

  def make_can_msg(self, name, bus, values):
    if name != "DAS_control":
      # Only DAS_control (all @1) is packed for real. Other frames are recorded.
      return [name, 0, repr(sorted(values.items())).encode(), bus]
    msg = self.MSGS[name]
    word = 0
    for sig, value in values.items():
      start, size, factor, offset = msg["sigs"][sig]
      raw = int(round((value - offset) / factor)) & ((1 << size) - 1)
      word |= raw << start
    return [msg["addr"], 0, word.to_bytes(msg["size"], "little"), bus]


def decode_accel(dat):
  amax = ((dat[6] & 0x1F) << 4) | (dat[5] >> 4)
  amin = ((dat[5] & 0x0F) << 5) | (dat[4] >> 3)
  return amin, amax


def test_dbc_packer_matches_safety_bit_layout():
  # Same extraction tesla_tx_hook uses. 0.00 m/s^2 is raw 375 for both.
  dat = DbcPacker().make_can_msg("DAS_control", 0, {"DAS_accelMin": 0.0, "DAS_accelMax": 0.0})[2]
  assert decode_accel(dat) == (INACTIVE_ACCEL_RAW, INACTIVE_ACCEL_RAW)
  dat = DbcPacker().make_can_msg("DAS_control", 0, {"DAS_accelMin": -0.88, "DAS_accelMax": 0.28})[2]
  assert decode_accel(dat) == (353, 382)
  assert ".inactive_accel = 375," in SAFETY
  tx = SAFETY.split("static bool tesla_tx_hook")[1].split("static int tesla_fwd_hook")[0]
  assert "longitudinal_accel_checks(raw_accel_max, TESLA_LONG_LIMITS)" in tx
  assert "longitudinal_accel_checks(raw_accel_min, TESLA_LONG_LIMITS)" in tx


# --- libpanda ---------------------------------------------------------------

@pytest.fixture
def lp():
  from panda import ALTERNATIVE_EXPERIENCE, Panda
  from panda.tests.libpanda import libpanda_py
  l = libpanda_py.libpanda
  l.set_timer(1000)
  assert l.set_safety_hooks(Panda.SAFETY_TESLA, Panda.FLAG_TESLA_AP1 | Panda.FLAG_TESLA_LONG_CONTROL) == 0
  # What AP1 runs with (carParams alternativeExperience 1): gas does not disengage.
  l.set_alternative_experience(ALTERNATIVE_EXPERIENCE.DISABLE_DISENGAGE_ON_GAS)
  l.set_controls_allowed(True)
  return SimpleNamespace(l=l, py=libpanda_py)


def _pedal(lp, raw):
  dat = bytearray(8)
  dat[6] = raw  # DI_torque1 0x108 DI_pedalPos, what tesla_rx_hook reads
  assert lp.l.safety_rx_hook(lp.py.make_CANPacket(0x108, 0, bytes(dat)))


def _tx(lp, frame):
  addr, _, dat, bus = frame
  return bool(lp.l.safety_tx_hook(lp.py.make_CANPacket(addr, bus, bytes(dat))))


def _plan(**kwargs):
  args = dict(
    frame=1, lat_active=True, hands_on_fault=False, openpilot_longitudinal_control=True,
    enabled=True, long_active=True, measured_angle_deg=0.0, requested_angle_deg=0.0,
    last_angle_deg=0.0, v_ego=14.0, accel=-0.88, acc_state=ACC_ON, das_counters=deque([5]),
    pcm_cancel=False, chassis_das_only=True,
  )
  args.update(kwargs)
  return build_actuator_plan(**args)


def _frames(plan):
  can = TeslaCAN(DbcPacker(), DbcPacker())
  out = []
  for cmd in plan.longitudinal:
    out.extend(can.create_longitudinal_commands(cmd.acc_state, cmd.target_speed, cmd.min_accel, cmd.max_accel,
                                                cmd.counter, chassis_only=True))
  return out


def test_gas_neutral_gate():
  assert ap1_gas_neutral(True, True, True, True) is True
  for args in ((False, True, True, True), (True, False, True, True), (True, True, False, True), (True, True, True, False)):
    assert ap1_gas_neutral(*args) is False


def test_gas_pressed_plans_neutral_frame_even_with_stale_long_active():
  for long_active in (True, False):
    counters = deque([5, 6])
    plan = _plan(gas_pressed=True, long_active=long_active, accel=0.7, das_counters=counters)
    assert plan.longitudinal_addrs == (DAS_CONTROL_CHASSIS,)
    assert [c.counter for c in plan.longitudinal] == [5, 6]
    assert list(counters) == []
    for c in plan.longitudinal:
      assert (c.min_accel, c.max_accel) == (0.0, 0.0)
      assert c.target_speed == 14.0  # vEgo, no accel-based speed offset
      assert c.acc_state == ACC_ON


def test_gas_pressed_unchanged_when_not_ap1_or_disengaged_or_no_op_long():
  # Disengaged: no DAS_control (stock DAS passes).
  assert _plan(gas_pressed=True, enabled=False, long_active=False).longitudinal == ()
  # No openpilot long: never DAS_control.
  assert _plan(gas_pressed=True, openpilot_longitudinal_control=False).longitudinal == ()
  # Non-AP1 keeps the old behavior (long_active decides, accel passed through).
  plan = _plan(gas_pressed=True, chassis_das_only=False, long_active=True, accel=0.7)
  assert plan.longitudinal[0].max_accel == 0.7
  assert _plan(gas_pressed=True, chassis_das_only=False, long_active=False).longitudinal == ()


def test_panda_rejects_old_frame_accepts_neutral_while_gas_pressed(lp):
  _pedal(lp, 0)
  assert _tx(lp, _frames(_plan(accel=-0.88))[0])
  # Pedal pressed (raw 2 = 0.8 %, as in the log).
  _pedal(lp, 2)
  assert lp.l.get_gas_pressed_prev()
  assert not lp.l.get_longitudinal_allowed()
  # Before: stale longActive=True step sent the planner accel. Panda drops it.
  for accel in (-0.88, 0.28):
    old = _frames(_plan(accel=accel))[0]
    assert decode_accel(old[2]) != (INACTIVE_ACCEL_RAW, INACTIVE_ACCEL_RAW)
    assert not _tx(lp, old)
  # Now: neutral frame, first (stale longActive) step and later steps.
  for long_active in (True, False):
    for frame in _frames(_plan(gas_pressed=True, long_active=long_active, accel=-0.88)):
      assert decode_accel(frame[2]) == (INACTIVE_ACCEL_RAW, INACTIVE_ACCEL_RAW)
      assert frame[2][2] & 0x03 == 0  # DAS_aebEvent
      assert _tx(lp, frame)
  # Engagement and gas state are not changed by any of this.
  assert lp.l.get_controls_allowed()


def test_panda_accepts_normal_request_after_release(lp):
  _pedal(lp, 3)
  assert _tx(lp, _frames(_plan(gas_pressed=True))[0])
  _pedal(lp, 0)
  assert lp.l.get_longitudinal_allowed()
  frame = _frames(_plan(gas_pressed=False, accel=0.7))[0]
  amin, amax = decode_accel(frame[2])
  assert amin == INACTIVE_ACCEL_RAW and amax == round((0.7 + 15) / 0.04)
  assert _tx(lp, frame)
  frame = _frames(_plan(gas_pressed=False, accel=-1.2))[0]
  assert _tx(lp, frame)


def test_neutral_frame_keeps_substitution_and_stock_aeb_rule(lp):
  # Neutral frames are allowed TX, so the stock bus-2 0x2b9 copy stays dropped
  # (same as any other accepted openpilot 0x2b9).
  _pedal(lp, 2)
  assert _tx(lp, _frames(_plan(gas_pressed=True))[0])
  assert lp.l.safety_fwd_hook(2, 0x2b9) == -1
  # Stock AEB active on bus 2: our neutral frame is still refused and stock passes.
  aeb = bytearray(8)
  aeb[2] = 1
  assert lp.l.safety_rx_hook(lp.py.make_CANPacket(0x2b9, 2, bytes(aeb)))
  assert not _tx(lp, _frames(_plan(gas_pressed=True))[0])
  assert lp.l.safety_fwd_hook(2, 0x2b9) == 0


# --- CarController ----------------------------------------------------------

@pytest.fixture
def cc_mod(monkeypatch):
  packer = types.ModuleType("opendbc.can.packer")
  packer.CANPacker = DbcPacker
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
    self.counter = 0

  def step(self, long_active, gas_pressed, accel, v_ego=14.0, n_stock=1):
    from cereal import car
    CC = car.CarControl.new_message()
    CC.enabled = True
    CC.latActive = True
    CC.longActive = long_active
    CC.actuators.accel = accel
    counters = deque()
    for _ in range(n_stock):
      counters.append(self.counter)
      self.counter = (self.counter + 1) % 8
    CS = SimpleNamespace(
      out=SimpleNamespace(steeringAngleDeg=0.0, vEgo=v_ego, gasPressed=gas_pressed),
      steer_warning="EAC_ERROR_IDLE", hands_on_level=0, eac_fault=False, eac_status="EAC_ACTIVE",
      acc_state=ACC_ON, das_control_counters=counters, msg_stw_actn_req={}, cluster_stock={},
    )
    _, can = self.ctl.update(CC.as_reader(), CS, self.t, None)
    self.t += 10_000_000
    return [m for m in can if m[0] == 0x2b9]


def test_controller_press_and_release_against_panda(cc_mod, lp):
  car_ = _Car(cc_mod)
  _pedal(lp, 0)
  # Driving under openpilot long.
  for _ in range(5):
    for f in car_.step(True, False, -0.88):
      assert decode_accel(f[2]) == (round((-0.88 + 15) / 0.04), INACTIVE_ACCEL_RAW)
      assert _tx(lp, f)
  # Press. First step: new carState (gas) with the previous CarControl (longActive True).
  _pedal(lp, 2)
  first = car_.step(True, True, -0.88)
  assert first and all(decode_accel(f[2]) == (INACTIVE_ACCEL_RAW, INACTIVE_ACCEL_RAW) for f in first)
  assert all(_tx(lp, f) for f in first)
  # Held: controlsd has dropped longActive; accel is 0. Neutral every step,
  # one per stock frame, so no counter backlog builds up.
  sent = 0
  for _ in range(100):
    frames = car_.step(False, True, 0.0)
    sent += len(frames)
    for f in frames:
      assert decode_accel(f[2]) == (INACTIVE_ACCEL_RAW, INACTIVE_ACCEL_RAW)
      assert _tx(lp, f)
  assert sent == 100
  # Release. First step: carState gas False, CarControl still longActive False.
  _pedal(lp, 0)
  assert car_.step(False, False, 0.0) == []
  # longActive back: normal request on the next step, one frame per stock frame.
  frames = car_.step(True, False, 0.7)
  assert len(frames) == 1
  assert decode_accel(frames[0][2]) == (INACTIVE_ACCEL_RAW, round((0.7 + 15) / 0.04))
  assert _tx(lp, frames[0])
  assert lp.l.get_controls_allowed()


def test_controller_neutral_set_speed_is_v_ego(cc_mod):
  car_ = _Car(cc_mod)
  f = car_.step(True, True, 1.5, v_ego=20.0)[0]
  set_speed_raw = int.from_bytes(f[2], "little") & 0xFFF
  assert set_speed_raw == round(20.0 * 3.6 / 0.1)


def test_controller_passes_gas_only_for_ap1():
  from openpilot.selfdrive.car.tesla.values import CAR
  ctl_src = (ROOT / "selfdrive/car/tesla/carcontroller.py").read_text()
  assert 'gas_pressed=bool(getattr(CS.out, "gasPressed", False)) if ap1 else False' in ctl_src
  assert CAR.TESLA_AP1_MODELS
