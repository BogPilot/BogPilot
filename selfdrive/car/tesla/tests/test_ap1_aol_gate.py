"""AP1 AOL low-speed gate: AOL-only lateral does not start at parking speed or near
full lock, releases below the stop speed, and never changes engaged lateral."""
from openpilot.common.conversions import Conversions as CV
from openpilot.selfdrive.car.tesla.aol_gate import AP1AolGate

MPH = CV.MPH_TO_MS


def run(gate, seq, engaged=False, aol=True):
  return [gate.update(aol, engaged, v * MPH, ang) for v, ang in seq]


def test_parking_creep_never_starts():
  # Three-point turn: under 3.2 mph, wheel swinging to near full lock.
  seq = [(0.7, -306), (1.5, -320), (2.6, -404), (2.0, -387), (1.0, -300), (2.9, 18), (3.1, -26)]
  assert not any(run(AP1AolGate(), seq))


def test_starts_at_speed_with_wheel_centred():
  assert run(AP1AolGate(), [(5.9, 0), (6.0, 0)]) == [False, True]


def test_no_start_past_max_angle():
  g = AP1AolGate()
  assert run(g, [(10, 120), (10, -91), (10, 45)]) == [False, False, True]


def test_hysteresis_and_release():
  g = AP1AolGate()
  assert run(g, [(10, 0), (5, 0), (4.1, 200), (3.9, 0), (5, 0), (6, 0)]) == [True, True, True, False, False, True]


def test_aol_off_is_off():
  g = AP1AolGate()
  assert run(g, [(30, 0)], aol=False) == [False]


def test_engaged_unaffected_and_handoff():
  g = AP1AolGate()
  assert run(g, [(1, 400), (30, 0)], engaged=True) == [True, True]
  # Disengage at road speed: AOL carries on without a gap.
  assert run(g, [(30, 0)]) == [True]
  g2 = AP1AolGate()
  run(g2, [(2, 0)], engaged=True)
  assert run(g2, [(2, 0)]) == [False]
