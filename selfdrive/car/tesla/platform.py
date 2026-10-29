"""Map an already-resolved Tesla identity to TeslaPlatform. No actuation."""

from dataclasses import dataclass

from openpilot.selfdrive.car.tesla.values import CANBUS, CAR, TeslaPlatform

# Production identities that exist in this tree.
# CAR.TESLA_AP1_MODELS has no FW_VERSIONS row in fingerprints.py, so the
# platform enum member is the AP1 Model S identity. Do not match on shared
# CarSpecs or dbc_dict: AP1 and AP2 share those, and that would be ambiguous.
# CAR.TESLA_MODELS_RAVEN is intentionally absent. Raven is not ap2.
_PRODUCTION = {
  CAR.TESLA_AP1_MODELS: TeslaPlatform.ap1_s,
  CAR.TESLA_AP2_MODELS: TeslaPlatform.ap2,
}

_FIXTURE_ONLY = (TeslaPlatform.preap, TeslaPlatform.ap1_x)

# AP1 chassis bus (CANBUS.chassis, bus 0) in opendbc/tesla_can.dbc.
# The redacted capture has these three and does not have 0x2bf.
# 0x45  STW_ACTN_RQ          stalk, including DTR_Dist_Rq
# 0x2b9 DAS_control          chassis longitudinal, 11-bit
# 0x488 DAS_steeringControl  chassis steering, 11-bit
# 0x2bf is DAS_control in opendbc/tesla_powertrain.dbc, the upstream
# second-panda powertrain check. It is not part of this signature.
_AP1_CHASSIS_ADDRS = frozenset((0x45, 0x2B9, 0x488))


@dataclass(frozen=True)
class EarlyPlatformFixture:
  """Test-built identity for preap and ap1_x.

  Not a CAN fingerprint and not an FW query result. Production recognition
  does not construct this. preap and ap1_x stay unmatched until a real
  fingerprint exists.
  """

  platform: TeslaPlatform

  def __post_init__(self) -> None:
    if self.platform not in _FIXTURE_ONLY:
      raise ValueError("EarlyPlatformFixture is only for preap and ap1_x")


def classify_ap1_chassis(addrs) -> TeslaPlatform | None:
  """Return ap1_s when a chassis address set contains 0x45, 0x2b9, and 0x488.

  `addrs` is the chassis (bus 0) address set, or the bus-0 map of address to
  count. Extra addresses are allowed. 0x2bf is not required and is not
  sufficient on its own. Never returns preap, ap1_x, or ap2. Not a command.
  """
  observed = _integer_addrs(addrs)
  if observed is None:
    return None
  if _AP1_CHASSIS_ADDRS <= observed:
    return TeslaPlatform.ap1_s
  return None


def classify_tesla_platform(fingerprint) -> TeslaPlatform | None:
  """Return one TeslaPlatform, or None when the identity is empty or ambiguous.

  None means longitudinal is not allowed. A bus-0 address set classifies as
  ap1_s only through classify_ap1_chassis. preap and ap1_x are returned only
  for an EarlyPlatformFixture. A dict that is not that chassis signature
  returns None.
  """
  if isinstance(fingerprint, EarlyPlatformFixture):
    return fingerprint.platform

  if isinstance(fingerprint, (set, frozenset)):
    # A set of ints is a chassis address set. A set of platform names is not.
    observed = _integer_addrs(fingerprint)
    if observed is not None:
      return classify_ap1_chassis(observed)

  if isinstance(fingerprint, dict):
    # Openpilot fingerprint: bus -> {address: count}. Chassis is bus 0.
    # Other buses, including a lone 0x2bf, do not select AP1.
    bus0 = fingerprint.get(CANBUS.chassis)
    if isinstance(bus0, (dict, set, frozenset)):
      observed = _integer_addrs(bus0)
      if observed is not None:
        return classify_ap1_chassis(observed)
    return None

  candidates = _candidates(fingerprint)
  if not candidates:
    return None

  matched: set[TeslaPlatform] = set()
  for token in candidates:
    if isinstance(token, EarlyPlatformFixture):
      matched.add(token.platform)
      continue
    try:
      resolved = _PRODUCTION.get(token)
    except TypeError:
      resolved = None
    if resolved is not None:
      matched.add(resolved)

  if len(matched) != 1:
    return None
  return next(iter(matched))


def long_control_allowed(platform: TeslaPlatform | None) -> bool:
  """Whether longitudinal commands are allowed.

  False for None. False for every TeslaPlatform, including ap1_s. This layer
  only recognizes. Longitudinal stays refused until a later layer adds a panda
  safety mode. Do not engage long from this helper.
  """
  if platform is None or isinstance(platform, TeslaPlatform):
    return False
  return False


def _integer_addrs(container) -> frozenset[int] | None:
  """Keys of an address set or address-to-count map. None if any key is not an int."""
  if isinstance(container, dict):
    keys = container.keys()
  elif isinstance(container, (set, frozenset)):
    keys = container
  else:
    return None
  addrs = []
  for key in keys:
    # bool is a subclass of int. Reject it.
    if type(key) is not int:
      return None
    addrs.append(key)
  return frozenset(addrs)


def _candidates(fingerprint) -> list:
  if fingerprint is None:
    return []
  # CAR members and names are strings. Do not iterate them into characters.
  # A dict is an openpilot CAN fingerprint (bus -> addresses). Do not invent
  # a message-count match from it.
  if isinstance(fingerprint, (str, bytes)):
    return [fingerprint]
  if isinstance(fingerprint, dict):
    return []
  if isinstance(fingerprint, (list, tuple, set, frozenset)):
    return list(fingerprint)
  return [fingerprint]
