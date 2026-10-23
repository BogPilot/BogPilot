#!/usr/bin/env python3
"""Bench: replay a recorded camera .hevc through the real hevc_streamd Publisher (auth required,
127.0.0.1 only) at 20 fps, as roadEncodeData (or --service). Each Event's logMonoTime and
idx.timestampEof are stamped with this host's monotonic clock at send time, so a receiver on the
same host (bp_video_rx.py --same-clock) can measure end-to-end latency.

  BP_STREAM_TOKEN=bench PYTHONPATH=<dir containing openpilot/> python bench_replay.py --hevc <fcamera.hevc>
No recording paths are stored; nothing is written."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from openpilot.system.hevc_stream.hevc_streamd import Publisher
from openpilot.system.hevc_stream.synth import KEYFRAME, access_units

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bp_video_rx import load_log  # noqa: E402


def main(argv=None):
  ap = argparse.ArgumentParser()
  ap.add_argument("--hevc", required=True)
  ap.add_argument("--port", type=int, default=18765)
  ap.add_argument("--service", default="roadEncodeData")
  ap.add_argument("--frames", type=int, default=600)
  ap.add_argument("--hz", type=float, default=20.0)
  ap.add_argument("--lead-s", type=float, default=1.5, help="wait for the subscriber to connect")
  ap.add_argument("--cereal")
  a = ap.parse_args(argv)
  token = os.environ.get("BP_STREAM_TOKEN", "")
  log = load_log(a.cereal)
  aus = list(access_units(Path(a.hevc).read_bytes(), a.frames))
  pub = Publisher("127.0.0.1", a.port, allow=("127.0.0.1",), token=token, require_auth=True)
  time.sleep(a.lead_s)
  period = 1.0 / a.hz; t_next = time.monotonic(); late = 0
  for n, (hdr, dat, key) in enumerate(aus):
    now = time.monotonic()
    if now < t_next:
      time.sleep(t_next - now)
    elif now - t_next > period:
      late += 1
    t_next += period
    ev = log.Event.new_message()
    ts = time.monotonic_ns()
    ev.logMonoTime = ts; ev.valid = True
    e = ev.init(a.service)
    e.idx.frameId = n; e.idx.encodeId = n; e.idx.timestampEof = ts; e.idx.timestampSof = ts
    e.idx.flags = KEYFRAME if key else 0
    e.width, e.height = 1928, 1208
    if key:
      e.header = hdr
    e.data = dat
    pub.send(a.service, ev.to_bytes())
  time.sleep(0.5)
  pub.close()
  print(f"sent {len(aus)} frames, {late} late ticks", file=sys.stderr)


if __name__ == "__main__":
  main()
