#!/usr/bin/env python3
"""BogPilot: raylib spinner / progress bar and text window for AGNOS 14+ (17.2).

From AGNOS 14 the "magic" service owns the display (DRM master) and hands it to raylib
clients through /tmp/drmfd.sock. Weston still ships but has no screen, so the Qt
spinner/text binaries draw nothing (black screen). This module replaces them on those
AGNOS versions. Standalone on purpose: stdlib + pyray only, no openpilot imports, so it
also works before anything is built.

  bogpilot_screen.py spinner            read lines from stdin:
                                          "42"               progress 42 %
                                          "p:42|some text"   progress 42 % with a status line
                                          "some text"        status text, no bar
  bogpilot_screen.py text "message"     scrollable text with a Reboot button (exit code 1)

Set BOGPILOT_SCREENSHOT=<png path> to save the rendered frame after ~2 s and on every
"shot" line on stdin (spinner) - used for remote verification.
"""
from __future__ import annotations

import os
import select
import struct
import subprocess
import sys
import time

BASEDIR = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "../"))
ASSETS = os.path.join(BASEDIR, "selfdrive", "assets")
DRM_SOCK = "/tmp/drmfd.sock"
W, H = 2160, 1080

EXIT_BUTTON, EXIT_NO_PYRAY, EXIT_NO_DISPLAY = 1, 3, 4


def magic_available() -> bool:
  """True on AGNOS versions where magic provides the display (it ships /usr/comma/magic.py)."""
  return os.path.isfile("/usr/comma/magic.py")


