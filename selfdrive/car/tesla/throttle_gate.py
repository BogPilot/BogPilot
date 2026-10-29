"""AP1 allowThrottle smoothing (planner side).

The planner caps the MPC's max accel at the coast accel (about -0.3 m/s^2)
whenever the driving model's gasPressProbs[1] is below 0.4 (allowThrottle).
On the e3574753 drives (routes 0000002f/30/31) that flag flipped 10-46 times
a minute while engaged, often for a single 50 ms plan step. Each flip moved
the MPC max-accel limit from about +2 to -0.3 in one step; the plan collapses
and the planner's aTarget formula (2 * (v(action_t) - v0) / action_t - a0)
turns the collapse into a step of up to -2.2 m/s^2 (route 00000030 18:39:45.69
and 18:39:56.69 ET), which then passes through the regen ramp's -2.0
hard-brake bypass. About half of the plan steps larger than 0.4 m/s^2 on those
routes sat within 0.1 s of a flip.

Ap1ThrottleCap turns the flag into a cut fraction (0 = no cap, 1 = the stock
coast cap) with an asymmetric first-order filter, and the planner blends the
MPC max accel linearly toward the coast cap by that fraction:
  - toward "no throttle" with AP1_THROTTLE_CUT_TAU (0.15 s): a single-frame
    flip moves the cap about a quarter of the way instead of all the way, and
    a sustained "no throttle" reaches 90% of the coast cap in about 0.4 s;
  - back toward throttle with AP1_THROTTLE_RELEASE_TAU (1.5 s), so a model
    that keeps hinting "no throttle" (chatter) keeps most of the cap. The
    e3574753 chatter acted as early caution before a lead appeared; a pure
    debounce or an upstream-style ramped limit dropped it and in replay of
    0000002e 16:09 ET (lead first seen at 86 m) closed the min gap from 34.5
    to about 27 m with -1.8 m/s^2 braking. This filter keeps 33.8 m / -1.17.
At or below MIN_ALLOW_THROTTLE_SPEED (creep) and while the planner is reset
(not engaged) it follows the raw flag at once. Only the max-accel limit is
affected: braking limits, FCW, the lead cost and ACCEL_MIN are unchanged.
AP1 only; other cars keep the stock 0/1 cut.
"""

DT_MDL = 0.05
AP1_THROTTLE_CUT_TAU = 0.15
AP1_THROTTLE_RELEASE_TAU = 1.5
AP1_FINGERPRINT = "TESLA_AP1_MODELS"


def ap1_throttle_gate_for(fingerprint):
  return Ap1ThrottleCap() if str(fingerprint) == AP1_FINGERPRINT else None


def apply_cut(max_accel, coast_limit, cut):
  """Blend the MPC max accel toward the coast limit by the cut fraction (0..1)."""
  if cut <= 0.0:
    return max_accel
  if cut >= 1.0:
    return min(max_accel, coast_limit)
  return min(max_accel, max_accel + cut * (coast_limit - max_accel))


class Ap1ThrottleCap:
  def __init__(self, tau=AP1_THROTTLE_CUT_TAU, tau_release=AP1_THROTTLE_RELEASE_TAU, dt=DT_MDL):
    self.alpha = dt / (tau + dt)
    self.alpha_release = dt / (tau_release + dt)
    self.cut = 0.0

  def update(self, raw_allow, low_speed=False, reset=False):
    """Returns the throttle cut fraction in [0, 1]."""
    raw_cut = 0.0 if raw_allow else 1.0
    if reset or low_speed:
      self.cut = raw_cut
    else:
      alpha = self.alpha if raw_cut > self.cut else self.alpha_release
      self.cut += alpha * (raw_cut - self.cut)
    return self.cut
