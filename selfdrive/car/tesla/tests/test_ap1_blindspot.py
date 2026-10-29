"""AP1 blind-spot flags from stock 0x399 (blindspot.py) and their wiring.

DAS_blindSpotRearLeft/Right: 0 none, 1/2 warning, 3 SNA. 1 and 2 set the
side, 3 and bad checksums are ignored, and each side holds BSM_HOLD_S after
the last warning frame because 0x399 only comes at about 2.4 Hz.
"""
import importlib
import sys
import types
from types import SimpleNamespace

import pytest

from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.car.tesla import blindspot as bsm
from openpilot.selfdrive.car.tesla import cluster as c
from openpilot.selfdrive.car.tesla.tests.fixtures.ap1_cluster_frames import AUTOPILOT_STATUS_HEX
from openpilot.selfdrive.car.tesla.tests.test_ap1_speed_limit import (  # noqa: F401 (fixture)
  _ap1_cans, _prep_define, carstate_mod,
)

STEPS_PER_S = round(1.0 / DT_CTRL)
BASE = c.unpack(c.AUTOPILOT_STATUS, bytes.fromhex(AUTOPILOT_STATUS_HEX[0]))


def frame(left=0, right=0, counter=0, checksum_delta=0):
  """Decoded 0x399 frame (stock base frame) with a valid checksum unless told otherwise."""
  v = {k: x for k, x in BASE.items() if k not in (c.COUNTER_SIGNALS[c.AUTOPILOT_STATUS], c.CHECKSUM_SIGNALS[c.AUTOPILOT_STATUS])}
  v[bsm.BSM_LEFT] = left
  v[bsm.BSM_RIGHT] = right
  word = c.pack(c.AUTOPILOT_STATUS, c.SIGNALS[c.AUTOPILOT_STATUS], v)
  dat = bytearray(c.finish(c.AUTOPILOT_STATUS, word, counter))
  dat[7] = (dat[7] + checksum_delta) & 0xFF
  return c.unpack(c.AUTOPILOT_STATUS, bytes(dat))


def run(b, frames_by_step, steps):
  """frames_by_step: {step: [frames]}. Returns [(left, right)] per step."""
  return [b.update(frames_by_step.get(i, []), DT_CTRL) for i in range(steps)]


def test_hold_is_a_named_one_second_constant():
  assert bsm.BSM_HOLD_S == pytest.approx(1.0)
  assert bsm.BSM_DETECTED == (1, 2)


def test_stock_frames_pass_checksum_and_show_no_blindspot():
  for h in AUTOPILOT_STATUS_HEX:
    f = c.unpack(c.AUTOPILOT_STATUS, bytes.fromhex(h))
    assert bsm.checksum_ok(f), h
  b = bsm.Ap1Blindspot()
  frames = [c.unpack(c.AUTOPILOT_STATUS, bytes.fromhex(h)) for h in AUTOPILOT_STATUS_HEX]
  assert b.update(frames, DT_CTRL) == (False, False)


@pytest.mark.parametrize("value", [1, 2])
def test_flag_decode_left_and_right(value):
  b = bsm.Ap1Blindspot()
  assert b.update([frame(left=value)], DT_CTRL) == (True, False)
  b = bsm.Ap1Blindspot()
  assert b.update([frame(right=value)], DT_CTRL) == (False, True)
  b = bsm.Ap1Blindspot()
  assert b.update([frame(left=value, right=value)], DT_CTRL) == (True, True)


def test_raw_bits_match_dbc():
  # DAS_blindSpotRearLeft is bits 4-5, Right bits 6-7 of byte 0.
  dat = c.finish(c.AUTOPILOT_STATUS, c.pack(c.AUTOPILOT_STATUS, c.SIGNALS[c.AUTOPILOT_STATUS], {bsm.BSM_LEFT: 2, bsm.BSM_RIGHT: 1}), 0)
  assert dat[0] == (2 << 4) | (1 << 6)


def test_value_3_sna_is_ignored():
  b = bsm.Ap1Blindspot()
  assert run(b, {0: [frame(left=3, right=3)]}, 5) == [(False, False)] * 5
  # SNA after a warning does not extend or cut the hold.
  b = bsm.Ap1Blindspot()
  out = run(b, {0: [frame(left=1)], 50: [frame(left=3)]}, STEPS_PER_S + 5)
  assert [lt for lt, _ in out].count(True) == STEPS_PER_S


def test_single_frame_holds_exactly_hold_s_then_clears():
  b = bsm.Ap1Blindspot()
  out = run(b, {0: [frame(right=1)], 10: [frame()]}, 3 * STEPS_PER_S)
  right = [r for _, r in out]
  assert right[:STEPS_PER_S] == [True] * STEPS_PER_S
  assert not any(right[STEPS_PER_S:])
  assert not any(lt for lt, _ in out)


