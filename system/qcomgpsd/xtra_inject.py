"""BogPilot: XTRA (gpsOneXTRA) injection for the Quectel EG25-G that works without a SIM.

Why: with no SIM, ModemManager parks the modem in state "failed" (reason "sim-missing"). It can't be
enabled, so `mmcli --location-inject-assistance-data` always fails with "modem not enabled yet" and
qcomgpsd has never loaded XTRA on this device. `mmcli --command` still works in that state, but it
can't carry the raw bytes AT+QFUPL needs, so the direct path talks to a modem AT tty itself.

Order (Quectel EC2x/EG2x GNSS application note, gpsOneXTRA section): GNSS engine off ->
AT+QGPSXTRA=1 (takes effect after a modem restart if it was off) -> AT+QGPSXTRATIME (time first) ->
AT+QFUPL file to UFS -> AT+QGPSXTRADATA="UFS:..." -> AT+QGPSXTRADATA? to verify -> GNSS on.

Port safety: ttyUSB0 is the diag port (qcomgpsd), ttyUSB1 is NMEA. The AT ports are ttyUSB2/ttyUSB3.
We only use an AT tty that no other process has open (checked via /proc or `sudo -n fuser`; if we can't
tell, we don't use it), opened with an flock plus TIOCEXCL.
If every AT tty is held by ModemManager, the fallback is `mmcli -m any --inhibit`, which makes
ModemManager release the modem until we stop it. That is only allowed while the modem is in the
failed/sim-missing state (no data session to drop) and only when allow_inhibit=True.

Everything here is best effort: no function raises to the caller, every step is logged, and the whole
direct path is bounded by a deadline (default 15 s). Never logs location; the only modem data logged
is the XTRA status line (validity minutes + XTRA epoch time).
"""
from __future__ import annotations

import datetime
import glob
import os
import re
import signal
import subprocess
import time
from typing import Callable

from openpilot.common.swaglog import cloudlog

UFS_NAME = "UFS:xtra3grc.bin"
DEFAULT_AT_PORTS = ["/dev/ttyUSB3", "/dev/ttyUSB2"]   # prefer USB3: ModemManager's primary AT is usually USB2
NEVER_PORTS = {"/dev/ttyUSB0", "/dev/ttyUSB1"}        # diag (qcomgpsd) and NMEA
MAX_XTRA_BYTES = 200_000
DEFAULT_BUDGET_S = 15.0

_FINAL = re.compile(r"^(OK|ERROR|\+CME ERROR:.*|\+CMS ERROR:.*)$")


def _now_utc_str() -> str:
  return datetime.datetime.now(datetime.timezone.utc).strftime("%Y/%m/%d,%H:%M:%S")


def _mmcli(args: str, timeout: float) -> str:
  return subprocess.check_output(f"mmcli -m any {args}", shell=True, encoding="utf8",
                                 stderr=subprocess.STDOUT, timeout=max(1.0, timeout))


def mm_status(timeout: float = 4.0) -> dict:
  """state, failed reason and port map from ModemManager. Never returns IDs (IMEI etc)."""
  out = {"state": None, "failed_reason": None, "ports": {}}
  try:
    txt = _mmcli("-K", timeout)
  except Exception as e:
    out["error"] = str(e)[:200]
    return out
  for line in txt.splitlines():
    k, _, v = line.partition(":")
    k, v = k.strip(), v.strip()
    if k == "modem.generic.state":
      out["state"] = v
    elif k == "modem.generic.state-failed-reason":
      out["failed_reason"] = v
    elif k.startswith("modem.generic.ports.value"):
      m = re.match(r"(\S+)\s*\((\w+)\)", v)
      if m:
        out["ports"]["/dev/" + m.group(1)] = m.group(2)
  return out


def mm_at(cmd: str, timeout: float = 5.0) -> str:
  """One AT command through ModemManager (works in the failed state). Returns the response text."""
  txt = _mmcli(f"--timeout {int(max(1, timeout))} --command='{cmd}'", timeout + 2)
  m = re.search(r"response:\s*'(.*)'", txt, re.S)
  return (m.group(1) if m else txt).strip()


def port_open_by_others(port: str) -> bool | None:
  """True/False if we can tell, None if we can't see other processes' fds (not root)."""
  real = os.path.realpath(port)
  unknown = False
  for fd in glob.glob("/proc/[0-9]*/fd/*"):
    try:
      if os.path.realpath(fd) == real and not fd.startswith(f"/proc/{os.getpid()}/"):
        return True
    except PermissionError:
      unknown = True
    except OSError:
      pass
  if unknown:
    try:
      r = subprocess.run(f"sudo -n fuser {real}", shell=True, capture_output=True, timeout=3)
      if r.returncode in (0, 1):
        return r.returncode == 0
    except Exception:
      pass
    return None
  return False


