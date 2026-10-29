#!/usr/bin/env python3
"""Replay recorded AP1 rlogs through BogPilot's panda safety code (libpanda).

Feeds every received CAN frame (rx) and every frame openpilot asked the panda
to send (sendcan) through the Tesla safety mode (param 10 = AP1 | long control)
in tests/libpanda, in log order, with the panda timer driven by logMonoTime.

It flags:
  * openpilot TX frames the safety code would block, with a decoded reason;
  * controls_allowed transitions (engage / disengage) and the rx frame or
    rx-check timeout that caused them;
  * stock bus-2 DAS frames (0x488, 0x2b9, 0x399, 0x389, 0x239) that the panda
    would forward to or drop from the chassis bus, over time, including stock
    frames dropped while openpilot is not engaged (the 2026-10-04 bug class).

When the log has them, the real panda's results are shown next to the replay:
pandaStates.controlsAllowed, rejected-TX echoes (src 192+bus) and forwarded
echoes (src 128 copies of bus-2 frames, tres/H7).

Usage (from the repo root):
  python panda/tests/safety_replay/replay_ap1.py <rlog | rlog.zst | rlog.bz2 | dir> [...] [--summary]
  python panda/tests/safety_replay/replay_ap1.py /data/media/0/realdata/<route>--* --summary
  python panda/tests/safety_replay/replay_ap1.py <dir> --safety-rev 6379b77c   # old safety from git
  python panda/tests/safety_replay/replay_ap1.py <dir> --rx-only               # ignore openpilot TX

Exit code: 0 no FAIL flags, 1 FAIL flags, 2 bad input.
Offline tool. A clean replay is not a road validation.
"""
from __future__ import annotations

import argparse
import bz2
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PANDA_DIR = REPO_ROOT / "panda"
if str(REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(REPO_ROOT))

SAFETY_TESLA = 10
AP1_PARAM = 10  # FLAG_TESLA_AP1 (8) | FLAG_TESLA_LONG_CONTROL (2)
ALT_EXP_DISABLE_DISENGAGE_ON_GAS = 1

STOCK_ADDRS = {
  0x488: "DAS_steeringControl",
  0x2b9: "DAS_control",
  0x399: "AutopilotStatus",
  0x389: "DAS_status2",
  0x239: "DAS_lanes",
}
ACTUATOR_ADDRS = (0x488, 0x2b9)
CLUSTER_ADDRS = (0x399, 0x389, 0x239)
# Substitution windows in safety_tesla.h (used only to label why a frame was dropped).
SUBSTITUTE_TIMEOUT_S = {0x488: 0.100, 0x2b9: 0.050, 0x399: 0.750, 0x389: 0.750, 0x239: 0.150}

# (addr, bus) -> length, TESLA_AP1_TX_MSGS
AP1_TX = {(0x488, 0): 4, (0x45, 0): 8, (0x45, 2): 8, (0x2b9, 0): 8, (0x349, 0): 8,
          (0x399, 0): 8, (0x389, 0): 8, (0x239, 0): 8}
TX_NAMES = {0x488: "DAS_steeringControl", 0x45: "STW_ACTN_RQ", 0x2b9: "DAS_control", 0x349: "Hold clear",
            0x399: "AutopilotStatus", 0x389: "DAS_status2", 0x239: "DAS_lanes"}
# tesla_rx_checks: (addr, bus, Hz, name)
RX_CHECKS = [(0x2b9, 2, 25, "DAS_control"), (0x370, 0, 25, "EPAS_sysStatus"), (0x108, 0, 100, "DI_torque1"),
             (0x118, 0, 100, "DI_torque2"), (0x20a, 0, 50, "BrakeMessage"), (0x368, 0, 10, "DI_state"),
             (0x318, 0, 10, "GTW_carState")]
MAX_MISSED_MSGS = 10
CRUISE_STATES = {0: "OFF", 1: "STANDBY", 2: "ENABLED", 3: "STANDSTILL", 4: "OVERRIDE", 5: "FAULT",
                 6: "PRE_FAULT", 7: "PRE_CANCEL"}
CRUISE_ENGAGED = {2, 3, 4, 6, 7}
STEER_TYPES = {0: "NONE", 1: "ANGLE_CONTROL", 2: "RESERVED", 3: "DISABLED"}
# TESLA_AP1_STEERING_LIMITS (Tinkla) and TESLA_LONG_LIMITS
AP1_RATE_BP = (2., 7., 17.)
AP1_RATE_UP = (8., 4., 2.5)
AP1_RATE_DOWN = (9., 5., 4.5)
ACCEL_RAW_MIN, ACCEL_RAW_MAX, ACCEL_RAW_INACTIVE = 287, 425, 375

LOG_NAME_RE = re.compile(r"^(rlog|.*\.rlog)(\.(zst|bz2))?$")
NUM_RE = re.compile(r"[-+]?[0-9]+(\.[0-9]+)?")
TICK_PERIOD_S = 1.0     # panda calls safety_tick at 1 Hz
GAP_S = 2.0             # forward jump treated as a log gap
RESET_BACK_S = 1.0      # backward jump treated as a new recording (mono time reset)
MISMATCH_MIN_S = 0.5    # replay vs pandaStates controlsAllowed disagreement shorter than this is ignored
TIMED_EVENTS = ("can", "sendcan", "pandaStates", "controlsState")
RELAY_END_GRACE_S = 1.0  # relay opens at ignition off a few frames before pandaStates shows the mode change


# ---------------------------------------------------------------- log reading

def _natural_key(p: Path):
  return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", str(p))]


def find_logs(inputs: list[str], allow_qlog: bool = False) -> list[Path]:
  files: list[Path] = []
  name_re = re.compile(r"^(rlog|qlog|.*\.rlog|.*\.qlog)(\.(zst|bz2))?$") if allow_qlog else LOG_NAME_RE
  for inp in inputs:
    p = Path(inp)
    if p.is_file():
      files.append(p)
    elif p.is_dir():
      files.extend(sorted((f for f in p.rglob("*") if f.is_file() and name_re.match(f.name)), key=_natural_key))
    else:
      raise FileNotFoundError(inp)
  return files


