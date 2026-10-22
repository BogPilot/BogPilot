"""On-road 0x309 pedestrian display test (ic_onroad.py) and the Dy sign fix."""

from types import SimpleNamespace

import pytest

from openpilot.selfdrive.car.tesla.tests.test_ap1_cluster import tesla_modules  # noqa: F401  (fixture)

from openpilot.selfdrive.car.tesla import ic_discovery as d
from openpilot.selfdrive.car.tesla import ic_onroad as o
from openpilot.selfdrive.car.tesla.values import CAR

AP1 = CAR.TESLA_AP1_MODELS
MS = 1_000_000
DT = 10 * MS


@pytest.fixture
def toggle(tmp_path, monkeypatch):
  p = tmp_path / "ICOnRoad"
  monkeypatch.setattr(o, "_iconroad_path", p)
  monkeypatch.setattr(d, "_icdiscovery_path", tmp_path / "ICDiscovery")
  return p


# ---- left/right fix ----

def test_dy_sign_left_negative_right_positive():
  assert d.das_dy_from_yrel(3.5) == -3.5   # + yRel is left -> DAS Dy < 0
  assert d.das_dy_from_yrel(-3.5) == 3.5
  left = [s for s in d.SEQUENCE if s[0] == d.MUX_LEFT]
  right = [s for s in d.SEQUENCE if s[0] == d.MUX_RIGHT]
  assert left and right
  assert all(dy < 0 for _, _, dy in left)
  assert all(dy > 0 for _, _, dy in right)
  # Decoded frames match the stock AP1 sign (LEFT median -3.50 m, RIGHT +3.85 m).
  for mux, vtype, dy in left + right:
    v = d.decode_frame(d.build_frame(mux, d.object_raws(vtype, d.TEST_DX_M, dy)))
    assert (v["Dy"] < 0) == (mux == d.MUX_LEFT)
    assert abs(v["Dy"] - dy) <= 0.35 / 2 + 1e-9


def test_onroad_frame_side_follows_radar_side():
  f = o.pedestrian_raws(9, 6.0, 3.0, 0.0)   # radar yRel +3 m = 10 ft left
  v = d.decode_frame(d.build_frame(d.MUX_LEFT, f))
  assert v["Type"] == 5 and v["Dx"] == 6.0 and abs(v["Dy"] + 3.0) <= 0.175 and v["Id"] == 9
  f = o.pedestrian_raws(200, 6.0, -3.0, 0.0)
  v = d.decode_frame(d.build_frame(d.MUX_RIGHT, f))
  assert abs(v["Dy"] - 3.0) <= 0.175 and v["Id"] == 200 % 127


# ---- toggle / gate ----

def test_default_off_modes_and_non_ap1(toggle):
  assert o.ic_onroad_mode(AP1) == o.MODE_OFF
  for s, m in (("1", o.MODE_BRAKE), ("brake", o.MODE_BRAKE), ("brake_all", o.MODE_BRAKE_ALL),
               ("lowspeed", o.MODE_LOWSPEED), (" Brake\n", o.MODE_BRAKE)):
    assert o.ic_onroad_mode(AP1, s) == m
  for s in ("0", "", "off", "yes", "fast"):
    assert o.ic_onroad_mode(AP1, s) == o.MODE_OFF
  toggle.write_text("1")
  assert o.ic_onroad_mode(AP1) == o.MODE_BRAKE
  assert o.ic_onroad_mode("TESLA_AP2_MODELS") == o.MODE_OFF


def G(**kw):
  a = dict(mode=o.MODE_BRAKE, in_drive=True, brake_pressed=True, op_enabled=False, cruise_enabled=False, v_ego=0.0)
  a.update(kw)
  return o.onroad_gate(**a)


def test_brake_gate():
  assert G()
  assert G(mode=o.MODE_BRAKE_ALL)
  for kw in (dict(mode=o.MODE_OFF), dict(in_drive=False), dict(brake_pressed=False), dict(op_enabled=True),
             dict(cruise_enabled=True), dict(v_ego=0.05), dict(v_ego=0.098), dict(v_ego=-0.2), dict(v_ego=2.0)):
    assert not G(**kw), kw


