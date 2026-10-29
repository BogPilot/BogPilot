"""AP1 0x488 counter continuity at stock <-> openpilot handovers.

Drive 18/19: every disengage where the stock stream came back with the
counter openpilot had just sent (delta 0) latched EPAS code 7, and an engage
with a regressing/repeated counter latched code 7 and the EPB revoked EAC.
"""
import ast
from collections import deque
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.car.tesla.actuator_plan import build_actuator_plan
from openpilot.selfdrive.car.tesla.steer_counter import (
  COUNTER_LEAD, STOCK_STALE_FRAMES, Ap1SteerCounterSync, SteerTick,
)
from openpilot.selfdrive.car.tesla.tests.test_ap1_resume_hold import cc_mod  # noqa: F401 (fixture)

EAC_ACTIVE = "EAC_ACTIVE"
ACC_ON = 4
# Panda: stock 0x488 is not forwarded within this long after the last allowed
# openpilot 0x488 (TESLA_STEER_SUBSTITUTE_TIMEOUT_US).
SUBSTITUTE_TIMEOUT_MS = 100.0


# --- Ap1SteerCounterSync -------------------------------------------------------

def test_follows_stock_frames_with_lead():
  s = Ap1SteerCounterSync()
  for stock in range(40):
    tick = s.update([stock % 16], 2 * stock)
    assert tick.send is True
    assert tick.counter == (stock + COUNTER_LEAD) % 16
    s.commit(tick.counter)
    # Odd control frame: no stock frame, stream still fresh → no send.
    assert s.update([], 2 * stock + 1).send is False


def test_several_stock_frames_in_one_step_use_newest():
  s = Ap1SteerCounterSync()
  tick = s.update([3, 4], 0)
  assert tick == SteerTick(True, 6)


def test_stale_stock_falls_back_to_own_cadence_and_continues_counter():
  s = Ap1SteerCounterSync()
  t = s.update([9], 0)
  s.commit(t.counter)  # 11
  sent = []
  for frame in range(1, 30):
    t = s.update([], frame)
    if t.send:
      s.commit(t.counter)
      sent.append((frame, t.counter))
  # Nothing while still fresh, then every even frame, counter +1 each time.
  assert sent[0][0] >= STOCK_STALE_FRAMES
  assert all(f % 2 == 0 for f, _ in sent)
  assert [c for _, c in sent] == [(12 + i) % 16 for i in range(len(sent))]


