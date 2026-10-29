"""AP1 cluster frames (cluster.py) and their wiring.

Real stock payloads come from fixtures/ap1_cluster_frames.py. CarController is
run with a stub CANPacker / CANParser (the opendbc .so files here are AArch64),
to check that the cluster frames only add to can_sends and do not change any
steering or longitudinal frame.

Not a product, no warranty, driver remains responsible, comply with local law.
This does not make the car safe to drive.
"""

import importlib
import sys
import types
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.car.tesla import cluster as c
from openpilot.selfdrive.car.tesla.tests.fixtures.ap1_cluster_frames import (
  AUTOPILOT_STATUS_HEX,
  DAS_LANES_HEX,
  DAS_STATUS2_HEX,
  DAS_BODY_CONTROLS_UNCOVERED_BITS_SEEN,
)

ROOT = Path(__file__).resolve().parents[4]
STOCK = {
  c.AUTOPILOT_STATUS: [bytes.fromhex(h) for h in AUTOPILOT_STATUS_HEX],
  c.DAS_STATUS2: [bytes.fromhex(h) for h in DAS_STATUS2_HEX],
  c.DAS_LANES: [bytes.fromhex(h) for h in DAS_LANES_HEX],
}
NS = 1_000_000_000


def hud(**kw):
  args = dict(enabled=True, fcw=False, steer_required=False, audible=False, human_steering=False,
              left_lane_depart=False, right_lane_depart=False, left_blinker=False, right_blinker=False,
              curvature=0.0, ic_integration=True, model_path=None, cruise_set_mph=None)
  args.update(kw)
  return c.HudInputs(**args)


def stock(addr, i=0):
  return c.unpack(addr, STOCK[addr][i])


def all_stock(i=0):
  return {a: stock(a, i) for a in c.CLUSTER_ADDRS}


# --- DBC, checksum, rebuild -------------------------------------------------

def test_signals_come_from_this_trees_dbc():
  assert c.MSG_NAMES == {0x399: "AutopilotStatus", 0x389: "DAS_status2", 0x239: "DAS_lanes"}
  assert "autopilotStatus" in c.SIGNALS[0x399]
  def geom(addr, name):
    sig = c.SIGNALS[addr][name]
    return (sig.start, sig.size)
  assert geom(0x399, "DAS_autopilotHandsOnState") == (42, 4)
  assert geom(0x399, "DAS_autoLaneChangeState") == (46, 5)
  assert geom(0x389, "DAS_activationFailureStatus") == (14, 2)
  c2 = c.SIGNALS[0x239]["DAS_virtualLaneC2"]
  assert (c2.start, c2.size, c2.factor, c2.offset) == (32, 8, 2e-05, -0.0025)
  acc = c.SIGNALS[0x389]["DAS_accSpeedLimit"]
  assert (acc.start, acc.size, acc.factor, acc.offset) == (0, 10, 0.4, 0.0)
  for addr, name in c.COUNTER_SIGNALS.items():
    assert geom(addr, name) == (60 if addr == c.DAS_LANES else 52, 4)
  for addr, name in c.CHECKSUM_SIGNALS.items():
    assert geom(addr, name) == (56, 8)
  assert c.DAS_LANES not in c.CHECKSUM_SIGNALS


def test_checksum_matches_real_stock_frames():
  for addr in (c.AUTOPILOT_STATUS, c.DAS_STATUS2):
    for dat in STOCK[addr]:
      assert c.tesla_checksum(addr, dat) == dat[7], (hex(addr), dat.hex())


def test_rebuild_from_decoded_values_is_lossless_on_real_frames():
  for addr, frames in STOCK.items():
    for dat in frames:
      v = c.unpack(addr, dat)
      word = c.pack(addr, c.SIGNALS[addr], v)
      assert word.to_bytes(8, "little") == dat
      assert c.finish(addr, word, int(v[c.COUNTER_SIGNALS[addr]])) == dat


def test_body_controls_not_rebuildable_from_dbc():
  sigs = c.load_signals(addrs=(0x3e9,))[0x3e9]
  covered = 0
  for s in sigs.values():
    covered |= ((1 << s.size) - 1) << s.start
  assert DAS_BODY_CONTROLS_UNCOVERED_BITS_SEEN & ~covered == DAS_BODY_CONTROLS_UNCOVERED_BITS_SEEN
  assert 0x3e9 not in c.CLUSTER_ADDRS


def test_unknown_signal_name_is_an_error():
  with pytest.raises(KeyError):
    c.pack(c.DAS_LANES, c.SIGNALS[c.DAS_LANES], {"DAS_invented": 1})


# --- engaged ----------------------------------------------------------------

def _send(ctrl, h, new, t):
  return dict(ctrl.update(h, new, t))