def test_lowspeed_clamped_to_panda(monkeypatch):
  assert o.PANDA_ALLOWS_MOVING_0x309 is False
  assert G(mode=o.MODE_LOWSPEED, brake_pressed=False)
  assert not G(mode=o.MODE_LOWSPEED, brake_pressed=False, v_ego=1.0)  # panda would drop it
  assert o.v_limit_ms(o.MODE_LOWSPEED) <= 0.1  # never above panda vehicle_moving (0.1 m/s)
  monkeypatch.setattr(o, "PANDA_ALLOWS_MOVING_0x309", True)
  assert G(mode=o.MODE_LOWSPEED, brake_pressed=False, v_ego=2.0)
  assert not G(mode=o.MODE_LOWSPEED, brake_pressed=False, v_ego=2.3)  # 5 mph
  assert not G(mode=o.MODE_LOWSPEED, op_enabled=True)
  assert not G(mode=o.MODE_BRAKE, v_ego=1.0)  # brake mode stays standstill


# ---- selection ----

def test_zones_and_nearest():
  tr = [(1, 8.0, 0.5, 0), (2, 5.0, -1.0, 0), (3, 4.0, 3.0, 0), (4, 9.0, 4.0, 0), (5, 7.0, -4.0, 0),
        (6, 30.0, 0.0, 0), (7, 5.0, 9.0, 0), (8, -1.0, 0.0, 0)]
  best = o.select_objects(tr)
  assert best[o.MUX_LEAD][0] == 2 and best[o.MUX_LEFT][0] == 3 and best[o.MUX_RIGHT][0] == 5
  assert set(best) == {o.MUX_LEAD, o.MUX_LEFT, o.MUX_RIGHT}
  assert o.select_objects(tr, lambda tid: tid in (1, 4)) == {o.MUX_LEAD: tr[0], o.MUX_LEFT: tr[3]}


def test_motion_filter_walker_vs_clutter():
  m = o.MotionFilter()
  for i in range(30):  # 3 s at 10 Hz
    t = i * 0.1
    jitter = 0.15 if i % 2 else -0.15
    walker_y = 4.0 - 1.3 * t   # crossing right to left at 1.3 m/s (4.3 ft/s), radar +left
    m.update(t, [(10, 6.0 + jitter, 2.0 + jitter, 0.1), (11, 6.0, walker_y, 0.0), (12, 12.0, -3.0, 1.0)])
  assert not m.moving(10, 2.9)  # jittering clutter
  assert m.moving(11, 2.9)      # lateral walker, vRel ~0
  assert m.moving(12, 2.9)      # radar Doppler
  m2 = o.MotionFilter()
  m2.update(0.0, [(13, 8.0, -5.0, -0.8)])  # one Doppler spike on a new track (seen on an AP1 stop)
  m2.update(0.1, [(13, 8.0, -5.0, 0.0)])
  assert not m2.moving(13, 0.1)
  m.update(3.0, [(10, 6.0, 2.0, 0.0)])
  assert m.moving(11, 3.0)      # held after it drops out
  m.update(5.5, [(10, 6.0, 2.0, 0.0)])
  assert not m.moving(11, 5.5)


# ---- IcOnRoad end to end ----

def _walker(t_s):
  """Walker 4 m (13 ft) ahead, crossing from 4 m right to 4 m left at 1.3 m/s, then gone."""
  y = -4.0 + 1.3 * t_s
  return [(42, 4.0, y, 0.0)] if y <= 4.5 else []


def _run(h, steps, gate=None, tracks_fn=_walker, t_tracks=10, valid=True):
  """100 Hz steps; liveTracks every t_tracks steps (10 Hz, the AP1 rlog rate)."""
  logs, out = [], []
  h._log_fn = lambda n, kw: logs.append((n, kw))
  tr, tns = None, None
  for i in range(steps):
    now = 1_000 * MS + i * DT
    if i % t_tracks == 0:
      tr, tns = tracks_fn(i * DT / 1e9), now
    g = dict(in_drive=True, brake_pressed=True, op_enabled=False, cruise_enabled=False, v_ego=0.0)
    if gate:
      g.update(gate(i))
    out.append(h.update(now_nanos=now, tracks=tr, tracks_mono_ns=tns, tracks_valid=valid, **g) if h.poll() else [])
  return out, logs


def test_inert_without_toggle(toggle):
  out, logs = _run(o.IcOnRoad(AP1), 500)
  assert all(f == [] for f in out) and logs == []


@pytest.mark.parametrize("kw", [dict(in_drive=False), dict(brake_pressed=False), dict(op_enabled=True),
                                dict(cruise_enabled=True), dict(v_ego=0.5)])
def test_gates_block(toggle, kw):
  toggle.write_text("1")
  out, _ = _run(o.IcOnRoad(AP1), 500, gate=lambda i: kw)
  assert all(f == [] for f in out)


