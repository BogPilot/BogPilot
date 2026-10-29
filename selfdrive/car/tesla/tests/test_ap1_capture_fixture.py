from openpilot.selfdrive.car.tesla.platform import classify_ap1_chassis, classify_tesla_platform, long_control_allowed
from openpilot.selfdrive.car.tesla.stalk_follow import map_stalk_follow, parse_stalk_raw
from openpilot.selfdrive.car.tesla.tests.fixtures.ap1_drive_addrs import (
  ADDR_0X2BF_ABSENT,
  DEVICE_RTC_YEAR_UNTRUSTED,
  DTR_DIST_RQ_RAW as DRIVE_DTR_DIST_RQ_RAW,
  ENGAGED_S,
  LAT_ACTIVE,
  LONG_ACTIVE,
  MAX_VEGO_MPS,
  PANDA_COUNT,
  ROUTE_LABEL as DRIVE_ROUTE_LABEL,
  SENDCAN_ADDRS_WHILE_ACTIVE,
  SENDCAN_BUS,
  SOURCE_BRANCH as DRIVE_SOURCE_BRANCH,
  SOURCE_CAR as DRIVE_SOURCE_CAR,
  SOURCE_COMMIT as DRIVE_SOURCE_COMMIT,
  SOURCE_DASHCAM_ONLY as DRIVE_SOURCE_DASHCAM_ONLY,
  SOURCE_FINGERPRINT as DRIVE_SOURCE_FINGERPRINT,
  SOURCE_SAFETY_PARAM,
)
from openpilot.selfdrive.car.tesla.tests.fixtures.ap1_parked_addrs import (
  ADDR_0X2B9_CAN_COUNTS,
  ADDR_0X2B9_PRESENT,
  ADDR_0X2BF_COUNT,
  CONTROLS_WENT_ACTIVE,
  DTR_DIST_RQ_RAW,
  OBSERVED_BUSES,
  ROUTE_LABEL,
  SOURCE_BRANCH,
  SOURCE_CAR,
  SOURCE_COMMIT,
  SOURCE_DASHCAM_ONLY,
  SOURCE_FINGERPRINT,
  VEGO_NEAR_ZERO,
  dual_panda_powertrain_seen,
)
from openpilot.selfdrive.car.tesla.values import CAR, TeslaPlatform

# Profiles already committed by stalk_follow.map_stalk_follow for these raw values.
EXPECTED = (
  (0, "traffic"),
  (33, "closer_than_aggressive"),
  (66, "aggressive"),
  (100, "between_aggressive_and_standard"),
  (133, "standard"),
  (166, "between_standard_and_relaxed"),
  (200, "relaxed"),
)


def test_route_label_has_no_dongle_and_recording_is_frog_ap1():
  assert ROUTE_LABEL == "ap1-parked-2026-10-03"
  assert "--" not in ROUTE_LABEL
  assert SOURCE_BRANCH == "frog_ap1"
  assert SOURCE_COMMIT == "73a16cdd"
  assert SOURCE_CAR == "TESLA_AP1_MODELS"
  assert SOURCE_FINGERPRINT == "fixed"
  # That recording only. This assertion does not change this tree's dashcamOnly.
  assert SOURCE_DASHCAM_ONLY is False
  assert CONTROLS_WENT_ACTIVE is False
  assert VEGO_NEAR_ZERO is True


def test_seven_observed_raw_values_map_to_stalk_profiles():
  assert DTR_DIST_RQ_RAW == tuple(raw for raw, _profile in EXPECTED)
  for raw, profile in EXPECTED:
    mapped = map_stalk_follow(raw)
    parsed = parse_stalk_raw(raw, None)
    parsed_float = parse_stalk_raw(float(raw), None)
    assert mapped.ready and mapped.valid
    assert mapped.profile == profile
    assert mapped.raw == raw
    assert parsed.profile == mapped.profile
    assert parsed.detent == mapped.detent
    assert parsed.follow_s == mapped.follow_s
    assert parsed.traffic is mapped.traffic
    assert parsed_float.profile == mapped.profile


