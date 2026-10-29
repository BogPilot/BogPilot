"""AP1 posted speed limit for FrogPilot dashboardSpeedLimit.

Mobileye DAS_fusedSpeedLimit (stock AutopilotStatus on autopilot_chassis /
bus 2) first, then GTW UI_mapSpeedLimit / UI_mppSpeedLimit. Output is m/s for
frogpilotCarState.dashboardSpeedLimit (same as Toyota/Ford/Hyundai).

Stock 0x399 only: CarState reads AutopilotStatus from cp_cam
(CANBUS.autopilot_chassis). Cluster TX is on chassis bus 0 and never reaches
that parser. SNA / UNKNOWN / UNLIMITED / missing → 0.

UI_mapSpeedLimit is a DBC enum (VAL_ 568 / BO_ 968), not a scaled mph field.
UI_mppSpeedLimit and DAS_fusedSpeedLimit use factor 5 (opendbc returns the
physical mph or kph). Units come from UI_mapSpeedLimitUnits (0 MPH, 1 KPH).

No new Params keys. AP1-only callers. Not a product, no warranty.
"""

from openpilot.common.conversions import Conversions as CV

# VAL_ UI_mapSpeedLimit on UI_driverAssistMapData. Codes not listed → no limit.
_UI_MAP_LIMIT = {
  1: 5, 2: 7, 3: 10, 4: 15, 5: 20, 6: 25, 7: 30, 8: 35, 9: 40, 10: 45,
  11: 50, 12: 55, 13: 60, 14: 65, 15: 70, 16: 75, 17: 80, 18: 85, 19: 90,
  20: 95, 21: 100, 22: 105, 23: 110, 24: 115, 25: 120, 26: 130, 27: 140,
  28: 150, 29: 160,
}
_UI_MAP_UNKNOWN = 0
_UI_MAP_UNLIMITED = 30
_UI_MAP_SNA = 31

# DAS_fusedSpeedLimit physical (after DBC factor 5). NONE is 31*5=155.
_FUSED_NONE_MIN = 150.0


def _uom_to_ms(value: float, metric: bool) -> float:
  return float(value) * (CV.KPH_TO_MS if metric else CV.MPH_TO_MS)


def fused_speed_limit_ms(fused_phys, metric: bool) -> float:
  """DAS_fusedSpeedLimit physical value → m/s, or 0 if SNA/NONE/missing."""
  if fused_phys is None:
    return 0.0
  try:
    v = float(fused_phys)
  except (TypeError, ValueError):
    return 0.0
  if v <= 0.0 or v >= _FUSED_NONE_MIN:
    return 0.0
  return _uom_to_ms(v, metric)


def ui_map_speed_limit_ms(ui_map_code, metric: bool) -> float:
  """UI_mapSpeedLimit enum code → m/s, or 0 if unknown/SNA/unlimited."""
  if ui_map_code is None:
    return 0.0
  try:
    code = int(ui_map_code)
  except (TypeError, ValueError):
    return 0.0
  if code in (_UI_MAP_UNKNOWN, _UI_MAP_UNLIMITED, _UI_MAP_SNA):
    return 0.0
  limit = _UI_MAP_LIMIT.get(code)
  if limit is None:
    return 0.0
  return _uom_to_ms(limit, metric)


def mpp_speed_limit_ms(mpp_phys, metric: bool) -> float:
  """UI_mppSpeedLimit physical value → m/s, or 0 if missing/zero."""
  if mpp_phys is None:
    return 0.0
  try:
    v = float(mpp_phys)
  except (TypeError, ValueError):
    return 0.0
  if v <= 0.0:
    return 0.0
  return _uom_to_ms(v, metric)


def units_are_metric(ui_map_speed_limit_units) -> bool:
  """UI_mapSpeedLimitUnits: 0 MPH, 1 KPH. Missing → MPH (AP1 US default)."""
  try:
    return int(ui_map_speed_limit_units) == 1
  except (TypeError, ValueError):
    return False


# Mobileye misread guard (drive 35 seg 11: camera read 80 for 9.6 s while the car's map said 35).
# When the camera limit is more than this far ABOVE a valid map limit, the map limit is used instead.
# Real sign changes seen in drives 2a-35 lead the map by at most 20 mph for about 1 s, so they are untouched.
IMPLAUSIBLE_RISE_MPH = 20.0
IMPLAUSIBLE_RISE_KPH = 30.0
MIN_MAP_CHECK_MPH = 15.0      # map limits below this (e.g. a stray 5) are not trusted for the check
MIN_MAP_CHECK_KPH = 25.0


def dashboard_speed_limit_ms(fused_phys, ui_map_code, mpp_phys, ui_map_speed_limit_units) -> float:
  """Mobileye fused first, then UI map enum, then UI mpp. m/s or 0.

  Exception: a fused (camera) limit far above a plausible map limit is treated as a misread
  and the map limit is returned. Lower camera limits always win (construction / school zones).
  """
  metric = units_are_metric(ui_map_speed_limit_units)
  fused = fused_speed_limit_ms(fused_phys, metric)
  map_limit = ui_map_speed_limit_ms(ui_map_code, metric) or mpp_speed_limit_ms(mpp_phys, metric)
  if fused > 0.0 and map_limit > 0.0:
    rise = IMPLAUSIBLE_RISE_KPH if metric else IMPLAUSIBLE_RISE_MPH
    floor = MIN_MAP_CHECK_KPH if metric else MIN_MAP_CHECK_MPH
    if map_limit >= _uom_to_ms(floor, metric) - 1e-3 and fused - map_limit > _uom_to_ms(rise, metric) + 1e-3:
      return map_limit
  for value in (
    fused,
    ui_map_speed_limit_ms(ui_map_code, metric),
    mpp_speed_limit_ms(mpp_phys, metric),
  ):
    if value > 0.0:
      return value
  return 0.0