class RawAt:
  """Minimal AT client on one tty. Opened exclusively; closed on exit."""

  def __init__(self, port: str):
    from serial import Serial
    self.port = port
    # exclusive=True takes an flock; TIOCEXCL also stops anyone (ModemManager included) opening it mid-upload
    self.ser = Serial(port, baudrate=115200, timeout=0.2, exclusive=True)
    try:
      import fcntl
      import termios
      fcntl.ioctl(self.ser.fileno(), termios.TIOCEXCL)
    except Exception:
      pass
    self.ser.reset_input_buffer()

  def close(self):
    try:
      self.ser.close()
    except Exception:
      pass

  def _readline_until(self, deadline: float) -> str | None:
    buf = b""
    while time.monotonic() < deadline:
      c = self.ser.read(1)
      if not c:
        continue
      if c == b"\n":
        return buf.decode("utf-8", errors="replace").strip()
      buf += c
    return None

  def cmd(self, cmd: str, timeout: float = 3.0, expect: str | None = None) -> tuple[bool, list[str]]:
    """Send cmd; return (ok, lines). If expect is set, return as soon as a line starts with it."""
    self.ser.reset_input_buffer()
    self.ser.write(f"{cmd}\r".encode())
    deadline = time.monotonic() + timeout
    lines: list[str] = []
    while True:
      line = self._readline_until(deadline)
      if line is None:
        return False, lines + ["<timeout>"]
      if not line or line == cmd:      # blank or echo
        continue
      lines.append(line)
      if expect is not None and line.startswith(expect):
        return True, lines
      if _FINAL.match(line):
        return line == "OK", lines

  def upload(self, name: str, data: bytes, timeout: float) -> tuple[bool, list[str]]:
    secs = max(5, int(timeout))
    ok, lines = self.cmd(f'AT+QFUPL="{name}",{len(data)},{secs}', timeout=3.0, expect="CONNECT")
    if not ok or not lines or not lines[-1].startswith("CONNECT"):
      return False, lines
    for i in range(0, len(data), 4096):
      self.ser.write(data[i:i + 4096])
    self.ser.flush()
    deadline = time.monotonic() + timeout
    got: list[str] = []
    while True:
      line = self._readline_until(deadline)
      if line is None:
        return False, lines + got + ["<timeout>"]
      if not line:
        continue
      got.append(line)
      if _FINAL.match(line):
        m = next((re.match(r"\+QFUPL:\s*(\d+)", g) for g in got if g.startswith("+QFUPL:")), None)
        return line == "OK" and m is not None and int(m.group(1)) == len(data), lines + got


