"""Client-side helpers for StateStream displays (Python 3.9+, stdlib only).

LeadSmoother: steadies the displayed lead using radard's track id (leads.*[8]).
  - same track: display follows the raw lead
  - raw track changes: display HOLDS the previous track until the new id has persisted
    `hold_s` (default 0.5 s), then switches and marks a handoff
  - raw lead disappears: display holds the last lead for `hold_s`, then shows none
  - no lead shown -> lead appears: acquired immediately (no hold)
  Every raw switch is returned as an event so the caller can log it.
  Old payloads without a track id (8-element lead lists) pass through unsmoothed.

ModelRate: honest model rate / age instead of "did this snapshot carry model".
  - hz: model sections received per second (receiver-side count)
  - age_ms: snapshot mono.carState - mono.modelV2 of the last model section (comma clock)
  - meta: the comma's own {"hz", "modelHz", "modelSrcHz"} if present

GapStats: gaps > threshold between consecutive state messages (receiver clock).
"""
from __future__ import annotations

HOLD_S = 0.5
GAP_S = 0.5

# lead list indices (system/state_stream/payload.py _lead)
L_STATUS, L_DREL, L_YREL, L_VREL, L_AREL, L_VLEAD, L_PROB, L_RADAR, L_TID, L_HANDOFF = range(10)


def lead_tid(ld) -> int | None:
  """Track id of a present lead; None if absent; -1 vision-only; 'na' handled by caller."""
  if not ld or not ld[L_STATUS]:
    return None
  return int(ld[L_TID]) if len(ld) > L_TID and ld[L_TID] is not None else -1


class LeadSmoother:
  def __init__(self, hold_s: float = HOLD_S):
    self.hold_s = hold_s
    self.raw_tid: int | None = None     # last raw track id (None = no lead)
    self.raw_d: float | None = None
    self.shown: list | None = None      # displayed lead list (or None)
    self.shown_tid: int | None = None
    self.cand_tid: int | None = None    # raw track waiting to persist
    self.cand_t0 = 0.0
    self.lost_t0: float | None = None
    self.handoff_t: float | None = None  # time of the last committed (displayed) handoff
    self.raw_switches = 0
    self.handoffs = 0
    self.flickers = 0                       # raw switches that reverted before hold_s

  def feed(self, ld, t: float) -> tuple[list | None, list[str]]:
    """ld: raw leads.leadOne list (or None). t: seconds (any monotonic clock).
    Returns (lead list to display or None, list of event strings to log)."""
    ev: list[str] = []
    if ld is not None and len(ld) <= L_TID:      # old payload: no track id, no smoothing
      self.shown = ld if ld and ld[L_STATUS] else None
      return self.shown, ev
    tid = lead_tid(ld)
    d = ld[L_DREL] if tid is not None else None
    if tid is not None and self.raw_tid is not None and tid != self.raw_tid:
      self.raw_switches += 1
      flag = " (comma handoff flag)" if len(ld) > L_HANDOFF and ld[L_HANDOFF] else ""
      ev.append(f"lead raw switch t{self.raw_tid}->t{tid} dRel {self.raw_d}->{d}{flag}")
    self.raw_tid, self.raw_d = tid, d

    if tid is None:                               # raw lead gone
      self.cand_tid = None
      if self.shown is not None:
        if self.lost_t0 is None:
          self.lost_t0 = t
        elif t - self.lost_t0 >= self.hold_s:
          ev.append(f"lead lost t{self.shown_tid} (held {t - self.lost_t0:.2f}s)")
          self.shown, self.shown_tid, self.lost_t0 = None, None, None
      return self.shown, ev
    self.lost_t0 = None

    if self.shown is None:                        # acquisition: immediate
      self.shown, self.shown_tid, self.cand_tid = ld, tid, None
      return self.shown, ev
    if tid == self.shown_tid:                     # same track (or reverted before hold)
      if self.cand_tid is not None:
        self.flickers += 1
        ev.append(f"lead flicker t{self.cand_tid} reverted to t{tid} after {t - self.cand_t0:.2f}s (display held)")
        self.cand_tid = None
      self.shown = ld
      return self.shown, ev
    if tid != self.cand_tid:                      # new candidate track
      self.cand_tid, self.cand_t0 = tid, t
    if t - self.cand_t0 >= self.hold_s:           # persisted: commit the handoff
      self.handoffs += 1
      self.handoff_t = t
      ev.append(f"lead HANDOFF t{self.shown_tid}->t{tid} dRel {self.shown[L_DREL]}->{d} " +
                f"(new track persisted {t - self.cand_t0:.2f}s)")
      self.shown, self.shown_tid, self.cand_tid = ld, tid, None
    return self.shown, ev

  def recent_handoff(self, t: float, within_s: float = 1.0) -> bool:
    return self.handoff_t is not None and t - self.handoff_t <= within_s


class ModelRate:
  def __init__(self):
    self.n = 0
    self.t0: float | None = None
    self.hz: float | None = None
    self.last_model_mono: int | None = None
    self.age_ms: float | None = None
    self.meta: dict | None = None

  def feed(self, m: dict, t: float) -> None:
    if self.t0 is None:
      self.t0 = t
    mono = m.get("mono") or {}
    if "model" in m:
      self.n += 1
      self.last_model_mono = mono.get("modelV2", self.last_model_mono)
    cs = mono.get("carState")
    if cs is not None and self.last_model_mono is not None:
      self.age_ms = round((cs - self.last_model_mono) / 1e6, 1)
    if isinstance(m.get("meta"), dict):
      self.meta = m["meta"]

  def roll(self, t: float) -> None:
    """Call once per display period: closes the rate window."""
    if self.t0 is not None and t > self.t0:
      self.hz = round(self.n / (t - self.t0), 1)
    self.n, self.t0 = 0, t

  def text(self) -> str:
    meta = self.meta or {}
    src = meta.get("modelSrcHz")
    cfg = meta.get("modelHz")
    s = f"model={self.hz if self.hz is not None else '-'}Hz age={self.age_ms if self.age_ms is not None else '-'}ms"
    if cfg is not None:
      s += f" cfg={cfg}"
    if src is not None:
      s += f" src={src}Hz"
    return s


class GapStats:
  def __init__(self, threshold_s: float = GAP_S):
    self.threshold_s = threshold_s
    self.last: float | None = None
    self.count = 0
    self.max_s = 0.0
    self.total = 0

  def mark(self, t: float) -> float | None:
    """Returns the gap length if this message ended a gap > threshold."""
    gap = None
    if self.last is not None and t - self.last > self.threshold_s:
      gap = t - self.last
      self.count += 1
      self.total += 1
      self.max_s = max(self.max_s, gap)
    self.last = t
    return gap

  def take(self) -> tuple[int, float]:
    c, m = self.count, round(self.max_s, 2)
    self.count, self.max_s = 0, 0.0
    return c, m


def lead_text(ld, sm: LeadSmoother, t: float) -> str:
  if not ld:
    return "lead=None"
  tid = ld[L_TID] if len(ld) > L_TID else None
  s = f"lead={ld[L_DREL]}" + (f" t{tid}" if tid is not None else "")
  if sm.recent_handoff(t):
    s += " HANDOFF"
  return s
