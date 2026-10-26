"""BogPilot: keep the on-device build from cooking the comma.

Standalone (stdlib only) so build.py can use it before anything is built.

  build_jobs()          parallel jobs: BOGPILOT_BUILD_JOBS (default 4 on a device), never more than the CPUs
  cpu_temp_c()          hottest CPU sensor in degrees C (cpu*-usr thermal zones on the comma 3X), or None
  ThermalGovernor       hysteresis: pause the build at >= pause_c, resume at <= resume_c, or after max_pause_s
  ThermalPauser         background thread that SIGSTOPs / SIGCONTs the scons process group
"""
from __future__ import annotations

import glob
import os
import signal
import threading
import time
from collections.abc import Callable

THERMAL_GLOB = "/sys/class/thermal/thermal_zone*"


def build_jobs(nproc: int | None, on_device: bool, env: dict | None = None) -> int:
  env = os.environ if env is None else env
  nproc = max(1, nproc or 2)
  try:
    want = int(env.get("BOGPILOT_BUILD_JOBS", "") or 0)
  except ValueError:
    want = 0
  if want <= 0:
    want = 4 if on_device else nproc
  return max(1, min(want, nproc))


def read_zones(pattern: str = THERMAL_GLOB) -> list[tuple[str, float]]:
  zones = []
  for z in sorted(glob.glob(pattern)):
    try:
      with open(os.path.join(z, "type")) as f:
        name = f.read().strip()
      with open(os.path.join(z, "temp")) as f:
        raw = f.read().strip()
      zones.append((name, int(raw) / 1000.0))
    except (OSError, ValueError):
      continue
  return zones


def cpu_temp_c(zones: list[tuple[str, float]] | None = None) -> float | None:
  """Hottest plausible CPU reading. Prefers cpu*-usr zones; ignores limits zones such as lmh-dcvs."""
  zones = read_zones() if zones is None else zones
  ok = [(n, t) for n, t in zones if 0.0 < t < 150.0]
  cpu = [t for n, t in ok if n.startswith("cpu") and n.endswith("-usr")]
  if not cpu:
    cpu = [t for n, t in ok if n.startswith("cpu")]
  return max(cpu) if cpu else None


class ThermalGovernor:
  def __init__(self, pause_c: float = 90.0, resume_c: float = 75.0, max_pause_s: float = 300.0):
    assert resume_c < pause_c
    self.pause_c, self.resume_c, self.max_pause_s = pause_c, resume_c, max_pause_s
    self.paused = False
    self.paused_at = 0.0

  @classmethod
  def from_env(cls, env: dict | None = None) -> ThermalGovernor:
    env = os.environ if env is None else env
    def f(k, d):
      try:
        return float(env.get(k, "") or d)
      except ValueError:
        return d
    pause_c, resume_c = f("BOGPILOT_BUILD_TEMP_PAUSE", 90.0), f("BOGPILOT_BUILD_TEMP_RESUME", 75.0)
    if resume_c >= pause_c:
      resume_c = pause_c - 5.0
    return cls(pause_c, resume_c, f("BOGPILOT_BUILD_MAX_PAUSE", 300.0))

  def step(self, temp_c: float | None, now: float) -> str | None:
    """-> "pause", "resume" or None. No reading never pauses (and resumes a paused build)."""
    if not self.paused:
      if temp_c is not None and temp_c >= self.pause_c:
        self.paused, self.paused_at = True, now
        return "pause"
      return None
    if temp_c is None or temp_c <= self.resume_c or now - self.paused_at >= self.max_pause_s:
      self.paused = False
      return "resume"
    return None


class ThermalPauser:
  """Watches the CPU temperature while scons runs; pauses the whole scons process group when hot."""
  def __init__(self, pgid: int, status: Callable[[str | None], None] | None = None, gov: ThermalGovernor | None = None,
               read_temp: Callable[[], float | None] = cpu_temp_c, interval: float = 5.0,
               kill: Callable[[int, int], None] = os.killpg):
    self.pgid, self.status, self.gov, self.read_temp = pgid, status, gov or ThermalGovernor.from_env(), read_temp
    self.interval, self.kill = interval, kill
    self._stop = threading.Event()
    self._t = threading.Thread(target=self._run, daemon=True)

  def start(self) -> ThermalPauser:
    self._t.start()
    return self

  def _signal(self, sig: int) -> None:
    try:
      self.kill(self.pgid, sig)
    except (ProcessLookupError, PermissionError):
      pass

  def _run(self) -> None:
    while not self._stop.wait(self.interval):
      t = self.read_temp()
      act = self.gov.step(t, time.monotonic())
      if act == "pause":
        self._signal(signal.SIGSTOP)
        print(f"BogPilot: CPU at {t:.0f} C, pausing the build to cool down", flush=True)
        if self.status:
          self.status(f"Cooling down ({t:.0f} C), the build continues by itself")
      elif act == "resume":
        self._signal(signal.SIGCONT)
        print("BogPilot: resuming the build", flush=True)
        if self.status:
          self.status(None)

  def stop(self) -> None:
    self._stop.set()
    if self._t.is_alive():
      self._t.join(timeout=self.interval + 1)
    if self.gov.paused:
      self.gov.paused = False
    self._signal(signal.SIGCONT)  # never leave scons stopped
