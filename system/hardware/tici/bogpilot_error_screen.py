#!/usr/bin/env python3
"""BogPilot: full-screen error shown when the AGNOS downgrade fails.

Standalone on purpose: stdlib + pyray only, no openpilot imports, so it runs on whatever AGNOS
the device is on. On AGNOS with the "magic" display server, raylib gets the display through
/tmp/drmfd.sock. Exit codes: 0 = Retry, 10 = Reboot, 3 = pyray missing, 4 = no display.

  bogpilot_error_screen.py --log FILE [--reason TEXT] [--print-reason] [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
import time

DEFAULT_LOG = "/data/tmp/bogpilot_agnos.log"
EXIT_RETRY, EXIT_REBOOT, EXIT_NO_PYRAY, EXIT_NO_DISPLAY = 0, 10, 3, 4

# "XxxError: msg" / "XxxException: msg" lines from a Python traceback
_EXC_RE = re.compile(r"^\s*(?:[A-Za-z_][\w.]*\.)?([A-Za-z_]\w*(?:Error|Exception|Exit|Interrupt))(?::\s*(.*))?$")
_FAIL_RE = re.compile(r"(error|failed|failure|exception|traceback)", re.IGNORECASE)
_NOISE_RE = re.compile(r"bogpilot_updater: (running|exited with code|log|AGNOS update:|pyserial missing)")


def extract_reason(text: str, max_len: int = 160) -> str:
  lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
  # bogpilot_updater may try more than one updater: report the last attempt
  starts = [i for i, ln in enumerate(lines) if "bogpilot_updater: running:" in ln]
  if starts and starts[-1] > 0:
    reason = extract_reason("\n".join(lines[starts[-1]:]), max_len)
    if reason != "unknown error":
      return reason
  # prefer the last exception line of a traceback
  for ln in reversed(lines):
    m = _EXC_RE.match(ln)
    if m:
      reason = f"{m.group(1)}: {m.group(2)}" if m.group(2) else m.group(1)
      return _clip(reason, max_len)
  for ln in reversed(lines):
    if _FAIL_RE.search(ln) and not _NOISE_RE.search(ln):
      return _clip(re.sub(r"^\[[^\]]*\]\s*", "", ln.strip()), max_len)
  for ln in reversed(lines):
    if not _NOISE_RE.search(ln):
      return _clip(re.sub(r"^\[[^\]]*\]\s*", "", ln.strip()), max_len)
  return "unknown error"


def _clip(s: str, n: int) -> str:
  s = " ".join(s.split())
  return s if len(s) <= n else s[:n - 3] + "..."


def read_log(path: str) -> str:
  try:
    with open(path, errors="replace") as f:
      return f.read()[-64 * 1024:]
  except OSError:
    return ""


def build_message(reason: str, log: str) -> str:
  return f"AGNOS downgrade failed: {reason}. See {log}"


def wrap(text: str, width: int, measure) -> list[str]:
  out = []
  for para in text.split("\n"):
    cur = ""
    for word in para.split(" "):
      cand = word if not cur else cur + " " + word
      if measure(cand) <= width or not cur:
        cur = cand
      else:
        out.append(cur)
        cur = word
    out.append(cur)
  return out


def run_screen(message: str, keep: str | None, min_wait: float) -> int:
  try:
    import pyray as rl
  except Exception as e:  # noqa: BLE001
    print(f"bogpilot_error_screen: pyray unavailable: {e}", file=sys.stderr)
    return EXIT_NO_PYRAY

  rl.set_trace_log_level(rl.TraceLogLevel.LOG_WARNING)
  rl.init_window(0, 0, "BogPilot")
  if not rl.is_window_ready():
    print("bogpilot_error_screen: no display", file=sys.stderr)
    return EXIT_NO_DISPLAY
  rl.set_target_fps(20)
  w, h = rl.get_screen_width(), rl.get_screen_height()
  if w <= 0 or h <= 0:
    w, h = 2160, 1080
  try:
    with open("/sys/class/backlight/panel0-backlight/bl_power", "w") as f:
      f.write("0")
  except OSError:
    pass

  pad = int(w * 0.05)
  title_size, body_size, btn_size = int(h * 0.09), int(h * 0.05), int(h * 0.055)
  body = message + "\n\nTap Retry to try again, or reflash AGNOS from flash.comma.ai."
  if keep:
    body += f"\nA copy of the log is kept at {keep}"
  lines = wrap(body, w - 2 * pad, lambda s: rl.measure_text(s, body_size))
  btn_w, btn_h = int(w * 0.22), int(h * 0.13)
  retry = rl.Rectangle(w - pad - btn_w, h - pad - btn_h, btn_w, btn_h)
  reboot = rl.Rectangle(w - 2 * pad - 2 * btn_w, h - pad - btn_h, btn_w, btn_h)
  start = time.monotonic()
  result = None
  while result is None and not rl.window_should_close():
    ready = time.monotonic() - start >= min_wait
    if ready and rl.is_mouse_button_released(rl.MouseButton.MOUSE_BUTTON_LEFT):
      p = rl.get_mouse_position()
      if rl.check_collision_point_rec(p, retry):
        result = EXIT_RETRY
      elif rl.check_collision_point_rec(p, reboot):
        result = EXIT_REBOOT
    rl.begin_drawing()
    rl.clear_background(rl.Color(20, 20, 20, 255))
    rl.draw_text("BogPilot: AGNOS downgrade failed", pad, pad, title_size, rl.Color(255, 90, 90, 255))
    y = pad + int(title_size * 1.6)
    for ln in lines:
      rl.draw_text(ln, pad, y, body_size, rl.WHITE)
      y += int(body_size * 1.35)
    col = rl.Color(70, 70, 70, 255) if ready else rl.Color(40, 40, 40, 255)
    for rect, label in ((reboot, "Reboot"), (retry, "Retry")):
      rl.draw_rectangle_rounded(rect, 0.3, 10, col)
      tw = rl.measure_text(label, btn_size)
      rl.draw_text(label, int(rect.x + (rect.width - tw) / 2), int(rect.y + (rect.height - btn_size) / 2), btn_size, rl.WHITE)
    rl.end_drawing()
  rl.close_window()
  return EXIT_RETRY if result is None else result


def main(argv=None) -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--log", default=DEFAULT_LOG)
  ap.add_argument("--reason")
  ap.add_argument("--keep")
  ap.add_argument("--print-reason", action="store_true")
  ap.add_argument("--dry-run", action="store_true")
  ap.add_argument("--min-wait", type=float, default=3.0)
  a = ap.parse_args(argv)

  reason = a.reason or extract_reason(read_log(a.log))
  if a.print_reason:
    print(reason)
    return 0
  message = build_message(reason, a.log)
  print(f"BogPilot: {message}", flush=True)
  if a.dry_run:
    return EXIT_RETRY
  return run_screen(message, a.keep, a.min_wait)


if __name__ == "__main__":
  sys.exit(main())
