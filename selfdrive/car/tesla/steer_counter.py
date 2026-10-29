"""AP1 0x488 (DAS_steeringControl) counter continuity with the stock DAS.

AP1 is an interceptor: the panda forwards stock 0x488 from bus 2 to the EPAS
until openpilot sends its own, and starts forwarding again ~100 ms after the
last allowed openpilot frame. Before this module the openpilot counter was
(frame // 2) % 16 on its own 50 Hz clock, unrelated to the stock counter. At a
handover the EPAS could then see the same counter twice or a counter going
backwards. Every disengage in the logs where the stock stream came back with
the counter openpilot had just used (delta 0) latched EPAS code 7
(EAC_ERROR_HIGH_ANGLE_RATE_REQ), and the one engage with a regressing and
repeated counter latched code 7 and the EPB then revoked EAC until power cycle.

Fix (openpilot side only, panda unchanged): while the stock stream is fresh,
send exactly when a stock frame arrives (same 50 Hz phase) with counter
stock + COUNTER_LEAD. At engage the EPAS sees stock s, then s+1 (stock, if it
won the race) and s+2 (ours), or s then s+2. At disengage the last openpilot
counter is s+2 and the first forwarded stock frame is ~5 frames later, so the
stream only moves forward. While openpilot is sending, a counter never
repeats or steps back (it is pushed to last + 1 instead).

If the stock stream goes stale (no frame for STOCK_STALE_FRAMES control
frames) the old frame % 2 cadence is used with the counter continuing from
the last one sent. Logged stock 0x488 arrival gaps: median 20 ms, 99.9th
percentile 30 ms, max 45 ms, so 60 ms only trips on a real stock dropout.
"""
from dataclasses import dataclass

COUNTER_MOD = 16
COUNTER_LEAD = 2
# Stock 0x488 is 50 Hz (every 2nd 100 Hz control frame). 6 frames = 60 ms.
STOCK_STALE_FRAMES = 6
# An openpilot 0x488 within this many frames means openpilot still owns the
# stream, so the next counter must move forward from the last one sent.
OWNED_FRAMES = 6


@dataclass(frozen=True)
class SteerTick:
  send: bool
  counter: int


class Ap1SteerCounterSync:
  def __init__(self):
    self.last_counter = None
    self.last_commit_frame = None
    self.stock_counter = None
    self.frames_since_stock = STOCK_STALE_FRAMES

  @property
  def stock_fresh(self):
    return self.frames_since_stock < STOCK_STALE_FRAMES

  def update(self, stock_counters, frame):
    """stock_counters: stock DAS_steeringControlCounter values received on bus 2
    this control frame (CANParser vl_all), oldest first. Returns a SteerTick.

    The returned counter is only consumed (and remembered) by commit() when a
    0x488 is actually built, so ticks with no steering frame do not advance it.
    """
    stock_counters = list(stock_counters or ())
    if stock_counters:
      self.stock_counter = int(stock_counters[-1]) % COUNTER_MOD
      self.frames_since_stock = 0
      counter = (self.stock_counter + COUNTER_LEAD) % COUNTER_MOD
      send = True
    else:
      self.frames_since_stock = min(self.frames_since_stock + 1, STOCK_STALE_FRAMES)
      if self.stock_fresh:
        return SteerTick(False, self._next_fallback(frame))
      send = frame % 2 == 0
      counter = self._next_fallback(frame)

    if self._owned(frame):
      step = (counter - self.last_counter) % COUNTER_MOD
      if step == 0 or step > COUNTER_MOD // 2:
        counter = (self.last_counter + 1) % COUNTER_MOD
    return SteerTick(send, counter)

  def commit(self, counter, frame=None):
    self.last_counter = int(counter) % COUNTER_MOD
    self.last_commit_frame = frame

  def _owned(self, frame):
    if self.last_counter is None:
      return False
    if self.last_commit_frame is None:
      return True
    return frame - self.last_commit_frame <= OWNED_FRAMES

  def _next_fallback(self, frame):
    if self.last_counter is None:
      return (frame // 2) % COUNTER_MOD
    return (self.last_counter + 1) % COUNTER_MOD
