"""BogPilot: optional audible alert when driver monitoring suggests phone use while moving.

The 0.9.7 driver monitoring model has no dedicated phone output. The closest
signal is the end-to-end "not ready" probability (driverStateV2
notReadyProb[0], `eev1` in helpers.py), which the 0.9.7 model notes describe as
improved for phone detection. It is not phone-specific: it also rises for other
eyes-off-road behaviour. This gate therefore needs a high value held for
several seconds before it fires.

Gate (all must hold for the whole hold window, allowing short dips):
  * toggle on (file /data/params_bogpilot/PhoneAlert, default OFF when absent)
  * vEgo above PHONE_MIN_SPEED (20 mph), not in reverse/park
  * face detected and pose std low (model is confident it sees the driver)
  * no stock driver monitoring alert active (those take priority)
  * eev1 > PHONE_EEV1_THRESH for at least PHONE_HOLD_FRAC of PHONE_HOLD_S
Then the alert is held on for PHONE_ALERT_S and the gate rests for PHONE_COOLDOWN_S.

Turn on, on device:
  echo 1 > /data/params_bogpilot/PhoneAlert    # on
  rm /data/params_bogpilot/PhoneAlert          # back to the default (off)
Read every 2 s by dmonitoringd, so no restart is needed.

The alert sound is loaded by soundd from /data/bogpilot_sounds/phone_use.wav
(device-local, not in git). If that file is missing, prompt_distracted.wav plays.
"""

from collections import deque
from pathlib import Path

from openpilot.common.conversions import Conversions as CV
from openpilot.common.realtime import DT_DMON

PHONE_EEV1_THRESH = 0.45
PHONE_HOLD_S = 3.0
PHONE_HOLD_FRAC = 0.8
PHONE_MIN_SPEED = 20. * CV.MPH_TO_MS
PHONE_ALERT_S = 3.0
PHONE_COOLDOWN_S = 120.0

PHONE_ALERT_PATH = Path("/data/params_bogpilot/PhoneAlert")
# Tests may override the path via this module attribute.
_phone_alert_path = PHONE_ALERT_PATH


def _as_text(value) -> str:
  if isinstance(value, bytes):
    value = value.decode("utf-8", "ignore")
  return str(value).strip().lower()


def phone_alert_enabled(stored: str | bytes | None = None) -> bool:
  """File-backed toggle. Absent or unreadable file means OFF."""
  if stored is None:
    try:
      stored = _phone_alert_path.read_text()
    except OSError:
      return False
  return _as_text(stored) in ("1", "true", "on")


class PhoneUseGate:
  def __init__(self, dt: float = DT_DMON):
    self.dt = dt
    self.hold_n = max(1, int(round(PHONE_HOLD_S / dt)))
    self.window: deque[bool] = deque(maxlen=self.hold_n)
    self.alert_frames = 0
    self.cooldown_frames = 0
    self.alert_active = False
    self.fire_count = 0

  def reset(self):
    self.window.clear()

  def update(self, enabled: bool, v_ego: float, wrong_gear: bool, face_detected: bool,
             low_std: bool, eev1: float, dm_alert_active: bool) -> bool:
    """Step once per driverStateV2 frame. Returns True while the alert should show."""
    if self.alert_frames > 0:
      self.alert_frames -= 1
    if self.cooldown_frames > 0:
      self.cooldown_frames -= 1

    if not enabled:
      self.reset()
      self.alert_frames = 0
    elif dm_alert_active:
      # A stock DM alert wins; drop ours and start the window over.
      self.reset()
      self.alert_frames = 0
    elif v_ego < PHONE_MIN_SPEED or wrong_gear or not face_detected or not low_std:
      self.reset()
    else:
      self.window.append(eev1 > PHONE_EEV1_THRESH)
      if (self.cooldown_frames == 0 and len(self.window) == self.hold_n and
          sum(self.window) >= PHONE_HOLD_FRAC * self.hold_n):
        self.fire_count += 1
        self.alert_frames = int(round(PHONE_ALERT_S / self.dt))
        self.cooldown_frames = int(round(PHONE_COOLDOWN_S / self.dt))
        self.reset()

    self.alert_active = self.alert_frames > 0
    return self.alert_active