def test_absent_0x2bf_is_not_the_dual_panda_powertrain_fingerprint():
  assert OBSERVED_BUSES == (0, 1, 2)
  assert ADDR_0X2BF_COUNT == 0
  assert dual_panda_powertrain_seen(ADDR_0X2BF_COUNT) is False
  assert dual_panda_powertrain_seen(0) is False
  for bus in OBSERVED_BUSES:
    assert ADDR_0X2B9_PRESENT[bus] is True
    assert ADDR_0X2B9_CAN_COUNTS[bus] > 0
  # 0x2b9 alone is not the AP1 chassis signature. 0x45 and 0x488 are also required.
  # 0x2bf is not required, and a lone 0x2bf is not AP1.
  capture = {bus: {0x2B9: ADDR_0X2B9_CAN_COUNTS[bus]} for bus in OBSERVED_BUSES}
  assert 0x2BF not in capture.get(6, {})
  assert classify_tesla_platform(capture) is None
  assert classify_tesla_platform({6: {0x2BF: 1}}) is None
  platform = classify_tesla_platform(CAR.TESLA_AP1_MODELS)
  assert platform == TeslaPlatform.ap1_s
  assert long_control_allowed(platform) is False


def test_drive_fixture_constants_and_long_still_refused():
  # Recording facts only. This tree does not replay the route and does not engage.
  assert DRIVE_ROUTE_LABEL == "ap1-drive-engaged"
  assert "--" not in DRIVE_ROUTE_LABEL
  assert DRIVE_SOURCE_BRANCH == "frog_ap1"
  assert DRIVE_SOURCE_COMMIT == "73a16cdd"
  assert DRIVE_SOURCE_CAR == "TESLA_AP1_MODELS"
  assert DRIVE_SOURCE_FINGERPRINT == "fixed"
  # That recording only. This assertion does not change this tree's dashcamOnly.
  assert DRIVE_SOURCE_DASHCAM_ONLY is False
  assert SOURCE_SAFETY_PARAM == 0
  assert PANDA_COUNT == 1
  assert ADDR_0X2BF_ABSENT is True
  assert LAT_ACTIVE is True
  assert LONG_ACTIVE is True
  assert SENDCAN_BUS == 0
  assert SENDCAN_ADDRS_WHILE_ACTIVE == (0x2B9, 0x488)
  assert ENGAGED_S == 88.5
  assert MAX_VEGO_MPS == 14.2
  assert DEVICE_RTC_YEAR_UNTRUSTED is True
  assert DRIVE_DTR_DIST_RQ_RAW == DTR_DIST_RQ_RAW
  platform = classify_tesla_platform(CAR.TESLA_AP1_MODELS)
  assert platform == TeslaPlatform.ap1_s
  assert long_control_allowed(platform) is False
  assert long_control_allowed(None) is False


def test_capture_chassis_signature_is_ap1_s_without_0x2bf():
  # Parked fixture: buses 0/1/2, 0x2b9 present, 0x2bf count 0, stalk 0x45 DTR values.
  # Drive fixture: sendcan bus 0 had 0x2b9 and 0x488 while active. No payloads.
  assert OBSERVED_BUSES == (0, 1, 2)
  assert 0 in OBSERVED_BUSES
  assert ADDR_0X2BF_COUNT == 0
  assert ADDR_0X2BF_ABSENT is True
  assert SENDCAN_BUS == 0
  assert set(SENDCAN_ADDRS_WHILE_ACTIVE) == {0x2B9, 0x488}
  # 0x45 is STW_ACTN_RQ, the parked capture's DTR_Dist_Rq source. Not 0x2bf.
  chassis = {0x45, 0x2B9, 0x488}
  fingerprint = {0: {addr: 1 for addr in chassis}}
  platform = classify_tesla_platform(fingerprint)
  assert classify_ap1_chassis(chassis) == TeslaPlatform.ap1_s
  assert platform == TeslaPlatform.ap1_s
  assert platform not in (TeslaPlatform.preap, TeslaPlatform.ap1_x, TeslaPlatform.ap2)
  assert long_control_allowed(platform) is False
  assert 0x2BF not in chassis
