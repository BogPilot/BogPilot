import pytest

import openpilot.frogpilot.controls.lib.radar_persist_lead as rpl
from openpilot.frogpilot.controls.lib.radar_persist_lead import (
  RadarPersistLeadTracker,
  hf_v9_should_use_radar,
  radar_persist_lead_enabled,
)

DT = 0.05


def lead(d, v_rel=-10.0, status=True, radar=True):
  return {"status": status, "radar": radar, "dRel": d, "vRel": v_rel}


def run_track(tracker, d0=150.0, v_rel=-14.0, frames=60, drop=lambda i: False, radar=True):
  out, d = [], d0
  for i in range(frames):
    if drop(i):
      l = lead(0, 0, status=False)
    else:
      l = lead(d, v_rel, radar=radar)
    out.append(tracker.update(l))
    d += v_rel * DT
  return out


class TestParam:
  def test_missing_file_is_on(self, tmp_path, monkeypatch):
    # This test branch: absent param = ON for install convenience.
    monkeypatch.setattr(rpl, "_radar_persist_lead_path", tmp_path / "RadarPersistLead")
    assert radar_persist_lead_enabled() is True

  @pytest.mark.parametrize(
    "text,expected",
    [("1", True), ("true\n", True), ("ON", True), ("yes", True), ("0", False), ("", True), ("garbage", False),
     ("false", False), ("off", False), ("no", False)],
  )
  def test_file_values(self, tmp_path, monkeypatch, text, expected):
    p = tmp_path / "RadarPersistLead"
    p.write_text(text)
    monkeypatch.setattr(rpl, "_radar_persist_lead_path", p)
    assert radar_persist_lead_enabled() is expected

  def test_stored_bytes(self):
    assert radar_persist_lead_enabled(b"1") and not radar_persist_lead_enabled(b"0")
    assert radar_persist_lead_enabled(b"") is True


class TestDecision:
  def test_high_model_prob_uses_model_radar(self):
    assert hf_v9_should_use_radar(0.49, 0.35, True, False) == "model_radar"

  def test_low_prob_without_persist_fakes(self):
    assert hf_v9_should_use_radar(0.0, 0.35, True, False) == "fake_far"

  def test_low_prob_with_persist_uses_radar(self):
    # E2 window: modelProb 0.00, radar status True, persist after 0.5 s
    assert hf_v9_should_use_radar(0.0, 0.35, True, True) == "radar_persist"

  def test_persist_without_radar_status_still_fakes(self):
    assert hf_v9_should_use_radar(0.0, 0.35, False, True) == "fake_far"

  def test_model_wins_over_persist(self):
    assert hf_v9_should_use_radar(0.9, 0.35, True, True) == "model_radar"


class TestTracker:
  def test_needs_persistence(self):
    t = RadarPersistLeadTracker(DT)
    out = run_track(t, frames=int(rpl.PERSIST_S / DT))
    assert all(x is False for x in out)

  def test_becomes_persistent_after_window(self):
    t = RadarPersistLeadTracker(DT)
    out = run_track(t, frames=int(rpl.PERSIST_S / DT) + 2)
    assert out[-1] is True
    assert t.persistent

  def test_vision_only_ignored(self):
    t = RadarPersistLeadTracker(DT)
    out = run_track(t, frames=60, radar=False)
    assert all(x is False for x in out)

  def test_flicker_bridged(self):
    t = RadarPersistLeadTracker(DT)
    # every other 4-frame block missing (0.2 s gaps < GAP_S)
    out = run_track(t, frames=60, drop=lambda i: (i // 4) % 2 == 1)
    assert any(out)
    assert out[-1] is True

  def test_long_dropout_releases(self):
    t = RadarPersistLeadTracker(DT)
    run_track(t, frames=40)
    assert t.persistent
    for _ in range(int((rpl.GAP_S + 0.2) / DT)):
      t.update(lead(0, 0, status=False))
    assert t.track is None and not t.persistent

  def test_inconsistent_jump_restarts(self):
    t = RadarPersistLeadTracker(DT)
    run_track(t, d0=60.0, frames=20)
    assert t.persistent
    t.update(lead(150.0, -15.0))
    assert not t.persistent

  def test_e2_like_acquisition(self):
    """E2: radar at 150 m for ~1.8 s with modelProb=0 — after 0.5 s persist should be True."""
    t = RadarPersistLeadTracker(DT)
    d, v_rel = 150.0, -14.6
    ok_at = None
    for i in range(int(1.8 / DT)):
      if t.update(lead(d, v_rel)):
        ok_at = i * DT
        break
      d += v_rel * DT
    assert ok_at is not None
    assert 0.45 <= ok_at <= 0.65