def test_engaged_frames_counter_checksum_and_stock_fields():
  ctrl = c.ClusterController()
  out = _send(ctrl, hud(), all_stock(), 10 * NS)
  assert set(out) == set(c.CLUSTER_ADDRS)
  for addr, dat in out.items():
    s = stock(addr)
    v = c.unpack(addr, dat)
    ctr = c.COUNTER_SIGNALS[addr]
    assert int(v[ctr]) == (int(s[ctr]) + 1) % 16
    if addr in c.CHECKSUM_SIGNALS:
      assert dat[7] == c.tesla_checksum(addr, dat)
  ap = c.unpack(0x399, out[0x399])
  s = stock(0x399)
  for keep in ("DAS_blindSpotRearLeft", "DAS_blindSpotRearRight", "DAS_fusedSpeedLimit",
               "DAS_visionOnlySpeedLimit", "DAS_suppressSpeedWarning", "DAS_fleetSpeedState"):
    assert ap[keep] == s[keep], keep
  st2 = c.unpack(0x389, out[0x389])
  s2 = stock(0x389)
  for keep in ("DAS_accSpeedLimit", "DAS_ACC_report", "DAS_robState", "DAS_ppOffsetDesiredRamp",
               "DAS_pmmSysFaultReason", "DAS_radarTelemetry"):
    assert st2[keep] == s2[keep], keep


def test_das_acc_speed_limit_kept_when_cruise_set_unset():
  # Default: stock DAS_accSpeedLimit preserved (no OP overlay).
  st2 = c.unpack(0x389, _send(c.ClusterController(), hud(), all_stock(), NS)[0x389])
  assert st2["DAS_accSpeedLimit"] == stock(0x389)["DAS_accSpeedLimit"]


def test_das_acc_speed_limit_written_from_op_set_when_engaged():
  # SLC raise path: IC set digit tracks OP cruise (mph), no stalk injection.
  out = _send(c.ClusterController(), hud(cruise_set_mph=51.0), all_stock(), NS)
  st2 = c.unpack(0x389, out[0x389])
  assert st2["DAS_accSpeedLimit"] == pytest.approx(51.0, abs=0.4)  # DBC factor 0.4
  # Fused speed-limit sign fields on 0x399 stay stock
  ap = c.unpack(0x399, out[0x399])
  s = stock(0x399)
  assert ap["DAS_fusedSpeedLimit"] == s["DAS_fusedSpeedLimit"]


def test_das_acc_speed_limit_30_packs_as_dig_30_not_60():
  """Regression: DBC 0.4 so raised 30 → raw 75 → dig 30 (not raw 150 → dig 60).

  Route 21 @ bd26defe packed 30 with legacy factor 0.2 (raw=150); IC scales
  0.4 and showed dig 60 (=2×). Factor 0.4 packs raw=75 → dig 30.
  """
  out = _send(c.ClusterController(), hud(cruise_set_mph=30.0), all_stock(), NS)
  dat = out[0x389]
  word = int.from_bytes(dat, "little")
  raw = c.get_raw(word, 0, 10)
  assert raw == 75, f"expected raw 75 for 30 mph @0.4, got {raw} (legacy 0.2 would be 150)"
  assert raw != 150
  st2 = c.unpack(0x389, dat)
  assert st2["DAS_accSpeedLimit"] == pytest.approx(30.0, abs=0.4)
  # Dig as IC would show with 0.4 scale
  assert raw * 0.4 == pytest.approx(30.0)


def test_das_acc_speed_limit_not_written_in_post_disengage():
  ctrl = c.ClusterController()
  _send(ctrl, hud(cruise_set_mph=51.0), all_stock(), NS)  # engage
  out = _send(ctrl, hud(enabled=False, cruise_set_mph=51.0), all_stock(), 2 * NS)
  assert 0x389 in out
  st2 = c.unpack(0x389, out[0x389])
  # post mode must keep stock acc speed limit (no OP overlay)
  assert st2["DAS_accSpeedLimit"] == stock(0x389)["DAS_accSpeedLimit"]


def test_counter_wraps():
  s = stock(0x399)
  s["DAS_statusCounter"] = 15
  out = _send(c.ClusterController(), hud(), {0x399: s}, NS)
  assert int(c.unpack(0x399, out[0x399])["DAS_statusCounter"]) == 0


def test_engaged_autopilot_state_and_hands_on():
  ctrl = c.ClusterController()
  ap = c.unpack(0x399, _send(ctrl, hud(), all_stock(), NS)[0x399])
  assert ap["autopilotStatus"] == 5
  assert ap["DAS_autopilotHandsOnState"] == 2
  cases = [
    (dict(steer_required=True), 3),
    (dict(steer_required=True, audible=True), 5),
    (dict(human_steering=True), 3),
    (dict(steer_required=True, audible=True, human_steering=True), 3),
  ]
  for kw, want in cases:
    ap = c.unpack(0x399, _send(ctrl, hud(**kw), all_stock(), NS)[0x399])
    assert ap["DAS_autopilotHandsOnState"] == want, kw


