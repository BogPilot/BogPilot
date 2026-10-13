"""AP1 long FWD stalk-forward Experimental Mode helper. No PII."""

from openpilot.selfdrive.car.tesla.stalk_fwd_hold import FWD, FWD_HOLD_ENABLED, FWD_HOLD_S, StalkFwdHold
from openpilot.selfdrive.car.tesla.stalk_pull_hold import PULL_HOLD_ENABLED, RWD, StalkPullHold


def test_short_fwd_while_disengaged_does_not_toggle():
  h = StalkFwdHold(hold_s=2.0)
  assert h.update(FWD, False, 0.01) is False
  fired = False
  for _ in range(49):
    fired |= h.update(FWD, False, 0.01)
  assert fired is False
  assert h.update(0, False, 0.01) is False


def test_long_fwd_while_disengaged_toggles_once():
  h = StalkFwdHold(hold_s=2.0)
  assert h.update(FWD, False, 0.01) is False
  fired_at = None
  for i in range(250):
    if h.update(FWD, False, 0.01):
      fired_at = i
      break
  assert fired_at is not None
  # ~2.0 s after rising edge: first update is edge (0 elapsed), then 199 more = 2.00s
  assert fired_at == 199
  for _ in range(50):
    assert h.update(FWD, False, 0.01) is False
  # Must release to re-arm
  assert h.update(0, False, 0.01) is False
  assert h.update(FWD, False, 0.01) is False
  fired2 = False
  for _ in range(200):
    fired2 |= h.update(FWD, False, 0.01)
  assert fired2 is True


def test_fwd_while_engaged_cancel_never_toggles():
  h = StalkFwdHold(hold_s=2.0)
  # Rising edge while engaged (cancel) — even if cruise drops mid-hold
  assert h.update(FWD, True, 0.01) is False
  for i in range(250):
    # After ~0.1 s cruise is off (typical cancel), still must not toggle
    engaged = i < 10
    assert h.update(FWD, engaged, 0.01) is False


def test_engage_mid_hold_aborts():
  h = StalkFwdHold(hold_s=2.0)
  assert h.update(FWD, False, 0.01) is False
  for _ in range(50):
    assert h.update(FWD, False, 0.01) is False
  # Engage mid-hold aborts; further hold does not fire
  for _ in range(250):
    assert h.update(FWD, True, 0.01) is False
  # Release and a new long FWD while disengaged can toggle
  h.update(0, False, 0.01)
  h.update(FWD, False, 0.01)
  fired = False
  for _ in range(200):
    fired |= h.update(FWD, False, 0.01)
  assert fired is True


def test_release_rearms():
  h = StalkFwdHold(hold_s=2.0)
  h.update(FWD, False, 0.01)
  for _ in range(200):
    h.update(FWD, False, 0.01)
  # fired; still holding does not re-fire
  assert h.update(FWD, False, 0.01) is False
  h.update(0, False, 0.01)
  assert h.update(FWD, False, 0.01) is False
  fired = False
  for _ in range(200):
    fired |= h.update(FWD, False, 0.01)
  assert fired is True


def test_non_fwd_values_ignored():
  h = StalkFwdHold(hold_s=2.0)
  for val in (0, 2, 4, 8, 16, 32):
    assert h.update(val, False, 0.5) is False


def test_defaults():
  assert FWD == 1
  assert FWD_HOLD_S == 1.23
  assert FWD_HOLD_ENABLED is True


def test_rwd_engaged_path_unchanged():
  """RWD long-pull still toggles once while engaged after FWD sibling lands."""
  assert PULL_HOLD_ENABLED is True
  h = StalkPullHold(hold_s=2.0)
  assert h.update(RWD, True, 0.01) is False
  fired = False
  for _ in range(200):
    fired |= h.update(RWD, True, 0.01)
  assert fired is True


def test_wiring():
  from pathlib import Path
  root = Path(__file__).resolve().parents[4]
  carstate = (root / "selfdrive/car/tesla/carstate.py").read_text()
  assert "StalkFwdHold" in carstate
  assert "stalk_fwd_toggle" in carstate
  assert "if FWD_HOLD_ENABLED:" in carstate
  card = (root / "frogpilot/controls/frogpilot_card.py").read_text()
  assert "_apply_ap1_fwd_hold" in card
  assert "stalk_fwd_toggle" in card
  assert "handle_experimental_mode(" in card
  # No panda / tip-cruise TX for this feature
  safety = (root / "panda/board/safety/safety_tesla.h").read_text()
  assert "TESLA_AP1_FWD_HOLD" not in safety
  assert (root / "selfdrive/car/tesla/stalk_fwd_hold.py").exists()
