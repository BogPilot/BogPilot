import os
import subprocess
import sys
from openpilot.common.basedir import BASEDIR
from openpilot.common.bogpilot_screen import magic_available


class Spinner:
  def __init__(self):
    # BogPilot: on AGNOS 14+ (17.2) the display belongs to magic/raylib and the Qt spinner
    # would draw nothing, so use the raylib spinner there.
    self.raylib = magic_available()
    try:
      if self.raylib:
        self.spinner_proc = subprocess.Popen([sys.executable, os.path.join(BASEDIR, "common", "bogpilot_screen.py"), "spinner"],
                                             stdin=subprocess.PIPE, cwd=BASEDIR, close_fds=True)
      else:
        self.spinner_proc = subprocess.Popen(["./spinner"],
                                             stdin=subprocess.PIPE,
                                             cwd=os.path.join(BASEDIR, "selfdrive", "ui"),
                                             close_fds=True)
    except OSError:
      self.spinner_proc = None

  def __enter__(self):
    return self

  def update(self, spinner_text: str):
    if self.spinner_proc is not None:
      self.spinner_proc.stdin.write(spinner_text.encode('utf8') + b"\n")
      try:
        self.spinner_proc.stdin.flush()
      except BrokenPipeError:
        pass

  def update_progress(self, cur: float, total: float, text: str | None = None):
    pct = str(round(100 * cur / total))
    # the raylib spinner can show a status line under the bar; the Qt one only takes a number
    self.update(f"p:{pct}|{text}" if (text and self.raylib) else pct)

  def close(self):
    if self.spinner_proc is not None:
      self.spinner_proc.kill()
      try:
        self.spinner_proc.communicate(timeout=2.)
      except subprocess.TimeoutExpired:
        print("WARNING: failed to kill spinner")
      self.spinner_proc = None

  def __del__(self):
    self.close()

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()


if __name__ == "__main__":
  import time
  with Spinner() as s:
    s.update("Spinner text")
    time.sleep(5.0)
  print("gone")
  time.sleep(5.0)
