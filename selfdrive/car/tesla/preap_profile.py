"""Pick the early Model S profile (AP1 or Pre-AP) with no user input.

Ported from NotAutopilot/opendbc 21a45cf918dd5df03f9143e16eb9890a2936f5d3
(opendbc/car/tesla/nap_detect.py, MIT): "AP1 when the DAS ECU is on the harness,
else Pre-AP". BogPilot is stricter on the Pre-AP side: NAP falls back to Pre-AP
whenever both AP addresses are not seen; here Pre-AP needs a sustained window of
Tesla chassis traffic with ZERO factory AP frames. One AP address alone is
ambiguous and selects nothing.

CAN IDs, with sources (bus 0 = chassis, 2 = autopilot_chassis, values.CANBUS):
  0x488 DAS_steeringControl, 0x2B9 DAS_control - factory AP ECU
      (opendbc/tesla_can.dbc BO_ 1160 / BO_ 697; NAP nap_detect.py AP_ECU_ADDRS)
  0x370 EPAS_sysStatus, 0x108 DI_torque1, 0x118 DI_torque2, 0x155 ESP_B,
  0x45 STW_ACTN_RQ, 0x318 GTW_carState - Tesla chassis, present on Pre-AP and AP1
      (opendbc/tesla_can.dbc; NAP opendbc cc39c7f safety/modes/tesla_preap.h RX checks)

Only frames the car sent count: openpilot marks panda TX echoes with src >= 128,
and only src 0 and 2 are read here, so nothing the device sends can make a car
look like AP1 (or hide AP frames).

Precedence:
  FINGERPRINT env > TeslaCarProfile file > explicit CarModel / fingerprint match
  > NAPCarType import > CAN auto-detect > existing fallback.
The auto result is never written to CarModel.
"""
from pathlib import Path

from openpilot.selfdrive.car.tesla.nap_import import (
  DEFAULT_OUT_DIR, NAP_CAR_TYPE_AP1, NAP_CAR_TYPE_PREAP, load_nap_import, nap_car_type,
)

PREAP_PLATFORM = "TESLA_PREAP_MODELS"
AP1_PLATFORM = "TESLA_AP1_MODELS"

PROFILE_FILE_NAME = "TeslaCarProfile"
PROFILE_AUTO = "auto"
PROFILE_PREAP = "preap"
PROFILE_AP1 = "ap1"
_PROFILE_TO_PLATFORM = {PROFILE_PREAP: PREAP_PLATFORM, PROFILE_AP1: AP1_PLATFORM}
_NAP_TO_PLATFORM = {NAP_CAR_TYPE_PREAP: PREAP_PLATFORM, NAP_CAR_TYPE_AP1: AP1_PLATFORM}

AP_ECU_ADDRS = (0x488, 0x2B9)
CHASSIS_ADDRS = (0x370, 0x108, 0x118, 0x155, 0x45, 0x318)
CAR_BUSES = (0, 2)

# AP1 needs each AP address this many times (0x488 is 50 Hz, 0x2B9 ~25 Hz).
AP_MIN_COUNT = 3
# Pre-AP needs this long of Tesla chassis traffic with no AP frame at all.
# can_fingerprint bails after 200 CAN batches (~2 s), so 1.5 s fits inside it.
PREAP_MIN_ABSENCE_NS = int(1.5e9)

EMPTY_CAR_MODELS = (None, "", "MOCK")


def read_profile_override(bogpilot_dir=DEFAULT_OUT_DIR) -> str:
  """auto (default, absent, unreadable or unknown), preap or ap1."""
  try:
    text = (Path(bogpilot_dir) / PROFILE_FILE_NAME).read_text().strip().lower()
  except (OSError, UnicodeDecodeError):
    return PROFILE_AUTO
  return text if text in _PROFILE_TO_PLATFORM else PROFILE_AUTO


class ApEcuObserver:
  """Feeds on CAN frames during fingerprinting. Pure: no I/O."""

  def __init__(self):
    self.ap_counts = {addr: 0 for addr in AP_ECU_ADDRS}
    self.chassis_seen = set()
    self.first_chassis_ns = None
    self.last_ns = None

  def update(self, mono_ns: int, frames):
    """frames: iterable of (address, src)."""
    for addr, src in frames:
      if src not in CAR_BUSES:
        continue  # radar bus, aux buses, and every TX echo (src >= 128)
      if addr in self.ap_counts:
        self.ap_counts[addr] += 1
      elif src == 0 and addr in CHASSIS_ADDRS:
        self.chassis_seen.add(addr)
        if self.first_chassis_ns is None:
          self.first_chassis_ns = mono_ns
    self.last_ns = mono_ns

  def update_can_msg(self, can_msg):
    """cereal `can` event (logMonoTime + list of CanData)."""
    self.update(can_msg.logMonoTime, ((c.address, c.src) for c in can_msg.can))

  def result(self) -> str | None:
    """AP1_PLATFORM, PREAP_PLATFORM, or None (not enough evidence)."""
    if all(n >= AP_MIN_COUNT for n in self.ap_counts.values()):
      return AP1_PLATFORM
    if any(n > 0 for n in self.ap_counts.values()):
      return None  # some AP traffic: never call this Pre-AP
    if len(self.chassis_seen) != len(CHASSIS_ADDRS) or self.first_chassis_ns is None:
      return None  # not (yet) a Tesla chassis bus
    if self.last_ns - self.first_chassis_ns < PREAP_MIN_ABSENCE_NS:
      return None
    return PREAP_PLATFORM


def select_candidate(candidate, force_fingerprint, toggles_car_model, car_model_param,
                     fixed_fingerprint="", override=PROFILE_AUTO, nap_type=None, can_result=None):
  """Return (platform, source).

  source: fixed / override / existing / nap / can. 'existing' is exactly the
  pre-NAP get_car() rule, so a device with CarModel set (every AP1 install
  today) behaves as before unless the TeslaCarProfile file exists.
  """
  if fixed_fingerprint:
    return fixed_fingerprint, "fixed"
  if override in _PROFILE_TO_PLATFORM:
    return _PROFILE_TO_PLATFORM[override], "override"

  if candidate is None or force_fingerprint:
    existing = toggles_car_model if toggles_car_model is not None else "MOCK"
  else:
    existing = candidate

  car_model_set = car_model_param not in EMPTY_CAR_MODELS
  fingerprint_matched = candidate is not None and not force_fingerprint
  if car_model_set or fingerprint_matched:
    return existing, "existing"

  if nap_type in _NAP_TO_PLATFORM:
    return _NAP_TO_PLATFORM[nap_type], "nap"
  if can_result in (AP1_PLATFORM, PREAP_PLATFORM):
    return can_result, "can"
  return existing, "existing"


def resolve_live(candidate, force_fingerprint, toggles_car_model, car_model_param,
                 fixed_fingerprint, observer, bogpilot_dir=DEFAULT_OUT_DIR):
  """select_candidate with the device inputs read. Never raises on bad files."""
  override = read_profile_override(bogpilot_dir)
  nap_type = nap_car_type(load_nap_import(bogpilot_dir))
  can_result = observer.result() if observer is not None else None
  return select_candidate(candidate, force_fingerprint, toggles_car_model, car_model_param,
                          fixed_fingerprint, override, nap_type, can_result)
