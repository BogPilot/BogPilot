"""Loopback: a real recorded ecamera.hevc segment's AUs -> Publisher (127.0.0.1) -> mac_client -> ffmpeg decode."""
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
import zmq

from openpilot.system.hevc_stream.hevc_streamd import Publisher
from openpilot.system.hevc_stream.synth import encode_events

ROOT = Path(__file__).resolve().parents[3]
SEG = Path(os.environ.get("BP_HEVC_SEGMENT", "/workspace/realdata/hevc_segment/ecamera.hevc"))
sys.path.insert(0, str(ROOT / "tools/hevc_stream"))

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or not SEG.exists(), reason="needs ffmpeg + BP_HEVC_SEGMENT (a recorded ecamera.hevc)")
N = 40


def _run(port, token=None, allow=(), client_token=None, hz=20.0):
  import mac_client
  ctx = zmq.Context()
  pub = Publisher("127.0.0.1", port, allow=allow, token=token, ctx=ctx)
  log = mac_client.load_log(str(ROOT / "cereal"))
  evs = list(encode_events(log, SEG.read_bytes()[:12_000_000], max_au=N))
  cl = mac_client.Client(f"tcp://127.0.0.1:{port}", "wide", str(ROOT / "cereal"), client_token, hw=False, ctx=ctx)
  stop = threading.Event()

  def feed():
    time.sleep(0.5)  # slow-joiner
    for b, tlog in evs:
      if stop.is_set():
        return
      cs = log.Event.new_message(logMonoTime=tlog)
      cs.init("carState").vEgo = 5.0
      pub.send("carState", cs.to_bytes())
      pub.send("wideRoadEncodeData", b)
      time.sleep(1 / hz)
  th = threading.Thread(target=feed, daemon=True)
  th.start()
  got = []
  deadline = time.monotonic() + 15
  def run():
    for img, meta in cl.frames(max_frames=N - 5):
      got.append((img, meta))
  t = threading.Thread(target=run, daemon=True)
  t.start()
  t.join(max(0.1, deadline - time.monotonic()))
  stop.set()
  pub.close()
  return got, len(evs)


def test_loopback_decodes_full_res_frames():
  got, n = _run(18766)
  assert n == N and len(got) >= N - 10
  img, meta = got[-1]
  assert img.shape == (1208, 1928, 3) and img.std() > 5  # real picture, not a gray/blank frame
  assert meta["camera"] == "wide" and meta["img_w"] == 1928 and meta["img_h"] == 1208
  assert meta["v_ego"] == 5.0 and meta["frame_id"] is not None


def test_token_required():
  got, _ = _run(18767, token="s3cret", client_token="wrong")
  assert got == []
  got, _ = _run(18768, token="s3cret", client_token="s3cret")
  assert len(got) >= N - 10


def test_ip_allowlist_denies_other_hosts():
  got, _ = _run(18769, allow=("127.0.0.2",))
  assert got == []
