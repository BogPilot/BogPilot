"""AP1 stalk -> LongitudinalPersonality write is re-checked and retried until it lands."""
from types import SimpleNamespace

import openpilot.frogpilot.controls.frogpilot_card as fc
from openpilot.selfdrive.car.tesla.stalk_follow import map_stalk_follow
from openpilot.selfdrive.car.tesla.values import CAR


class FakeParams:
  def __init__(self, value, drop=0):
    self.value, self.drop, self.writes = str(value).encode(), drop, []

  def put_nonblocking(self, key, val):
    assert key == "LongitudinalPersonality"
    self.writes.append(val)
    if self.drop:  # simulate a lost async write
      self.drop -= 1
      return
    self.value = val.encode()

  def get(self, key):
    assert key == "LongitudinalPersonality"
    return self.value


def _card(monkeypatch, fake):
  monkeypatch.setattr(fc, "params", fake)
  card = fc.FrogPilotCard.__new__(fc.FrogPilotCard)
  card.car = SimpleNamespace(CP=SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS), CI=SimpleNamespace(CS=SimpleNamespace(stalk_follow=None)))
  card.traffic_mode_enabled = False
  card._ap1_stalk_personality = None
  card._ap1_stalk_detent = None
  card._ap1_personality_target = None
  card._ap1_personality_frames = 0
  return card


def _run(card, raw, frames=1):
  for _ in range(frames):
    card.car.CI.CS.stalk_follow = map_stalk_follow(raw, card.car.CI.CS.stalk_follow)
    card._apply_ap1_stalk()


def test_lost_write_is_retried(monkeypatch):
  fake = FakeParams(2, drop=1)
  card = _card(monkeypatch, fake)
  _run(card, 200, 400)          # relaxed, settles
  fake.drop = 1
  _run(card, 66, 30)            # quick flick to aggressive; first write lost
  assert fake.value == b"0"
  assert fake.writes.count("0") == 2
  n = len(fake.writes)
  _run(card, 66, 400)           # matched: no further writes
  assert len(fake.writes) == n


def test_stale_cache_after_tap(monkeypatch):
  fake = FakeParams(0)
  card = _card(monkeypatch, fake)
  _run(card, 66, 400)           # stalk aggressive
  fake.value = b"2"             # screen tap to relaxed; stalk not moved -> not overridden
  _run(card, 66, 400)
  assert fake.value == b"2"
  _run(card, 100, 1)            # flick through in-between detent and back to aggressive
  _run(card, 66, 20)
  assert fake.value == b"0"


def test_gives_up_so_taps_are_not_fought(monkeypatch):
  fake = FakeParams(2, drop=10**6)
  card = _card(monkeypatch, fake)
  _run(card, 133, 1000)
  assert len(fake.writes) <= fc.AP1_PERSONALITY_MAX_FRAMES // fc.AP1_PERSONALITY_RETRY_FRAMES + 1
  assert card._ap1_personality_target is None


# Quick twists skip detents on CAN (2026-10-20 route 000000a5 seg 5, 20:44:04 ET: DTR 200 -> 166 -> 100 -> 33 never
# reported 66, so LongitudinalPersonality stayed relaxed and the icon stayed on three bars).
def _settled(monkeypatch, raw=200, value=2):
  fake = FakeParams(value)
  card = _card(monkeypatch, fake)
  _run(card, raw, 400)
  return fake, card


def test_quick_7_to_3_sets_aggressive(monkeypatch):
  for seq in ([66], [166, 100, 66], [133, 66]):
    fake, card = _settled(monkeypatch)
    for raw in seq:
      _run(card, raw, 1)
    _run(card, seq[-1], 50)
    assert fake.value == b"0", seq


def test_quick_7_to_2_sets_aggressive(monkeypatch):
  for seq in ([33], [166, 100, 33], [166, 33], [133, 33]):
    fake, card = _settled(monkeypatch)
    for raw in seq:
      _run(card, raw, 1)
    _run(card, 33, 50)
    assert fake.value == b"0", seq


def test_slow_7_to_2_unchanged(monkeypatch):
  fake, card = _settled(monkeypatch)
  for raw in (166, 133, 100, 66, 33):
    _run(card, raw, 30)
  assert fake.value == b"0"


def test_skip_lands_on_last_named_crossed(monkeypatch):
  fake, card = _settled(monkeypatch)
  _run(card, 100, 50)            # 7 -> 4 skips 5: standard
  assert fake.value == b"1"
  fake, card = _settled(monkeypatch, raw=0, value=0)
  _run(card, 166, 50)            # 1 -> 6 skips 3, 5: standard (last crossed)
  assert fake.value == b"1"
  fake, card = _settled(monkeypatch, raw=33, value=0)
  _run(card, 166, 50)            # 2 -> 6 skips 3, 5: standard
  assert fake.value == b"1"


def test_quick_7_to_1_traffic_then_2_is_aggressive(monkeypatch):
  fake, card = _settled(monkeypatch)
  _run(card, 0, 50)              # 7 -> 1 (traffic) skips 5, 3
  assert card.traffic_mode_enabled
  _run(card, 33, 50)
  assert not card.traffic_mode_enabled and fake.value == b"0"


def test_neighbor_steps_and_tap_unchanged(monkeypatch):
  fake, card = _settled(monkeypatch, raw=66, value=0)
  fake.value = b"2"              # screen tap to relaxed
  _run(card, 100, 50)            # 3 -> 4 skips nothing: tap is kept
  assert fake.value == b"2"
  _run(card, 133, 50)            # 4 -> 5 named
  assert fake.value == b"1"
