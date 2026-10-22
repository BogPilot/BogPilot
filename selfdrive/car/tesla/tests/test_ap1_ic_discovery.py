"""Parked-only 0x309 IC discovery harness (ic_discovery.py)."""

import pytest

from openpilot.selfdrive.car.tesla.tests.test_ap1_cluster import tesla_modules  # noqa: F401  (fixture)

from openpilot.selfdrive.car.tesla import ic_discovery as d
from openpilot.selfdrive.car.tesla.values import CAR

NS = 1_000_000_000
AP1 = CAR.TESLA_AP1_MODELS


@pytest.fixture
def toggle(tmp_path, monkeypatch):
  p = tmp_path / "ICDiscovery"
  monkeypatch.setattr(d, "_icdiscovery_path", p)
  return p


def run(h, steps, t0=0, park=True, op=False, cruise=False, v=0.0, dt=10_000_000):
  out = []
  for i in range(steps):
    out.append(h.update(park, op, cruise, v, t0 + i * dt) if h.poll() else [])
  return out


def test_default_off_and_non_ap1_off(toggle):
  assert not d.ic_discovery_enabled(AP1)
  toggle.write_text("1")
  assert d.ic_discovery_enabled(AP1)
  assert not d.ic_discovery_enabled("TESLA_AP2_MODELS")
  for s in ("0", "off", "false", "", "yes"):
    assert not d.ic_discovery_enabled(AP1, s)


def test_inert_without_toggle(toggle):
  h = d.IcDiscovery(AP1)
  assert all(f == [] for f in run(h, 500))


@pytest.mark.parametrize("kw", [dict(park=False), dict(op=True), dict(cruise=True), dict(v=0.5)])
def test_gates_block(toggle, kw):
  toggle.write_text("1")
  h = d.IcDiscovery(AP1)
  assert all(f == [] for f in run(h, 500, **kw))


def test_parked_sends_mux_round_robin_at_stock_rate(toggle):
  toggle.write_text("1")
  h = d.IcDiscovery(AP1)
  sent = [f for f in run(h, 300) if f]
  assert len(sent) == 100  # every 3rd of 300 steps -> ~33 Hz
  muxes = [d.decode_frame(f[0][1])["mux"] for f in sent]
  assert muxes[:5] == list(d.MUX_ORDER)
  for f in sent:
    assert f[0][0] == 0x309 and len(f[0][1]) == 8


def test_type_cycles_every_step(toggle):
  toggle.write_text("1")
  h = d.IcDiscovery(AP1)
  seen = []
  for frames in run(h, 7 * 300 + 10):  # 7 steps of 3 s at 100 Hz
    for _, dat in frames:
      v = d.decode_frame(dat)
      if v["mux"] == d.MUX_LEAD:
        seen.append(int(v["Type"]))
  types = []
  for t in seen:
    if not types or types[-1] != t:
      types.append(t)
  assert types[:7] == [0, 1, 2, 3, 4, 5, 6]


def test_lead_frame_values_and_sna_slots():
  dat = d.build_frame(d.MUX_LEAD, d.object_raws(5, d.TEST_DX_M, 0.0))
  v = d.decode_frame(dat)
  assert v["mux"] == 0 and v["Type"] == 5 and v["Dx"] == 20.0
  assert abs(v["Dy"]) < 0.35 / 2 + 1e-9
  # Empty slots match stock AP1 empty decode (Dx 127.5 m, Dy -22.05, Id 127).
  e = d.decode_frame(d.build_frame(d.MUX_LEFT))
  assert e["Type"] == 0 and e["Dx"] == 127.5 and abs(e["Dy"] + 22.05) < 1e-9 and e["Id"] == 127
  h = int.from_bytes(d.build_frame(d.MUX_HEADINGS), "little")
  assert h & 0x7 == 5
  assert all(((h >> s) & 0xFF) == 128 for s in d.HEADING_STARTS)


def test_gate_drop_restarts_sequence(toggle):
  toggle.write_text("1")
  h = d.IcDiscovery(AP1)
  run(h, 1000)
  assert h.start_ns is not None
  assert h.poll()
  assert h.update(False, False, False, 0.0, 20 * NS) == []
  assert h.start_ns is None


def _cc_cs(enabled, gear, v_ego=0.0):
  from cereal import car
  from types import SimpleNamespace
  from collections import deque
  CC = car.CarControl.new_message()
  CC.enabled = enabled
  CC.latActive = enabled
  CC.longActive = enabled
  CS = SimpleNamespace(
    out=SimpleNamespace(steeringAngleDeg=0.0, vEgo=v_ego, gearShifter=gear,
                        cruiseState=SimpleNamespace(enabled=False)),
    steer_warning="EAC_ERROR_IDLE", hands_on_level=0, eac_fault=False, eac_status="EAC_ACTIVE", acc_state=0,
    das_control_counters=deque([0]), msg_stw_actn_req={}, cluster_stock={},
  )
  return CC.as_reader(), CS


def _drive(cc_mod, toggle_on, gear, toggle, steps=60, enabled=False):
  from types import SimpleNamespace
  toggle.write_text("1" if toggle_on else "0")
  ctl = cc_mod.CarController("tesla_can", SimpleNamespace(carFingerprint=AP1, openpilotLongitudinalControl=True), None)
  out = []
  for i in range(steps):
    CC, CS = _cc_cs(enabled, gear)
    _, can = ctl.update(CC, CS, i * 10_000_000, None)
    out.append(can)
  return ctl, out


def test_carcontroller_parked_only_appends_0x309(tesla_modules, toggle):  # noqa: F811
  from cereal import car
  _, cc = tesla_modules
  park, drive = car.CarState.GearShifter.park, car.CarState.GearShifter.drive
  _, on = _drive(cc, True, park, toggle)
  _, off = _drive(cc, False, park, toggle)
  n = 0
  for a, b in zip(on, off, strict=True):
    extra = [m for m in a if m[0] == 0x309]
    assert [m for m in a if m[0] != 0x309] == b
    assert all(m[3] == 0 and len(m[2]) == 8 for m in extra)
    n += len(extra)
  assert n == 20
  for gear in (drive, car.CarState.GearShifter.reverse, car.CarState.GearShifter.neutral):
    _, out = _drive(cc, True, gear, toggle)
    assert not [m for c_ in out for m in c_ if m[0] == 0x309]
  _, out = _drive(cc, True, park, toggle, enabled=True)
  assert not [m for c_ in out for m in c_ if m[0] == 0x309]


def test_carcontroller_harness_failure_keeps_other_frames(tesla_modules, toggle, monkeypatch):  # noqa: F811
  from cereal import car
  _, cc = tesla_modules
  ctl, _ = _drive(cc, True, car.CarState.GearShifter.park, toggle, steps=1)
  monkeypatch.setattr(ctl.ic_discovery, "update", lambda *a, **k: 1 / 0)
  CC, CS = _cc_cs(False, car.CarState.GearShifter.park)
  ctl.ic_discovery.frame = 0
  _, can = ctl.update(CC, CS, 0, None)
  assert ctl.ic_discovery is None
  assert not [m for m in can if m[0] == 0x309]
