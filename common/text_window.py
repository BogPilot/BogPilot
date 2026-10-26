#!/usr/bin/env python3
import os
import sys
import time
import subprocess
from openpilot.common.basedir import BASEDIR
from openpilot.common.bogpilot_screen import magic_available


class TextWindow:
  def __init__(self, text):
    try:
      if magic_available():
        # BogPilot: AGNOS 14+ (17.2): the Qt text window has no screen there; use raylib
        self.text_proc = subprocess.Popen([sys.executable, os.path.join(BASEDIR, "common", "bogpilot_screen.py"), "text", text],
                                          stdin=subprocess.PIPE, cwd=BASEDIR, close_fds=True)
      else:
        self.text_proc = subprocess.Popen(["./text", text],
                                          stdin=subprocess.PIPE,
                                          cwd=os.path.join(BASEDIR, "selfdrive", "ui"),
                                          close_fds=True)
    except OSError:
      self.text_proc = None

  def get_status(self):
    if self.text_proc is not None:
      self.text_proc.poll()
      return self.text_proc.returncode
    return None

  def __enter__(self):
    return self

  def close(self):
    if self.text_proc is not None:
      self.text_proc.terminate()
      self.text_proc = None

  def wait_for_exit(self):
    if self.text_proc is not None:
      while True:
        # BogPilot: any exit ends the wait (a crashed or display-less text window must not hang the caller)
        if self.get_status() is not None:
          return
        time.sleep(0.1)

  def __del__(self):
    self.close()

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()


if __name__ == "__main__":
  text = """Traceback (most recent call last):
  File "./controlsd.py", line 608, in <module>
    main()
  File "./controlsd.py", line 604, in main
    controlsd_thread(sm, pm, logcan)
  File "./controlsd.py", line 455, in controlsd_thread
    1/0
ZeroDivisionError: division by zero"""
  print(text)

  with TextWindow(text) as s:
    for _ in range(100):
      if s.get_status() == 1:
        print("Got exit button")
        break
      time.sleep(0.1)
  print("gone")
