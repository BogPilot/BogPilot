"""BogPilot: let MPC keep a persistent radar lead when HumanFollowing would ignore it.

Motivation (from a late-brake dig on a HumanFollowing + v9 drive):

* Event: radard published a radar-only lead at ~150 m (vLead ~7.5, vRel ~-14.6,
  modelProb 0.00). With HumanFollowing on and a v9 model, LongitudinalMpc.process_lead
  replaces the radar lead with a fake far lead unless model_lead.prob >
  lead_detection_probability (~0.35), so the plan stayed on cruise for ~1.8 s. The model
  then confirmed at ~123 m and the MPC asked for about -3.50 at once (aEgo ~-3.88).
* Closed-loop counterfactual: forcing the non-HF radar path (MPC sees radar-only)
  cut that peak brake from about -3.50 to about -2.62.

What this adds (ON by default on this test branch; write 0 to disable):

RadarPersistLead: once radarState.leadOne/leadTwo has been a radar-measured lead for
PERSIST_S (gaps up to GAP_S bridged, dRel must stay kinematically consistent),
process_lead uses classic radar kinematic extrapolation even when model prob is still
low. HumanFollowing stays on for the high-prob path (radar-anchored model traj).
Does NOT disable HumanFollowing globally. Does NOT change radard selection, stalk,
CSC, or the optional RadarEarlyBrake bandage.

File-backed toggle for THIS TEST BRANCH (missing/absent = ON):
  # default: missing file = ON (install/checkout of this branch enables the fix)
  echo 0 > /data/params_bogpilot/RadarPersistLead   # OFF / rollback
  echo 1 > /data/params_bogpilot/RadarPersistLead   # ON (same as missing)
  rm /data/params_bogpilot/RadarPersistLead         # ON again (absent = ON on this branch)
Read when openpilot starts (LongitudinalMpc.__init__).
On bogpilot-tesla main and other branches this file is absent and the code is not present.
"""
from __future__ import annotations

from pathlib import Path

PERSIST_S = 0.5         # s, lead must be tracked this long before MPC trusts radar-only
MIN_RADAR_FRAMES = 4    # radar-measured frames needed inside the persistence window
GAP_S = 1.0             # s, dropouts up to this long are bridged (lead extrapolated)
CONSIST_D_M = 10.0      # m, max |dRel - predicted| for a frame to count as the same lead
CONSIST_D_FRAC = 0.10   # or this fraction of dRel, whichever is larger

RADAR_PERSIST_LEAD_PATH = Path("/data/params_bogpilot/RadarPersistLead")
# Tests may override the path via this module attribute.
_radar_persist_lead_path = RADAR_PERSIST_LEAD_PATH


def radar_persist_lead_enabled(stored: str | bytes | None = None) -> bool:
  """THIS BRANCH: missing/empty file = ON. Explicit 0/false/off/no = OFF.

  1/true/on/yes = ON. Anything else that is non-empty and not a yes-value = OFF.
  """
  if stored is None:
    try:
      if not _radar_persist_lead_path.is_file():
        return True  # absent = ON on this test branch
      text = _radar_persist_lead_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
      return True
  else:
    text = (stored.decode() if isinstance(stored, bytes) else stored).strip()
  if text == "":
    return True  # empty file = ON (same as missing)
  if text.lower() in ("0", "false", "off", "no"):
    return False
  return text.lower() in ("1", "true", "on", "yes")


def _get(lead, key, default=0.0):
  if lead is None:
    return default
  if isinstance(lead, dict):
    return lead.get(key, default)
  return getattr(lead, key, default)


class RadarPersistLeadTracker:
  """Tracks one radar lead slot (leadOne or leadTwo) for multi-frame persistence."""

  def __init__(self, dt: float = 0.05):
    self.dt = dt
    self.reset()

  def reset(self):
    self.track = None        # dict: dRel, vRel of the lead being followed
    self.first_t = 0.0
    self.last_seen_t = 0.0
    self.frames = 0
    self.t = 0.0

  @property
  def persistent(self) -> bool:
    return (
      self.track is not None
      and self.frames >= MIN_RADAR_FRAMES
      and self.last_seen_t - self.first_t >= PERSIST_S - 1e-6
    )

  def _consistent(self, d_rel: float) -> bool:
    if self.track is None:
      return False
    pred = self.track["dRel"]
    return abs(d_rel - pred) <= max(CONSIST_D_M, CONSIST_D_FRAC * d_rel)

  def update(self, lead) -> bool:
    """Advance one planner tick. Returns True when radar-only may feed MPC."""
    self.t += self.dt

    # Extrapolate the followed lead one step for consistency checks / bridging.
    if self.track is not None:
      self.track["dRel"] += self.track["vRel"] * self.dt

    status = bool(_get(lead, "status", False))
    radar = bool(_get(lead, "radar", False))
    if status and radar:
      d_rel = float(_get(lead, "dRel"))
      v_rel = float(_get(lead, "vRel"))
      if not self._consistent(d_rel):
        self.first_t = self.t
        self.frames = 0
      self.track = {"dRel": d_rel, "vRel": v_rel}
      self.last_seen_t = self.t
      self.frames += 1
    elif self.track is not None and self.t - self.last_seen_t > GAP_S:
      self.track = None
      self.frames = 0

    # During a short dropout after persistence, keep returning True so MPC does not
    # snap back to the fake far lead for a single missing radar frame.
    if self.persistent:
      return True
    if (
      self.track is not None
      and self.frames >= MIN_RADAR_FRAMES
      and self.t - self.last_seen_t <= GAP_S
      and (self.last_seen_t - self.first_t) >= PERSIST_S - 1e-6
    ):
      return True
    return False


def hf_v9_should_use_radar(
  model_prob: float,
  lead_detection_probability: float,
  radar_status: bool,
  persist_ok: bool,
) -> str:
  """Decide which lead source process_lead should use under HumanFollowing + v9.

  Returns one of: "model_radar" | "radar_persist" | "fake_far".
  """
  if model_prob > lead_detection_probability and radar_status:
    return "model_radar"
  if persist_ok and radar_status:
    return "radar_persist"
  return "fake_far"