def load_log_schema():
  try:
    from cereal import log
    return log
  except Exception:
    import capnp
    capnp.remove_import_hook()
    cereal = REPO_ROOT / "cereal"
    return capnp.load(str(cereal / "log.capnp"), imports=[str(cereal)])


def read_log_bytes(path: Path) -> bytes:
  with open(path, "rb") as f:
    dat = f.read()
  if dat[:4] == b"\x28\xb5\x2f\xfd":
    import zstandard
    with zstandard.ZstdDecompressor().stream_reader(dat) as r:
      dat = r.read()
  elif dat[:3] == b"BZh":
    dat = bz2.decompress(dat)
  return dat


def iter_events(path: Path, log, warnings: list[str]):
  dat = read_log_bytes(path)
  try:
    yield from log.Event.read_multiple_bytes(dat)
  except Exception as e:  # truncated final segment
    warnings.append(f"{segment_label(path)}: stopped reading early ({type(e).__name__}); log truncated?")


def segment_label(path: Path) -> str:
  return path.parent.name if path.name.startswith(("rlog", "qlog")) else path.name


# ---------------------------------------------------------------- libpanda

def _build_libpanda(panda_root: Path, out: Path) -> Path:
  libdir = panda_root / "tests" / "libpanda"
  out.parent.mkdir(parents=True, exist_ok=True)
  obj = out.with_suffix(".os")
  cflags = ["-nostdlib", "-fno-builtin", "-std=gnu11", "-Wfatal-errors", "-Wno-pointer-to-int-cast", "-fPIC",
            f"-I{libdir}", f"-I{panda_root / 'board'}"]
  subprocess.run(["gcc", "-o", str(obj), "-c", *cflags, str(libdir / "panda.c")], check=True)
  subprocess.run(["gcc", "-o", str(out), "-shared", str(obj)], check=True)
  return out


def _newest_source_mtime(panda_root: Path) -> float:
  srcs = [f for d in ("board", "tests/libpanda") for f in (panda_root / d).rglob("*")
          if f.suffix in (".h", ".c") and "obj" not in f.parts]
  return max(f.stat().st_mtime for f in srcs)


def ensure_default_libpanda() -> Path:
  """tests/libpanda/libpanda.so, rebuilt if missing or older than any panda/board source.

  A stale .so silently replays old safety code (seen 2026-10-06), so this is not optional.
  """
  so = PANDA_DIR / "tests" / "libpanda" / "libpanda.so"
  if not so.exists() or so.stat().st_mtime < _newest_source_mtime(PANDA_DIR):
    print(f"building {so.relative_to(REPO_ROOT)} (missing or older than panda/board; " +
          "same flags as tests/libpanda/SConscript)", file=sys.stderr)
    _build_libpanda(PANDA_DIR, so)
  return so


def libpanda_for_rev(rev: str) -> Path:
  sha = subprocess.check_output(["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", f"{rev}^{{commit}}"],
                                text=True).strip()
  cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "bogpilot-safety-replay" / sha[:12]
  so = cache / "libpanda.so"
  if not so.exists():
    with tempfile.TemporaryDirectory() as td:
      arch = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", sha, "panda/board", "panda/tests/libpanda"],
                            check=True, capture_output=True).stdout
      subprocess.run(["tar", "-x", "-C", td], input=arch, check=True)
      print(f"building libpanda from {sha[:12]} -> {so}", file=sys.stderr)
      _build_libpanda(Path(td) / "panda", so)
  return so


def load_libpanda(path: Path | None):
  default = ensure_default_libpanda()
  from panda.tests.libpanda import libpanda_py
  if path is None or Path(path).resolve() == default.resolve():
    return libpanda_py, libpanda_py.libpanda
  return libpanda_py, libpanda_py.ffi.dlopen(str(path))


# ---------------------------------------------------------------- decoding

def _interp(x, xp, fp):
  if x <= xp[0]:
    return fp[0]
  for i in range(1, len(xp)):
    if x <= xp[i]:
      return fp[i - 1] + (fp[i] - fp[i - 1]) * (x - xp[i - 1]) / (xp[i] - xp[i - 1])
  return fp[-1]


def accel_from_raw(raw: int) -> float:
  return raw * 0.04 - 15.


@dataclass
class TxContext:
  controls: bool
  long_allowed: bool
  relay: bool
  desired_last: int
  meas_min: int
  meas_max: int
  speed_min: float
  stock_aeb: bool
  gas: bool