class Inhibit:
  """Hold `mmcli -m any --inhibit` for the duration of the with-block."""

  def __init__(self, wait_s: float = 3.0):
    self.proc = None
    self.wait_s = wait_s

  def __enter__(self):
    self.proc = subprocess.Popen("exec mmcli -m any --inhibit", shell=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(self.wait_s)
    if self.proc.poll() is not None:
      raise RuntimeError(f"mmcli --inhibit exited early rc={self.proc.returncode}")
    return self

  def __exit__(self, *exc):
    if self.proc and self.proc.poll() is None:
      self.proc.send_signal(signal.SIGINT)   # mmcli releases the inhibition on SIGINT
      try:
        self.proc.wait(timeout=3)
      except subprocess.TimeoutExpired:
        self.proc.kill()
    return False


def parse_xtradata(lines_or_text) -> tuple[int, str] | None:
  text = "\n".join(lines_or_text) if isinstance(lines_or_text, list) else str(lines_or_text)
  m = re.search(r'\+QGPSXTRADATA:\s*(\d+)\s*,\s*"([^"]*)"', text)
  return (int(m.group(1)), m.group(2)) if m else None


def _pick_port(status: dict) -> str | None:
  ports = [p for p, kind in status.get("ports", {}).items() if kind == "at"] or list(DEFAULT_AT_PORTS)
  ports.sort(key=lambda p: (p != "/dev/ttyUSB3", p))
  for p in ports:
    if p in NEVER_PORTS or not os.path.exists(p):
      continue
    busy = port_open_by_others(p)
    if busy is False:
      return p
    # busy is None: can't tell who has it open -> don't risk sharing ModemManager's port
  return None


def inject_direct(path: str, time_trusted: bool, budget_s: float = DEFAULT_BUDGET_S,
                  allow_inhibit: bool = True, port: str | None = None,
                  log: Callable[[str], None] | None = None) -> dict:
  """Upload + inject XTRA over a raw AT tty. Assumes the GNSS engine is off. Never raises."""
  t0 = time.monotonic()
  deadline = t0 + budget_s
  res: dict = {"ok": False, "mode": None, "port": None, "steps": []}
  say = log or (lambda s: cloudlog.warning(f"xtra: {s}"))

  def step(name, ok, lines):
    res["steps"].append((name, ok, lines[-3:]))
    say(f"{name}: {'ok' if ok else 'FAIL'} {lines[-3:]}")

  try:
    with open(path, "rb") as f:
      data = f.read()
    if not 1000 < len(data) <= MAX_XTRA_BYTES:
      raise ValueError(f"unexpected XTRA size {len(data)}")
    status = mm_status(timeout=min(4.0, deadline - time.monotonic()))
    res["mm_state"], res["mm_failed_reason"] = status.get("state"), status.get("failed_reason")
    failed_nosim = status.get("state") == "failed"

    inhibit = None
    p = port or _pick_port(status)
    at = None
    if p is not None:
      try:
        at = RawAt(p)
        res["mode"], res["port"] = "free-port", p
      except Exception as e:
        say(f"open {p} failed ({e}); port is held")
        at = None
    if at is None:
      if not (allow_inhibit and failed_nosim):
        raise RuntimeError("no free AT port and inhibit not allowed (modem not in failed state)")
      inhibit = Inhibit(wait_s=min(3.0, max(0.5, deadline - time.monotonic() - 8)))
      inhibit.__enter__()
      for cand in (port,) if port else DEFAULT_AT_PORTS:
        try:
          at = RawAt(cand)
          res["mode"], res["port"] = "inhibit", cand
          break
        except Exception as e:
          say(f"open {cand} under inhibit failed ({e})")
      if at is None:
        raise RuntimeError("no AT port could be opened even with ModemManager inhibited")

    try:
      left = lambda: max(0.5, deadline - time.monotonic())
      ok, lines = at.cmd("AT+QGPS?", timeout=min(2, left()))
      step("QGPS?", ok, lines)
      if any("+QGPS: 1" in l for l in lines):
        ok, lines = at.cmd("AT+QGPSEND", timeout=min(2, left()))
        step("QGPSEND", ok, lines)
      ok, lines = at.cmd("AT+QGPSXTRA?", timeout=min(2, left()))
      step("QGPSXTRA?", ok, lines)
      if ok and not any(re.search(r"\+QGPSXTRA:\s*1", l) for l in lines):
        ok2, lines2 = at.cmd("AT+QGPSXTRA=1", timeout=min(2, left()))
        step("QGPSXTRA=1", ok2, lines2)
        res["xtra_was_disabled"] = True
        say("XTRA was disabled; enabled now, it takes effect after the next modem restart")
      before = at.cmd("AT+QGPSXTRADATA?", timeout=min(2, left()))
      res["before"] = parse_xtradata(before[1])
      if time_trusted:
        ok, lines = at.cmd(f'AT+QGPSXTRATIME=0,"{_now_utc_str()}",1,1,3500', timeout=min(2, left()))
        step("QGPSXTRATIME", ok, lines)
      else:
        say("system clock not NTP-synced; skipping XTRATIME (data inject may be rejected)")
      ok, lines = at.cmd(f'AT+QFDEL="{UFS_NAME}"', timeout=min(2, left()))   # error = no old file, fine
      step("QFDEL (error just means no old file)", ok, lines)
      ok, lines = at.upload(UFS_NAME, data, timeout=min(8, left()))
      step(f"QFUPL {len(data)}B", ok, lines)
      if ok:
        ok, lines = at.cmd(f'AT+QGPSXTRADATA="{UFS_NAME}"', timeout=min(3, left()))
        step("QGPSXTRADATA=", ok, lines)
      after = at.cmd("AT+QGPSXTRADATA?", timeout=min(2, left()))
      res["after"] = parse_xtradata(after[1])
      res["ok"] = bool(res["after"] and res["after"][0] > 0)
      at.cmd(f'AT+QFDEL="{UFS_NAME}"', timeout=min(2, left()))
    finally:
      at.close()
      if inhibit is not None:
        inhibit.__exit__(None, None, None)
        res["reprobe_needed"] = True
  except Exception as e:
    res["error"] = f"{type(e).__name__}: {e}"[:300]
    say(f"direct inject failed: {res['error']}")
  res["elapsed_s"] = round(time.monotonic() - t0, 2)
  cloudlog.event("xtra direct inject", ok=res["ok"], mode=res["mode"], port=res["port"],
                 before=res.get("before"), after=res.get("after"), elapsed_s=res["elapsed_s"],
                 mm_state=res.get("mm_state"), mm_failed_reason=res.get("mm_failed_reason"),
                 error=res.get("error"))
  return res


def inject_mmcli(path: str, timeout: float = 5.0) -> tuple[bool, str]:
  try:
    subprocess.check_output(f"mmcli -m any --timeout {int(timeout)} --location-inject-assistance-data={path}",
                            shell=True, stderr=subprocess.PIPE, timeout=timeout + 2)
    return True, ""
  except subprocess.CalledProcessError as e:
    return False, (e.stderr or b"").decode(errors="replace").strip()[:300]
  except Exception as e:
    return False, str(e)[:300]


def inject_xtra(path: str, time_trusted: bool, budget_s: float = DEFAULT_BUDGET_S) -> dict:
  """mmcli first (works when ModemManager has the modem enabled), else the direct UFS path."""
  t0 = time.monotonic()
  status = mm_status(timeout=3.0)
  if status.get("state") != "failed":
    ok, err = inject_mmcli(path, timeout=5.0)
    if ok:
      cloudlog.info("successfully loaded assistance data (mmcli)")
      return {"ok": True, "mode": "mmcli"}
    cloudlog.error(f"inject_assistance mmcli failed: {err}")
  else:
    cloudlog.warning(f"modem state failed ({status.get('failed_reason')}); skipping mmcli inject")
  return inject_direct(path, time_trusted, budget_s=max(3.0, budget_s - (time.monotonic() - t0)))
