"""Client-side display helpers (tools/state_stream/stream_view.py): lead smoothing by radar
track id, model rate/age, gap stats. Pure Python, also runs on MM2 (Python 3.9)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../tools/state_stream"))
import stream_view as sv


def L(d, tid, status=True, handoff=False):
  return [status, d, 0.0, 0.0, 0.0, 0.0, 0.9, tid >= 0, tid, handoff]


def run(sm, seq, dt=0.05):
  shown, evs = [], []
  for i, ld in enumerate(seq):
    s, e = sm.feed(ld, i * dt)
    shown.append(s)
    evs.extend(e)
  return shown, evs


def test_same_track_follows_raw():
  sm = sv.LeadSmoother()
  shown, evs = run(sm, [L(20 + i * 0.1, 7) for i in range(20)])
  assert [s[1] for s in shown] == [20 + i * 0.1 for i in range(20)]
  assert evs == [] and sm.raw_switches == 0 and sm.handoffs == 0


def test_flip_flop_is_held_and_every_raw_switch_logged():
  # parked-lead pattern: radard alternates two tracks every 2-3 frames (18.4 m vs 20.9 m)
  seq = []
  for _ in range(30):
    seq += [L(18.4, 3)] * 2 + [L(20.9, 5)] * 3
  sm = sv.LeadSmoother()
  shown, evs = run(sm, seq)
  assert {s[8] for s in shown} == {3}                 # display never left the first track
  assert sm.handoffs == 0
  assert sm.raw_switches == 59                         # every raw change counted
  assert sum("raw switch" in e for e in evs) == 59     # ... and logged
  assert sm.flickers == 29                             # last candidate is still pending


def test_persistent_new_track_commits_after_hold():
  seq = [L(30.0, 1)] * 10 + [L(25.0, 2)] * 20        # 50 ms frames
  sm = sv.LeadSmoother(hold_s=0.5)
  shown, evs = run(sm, seq)
  tids = [s[8] for s in shown]
  first_new = tids.index(2)
  assert first_new == 10 + 10                          # 0.5 s after the raw switch
  assert all(t == 1 for t in tids[:first_new])
  assert sm.handoffs == 1 and sm.raw_switches == 1
  assert any("HANDOFF t1->t2" in e for e in evs)
  assert sm.recent_handoff(first_new * 0.05)


def test_lost_lead_held_then_cleared_and_reacquired_immediately():
  seq = [L(30.0, 1)] * 5 + [None] * 15 + [L(40.0, 9)]
  sm = sv.LeadSmoother(hold_s=0.5)
  shown, evs = run(sm, seq)
  assert shown[5][8] == 1 and shown[14][8] == 1      # held 0.45 s
  assert shown[15] is None                            # 0.5 s after loss
  assert shown[-1][8] == 9 and sm.handoffs == 0       # acquisition, not a handoff
  assert any("lead lost" in e for e in evs)


def test_old_payload_without_track_id_passes_through():
  sm = sv.LeadSmoother()
  old = [True, 12.0, 0, 0, 0, 0, 0.9, True]
  s, e = sm.feed(old, 0.0)
  assert s is old and e == []
  assert sm.feed([False, 0, 0, 0, 0, 0, 0, False], 0.1)[0] is None


def test_model_rate_and_age():
  mr = sv.ModelRate()
  t = 0.0
  for i in range(40):                                  # 20 Hz state, model every 2nd -> 10 Hz
    mono = {"carState": int(1e9 + i * 50e6)}
    m = {"mono": mono, "meta": {"hz": 20, "modelHz": 10, "modelSrcHz": 19.8}}
    if i % 2 == 0:
      mono["modelV2"] = mono["carState"] - int(30e6)
      m["model"] = {}
    mr.feed(m, t)
    t += 0.05
  mr.roll(2.0)
  assert mr.hz == 10.0
  assert mr.age_ms == 80.0                             # odd frame: 50 ms + 30 ms
  txt = mr.text()
  assert "model=10.0Hz" in txt and "age=80.0ms" in txt and "src=19.8Hz" in txt and "cfg=10" in txt


def test_gap_stats():
  g = sv.GapStats(0.5)
  assert g.mark(0.0) is None and g.mark(0.05) is None
  assert abs(g.mark(0.85) - 0.8) < 1e-9
  assert g.mark(0.9) is None
  assert g.take() == (1, 0.8) and g.take() == (0, 0.0) and g.total == 1


def test_lead_text():
  sm = sv.LeadSmoother()
  assert sv.lead_text(None, sm, 0) == "lead=None"
  assert sv.lead_text(L(18.4, 3), sm, 0) == "lead=18.4 t3"