def test_stock_active_state_is_left_alone():
  s = stock(0x399)
  s["autopilotStatus"] = 3
  ap = c.unpack(0x399, _send(c.ClusterController(), hud(), {0x399: s}, NS)[0x399])
  assert ap["autopilotStatus"] == 3


def test_alc_state_from_stock_lane_bits_and_op_lane_change():
  def alc(left, right, **kw):
    lanes = stock(0x239)
    lanes["DAS_leftLaneExists"], lanes["DAS_rightLaneExists"] = left, right
    out = _send(c.ClusterController(), hud(**kw), {0x239: lanes, 0x399: stock(0x399)}, NS)
    return c.unpack(0x399, out[0x399])["DAS_autoLaneChangeState"]
  assert alc(1, 1) == 8
  assert alc(1, 0) == 6
  assert alc(0, 1) == 7
  assert alc(0, 0) == 1
  assert alc(1, 1, left_blinker=True) == 9
  assert alc(1, 1, right_blinker=True) == 10


def test_alc_uses_last_lanes_frame_when_none_new_this_step():
  ctrl = c.ClusterController()
  lanes = stock(0x239)
  lanes["DAS_leftLaneExists"], lanes["DAS_rightLaneExists"] = 1, 0
  _send(ctrl, hud(), {0x239: lanes}, NS)
  out = _send(ctrl, hud(), {0x399: stock(0x399)}, NS + 10_000_000)
  assert c.unpack(0x399, out[0x399])["DAS_autoLaneChangeState"] == 6


def test_op_warnings_add_and_never_clear_stock_warnings():
  s = stock(0x399)
  s["DAS_forwardCollisionWarning"] = 1
  s["DAS_laneDepartureWarning"] = 3
  s2 = stock(0x389)
  s2["DAS_longCollisionWarning"] = 6
  out = _send(c.ClusterController(), hud(), {0x399: s, 0x389: s2}, NS)
  ap, st2 = c.unpack(0x399, out[0x399]), c.unpack(0x389, out[0x389])
  assert ap["DAS_forwardCollisionWarning"] == 1
  assert ap["DAS_laneDepartureWarning"] == 3
  assert st2["DAS_longCollisionWarning"] == 6

  out = _send(c.ClusterController(), hud(fcw=True, right_lane_depart=True), all_stock(), NS)
  ap, st2 = c.unpack(0x399, out[0x399]), c.unpack(0x389, out[0x389])
  assert ap["DAS_forwardCollisionWarning"] == 1
  assert ap["DAS_laneDepartureWarning"] == 2
  assert st2["DAS_longCollisionWarning"] == 1
  ap = c.unpack(0x399, _send(c.ClusterController(), hud(left_lane_depart=True), all_stock(), NS)[0x399])
  assert ap["DAS_laneDepartureWarning"] == 1


def test_engaged_status2_fields_and_csa_bits():
  s2 = stock(0x389)
  s2["DAS_activationFailureStatus"] = 2
  s2["DAS_driverInteractionLevel"] = 1
  dat = _send(c.ClusterController(), hud(), {0x389: s2}, NS)[0x389]
  v = c.unpack(0x389, dat)
  assert v["DAS_activationFailureStatus"] == 0
  assert v["DAS_driverInteractionLevel"] == 0
  word = int.from_bytes(dat, "little")
  assert c.get_raw(word, 32, 2) == c.CSA_ENABLE
  # Bit 31 (Tinkla DAS_relaxCruiseLimits) and bits 34+ stay stock.
  stock_word = int.from_bytes(STOCK[0x389][0], "little")
  assert c.get_raw(word, 31, 1) == c.get_raw(stock_word, 31, 1)
  assert c.get_raw(word, 34, 14) == c.get_raw(stock_word, 34, 14)


def test_lanes_path_from_curvature():
  ctrl = c.ClusterController()
  for k, want in ((0.0, 0.0), (0.0002, 0.0004), (-0.0002, -0.0004), (0.01, 0.0025), (-0.01, -0.0025)):
    v = c.unpack(0x239, _send(ctrl, hud(curvature=k), {0x239: stock(0x239)}, NS)[0x239])
    assert v["DAS_virtualLaneC2"] == pytest.approx(want, abs=2e-05), k
    assert v["DAS_virtualLaneC0"] == pytest.approx(0.0, abs=0.035)
    assert v["DAS_virtualLaneC1"] == pytest.approx(0.0, abs=0.0016)
    assert v["DAS_virtualLaneC3"] == pytest.approx(0.0, abs=2.4e-07)
    assert v["DAS_virtualLaneViewRange"] == 50
  lanes = stock(0x239)
  lanes["DAS_leftLaneExists"], lanes["DAS_rightLaneExists"] = 1, 0
  lanes["DAS_leftLineUsage"], lanes["DAS_rightLineUsage"] = 2, 0
  v = c.unpack(0x239, _send(ctrl, hud(), {0x239: lanes}, NS)[0x239])
  assert (v["DAS_leftLineUsage"], v["DAS_rightLineUsage"]) == (2, 0)
  assert (v["DAS_leftLaneExists"], v["DAS_rightLaneExists"]) == (1, 0)
  assert v["DAS_virtualLaneWidth"] == lanes["DAS_virtualLaneWidth"]
  assert v["DAS_leftFork"] == lanes["DAS_leftFork"]


