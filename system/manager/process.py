import importlib
import os
import signal
import struct
import sys
import time
import subprocess
from collections.abc import Callable, ValuesView
from abc import ABC, abstractmethod
from multiprocessing import Process

from setproctitle import setproctitle

from cereal import car, log
import cereal.messaging as messaging
import openpilot.system.sentry as sentry
from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

WATCHDOG_FN = "/dev/shm/wd_"
ENABLE_WATCHDOG = os.getenv("NO_WATCHDOG") is None


def launcher(proc: str, name: str) -> None:
  try:
    # import the process
    mod = importlib.import_module(proc)

    # rename the process
    setproctitle(proc)

    # create new context since we forked
    messaging.context = messaging.Context()

    # add daemon name tag to logs
    cloudlog.bind(daemon=name)
    sentry.set_tag("daemon", name)

    # exec the process
    mod.main()
  except KeyboardInterrupt:
    cloudlog.warning(f"child {proc} got SIGINT")
  except Exception:
    # can't install the crash handler because sys.excepthook doesn't play nice
    # with threads, so catch it here.
    sentry.capture_exception()
    raise


def nativelauncher(pargs: list[str], cwd: str, name: str) -> None:
  os.environ['MANAGER_DAEMON'] = name

  # exec the process
  os.chdir(cwd)
  os.execvp(pargs[0], pargs)


def join_process(process: Process, timeout: float) -> None:
  # Process().join(timeout) will hang due to a python 3 bug: https://bugs.python.org/issue28382
  # We have to poll the exitcode instead
  t = time.monotonic()
  while time.monotonic() - t < timeout and process.exitcode is None:
    time.sleep(0.001)


class ManagerProcess(ABC):
  daemon = False
  sigkill = False
  should_run: Callable[[bool, Params, car.CarParams], bool]
  proc: Process | None = None
  enabled = True
  name = ""

  last_watchdog_time = 0
  watchdog_max_dt: int | None = None
  watchdog_seen = False
  shutting_down = False

  @abstractmethod
  def prepare(self) -> None:
    pass

  @abstractmethod
  def start(self) -> None:
    pass

  def restart(self) -> None:
    self.stop(sig=signal.SIGKILL)
    self.start()

  def check_watchdog(self, started: bool) -> None:
    if self.watchdog_max_dt is None or self.proc is None:
      return

    try:
      fn = WATCHDOG_FN + str(self.proc.pid)
      with open(fn, "rb") as f:
        # TODO: why can't pylint find struct.unpack?
        self.last_watchdog_time = struct.unpack('Q', f.read())[0]
    except Exception:
      pass

    dt = time.monotonic() - self.last_watchdog_time / 1e9

    if dt > self.watchdog_max_dt:
      if self.watchdog_seen and ENABLE_WATCHDOG:
        cloudlog.error(f"Watchdog timeout for {self.name} (exitcode {self.proc.exitcode}) restarting ({started=})")
        self.restart()
    else:
      self.watchdog_seen = True

  def stop(self, retry: bool = True, block: bool = True, sig: signal.Signals = None) -> int | None:
    if self.proc is None:
      return None

    if self.proc.exitcode is None:
      if not self.shutting_down:
        cloudlog.info(f"killing {self.name}")
        if sig is None:
          sig = signal.SIGKILL if self.sigkill else signal.SIGINT
        self.signal(sig)
        self.shutting_down = True

        if not block:
          return None

      join_process(self.proc, 5)

      # If process failed to die send SIGKILL
      if self.proc.exitcode is None and retry:
        cloudlog.info(f"killing {self.name} with SIGKILL")
        self.signal(signal.SIGKILL)
        self.proc.join()

    ret = self.proc.exitcode
    cloudlog.info(f"{self.name} is dead with {ret}")

    if self.proc.exitcode is not None:
      self.shutting_down = False
      self.proc = None

    return ret

  def signal(self, sig: int) -> None:
    if self.proc is None:
      return

    # Don't signal if already exited
    if self.proc.exitcode is not None and self.proc.pid is not None:
      return

    # Can't signal if we don't have a pid
    if self.proc.pid is None:
      return

    cloudlog.info(f"sending signal {sig} to {self.name}")
    os.kill(self.proc.pid, sig)

  def get_process_state_msg(self):
    state = log.ManagerState.ProcessState.new_message()
    state.name = self.name
    if self.proc:
      state.running = self.proc.is_alive()
      state.shouldBeRunning = self.proc is not None and not self.shutting_down
      state.pid = self.proc.pid or 0
      state.exitCode = self.proc.exitcode or 0
    return state


class NativeProcess(ManagerProcess):
  def __init__(self, name, cwd, cmdline, should_run, enabled=True, sigkill=False, watchdog_max_dt=None):
    self.name = name
    self.cwd = cwd
    self.cmdline = cmdline
    self.should_run = should_run
    self.enabled = enabled
    self.sigkill = sigkill
    self.watchdog_max_dt = watchdog_max_dt
    self.launcher = nativelauncher

  def prepare(self) -> None:
    pass

  def start(self) -> None:
    # In case we only tried a non blocking stop we need to stop it before restarting
    if self.shutting_down:
      self.stop()

    if self.proc is not None:
      return

    cwd = os.path.join(BASEDIR, self.cwd)
    cloudlog.info(f"starting process {self.name}")
    self.proc = Process(name=self.name, target=self.launcher, args=(self.cmdline, cwd, self.name))
    self.proc.start()
    self.watchdog_seen = False
    self.shutting_down = False


