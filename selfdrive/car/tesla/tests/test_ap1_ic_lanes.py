"""ICLanes test toggle (ic_lanes.py): unscaled lane-line 0x239 path, snapping."""

from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.selfdrive.car.tesla.tests.test_ap1_cluster import (  # noqa: F401  (fixture)
  NS, _cc_inputs, _cp, _send, hud, stock, tesla_modules,
)
from openpilot.selfdrive.car.tesla import cluster as c
from openpilot.selfdrive.car.tesla import ic_lanes as L
from openpilot.selfdrive.car.tesla.values import CAR

AP1 = CAR.TESLA_AP1_MODELS


@pytest.fixture
def toggle(tmp_path, monkeypatch):
  p = tmp_path / "ICLanes"
  monkeypatch.setattr(L, "_iclanes_path", p)
  return p


def _line(xs, f):
  return SimpleNamespace(x=list(xs), y=[f(x) for x in xs])


def model(center=lambda x: 0.0, probs=(0.9, 0.9, 0.9, 0.9), width=3.3, path=None, n=33):
  xs = np.linspace(0.0, 100.0, n)
  lines = [_line(xs, lambda x, k=k: center(x) + k * width) for k in (-1.5, -0.5, 0.5, 1.5)]
  p = path or center
  return SimpleNamespace(laneLines=lines, laneLineProbs=list(probs),
                         position=SimpleNamespace(x=list(xs[1:]), y=[p(x) for x in xs[1:]]))


def test_default_off_and_ap1_only(toggle):
  assert L.ic_lanes_mode(AP1) == L.MODE_OFF
  for word, mode in (("1", L.MODE_LANES), ("lanes\n", L.MODE_LANES), ("SNAP", L.MODE_SNAP),
                     ("0", L.MODE_OFF), ("junk", L.MODE_OFF)):
    toggle.write_text(word)
    assert L.ic_lanes_mode(AP1) == mode
  toggle.write_text("1")
  assert L.ic_lanes_mode("TESLA_AP2_MODELS") == L.MODE_OFF


def test_lane_fit_unscaled_keeps_offset_and_sign():
  # Lane center 0.3 m right of the car, curving right: y = 0.3 + 0.0005 x^2.
  m = model(center=lambda x: 0.3 + 0.0005 * x * x)
  p, src = L.corrected_lane_poly(m, 0.0)
  assert src == L.SRC_LANES
  assert p.c0 == pytest.approx(0.3, abs=1e-3)
  assert p.c1 == pytest.approx(0.0, abs=1e-4)
  assert p.c2 == pytest.approx(0.0005, abs=1e-6)  # NOT 4x (no 2^n scale)
  assert p.view_range_m == 100


def test_lane_fit_clipped_to_dbc():
  p, _ = L.corrected_lane_poly(model(center=lambda x: 9.0 + 0.01 * x * x), 0.0)
  assert p.c0 == c.C0_RANGE[1] and p.c2 == c.C2_RANGE[1]


def test_low_probs_still_use_lane_lines_by_default():
  p, src = L.corrected_lane_poly(model(center=lambda x: 0.5, probs=(0.1, 0.05, 0.05, 0.1)), 0.0)
  assert src == L.SRC_LANES and p.c0 == pytest.approx(0.5, abs=1e-3)


def test_missing_lines_fall_back_to_unscaled_path_then_curvature(monkeypatch):
  monkeypatch.setattr(L, "LANE_PROB_MIN", 0.5)  # optional gate
  m = model(center=lambda x: 2.0, probs=(0.9, 0.2, 0.9, 0.9), path=lambda x: 0.0002 * x * x)
  p, src = L.corrected_lane_poly(m, 0.0)
  assert src == L.SRC_PATH
  assert (p.c0, p.c1, p.c3) == (0.0, 0.0, 0.0)
  assert p.c2 == pytest.approx(0.0002, abs=1e-6)
  assert c.path_from_model_v2(m).c2 == pytest.approx(0.0008, abs=1e-6)  # shipped: 4x
  m.laneLines = []
  monkeypatch.setattr(L, "LANE_PROB_MIN", 0.0)
  assert L.corrected_lane_poly(m, 0.0)[1] == L.SRC_PATH
  p, src = L.corrected_lane_poly(None, 0.0004)
  assert src == L.SRC_CURV and p.c2 == pytest.approx(0.0002) and p.view_range_m == 50


