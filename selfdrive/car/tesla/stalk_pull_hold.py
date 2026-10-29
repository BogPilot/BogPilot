"""AP1 long stalk-pull (RWD) toggles Experimental Mode while engaged.

Pure timing helper. No CAN sockets, no params, no actuation.

SpdCtrlLvr_Stat == 2 is RWD (pull toward the driver) in tesla_can.dbc.
BogPilot maps that value to ButtonType.resumeCruise. Short RWD pulses
engage / resume; a continuous hold of ~1.23 s while already engaged is
repurposed as one Experimental Mode toggle. Release returns to IDLE and
re-arms.

Evidence from AP1 rlogs (routes 10/11/12/17): RWD asserts continuously
for the pulse seen on the bus (spring stalk). Observed RWD holds were
70–140 ms (taps). No ≥0.5 s hold appeared in those logs — a 2.0 s hold
requires holding against the spring so SpdCtrlLvr stays at 2.

RE-ENABLED (PULL_HOLD_ENABLED = True). Drive 18 showed the stock DI treats a
held RWD pull as resume: ~0.7 s into the hold the cruise set speed may jump
back to the remembered set (e.g. 15 -> 31 mph) before Experimental Mode
toggles. That DI side-effect is accepted: engaged-only ~2 s hold toggles
Experimental once; short pulls and the engage pull itself stay disarmed.
"""

from __future__ import annotations

# DBC VAL_ SpdCtrlLvr_Stat 2 "RWD"
RWD = 2
# Require this continuous assertion while engaged before toggling once.
PULL_HOLD_S = 1.23
# CarState only runs the helper when this is True. See module docstring.
PULL_HOLD_ENABLED = True


class StalkPullHold:
  """Track one continuous RWD hold for the Experimental Mode long-pull."""

  def __init__(self, hold_s: float = PULL_HOLD_S):
    self.hold_s = float(hold_s)
    self._pulling = False
    self._armed = True  # False after a toggle until release to IDLE
    self._started_engaged = False
    self._hold_elapsed = 0.0
    self._fired = False

  def update(self, spd_ctrl_lvr, engaged: bool, dt: float) -> bool:
    """Advance one control step.

    Returns True on the single frame where a long-pull toggle should fire.
    spd_ctrl_lvr: SpdCtrlLvr_Stat (0 IDLE, 2 RWD, ...).
    engaged: openpilot / cruise enabled at the *start* of this sample
             (already engaged before this pull counts).
    dt: step seconds (positive).
    """
    try:
      spd = 0 if spd_ctrl_lvr is None else int(spd_ctrl_lvr)
    except (TypeError, ValueError):
      spd = 0
    if dt < 0:
      dt = 0.0

    is_rwd = spd == RWD

    if not is_rwd:
      # Release re-arms for the next hold.
      self._pulling = False
      self._armed = True
      self._started_engaged = False
      self._hold_elapsed = 0.0
      self._fired = False
      return False

    # Rising edge into RWD: note whether OP was already engaged.
    if not self._pulling:
      self._pulling = True
      self._started_engaged = bool(engaged)
      self._hold_elapsed = 0.0
      self._fired = False
      # If this pull is the engage gesture itself, do not toggle on this hold.
      if not self._started_engaged:
        self._armed = False
      return False

    # Sustained RWD.
    if not self._armed or not self._started_engaged or self._fired:
      return False

    self._hold_elapsed += dt
    if self._hold_elapsed >= self.hold_s:
      self._fired = True
      self._armed = False
      return True
    return False
