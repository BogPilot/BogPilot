"""AP1 long RWD stalk-pull Experimental Mode helper. No PII."""

from openpilot.selfdrive.car.tesla.stalk_pull_hold import PULL_HOLD_ENABLED, PULL_HOLD_S, RWD, StalkPullHold


def test_short_rwd_while_engaged_does_not_toggle():
  h = StalkPullHold(hold_s=2.0)
  # Rising edge while engaged
  assert h.update(RWD, True, 0.01) is False
  # Hold 0.5 s total
  fired = False
  for _ in range(49):
    fired |= h.update(RWD, True, 0.01)
  assert fired is False
  # Release re-arms
  assert h.update(0, True, 0.01) is False


def test_long_rwd_while_engaged_toggles_once():
  h = StalkPullHold(hold_s=2.0)
  assert h.update(RWD, True, 0.01) is False
  fired_at = None
  for i in range(250):
    if h.update(RWD, True, 0.01):
      fired_at = i
      break
  assert fired_at is not None
  # ~2.0 s after rising edge: first update is edge (0 elapsed), then 199 more = 2.00s
  assert fired_at == 199
  # Further hold does not re-fire
  for _ in range(50):
    assert h.update(RWD, True, 0.01) is False
  # Must release to re-arm
  assert h.update(0, True, 0.01) is False
  assert h.update(RWD, True, 0.01) is False
  fired2 = False
  for _ in range(200):
    fired2 |= h.update(RWD, True, 0.01)
  assert fired2 is True


def test_engage_pull_does_not_toggle_even_if_held():
  h = StalkPullHold(hold_s=2.0)
  # Rising edge while NOT engaged (the engage gesture)
  assert h.update(RWD, False, 0.01) is False
  # Even if cruise becomes enabled mid-hold, this hold started disengaged
  for _ in range(250):
    assert h.update(RWD, True, 0.01) is False
  # Release and a new long pull while engaged can toggle
  h.update(0, True, 0.01)
  h.update(RWD, True, 0.01)
  fired = False
  for _ in range(200):
    fired |= h.update(RWD, True, 0.01)
  assert fired is True


def test_non_rwd_values_ignored():
  h = StalkPullHold(hold_s=2.0)
  for val in (0, 1, 4, 8, 16, 32):
    assert h.update(val, True, 0.5) is False


def test_defaults():
  assert RWD == 2
  assert PULL_HOLD_S == 1.23
  # Re-enabled; DI may bump set mid-hold (accepted).
  assert PULL_HOLD_ENABLED is True


def test_wiring():
  from pathlib import Path
  root = Path(__file__).resolve().parents[4]
  carstate = (root / "selfdrive/car/tesla/carstate.py").read_text()
  assert "StalkPullHold" in carstate
  assert "stalk_pull_toggle" in carstate
  assert "if PULL_HOLD_ENABLED:" in carstate or "if PULL_HOLD_ENABLED and" in carstate
  assert "parse_stalk_tip" not in carstate
  assert "tip_hold_cruise" not in carstate
  card = (root / "frogpilot/controls/frogpilot_card.py").read_text()
  assert "_apply_ap1_pull_hold" in card
  assert "stalk_pull_toggle" in card
  assert "_apply_ap1_stalk_tip" not in card
  assert "handle_experimental_mode(" in card
  safety = (root / "panda/board/safety/safety_tesla.h").read_text()
  assert "TESLA_AP1_TIP_CRUISE_IGNORE_US" not in safety
  assert "tesla_ap1_tip_ignore_cruise_ts" not in safety
  # Tip module removed
  assert not (root / "selfdrive/car/tesla/stalk_tip.py").exists()
