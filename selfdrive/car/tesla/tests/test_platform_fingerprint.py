from openpilot.selfdrive.car.tesla.platform import EarlyPlatformFixture, classify_ap1_chassis, classify_tesla_platform, long_control_allowed
from openpilot.selfdrive.car.tesla.values import CANBUS, CAR, TeslaPlatform

# tesla_can.dbc chassis: STW_ACTN_RQ, DAS_control, DAS_steeringControl.
_AP1_CHASSIS = frozenset((0x45, 0x2B9, 0x488))


def test_ap1_model_s_maps_to_ap1_s_and_long_stays_off():
  platform = classify_tesla_platform(CAR.TESLA_AP1_MODELS)
  assert platform == TeslaPlatform.ap1_s
  assert long_control_allowed(platform) is False
  assert long_control_allowed(None) is False


def test_ap1_name_is_the_same_identity():
  assert classify_tesla_platform("TESLA_AP1_MODELS") == TeslaPlatform.ap1_s
  assert classify_tesla_platform([CAR.TESLA_AP1_MODELS, "TESLA_AP1_MODELS"]) == TeslaPlatform.ap1_s


def test_empty_and_garbage_are_unmatched():
  for fingerprint in (None, "", [], set(), {}, {0: {0x2bf: 8}}, b"\x00", 0, "not-a-tesla", "HONDA_CIVIC"):
    assert classify_tesla_platform(fingerprint) is None
    assert long_control_allowed(classify_tesla_platform(fingerprint)) is False


def test_ambiguous_ap1_and_ap2_is_refused():
  both = [CAR.TESLA_AP1_MODELS, CAR.TESLA_AP2_MODELS]
  assert classify_tesla_platform(both) is None
  assert classify_tesla_platform(set(both)) is None
  assert long_control_allowed(None) is False


def test_raven_is_not_ap2():
  assert classify_tesla_platform(CAR.TESLA_MODELS_RAVEN) is None
  assert classify_tesla_platform(CAR.TESLA_MODELS_RAVEN) != TeslaPlatform.ap2
  assert classify_tesla_platform([CAR.TESLA_MODELS_RAVEN, CAR.TESLA_AP2_MODELS]) == TeslaPlatform.ap2
  assert classify_tesla_platform([CAR.TESLA_MODELS_RAVEN, CAR.TESLA_AP1_MODELS]) == TeslaPlatform.ap1_s


def test_ap2_alone_maps_to_ap2_without_long():
  platform = classify_tesla_platform(CAR.TESLA_AP2_MODELS)
  assert platform == TeslaPlatform.ap2
  assert platform != TeslaPlatform.ap1_s
  assert long_control_allowed(platform) is False


def test_ap1_model_s_does_not_select_preap_or_ap1_x():
  platform = classify_tesla_platform(CAR.TESLA_AP1_MODELS)
  assert platform != TeslaPlatform.preap
  assert platform != TeslaPlatform.ap1_x
  for candidate in (None, "", CAR.TESLA_AP2_MODELS, CAR.TESLA_MODELS_RAVEN, "TESLA_AP1_MODELS"):
    assert classify_tesla_platform(candidate) not in (TeslaPlatform.preap, TeslaPlatform.ap1_x)


def test_preap_and_ap1_x_only_from_explicit_fixture():
  assert classify_tesla_platform(EarlyPlatformFixture(TeslaPlatform.preap)) == TeslaPlatform.preap
  assert classify_tesla_platform(EarlyPlatformFixture(TeslaPlatform.ap1_x)) == TeslaPlatform.ap1_x
  assert long_control_allowed(TeslaPlatform.preap) is False
  assert long_control_allowed(TeslaPlatform.ap1_x) is False
  try:
    EarlyPlatformFixture(TeslaPlatform.ap1_s)
    raised = False
  except ValueError:
    raised = True
  assert raised


def test_long_refused_for_every_platform():
  for platform in TeslaPlatform:
    assert long_control_allowed(platform) is False

def test_honda_civic_is_not_an_early_tesla_platform():
  # Honda CAR imports in this environment. A non-Tesla platform id must not
  # classify as preap, ap1_s, ap1_x, or ap2, and long stays refused.
  from openpilot.selfdrive.car.honda.values import CAR as HONDA

  platform = classify_tesla_platform(HONDA.HONDA_CIVIC)
  assert platform is None
  assert platform not in (TeslaPlatform.preap, TeslaPlatform.ap1_s, TeslaPlatform.ap1_x, TeslaPlatform.ap2)
  assert long_control_allowed(platform) is False


def test_ap1_chassis_addrs_classify_as_ap1_s_without_0x2bf():
  # Bus 0 is the chassis bus. Presence of the three IDs is the rule.
  # 0x2bf is not required. Extra addresses do not change the result.
  assert CANBUS.chassis == 0
  assert classify_ap1_chassis(_AP1_CHASSIS) == TeslaPlatform.ap1_s
  assert classify_ap1_chassis({0x45: 8, 0x2B9: 8, 0x488: 4}) == TeslaPlatform.ap1_s
  bus0 = {0: {addr: 1 for addr in _AP1_CHASSIS}}
  platform = classify_tesla_platform(bus0)
  assert platform == TeslaPlatform.ap1_s
  assert platform not in (TeslaPlatform.preap, TeslaPlatform.ap1_x, TeslaPlatform.ap2)
  assert long_control_allowed(platform) is False
  with_extra = {0: {0x45: 8, 0x2B9: 8, 0x488: 4, 0x100: 8, 0x2BF: 1}, 6: {0x2BF: 1}}
  assert classify_tesla_platform(with_extra) == TeslaPlatform.ap1_s
  assert classify_tesla_platform(set(_AP1_CHASSIS)) == TeslaPlatform.ap1_s


def test_0x2bf_alone_and_incomplete_chassis_are_not_ap1():
  assert classify_ap1_chassis({0x2BF}) is None
  assert classify_ap1_chassis(frozenset((0x2BF,))) is None
  assert classify_tesla_platform({0x2BF}) is None
  assert classify_tesla_platform({0: {0x2BF: 8}}) is None
  assert classify_tesla_platform({6: {0x2BF: 8}}) is None
  for missing in (
    frozenset((0x45, 0x2B9)),
    frozenset((0x45, 0x488)),
    frozenset((0x2B9, 0x488)),
    frozenset((0x45,)),
    frozenset((0x2B9,)),
    frozenset((0x488,)),
  ):
    assert classify_ap1_chassis(missing) is None
    assert classify_tesla_platform({0: {addr: 1 for addr in missing}}) is None
  # The same three IDs on another bus are not the chassis signature.
  elsewhere = {1: {0x45: 1, 0x2B9: 1, 0x488: 1}, 2: {0x45: 1, 0x2B9: 1, 0x488: 1}}
  assert classify_tesla_platform(elsewhere) is None
  assert classify_ap1_chassis(None) is None
  assert classify_ap1_chassis("TESLA_AP1_MODELS") is None
