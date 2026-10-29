from openpilot.selfdrive.car.tesla.stalk_follow import SNA, detent_edges, dtr_sample, map_stalk_follow, parse_stalk_raw

# Stock FrogPilot defaults. Traffic is the cruising TRAFFIC_FOLLOW value (1.00s),
# not the 0.50s standstill end of that curve. Intermediates are midpoints.
EXPECTED = (
  (0, 1, "traffic", True, 1.0),
  (33, 2, "closer_than_aggressive", False, 1.125),
  (66, 3, "aggressive", False, 1.25),
  (100, 4, "between_aggressive_and_standard", False, 1.35),
  (133, 5, "standard", False, 1.45),
  (166, 6, "between_standard_and_relaxed", False, 1.6),
  (200, 7, "relaxed", False, 1.75),
)

# Halfway edges. The farther detent owns the edge (raw >= edge).
EDGES = (16.5, 49.5, 83.0, 116.5, 149.5, 183.0)


def _same_profile(decision, other):
  assert decision.ready
  assert decision.detent == other.detent
  assert decision.profile == other.profile
  assert decision.traffic == other.traffic
  assert decision.follow_s == other.follow_s


def test_seven_exact_detents():
  assert detent_edges() == EDGES
  for raw, detent, profile, traffic, follow_s in EXPECTED:
    decision = map_stalk_follow(raw)
    assert decision.ready
    assert decision.valid
    assert decision.raw == raw
    assert decision.detent == detent
    assert decision.profile == profile
    assert decision.traffic is traffic
    assert decision.follow_s == follow_s


def test_sna_holds_previous():
  previous = map_stalk_follow(133)
  held = map_stalk_follow(SNA, previous)
  _same_profile(held, previous)
  assert held.valid
  assert held.raw == 255
  again = map_stalk_follow(None, held)
  _same_profile(again, previous)
  assert again.valid


def test_first_sna_is_not_ready():
  for raw in (255, None):
    decision = map_stalk_follow(raw)
    assert decision.ready is False
    assert decision.valid is False
    assert decision.detent is None
    assert decision.profile is None
    assert decision.follow_s is None
    assert decision.traffic is False


def test_edge_sides_do_not_oscillate():
  state = map_stalk_follow(0)
  for edge in EDGES:
    below = int(edge) if edge == int(edge) else int(edge)
    # integer strictly below the edge, and the first integer at or above it
    if edge == int(edge):
      below = int(edge) - 1
      above = int(edge)
    else:
      below = int(edge)
      above = int(edge) + 1

    stayed = map_stalk_follow(below, state)
    stayed_again = map_stalk_follow(below, stayed)
    assert stayed.detent == state.detent
    assert stayed_again.detent == stayed.detent
    assert stayed_again.profile == stayed.profile == state.profile

    crossed = map_stalk_follow(above, state)
    crossed_again = map_stalk_follow(above, crossed)
    assert crossed.detent == state.detent + 1
    assert crossed.valid
    assert crossed_again.detent == crossed.detent
    assert crossed_again.profile == crossed.profile
    assert crossed_again.follow_s == crossed.follow_s
    state = map_stalk_follow(EXPECTED[crossed.detent - 1][0])

  assert state.detent == 7
  for edge in reversed(EDGES):
    if edge == int(edge):
      above = int(edge)
      below = int(edge) - 1
    else:
      above = int(edge) + 1
      below = int(edge)
    stayed = map_stalk_follow(above, state)
    stayed_again = map_stalk_follow(above, stayed)
    assert stayed.detent == stayed_again.detent == state.detent
    dropped = map_stalk_follow(below, state)
    dropped_again = map_stalk_follow(below, dropped)
    assert dropped.detent == state.detent - 1
    assert dropped.valid
    assert dropped_again.detent == dropped.detent
    assert dropped_again.profile == dropped.profile
    state = map_stalk_follow(EXPECTED[dropped.detent - 1][0])
  assert state.detent == 1


def test_sweep_does_not_skip_or_double_fire():
  state = map_stalk_follow(0)
  upward = []
  for raw in range(0, 201):
    nxt = map_stalk_follow(raw, state)
    if nxt.detent != state.detent:
      assert nxt.detent == state.detent + 1
      assert nxt.valid
      upward.append(raw)
    repeated = map_stalk_follow(raw, nxt)
    assert repeated.detent == nxt.detent
    assert repeated.profile == nxt.profile
    state = nxt
  assert upward == [17, 50, 83, 117, 150, 183]
  assert state.detent == 7
  assert state.profile == "relaxed"

  downward = []
  for raw in range(200, -1, -1):
    nxt = map_stalk_follow(raw, state)
    if nxt.detent != state.detent:
      assert nxt.detent == state.detent - 1
      assert nxt.valid
      downward.append(raw)
    repeated = map_stalk_follow(raw, nxt)
    assert repeated.detent == nxt.detent
    assert repeated.profile == nxt.profile
    state = nxt
  assert downward == [182, 149, 116, 82, 49, 16]
  assert state.detent == 1
  assert state.profile == "traffic"


def test_unknown_raw_holds_last_and_is_invalid():
  closest = map_stalk_follow(0)
  farther = map_stalk_follow(133)
  for previous in (closest, farther):
    for raw in (1, 999):
      held = map_stalk_follow(raw, previous)
      _same_profile(held, previous)
      assert held.valid is False
      assert held.raw == raw
      repeated = map_stalk_follow(raw, held)
      _same_profile(repeated, previous)
      assert repeated.valid is False


def test_first_unknown_does_not_guess():
  decision = map_stalk_follow(1)
  assert decision.ready is False
  assert decision.valid is False
  assert decision.profile is None
  assert decision.detent is None


def test_parse_stalk_raw_does_not_guess_on_first_missing_sample():
  for raw in (None, dtr_sample(0.0, 0), dtr_sample(0, None), dtr_sample(None, 0), dtr_sample(None, 123)):
    decision = parse_stalk_raw(raw, None)
    assert decision.ready is False
    assert decision.valid is False
    assert decision.detent is None
    assert decision.profile is None
    assert decision.follow_s is None


def test_parse_stalk_raw_parser_floats_and_hold():
  # Seen frame: timestamp nonzero. 0.0 is ACC_DIST_1, not the unseen default.
  assert dtr_sample(0.0, 0) is None
  seen = dtr_sample(0.0, 1000)
  decision = parse_stalk_raw(seen, None)
  assert decision.ready
  assert decision.valid
  assert decision.detent == 1
  assert decision.profile == "traffic"
  assert decision.raw == 0

  nxt = parse_stalk_raw(dtr_sample(133.0, 2000), decision)
  assert nxt.detent == 5
  assert nxt.profile == "standard"
  assert nxt.follow_s == 1.45

  held = parse_stalk_raw(dtr_sample(0.0, 0), nxt)
  assert held.ready
  assert held.valid
  assert held.detent == 5
  assert held.profile == "standard"
  assert held.raw is None

  sna = parse_stalk_raw(255.0, None)
  assert sna.ready is False
  assert sna.profile is None

  bad = parse_stalk_raw(33.5, nxt)
  assert bad.detent == 5
  assert bad.valid is False
