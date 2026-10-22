"""AP1 instrument-cluster discovery harness for 0x309 DAS_object. Parked only.

Test branch only (bogpilot-tesla-ic-discovery). Off unless the file
/data/params_bogpilot/ICDiscovery says 1/true/on. Even then a frame is built
only while ALL of these hold on the same step:
  - AP1 fingerprint
  - gear is Park
  - openpilot not enabled (CC.enabled False) and stock cruise not enabled
  - vEgo below 0.1 m/s (0.3 ft/s)
Any gate false: nothing is sent on that step, so the panda forwards stock
0x309 again within its substitution timeout.

What it sends: 0x309 on bus 0, one mux per frame, in the stock order
0 LEAD, 1 LEFT, 2 RIGHT, 3 CUTIN, 5 HEADINGS, every 3rd control step
(~33 Hz total, ~6.7 Hz per mux, the stock rates from the AP1 survey).
One test object steps through a fixed list every STEP_S seconds; every other
slot is "no object" (stock SNA values). The current step is logged once per
change (cloudlog "ic_discovery step ...") so a phone video of the IC can be
matched to the type that was on the bus.

Layout: BO_ 777 DAS_object, BogGyver/opendbc 9c0b6fe tesla_can_pre1916.dbc
(L808 BO_, L978-L999 VAL_). It is not a BO_ in this tree's tesla_can.dbc, so
it is packed here by hand. Checked on AP1 rlogs: decoded leadVehDx is within
10 ft of openpilot's radar lead 97 % of the time (n=2,840). No counter, no
checksum in this frame.

Panda: stock BogPilot AP1 safety does NOT list 0x309 in TESLA_AP1_TX_MSGS,
so without the separate (unapplied) safety patch every frame from here is
dropped by the panda (counted in pandaStates.safetyTxBlocked) and the IC
never sees it. With the patch, the panda also drops the stock 0x309 copy
while openpilot sent one recently, so the two do not alternate on the IC.

Not a product. No warranty. Display only. Parked only.
"""

from dataclasses import dataclass
from pathlib import Path

DAS_OBJECT = 0x309
IC_DISCOVERY_BUS = 0

ICDISCOVERY_PATH = Path("/data/params_bogpilot/ICDiscovery")
# Tests override this module attribute.
_icdiscovery_path = ICDISCOVERY_PATH

MUX_LEAD = 0
MUX_LEFT = 1
MUX_RIGHT = 2
MUX_CUTIN = 3
MUX_HEADINGS = 5
MUX_ORDER = (MUX_LEAD, MUX_LEFT, MUX_RIGHT, MUX_CUTIN, MUX_HEADINGS)

# VAL_ 777 DAS_leadVehType (L979). Left/right/cutin/veh2 lists stop at 5.
TYPE_NAMES = {0: "UNKNOWN", 1: "TRUCK", 2: "CAR", 3: "MOTORCYCLE", 4: "BICYCLE", 5: "PEDESTRIAN", 6: "IPSO"}

# Fixed test position. 20 m = 66 ft ahead, centered. Side slots 3.5 m = 11 ft.
TEST_DX_M = 20.0
SIDE_DY_M = 3.5
STEP_S = 3.0
# Every 3rd 100 Hz control step -> ~33 Hz, the stock 0x309 rate.
SEND_EVERY_N_FRAMES = 3
V_EGO_PARKED_MS = 0.1

# (mux, type, dy_m). Lead types 0..6, then left and right 1..5.
# Dy sign: Tinkla sends Dy = C0 - radarState.yRel, and openpilot yRel is
# positive to the right, so +Dy is taken as left. Not yet seen on the IC.
SEQUENCE = tuple(
  [(MUX_LEAD, t, 0.0) for t in range(7)] +
  [(MUX_LEFT, t, SIDE_DY_M) for t in range(1, 6)] +
  [(MUX_RIGHT, t, -SIDE_DY_M) for t in range(1, 6)]
)