def ensure_magic(timeout: float = 8.0) -> bool:
  def active() -> bool:
    r = subprocess.run(["systemctl", "is-active", "--quiet", "magic"], check=False)
    return r.returncode == 0 and os.path.exists(DRM_SOCK)
  if active():
    return True
  # something (e.g. the Qt UI on eglfs) stopped magic; bring it back so we can draw
  try:
    old_ino = os.stat(DRM_SOCK).st_ino
  except OSError:
    old_ino = None
  subprocess.run(["sudo", "systemctl", "start", "magic"], check=False,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  end = time.monotonic() + timeout
  while time.monotonic() < end:
    try:
      fresh = os.stat(DRM_SOCK).st_ino != old_ino
    except OSError:
      fresh = False
    if fresh and active():
      time.sleep(0.5)  # magic binds the socket, then listens
      return True
    time.sleep(0.1)
  return False


def parse_spinner_line(line: str):
  """-> (progress or None, text or None, is_shot)"""
  line = line.strip()
  if line == "shot":
    return None, None, True
  if line.isdigit():
    return max(0, min(100, int(line))), None, False
  if line.startswith("p:"):
    pct, _, text = line[2:].partition("|")
    try:
      return max(0, min(100, int(float(pct)))), (text or None), False
    except ValueError:
      pass
  return None, (line or None), False


def wrap(text: str, width: float, measure) -> list[str]:
  out: list[str] = []
  for para in text.split("\n"):
    cur = ""
    for word in para.split(" "):
      cand = word if not cur else cur + " " + word
      if not cur or measure(cand) <= width:
        cur = cand
      else:
        out.append(cur)
        cur = word
    # hard-split very long tokens (paths, compiler lines)
    while cur and measure(cur) > width and len(cur) > 1:
      n = max(1, int(len(cur) * width / max(measure(cur), 1)))
      out.append(cur[:n])
      cur = cur[n:]
    out.append(cur)
  return out


# --- BogPilot: direct touch input (evdev) -------------------------------------------------------
# raylib's own touch input has not always worked under magic, which left the error screen with a
# Reboot button nobody could press. Read the touchscreen directly as well. The comma 3X panel
# reports portrait coordinates (x 0..1080, y 0..2160); the screen is landscape 2160x1080.
EV_KEY, EV_ABS = 0x01, 0x03
BTN_TOUCH = 0x14a
ABS_X, ABS_Y, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x00, 0x01, 0x35, 0x36, 0x39
INPUT_EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux
LONG_PRESS_S, LONG_PRESS_SLOP = 3.0, 120.0
IDLE_FPS, ACTIVE_FPS, ACTIVE_HOLD_S = 8, 30, 2.0


def map_touch(rx: float, ry: float, max_x: float = 1080, max_y: float = 2160, rotation: int = 90) -> tuple[float, float]:
  """raw panel (portrait) -> 2160x1080 landscape screen coordinates. BOGPILOT_TOUCH_ROTATION picks 0/90/180/270."""
  nx, ny = rx / max(max_x, 1), ry / max(max_y, 1)
  rotation %= 360
  if rotation == 90:
    return ny * W, (1 - nx) * H
  if rotation == 270:
    return (1 - ny) * W, nx * H
  if rotation == 180:
    return (1 - nx) * W, (1 - ny) * H
  return nx * W, ny * H


class TouchState:
  """Folds evdev events into press / release with a position. Feed it (type, code, value) tuples."""
  def __init__(self, max_x: float = 1080, max_y: float = 2160, rotation: int = 90):
    self.max_x, self.max_y, self.rotation = max_x, max_y, rotation
    self.rx = self.ry = 0.0
    self.down = False
    self.pending: bool | None = None  # touch state change waiting for the SYN

  def feed(self, etype: int, code: int, value: int) -> list[tuple[str, float, float]]:
    out: list[tuple[str, float, float]] = []
    if etype == EV_ABS and code in (ABS_MT_POSITION_X, ABS_X):
      self.rx = value
    elif etype == EV_ABS and code in (ABS_MT_POSITION_Y, ABS_Y):
      self.ry = value
    elif etype == EV_ABS and code == ABS_MT_TRACKING_ID:
      self.pending = value != -1
    elif etype == EV_KEY and code == BTN_TOUCH:
      self.pending = value != 0
    elif etype == 0:  # EV_SYN: the frame is complete
      if self.pending is not None and self.pending != self.down:
        self.down = self.pending
        x, y = self.pos()
        out.append(("down" if self.down else "up", x, y))
      self.pending = None
    return out

  def pos(self) -> tuple[float, float]:
    return map_touch(self.rx, self.ry, self.max_x, self.max_y, self.rotation)


def find_touchscreen(sys_input: str = "/sys/class/input") -> str | None:
  try:
    names = sorted(n for n in os.listdir(sys_input) if n.startswith("event"))
  except OSError:
    return None
  for n in names:
    try:
      with open(os.path.join(sys_input, n, "device", "name")) as f:
        if "touch" in f.read().lower():
          return os.path.join("/dev/input", n)
    except OSError:
      continue
  return None


class EvdevTouch:
  def __init__(self, path: str | None = None):
    rot = int(os.getenv("BOGPILOT_TOUCH_ROTATION", "90") or 90)
    self.state = TouchState(rotation=rot)
    self.fd = None
    self.buf = b""
    path = path or find_touchscreen()
    if path:
      try:
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self._ranges()
      except OSError:
        self.fd = None

  def _ranges(self) -> None:
    import fcntl
    for code, attr in ((ABS_MT_POSITION_X, "max_x"), (ABS_MT_POSITION_Y, "max_y")):
      buf = bytearray(24)
      try:
        fcntl.ioctl(self.fd, 0x80184540 + code, buf)  # EVIOCGABS(code)
        mx = struct.unpack("6i", buf)[2]
        if mx > 0:
          setattr(self.state, attr, mx)
      except OSError:
        pass

  def poll(self) -> list[tuple[str, float, float]]:
    if self.fd is None:
      return []
    try:
      while True:
        chunk = os.read(self.fd, INPUT_EVENT.size * 64)
        if not chunk:
          break
        self.buf += chunk
    except BlockingIOError:
      pass
    except OSError:
      return []
    out = []
    n = len(self.buf) // INPUT_EVENT.size
    for i in range(n):
      _, _, etype, code, value = INPUT_EVENT.unpack_from(self.buf, i * INPUT_EVENT.size)
      out += self.state.feed(etype, code, value)
    self.buf = self.buf[n * INPUT_EVENT.size:]
    return out


class LongPress:
  """True once a touch has been held LONG_PRESS_S without moving more than LONG_PRESS_SLOP."""
  def __init__(self, hold_s: float = LONG_PRESS_S, slop: float = LONG_PRESS_SLOP):
    self.hold_s, self.slop = hold_s, slop
    self.start: tuple[float, float, float] | None = None

  def update(self, down: bool, x: float, y: float, now: float) -> bool:
    if not down:
      self.start = None
      return False
    if self.start is None or abs(x - self.start[1]) > self.slop or abs(y - self.start[2]) > self.slop:
      self.start = (now, x, y)
      return False
    return now - self.start[0] >= self.hold_s


def pick_fps(now: float, last_input: float) -> int:
  """Full frame rate right after input; otherwise a slow redraw that keeps the CPU free."""
  return ACTIVE_FPS if now - last_input < ACTIVE_HOLD_S else IDLE_FPS


class Screen:
  def __init__(self, title: str):
    import pyray as rl
    self.rl = rl
    rl.set_trace_log_level(rl.TraceLogLevel.LOG_WARNING)
    rl.set_config_flags(rl.ConfigFlags.FLAG_MSAA_4X_HINT)
    rl.init_window(W, H, title)
    if not rl.is_window_ready():
      raise RuntimeError("no display")
    rl.set_target_fps(30)
    self.sw, self.sh = rl.get_screen_width() or W, rl.get_screen_height() or H
    # draw into a fixed 2160x1080 target so screenshots are exactly what is shown
    self.target = rl.load_render_texture(W, H)
    rl.set_texture_filter(self.target.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
    self.fonts: dict = {}
    self.shot_path = os.getenv("BOGPILOT_SCREENSHOT")
    self.t0 = time.monotonic()
    self.auto_shot_done = False
    try:
      with open("/sys/class/backlight/panel0-backlight/bl_power", "w") as f:
        f.write("0")
    except OSError:
      pass

  def font(self, name: str, size: int):
    key = (name, size)
    if key not in self.fonts:
      path = os.path.join(ASSETS, "fonts", name)
      f = self.rl.load_font_ex(path, size, None, 0) if os.path.isfile(path) else self.rl.get_font_default()
      self.rl.set_texture_filter(f.texture, self.rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      self.fonts[key] = f
    return self.fonts[key]

  def text(self, s: str, x: float, y: float, size: int, color, name: str = "Inter-Regular.ttf"):
    self.rl.draw_text_ex(self.font(name, size), s, self.rl.Vector2(x, y), size, 0, color)

  def measure(self, s: str, size: int, name: str = "Inter-Regular.ttf") -> float:
    return self.rl.measure_text_ex(self.font(name, size), s, size, 0).x

  def texture(self, fn: str, size: int):
    rl = self.rl
    path = os.path.join(ASSETS, fn)
    if not os.path.isfile(path):
      return None
    img = rl.load_image(path)
    rl.image_resize(img, size, size)
    tex = rl.load_texture_from_image(img)
    rl.unload_image(img)
    rl.set_texture_filter(tex, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
    return tex

  def touch(self):
    """touch (or mouse) position in 2160x1080 target coordinates"""
    rl = self.rl
    p = rl.get_touch_position(0) if rl.get_touch_point_count() > 0 else rl.get_mouse_position()
    return rl.Vector2(p.x * W / self.sw, p.y * H / self.sh)

  def begin(self):
    self.rl.begin_texture_mode(self.target)
    self.rl.clear_background(self.rl.BLACK)

  def end(self, want_shot: bool = False):
    rl = self.rl
    rl.end_texture_mode()
    rl.begin_drawing()
    rl.clear_background(rl.BLACK)
    rl.draw_texture_pro(self.target.texture, rl.Rectangle(0, 0, W, -H), rl.Rectangle(0, 0, self.sw, self.sh),
                        rl.Vector2(0, 0), 0, rl.WHITE)
    rl.end_drawing()
    if self.shot_path and (want_shot or (not self.auto_shot_done and time.monotonic() - self.t0 > 2.0)):
      self.auto_shot_done = True
      img = rl.load_image_from_texture(self.target.texture)
      rl.image_flip_vertical(img)
      rl.export_image(img, self.shot_path)
      rl.unload_image(img)


def run_spinner() -> int:
  scr = Screen("BogPilot")
  rl = scr.rl
  size = 360
  comma = scr.texture("img_spinner_comma.png", size)
  track = scr.texture("img_spinner_track.png", size)
  progress: int | None = None
  status: str | None = None
  rot = 0.0
  stdin_open = True
  while not rl.window_should_close():
    want_shot = False
    while stdin_open and select.select([sys.stdin], [], [], 0)[0]:
      line = sys.stdin.readline()
      if line == "":
        stdin_open = False  # parent closed the pipe; keep the last frame until killed
        break
      p, t, shot = parse_spinner_line(line)
      want_shot |= shot
      if p is not None:
        progress = p
        status = t
      elif t is not None:
        progress, status = None, t

    scr.begin()
    rot = (rot + 360.0 * rl.get_frame_time()) % 360.0
    cy = H / 2 - 120
    if track is not None:
      rl.draw_texture_pro(track, rl.Rectangle(0, 0, size, size), rl.Rectangle(W / 2, cy, size, size),
                          rl.Vector2(size / 2, size / 2), rot, rl.WHITE)
    if comma is not None:
      rl.draw_texture(comma, int(W / 2 - size / 2), int(cy - size / 2), rl.WHITE)
    y = cy + size / 2 + 70
    title = "BogPilot"
    scr.text(title, W / 2 - scr.measure(title, 70, "Inter-SemiBold.ttf") / 2, 40, 70, rl.Color(255, 255, 255, 200),
             "Inter-SemiBold.ttf")
    if progress is not None:
      bw, bh = 1000, 24
      bar = rl.Rectangle(W / 2 - bw / 2, y, bw, bh)
      rl.draw_rectangle_rounded(bar, 1, 10, rl.Color(55, 55, 55, 255))
      if progress > 0:
        rl.draw_rectangle_rounded(rl.Rectangle(bar.x, bar.y, max(bh, bw * progress / 100), bh), 1, 10, rl.WHITE)
      pct = f"{progress}%"
      scr.text(pct, W / 2 - scr.measure(pct, 56) / 2, y + 50, 56, rl.WHITE)
      y += 140
    if status:
      for ln in wrap(status, W - 200, lambda s: scr.measure(s, 56))[:3]:
        scr.text(ln, W / 2 - scr.measure(ln, 56) / 2, y, 56, rl.Color(200, 200, 200, 255))
        y += 66
    scr.end(want_shot)
  return 0


def touch_logger():
  """BOGPILOT_TOUCH_LOG=<file> (default /tmp/bogpilot_touch.log on a device): one line per press/release"""
  path = os.getenv("BOGPILOT_TOUCH_LOG", "/tmp/bogpilot_touch.log" if os.path.isfile("/AGNOS") else "")
  def log(msg: str) -> None:
    if path:
      try:
        with open(path, "a") as f:
          f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
      except OSError:
        pass
  return log


def do_reboot(log) -> None:
  if os.path.isfile("/AGNOS") and not os.getenv("BOGPILOT_NO_REBOOT"):
    log("reboot")
    subprocess.run(["sudo", "reboot"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


def run_text(message: str) -> int:
  scr = Screen("BogPilot")
  rl = scr.rl
  log = touch_logger()
  log(f"text window up, screen {scr.sw}x{scr.sh}")
  # BogPilot: never leave a dead screen. Reboot by itself after this many minutes (0 = never);
  # BOGPILOT_ERROR_REBOOT_MIN overrides, and the build caller limits it to once per failure streak.
  auto_min = float(os.getenv("BOGPILOT_ERROR_REBOOT_MIN", "0") or 0)
  deadline = time.monotonic() + auto_min * 60 if auto_min > 0 else None
  pad, fs, lh = 60, 44, 54
  mono = "JetBrainsMono-Medium.ttf"
  lines = wrap(message, W - 2 * pad - 40, lambda s: scr.measure(s, fs, mono))
  btn = rl.Rectangle(W - pad - 420, H - pad - 150, 420, 150)
  logo = scr.texture("img_spinner_comma.png", 150)  # BogPilot mark, bottom-left of the button row
  view_h = H - 2 * pad - 180
  max_scroll = max(0, len(lines) * lh - view_h)
  scroll = 0.0  # build.py puts the error summary first
  drag_y = None
  hit = rl.Rectangle(btn.x - 120, btn.y - 120, btn.width + 120 + pad, btn.height + 120 + pad)  # generous tap area
  touch_dev = os.getenv("BOGPILOT_TOUCH_DEV")
  ev = EvdevTouch(touch_dev) if (touch_dev or os.path.isfile("/AGNOS")) else None
  log(f"evdev touch {'on' if ev is not None and ev.fd is not None else 'off'}")
  hold = LongPress()
  last_input = time.monotonic()
  fps = ACTIVE_FPS
  while not rl.window_should_close():
    now = time.monotonic()
    if deadline is not None and now > deadline:
      log("auto reboot timeout")
      do_reboot(log)
      return EXIT_BUTTON
    p = scr.touch()
    rl_down = rl.is_mouse_button_down(rl.MouseButton.MOUSE_BUTTON_LEFT)
    if rl.is_mouse_button_pressed(rl.MouseButton.MOUSE_BUTTON_LEFT):
      log(f"press {p.x:.0f},{p.y:.0f}")
      drag_y = p.y
      last_input = now
    elif rl_down and drag_y is not None:
      scroll = max(0.0, min(float(max_scroll), scroll - (p.y - drag_y)))
      drag_y = p.y
      last_input = now
    if rl.is_mouse_button_released(rl.MouseButton.MOUSE_BUTTON_LEFT):
      drag_y = None
      last_input = now
      on_btn = rl.check_collision_point_rec(p, hit)
      log(f"release {p.x:.0f},{p.y:.0f} button={'yes' if on_btn else 'no'}")
      if on_btn:
        do_reboot(log)
        return EXIT_BUTTON
    # direct touchscreen: a tap on Reboot, and a long press anywhere, reboot even if raylib sees nothing
    ev_down = False
    if ev is not None:
      for kind, x, y in ev.poll():
        last_input = now
        on_btn = rl.check_collision_point_rec(rl.Vector2(x, y), hit)
        log(f"evdev {kind} {x:.0f},{y:.0f} button={'yes' if on_btn else 'no'}")
        if kind == "up" and on_btn:
          do_reboot(log)
          return EXIT_BUTTON
      ev_down = ev.state.down
    if ev_down:
      hx, hy = ev.state.pos()
    else:
      hx, hy = p.x, p.y
    if hold.update(ev_down or rl_down, hx, hy, now):
      log("long press")
      do_reboot(log)
      return EXIT_BUTTON
    want = pick_fps(now, last_input)
    if want != fps:
      fps = want
      rl.set_target_fps(fps)
    scr.begin()
    y = pad - scroll
    for ln in lines:
      if pad - 1 <= y and y + lh <= pad + view_h + 1:
        col = rl.Color(255, 110, 110, 255) if ("error" in ln.lower() or "failed" in ln.lower()) else rl.WHITE
        scr.text(ln, pad, y, fs, col, mono)
      y += lh
    if max_scroll > 0:
      th = max(60, view_h * view_h / (view_h + max_scroll))
      ty = pad + (view_h - th) * (scroll / max_scroll)
      rl.draw_rectangle_rounded(rl.Rectangle(W - 24, ty, 12, th), 1, 6, rl.Color(120, 120, 120, 255))
    if logo is not None:
      rl.draw_texture(logo, int(pad), int(btn.y), rl.WHITE)
    scr.text("BogPilot", pad + 160, btn.y + 45, 60, rl.Color(255, 255, 255, 200), "Inter-SemiBold.ttf")
    rl.draw_rectangle_rounded(btn, 0.3, 10, rl.Color(70, 70, 70, 255))
    label = "Reboot" if os.path.isfile("/AGNOS") else "Exit"
    if deadline is not None:
      left = max(0, int(deadline - time.monotonic()))
      note = f"Rebooting by itself in {left // 60}:{left % 60:02d}"
      scr.text(note, btn.x - 40 - scr.measure(note, 44), btn.y + 22, 44, rl.Color(170, 170, 170, 255))
    if os.path.isfile("/AGNOS"):
      note = "or hold anywhere for 3 seconds"
      scr.text(note, btn.x - 40 - scr.measure(note, 36), btn.y + 92, 36, rl.Color(150, 150, 150, 255))
    scr.text(label, btn.x + (btn.width - scr.measure(label, 64, "Inter-SemiBold.ttf")) / 2, btn.y + 40, 64, rl.WHITE,
             "Inter-SemiBold.ttf")
    scr.end()
  return EXIT_BUTTON


def main(argv: list[str]) -> int:
  if len(argv) < 2 or argv[1] not in ("spinner", "text"):
    print(__doc__, file=sys.stderr)
    return 2
  try:
    import pyray  # noqa: F401
  except Exception as e:
    print(f"bogpilot_screen: pyray unavailable: {e}", file=sys.stderr)
    return EXIT_NO_PYRAY
  if magic_available() and not ensure_magic():
    print("bogpilot_screen: magic display server not available", file=sys.stderr)
  try:
    if argv[1] == "spinner":
      return run_spinner()
    return run_text(" ".join(argv[2:]) if len(argv) > 2 else sys.stdin.read())
  except RuntimeError as e:
    print(f"bogpilot_screen: {e}", file=sys.stderr)
    return EXIT_NO_DISPLAY


if __name__ == "__main__":
  sys.exit(main(sys.argv))