def test_lanes_preserves_stock_usage_when_exists_zero():
  """AP1 stock: Exists often 0 while LineUsage is 2. Do not wipe usage."""
  lanes = stock(0x239)
  lanes["DAS_leftLaneExists"], lanes["DAS_rightLaneExists"] = 0, 0
  lanes["DAS_leftLineUsage"], lanes["DAS_rightLineUsage"] = 2, 2
  v = c.unpack(0x239, _send(c.ClusterController(), hud(), {0x239: lanes}, NS)[0x239])
  assert (v["DAS_leftLaneExists"], v["DAS_rightLaneExists"]) == (0, 0)
  assert (v["DAS_leftLineUsage"], v["DAS_rightLineUsage"]) == (2, 2)
  assert v["DAS_virtualLaneViewRange"] == 50


# --- when nothing is sent ---------------------------------------------------

def test_never_engaged_sends_nothing():
  ctrl = c.ClusterController()
  for i in range(5):
    assert ctrl.update(hud(enabled=False), all_stock(i), i * NS) == []


def test_no_stock_frame_no_send():
  ctrl = c.ClusterController()
  assert ctrl.update(hud(), {}, NS) == []
  out = _send(ctrl, hud(), {0x389: stock(0x389)}, 2 * NS)
  assert set(out) == {0x389}


def test_post_disengage_window():
  ctrl = c.ClusterController()
  _send(ctrl, hud(), all_stock(), 0)
  t0 = 1 * NS
  s = stock(0x399)
  s["autopilotStatus"] = 1
  s2 = stock(0x389)
  s2["DAS_activationFailureStatus"] = 1
  new = {0x399: s, 0x389: s2, 0x239: stock(0x239)}
  out = _send(ctrl, hud(enabled=False), new, t0)
  assert set(out) == {0x399, 0x389}  # lanes are engaged only
  ap = c.unpack(0x399, out[0x399])
  assert ap["autopilotStatus"] == 2
  # Post rewrite touches only the state; everything else is stock.
  assert ap["DAS_autopilotHandsOnState"] == s["DAS_autopilotHandsOnState"]
  assert ap["DAS_autoLaneChangeState"] == s["DAS_autoLaneChangeState"]
  st2 = c.unpack(0x389, out[0x389])
  assert st2["DAS_activationFailureStatus"] == 0
  stock_word = int.from_bytes(c.finish(0x389, c.pack(0x389, c.SIGNALS[0x389], s2), 0), "little")
  assert c.get_raw(int.from_bytes(out[0x389], "little"), 32, 2) == c.get_raw(stock_word, 32, 2)

  # Stock states other than UNAVAILABLE are not rewritten.
  for state in (0, 2, 3, 15):
    s3 = dict(s, autopilotStatus=state)
    ap = c.unpack(0x399, _send(ctrl, hud(enabled=False), {0x399: s3}, t0 + NS)[0x399])
    assert ap["autopilotStatus"] == state

  assert _send(ctrl, hud(enabled=False), new, t0 + c.POST_DISENGAGE_NS - 1)
  assert ctrl.update(hud(enabled=False), new, t0 + c.POST_DISENGAGE_NS) == []
  assert ctrl.update(hud(enabled=False), new, t0 + 10 * NS) == []


def test_reengage_inside_window_is_engaged():
  ctrl = c.ClusterController()
  _send(ctrl, hud(), all_stock(), 0)
  _send(ctrl, hud(enabled=False), all_stock(), NS)
  out = _send(ctrl, hud(), all_stock(), 2 * NS)
  assert set(out) == set(c.CLUSTER_ADDRS)
  assert c.unpack(0x399, out[0x399])["autopilotStatus"] == 5


def test_post_window_never_shows_active_state():
  ctrl = c.ClusterController()
  _send(ctrl, hud(), all_stock(), 0)
  for i, dat in enumerate(STOCK[0x399]):
    v = c.unpack(0x399, dat)
    if int(v["autopilotStatus"]) in (3, 4, 5):
      continue
    out = _send(ctrl, hud(enabled=False), {0x399: v}, NS + i)
    assert int(c.unpack(0x399, out[0x399])["autopilotStatus"]) not in (3, 4, 5)


