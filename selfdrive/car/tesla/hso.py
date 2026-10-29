"""AP1 hands-on pause. No CAN.

Tinkla (501c7de) HSO, default TinklaHso True and TinklaHandsOnLevel 2.0:
EPAS_handsOnLevel >= 2 pauses path lateral (CarController sends type NONE)
and cruise is not dropped. steerTempUnavailableSilent is warning-only. It is
not no-entry and not a soft disable.

carState.steeringPressed is a separate, lower threshold: any non-zero
EPAS_handsOnLevel (AP1_DRIVER_INPUT_LEVEL). That is the stock Tesla port and
frog_ap1 mapping. It raises steerOverride, controlsd goes to the overriding
state, and the prebuilt UI draws the grey border. Logged AP1 EPAS reports
levels 0, 1 and 3 only (never 2), so a >= 2 steeringPressed only fired on the
brief level 3 peaks and the border stayed green while the driver steered.

The 50-frame numb period and the 15 degree handoff in HSO_module.py are
not applied. Resume waits for Ap1DriverYield: hands back at level 0 for
AP1_RESUME_HOLD_S, then the measured-angle soft-start, if cruise is still
enabled and EPAS is not EAC_FAULT. Code 6 and a latched
code 3 do not block that resume. Any other non-idle name stays a steer warning.
"""

# selfdrive/car/tesla/carstate.py: load_float_param("TinklaHandsOnLevel", 2.0)
AP1_HANDS_ON_LEVEL = 2
# carState.steeringPressed (override / grey border). Stock Tesla port and
# frog_ap1: hands_on_level > 0.
AP1_DRIVER_INPUT_LEVEL = 1

# Resume hold after a hands pause. Once hands reach AP1_HANDS_ON_LEVEL while
# engaged, 0x488 stays type NONE until EPAS has reported level 0 (no driver
# torque at all) for this long; any level >= 1 restarts it. In logged AP1
# drives 9 of 14 level 3 overrides dropped to 1 first and reached 0 up to
# 2.9 s later. After reaching 0 the driver came back within 0.24-0.56 s in
# 9 of 10 re-presses (next 1.0 s, then >= 2 s). Builds used 0.8 s then 0.5 s
# (Tinkla's HSO numb period is 50 frames = 0.5 s); shortened to 0.3 s.
# Lateral then resumes through the 300 ms measured-angle soft-start
# (AP1_ENGAGE_SOFT_START_FRAMES).
AP1_RESUME_HOLD_S = 0.3
# CarController runs at 100 Hz (DT_CTRL).
AP1_CONTROL_HZ = 100
AP1_RESUME_HOLD_FRAMES = int(round(AP1_RESUME_HOLD_S * AP1_CONTROL_HZ))

_HARSH = "steerTempUnavailable"
_SILENT = "steerTempUnavailableSilent"


def ap1_steering_pressed(hands_on_level):
  """True at the Tinkla HSO threshold (lateral pause). Level 1 does not pause."""
  return hands_on_level >= AP1_HANDS_ON_LEVEL


def ap1_driver_input(hands_on_level):
  """carState.steeringPressed on AP1: any driver torque EPAS reports.

  Display / override only. It does not by itself pause lateral; the pause is
  ap1_steering_pressed (>= 2) in CarController.
  """
  return hands_on_level >= AP1_DRIVER_INPUT_LEVEL


class Ap1DriverYield:
  """Engaged-only hands pause with a resume hold. One update per CarController step.

  Enters at hands_on_level >= AP1_HANDS_ON_LEVEL while enabled. Stays active
  until hands_on_level has been 0 for AP1_RESUME_HOLD_FRAMES in a row; a level
  1 or higher restarts the count. Not enabled clears it at once, so disengage
  and re-engage are not delayed. `resumed` is true only on the step the hold
  ends, so CarController can restart the measured-angle soft-start.
  """

  def __init__(self):
    self.active = False
    self.quiet_frames = 0
    self.resumed = False

  def update(self, enabled, hands_on_level):
    self.resumed = False
    if not enabled:
      self.active = False
      self.quiet_frames = 0
      return False
    if ap1_steering_pressed(hands_on_level):
      self.active = True
      self.quiet_frames = 0
    elif self.active:
      if ap1_driver_input(hands_on_level):
        self.quiet_frames = 0
      else:
        self.quiet_frames += 1
        if self.quiet_frames >= AP1_RESUME_HOLD_FRAMES:
          self.active = False
          self.quiet_frames = 0
          self.resumed = True
    return self.active


def ap1_lat_active(lat_active, hands_on_level):
  """Path lateral is off while hands are at or above the HSO threshold.

  This is the AP1 pause gate. controlsd keeps latActive through a level 1
  override (steeringPressed), like the stock Tesla port, so the planned angle
  is withheld here instead.
  """
  if ap1_steering_pressed(hands_on_level):
    return False
  return bool(lat_active)


def ap1_hso_event_names(names):
  """Replace the soft-disable steer event with the silent warning.

  steerUnavailable (EAC_FAULT) is left alone. That one still disables.
  Duplicate silent names are dropped.
  """
  out = []
  for name in names:
    if name == _HARSH:
      name = _SILENT
    if name not in out:
      out.append(name)
  return out
