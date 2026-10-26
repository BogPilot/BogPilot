"""BogPilot: error screen input helpers (evdev touch, long press, idle frame rate). No display needed."""
import importlib.util
import os

_P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bogpilot_screen.py")
_spec = importlib.util.spec_from_file_location("bogpilot_screen", _P)
s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s)


def _w(path, data):
  with open(path, "w") as f:
    f.write(data)


def _r(path):
  with open(path) as f:
    return f.read()


def ev(t, c, v):
  return s.INPUT_EVENT.pack(0, 0, t, c, v)


class TestMap:
  def test_rotations(self):
    assert s.map_touch(0, 0, rotation=90) == (0.0, 1080.0)
    assert s.map_touch(1080, 2160, rotation=90) == (2160.0, 0.0)
    assert s.map_touch(540, 1080, rotation=270) == (1080.0, 540.0)
    assert s.map_touch(0, 0, rotation=180) == (2160.0, 1080.0)
    assert s.map_touch(1080, 2160, rotation=0) == (2160.0, 1080.0)


class TestTouchState:
  def test_mt_tap(self):
    st = s.TouchState(rotation=0)
    out = []
    for e in ((s.EV_ABS, s.ABS_MT_TRACKING_ID, 7), (s.EV_ABS, s.ABS_MT_POSITION_X, 540), (s.EV_ABS, s.ABS_MT_POSITION_Y, 1080),
              (s.EV_KEY, s.BTN_TOUCH, 1), (0, 0, 0)):
      out += st.feed(*e)
    assert out == [("down", 1080.0, 540.0)]
    assert st.down
    out = st.feed(s.EV_ABS, s.ABS_MT_TRACKING_ID, -1) + st.feed(s.EV_KEY, s.BTN_TOUCH, 0) + st.feed(0, 0, 0)
    assert out == [("up", 1080.0, 540.0)]
    assert not st.down

  def test_move_without_state_change(self):
    st = s.TouchState(rotation=0)
    st.feed(s.EV_KEY, s.BTN_TOUCH, 1)
    st.feed(0, 0, 0)
    assert (st.feed(s.EV_ABS, s.ABS_MT_POSITION_X, 10) + st.feed(0, 0, 0)) == []


class TestEvdevPipe:
  def test_poll_split_reads(self):
    r, w = os.pipe()
    os.set_blocking(r, False)
    t = s.EvdevTouch.__new__(s.EvdevTouch)
    t.state, t.fd, t.buf = s.TouchState(rotation=90), r, b""
    data = ev(s.EV_ABS, s.ABS_MT_TRACKING_ID, 1) + ev(s.EV_ABS, s.ABS_MT_POSITION_X, 108) + \
      ev(s.EV_ABS, s.ABS_MT_POSITION_Y, 2052) + ev(0, 0, 0)
    os.write(w, data[:30])
    assert t.poll() == []
    os.write(w, data[30:])
    (kind, x, y), = t.poll()
    assert kind == "down"
    assert abs(x - (2052 / 2160 * 2160)) < 1e-6
    assert abs(y - ((1 - 108 / 1080) * 1080)) < 1e-6
    os.close(w)
    os.close(r)

  def test_no_device(self):
    t = s.EvdevTouch("/nonexistent/event9")
    assert t.fd is None
    assert t.poll() == []

  def test_find_touchscreen(self):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
      for n, name in (("event0", "qpnp_pon"), ("event2", "Samsung Touchscreen")):
        os.makedirs(os.path.join(d, n, "device"))
        _w(os.path.join(d, n, "device", "name"), name + "\n")
      assert s.find_touchscreen(d) == "/dev/input/event2"
      assert s.find_touchscreen(os.path.join(d, "missing")) is None


class TestLongPress:
  def test_hold(self):
    lp = s.LongPress(3.0, 120)
    assert not lp.update(True, 100, 100, 0)
    assert not lp.update(True, 150, 120, 2.9)
    assert lp.update(True, 150, 120, 3.0)

  def test_release_or_move_resets(self):
    lp = s.LongPress(3.0, 120)
    lp.update(True, 100, 100, 0)
    assert not lp.update(False, 0, 0, 1)
    assert not lp.update(True, 100, 100, 2)
    assert not lp.update(True, 400, 100, 4)   # moved: a drag, not a hold
    assert not lp.update(True, 400, 100, 6.9)
    assert lp.update(True, 400, 100, 7.0)


class TestFps:
  def test_idle_between_5_and_10(self):
    assert (5 <= s.IDLE_FPS <= 10)
    assert s.pick_fps(100.0, 99.5) == s.ACTIVE_FPS
    assert s.pick_fps(100.0, 90.0) == s.IDLE_FPS