# --- CarState / CarController wiring ---------------------------------------

class _FakeParser:
  def __init__(self, dbc, messages, bus=0):
    self.dbc, self.messages, self.bus = dbc, list(messages), bus
    self.vl, self.vl_all = {}, {}


class _FakeDefine:
  def __init__(self, dbc):
    self.dv = {}


class _FakePacker:
  def __init__(self, dbc):
    self.dbc = dbc

  def make_can_msg(self, name, bus, values):
    # Deterministic stand-in: name + sorted values. Enough to compare runs.
    return [name, 0, repr(sorted(values.items())).encode(), bus]


@pytest.fixture
def tesla_modules(monkeypatch):
  parser = types.ModuleType("opendbc.can.parser")
  parser.CANParser = _FakeParser
  packer = types.ModuleType("opendbc.can.packer")
  packer.CANPacker = _FakePacker
  define = types.ModuleType("opendbc.can.can_define")
  define.CANDefine = _FakeDefine
  monkeypatch.setitem(sys.modules, "opendbc.can.can_define", define)
  # interfaces.py pulls msgq (AArch64 .so here). Only the base classes are used.
  interfaces = types.ModuleType("openpilot.selfdrive.car.interfaces")
  interfaces.CarStateBase = type("CarStateBase", (), {})
  interfaces.CarControllerBase = type("CarControllerBase", (), {})
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.car.interfaces", interfaces)
  # swaglog pulls zmq. Record exceptions instead.
  swaglog = types.ModuleType("openpilot.common.swaglog")
  swaglog.cloudlog = SimpleNamespace(exception=lambda *a, **k: None)
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", swaglog)
  monkeypatch.setitem(sys.modules, "opendbc.can.parser", parser)
  monkeypatch.setitem(sys.modules, "opendbc.can.packer", packer)
  for name in ("openpilot.selfdrive.car.tesla.carstate", "openpilot.selfdrive.car.tesla.carcontroller"):
    sys.modules.pop(name, None)
  cs = importlib.import_module("openpilot.selfdrive.car.tesla.carstate")
  cc = importlib.import_module("openpilot.selfdrive.car.tesla.carcontroller")
  yield cs, cc
  for name in ("openpilot.selfdrive.car.tesla.carstate", "openpilot.selfdrive.car.tesla.carcontroller"):
    sys.modules.pop(name, None)


def _cp(fp):
  return SimpleNamespace(carFingerprint=fp, openpilotLongitudinalControl=True)


def test_cam_parser_adds_cluster_frames_for_ap1_only_without_alive_check(tesla_modules):
  cs, _ = tesla_modules
  from openpilot.selfdrive.car.tesla.values import CAR
  ap1 = cs.CarState.get_cam_can_parser(_cp(CAR.TESLA_AP1_MODELS), None)
  assert ("DAS_control", 40) in ap1.messages
  for name in ("AutopilotStatus", "DAS_status2", "DAS_lanes"):
    assert (name, 0) in ap1.messages
  for fp in (CAR.TESLA_AP2_MODELS, CAR.TESLA_MODELS_RAVEN):
    other = cs.CarState.get_cam_can_parser(_cp(fp), None)
    assert not {m for m, _ in other.messages} & {"AutopilotStatus", "DAS_status2", "DAS_lanes"}
  # Chassis parser untouched.
  chassis = cs.CarState.get_can_parser(_cp(CAR.TESLA_AP1_MODELS), None)
  assert not {m for m, _ in chassis.messages} & {"AutopilotStatus", "DAS_status2", "DAS_lanes"}


def test_new_cluster_frames_reads_only_frames_seen_this_step(tesla_modules):
  cs, _ = tesla_modules
  cp_cam = SimpleNamespace(vl={}, vl_all={})
  for addr in c.CLUSTER_ADDRS:
    cp_cam.vl[c.MSG_NAMES[addr]] = stock(addr)
    cp_cam.vl_all[c.MSG_NAMES[addr]] = {}
  assert cs.CarState.new_cluster_frames(cp_cam) == {}
  cp_cam.vl_all["DAS_lanes"] = {"DAS_lanesCounter": [3.0]}
  got = cs.CarState.new_cluster_frames(cp_cam)
  assert set(got) == {c.DAS_LANES}
  assert got[c.DAS_LANES] == stock(c.DAS_LANES)


