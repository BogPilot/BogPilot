"""AP1 on-road 0x309 pedestrian display test. Display only. Test branch only.

Off unless /data/params_bogpilot/ICOnRoad holds one of:
  1 / brake   Drive + brake pressed + standstill. Moving radar tracks only.
  brake_all   Same gate. Nearest radar track in each zone, moving or not.
  lowspeed    Drive + below 5 mph (no brake needed). Moving tracks only.
              MARKED OPTION: the branch panda drops 0x309 whenever the car
              moves (> 0.1 m/s), so until an approved panda change this is
              clamped to standstill too (PANDA_ALLOWS_MOVING_0x309).
Every gate below must hold on the same step, or nothing is sent and the panda
forwards stock 0x309 again within its 100 ms substitution timeout:
  - AP1 fingerprint, gear Drive
  - openpilot not enabled (CC.enabled False), stock cruise not enabled
  - brake modes: brakePressed and vEgo below 0.05 m/s (0.16 ft/s)
  - lowspeed: vEgo below the clamp above
No actuator, lead, radarState or panda change. Radar tracks are read from
liveTracks (radard) only to draw icons; nothing here feeds control.

What it sends: the same 0x309 layout and round robin as ic_discovery.py
(0 LEAD, 1 LEFT, 2 RIGHT, 3 CUTIN, 5 HEADINGS, every 3rd control step). The
nearest qualifying track in each zone is drawn as type 5 PEDESTRIAN:
  LEAD  |y| <= 1.5 m (5 ft), LEFT y > 1.5 m, RIGHT y < -1.5 m,
  0 < dRel <= 20 m (66 ft), |y| <= 6 m (20 ft).
liveTracks has no radar class, so every drawn track is shown as a pedestrian.
"Moving" = radar |vRel| >= 0.7 m/s on 2 radar frames in a row, or the track moved >= 1.0 m (3 ft) over
the last 1.5 s; held for 2 s after it last qualified. Parked-and-stopped
radar clutter in AP1 rlogs (Drive + brake windows) moved <= 0.6 m p99.

Sign: openpilot / radar yRel is + LEFT. DAS Dy is + RIGHT (stock LEFT mux
Dy < 0, RIGHT mux Dy > 0 on AP1 rlogs), so Dy = -yRel.

Logged: cloudlog event "ic_onroad" for every frame that carries an object
(mono ns, mux, slot, trackId, dRel, yRel, vRel, Dx/Dy/Id raw + phys, track age,
mode) and "ic_onroad_gate" on every gate change. The bytes themselves are in
sendcan and in the panda echo (can src 128).
"""

from collections import deque
from pathlib import Path

from openpilot.selfdrive.car.tesla.ic_discovery import (
  DAS_OBJECT, IC_DISCOVERY_BUS, MUX_CUTIN, MUX_HEADINGS, MUX_LEAD, MUX_LEFT, MUX_ORDER, MUX_RIGHT, OBJ1,
  SEND_EVERY_N_FRAMES, build_frame, das_dy_from_yrel, to_raw,
)

IC_ONROAD_BUS = IC_DISCOVERY_BUS

ICONROAD_PATH = Path("/data/params_bogpilot/ICOnRoad")
# Tests override this module attribute.
_iconroad_path = ICONROAD_PATH

MODE_OFF = "off"
MODE_BRAKE = "brake"
MODE_BRAKE_ALL = "brake_all"
MODE_LOWSPEED = "lowspeed"
_MODE_WORDS = {
  "1": MODE_BRAKE, "true": MODE_BRAKE, "on": MODE_BRAKE, "brake": MODE_BRAKE,
  "brake_all": MODE_BRAKE_ALL, "lowspeed": MODE_LOWSPEED,
}

TYPE_PEDESTRIAN = 5
# 0.16 ft/s. Half the panda's vehicle_moving (0.1 m/s on 0x118): vEgo can read
# below 0.1 while 0x118 still reads above it, as the car settles.
V_STANDSTILL_MS = 0.05
LOWSPEED_MAX_MS = 2.2352     # 5 mph
# The branch panda (safety_tesla.h) rejects 0x309 while the car moves. Keep
# False until an approved panda change allows it; lowspeed then sends only
# at standstill and nothing is ever dropped by the panda.
PANDA_ALLOWS_MOVING_0x309 = False