def test_no_stock_ever_matches_old_cadence():
  s = Ap1SteerCounterSync()
  for frame in range(STOCK_STALE_FRAMES, 40):
    t = s.update([], frame)
    assert t.send == (frame % 2 == 0)
    if t.send:
      assert t.counter == (frame // 2) % 16
      s.commit(t.counter)


def test_never_repeats_last_counter():
  s = Ap1SteerCounterSync()
  t = s.update([5], 0)
  s.commit(t.counter)  # 7
  t = s.update([5], 2)  # stock repeated itself
  assert t.counter != 7


def test_logged_stock_jitter_does_not_trip_fallback():
  # Worst logged stock gap was 45 ms (4-5 control frames): no fallback send.
  s = Ap1SteerCounterSync()
  t = s.update([1], 0)
  s.commit(t.counter, 0)
  assert all(not s.update([], f).send for f in range(1, 5))
  assert s.update([2], 5) == SteerTick(True, 4)


def test_late_stock_after_fallback_never_steps_back():
  s = Ap1SteerCounterSync()
  t = s.update([1], 0)
  s.commit(t.counter, 0)  # 3
  sent = [3]
  frame = 1
  for _ in range(12):  # 120 ms stock dropout: fallback sends 4, 5, ...
    t = s.update([], frame)
    if t.send:
      s.commit(t.counter, frame)
      sent.append(t.counter)
    frame += 1
  for stock in (2, 3, 4, 5, 6, 7, 8, 9):  # stock resumes behind openpilot
    t = s.update([stock], frame)
    s.commit(t.counter, frame)
    sent.append(t.counter)
    frame += 2
  assert all((b - a) % 16 == 1 for a, b in zip(sent, sent[1:], strict=False)), sent


# --- build_actuator_plan -----------------------------------------------------

def _plan(frame, steer_tick=None, enabled=True):
  return build_actuator_plan(
    frame, enabled, False, True, enabled, enabled, 1.0, 2.0, 1.0, 10.0, 0.0, ACC_ON, deque(), False,
    chassis_das_only=True, epas_error="EAC_ERROR_IDLE", eac_status=EAC_ACTIVE, steer_tick=steer_tick)


def test_plan_without_tick_keeps_old_cadence_and_counter():
  for frame in range(8):
    plan = _plan(frame)
    if frame % 2:
      assert plan.steer is None
    else:
      assert plan.steer.counter == (frame // 2) % 16


def test_plan_uses_tick():
  assert _plan(0, SteerTick(False, 3)).steer is None
  assert _plan(1, SteerTick(True, 3)).steer.counter == 3
  assert _plan(1, SteerTick(True, 3), enabled=False).steer is None


# --- Handover simulation through CarController --------------------------------

def _counter_step(ctl, enabled, stock_counters, frame_ms):
  from cereal import car
  CC = car.CarControl.new_message()
  CC.enabled = enabled
  CC.latActive = enabled
  CC.longActive = enabled
  CC.actuators.steeringAngleDeg = 1.0
  CS = SimpleNamespace(
    out=SimpleNamespace(steeringAngleDeg=1.0, vEgo=10.0, gasPressed=False),
    steer_warning="EAC_ERROR_IDLE", hands_on_level=0, eac_fault=False, eac_status=EAC_ACTIVE,
    acc_state=ACC_ON, das_control_counters=deque(), msg_stw_actn_req={}, cluster_stock={},
    stock_steer_counters=list(stock_counters),
  )
  _, can = ctl.update(CC.as_reader(), CS, int(frame_ms * 1e6), None)
  out = []
  for m in can:
    if m[0] == "DAS_steeringControl":
      out.append(dict(ast.literal_eval(m[2].decode()))["DAS_steeringControlCounter"])
  return out


def _simulate(mod, phase_ms, c0, period_ms, use_sync, engage_ms=1003.0, disengage_ms=2507.0,
              lag_frames=2, end_ms=3500.0):
  """EPAS-visible 0x488 counters (time ordered) across one engage and one disengage."""
  from openpilot.selfdrive.car.tesla.values import CAR
  ctl = mod.CarController("tesla_can", SimpleNamespace(
    carFingerprint=CAR.TESLA_AP1_MODELS, openpilotLongitudinalControl=True), None)
  ctl.cluster = None
  if not use_sync:
    ctl.ap1_steer_sync = None

  stock = []  # (t_ms, counter)
  j = 0
  while phase_ms + j * period_ms < end_ms:
    stock.append((phase_ms + j * period_ms, (c0 + j) % 16))
    j += 1

  def allowed(t):
    return engage_ms <= t < disengage_ms

  op = []  # (t_ms, counter, panda_allowed)
  si = 0
  for k in range(int(end_ms // 10)):
    t = k * 10.0
    batch = []
    while si < len(stock) and stock[si][0] <= t:
      batch.append(stock[si][1])
      si += 1
    cc_enabled = allowed(t - lag_frames * 10.0)
    tx_t = t + 2.0
    for ctr in _counter_step(ctl, cc_enabled, batch, t):
      op.append((tx_t, ctr, allowed(tx_t)))

  bus = []
  last_op_ok = None
  events = sorted([(t, 0, c) for t, c in stock] + [(t, 1, c) for t, c, ok in op if ok])
  for t, is_op, c in events:
    if is_op:
      last_op_ok = t
      bus.append(c)
    elif last_op_ok is None or t - last_op_ok >= SUBSTITUTE_TIMEOUT_MS:
      bus.append(c)
  return bus


def _bad_steps(bus):
  # 0 = repeat, 9..15 = backwards (mod 16). Forward jumps of 1..8 are fine.
  return [(a, b) for a, b in zip(bus, bus[1:], strict=False) if (b - a) % 16 == 0 or (b - a) % 16 > 8]


PHASES = [0.5, 3.0, 5.0, 7.5, 9.9, 12.0, 15.0, 19.5]


@pytest.mark.parametrize("period_ms", [20.0, 20.01])
@pytest.mark.parametrize("c0", range(16))
def test_handover_counter_only_moves_forward(cc_mod, c0, period_ms):  # noqa: F811
  for phase in PHASES:
    bus = _simulate(cc_mod, phase, c0, period_ms, use_sync=True)
    assert _bad_steps(bus) == [], (phase, c0)


def test_old_cadence_reproduces_repeat_or_regression(cc_mod):  # noqa: F811
  """Documents the bug: the free-running counter repeats or regresses for some alignments."""
  bad = 0
  for c0 in range(16):
    for phase in PHASES:
      bad += bool(_bad_steps(_simulate(cc_mod, phase, c0, 20.0, use_sync=False)))
  assert bad > 0


def test_controller_counter_is_stock_plus_lead(cc_mod):  # noqa: F811
  from openpilot.selfdrive.car.tesla.values import CAR
  ctl = cc_mod.CarController("tesla_can", SimpleNamespace(
    carFingerprint=CAR.TESLA_AP1_MODELS, openpilotLongitudinalControl=True), None)
  ctl.cluster = None
  for k in range(1, 40):  # first stock frame at k = 1 (before it: stale fallback)
    stock = [(k // 2 + 5) % 16] if k % 2 else []
    got = _counter_step(ctl, True, stock, k * 10.0)
    if stock:
      assert got == [(stock[0] + COUNTER_LEAD) % 16]
    else:
      assert got == []