def _cc_inputs(enabled, frame_counters, cluster_stock):
  from cereal import car
  CC = car.CarControl.new_message()
  CC.enabled = enabled
  CC.latActive = enabled
  CC.longActive = enabled
  CC.actuators.steeringAngleDeg = 2.0
  CC.actuators.accel = 0.3
  CC.actuators.curvature = 0.001
  CC.hudControl.visualAlert = car.CarControl.HUDControl.VisualAlert.steerRequired
  CS = SimpleNamespace(
    out=SimpleNamespace(steeringAngleDeg=1.0, vEgo=10.0),
    steer_warning="EAC_ERROR_IDLE", hands_on_level=0, eac_fault=False, eac_status="EAC_ACTIVE", acc_state=4,
    das_control_counters=deque(frame_counters), msg_stw_actn_req={}, cluster_stock=cluster_stock,
  )
  return CC.as_reader(), CS


def _run(cc_mod, fp, cluster_on, steps=12, enabled=True):
  ctl = cc_mod.CarController("tesla_can", _cp(fp), None)
  if not cluster_on:
    ctl.cluster = None
  sends = []
  for i in range(steps):
    new = all_stock(i % 3) if i % 5 == 0 else {}
    CC, CS = _cc_inputs(enabled, [i % 8], new)
    _, can = ctl.update(CC, CS, i * 10_000_000, None)
    sends.append(can)
  return sends


def test_cluster_frames_only_append_to_ap1_can_sends(tesla_modules):
  _, cc = tesla_modules
  from openpilot.selfdrive.car.tesla.values import CAR
  for enabled in (True, False):
    with_cluster = _run(cc, CAR.TESLA_AP1_MODELS, True, enabled=enabled)
    without = _run(cc, CAR.TESLA_AP1_MODELS, False, enabled=enabled)
    saw_cluster = False
    for a, b in zip(with_cluster, without):
      extra = [m for m in a if m[0] in c.CLUSTER_ADDRS]
      rest = [m for m in a if m[0] not in c.CLUSTER_ADDRS]
      assert rest == b
      assert all(m[3] == 0 and len(m[2]) == 8 for m in extra)
      saw_cluster |= bool(extra)
    assert saw_cluster is enabled


def test_non_ap1_never_sends_cluster_frames(tesla_modules):
  _, cc = tesla_modules
  from openpilot.selfdrive.car.tesla.values import CAR
  for fp in (CAR.TESLA_AP2_MODELS, CAR.TESLA_MODELS_RAVEN):
    ctl = cc.CarController("tesla_can", _cp(fp), None)
    assert ctl.cluster is None
    for can in _run(cc, fp, True):
      assert not [m for m in can if m[0] in c.CLUSTER_ADDRS]


def test_cluster_failure_disables_cluster_but_keeps_actuator_frames(tesla_modules, monkeypatch):
  _, cc = tesla_modules
  from openpilot.selfdrive.car.tesla.values import CAR
  ctl = cc.CarController("tesla_can", _cp(CAR.TESLA_AP1_MODELS), None)

  def boom(*a, **k):
    raise RuntimeError("test")
  monkeypatch.setattr(ctl.cluster, "update", boom)
  CC, CS = _cc_inputs(True, [1], all_stock())
  _, can = ctl.update(CC, CS, 0, None)
  assert ctl.cluster is None
  assert any(m[0] == "DAS_steeringControl" for m in can)
  assert any(m[0] == "DAS_control" for m in can)
  assert not [m for m in can if m[0] in c.CLUSTER_ADDRS]


def test_panda_lists_cluster_addresses_for_ap1_only():
  header = (ROOT / "panda/board/safety/safety_tesla.h").read_text()
  ap1_list = header.split("const CanMsg TESLA_AP1_TX_MSGS[]")[1].split(";")[0]
  shared = header.split("const CanMsg TESLA_TX_MSGS[]")[1].split(";")[0]
  pt = header.split("const CanMsg TESLA_PT_TX_MSGS[]")[1].split(";")[0]
  for addr in ("0x399", "0x389", "0x239"):
    assert "{%s, 0, 8}" % addr in ap1_list
    assert addr not in shared
    assert addr not in pt
  for addr in ("0x3e9", "0x309", "0x3a9", "0x659"):
    assert addr not in ap1_list



# --- enableICIntegration toggle + modelV2 path --------------------------------

def test_enable_ic_integration_defaults():
  from openpilot.selfdrive.car.tesla import toggles as tg
  from openpilot.selfdrive.car.tesla.values import CAR
  assert tg.enable_ic_integration(CAR.TESLA_AP1_MODELS, None) is True
  assert tg.enable_ic_integration(CAR.TESLA_AP1_MODELS, "") is True
  assert tg.enable_ic_integration(CAR.TESLA_AP1_MODELS, "1") is True
  assert tg.enable_ic_integration(CAR.TESLA_AP1_MODELS, "0") is False
  assert tg.enable_ic_integration(CAR.TESLA_AP1_MODELS, "off") is False
  for fp in (CAR.TESLA_AP2_MODELS, CAR.TESLA_MODELS_RAVEN, None, "not_tesla"):
    assert tg.enable_ic_integration(fp, None) is False
    assert tg.enable_ic_integration(fp, "1") is False


