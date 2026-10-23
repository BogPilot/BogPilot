"""Heat / CPU guard for hevc_streamd (pure state machine, unit-testable, no messaging imports).

States:
  RUN     forwarding packets
  PAUSED  socket kept, packets dropped (tripped by heat or CPU)
  OFF     disabled for the rest of this drive (the process exits; the manager
          starts it again only at the next onroad)

Trip (RUN -> PAUSED) when any holds:
  - maxTempC >= PAUSE_TEMP_C (75)
  - average total CPU over the last CPU_WINDOW_S (10 s) >= PAUSE_CPU_PCT (85)
  - thermalStatus is yellow or worse
Resume (PAUSED -> RUN) only after RESUME_HOLD_S (60 s) continuously below
RESUME_TEMP_C (70) / RESUME_CPU_PCT (75) with thermalStatus green.
The MAX_TRIPS-th trip (3rd pause) goes straight to OFF.
"""
from __future__ import annotations

from collections import deque

PAUSE_TEMP_C = 75.0
PAUSE_CPU_PCT = 85.0
RESUME_TEMP_C = 70.0
RESUME_CPU_PCT = 75.0
RESUME_HOLD_S = 60.0
CPU_WINDOW_S = 10.0
MAX_TRIPS = 3

RUN, PAUSED, OFF = "run", "paused", "off"


class HeatGuard:
  def __init__(self, pause_temp=PAUSE_TEMP_C, pause_cpu=PAUSE_CPU_PCT, resume_temp=RESUME_TEMP_C,
               resume_cpu=RESUME_CPU_PCT, resume_hold_s=RESUME_HOLD_S, cpu_window_s=CPU_WINDOW_S,
               max_trips=MAX_TRIPS):
    self.pause_temp, self.pause_cpu = pause_temp, pause_cpu
    self.resume_temp, self.resume_cpu = resume_temp, resume_cpu
    self.resume_hold_s, self.cpu_window_s, self.max_trips = resume_hold_s, cpu_window_s, max_trips
    self.state = RUN
    self.trips = 0
    self.reason = ""
    self._cpu: deque[tuple[float, float]] = deque()
    self._cool_since: float | None = None

  def cpu_avg(self) -> float:
    return sum(c for _, c in self._cpu) / len(self._cpu) if self._cpu else 0.0

  def update(self, now: float, max_temp_c: float, cpu_pct: float, thermal_hot: bool = False) -> str:
    """Feed one deviceState sample (monotonic seconds). Returns the new state."""
    self._cpu.append((now, float(cpu_pct)))
    while self._cpu and now - self._cpu[0][0] > self.cpu_window_s:
      self._cpu.popleft()
    if self.state == OFF:
      return OFF
    avg = self.cpu_avg()
    if self.state == RUN:
      why = []
      if max_temp_c >= self.pause_temp:
        why.append(f"temp {max_temp_c:.0f}C")
      if avg >= self.pause_cpu:
        why.append(f"cpu {avg:.0f}%")
      if thermal_hot:
        why.append("thermalStatus")
      if why:
        self.trips += 1
        self.reason = ",".join(why)
        self._cool_since = None
        self.state = OFF if self.trips >= self.max_trips else PAUSED
      return self.state
    # PAUSED
    cool = max_temp_c < self.resume_temp and avg < self.resume_cpu and not thermal_hot
    if not cool:
      self._cool_since = None
    elif self._cool_since is None:
      self._cool_since = now
    elif now - self._cool_since >= self.resume_hold_s:
      self.state, self._cool_since = RUN, None
    return self.state

  @property
  def forwarding(self) -> bool:
    return self.state == RUN
