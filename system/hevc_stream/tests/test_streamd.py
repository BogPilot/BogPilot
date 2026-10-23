"""Frame-drop behaviour: the streamer never queues stale frames and never blocks on a slow client."""
import time

import zmq

from openpilot.system.hevc_stream import config as c
from openpilot.system.hevc_stream.hevc_streamd import Publisher, newest


def test_newest_keeps_latest_burst():
  assert newest([]) == ([], 0)
  assert newest([1, 2]) == ([1, 2], 0)
  assert newest(list(range(10))) == ([8, 9], 8)
  assert newest(list(range(10)), max_burst=1) == ([9], 9)


def test_pub_never_blocks_and_drops_for_slow_subscriber():
  ctx = zmq.Context()
  pub = Publisher("127.0.0.1", 18771, allow=("127.0.0.1",), token="t", ctx=ctx, require_auth=True)
  sub = ctx.socket(zmq.SUB)
  sub.setsockopt(zmq.PLAIN_USERNAME, b"bogpilot")
  sub.setsockopt(zmq.PLAIN_PASSWORD, b"t")
  sub.setsockopt(zmq.RCVHWM, 4)
  sub.setsockopt(zmq.RCVBUF, 65536)
  sub.setsockopt(zmq.SUBSCRIBE, b"roadEncodeData")
  sub.connect("tcp://127.0.0.1:18771")
  time.sleep(0.5)
  payload = b"x" * 200_000  # ~ a large fcamera keyframe
  n = 400
  t0 = time.perf_counter()
  for _ in range(n):
    pub.send("roadEncodeData", payload)  # subscriber reads nothing meanwhile
  dt = time.perf_counter() - t0
  time.sleep(0.3)
  got = 0
  while sub.poll(100):
    sub.recv_multipart()
    got += 1
  pub.close()
  sub.close(0)
  ctx.term()
  assert pub.sent == n and dt < 2.0      # never waited on the slow client
  assert 0 < got < n // 4                # most frames dropped, not queued
  assert c.SNDHWM <= 4