def test_enable_ic_integration_file_backed(tmp_path, monkeypatch):
  from openpilot.selfdrive.car.tesla import toggles as tg
  from openpilot.selfdrive.car.tesla.values import CAR
  path = tmp_path / "EnableICIntegration"
  monkeypatch.setattr(tg, "_ic_integration_path", path)
  assert tg.enable_ic_integration(CAR.TESLA_AP1_MODELS) is True  # absent => on
  tg.set_enable_ic_integration(False, path=path)
  assert path.read_text() == "0"
  assert tg.enable_ic_integration(CAR.TESLA_AP1_MODELS) is False
  tg.set_enable_ic_integration(True, path=path)
  assert tg.enable_ic_integration(CAR.TESLA_AP1_MODELS) is True


def test_toggle_off_sends_nothing_even_when_engaged():
  ctrl = c.ClusterController()
  assert ctrl.update(hud(ic_integration=False), all_stock(), NS) == []
  # Re-enable works after off.
  out = _send(ctrl, hud(ic_integration=True), all_stock(), 2 * NS)
  assert set(out) == set(c.CLUSTER_ADDRS)


def test_toggle_off_clears_post_disengage_window():
  ctrl = c.ClusterController()
  _send(ctrl, hud(), all_stock(), 0)
  # Enter post window
  _send(ctrl, hud(enabled=False), all_stock(), NS)
  # Toggle off mid-window: nothing, and state resets.
  assert ctrl.update(hud(enabled=False, ic_integration=False), all_stock(), 2 * NS) == []
  # Still disabled with toggle back on but never re-engaged: no post rewrite
  # because toggle-off cleared disengaged_ns.
  assert ctrl.update(hud(enabled=False, ic_integration=True), all_stock(), 3 * NS) == []


def test_lanes_use_model_path_when_present():
  path = c.ModelPath(c0=0.35, c1=-0.05, c2=0.0012, c3=0.0, view_range_m=72)
  v = c.unpack(0x239, _send(c.ClusterController(), hud(model_path=path, curvature=0.01),
                            {0x239: stock(0x239)}, NS)[0x239])
  assert v["DAS_virtualLaneC0"] == pytest.approx(0.0, abs=0.035)  # path starts at the car
  assert v["DAS_virtualLaneC1"] == pytest.approx(0.0, abs=0.0016)  # Tinkla: C1 suppressed
  assert v["DAS_virtualLaneC2"] == pytest.approx(0.0012, abs=2e-05)
  assert v["DAS_virtualLaneC3"] == pytest.approx(0.0, abs=2.4e-07)
  assert v["DAS_virtualLaneViewRange"] == 72


def test_view_range_clamped_to_dbc():
  for raw, want in ((-5, 0), (0, 0), (50, 50), (160, 160), (200, 160), (72.4, 72)):
    assert c.clamp_view_range_m(raw) == want
  path = c.ModelPath(c0=0, c1=0, c2=0, c3=0, view_range_m=999)
  v = c.unpack(0x239, _send(c.ClusterController(), hud(model_path=path),
                            {0x239: stock(0x239)}, NS)[0x239])
  assert v["DAS_virtualLaneViewRange"] == 160


def test_model_path_missing_falls_back_to_curvature_and_50m():
  v = c.unpack(0x239, _send(c.ClusterController(), hud(model_path=None, curvature=0.0002),
                            {0x239: stock(0x239)}, NS)[0x239])
  assert v["DAS_virtualLaneC0"] == pytest.approx(0.0, abs=0.035)
  assert v["DAS_virtualLaneC1"] == pytest.approx(0.0, abs=0.0016)
  assert v["DAS_virtualLaneC2"] == pytest.approx(0.0004, abs=2e-05)
  assert v["DAS_virtualLaneViewRange"] == 50


def test_path_from_model_v2_fits_poly_and_scales():
  # Curved path from the car: y = 0.0001 x^2, out to 60 m.
  xs = [float(i) for i in range(1, 61)]
  ys = [0.0001 * x * x for x in xs]
  model = SimpleNamespace(position=SimpleNamespace(x=xs, y=ys))
  path = c.path_from_model_v2(model)
  assert path is not None
  f = 1.0 / c.IC_LANE_SCALE
  assert path.c0 == 0.0
  assert path.c1 == 0.0  # Tinkla suppress_x_coord
  assert path.c2 == pytest.approx(0.0001 * f * f, abs=2e-5)
  assert path.c3 == 0.0
  assert path.view_range_m == 60


