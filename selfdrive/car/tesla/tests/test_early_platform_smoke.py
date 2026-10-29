"""Early Tesla platform enum must exist. The member list stays strict."""

from openpilot.selfdrive.car import tesla as tesla_car
from openpilot.selfdrive.car.tesla import values

EARLY_PLATFORM_MEMBERS = ("preap", "ap1_s", "ap1_x", "ap2")


def test_early_tesla_platform_enum():
  # Package import is the current dashcam-only Tesla interface. It must not count as the early contract.
  assert tesla_car is not None

  platform = getattr(values, "TeslaPlatform", None)
  missing = [name for name in EARLY_PLATFORM_MEMBERS if platform is None or not hasattr(platform, name)]
  assert not missing, "early Tesla platforms are not in this tree yet (Phase 3)"