MAX_DX_M = 20.0
MAX_ABS_Y_M = 6.0
LEAD_HALF_WIDTH_M = 1.5
TRACKS_STALE_S = 0.5
MOVING_VREL_MS = 0.7
MOVING_VREL_FRAMES = 2
MOVING_DISP_M = 1.0
MOVING_WINDOW_S = 1.5
MOVING_MIN_SPAN_S = 1.0
MOVING_MIN_SAMPLES = 5
MOVING_HOLD_S = 2.0
_SLOT = {MUX_LEAD: "lead", MUX_LEFT: "left", MUX_RIGHT: "right"}


def ic_onroad_mode(fingerprint: str | None, stored: str | bytes | None = None) -> str:
  """AP1 only. MODE_OFF unless the file (or `stored`) names a mode."""
  from openpilot.selfdrive.car.tesla.values import CAR
  if fingerprint != CAR.TESLA_AP1_MODELS:
    return MODE_OFF
  if stored is None:
    try:
      stored = _iconroad_path.read_text(encoding="utf-8", errors="replace") if _iconroad_path.is_file() else ""
    except OSError:
      stored = ""
  if isinstance(stored, bytes):
    stored = stored.decode(errors="replace")
  return _MODE_WORDS.get(stored.strip().lower(), MODE_OFF)


def v_limit_ms(mode: str) -> float:
  if mode == MODE_LOWSPEED and PANDA_ALLOWS_MOVING_0x309:
    return LOWSPEED_MAX_MS
  return V_STANDSTILL_MS


def onroad_gate(mode: str, in_drive: bool, brake_pressed: bool, op_enabled: bool, cruise_enabled: bool,
                v_ego: float) -> bool:
  if mode == MODE_OFF or not in_drive or op_enabled or cruise_enabled:
    return False
  if mode in (MODE_BRAKE, MODE_BRAKE_ALL) and not brake_pressed:
    return False
  return abs(v_ego) < v_limit_ms(mode)


def zone_of(d_rel: float, y_rel: float):
  """Mux for a track (openpilot yRel, + left), or None when outside the box."""
  if not (0.0 < d_rel <= MAX_DX_M) or abs(y_rel) > MAX_ABS_Y_M:
    return None
  if abs(y_rel) <= LEAD_HALF_WIDTH_M:
    return MUX_LEAD
  return MUX_LEFT if y_rel > 0 else MUX_RIGHT


class MotionFilter:
  """Per-trackId radar history -> is this track moving (walker) or clutter."""

  def __init__(self):
    self.hist = {}
    self.vrel_count = {}
    self.moving_until = {}

  def update(self, t_s: float, tracks):
    seen = set()
    for tid, d, y, v in tracks:
      seen.add(tid)
      h = self.hist.setdefault(tid, deque())
      h.append((t_s, d, y))
      while h and t_s - h[0][0] > MOVING_WINDOW_S:
        h.popleft()
      self.vrel_count[tid] = self.vrel_count.get(tid, 0) + 1 if abs(v) >= MOVING_VREL_MS else 0
      if self.vrel_count[tid] >= MOVING_VREL_FRAMES or self._displaced(h):
        self.moving_until[tid] = t_s + MOVING_HOLD_S
    for tid in list(self.hist):
      if tid not in seen:
        del self.hist[tid]
        self.vrel_count.pop(tid, None)
    for tid in list(self.moving_until):
      if tid not in seen and self.moving_until[tid] < t_s:
        del self.moving_until[tid]

  @staticmethod
  def _displaced(h) -> bool:
    if len(h) < MOVING_MIN_SAMPLES or h[-1][0] - h[0][0] < MOVING_MIN_SPAN_S:
      return False
    a, b = list(h)[:3], list(h)[-3:]
    dd = sum(x[1] for x in b) / 3 - sum(x[1] for x in a) / 3
    dy = sum(x[2] for x in b) / 3 - sum(x[2] for x in a) / 3
    return (dd * dd + dy * dy) ** 0.5 >= MOVING_DISP_M

  def moving(self, tid: int, t_s: float) -> bool:
    return self.moving_until.get(tid, -1.0) >= t_s

  def reset(self):
    self.hist.clear()
    self.vrel_count.clear()
    self.moving_until.clear()


def select_objects(tracks, moving=None):
  """{mux: (tid, dRel, yRel, vRel)} nearest (by dRel) per zone. `moving(tid)`
  filters when given."""
  best = {}
  for tr in tracks:
    tid, d, y, _v = tr
    mux = zone_of(d, y)
    if mux is None or (moving is not None and not moving(tid)):
      continue
    if mux not in best or d < best[mux][1]:
      best[mux] = tr
  return best


