"""AP1 blind-spot flags from the stock Mobileye AutopilotStatus (0x399).

The stock DAS sends DAS_blindSpotRearLeft / DAS_blindSpotRearRight in 0x399
on bus 2 (cp_cam), the frame the cluster substitution already reads. Values
(tesla_can.dbc VAL_ 921): 0 NO_WARNING, 1 WARNING_LEVEL_1, 2 WARNING_LEVEL_2,
3 SNA. 1 and 2 count as a car in the blind spot. 0 and 3 do not set the flag.

0x399 comes at about 2.4 Hz, so each side holds for BSM_HOLD_S after the last
frame that said 1 or 2. A single warning frame shows for BSM_HOLD_S; a run of
warning frames shows for the run plus BSM_HOLD_S, with no gaps between frames.

opendbc has no Tesla checksum check, so a frame whose DAS_statusChecksum does
not match its bytes is skipped here (no new detection, the hold still runs
out). Read-only: nothing here is sent to the car.
"""
from openpilot.selfdrive.car.tesla.cluster import (
  AUTOPILOT_STATUS, CHECKSUM_SIGNALS, MSG_NAMES, SIGNALS, pack, tesla_checksum,
)

# Seconds a side stays on after the last 0x399 frame that reported it.
BSM_HOLD_S = 1.0

BSM_LEFT = "DAS_blindSpotRearLeft"
BSM_RIGHT = "DAS_blindSpotRearRight"
BSM_DETECTED = (1, 2)  # WARNING_LEVEL_1, WARNING_LEVEL_2. 3 is SNA.

_NAME = MSG_NAMES[AUTOPILOT_STATUS]
_CHECKSUM = CHECKSUM_SIGNALS[AUTOPILOT_STATUS]


def checksum_ok(values):
  """True when DAS_statusChecksum matches the frame rebuilt from its signals."""
  try:
    body = {k: v for k, v in values.items() if k != _CHECKSUM and k in SIGNALS[AUTOPILOT_STATUS]}
    dat = pack(AUTOPILOT_STATUS, SIGNALS[AUTOPILOT_STATUS], body).to_bytes(8, "little")
    return int(values[_CHECKSUM]) == tesla_checksum(AUTOPILOT_STATUS, dat)
  except (KeyError, TypeError, ValueError):
    return False


def detected(value):
  try:
    return int(value) in BSM_DETECTED
  except (TypeError, ValueError):
    return False


def frames_from_vl_all(cp_cam):
  """0x399 frames received this step, oldest first, as {signal: value}."""
  sigs = cp_cam.vl_all.get(_NAME, {}) if hasattr(cp_cam.vl_all, "get") else {}
  n = len(sigs.get(_CHECKSUM, []))
  out = []
  for i in range(n):
    f = {}
    for name, vals in sigs.items():
      if i < len(vals):
        f[name] = vals[i]
    out.append(f)
  return out


class Ap1Blindspot:
  """Per-side hold (latch-off delay) over the 0x399 blind-spot fields."""

  def __init__(self, hold_s=BSM_HOLD_S):
    self.hold_s = hold_s
    self.left_s = 0.0   # seconds of hold left
    self.right_s = 0.0
    self.bad_checksum = 0

  @property
  def left(self):
    return self.left_s > 0.0

  @property
  def right(self):
    return self.right_s > 0.0

  def update(self, frames, dt):
    """Age the holds by dt, then apply this step's frames. Returns (left, right)."""
    self.left_s = max(0.0, self.left_s - dt)
    self.right_s = max(0.0, self.right_s - dt)
    for f in frames:
      if not checksum_ok(f):
        self.bad_checksum += 1
        continue
      if detected(f.get(BSM_LEFT)):
        self.left_s = self.hold_s
      if detected(f.get(BSM_RIGHT)):
        self.right_s = self.hold_s
    return self.left, self.right