@dataclass(frozen=True)
class Field:
  start: int
  size: int
  factor: float
  offset: float


# Object slot layout, relative names. Same bits for mux 0/1/2/3 (pre1916 L809-L842).
# First object: Type 3|3, Relevant 7|1, Dx 8|8 (0.5), VxRel 16|4 (4,-30),
# Dy 20|7 (0.35,-22.05), Id 27|7. Second object (not in CUTIN): Type 34|3,
# Relevant 38|1, Dx 39|8, VxRel 47|4, Dy 51|7, Id 58|6.
MUX_FIELD = Field(0, 3, 1, 0)
OBJ1 = {
  "Type": Field(3, 3, 1, 0), "Relevant": Field(7, 1, 1, 0), "Dx": Field(8, 8, 0.5, 0),
  "VxRel": Field(16, 4, 4, -30), "Dy": Field(20, 7, 0.35, -22.05), "Id": Field(27, 7, 1, 0),
}
OBJ2 = {
  "Type": Field(34, 3, 1, 0), "Relevant": Field(38, 1, 1, 0), "Dx": Field(39, 8, 0.5, 0),
  "VxRel": Field(47, 4, 4, -30), "Dy": Field(51, 7, 0.35, -22.05), "Id": Field(58, 6, 1, 0),
}
# Mux 5: seven 8-bit headings at 8, 16, ..., 56, (0.0245437, -3.1415936) rad.
HEADING_STARTS = (8, 16, 24, 32, 40, 48, 56)
HEADING_ZERO_RAW = 128  # 0 rad

# SNA ("no object"): VAL_ 777 ...Dx 255, ...VxRel 15, ...Id 127 (Veh2Id 0).
# Stock AP1 empty lead slots decode to Dx 127.5 m, Dy -22.05 m, Id 127.
SNA1 = {"Type": 0, "Relevant": 0, "Dx": 255, "VxRel": 15, "Dy": 0, "Id": 127}
SNA2 = {"Type": 0, "Relevant": 0, "Dx": 255, "VxRel": 15, "Dy": 0, "Id": 0}


def to_raw(f: Field, value: float) -> int:
  raw = int(round((float(value) - f.offset) / f.factor))
  return max(0, min(raw, (1 << f.size) - 1))


def from_raw(f: Field, raw: int) -> float:
  return raw * f.factor + f.offset


def _set(word: int, f: Field, raw: int) -> int:
  mask = ((1 << f.size) - 1) << f.start
  return (word & ~mask) | ((int(raw) << f.start) & mask)


def _get(word: int, f: Field) -> int:
  return (word >> f.start) & ((1 << f.size) - 1)


def _slot(word: int, layout, raws) -> int:
  for k, f in layout.items():
    word = _set(word, f, raws[k])
  return word


def object_raws(vtype: int, dx_m: float, dy_m: float, obj_id: int = 1) -> dict:
  """Raw slot values for one stationary test object (VxRel 0 is not exact: raw 8 = +2 m/s)."""
  return {
    "Type": int(vtype) & 0x7, "Relevant": 0,
    "Dx": to_raw(OBJ1["Dx"], dx_m), "VxRel": to_raw(OBJ1["VxRel"], 0.0),
    "Dy": to_raw(OBJ1["Dy"], dy_m), "Id": int(obj_id),
  }


def build_frame(mux: int, obj1: dict | None = None, obj2: dict | None = None) -> bytes:
  """8-byte 0x309 for one mux. obj1/obj2 are raw slot dicts; None = SNA."""
  word = _set(0, MUX_FIELD, mux)
  if mux == MUX_HEADINGS:
    for s in HEADING_STARTS:
      word = _set(word, Field(s, 8, 1, 0), HEADING_ZERO_RAW)
  elif mux in (MUX_LEAD, MUX_LEFT, MUX_RIGHT, MUX_CUTIN):
    word = _slot(word, OBJ1, obj1 if obj1 is not None else SNA1)
    if mux != MUX_CUTIN:
      word = _slot(word, OBJ2, obj2 if obj2 is not None else SNA2)
  else:
    raise ValueError(f"mux {mux} not built")
  return word.to_bytes(8, "little")