def test_frames_at_stock_rate_do_not_flap_and_hold_after_last():
  # 0x399 at ~2.4 Hz: a warning frame every 42 steps for 3 s, then NO_WARNING.
  period = 42
  steps = 6 * STEPS_PER_S
  fr = {i: [frame(left=2 if i < 3 * STEPS_PER_S else 0, counter=i // period)] for i in range(0, steps, period)}
  b = bsm.Ap1Blindspot()
  left = [lt for lt, _ in run(b, fr, steps)]
  last_warn = max(i for i in fr if i < 3 * STEPS_PER_S)
  on = [i for i, x in enumerate(left) if x]
  assert on == list(range(0, last_warn + STEPS_PER_S))  # one solid run, no gaps
  assert left.count(True) == last_warn + STEPS_PER_S


def test_invalid_checksum_is_ignored():
  b = bsm.Ap1Blindspot()
  assert run(b, {0: [frame(left=1, right=2, checksum_delta=1)]}, 3) == [(False, False)] * 3
  assert b.bad_checksum == 1
  # A bad frame does not refresh an existing hold.
  b = bsm.Ap1Blindspot()
  out = run(b, {0: [frame(left=1)], 60: [frame(left=1, checksum_delta=7)]}, 2 * STEPS_PER_S)
  assert [lt for lt, _ in out].count(True) == STEPS_PER_S


def test_missing_checksum_signal_is_ignored():
  f = frame(left=1)
  del f[c.CHECKSUM_SIGNALS[c.AUTOPILOT_STATUS]]
  assert bsm.Ap1Blindspot().update([f], DT_CTRL) == (False, False)


def test_frames_from_vl_all_keeps_order():
  f1, f2 = frame(left=1, counter=1), frame(right=2, counter=2)
  sigs = {k: [f1[k], f2[k]] for k in f1}
  cp_cam = SimpleNamespace(vl_all={"AutopilotStatus": sigs})
  assert bsm.frames_from_vl_all(cp_cam) == [f1, f2]
  assert bsm.frames_from_vl_all(SimpleNamespace(vl_all={})) == []


def _feed(cp_cam, frames):
  for f in frames:
    for k, v in f.items():
      cp_cam.vl_all["AutopilotStatus"][k].append(v)


def test_carstate_ap1_sets_blindspot_and_holds(carstate_mod):  # noqa: F811
  from openpilot.selfdrive.car.tesla.values import CAR
  CS = carstate_mod.CarState(SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS), None)
  _prep_define(CS)
  cp, cp_cam = _ap1_cans(45.0)
  _feed(cp_cam, [frame(left=1)])
  ret, _ = CS.update(cp, cp_cam, None)
  assert (ret.leftBlindspot, ret.rightBlindspot) == (True, False)
  for i in range(1, STEPS_PER_S + 2):
    cp, cp_cam = _ap1_cans(45.0)
    ret, _ = CS.update(cp, cp_cam, None)
    assert ret.leftBlindspot == (i < STEPS_PER_S)
  cp, cp_cam = _ap1_cans(45.0)
  _feed(cp_cam, [frame(right=3), frame(right=2)])
  ret, _ = CS.update(cp, cp_cam, None)
  assert (ret.leftBlindspot, ret.rightBlindspot) == (False, True)


def test_carstate_non_ap1_never_sets_blindspot(carstate_mod):  # noqa: F811
  from openpilot.selfdrive.car.tesla.values import CAR
  for fp in (CAR.TESLA_AP2_MODELS, CAR.TESLA_MODELS_RAVEN):
    CS = carstate_mod.CarState(SimpleNamespace(carFingerprint=fp), None)
    _prep_define(CS)
    cp, cp_cam = _ap1_cans(45.0)
    cp.vl["DriverSeat"]["buckleStatus"] = 1
    _feed(cp_cam, [frame(left=2, right=2)])
    ret, _ = CS.update(cp, cp_cam, None)
    assert (ret.leftBlindspot, ret.rightBlindspot) == (False, False)


def test_autopilot_status_stays_frequency_zero_on_cam_parser(carstate_mod):  # noqa: F811
  from openpilot.selfdrive.car.tesla.values import CAR
  ap1 = carstate_mod.CarState.get_cam_can_parser(SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS), None)
  assert dict(ap1.messages)["AutopilotStatus"] == 0


@pytest.fixture
def interface_mod(monkeypatch):
  stub = types.ModuleType("openpilot.selfdrive.car.interfaces")
  stub.CarInterfaceBase = object
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.car.interfaces", stub)
  sys.modules.pop("openpilot.selfdrive.car.tesla.interface", None)
  yield importlib.import_module("openpilot.selfdrive.car.tesla.interface")
  sys.modules.pop("openpilot.selfdrive.car.tesla.interface", None)


def test_enable_bsm_ap1_only(interface_mod):
  from cereal import car
  from openpilot.selfdrive.car.tesla.values import CAR
  toggles = SimpleNamespace(disable_openpilot_long=False)
  got = {}
  for cand in CAR:
    for fp in ({0: {}, 1: {}, 2: {}}, {0: {}, 1: {}, 2: {}, 4: {}, 5: {}, 6: {0x2bf: 8}}):
      r = interface_mod.CarInterface._get_params(car.CarParams.new_message(), cand, fp, [], False, False, toggles)
      got.setdefault(cand, set()).add(r.enableBsm)
  assert got[CAR.TESLA_AP1_MODELS] == {True}
  assert all(v == {False} for k, v in got.items() if k != CAR.TESLA_AP1_MODELS)
