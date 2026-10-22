"""AP1 cluster lane placement fix (test branch). Display only.

Off unless /data/params_bogpilot/ICLanes holds one of:
  1 / lanes   Engaged 0x239 DAS_lanes path from openpilot's lane lines in the
              cluster's own units (see "Mapping" below).
  snap        Same, plus a shadow log of where openpilot's lead would be drawn
              snapped to the center of the ego / left / right lane. Nothing is
              sent for objects: 0x309 is dropped by the branch panda whenever
              the car moves or openpilot is engaged.
Off (file absent / anything else): the shipped cluster.py mapping, unchanged.

Why. While engaged the cluster draws openpilot's 0x239 lane path but stock
Mobileye 0x309 objects. The shipped path was C0 = C1 = 0, C2 = 4 * model path
C2 (Tinkla IC_LANE_SCALE = 0.5 -> C_n * 2^n). On AP1 rlogs (engaged, stock
0x309 LEAD-mux objects 3..100 m) 19.5 % of ego-lane objects fell outside the
sent lane, vs 6.0 % against stock 0x239. The 2^n scale alone takes that to
4.0 %; the full lane-line fit below gives 3.0 %. Stock 0x239 coefficients
evaluated with the same 2^n scale put 29 % of stock objects outside, so the
cluster reads C0..C3 in plain meters (x and y in m, like DAS Dy / Dx).

Mapping (all in the DAS frame: x forward m, y + RIGHT m, same as DAS Dy):
  - modelV2.laneLines / position y is + RIGHT (left near line ~ -1.5 m), the
    same sign as stock DAS_virtualLaneC0..C2 (stock C0 = 0.81 * model lane
    center at x=0, stock C2 rises with model path C2). No sign flip.
  - openpilot lead / radar yRel is + LEFT, so DAS Dy = -yRel.
  - Lane center = (laneLines[1] + laneLines[2]) / 2, cubic least squares over
    0..60 m. C0..C3 unscaled, clipped to the DBC ranges. Width stays stock
    DAS_virtualLaneWidth (median 3.25 m on both). No laneLineProbs gate by
    default: in replay the lane fit beat the path fallback at every prob
    (both near probs < 0.1: 4.5 % vs 14.5 % outside) and its 50 m jitter
    stayed under stock's. LANE_PROB_MIN is kept for an on-road A/B.
  - Fallback 1 (lane lines missing / short / gated): model path through the
    origin, C2 unscaled, C0 = C1 = 0.
  - Fallback 2: actuator curvature, C2 = curvature / 2, unscaled, 50 m.
  The shipped free path fit that traded C0 against C1 (DIVERGENCES.md) fit
  the planned path. This fits the lane lines; C0 / C1 spread matches stock
  (C0 p5..p95 -0.30..0.41 m vs stock -0.32..0.46 m) and frame-to-frame
  jitter at 50 m is below stock (p95 0.25 vs 0.40 m, good lines).

Logged (cloudlog "ic_lanes", 1 Hz while engaged and on): source, sent C0..C3,
stock C0..C3 and width, near-line probs; in snap mode also the lead raw
dRel / yRel / Dy and its snapped Dy and lane index. Raw stock 0x309 / 0x239
(can src 2) and the sent 0x239 (sendcan) are in the rlog already.

Not a product, no warranty, driver remains responsible.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from openpilot.selfdrive.car.tesla.cluster import (
  C0_RANGE, C1_RANGE, C2_RANGE, C3_RANGE, LANE_VIEW_RANGE_M, MODEL_C2_FIT_M, MODEL_FIT_MAX_M, MODEL_PATH_MIN_M,
  ModelPath, clamp_view_range_m,
)

ICLANES_PATH = Path("/data/params_bogpilot/ICLanes")
# Tests override this module attribute.
_iclanes_path = ICLANES_PATH

MODE_OFF = "off"
MODE_LANES = "lanes"
MODE_SNAP = "snap"
_MODE_WORDS = {"1": MODE_LANES, "true": MODE_LANES, "on": MODE_LANES, "lanes": MODE_LANES, "snap": MODE_SNAP}

LANE_FIT_MAX_M = 60.0
LANE_PROB_MIN = 0.0
LEFT_NEAR, RIGHT_NEAR = 1, 2
# Snap: objects beyond 1.5 lane widths from the ego center are left alone.
SNAP_MAX_LANES = 1
LOG_PERIOD_NS = 1_000_000_000  # 1 Hz
TOGGLE_POLL_FRAMES = 100

SRC_LANES = "lanes"
SRC_PATH = "path"
SRC_CURV = "curvature"


def ic_lanes_mode(fingerprint: Optional[str], stored=None) -> str:
  """AP1 only. MODE_OFF unless the file (or `stored`) names a mode."""
  from openpilot.selfdrive.car.tesla.values import CAR
  if fingerprint != CAR.TESLA_AP1_MODELS:
    return MODE_OFF
  if stored is None:
    try:
      stored = _iclanes_path.read_text(encoding="utf-8", errors="replace") if _iclanes_path.is_file() else ""
    except OSError:
      stored = ""
  if isinstance(stored, bytes):
    stored = stored.decode(errors="replace")
  return _MODE_WORDS.get(stored.strip().lower(), MODE_OFF)


def _clip(v, lo_hi):
  return float(min(max(float(v), lo_hi[0]), lo_hi[1]))


def poly_y(c, x):
  """DAS lane center y (+ right, m) at x m."""
  return c[0] + c[1] * x + c[2] * x * x + c[3] * x ** 3


def _view_range(model_v2) -> Optional[float]:
  try:
    xs = np.asarray(model_v2.position.x, dtype=float)
  except Exception:
    return None
  xs = xs[np.isfinite(xs) & (xs > 0.5)]
  if xs.size < 4:
    return None
  v = min(float(xs[-1]), MODEL_FIT_MAX_M)
  return v if v >= MODEL_PATH_MIN_M else None


def lane_poly_from_model_v2(model_v2) -> Optional[ModelPath]:
  """Lane-center cubic from the two near lane lines, DAS units, unscaled."""
  try:
    lines = model_v2.laneLines
    probs = list(model_v2.laneLineProbs)
    if len(lines) <= RIGHT_NEAR or len(probs) <= RIGHT_NEAR:
      return None
    if probs[LEFT_NEAR] < LANE_PROB_MIN or probs[RIGHT_NEAR] < LANE_PROB_MIN:
      return None
    x = np.asarray(lines[LEFT_NEAR].x, dtype=float)
    yl = np.asarray(lines[LEFT_NEAR].y, dtype=float)
    yr = np.asarray(lines[RIGHT_NEAR].y, dtype=float)
  except Exception:
    return None
  if not (x.size == yl.size == yr.size) or x.size < 6:
    return None
  y = (yl + yr) / 2.0
  m = np.isfinite(x) & np.isfinite(y) & (x >= 0.0) & (x <= LANE_FIT_MAX_M)
  if int(m.sum()) < 6:
    return None
  c = np.polyfit(x[m], y[m], 3)[::-1]
  if not np.all(np.isfinite(c)):
    return None
  view = _view_range(model_v2)
  if view is None:
    view = float(LANE_VIEW_RANGE_M)
  return ModelPath(c0=_clip(c[0], C0_RANGE), c1=_clip(c[1], C1_RANGE), c2=_clip(c[2], C2_RANGE),
                   c3=_clip(c[3], C3_RANGE), view_range_m=float(clamp_view_range_m(view)))


def path_poly_unscaled(model_v2) -> Optional[ModelPath]:
  """Shipped through-origin path C2 fit, without the 2^n scale."""
  try:
    xs = np.asarray(model_v2.position.x, dtype=float)
    ys = np.asarray(model_v2.position.y, dtype=float)
  except Exception:
    return None
  if xs.size < 4 or xs.size != ys.size:
    return None
  view = _view_range(model_v2)
  if view is None:
    return None
  n = np.isfinite(xs) & np.isfinite(ys) & (xs > 0.5) & (xs <= MODEL_C2_FIT_M)
  if int(n.sum()) < 4:
    return None
  den = float(np.sum(xs[n] ** 4))
  if den <= 0.0:
    return None
  c2 = float(np.sum(xs[n] ** 2 * ys[n]) / den)
  if not np.isfinite(c2):
    return None
  return ModelPath(0.0, 0.0, _clip(c2, C2_RANGE), 0.0, float(clamp_view_range_m(view)))


def corrected_lane_poly(model_v2, curvature: float):
  """(ModelPath, source). Lane lines, else path, else actuator curvature."""
  if model_v2 is not None:
    p = lane_poly_from_model_v2(model_v2)
    if p is not None:
      return p, SRC_LANES
    p = path_poly_unscaled(model_v2)
    if p is not None:
      return p, SRC_PATH
  return ModelPath(0.0, 0.0, _clip(float(curvature) / 2.0, C2_RANGE), 0.0, float(LANE_VIEW_RANGE_M)), SRC_CURV


def lane_index(dx, dy, c, width) -> Optional[int]:
  """-1 left lane, 0 ego, +1 right lane (DAS Dy + right). None when farther."""
  k = int(round((float(dy) - poly_y(c, dx)) / float(width)))
  return k if abs(k) <= SNAP_MAX_LANES else None


def snap_dy(dx, dy, c, width):
  """(snapped Dy, lane index). Object drawn at the center of its lane."""
  k = lane_index(dx, dy, c, width)
  if k is None:
    return float(dy), None
  return float(poly_y(c, dx) + k * width), k


def _coefs(p):
  return [p.c0, p.c1, p.c2, p.c3]


class IcLanes:
  """Toggle state, corrected poly and the 1 Hz raw-vs-sent log."""

  def __init__(self, fingerprint: str, log_fn=None):
    self.fingerprint = fingerprint
    self.mode = MODE_OFF
    self.frame = 0
    self._log_fn = log_fn
    self._last_log_ns = None

  def poll(self) -> str:
    if self.frame % TOGGLE_POLL_FRAMES == 0:
      self.mode = ic_lanes_mode(self.fingerprint)
    self.frame += 1
    return self.mode

  @property
  def on(self) -> bool:
    return self.mode != MODE_OFF

  def poly(self, model_v2, curvature):
    return corrected_lane_poly(model_v2, curvature)

  def lead_record(self, lead, poly, width):
    """Shadow placement of openpilot's lead (radarState.leadOne)."""
    if lead is None or not bool(getattr(lead, "status", False)):
      return None
    d_rel, y_rel = float(lead.dRel), float(lead.yRel)
    dy = -y_rel  # yRel + left, DAS Dy + right
    snapped, k = snap_dy(d_rel, dy, _coefs(poly), width)
    return {"dRel": round(d_rel, 2), "yRel": round(y_rel, 2), "dy_raw": round(dy, 2),
            "dy_snapped": round(snapped, 2), "lane": k, "radar": bool(getattr(lead, "radar", False))}

  def maybe_log(self, now_nanos, poly, source, stock_lanes=None, model_v2=None, lead=None):
    if self._last_log_ns is not None and 0 <= now_nanos - self._last_log_ns < LOG_PERIOD_NS:
      return None
    self._last_log_ns = int(now_nanos)
    width = float(stock_lanes.get("DAS_virtualLaneWidth", 3.25)) if stock_lanes else 3.25
    rec = {"mono_ns": int(now_nanos), "mode": self.mode, "source": source,
           "sent": [round(v, 6) for v in _coefs(poly)], "view_m": poly.view_range_m, "width": width}
    if stock_lanes:
      rec["stock"] = [round(float(stock_lanes.get(f"DAS_virtualLaneC{i}", 0.0)), 6) for i in range(4)]
    try:
      if model_v2 is not None:
        rec["probs"] = [round(float(p), 3) for p in list(model_v2.laneLineProbs)[:4]]
    except Exception:
      pass
    if self.mode == MODE_SNAP:
      rec["lead"] = self.lead_record(lead, poly, width)
    self._log("ic_lanes", **rec)
    return rec

  def _log(self, name, **kw):
    if self._log_fn is not None:
      self._log_fn(name, kw)
      return
    from openpilot.common.swaglog import cloudlog
    cloudlog.event(name, **kw)