def test_path_from_model_v2_heading_does_not_tilt_or_offset_line():
  # A slanted model path (979dbf8 regression) must not produce C0/C1.
  xs = [float(i) for i in range(1, 101)]
  ys = [0.3 + 0.05 * x for x in xs]
  path = c.path_from_model_v2(SimpleNamespace(position=SimpleNamespace(x=xs, y=ys)))
  assert path is not None
  assert (path.c0, path.c1, path.c3) == (0.0, 0.0, 0.0)
  assert path.view_range_m == 100  # Tinkla max_distance cap
  path = c.ModelPath(c0=1.5, c1=0.2, c2=0.0, c3=0.0, view_range_m=60)
  v = c.unpack(0x239, _send(c.ClusterController(), hud(model_path=path),
                            {0x239: stock(0x239)}, NS)[0x239])
  assert v["DAS_virtualLaneC0"] == pytest.approx(0.0, abs=0.035)
  assert v["DAS_virtualLaneC1"] == pytest.approx(0.0, abs=0.0016)


def test_path_from_model_v2_rejects_short_or_empty():
  assert c.path_from_model_v2(SimpleNamespace(position=SimpleNamespace(x=[], y=[]))) is None
  assert c.path_from_model_v2(SimpleNamespace(position=SimpleNamespace(x=[1, 2], y=[0, 0]))) is None
  # xmax < MODEL_PATH_MIN_M
  xs = [0.5, 1.0, 2.0, 3.0]
  ys = [0.0, 0.0, 0.0, 0.0]
  assert c.path_from_model_v2(SimpleNamespace(position=SimpleNamespace(x=xs, y=ys))) is None


def test_stock_line_usage_preserved_with_model_path():
  lanes = stock(0x239)
  lanes["DAS_leftLaneExists"], lanes["DAS_rightLaneExists"] = 0, 0
  lanes["DAS_leftLineUsage"], lanes["DAS_rightLineUsage"] = 2, 2
  path = c.ModelPath(c0=0.1, c1=0.0, c2=0.0005, c3=0.0, view_range_m=40)
  v = c.unpack(0x239, _send(c.ClusterController(), hud(model_path=path), {0x239: lanes}, NS)[0x239])
  assert (v["DAS_leftLineUsage"], v["DAS_rightLineUsage"]) == (2, 2)
  assert (v["DAS_leftLaneExists"], v["DAS_rightLaneExists"]) == (0, 0)


def test_carcontroller_toggle_off_sends_no_cluster(tesla_modules, monkeypatch, tmp_path):
  _, cc = tesla_modules
  from openpilot.selfdrive.car.tesla import toggles as tg
  from openpilot.selfdrive.car.tesla.values import CAR
  path = tmp_path / "EnableICIntegration"
  path.write_text("0")
  monkeypatch.setattr(tg, "_ic_integration_path", path)
  ctl = cc.CarController("tesla_can", _cp(CAR.TESLA_AP1_MODELS), None)
  # Avoid real messaging
  ctl._model_sm = False
  CC, CS = _cc_inputs(True, [1], all_stock())
  _, can = ctl.update(CC, CS, 0, None)
  assert not [m for m in can if m[0] in c.CLUSTER_ADDRS]
  # Actuators still present
  assert any(m[0] == "DAS_steeringControl" for m in can)


def test_carcontroller_uses_model_path_when_engaged(tesla_modules, monkeypatch):
  _, cc = tesla_modules
  from openpilot.selfdrive.car.tesla.values import CAR
  ctl = cc.CarController("tesla_can", _cp(CAR.TESLA_AP1_MODELS), None)
  path = c.ModelPath(c0=0.5, c1=0.02, c2=-0.001, c3=0.0, view_range_m=55)
  monkeypatch.setattr(ctl, "_model_path_for_cluster", lambda enabled, ic_on: path if enabled and ic_on else None)
  CC, CS = _cc_inputs(True, [1], {0x239: stock(0x239)})
  _, can = ctl.update(CC, CS, 0, None)
  lanes = [m for m in can if m[0] == 0x239]
  assert len(lanes) == 1
  v = c.unpack(0x239, lanes[0][2])
  assert v["DAS_virtualLaneC0"] == pytest.approx(0.0, abs=0.035)
  assert v["DAS_virtualLaneC2"] == pytest.approx(-0.001, abs=2e-05)
  assert v["DAS_virtualLaneViewRange"] == 55


def test_enable_ic_integration_not_in_frogpilot_default_params():
  """Must not join frogpilot_default_params (params_pyx.so has no Tesla* keys)."""
  src = (ROOT / "frogpilot/common/frogpilot_variables.py").read_text()
  assert "TeslaLongControl and TeslaStalkFollow stay out of this list" in src
  assert "params_bogpilot/EnableICIntegration" in src
  defaults_block = src.split("frogpilot_default_params")[1].split(")")[0]
  assert "EnableICIntegration" not in defaults_block
  assert "TeslaLongControl" not in defaults_block
  assert "TeslaStalkFollow" not in defaults_block