def decode_frame(dat: bytes) -> dict:
  """Mux and first-object physical values (tests / logs)."""
  word = int.from_bytes(bytes(dat), "little")
  mux = _get(word, MUX_FIELD)
  out = {"mux": mux}
  if mux in (MUX_LEAD, MUX_LEFT, MUX_RIGHT, MUX_CUTIN):
    for k, f in OBJ1.items():
      out[k] = from_raw(f, _get(word, f))
  return out


def ic_discovery_enabled(fingerprint: str | None, stored: str | bytes | None = None) -> bool:
  """AP1 only. Off unless the file (or `stored`) says 1/true/on."""
  from openpilot.selfdrive.car.tesla.values import CAR
  if fingerprint != CAR.TESLA_AP1_MODELS:
    return False
  if stored is None:
    try:
      stored = _icdiscovery_path.read_text(encoding="utf-8", errors="replace") if _icdiscovery_path.is_file() else ""
    except OSError:
      stored = ""
  if isinstance(stored, bytes):
    stored = stored.decode(errors="replace")
  return stored.strip().lower() in ("1", "true", "on")


def parked_gate(enabled_toggle: bool, in_park: bool, op_enabled: bool, cruise_enabled: bool, v_ego: float) -> bool:
  return bool(enabled_toggle and in_park and not op_enabled and not cruise_enabled and abs(v_ego) < V_EGO_PARKED_MS)


def step_at(elapsed_s: float) -> int:
  return int(max(elapsed_s, 0.0) // STEP_S) % len(SEQUENCE)


class IcDiscovery:
  """Per control step: poll(), then update() -> [(addr, bytes)] or []."""

  # Re-read the toggle file at 1 Hz (100 control steps), not every step.
  TOGGLE_POLL_FRAMES = 100

  def __init__(self, fingerprint: str):
    self.fingerprint = fingerprint
    self.toggle = False
    self.start_ns = None
    self.mux_idx = 0
    self.last_step = None
    self.frame = 0

  def poll(self) -> bool:
    """Call once per control step before update(). Re-reads the toggle file at
    1 Hz. When False, the caller must not call update() and sends nothing."""
    if self.frame % self.TOGGLE_POLL_FRAMES == 0:
      self.toggle = ic_discovery_enabled(self.fingerprint)
    self.frame += 1
    if not self.toggle:
      self._reset()
    return self.toggle

  def _reset(self):
    self.start_ns = None
    self.last_step = None
    self.mux_idx = 0

  def update(self, in_park: bool, op_enabled: bool, cruise_enabled: bool, v_ego: float, now_nanos: int):
    if not parked_gate(self.toggle, in_park, op_enabled, cruise_enabled, v_ego):
      self._reset()
      return []
    if self.start_ns is None:
      self.start_ns = now_nanos
    if (self.frame - 1) % SEND_EVERY_N_FRAMES != 0:
      return []
    step = step_at((now_nanos - self.start_ns) / 1e9)
    mux_t, vtype, dy = SEQUENCE[step]
    if step != self.last_step:
      self.last_step = step
      self._log(step, mux_t, vtype)
    mux = MUX_ORDER[self.mux_idx]
    self.mux_idx = (self.mux_idx + 1) % len(MUX_ORDER)
    obj1 = object_raws(vtype, TEST_DX_M, dy) if mux == mux_t else None
    return [(DAS_OBJECT, build_frame(mux, obj1))]

  @staticmethod
  def _log(step, mux, vtype):
    try:
      from openpilot.common.swaglog import cloudlog
      slot = {MUX_LEAD: "lead", MUX_LEFT: "left", MUX_RIGHT: "right"}.get(mux, str(mux))
      cloudlog.warning(f"ic_discovery step {step} slot {slot} type {vtype} {TYPE_NAMES.get(vtype, '?')}")
    except Exception:
      pass