class PythonProcess(ManagerProcess):
  def __init__(self, name, module, should_run, enabled=True, sigkill=False, watchdog_max_dt=None):
    self.name = name
    self.module = module
    self.should_run = should_run
    self.enabled = enabled
    self.sigkill = sigkill
    self.watchdog_max_dt = watchdog_max_dt
    self.launcher = launcher

  def prepare(self) -> None:
    if self.enabled:
      cloudlog.info(f"preimporting {self.module}")
      importlib.import_module(self.module)

  def start(self) -> None:
    # In case we only tried a non blocking stop we need to stop it before restarting
    if self.shutting_down:
      self.stop()

    if self.proc is not None:
      return

    cloudlog.info(f"starting python {self.module}")
    self.proc = Process(name=self.name, target=self.launcher, args=(self.module, self.name))
    self.proc.start()
    self.watchdog_seen = False
    self.shutting_down = False


class DaemonProcess(ManagerProcess):
  """Python process that has to stay running across manager restart.
  This is used for athena so you don't lose SSH access when restarting manager."""
  def __init__(self, name, module, param_name, enabled=True):
    self.name = name
    self.module = module
    self.param_name = param_name
    self.enabled = enabled
    self.params = None

  @staticmethod
  def should_run(started, params, CP, classic_model, tinygrad_model, frogpilot_toggles):
    return True

  def prepare(self) -> None:
    pass

  def start(self) -> None:
    if self.params is None:
      self.params = Params()

    pid = self.params.get(self.param_name, encoding='utf-8')
    if pid is not None:
      try:
        os.kill(int(pid), 0)
        with open(f'/proc/{pid}/cmdline') as f:
          if self.module in f.read():
            # daemon is running
            return
      except (OSError, FileNotFoundError):
        # process is dead
        pass

    cloudlog.info(f"starting daemon {self.name}")
    proc = subprocess.Popen(['python', '-m', self.module],
                               stdin=open('/dev/null'),
                               stdout=open('/dev/null', 'w'),
                               stderr=open('/dev/null', 'w'),
                               preexec_fn=os.setpgrp)

    self.params.put(self.param_name, str(proc.pid))

  def stop(self, retry=True, block=True, sig=None) -> None:
    pass


RESTART_IF_CRASH = {"qcomgpsd"}

# BogPilot (AGNOS 17.2, Qt on DRM): a ui that exits never comes back by itself (start() is a no-op
# while self.proc is set, and the watchdog only restarts a ui that has sent a heartbeat), so the
# screen stayed on the last splash frame with no message. Free the display and relaunch it a few
# times; if it keeps dying, put an error on screen instead of a frozen splash.
UI_RESTART_MAX, UI_RESTART_GAP_S = 3, 10.0


def bogpilot_ui_guard(p: ManagerProcess) -> None:
  if os.getenv("BOGPILOT_QT_EGLFS") != "1" or p.proc is None or p.proc.exitcode is None:
    return
  if getattr(p, "_ui_error_shown", False):
    return
  now = time.monotonic()
  if now - getattr(p, "_ui_last_restart", 0.0) < UI_RESTART_GAP_S:
    return
  if p.watchdog_seen:
    p._ui_failures = 0  # it ran normally before; count from here
  code = p.proc.exitcode
  p._ui_failures = getattr(p, "_ui_failures", 0) + 1
  if p._ui_failures > UI_RESTART_MAX:
    cloudlog.error(f"ui keeps exiting (last exit code {code}), showing an error")
    p._ui_error_shown = True
    try:
      # the raylib error screen does not need the Qt flip shim
      env = dict(os.environ)
      pre = [x for x in env.get("LD_PRELOAD", "").split(":") if x and "bogpilot_drm_noflipevent" not in x]
      if pre:
        env["LD_PRELOAD"] = ":".join(pre)
      else:
        env.pop("LD_PRELOAD", None)
      subprocess.Popen([sys.executable, os.path.join(BASEDIR, "common", "bogpilot_screen.py"), "text",
                        "BogPilot's screen could not start.\n\n"
                        f"The UI exited {UI_RESTART_MAX + 1} times in a row (last exit code {code}).\n"
                        "Reboot to try again. If this keeps happening, reinstall BogPilot."],
                       cwd=BASEDIR, close_fds=True, env=env)
    except Exception:
      cloudlog.exception("could not show the ui error screen")
    return
  cloudlog.error(f"ui exited with {code}, restarting ({p._ui_failures}/{UI_RESTART_MAX})")
  p._ui_last_restart = now
  try:
    from openpilot.common.bogpilot_screen import release_magic_for_qt
    release_magic_for_qt()
  except Exception:
    pass
  p.stop(block=True)


def ensure_running(procs: ValuesView[ManagerProcess], started: bool, params=None, CP: car.CarParams=None,
                   not_run: list[str] | None=None, classic_model=False, tinygrad_model=False, frogpilot_toggles=None) -> list[ManagerProcess]:
  if not_run is None:
    not_run = []

  running = []
  for p in procs:
    if p.enabled and p.name not in not_run and p.should_run(started, params, CP, classic_model, tinygrad_model, frogpilot_toggles):
      running.append(p)
    else:
      p.stop(block=False)

    p.check_watchdog(started)

  for p in running:
    # BogPilot: a crashed qcomgpsd used to stay dead for the rest of the drive (start() is a no-op
    # while self.proc is set). Reap and relaunch it, at most once every 10 s.
    if p.name in RESTART_IF_CRASH and p.proc is not None and p.proc.exitcode not in (None, 0):
      now = time.monotonic()
      if now - getattr(p, "_last_crash_restart", 0.0) > 10.0:
        cloudlog.error(f"{p.name} exited with {p.proc.exitcode} while it should run, restarting")
        p._last_crash_restart = now
        p.stop(block=True)
    if p.name == "ui":
      bogpilot_ui_guard(p)
    p.start()

  return running