def test_walker_moves_left_to_right_across_slots(toggle):
  toggle.write_text("1")
  out, logs = _run(o.IcOnRoad(AP1), 800)
  sent = [f[0] for f in out if f]
  assert len(sent) == 800 // 3 + 1  # every 3rd step, ~33 Hz, CUTIN/HEADINGS too
  assert all(a == 0x309 and len(b) == 8 for a, b in sent)
  muxes = [d.decode_frame(b)["mux"] for _, b in sent]
  assert muxes[:5] == list(d.MUX_ORDER)
  ev = [kw for n, kw in logs if n == "ic_onroad"]
  slots = []
  for kw in ev:
    if not slots or slots[-1] != kw["slot"]:
      slots.append(kw["slot"])
  # Walker starts on the car's right, crosses the front, ends on the left.
  assert slots == ["right", "lead", "left"]
  for kw in ev:
    v = d.decode_frame(bytes.fromhex(kw["dat"]))
    assert v["Type"] == 5 and v["mux"] == kw["mux"]
    assert abs(v["Dy"] + kw["y_rel_m"]) <= 0.18  # Dy = -yRel
    assert v["Dx"] == 4.0 and kw["track_id"] == 42 and kw["age_s"] <= o.TRACKS_STALE_S
    assert (v["Dy"] > 0) == (kw["slot"] == "right") or kw["slot"] == "lead"
  gates = [kw for n, kw in logs if n == "ic_onroad_gate"]
  assert gates[0]["open"] is True


def test_clutter_not_drawn_in_brake_mode_but_drawn_in_brake_all(toggle):
  clutter = lambda t: [(5, 6.0, 0.2, 0.0)]  # noqa: E731
  toggle.write_text("1")
  _, logs = _run(o.IcOnRoad(AP1), 500, tracks_fn=clutter)
  assert not [1 for n, _ in logs if n == "ic_onroad"]
  toggle.write_text("brake_all")
  _, logs = _run(o.IcOnRoad(AP1), 500, tracks_fn=clutter)
  assert {kw["slot"] for n, kw in logs if n == "ic_onroad"} == {"lead"}


def test_stale_or_invalid_tracks_send_sna(toggle):
  toggle.write_text("brake_all")
  only_first = lambda t: [(5, 6.0, 0.0, 0.0)]  # noqa: E731
  # Tracks published once at t=0 then never again: after 0.5 s only empty slots.
  out, logs = _run(o.IcOnRoad(AP1), 300, tracks_fn=only_first, t_tracks=10_000)
  ev = [kw for n, kw in logs if n == "ic_onroad"]
  assert ev and max(kw["age_s"] for kw in ev) <= o.TRACKS_STALE_S
  late = [f[0][1] for i, f in enumerate(out) if f and i > 60]
  assert late and all(d.decode_frame(b).get("Type", 0) == 0 for b in late)
  _, logs = _run(o.IcOnRoad(AP1), 300, tracks_fn=only_first, valid=False)
  assert not [1 for n, _ in logs if n == "ic_onroad"]


def test_brake_release_closes_gate_and_logs(toggle):
  toggle.write_text("1")
  out, logs = _run(o.IcOnRoad(AP1), 600, gate=lambda i: dict(brake_pressed=i < 300))
  assert all(f == [] for f in out[300:])
  gates = [kw for n, kw in logs if n == "ic_onroad_gate"]
  assert [g["open"] for g in gates] == [True, False]


def test_onroad_rate_never_exceeds_stock(toggle):
  toggle.write_text("1")
  out, _ = _run(o.IcOnRoad(AP1), 1000)
  assert sum(len(f) for f in out) <= 1000 // 3 + 1


# ---- carcontroller ----

def _cc_cs(enabled, gear, v_ego=0.0, brake=True):
  from cereal import car
  from collections import deque
  CC = car.CarControl.new_message()
  CC.enabled = enabled
  CC.latActive = enabled
  CC.longActive = enabled
  CS = SimpleNamespace(
    out=SimpleNamespace(steeringAngleDeg=0.0, vEgo=v_ego, gearShifter=gear, brakePressed=brake,
                        cruiseState=SimpleNamespace(enabled=False)),
    steer_warning="EAC_ERROR_IDLE", hands_on_level=0, eac_fault=False, eac_status="EAC_ACTIVE", acc_state=0,
    das_control_counters=deque([0]), msg_stw_actn_req={}, cluster_stock={},
  )
  return CC.as_reader(), CS


