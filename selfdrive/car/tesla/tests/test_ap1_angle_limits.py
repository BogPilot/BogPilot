"""AP1 panda angle-rate table is Tinkla's. The shared table stays for non-AP1.

No panda firmware test is compiled here. This checks the safety header the
Python Tesla tests already read. Dashcam is not decided here. The AP1 flag is applied
by flags_for_candidate, not by this table test.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HEADER = (ROOT / "panda/board/safety/safety_tesla.h").read_text()
INTERFACE = (ROOT / "selfdrive/car/tesla/interface.py").read_text()
PANDA_PY = (ROOT / "panda/python/__init__.py").read_text()


def _struct(name):
  match = re.search(rf"const SteeringLimits {name} = \{{(.*?)\n\}};", HEADER, re.S)
  assert match, name
  return match.group(1)


def _floats(body, field):
  match = re.search(
    rf"\.{field} = \{{\s*\{{([^}}]+)\}}\s*,\s*\{{([^}}]+)\}}",
    body,
  )
  assert match, field
  speeds = [float(part) for part in match.group(1).split(",") if part.strip()]
  rates = [float(part) for part in match.group(2).split(",") if part.strip()]
  return speeds, rates


def test_shared_tesla_steering_limits_are_unchanged():
  body = _struct("TESLA_STEERING_LIMITS")
  assert "angle_deg_to_can = 10" in body
  assert _floats(body, "angle_rate_up_lookup") == ([0.0, 5.0, 15.0], [10.0, 1.6, 0.3])
  assert _floats(body, "angle_rate_down_lookup") == ([0.0, 5.0, 15.0], [10.0, 7.0, 0.8])
  assert "Not an AP1 limit" in HEADER


def test_ap1_table_is_tinkla_and_flag_bit_is_8():
  body = _struct("TESLA_AP1_STEERING_LIMITS")
  assert "angle_deg_to_can = 10" in body
  assert _floats(body, "angle_rate_up_lookup") == ([2.0, 7.0, 17.0], [8.0, 4.0, 2.5])
  assert _floats(body, "angle_rate_down_lookup") == ([2.0, 7.0, 17.0], [9.0, 5.0, 4.5])
  assert "const int TESLA_FLAG_AP1 = 8;" in HEADER
  assert "const int TESLA_FLAG_POWERTRAIN = 1;" in HEADER
  assert "const int TESLA_FLAG_LONGITUDINAL_CONTROL = 2;" in HEADER
  assert "const int TESLA_FLAG_RAVEN = 4;" in HEADER
  assert "FLAG_TESLA_AP1 = 8" in PANDA_PY
  # The AP1 table itself does not mention the powertrain id.
  assert "0x2bf" not in body
  assert "0x2b9f" not in body


def test_tx_hook_picks_ap1_table_only_when_flag_is_set():
  assert "tesla_ap1 = GET_FLAG(param, TESLA_FLAG_AP1);" in HEADER
  assert "tesla_ap1 ? TESLA_AP1_STEERING_LIMITS : TESLA_STEERING_LIMITS" in HEADER
  assert "steer_angle_cmd_checks(desired_angle, steer_control_enabled, limits)" in HEADER


def test_interface_delegates_dashcam_and_ap1_flag():
  assert "dashcam_only_for_candidate(candidate)" in INTERFACE
  assert "flags_for_candidate(candidate, fingerprint, standstill=standstill)" in INTERFACE
  # The bit is applied in safety_flags.py, not inlined here.
  assert "FLAG_TESLA_AP1" not in INTERFACE
  assert "TESLA_FLAG_AP1" not in INTERFACE