def pedestrian_raws(tid: int, d_rel: float, y_rel: float, v_rel: float) -> dict:
  return {
    "Type": TYPE_PEDESTRIAN, "Relevant": 0,
    "Dx": to_raw(OBJ1["Dx"], d_rel), "VxRel": to_raw(OBJ1["VxRel"], v_rel),
    "Dy": to_raw(OBJ1["Dy"], das_dy_from_yrel(y_rel)), "Id": int(tid) % 127,
  }


class IcOnRoad:
  """Per control step: poll(), then update() -> [(addr, bytes)] or []."""

  TOGGLE_POLL_FRAMES = 100

  def __init__(self, fingerprint: str, log_fn=None):
    self.fingerprint = fingerprint
    self.mode = MODE_OFF
    self.frame = 0
    self.mux_idx = 0
    self.gate_open = False
    self.motion = MotionFilter()
    self.last_tracks_ns = None
    self._tracks = []
    self._log_fn = log_fn

  def poll(self) -> bool:
    if self.frame % self.TOGGLE_POLL_FRAMES == 0:
      self.mode = ic_onroad_mode(self.fingerprint)
    self.frame += 1
    if self.mode == MODE_OFF:
      self._close(None, "toggle off")
    return self.mode != MODE_OFF

  def _close(self, now_nanos, reason):
    if self.gate_open:
      self._log("ic_onroad_gate", open=False, reason=reason, mono_ns=now_nanos, mode=self.mode)
    self.gate_open = False
    self.mux_idx = 0
    self.motion.reset()
    self.last_tracks_ns = None
    self._tracks = []

  def update(self, in_drive: bool, brake_pressed: bool, op_enabled: bool, cruise_enabled: bool, v_ego: float,
             now_nanos: int, tracks=None, tracks_mono_ns=None, tracks_valid=True):
    """tracks: iterable of (trackId, dRel, yRel, vRel) from the newest
    liveTracks, or None when none arrived this step. tracks_mono_ns is that
    message's logMonoTime."""
    if not onroad_gate(self.mode, in_drive, brake_pressed, op_enabled, cruise_enabled, v_ego):
      self._close(now_nanos, "gate")
      return []
    if not self.gate_open:
      self.gate_open = True
      self._log("ic_onroad_gate", open=True, mono_ns=now_nanos, mode=self.mode, v_ego=float(v_ego))

    if tracks is not None and tracks_mono_ns is not None and tracks_mono_ns != self.last_tracks_ns:
      self.last_tracks_ns = tracks_mono_ns
      self._tracks = [(int(t[0]), float(t[1]), float(t[2]), float(t[3])) for t in tracks] if tracks_valid else []
      self.motion.update(tracks_mono_ns / 1e9, self._tracks)

    if (self.frame - 1) % SEND_EVERY_N_FRAMES != 0:
      return []
    mux = MUX_ORDER[self.mux_idx]
    self.mux_idx = (self.mux_idx + 1) % len(MUX_ORDER)
    if mux in (MUX_CUTIN, MUX_HEADINGS):
      return [(DAS_OBJECT, build_frame(mux))]

    obj = None
    age_s = None if self.last_tracks_ns is None else (now_nanos - self.last_tracks_ns) / 1e9
    if age_s is not None and age_s <= TRACKS_STALE_S:
      t_s = self.last_tracks_ns / 1e9
      flt = None if self.mode == MODE_BRAKE_ALL else (lambda tid: self.motion.moving(tid, t_s))
      obj = select_objects(self._tracks, flt).get(mux)
    if obj is None:
      return [(DAS_OBJECT, build_frame(mux))]
    raws = pedestrian_raws(*obj)
    dat = build_frame(mux, raws)
    self._log("ic_onroad", mono_ns=int(now_nanos), tracks_mono_ns=int(self.last_tracks_ns), age_s=round(age_s, 3),
              mode=self.mode, mux=mux, slot=_SLOT[mux], track_id=obj[0], d_rel_m=round(obj[1], 2),
              y_rel_m=round(obj[2], 2), v_rel_ms=round(obj[3], 2), das_dx_m=round(raws["Dx"] * 0.5, 2),
              das_dy_m=round(raws["Dy"] * 0.35 - 22.05, 2), raw_dx=raws["Dx"], raw_dy=raws["Dy"],
              raw_vx=raws["VxRel"], das_id=raws["Id"], type=TYPE_PEDESTRIAN, dat=dat.hex())
    return [(DAS_OBJECT, dat)]

  def _log(self, name, **kw):
    try:
      if self._log_fn is not None:
        self._log_fn(name, kw)
        return
      from openpilot.common.swaglog import cloudlog
      cloudlog.event(name, **kw)
    except Exception:
      pass