def explain_tx_block(addr: int, bus: int, dat: bytes, c: TxContext) -> str:
  if c.relay:
    return "relay malfunction latched (all TX blocked)"
  if (addr, bus) not in AP1_TX:
    return f"{addr:#x} on bus {bus} is not in the AP1 TX list"
  if len(dat) != AP1_TX[(addr, bus)]:
    return f"length {len(dat)} (AP1 TX list wants {AP1_TX[(addr, bus)]})"
  if addr == 0x488:
    desired = (((dat[0] & 0x7F) << 8) | dat[1]) - 16384
    typ = dat[2] >> 6
    enabled = typ not in (0, 3)
    if enabled and not c.controls:
      return f"steering type {typ} ({STEER_TYPES[typ]}) while controls not allowed"
    if enabled:
      v = c.speed_min - 1.
      up = int(_interp(v, AP1_RATE_BP, AP1_RATE_UP) * 10 + 1.)
      down = int(_interp(v, AP1_RATE_BP, AP1_RATE_DOWN) * 10 + 1.)
      last = c.desired_last
      hi = last + (up if last > 0 else down)
      lo = last - (down if last >= 0 else up)
      if desired > hi or desired < lo:
        return (f"angle rate: {desired / 10:+.1f} deg after {last / 10:+.1f} deg " +
                f"(step {abs(desired - last) / 10:.1f}, allowed {lo / 10:+.1f}..{hi / 10:+.1f} at {c.speed_min:.1f} m/s)")
    else:
      if desired > c.meas_max + 1 or desired < c.meas_min - 1:
        return (f"type {typ} ({STEER_TYPES[typ]}) frame at {desired / 10:+.1f} deg, measured " +
                f"{c.meas_min / 10:+.1f}..{c.meas_max / 10:+.1f} deg (inactive frames must match the wheel)")
  elif addr == 0x45:
    lever = dat[0] & 0x3F
    if lever != 1:
      return f"stalk request {lever} (only cancel = 1 may be sent)"
  elif addr == 0x2b9:
    if (dat[2] & 0x03) != 0:
      return f"AEB event {dat[2] & 0x03} set by openpilot"
    if c.stock_aeb:
      return "stock AEB active on bus 2 (openpilot DAS_control not allowed)"
    raw_max = ((dat[6] & 0x1F) << 4) | (dat[5] >> 4)
    raw_min = ((dat[5] & 0x0F) << 5) | (dat[4] >> 3)
    for name, raw in (("accelMin", raw_min), ("accelMax", raw_max)):
      if raw == ACCEL_RAW_INACTIVE:
        continue
      if not c.long_allowed:
        why = "controls not allowed" if not c.controls else "gas pressed"
        return f"{name} {accel_from_raw(raw):+.2f} m/s^2 while longitudinal not allowed ({why}); only 0.00 allowed"
      if raw < ACCEL_RAW_MIN or raw > ACCEL_RAW_MAX:
        return f"{name} {accel_from_raw(raw):+.2f} m/s^2 outside [-3.52, +2.00]"
  elif addr == 0x349:
    if any(dat):
      return f"0x349 must be all zero (Hold clear), got {dat.hex()}"
  elif addr == 0x399:
    state = dat[0] & 0x0F
    if 3 <= state <= 5 and not c.controls:
      return f"AutopilotStatus state {state} (active) while controls not allowed"
  return "blocked by the safety hook (reason not decoded)"


# ---------------------------------------------------------------- report

@dataclass
class FwdRun:
  state: str          # "forwarded" | "blocked"
  t0: float
  t1: float
  n: int = 0
  tags: Counter = field(default_factory=Counter)

  def as_dict(self):
    return {"state": self.state, "t0": round(self.t0, 3), "t1": round(self.t1, 3), "n": self.n, "tags": dict(self.tags)}


@dataclass
class Report:
  files: list[str] = field(default_factory=list)
  libpanda: str = ""
  software: list[dict] = field(default_factory=list)
  param: int | None = None
  alt_exp: int = 0
  recordings: int = 0
  active_spans: list[dict] = field(default_factory=list)
  notes: list[str] = field(default_factory=list)
  warnings: list[str] = field(default_factory=list)
  # tx
  tx_total: Counter = field(default_factory=Counter)
  tx_blocked: Counter = field(default_factory=Counter)
  tx_blocked_controls: Counter = field(default_factory=Counter)
  tx_block_reasons: Counter = field(default_factory=Counter)
  tx_examples: list[dict] = field(default_factory=list)
  real_tx_rejected: Counter = field(default_factory=Counter)
  real_tx_rejected_matched: int = 0
  # rx / controls
  rx_total: int = 0
  rx_invalid: Counter = field(default_factory=Counter)
  transitions: list[dict] = field(default_factory=list)
  op_transitions: list[dict] = field(default_factory=list)
  controls_samples: int = 0
  controls_agree: int = 0
  controls_mismatch: list[dict] = field(default_factory=list)
  relay_malfunction: list[dict] = field(default_factory=list)
  rx_check_invalid_ticks: int = 0
  # forwarding
  fwd_runs: dict = field(default_factory=lambda: defaultdict(list))
  fwd_counts: dict = field(default_factory=lambda: defaultdict(Counter))
  fwd_disengaged_blocked: Counter = field(default_factory=Counter)
  fwd_disengaged_examples: list[dict] = field(default_factory=list)
  stock_aeb_blocked: int = 0
  real_fwd: Counter = field(default_factory=Counter)
  real_fwd_sim_blocked: Counter = field(default_factory=Counter)
  real_echo_seen: bool = False
  flags: list[tuple[str, str]] = field(default_factory=list)

  def as_dict(self):
    d = {k: v for k, v in self.__dict__.items() if k not in ("fwd_runs", "fwd_counts")}
    for k, v in list(d.items()):
      if isinstance(v, Counter):
        d[k] = {(f"{kk:#x}" if isinstance(kk, int) else str(kk)): vv for kk, vv in v.items()}
    d["fwd_runs"] = {f"{a:#x}": [r.as_dict() for r in runs] for a, runs in self.fwd_runs.items()}
    d["fwd_counts"] = {f"{a:#x}": dict(c) for a, c in self.fwd_counts.items()}
    d["flags"] = [{"level": lvl, "msg": m} for lvl, m in self.flags]
    return d


# ---------------------------------------------------------------- replay

