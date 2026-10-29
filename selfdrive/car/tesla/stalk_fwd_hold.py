"""AP1 long stalk-forward (FWD) toggles Experimental Mode while disengaged.

Pure timing helper. No CAN sockets, no params, no actuation.

SpdCtrlLvr_Stat == 1 is FWD (push away / cancel) in tesla_can.dbc.
BogPilot maps that value to ButtonType.cancel. Short FWD pulses cancel /
disengage; a continuous hold of ~1.23 s while already disengaged is
repurposed as one Experimental Mode toggle. Release returns to IDLE and
re-arms.

Mirrors stalk_pull_hold.StalkPullHold (engaged RWD) with inverted arming:
arm only if openpilot/cruise was NOT engaged at the FWD rising edge, and
abort if engagement happens during the hold. A cancel that starts while
engaged (and may disengage mid-pulse) stays disarmed for that hold.

No panda / TX change — device-side Params toggle only, same as RWD pull.
"""

from __future__ import annotations

# DBC VAL_ SpdCtrlLvr_Stat 1 "FWD"
FWD = 1
# Require this continuous assertion while disengaged before toggling once.
FWD_HOLD_S = 1.23
# CarState only runs the helper when this is True.
FWD_HOLD_ENABLED = True


class StalkFwdHold:
  """Track one continuous FWD hold for the Experimental Mode long-forward."""

  def __init__(self, hold_s: float = FWD_HOLD_S):
    self.hold_s = float(hold_s)
    self._holding = False
    self._armed = True  # False after a toggle / abort until release to IDLE
    self._started_disengaged = False
    self._hold_elapsed = 0.0
    self._fired = False

  def update(self, spd_ctrl_lvr, engaged: bool, dt: float) -> bool:
    """Advance one control step.

    Returns True on the single frame where a long-forward toggle should fire.
    spd_ctrl_lvr: SpdCtrlLvr_Stat (0 IDLE, 1 FWD, ...).
    engaged: openpilot / cruise enabled at this sample (prior-step cruise
             enabled in CarState, same as StalkPullHold).
    dt: step seconds (positive).
    """
    try:
      spd = 0 if spd_ctrl_lvr is None else int(spd_ctrl_lvr)
    except (TypeError, ValueError):
      spd = 0
    if dt < 0:
      dt = 0.0

    is_fwd = spd == FWD

    if not is_fwd:
      # Release re-arms for the next hold.
      self._holding = False
      self._armed = True
      self._started_disengaged = False
      self._hold_elapsed = 0.0
      self._fired = False
      return False

    # Rising edge into FWD: arm only if OP was already disengaged.
    if not self._holding:
      self._holding = True
      self._started_disengaged = not bool(engaged)
      self._hold_elapsed = 0.0
      self._fired = False
      # Cancel-while-engaged must not toggle on this hold (even after disengage).
      if not self._started_disengaged:
        self._armed = False
      return False

    # Sustained FWD: abort if engagement happens mid-hold.
    if engaged:
      self._armed = False
      return False

    if not self._armed or not self._started_disengaged or self._fired:
      return False

    self._hold_elapsed += dt
    if self._hold_elapsed >= self.hold_s:
      self._fired = True
      self._armed = False
      return True
    return False
