#!/usr/bin/env python3
"""BogPilot hevc_streamd reference client (Mac; also runs on Linux for tests).

Subscribes to the comma's hevc_streamd, pipes one camera's HEVC into ffmpeg
(VideoToolbox hardware decode on macOS: `-hwaccel videotoolbox`), and yields
decoded BGR frames with the encoder timestamps and the nearest carState v_ego.

  python mac_client.py --host 192.168.8.20 --camera wide --jsonl frames.jsonl
  python mac_client.py --host ... --camera wide --yolo yolo11m.pt --jsonl feeder.jsonl

JSONL lines match Buddy's feeder format (export_seg56_wide.py):
  {"frame","frame_id","t","t_mono_ns","camera":"wide","img_w":1928,"img_h":1208,
   "v_ego","standstill","steering_angle_deg","objects":[...]}
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

import capnp
import zmq

TOPIC = {"wide": "wideRoadEncodeData", "road": "roadEncodeData", "driver": "driverEncodeData"}
STATE = ("carState", "liveCalibration", "radarState", "modelV2")
KEYFRAME = 8  # V4L2_BUF_FLAG_KEYFRAME


def load_log(cereal_dir: str | None):
  capnp.remove_import_hook()
  cands = [cereal_dir, os.environ.get("CEREAL_DIR"), str(Path(__file__).resolve().parents[2] / "cereal")]
  for d in cands:
    if d and Path(d, "log.capnp").exists():
      return capnp.load(str(Path(d, "log.capnp")), imports=[d])
  sys.exit("log.capnp not found: pass --cereal /path/to/BogPilot/cereal")


def ffmpeg_cmd(hw: bool) -> list[str]:
  c = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", "-flags", "low_delay",
       "-probesize", "32", "-analyzeduration", "0"]
  if hw:
    c += ["-hwaccel", "videotoolbox"]
  return c + ["-f", "hevc", "-i", "pipe:0", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]


class Client:
  def __init__(self, endpoint: str, camera: str = "wide", cereal_dir: str | None = None,
               token: str | None = None, hw: bool | None = None, state=("carState",), ctx=None):
    self.log = load_log(cereal_dir)
    self.topic = TOPIC[camera]
    self.camera = camera
    self.ctx = ctx or zmq.Context.instance()
    self.sock = self.ctx.socket(zmq.SUB)
    self.sock.setsockopt(zmq.RCVHWM, 20)
    if token:
      self.sock.setsockopt(zmq.PLAIN_USERNAME, b"bogpilot")
      self.sock.setsockopt(zmq.PLAIN_PASSWORD, token.encode())
    for t in (self.topic, *state):
      self.sock.setsockopt(zmq.SUBSCRIBE, t.encode())
    self.sock.connect(endpoint)
    self.hw = (platform.system() == "Darwin") if hw is None else hw
    self.cs = collections.deque(maxlen=400)  # (logMonoTime, vEgo, standstill, steerDeg)
    self.calib = None
    self.meta = collections.deque()  # per-packet metadata, FIFO-matched to decoded frames
    self.wh = None
    self.stats = {"packets": 0, "gaps": 0, "frames": 0}
    self._stop = False

  # ---- network -> ffmpeg ----
  def _pump(self, ff):
    synced, last_id = False, None
    while not self._stop:
      if not self.sock.poll(200):
        continue
      topic, dat = self.sock.recv_multipart()
      topic = topic.decode()
      with self.log.Event.from_bytes(dat, traversal_limit_in_words=2**40) as ev:
        if topic == "carState":
          c = ev.carState
          self.cs.append((ev.logMonoTime, c.vEgo, c.standstill, c.steeringAngleDeg))
          continue
        if topic == "liveCalibration":
          self.calib = list(ev.liveCalibration.rpyCalib)
          continue
        if topic != self.topic:
          continue
        e = getattr(ev, self.topic)
        idx = e.idx
        if last_id is not None and idx.encodeId != last_id + 1:
          self.stats["gaps"] += 1
          synced = False  # dropped packets: resync on next keyframe
        last_id = idx.encodeId
        key = bool(idx.flags & KEYFRAME) and len(e.header) > 0
        if not synced and not key:
          continue
        synced = True
        self.wh = (e.width, e.height)
        self.meta.append((idx.frameId, idx.timestampEof, ev.logMonoTime))
        try:
          if key:
            ff.stdin.write(bytes(e.header))
          ff.stdin.write(bytes(e.data))
          ff.stdin.flush()
        except BrokenPipeError:
          return
        self.stats["packets"] += 1
    ff.stdin.close()

  def nearest_cs(self, t):
    if not self.cs:
      return None
    ts = [c[0] for c in self.cs]
    i = bisect.bisect_left(ts, t)
    j = min((k for k in (i - 1, i) if 0 <= k < len(ts)), key=lambda k: abs(ts[k] - t))
    return self.cs[j]

  def frames(self, max_frames: int | None = None):
    """Yield (bgr ndarray or raw bytes, meta dict)."""
    try:
      import numpy as np
    except ImportError:
      np = None
    ff = subprocess.Popen(ffmpeg_cmd(self.hw), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    th = threading.Thread(target=self._pump, args=(ff,), daemon=True)
    th.start()
    n, t0 = 0, None
    try:
      while max_frames is None or n < max_frames:
        while self.wh is None:
          if not th.is_alive():
            return
          threading.Event().wait(0.01)
        w, h = self.wh
        buf = ff.stdout.read(w * h * 3)
        if len(buf) < w * h * 3:
          return
        fid, teof, tlog = self.meta.popleft() if self.meta else (None, 0, 0)
        tm = teof or tlog
        t0 = t0 or tm
        c = self.nearest_cs(tm)
        meta = {"frame": n, "frame_id": fid, "t": round((tm - t0) / 1e9, 4), "t_mono_ns": tm,
                "camera": self.camera, "img_w": w, "img_h": h,
                "v_ego": round(c[1], 3) if c else None, "standstill": c[2] if c else None,
                "steering_angle_deg": round(c[3], 2) if c else None, "rpy_calib": self.calib}
        img = np.frombuffer(buf, np.uint8).reshape(h, w, 3) if np is not None else buf
        self.stats["frames"] += 1
        n += 1
        yield img, meta
    finally:
      self._stop = True
      ff.kill()
      ff.wait()
      th.join(1.0)
      for f in (ff.stdin, ff.stdout):
        try:
          f.close()
        except OSError:
          pass

  def close(self):
    self._stop = True
    self.sock.close(linger=0)


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--host", required=True)
  ap.add_argument("--port", type=int, default=8765)
  ap.add_argument("--camera", choices=["wide", "road"], default="wide")
  ap.add_argument("--token", default=os.environ.get("HEVC_STREAM_TOKEN"))
  ap.add_argument("--cereal", default=None, help="path to BogPilot cereal/ (log.capnp)")
  ap.add_argument("--jsonl", default=None, help="write feeder JSONL here ('-' = stdout)")
  ap.add_argument("--yolo", default=None, help="ultralytics weights, e.g. yolo11m.pt (imgsz 1280, full-res boxes)")
  ap.add_argument("--no-hw", action="store_true")
  ap.add_argument("--max-frames", type=int, default=None)
  a = ap.parse_args()

  cl = Client(f"tcp://{a.host}:{a.port}", a.camera, a.cereal, a.token, hw=False if a.no_hw else None)
  model = None
  if a.yolo:
    from ultralytics import YOLO
    model = YOLO(a.yolo)
    cls = [i for i in range(80) if i != 32]
  out = sys.stdout if a.jsonl == "-" else (open(a.jsonl, "w") if a.jsonl else None)
  for img, meta in cl.frames(a.max_frames):
    objs = []
    if model is not None:
      r = model.track(img, persist=True, verbose=False, tracker="bytetrack.yaml", classes=cls, imgsz=1280)[0]
      for b in r.boxes:
        x1, y1, x2, y2 = [round(float(q), 1) for q in b.xyxy[0]]
        objs.append({"id": int(b.id) if b.id is not None else None, "class": r.names[int(b.cls)],
                     "conf": round(float(b.conf), 3), "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    meta["objects"] = objs
    if out:
      out.write(json.dumps(meta) + "\n")
      out.flush()
  print(json.dumps(cl.stats), file=sys.stderr)


if __name__ == "__main__":
  main()
