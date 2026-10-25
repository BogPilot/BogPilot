"""get_car() glue for the early Model S profile (AP1 / Pre-AP).

Copyright (c) 2026 BogPilot contributors. MIT License.
Auto-detection idea from NotAutopilot/opendbc 21a45cf (nap_detect.py, MIT);
see preap_profile.py for the rules and CAN ID sources.

car_helpers.get_car() calls:
  observe_more()   - only on an unconfigured device with Tesla chassis traffic
                     and no answer yet, read CAN a little longer
  choose()         - None means "run the original get_car() block unchanged"
                     (every device with CarModel set or a fingerprint match)
  write_status()   - TeslaProfileStatus for the UI, once per fingerprint
Nothing here raises; callers also wrap it.
"""
import time

from openpilot.selfdrive.car.tesla.nap_import import (
  DEFAULT_OUT_DIR, NAP_CAR_TYPE_AP1, NAP_CAR_TYPE_PREAP, load_nap_import, nap_car_type,
)
from openpilot.selfdrive.car.tesla.preap_profile import (
  AP1_PLATFORM, EMPTY_CAR_MODELS, PREAP_PLATFORM, PROFILE_AUTO, read_profile_override, resolve_live,
)

# Upper bound on the extra CAN read. ApEcuObserver needs 1.5 s of chassis
# traffic with zero AP frames; can_fingerprint usually provides most of it.
EXTRA_OBSERVE_S = 2.5
TESLA_PLATFORMS = (AP1_PLATFORM, PREAP_PLATFORM)


def known_car_model(car_model_param, known_platforms):
  """An unknown CarModel (e.g. a NAP-era name) counts as empty for Tesla selection."""
  if isinstance(car_model_param, bytes):
    car_model_param = car_model_param.decode(errors="ignore")
  return car_model_param if car_model_param in known_platforms else None


def needs_more_observation(candidate, force_fingerprint, car_model_param, fixed_fingerprint, observer,
                           bogpilot_dir=DEFAULT_OUT_DIR):
  if observer is None or fixed_fingerprint or car_model_param not in EMPTY_CAR_MODELS:
    return False
  if candidate is not None and not force_fingerprint:
    return False
  if not observer.chassis_seen or observer.result() is not None:
    return False
  if read_profile_override(bogpilot_dir) != PROFILE_AUTO:
    return False
  # NAP 'Auto' (or no NAP import) still auto-detects.
  return nap_car_type(load_nap_import(bogpilot_dir)) not in (NAP_CAR_TYPE_PREAP, NAP_CAR_TYPE_AP1)


def observe_more(observer, next_can, clock=time.monotonic, limit_s=EXTRA_OBSERVE_S):
  """Feed observer from next_can() until it answers, or limit_s passes."""
  deadline = clock() + limit_s
  while observer.result() is None and clock() < deadline:
    observer.update_can_msg(next_can())
  return observer.result()


def choose(candidate, force_fingerprint, toggles_car_model, car_model_param, fixed_fingerprint, observer,
           bogpilot_dir=DEFAULT_OUT_DIR):
  """(platform, source), or None to keep the original get_car() block."""
  platform, source = resolve_live(candidate, force_fingerprint, toggles_car_model, car_model_param,
                                  fixed_fingerprint, observer, bogpilot_dir)
  if source in ("existing", "fixed"):
    return None
  return platform, source


def write_status(platform, source, fingerprints, bogpilot_dir=DEFAULT_OUT_DIR):
  """Write TeslaProfileStatus for Tesla platforms. Returns the dict or None."""
  from openpilot.selfdrive.car.tesla.preap_settings import load_preap_settings, profile_status, write_profile_status
  from openpilot.selfdrive.car.tesla.safety_flags import learn_pedal_bus
  if not str(platform).startswith("TESLA_"):
    return None
  try:
    settings, _ = load_preap_settings(bogpilot_dir)
  except Exception:
    settings = None
  status = profile_status(platform, source, settings, None, learn_pedal_bus(fingerprints or {}))
  write_profile_status(status, bogpilot_dir)
  return status
