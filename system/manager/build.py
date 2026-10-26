#!/usr/bin/env python3
import os
import re
import subprocess
import threading
from pathlib import Path

# NOTE: Do NOT import anything here that needs be built (e.g. params)
from openpilot.common.basedir import BASEDIR
from openpilot.common.bogpilot_build_guard import ThermalPauser, build_jobs
from openpilot.common.spinner import Spinner
from openpilot.common.text_window import TextWindow
from openpilot.system.hardware import AGNOS
from openpilot.common.swaglog import cloudlog, add_file_handler
from openpilot.system.version import get_build_metadata

MAX_CACHE_SIZE = 4e9 if "CI" in os.environ else 2e9
CACHE_DIR = Path("/data/scons_cache" if AGNOS else "/tmp/scons_cache")

TOTAL_SCONS_NODES = 2820
MAX_BUILD_PROGRESS = 100

# BogPilot: the lines that say what failed: "file:line:col: message" (gcc, Cython), "scons: *** [target] ...",
# and the last line of Python tracebacks. Headers like "Error compiling Cython file:" carry no information.
_LOCATED = re.compile(r"^\S+:\d+(:\d+)?:\s+(?!warning|note)")
_PY_EXC = re.compile(r"^([A-Za-z_][\w.]*)(Error|Exception|Exit): ")
_NOISE = ("Error compiling Cython file:", "Cython.Compiler.Errors.CompileError:")


def error_summary(output: str, max_lines: int = 15) -> list[str]:
  picked: list[str] = []
  seen: set[str] = set()
  for ln in output.split("\n"):
    s = ln.strip()
    if not s or s.startswith(_NOISE) or s in seen:
      continue
    if _LOCATED.match(s) or s.startswith(("scons: ***", "error:")) or _PY_EXC.match(s) or " error:" in s:
      seen.add(s)
      picked.append(s)
  if not picked:  # unknown format: show the tail
    picked = [ln for ln in output.split("\n") if ln.strip()][-max_lines:]
  # keep the first errors (usually the cause) and the final scons lines
  if len(picked) > max_lines:
    picked = picked[:max_lines - 3] + ["..."] + picked[-2:]
  return picked


def build(spinner: Spinner, dirty: bool = False, minimal: bool = False) -> None:
  env = os.environ.copy()
  env['SCONS_PROGRESS'] = "1"
  # BogPilot: at most BOGPILOT_BUILD_JOBS (default 4) jobs on a device, so the comma stays cool and responsive
  nproc = build_jobs(os.cpu_count(), AGNOS)

  extra_args = ["--minimal"] if minimal else []

  # BogPilot: the spinner is also updated by the thermal pause thread
  spinner_lock = threading.Lock()
  cooling: list[str | None] = [None]
  last_progress: list[tuple[float, str]] = [(0.0, "Building BogPilot: starting")]

  def show_cooling(text: str | None) -> None:
    cooling[0] = text
    with spinner_lock:
      pct, msg = last_progress[0]
      spinner.update_progress(pct, 100., text or msg)

  # building with all cores can result in using too
  # much memory, so retry with less parallelism
  compile_output: list[bytes] = []
  for n in dict.fromkeys((nproc, max(1, nproc // 2), 1)):
    compile_output.clear()
    # on a device: own process group, so the thermal pause can stop scons and every compiler it started
    scons: subprocess.Popen = subprocess.Popen(["scons", f"-j{int(n)}", "--cache-populate", *extra_args], cwd=BASEDIR, env=env,
                                               stderr=subprocess.PIPE, start_new_session=AGNOS)
    assert scons.stderr is not None
    pauser = ThermalPauser(scons.pid, status=show_cooling).start() if AGNOS else None

    # Read progress from stderr and update spinner
    while scons.poll() is None:
      try:
        line = scons.stderr.readline()
        if line is None:
          continue
        line = line.rstrip()

        prefix = b'progress: '
        if line.startswith(prefix):
          i = int(line[len(prefix):])
          last_progress[0] = (MAX_BUILD_PROGRESS * min(1., i / TOTAL_SCONS_NODES),
                              f"Building BogPilot: step {min(i, TOTAL_SCONS_NODES)} of {TOTAL_SCONS_NODES}")
          with spinner_lock:
            spinner.update_progress(last_progress[0][0], 100., cooling[0] or last_progress[0][1])
        elif len(line):
          compile_output.append(line)
          print(line.decode('utf8', 'replace'))
      except Exception:
        pass

    if pauser is not None:
      pauser.stop()
    if scons.returncode == 0:
      break

  if scons.returncode != 0:
    # Read remaining output
    if scons.stderr is not None:
      compile_output += scons.stderr.read().split(b'\n')

    # Build failed log errors
    error_s = b"\n".join(compile_output).decode('utf8', 'replace')
    add_file_handler(cloudlog)
    cloudlog.error("scons build failed\n" + error_s)

    # Show TextWindow
    spinner.close()
    if not os.getenv("CI"):
      # BogPilot: put the actual error lines first so they are on screen without scrolling
      summary = "\n".join(error_summary(error_s))[-8000:]
      # a single argv string is capped at 128 KiB by the kernel; keep the tail of the log
      error_s = error_s[-60000:]
      with TextWindow("BogPilot failed to build\n \nErrors:\n" + summary + "\n \nFull output:\n" + error_s) as t:
        t.wait_for_exit()
    exit(1)

  # enforce max cache size
  cache_files = [f for f in CACHE_DIR.rglob('*') if f.is_file()]
  cache_files.sort(key=lambda f: f.stat().st_mtime)
  cache_size = sum(f.stat().st_size for f in cache_files)
  for f in cache_files:
    if cache_size < MAX_CACHE_SIZE:
      break
    cache_size -= f.stat().st_size
    f.unlink()


if __name__ == "__main__":
  spinner = Spinner()
  spinner.update_progress(0, 100, "Building BogPilot: starting")
  build_metadata = get_build_metadata()
  build(spinner, build_metadata.openpilot.is_dirty, minimal = AGNOS)