def _drive(cc_mod, value, gear, toggle, steps=60, enabled=False, brake=True, tracks=None):
  toggle.write_text(value)
  ctl = cc_mod.CarController("tesla_can", SimpleNamespace(carFingerprint=AP1, openpilotLongitudinalControl=True), None)
  if tracks is not None:
    ctl._live_tracks = lambda: (tracks, ctl.frame * DT, True)
  out = []
  for i in range(steps):
    CC, CS = _cc_cs(enabled, gear, brake=brake)
    _, can = ctl.update(CC, CS, i * DT, None)
    out.append(can)
  return ctl, out


def test_carcontroller_drive_brake_appends_only_0x309(tesla_modules, toggle):  # noqa: F811
  from cereal import car
  _, cc = tesla_modules
  G_ = car.CarState.GearShifter
  tr = [(7, 5.0, 0.0, 0.0)]
  _, on = _drive(cc, "brake_all", G_.drive, toggle, tracks=tr)
  _, off = _drive(cc, "0", G_.drive, toggle, tracks=tr)
  n, peds = 0, 0
  for a, b in zip(on, off, strict=True):
    extra = [m for m in a if m[0] == 0x309]
    assert [m for m in a if m[0] != 0x309] == b  # every other frame byte-identical
    assert all(m[3] == 0 and len(m[2]) == 8 for m in extra)
    n += len(extra)
    peds += sum(1 for m in extra if d.decode_frame(m[2]).get("Type") == 5)
  assert n == 20 and peds == 4  # 4 LEAD mux frames in 60 steps
  for kw in (dict(gear=G_.park), dict(gear=G_.reverse), dict(gear=G_.neutral), dict(brake=False),
             dict(enabled=True)):
    a = dict(gear=G_.drive)
    a.update(kw)
    _, out = _drive(cc, "brake_all", a.pop("gear"), toggle, tracks=tr, **a)
    assert not [m for c_ in out for m in c_ if m[0] == 0x309], kw


def test_carcontroller_failure_disables_and_keeps_other_frames(tesla_modules, toggle, monkeypatch):  # noqa: F811
  from cereal import car
  _, cc = tesla_modules
  ctl, _ = _drive(cc, "1", car.CarState.GearShifter.drive, toggle, steps=1, tracks=[])
  monkeypatch.setattr(ctl.ic_onroad, "update", lambda *a, **k: 1 / 0)
  CC, CS = _cc_cs(False, car.CarState.GearShifter.drive)
  _, can = ctl.update(CC, CS, 0, None)
  assert ctl.ic_onroad is None
  assert not [m for m in can if m[0] == 0x309]
  ref, _ = _drive(cc, "0", car.CarState.GearShifter.drive, toggle, steps=1)
  _, can_ref = ref.update(CC, CS, 0, None)
  assert can == can_ref  # other frames unchanged


def test_carcontroller_without_messaging_sends_sna_only(tesla_modules, toggle):  # noqa: F811
  from cereal import car
  _, cc = tesla_modules
  _, out = _drive(cc, "brake_all", car.CarState.GearShifter.drive, toggle)
  frames = [m for c_ in out for m in c_ if m[0] == 0x309]
  assert frames and all(d.decode_frame(m[2]).get("Type", 0) == 0 for m in frames)


# ---- panda (libpanda, existing branch safety; no new panda change) ----

@pytest.fixture
def lp():
  from panda import Panda
  from panda.tests.libpanda import libpanda_py
  l = libpanda_py.libpanda
  l.set_timer(1000)
  assert l.set_safety_hooks(Panda.SAFETY_TESLA, Panda.FLAG_TESLA_AP1 | Panda.FLAG_TESLA_LONG_CONTROL) == 0
  l.set_controls_allowed(False)
  return SimpleNamespace(l=l, py=libpanda_py)


def _speed(lp, mph):
  raw = int(round((mph + 25) / 0.05))
  dat = bytearray(6)
  dat[2] = raw & 0xFF
  dat[3] = (raw >> 8) & 0x0F
  assert lp.l.safety_rx_hook(lp.py.make_CANPacket(0x118, 0, bytes(dat)))


def test_panda_allows_onroad_frame_at_standstill_only(lp):
  dat = d.build_frame(d.MUX_LEAD, o.pedestrian_raws(42, 4.0, 0.0, 0.0))
  tx = lambda: bool(lp.l.safety_tx_hook(lp.py.make_CANPacket(0x309, 0, dat)))  # noqa: E731
  _speed(lp, 0.0)
  assert tx()
  assert lp.l.safety_fwd_hook(2, 0x309) == -1
  _speed(lp, 1.0)   # 1 mph: the panda drops it, so lowspeed stays clamped
  assert not tx()
  _speed(lp, 0.0)
  lp.l.set_controls_allowed(True)
  assert not tx()
