"""AP1 Always On Lateral low-speed gate.

AOL-only lateral (latActive without openpilot engaged) acting while the car
creeps at parking speed, with the driver turning the wheel, has led to
EAC_ERROR_HANDS_ON followed by the EPB revoking EAC (permanent LKAS Fault
until restart). Two logged cases: both under 3.2 mph during parking
manoeuvres, one with the wheel near full lock. AOL overrides at road speed and
engaged overrides down to ~4.5 mph did not fault.

AOL lateral therefore starts only once the car is moving at a steady speed with
the wheel near centre, and releases (no steering frame, same as AOL off) when
the car slows to a crawl. Engaged lateral is not changed. No panda change.
"""
from openpilot.common.conversions import Conversions as CV

AOL_START_SPEED = 6.0 * CV.MPH_TO_MS   # AOL lateral may start at or above this
AOL_STOP_SPEED = 4.0 * CV.MPH_TO_MS    # AOL lateral releases below this
AOL_MAX_START_ANGLE_DEG = 90.0         # no start with the wheel past this


class AP1AolGate:
  def __init__(self):
    self.aol_allowed = False

  def update(self, aol_enabled: bool, engaged: bool, v_ego: float, steering_angle_deg: float) -> bool:
    """Return the AOL flag to use for latActive (engaged lateral is unaffected)."""
    if not aol_enabled:
      self.aol_allowed = False
      return False
    if engaged:
      # Engaged lateral uses its own path; let AOL carry on at a disengage if
      # the car is still above the stop speed.
      self.aol_allowed = v_ego >= AOL_STOP_SPEED
      return True
    if self.aol_allowed:
      if v_ego < AOL_STOP_SPEED:
        self.aol_allowed = False
    elif v_ego >= AOL_START_SPEED and abs(steering_angle_deg) <= AOL_MAX_START_ANGLE_DEG:
      self.aol_allowed = True
    return self.aol_allowed