class Ap1SafetyReplay:
  def __init__(self, lp, lib, param: int | None = None, alt_exp: int | None = None, follow_panda: bool = True,
               rx_only: bool = False, grace_actuator: float = 1.0, grace_cluster: float = 5.0,
               max_examples: int = 10):
    self.lp, self.lib = lp, lib
    self.param_override, self.alt_override = param, alt_exp
    self.follow_panda, self.rx_only = follow_panda, rx_only
    self.grace = {a: (grace_cluster if a in CLUSTER_ADDRS else grace_actuator) for a in STOCK_ADDRS}
    self.max_examples = max_examples
    self.r = Report()
    self.log = load_log_schema()

    self.active = False
    self.rec_t0 = None
    self.last_t = None
    self.last_tick = 0.
    self.active_t0 = 0.
    self.ticks_since_active = 0
    self.relay_armed = False
    self.cp_param = None
    self.cp_alt = 0
    self.real_model = None
    self.real_param = None
    self.real_controls = None
    self.seen_panda_states = False
    self.can_t_first = None
    self.mismatch_t0 = None
    self.op_enabled = None
    self.disengaged_since = 0.
    self.controls = False
    self.relay = False
    # python mirrors of a few car signals (labels only)
    self.cruise = None
    self.gas = False
    self.brake = False
    self.speed = 0.
    self.stock_aeb = False
    self.rx_last = {}
    self.op_last_tx = {}
    self.recent_bus2 = {}
    self.sim_tx_blocked = {}
    self.last_engage_t = None
    self.recent_sendcan = {}

  # -- helpers
  def _t(self, mono_ns: int) -> float:
    return (mono_ns - self.rec_t0) / 1e9

  def _where(self, t: float) -> str:
    return f"R{self.r.recordings} {t:8.2f}s [{self.seg}]" if self.r.recordings > 1 else f"{t:8.2f}s [{self.seg}]"

  def _param(self) -> int:
    for p in (self.param_override, self.real_param, self.cp_param):
      if p is not None:
        return int(p)
    return AP1_PARAM

  def _alt(self) -> int:
    return int(self.alt_override if self.alt_override is not None else self.cp_alt)

  def activate(self, t: float, why: str, seed_controls: bool | None = None):
    lib = self.lib
    param = self._param()
    assert lib.set_safety_hooks(SAFETY_TESLA, param) == 0, f"safety mode tesla param {param} not registered"
    lib.set_alternative_experience(self._alt())
    if seed_controls:
      lib.set_controls_allowed(True)
      lib.set_cruise_engaged_prev(True)
    self.active = True
    self.r.param = param
    self.r.alt_exp = self._alt()
    self.active_t0 = t
    self.last_tick = t
    self.ticks_since_active = 0
    self.relay_armed = False
    self.controls = bool(lib.get_controls_allowed())
    self.disengaged_since = t
    self.relay = False
    self.rx_last = {}
    self.op_last_tx = {}
    self.r.active_spans.append({"rec": self.r.recordings, "seg": self.seg, "t0": round(t, 3), "t1": None, "why": why})

  def deactivate(self, t: float, why: str):
    if self.active:
      rm = self.r.relay_malfunction
      if rm and why.startswith("pandaStates: panda left tesla") and (t - rm[-1]["t"]) < RELAY_END_GRACE_S \
         and rm[-1]["rec"] == self.r.recordings:
        x = rm.pop()
        self.r.notes.append(f"{x['where']}: stock {x['addr']} on bus {x['bus']} {t - x['t']:.2f} s before the panda left " +
                            "tesla mode (relay opening at ignition off); not counted as a relay malfunction")
      self.r.active_spans[-1]["t1"] = round(t, 3)
      self.r.active_spans[-1]["end"] = why
      self._close_mismatch(t)
    self.active = False

  def _close_mismatch(self, t: float):
    if self.mismatch_t0 is not None:
      dur = t - self.mismatch_t0[0]
      if dur >= MISMATCH_MIN_S:
        self.r.controls_mismatch.append({"where": self._where(self.mismatch_t0[0]), "dur_s": round(dur, 2),
                                         "sim": self.mismatch_t0[1], "panda": not self.mismatch_t0[1]})
      self.mismatch_t0 = None

  def _set_timer(self, mono_ns: int):
    us = mono_ns // 1000
    if self._timer_us is not None and us < self._timer_us:
      us = self._timer_us
    self._timer_us = us
    self.lib.set_timer(us & 0xFFFFFFFF)

  def _flag_controls_change(self, t: float, cause: str):
    now = bool(self.lib.get_controls_allowed())
    if now != self.controls:
      self.r.transitions.append({"where": self._where(t), "t": round(t, 3), "event": "ENGAGE" if now else "DISENGAGE",
                                 "cause": cause})
      self.controls = now
      if not now:
        self.disengaged_since = t
      else:
        self.last_engage_t = t
    if self.controls:
      self.disengaged_since = t

  # -- rx cause labels
  def _rx_cause(self, addr: int, bus: int, dat: bytes, prev_cruise, prev_gas, prev_brake, engaged_now: bool) -> str:
    if addr == 0x368 and bus == 0 and len(dat) > 1:
      cs = dat[1] >> 4
      p = CRUISE_STATES.get(prev_cruise, prev_cruise) if prev_cruise is not None else "?"
      return f"DI_cruiseState {p} -> {CRUISE_STATES.get(cs, cs)}"
    if self.gas and not prev_gas and not engaged_now:
      return "gas pressed (DI_torque1 DI_pedalPos rising edge)"
    if self.brake and not engaged_now:
      return ("brake pressed (BrakeMessage driverBrakeStatus)" if not prev_brake
              else f"brake held while moving ({self.speed:.1f} m/s)")
    return f"rx {addr:#x} bus {bus}"

  def _update_mirrors(self, addr: int, bus: int, dat: bytes):
    if bus == 0:
      if addr == 0x118 and len(dat) >= 4:
        self.speed = (((((dat[3] & 0x0F) << 8) | dat[2]) * 0.05) - 25) * 0.447
      elif addr == 0x108 and len(dat) >= 7:
        self.gas = dat[6] != 0
      elif addr == 0x20a and len(dat) >= 1:
        self.brake = ((dat[0] & 0x0C) >> 2) != 1
      elif addr == 0x368 and len(dat) >= 2:
        self.cruise = dat[1] >> 4
    elif bus == 2 and addr == 0x2b9 and len(dat) >= 3:
      self.stock_aeb = (dat[2] & 0x03) == 1

  # -- event handlers
  def handle_rx(self, t: float, addr: int, bus: int, dat: bytes):
    lp, lib = self.lp, self.lib
    prev = (self.cruise, self.gas, self.brake)
    self._update_mirrors(addr, bus, dat)
    self.rx_last[(addr, bus)] = t
    self.r.rx_total += 1
    if not lib.safety_rx_hook(lp.make_CANPacket(addr, bus, dat)):
      self.r.rx_invalid[addr] += 1
    now = bool(lib.get_controls_allowed())
    if now != self.controls:
      self._flag_controls_change(t, self._rx_cause(addr, bus, dat, *prev, now))
    elif now:
      self.disengaged_since = t
    if not self.relay and lib.get_relay_malfunction():
      self.relay = True
      self.r.relay_malfunction.append({"where": self._where(t), "t": t, "rec": self.r.recordings,
                                       "addr": f"{addr:#x}", "bus": bus})

    if bus == 2 and addr in STOCK_ADDRS:
      fwd = lib.safety_fwd_hook(2, addr)
      state = "forwarded" if fwd == 0 else "blocked"
      self.recent_bus2[(addr, dat)] = (t, state)
      tag = "engaged" if self.controls else "disengaged"
      if state == "blocked":
        last_op = self.op_last_tx.get(addr)
        sub = last_op is not None and (t - last_op) < SUBSTITUTE_TIMEOUT_S[addr] + 0.02
        tag = ("OP substituting" if sub else "no recent OP TX") + (", engaged" if self.controls else ", disengaged")
        if addr == 0x2b9 and self.stock_aeb:
          self.r.stock_aeb_blocked += 1
        dis_for = t - self.disengaged_since
        if not self.controls and dis_for > self.grace[addr]:
          self.r.fwd_disengaged_blocked[addr] += 1
          if len(self.r.fwd_disengaged_examples) < self.max_examples:
            self.r.fwd_disengaged_examples.append({"where": self._where(t), "addr": f"{addr:#x}",
                                                   "name": STOCK_ADDRS[addr],
                                                   "disengaged_for_s": round(dis_for, 2),
                                                   "op_tx_recent": sub})
      self.r.fwd_counts[addr][state] += 1
      runs = self.r.fwd_runs[addr]
      if runs and runs[-1].state == state and (t - runs[-1].t1) < 1.0:
        runs[-1].t1 = t
      else:
        runs.append(FwdRun(state, t, t))
      runs[-1].n += 1
      runs[-1].tags[tag] += 1

  def handle_echo(self, t: float, addr: int, src: int, dat: bytes):
    if src >= 192:
      self.r.real_tx_rejected[(addr, src - 192)] += 1
      hit = self.sim_tx_blocked.get((addr, src - 192, dat))
      if hit is not None and abs(t - hit) < 0.1:
        self.r.real_tx_rejected_matched += 1
      return
    bus = src - 128
    self.r.real_echo_seen = True
    sent = self.recent_sendcan.get((addr, bus, dat))
    if sent is not None and 0 <= t - sent < 0.2:
      return  # echo of openpilot's own frame (it can be a byte copy of the stock frame)
    if bus == 0 and addr in STOCK_ADDRS:
      hit = self.recent_bus2.get((addr, dat))
      if hit is not None and 0 <= t - hit[0] < 0.2:
        self.r.real_fwd[addr] += 1
        if hit[1] == "blocked":
          self.r.real_fwd_sim_blocked[addr] += 1
        del self.recent_bus2[(addr, dat)]

  def handle_tx(self, t: float, addr: int, bus: int, dat: bytes):
    lp, lib = self.lp, self.lib
    ctx = TxContext(controls=bool(lib.get_controls_allowed()), long_allowed=bool(lib.get_longitudinal_allowed()),
                    relay=bool(lib.get_relay_malfunction()), desired_last=lib.get_desired_angle_last(),
                    meas_min=lib.get_angle_meas_min(), meas_max=lib.get_angle_meas_max(),
                    speed_min=lib.get_vehicle_speed_min() / 100., stock_aeb=self.stock_aeb, gas=self.gas)
    ok = lib.safety_tx_hook(lp.make_CANPacket(addr, bus, dat))
    self.r.tx_total[addr] += 1
    if ok:
      self.op_last_tx[addr] = t
      return
    reason = explain_tx_block(addr, bus, dat, ctx)
    if ctx.controls and self.last_engage_t is not None and (t - self.last_engage_t) < 0.25:
      reason += f" [{(t - self.last_engage_t) * 1000:.0f} ms after engage]"
      if reason.startswith("angle rate"):
        reason += " (panda's last angle is from before engage: no 0x488 is sent while disengaged)"
    self.sim_tx_blocked[(addr, bus, dat)] = t
    self.r.tx_blocked[addr] += 1
    if ctx.controls:
      self.r.tx_blocked_controls[addr] += 1
    generic = NUM_RE.sub("#", reason)
    self.r.tx_block_reasons[f"{addr:#x} {TX_NAMES.get(addr, '')}: {generic}"] += 1
    if len(self.r.tx_examples) < self.max_examples:
      self.r.tx_examples.append({"where": self._where(t), "addr": f"{addr:#x}", "bus": bus, "dat": dat.hex(),
                                 "controls_allowed": ctx.controls, "reason": reason})

  def tick(self, t: float):
    lib = self.lib
    while t - self.last_tick >= TICK_PERIOD_S:
      self.last_tick += TICK_PERIOD_S
      self.ticks_since_active += 1
      if self.ticks_since_active == 2 and not self.relay_armed:
        # main.c bumps safety_mode_cnt at 1 Hz; relay check arms once it passes RELAY_TRNS_TIMEOUT.
        lib.init_tests()
        lib.set_alternative_experience(self._alt())
        lib.set_timer(self._timer_us & 0xFFFFFFFF)
        self.relay_armed = True
      lib.safety_tick_current_safety_config()
      lagging = [n for a, b, hz, n in RX_CHECKS
                 if (self.last_tick - self.rx_last.get((a, b), self.active_t0)) > max(MAX_MISSED_MSGS / hz, 1.0)]
      if (t - self.active_t0) > 3.0 and not lib.safety_config_valid():
        self.r.rx_check_invalid_ticks += 1
      if bool(lib.get_controls_allowed()) != self.controls:
        self._flag_controls_change(t, "rx check timeout: " + (", ".join(lagging) or "rx check invalid"))

  def handle_panda_states(self, t: float, ps):
    if len(ps) == 0:
      return
    s = ps[0]
    self.seen_panda_states = True
    model = str(s.safetyModel)
    self.real_model, self.real_param = model, s.safetyParam
    self.real_controls = bool(s.controlsAllowed)
    if self.follow_panda:
      if model == "tesla" and not self.active:
        self.activate(t, f"pandaStates: panda in tesla param {s.safetyParam}")
      elif model != "tesla" and self.active:
        self.deactivate(t, f"pandaStates: panda left tesla -> {model}")
    if self.active:
      sim = bool(self.lib.get_controls_allowed())
      self.r.controls_samples += 1
      if sim == self.real_controls:
        self.r.controls_agree += 1
        self._close_mismatch(t)
      elif self.mismatch_t0 is None:
        self.mismatch_t0 = (t, sim)

  def run(self, files: list[Path]) -> Report:
    r = self.r
    r.files = [str(f) for f in files]
    self._timer_us = None
    for path in files:
      self.seg = segment_label(path)
      timed = []
      for ev in iter_events(path, self.log, r.warnings):
        w = ev.which()
        if w == "initData":
          i = ev.initData
          sw = {"version": i.version, "branch": i.gitBranch, "commit": i.gitCommit[:12], "dirty": i.dirty}
          if sw not in r.software:
            r.software.append(sw)
          continue
        if w == "carParams":
          cp = ev.carParams
          if len(cp.safetyConfigs):
            self.cp_param = cp.safetyConfigs[-1].safetyParam
          self.cp_alt = cp.alternativeExperience
          continue
        if w in TIMED_EVENTS:  # initData/carParams repeat the route-start time in every segment
          timed.append((ev.logMonoTime, w, ev))
      # loggerd writes nearly in order; re-written (redacted) logs may be grouped by service
      timed.sort(key=lambda x: x[0])
      for mono, w, ev in timed:
        if self.last_t is not None and (mono < self.last_t - RESET_BACK_S * 1e9 or
                                        (not self.active and mono > self.last_t + GAP_S * 1e9)):
          self.deactivate(self._t(self.last_t), "log mono time reset (new recording)")
          self.rec_t0 = None
          self.last_t = None
        if self.rec_t0 is None:
          self.rec_t0 = mono
          self._timer_us = None
          self.can_t_first = None
          r.recordings += 1
        if self.last_t is not None and mono > self.last_t + GAP_S * 1e9 and self.active:
          gap = (mono - self.last_t) / 1e9
          r.notes.append(f"{self._where(self._t(mono))}: {gap:.1f} s log gap, safety re-initialized " +
                         f"(controls seeded from pandaStates = {self.real_controls})")
          self._timer_us = None
          self._set_timer(mono)
          self.deactivate(self._t(self.last_t), "log gap")
          self.activate(self._t(mono), "resume after log gap", seed_controls=bool(self.real_controls))
        self.last_t = max(mono, self.last_t or 0)
        t = self._t(mono)

        if w == "pandaStates":
          self._set_timer(mono)
          self.handle_panda_states(t, ev.pandaStates)
        elif w == "controlsState":
          en = bool(ev.controlsState.enabled)
          if en != self.op_enabled and self.op_enabled is not None:
            r.op_transitions.append({"where": self._where(t), "event": "openpilot enabled" if en else "openpilot disabled"})
          self.op_enabled = en
        elif w == "can":
          if self.can_t_first is None:
            self.can_t_first = t
          if not self.active and not self.follow_panda:
            self._set_timer(mono)
            self.activate(t, "--from-start")
          elif not self.active and self.follow_panda and not self.seen_panda_states and t - self.can_t_first > 5.0:
            self._set_timer(mono)
            self.activate(t, "no pandaStates in log: assuming tesla from here")
            r.notes.append("no pandaStates seen in the first 5 s of CAN; replaying as if safety were tesla")
          if not self.active:
            continue
          self._set_timer(mono)
          self.tick(t)
          echoes = []
          for c in ev.can:
            src = c.src
            if src < 64:
              self.handle_rx(t, c.address, src, bytes(c.dat))
            elif src >= 128:
              echoes.append((c.address, src, bytes(c.dat)))
          # pandad batches the forward echo ahead of the bus-2 frame it copies
          for addr, src, dat in echoes:
            self.handle_echo(t, addr, src, dat)
          if len(self.recent_bus2) > 512:
            self.recent_bus2 = {k: v for k, v in self.recent_bus2.items() if t - v[0] < 0.2}
        elif w == "sendcan" and self.active:
          self._set_timer(mono)
          for c in ev.sendcan:
            dat = bytes(c.dat)
            self.recent_sendcan[(c.address, c.src, dat)] = t
            if not self.rx_only:
              self.handle_tx(t, c.address, c.src, dat)
          if len(self.recent_sendcan) > 2048:
            self.recent_sendcan = {k: v for k, v in self.recent_sendcan.items() if t - v < 0.2}
    if self.last_t is not None:
      self.deactivate(self._t(self.last_t), "end of log")
    self._grade()
    return r

  def _grade(self):
    r = self.r
    f = r.flags
    if not r.active_spans:
      f.append(("FAIL", "safety never ran: no tesla pandaStates (use --from-start to force)"))
    n = sum(r.tx_blocked_controls.values())
    if n:
      f.append(("FAIL", f"{n} openpilot TX frames blocked while controls were allowed"))
    n = sum(r.tx_blocked.values()) - sum(r.tx_blocked_controls.values())
    if n:
      f.append(("WARN", f"{n} openpilot TX frames blocked while controls were not allowed"))
    for a in ACTUATOR_ADDRS:
      if r.fwd_disengaged_blocked[a]:
        f.append(("FAIL", f"stock {a:#x} {STOCK_ADDRS[a]} dropped from the chassis {r.fwd_disengaged_blocked[a]} times " +
                          f"while openpilot was not engaged (> {self.grace[a]:.1f} s after disengage)"))
    for a in CLUSTER_ADDRS:
      if r.fwd_disengaged_blocked[a]:
        f.append(("WARN", f"stock {a:#x} {STOCK_ADDRS[a]} dropped {r.fwd_disengaged_blocked[a]} times while not engaged " +
                          f"(> {self.grace[a]:.1f} s after disengage)"))
    if r.stock_aeb_blocked:
      f.append(("FAIL", f"{r.stock_aeb_blocked} stock DAS_control frames with AEB active were dropped"))
    if r.relay_malfunction:
      f.append(("FAIL", f"relay malfunction at {r.relay_malfunction[0]['where']} ({r.relay_malfunction[0]['addr']})"))
    if r.controls_mismatch:
      f.append(("WARN", f"{len(r.controls_mismatch)} periods where replayed controls_allowed disagreed with the car's " +
                        f"panda for >= {MISMATCH_MIN_S} s (different panda firmware, or log gaps)"))
    if sum(r.rx_invalid.values()):
      f.append(("WARN", f"{sum(r.rx_invalid.values())} rx frames rejected by the rx hook"))
    if r.rx_check_invalid_ticks:
      f.append(("WARN", f"{r.rx_check_invalid_ticks} safety ticks with rx checks invalid (missing/lagging car messages)"))
    if r.real_echo_seen:
      for a in STOCK_ADDRS:
        c = r.fwd_counts.get(a, Counter())
        car_only = r.real_fwd_sim_blocked[a]
        sim_only = c["forwarded"] - (r.real_fwd[a] - car_only)
        if car_only + sim_only > max(5, 0.01 * sum(c.values())):
          f.append(("WARN", f"{a:#x} forwarding differs from the car's panda: car forwarded {car_only} the replay " +
                            f"dropped, replay forwarded {sim_only} the car did not (other firmware on the car?)"))
    if sum(r.real_tx_rejected.values()):
      f.append(("WARN", f"the car's panda rejected {sum(r.real_tx_rejected.values())} TX frames (src 192+bus echoes)"))