def test_lane_poly_sent_as_is_in_0x239_and_stock_fields_kept():
  lanes = stock(0x239)
  poly = c.ModelPath(c0=0.4, c1=-0.01, c2=0.0006, c3=1e-6, view_range_m=70)
  v = c.unpack(0x239, _send(c.ClusterController(), hud(lane_poly=poly), {0x239: lanes}, NS)[0x239])
  assert v["DAS_virtualLaneC0"] == pytest.approx(0.4, abs=0.035)
  assert v["DAS_virtualLaneC1"] == pytest.approx(-0.01, abs=0.0016)
  assert v["DAS_virtualLaneC2"] == pytest.approx(0.0006, abs=2e-5)
  assert v["DAS_virtualLaneC3"] == pytest.approx(1e-6, abs=2.4e-7)
  assert v["DAS_virtualLaneViewRange"] == 70
  for k in ("DAS_virtualLaneWidth", "DAS_leftLineUsage", "DAS_rightLineUsage", "DAS_leftLaneExists"):
    assert v[k] == pytest.approx(lanes[k], abs=0.32)


def test_snap_lane_index_and_center():
  C = [0.2, 0.0, 0.0005, 0.0]
  W = 3.25
  cy = L.poly_y(C, 40.0)
  assert L.snap_dy(40.0, cy + 0.9, C, W) == (pytest.approx(cy), 0)
  assert L.snap_dy(40.0, cy + 2.8, C, W) == (pytest.approx(cy + W), 1)   # right lane (+ right)
  assert L.snap_dy(40.0, cy - 3.6, C, W) == (pytest.approx(cy - W), -1)
  assert L.snap_dy(40.0, cy + 6.0, C, W) == (pytest.approx(cy + 6.0), None)  # 2 lanes over: untouched


def test_log_1hz_and_lead_shadow_uses_minus_yrel(toggle):
  toggle.write_text("snap")
  logs = []
  il = L.IcLanes(AP1, log_fn=lambda n, kw: logs.append(kw))
  il.poll()
  poly = c.ModelPath(0.0, 0.0, 0.0, 0.0, 50)
  lead = SimpleNamespace(status=True, dRel=30.0, yRel=3.0, radar=True)  # 3 m LEFT
  stock_l = {"DAS_virtualLaneWidth": 3.25, **{f"DAS_virtualLaneC{i}": 0.0 for i in range(4)}}
  for i in range(250):
    il.maybe_log(i * 10_000_000, poly, L.SRC_LANES, stock_l, None, lead)
  assert len(logs) == 3
  r = logs[0]
  assert r["lead"]["dy_raw"] == -3.0 and r["lead"]["lane"] == -1
  assert r["lead"]["dy_snapped"] == pytest.approx(-3.25)
  assert r["stock"] == [0.0] * 4 and r["source"] == L.SRC_LANES


def _ctl(cc_mod):
  ctl = cc_mod.CarController("tesla_can", _cp(AP1), None)
  ctl._model_sm = False
  ctl._lanes_sm = False
  return ctl


def _sent_c2(ctl, enabled=True):
  CC, CS = _cc_inputs(enabled, [1], {0x239: stock(0x239)})
  _, can = ctl.update(CC, CS, 0, None)
  lanes = [m for m in can if m[0] == 0x239]
  return c.unpack(0x239, lanes[0][2]) if lanes else None


def test_carcontroller_off_is_shipped_mapping(tesla_modules, toggle):  # noqa: F811
  _, cc = tesla_modules
  v = _sent_c2(_ctl(cc))
  assert v["DAS_virtualLaneC2"] == pytest.approx(0.001 / 2 * 4, abs=2e-5)  # curvature 0.001, shipped 4x


def test_carcontroller_on_sends_unscaled(tesla_modules, toggle):  # noqa: F811
  _, cc = tesla_modules
  toggle.write_text("1")
  v = _sent_c2(_ctl(cc))
  assert v["DAS_virtualLaneC2"] == pytest.approx(0.001 / 2, abs=2e-5)  # curvature fallback, 1x


def test_carcontroller_on_uses_lane_lines(tesla_modules, toggle, monkeypatch):  # noqa: F811
  _, cc = tesla_modules
  toggle.write_text("snap")
  ctl = _ctl(cc)
  m = model(center=lambda x: 0.25 + 0.0003 * x * x)
  monkeypatch.setattr(ctl, "_latest_model_v2", lambda: m)
  v = _sent_c2(ctl)
  assert v["DAS_virtualLaneC0"] == pytest.approx(0.25, abs=0.035)
  assert v["DAS_virtualLaneC2"] == pytest.approx(0.0003, abs=2e-5)


def test_carcontroller_failure_disables_ic_lanes_only(tesla_modules, toggle, monkeypatch):  # noqa: F811
  _, cc = tesla_modules
  toggle.write_text("1")
  ctl = _ctl(cc)
  monkeypatch.setattr(ctl.ic_lanes, "poly", lambda *a: 1 / 0)
  v = _sent_c2(ctl)
  assert ctl.ic_lanes is None and ctl.cluster is not None
  assert v["DAS_virtualLaneC2"] == pytest.approx(0.002, abs=2e-5)  # shipped fallback
