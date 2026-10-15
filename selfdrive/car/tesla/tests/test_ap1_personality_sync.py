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