# ---------------------------------------------------------------- output

def _fmt_counter(c: Counter, names=None) -> str:
  return ", ".join(f"{k:#x}{('/' + names[k]) if names and k in names else ''}={v}" if isinstance(k, int) else f"{k}={v}"
                   for k, v in sorted(c.items(), key=lambda kv: str(kv[0]))) or "none"


def print_report(r: Report, summary: bool, max_runs: int = 30, out=sys.stdout):
  p = lambda *a: print(*a, file=out)  # noqa: E731
  p("BogPilot AP1 safety replay (offline; not a road validation)")
  p(f"  logs: {len(r.files)} file(s), {r.recordings} recording(s)")
  p(f"  libpanda: {r.libpanda}")
  for sw in r.software:
    p(f"  logged software: {sw['version']} {sw['branch']} {sw['commit']}{' dirty' if sw['dirty'] else ''}")
  p(f"  safety: tesla param {r.param} alternativeExperience {r.alt_exp}")
  for s in r.active_spans:
    rec = f"R{s['rec']} " if r.recordings > 1 else ""
    p(f"  replayed {rec}{s['t0']:.2f}-{s['t1']}s from [{s['seg']}] ({s['why']}; ended: {s.get('end', '?')})")
  for n in r.notes:
    p(f"  note: {n}")
  for w in r.warnings:
    p(f"  warning: {w}")

  p("\nTX (sendcan through safety_tx_hook)")
  p(f"  sent: {_fmt_counter(r.tx_total, TX_NAMES)}")
  p(f"  blocked: {sum(r.tx_blocked.values())} (with controls allowed: {sum(r.tx_blocked_controls.values())})" +
    f"  {_fmt_counter(r.tx_blocked)}")
  for k, v in r.tx_block_reasons.most_common(None if not summary else 5):
    p(f"    {v:6d} x {k}")
  for e in r.tx_examples[: (3 if summary else None)]:
    p(f"    e.g. {e['where']} {e['addr']} bus {e['bus']} {e['dat']} controls={int(e['controls_allowed'])}: {e['reason']}")
  rej = {f"{a:#x}/bus{b}": n for (a, b), n in r.real_tx_rejected.items()}
  extra = f"  {rej}; {r.real_tx_rejected_matched} of them also blocked in replay" if rej else ""
  p(f"  car's panda rejected (src 192+bus echoes): {sum(r.real_tx_rejected.values())}{extra}")

  p("\ncontrols_allowed (rx-driven)")
  p(f"  rx frames: {r.rx_total}, rejected by rx hook: {sum(r.rx_invalid.values())}")
  eng = sum(1 for x in r.transitions if x["event"] == "ENGAGE")
  p(f"  transitions: {eng} engage, {len(r.transitions) - eng} disengage")
  causes = Counter(f"{x['event']}: {re.sub(r'[0-9.]+ m/s', '# m/s', x['cause'])}" for x in r.transitions)
  for k, v in causes.most_common():
    p(f"    {v:4d} x {k}")
  if not summary:
    for x in r.transitions:
      p(f"    {x['where']} {x['event']:9s} {x['cause']}")
    for x in r.op_transitions[:200]:
      p(f"    {x['where']} (context) {x['event']}")
  if r.controls_samples:
    p(f"  vs car's panda (pandaStates.controlsAllowed): {100. * r.controls_agree / r.controls_samples:.2f}% of " +
      f"{r.controls_samples} samples agree; {len(r.controls_mismatch)} disagreement(s) >= {MISMATCH_MIN_S} s")
    for m in r.controls_mismatch[: (3 if summary else 50)]:
      p(f"    {m['where']} for {m['dur_s']} s: replay={int(m['sim'])} panda={int(m['panda'])}")
  p(f"  relay malfunction: {'yes ' + r.relay_malfunction[0]['where'] if r.relay_malfunction else 'no'}")
  p(f"  safety ticks with rx checks invalid: {r.rx_check_invalid_ticks}")

  p("\nstock DAS frames from bus 2 (safety_fwd_hook to the chassis)")
  for a, name in STOCK_ADDRS.items():
    c = r.fwd_counts.get(a, Counter())
    if not c:
      p(f"  {a:#x} {name}: not seen on bus 2")
      continue
    real = (f", car's panda forwarded {r.real_fwd[a]} (echo evidence; {r.real_fwd_sim_blocked[a]} of them dropped " +
            "in replay)") if r.real_echo_seen else ""
    p(f"  {a:#x} {name}: forwarded {c['forwarded']}, dropped {c['blocked']}" +
      f" (while disengaged past grace: {r.fwd_disengaged_blocked[a]}){real}")
    if not summary:
      runs = r.fwd_runs[a]
      for run in runs[:max_runs]:
        tags = ", ".join(f"{k} {v}" for k, v in run.tags.most_common())
        p(f"      {run.t0:8.2f}-{run.t1:8.2f}s {run.state:9s} {run.n:6d}  ({tags})")
      if len(runs) > max_runs:
        p(f"      ... {len(runs) - max_runs} more runs (use --max-runs or --json)")
  for e in r.fwd_disengaged_examples[: (3 if summary else None)]:
    p(f"    e.g. {e['where']} {e['addr']} {e['name']} dropped {e['disengaged_for_s']} s after disengage" +
      f" ({'OP was still sending it' if e['op_tx_recent'] else 'no OP replacement'})")
  if r.stock_aeb_blocked:
    p(f"  stock AEB DAS_control dropped: {r.stock_aeb_blocked}")

  p("\nflags")
  if not r.flags:
    p("  none")
  for lvl, m in r.flags:
    p(f"  {lvl}: {m}")
  p(f"\nRESULT: {'FAIL' if any(l == 'FAIL' for l, _ in r.flags) else 'PASS'}")


