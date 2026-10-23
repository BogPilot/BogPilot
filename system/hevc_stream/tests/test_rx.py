"""MM2 receiver (tools/hevc_stream/bp_video_rx.py): letterbox, YOLO post, feeder v2 tracker/direction,
and (with PyAV + BP_HEVC_SEGMENT) a Publisher -> Receiver loopback that checks frameIds survive."""
import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hevc_stream"))
import bp_video_rx as rx  # noqa: E402


def test_letterbox_pads_top_7px_into_416x640():
  img = rx.letterbox(np.full((401, 640, 3), 200, np.uint8))
  assert img.shape == (416, 640, 3) and img.dtype == np.uint8
  assert (img[:7] == 114).all() and (img[7:408] == 200).all() and (img[408:] == 114).all()


def _raw(boxes):
  """boxes: [(cx, cy, w, h, cls, score)] -> (84, N) like yolo11 raw output."""
  y = np.zeros((84, len(boxes)), np.float32)
  for i, (cx, cy, w, h, c, s) in enumerate(boxes):
    y[:4, i] = (cx, cy, w, h); y[4 + c, i] = s
  return y


def test_postprocess_keeps_vehicles_applies_conf_and_nms():
  y = _raw([(320, 250, 60, 40, 2, 0.9), (322, 251, 60, 40, 2, 0.8),  # duplicate car -> NMS
            (100, 250, 30, 60, 0, 0.95),  # person: not a vehicle class
            (500, 250, 80, 60, 7, 0.30)])  # truck under conf 0.35
  d = rx.postprocess(y, 0.35)
  assert len(d) == 1 and d[0][0] == "car" and abs(d[0][1] - 0.9) < 1e-6
  assert np.allclose(d[0][2], [290, 230, 350, 270])


def test_range_from_height_and_ground():
  h = 40.0; b = np.array([300, 300 - h, 340, 300.0])
  d_h = rx.F * 1.5 / h; d_g = rx.F * rx.CAM_H / (300 - rx.CY)
  assert rx.rng("car", b) == pytest.approx(0.5 * (d_h + min(d_g, 2 * d_h)) - 1.0)
  assert rx.rng("car", np.array([300, rx.CY - 40, 340, rx.CY])) == pytest.approx(rx.F * 1.5 / 40 - 1.0)  # above horizon: height only


def test_feeder_ids_hits_and_track_expiry():
  f = rx.FeederV2(); b = np.array([300, 230, 340, 260.0])
  assert f.step([("car", 0.9, b)], 0.0, 0.0) == []  # hits must be >= 2
  out = f.step([("car", 0.9, b + 1)], 0.05, 0.0)
  assert [o["id"] for o in out] == ["L1"] and set(out[0]) == {"id", "cls", "conf", "d_m", "lat_left_m", "v_mps", "dir"}
  f.step([], 1.2, 0.0)  # > 1.0 s unseen -> dropped
  assert f.tracks == {}
  f.step([("car", 0.9, b)], 1.25, 0.0)
  assert "L2" in f.tracks


def test_direction_needs_10_consecutive_frames():
  f = rx.FeederV2(); dirs = []
  for i in range(60):  # parked ego, object closing at ~10 m/s -> oncoming, but only after persistence
    t = i / 20.0; d = 60.0 - 10.0 * t; h = rx.F * 1.5 / (d + 1.0)
    b = np.array([300, 150 - h, 340, 150.0])  # above CY: height-only range
    out = f.step([("car", 0.9, b)], t, 0.0)
    dirs.append(out[0]["dir"] if out else None)
  first = dirs.index("oncoming")
  assert first >= 16  # polyfit needs >= 6 samples spanning > 0.5 s, then 10 frames of persistence
  assert all(x == "oncoming" for x in dirs[first:])


def test_row_format_matches_feeder():
  r = json.loads(rx.row(41, 1.5, 1.6, [], "road", 7, 8))
  assert r["render_t"] == 2.05 and r["frameId"] == 41 and {"capT", "sentT", "objects"} <= set(r)


SEG = Path(os.environ.get("BP_HEVC_SEGMENT", "/nonexistent"))


@pytest.mark.skipif(not SEG.exists(), reason="needs BP_HEVC_SEGMENT (a recorded fcamera .hevc)")
def test_receiver_loopback_carries_frame_ids_and_needs_token():
  pytest.importorskip("av")
  import zmq
  from openpilot.system.hevc_stream.hevc_streamd import Publisher
  from openpilot.system.hevc_stream.synth import encode_events
  log = rx.load_log(str(ROOT / "cereal"))
  idx = [(1000 + 2 * n, 0, 0) for n in range(40)]  # non-contiguous frameIds, contiguous encodeIds
  evs = list(encode_events(log, SEG.read_bytes()[:12_000_000], service="roadEncodeData", max_au=40, idx=idx))

  def run(port, client_token):
    ctx = zmq.Context()
    pub = Publisher("127.0.0.1", port, allow=("127.0.0.1",), token="test-token", ctx=ctx, require_auth=True)
    r = rx.Receiver(f"tcp://127.0.0.1:{port}", "road", client_token, str(ROOT / "cereal"), hw="off", ctx=ctx)
    th = threading.Thread(target=r.run, daemon=True); th.start()
    time.sleep(0.5)
    got = []
    for b, _ in evs:
      pub.send("roadEncodeData", b); time.sleep(0.05)
      s = r.latest(0.0)
      if s:
        got.append(s)
    end = time.monotonic() + 3
    while time.monotonic() < end:
      s = r.latest(0.2)
      if s:
        got.append(s)
    r.stop.set(); th.join(2); pub.close(); ctx.term()
    return got, r

  got, r = run(18791, "test-token")
  fids = [m["fid"] for _, m in got]
  assert len(got) >= 30 and got[-1][0].shape == (416, 640, 3) and got[-1][0][10:].std() > 5
  assert all(f % 2 == 0 and f >= 1000 for f in fids) and fids == sorted(fids) and r.stats["gaps"] == 0
  got, _ = run(18792, "wrong")
  assert got == []
