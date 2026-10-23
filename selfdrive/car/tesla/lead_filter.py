"""AP1 one-frame radar lead glitch filter (radard, AP1 only).

On a recorded drive leadOne jumped for one radar frame from the lead car
(46 m, 10.6 m/s) to a stationary roadside track (42 m, 0.3 m/s, yRel -7.5) and
the plan stepped -1.60 -> -2.81 m/s^2 (12.5 bar friction thunk). 17:45:28.8: a
one-frame radar-only lead at 1.5 m (modelProb 0) stepped the plan -0.88 -> -1.71.

Rules, per lead slot, at the radard rate (Tesla 20 Hz):
- Camera-confirmed leads are never filtered: model lead prob above the
  detection threshold and the lead's dRel / vLead agree with the model lead.
- A radar-only lead closer than CLOSE_RADAR_ONLY_M that is new this frame is
  dropped for that frame (previous output held, or no lead). If it is still
  there next frame it is accepted (standstill behind a car stays responsive).
- A jump vs the previous output (|dRel - predicted| > JUMP_D_M or |dvLead| >
  JUMP_V_MS) holds the previous lead (dRel extrapolated by vRel*dt) for one
  frame. If the next frame agrees with the new lead, it is accepted, so a real
  cut-in is delayed by at most one frame.
- A lead appearing (no lead -> lead) or disappearing is never delayed.
- Close radar-only point under a confident camera lead (a
  recorded drive): at 4 mph behind a stopped car the camera lead was at 9.5 m
  (prob 1.00) and a stationary radar-only track at 2.06 m (modelProb 0) won
  radard's low-speed override for 2 radar frames after the 1-frame hold above,
  stepping the plan -0.47 -> -1.47 m/s^2 into the regen -> friction handoff
  (brake pedal thunk). A radar-only lead closer than CLOSE_RADAR_ONLY_M that is
  more than CONTRADICT_GAP_M short of a camera lead with prob >=
  CONTRADICT_PROB, while the previous output was a lead, is held for up to
  CONTRADICT_HOLD_FRAMES frames; still there after that, it is accepted.
Pure Python. No CAN, no panda change.
"""

CLOSE_RADAR_ONLY_M = 3.0
JUMP_D_M = 5.0
JUMP_V_MS = 3.0
CONFIRM_D_M = 4.0
CONFIRM_D_FRAC = 0.15
CONFIRM_V_MS = 3.0
CONTRADICT_PROB = 0.9
CONTRADICT_GAP_M = 4.0
CONTRADICT_HOLD_FRAMES = 4


def camera_confirms(lead, model_prob, model_x, model_v, prob_threshold):
  if not lead.get('status') or model_prob <= prob_threshold:
    return False
  if not lead.get('radar', False):
    return True  # vision lead
  d = lead['dRel']
  return (abs(d - model_x) <= max(CONFIRM_D_M, CONFIRM_D_FRAC * d) and
          abs(lead['vLead'] - model_v) <= CONFIRM_V_MS)


def camera_contradicts(lead, model_prob, model_x):
  """A close radar-only lead well short of a confident camera lead."""
  return (bool(lead.get('status')) and bool(lead.get('radar', False)) and lead['dRel'] < CLOSE_RADAR_ONLY_M and
          model_prob >= CONTRADICT_PROB and model_x - lead['dRel'] > CONTRADICT_GAP_M)


def _jumped(a, b, dt):
  pred = a['dRel'] + a.get('vRel', 0.0) * dt
  return abs(b['dRel'] - pred) > JUMP_D_M or abs(b['vLead'] - a['vLead']) > JUMP_V_MS


class Ap1LeadGlitchFilter:
  def __init__(self, dt=0.05):
    self.dt = dt
    self.prev = None      # last published lead dict (status True) or None
    self.pending = None   # candidate held back for one frame
    self.last_held = False
    self.held = 0         # count of glitch frames held (for logging/tests)
    self.contradict = 0   # consecutive frames a camera-contradicted close point was held

  def update(self, lead, model_prob, model_x, model_v, prob_threshold):
    if camera_contradicts(lead, model_prob, model_x):
      self.contradict += 1
      if self.prev is not None and self.contradict <= CONTRADICT_HOLD_FRAMES:
        out = self._hold()
        self.pending = None
        self.last_held = True
        self.prev = dict(out) if out.get('status') else None
        return out
      if self.contradict > CONTRADICT_HOLD_FRAMES:
        # Held long enough and still there: accept it as a real close object.
        self.pending = None
        self.last_held = False
        self.prev = dict(lead)
        return lead
    else:
      self.contradict = 0
    out = lead
    suspect = False
    if lead.get('status') and not camera_confirms(lead, model_prob, model_x, model_v, prob_threshold):
      jump = self.prev is not None and _jumped(self.prev, lead, self.dt)
      # Close radar-only lead that is new this frame (no lead before, or a jump).
      new_close = lead['dRel'] < CLOSE_RADAR_ONLY_M and self.prev is None
      suspect = new_close or jump
    if suspect:
      if self.last_held or (self.pending is not None and not _jumped(self.pending, lead, self.dt)):
        # Seen 2 frames in a row, or we held last frame already: accept.
        # Never hold two frames in a row (max 1 frame / 50 ms delay).
        self.pending = None
        self.last_held = False
      else:
        self.pending = dict(lead)
        out = self._hold()
        self.last_held = True
    else:
      self.pending = None
      self.last_held = False
    self.prev = dict(out) if out.get('status') else None
    return out

  def _hold(self):
    self.held += 1
    if self.prev is None:
      return {'status': False}
    h = dict(self.prev)
    h['dRel'] = h['dRel'] + h.get('vRel', 0.0) * self.dt
    return h