def run_quiet(rp: Ap1SafetyReplay, files: list[Path]) -> Report:
  """Run with libpanda's C printf (e.g. "Temporary fault occurred") sent to stderr, not into the report."""
  sys.stdout.flush()
  saved = os.dup(1)
  try:
    os.dup2(2, 1)
    return rp.run(files)
  finally:
    try:
      import ctypes
      ctypes.CDLL(None).fflush(None)
    except Exception:
      pass
    sys.stdout.flush()
    os.dup2(saved, 1)
    os.close(saved)


def main(argv=None) -> int:
  ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                               formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
  ap.add_argument("logs", nargs="+", help="rlog / rlog.zst / rlog.bz2 files or directories (searched recursively)")
  ap.add_argument("--summary", action="store_true", help="counts and flags only, no timelines")
  ap.add_argument("--json", metavar="FILE", help="also write the full report as JSON")
  ap.add_argument("--safety-rev", metavar="REV", help="build libpanda from this git revision's panda/board")
  ap.add_argument("--libpanda", metavar="SO", help="use this prebuilt libpanda.so")
  ap.add_argument("--param", type=int, help="override the Tesla safety param (default: from the log, else 10)")
  ap.add_argument("--alt-exp", type=int, help="override alternativeExperience (default: carParams)")
  ap.add_argument("--from-start", action="store_true", help="run tesla safety from the first frame, ignore pandaStates")
  ap.add_argument("--rx-only", action="store_true", help="do not replay sendcan (safety rx/forwarding only)")
  ap.add_argument("--grace-actuator", type=float, default=1.0,
                  help="seconds after disengage before a dropped stock 0x488/0x2b9 is flagged (default 1.0)")
  ap.add_argument("--grace-cluster", type=float, default=5.0,
                  help="same for 0x399/0x389/0x239; openpilot keeps them 4 s after disengage (default 5.0)")
  ap.add_argument("--max-runs", type=int, default=30, help="forwarding runs printed per address")
  ap.add_argument("--examples", type=int, default=10, help="examples kept per category (default 10)")
  ap.add_argument("--qlog", action="store_true", help="also pick up qlogs in directories (decimated CAN)")
  args = ap.parse_args(argv)

  try:
    files = find_logs(args.logs, allow_qlog=args.qlog)
  except FileNotFoundError as e:
    print(f"no such file or directory: {e}", file=sys.stderr)
    return 2
  if not files:
    print("no rlog files found", file=sys.stderr)
    return 2

  so = None
  if args.safety_rev:
    so = libpanda_for_rev(args.safety_rev)
  elif args.libpanda:
    so = Path(args.libpanda)
  lp, lib = load_libpanda(so)
  rp = Ap1SafetyReplay(lp, lib, param=args.param, alt_exp=args.alt_exp, follow_panda=not args.from_start,
                       rx_only=args.rx_only, grace_actuator=args.grace_actuator, grace_cluster=args.grace_cluster,
                       max_examples=args.examples)
  rp.r.libpanda = (f"git {args.safety_rev} ({so})" if args.safety_rev else str(so or PANDA_DIR / 'tests/libpanda/libpanda.so'))
  if args.rx_only:
    rp.r.notes.append("--rx-only: openpilot TX not replayed")
  report = run_quiet(rp, files)
  print_report(report, args.summary, args.max_runs)
  if args.json:
    with open(args.json, "w") as f:
      json.dump(report.as_dict(), f, indent=1, default=str)
  return 1 if any(lvl == "FAIL" for lvl, _ in report.flags) else 0


if __name__ == "__main__":
  sys.exit(main())
