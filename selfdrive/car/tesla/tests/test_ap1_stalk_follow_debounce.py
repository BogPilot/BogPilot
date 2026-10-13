from openpilot.selfdrive.car.tesla.stalk_follow import StalkFollowDebounce, map_stalk_follow


def run(samples, dt=0.01):
  db = StalkFollowDebounce(dt)
  prev = None
  out = []
  for raw in samples:
    prev = map_stalk_follow(raw, prev)
    out.append(db.update(prev))
  return out


def test_first_ready_applies_immediately():
  out = run([200])
  assert out[0].detent == 7


def test_route58_spin_7_to_1_not_applied_until_stable():
  # 200 for 1 s, 166 for 0.2 s, 0 for 0.3 s, 33 held.
  seq = [200] * 100 + [166] * 20 + [0] * 30 + [33] * 100
  out = run(seq)
  assert all(o.detent == 7 for o in out[:150 + 49])
  assert out[150 + 49].detent == 2  # 0.5 s after 33 appeared
  assert out[-1].follow_s == out[150 + 60].follow_s


def test_quick_bump_and_back_never_changes():
  seq = [200] * 50 + [166] * 30 + [200] * 100
  assert all(o.detent == 7 for o in run(seq))


def test_sna_holds_applied():
  out = run([200] * 10 + [255] * 100)
  assert all(o.detent == 7 for o in out)
