#!/usr/bin/env python3
"""BogPilot video receiver (MM2): hevc_streamd -> decode -> yolo11s -> UE feeder UDP.

Subscribes to ONE camera from the comma's hevc_streamd (PLAIN token, comma side also
IP-allowlists), decodes it (VideoToolbox when available, else CPU), runs yolo11s at
416x640 (Core ML CPU_AND_NE on the Mac; ONNX on Linux for benches; or none), and sends
one JSON datagram per processed frame to UDP 127.0.0.1:7778 in the feeder v2 format:

  {"render_t","frameId","capT","sentT","objects":[{"id","cls","conf","d_m","lat_left_m","v_mps","dir"}],
   "cam","t_eof_ns","t_log_ns"}

render_t = frameId / 20, frameId = the camera frameId carried in every packet,
capT = wall time the packet arrived here, sentT = wall time after YOLO + post.
"cam", "t_eof_ns", "t_log_ns" are extra keys (consumers must ignore unknown keys).
Tracker, range and direction logic are feeder v2's, unchanged.

Latest-frame-only: decode keeps up with every packet (needed for HEVC references),
but YOLO only ever sees the newest decoded frame; older ones are dropped, never queued.
Nothing is written to disk except optional per-frame timing stats (no video).

  BP_STREAM_TOKEN=... python bp_video_rx.py --host <comma IP> --model yolo11s.mlpackage
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

KEYFRAME = 8  # V4L2_BUF_FLAG_KEYFRAME
TOPIC = {"road": "roadEncodeData", "wide": "wideRoadEncodeData", "qroad": "qRoadEncodeData"}
CODEC = {"road": "hevc", "wide": "hevc", "qroad": "h264"}

# feeder v2 camera model: comma fcam 1928x1208, f=2648 px, letterboxed to 640x416 (scale 640/1928, 7 px top pad)
S = 640 / 1928; F = 2648 * S; CX = 320.0; CY = 7 + 604 * S; CAM_H = 1.22; CAM_TO_BUMPER = 1.0
IN_W, IN_H, LB_H, PAD_TOP = 640, 416, 401, 7
CLS = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}; HGT = {"car": 1.5, "motorcycle": 1.4, "bus": 3.2, "truck": 3.0}


# ---------------- pre/post (feeder v2) ----------------
def letterbox(rgb_640x401: np.ndarray) -> np.ndarray:
  """640x401 RGB -> 416x640 RGB with a 7 px top pad (pad gray 114, as ultralytics)."""
  out = np.full((IN_H, IN_W, 3), 114, np.uint8)
  h = min(rgb_640x401.shape[0], IN_H - PAD_TOP)
  out[PAD_TOP:PAD_TOP + h] = rgb_640x401[:h]
  return out


def nms(b, s, iou=0.6):
  o = s.argsort()[::-1]; keep = []
  while o.size:
    i = o[0]; keep.append(i)
    xx1 = np.maximum(b[i, 0], b[o[1:], 0]); yy1 = np.maximum(b[i, 1], b[o[1:], 1]); xx2 = np.minimum(b[i, 2], b[o[1:], 2]); yy2 = np.minimum(b[i, 3], b[o[1:], 3])
    inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None); ar = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    o = o[1:][inter / (ar[i] + ar[o[1:]] - inter + 1e-9) < iou]
  return keep


def postprocess(y: np.ndarray, conf: float = 0.35):
  """y: raw yolo output (84, N) -> [(cls, conf, xyxy)] after class filter + NMS."""
  cls_sc = y[4:]; c = cls_sc.argmax(0); sc = cls_sc.max(0)
  k = (sc > conf) & np.isin(c, list(CLS)); bx = y[:4, k].T; s = sc[k]; c = c[k]
  if not len(s):
    return []
  xyxy = np.c_[bx[:, 0] - bx[:, 2] / 2, bx[:, 1] - bx[:, 3] / 2, bx[:, 0] + bx[:, 2] / 2, bx[:, 1] + bx[:, 3] / 2]
  return [(CLS[int(c[i])], float(s[i]), xyxy[i]) for i in nms(xyxy, s)]


def rng(cls, b):
  h = max(b[3] - b[1], 1.0); d_h = F * HGT[cls] / h
  dy = b[3] - CY; d_g = F * CAM_H / dy if dy > 6 else None
  d = d_h if d_g is None else 0.5 * (d_h + min(d_g, 2 * d_h))
  return max(d - CAM_TO_BUMPER, 0.5)


def iou(p, q):
  ix = max(0, min(p[2], q[2]) - max(p[0], q[0])); iy = max(0, min(p[3], q[3]) - max(p[1], q[1])); i = ix * iy
  return i / ((p[2] - p[0]) * (p[3] - p[1]) + (q[2] - q[0]) * (q[3] - q[1]) - i + 1e-9)


class FeederV2:
  """IoU tracker + monocular range + v2 direction (median-7, polyfit 1.2 s, hysteresis, 10-frame persistence)."""

  def __init__(self):
    self.tracks: dict = {}
    self.nid = 0

  def step(self, dets, render_t: float, vego: float):
    tracks = self.tracks; used = set(); out = []
    for cls, conf, b in sorted(dets, key=lambda d: -d[1]):
      best, bi = 0.3, None
      for tid, tr in tracks.items():
        if tid in used:
          continue
        v = iou(b, tr["b"])
        if v > best:
          best, bi = v, tid
      d = rng(cls, b); lat = -((b[0] + b[2]) / 2 - CX) * (d + CAM_TO_BUMPER) / F
      if bi is None:
        self.nid += 1; bi = f"L{self.nid}"
        tracks[bi] = dict(b=b, d=d, t=render_t, rv=0.0, hits=0, cls=cls, h=deque(maxlen=40), dir="same", cand=None, cn=0)
      tr = tracks[bi]; used.add(bi)
      tr["h"].append((render_t, d, lat)); tr.update(b=b, t=render_t, hits=tr["hits"] + 1)
      H = list(tr["h"]); d_out = float(np.median([x[1] for x in H[-7:]])); lat_out = float(np.median([x[2] for x in H[-7:]]))
      Ds = np.array([np.median([x[1] for x in H[max(0, i - 6):i + 1]]) for i in range(len(H))]); Ts = np.array([x[0] for x in H]); msk = Ts >= render_t - 1.2
      tr["rv"] = float(np.polyfit(Ts[msk], Ds[msk], 1)[0]) if msk.sum() >= 6 and np.ptp(Ts[msk]) > 0.5 else 0.0
      vobj = vego + tr["rv"]
      c = "oncoming" if vobj < -2.5 else ("same" if vobj > -1.0 else tr["dir"])  # hysteresis band
      if c != tr["dir"]:
        tr["cn"] = tr["cn"] + 1 if tr["cand"] == c else 1; tr["cand"] = c
        if tr["cn"] >= 10:
          tr["dir"], tr["cn"] = c, 0  # 0.5 s persistence before a dir change
      else:
        tr["cn"] = 0
      if tr["hits"] >= 2:
        out.append(dict(id=bi, cls=tr["cls"], conf=round(conf, 3), d_m=round(float(d_out), 2), lat_left_m=round(float(lat_out), 2),
                        v_mps=round(float(vobj), 2), dir=tr["dir"]))
    for tid in [t for t, tr in tracks.items() if render_t - tr["t"] > 1.0]:
      del tracks[tid]
    return out


def row(fid: int, capT: float, sentT: float, objects, cam: str, t_eof_ns: int = 0, t_log_ns: int = 0) -> bytes:
  return json.dumps(dict(render_t=fid / 20.0, frameId=fid, capT=capT, sentT=sentT, objects=objects,
                         cam=cam, t_eof_ns=t_eof_ns, t_log_ns=t_log_ns), separators=(",", ":")).encode()


# ---------------- detector backends ----------------
class Detector:
  def __init__(self, backend: str, model: str | None, conf: float):
    self.backend, self.conf = backend, conf
    if backend == "coreml":
      import coremltools as ct
      self.m = ct.models.MLModel(model, compute_units=ct.ComputeUnit.CPU_AND_NE)
      self.inp = self.m.get_spec().description.input[0].name
    elif backend == "onnx":
      import onnxruntime as ort
      self.m = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
      self.inp = self.m.get_inputs()[0].name

  def raw(self, img: np.ndarray):
    if self.backend == "coreml":
      from PIL import Image
      return np.asarray(next(iter(self.m.predict({self.inp: Image.fromarray(img)}).values())))[0]
    if self.backend == "onnx":
      x = np.ascontiguousarray(img.transpose(2, 0, 1)[None], np.float32) / 255.0
      return self.m.run(None, {self.inp: x})[0][0]
    return None

  def __call__(self, img: np.ndarray):
    y = self.raw(img)
    return [] if y is None else postprocess(y, self.conf)


# ---------------- network + decode ----------------
def load_log(cereal_dir: str | None):
  import capnp
  capnp.remove_import_hook()
  for d in (cereal_dir, os.environ.get("CEREAL_DIR"), str(Path(__file__).resolve().parents[2] / "cereal")):
    if d and Path(d, "log.capnp").exists():
      return capnp.load(str(Path(d, "log.capnp")), imports=[d])
  sys.exit("log.capnp not found: pass --cereal /path/to/BogPilot/cereal")


def make_decoder(codec: str, hw: str):
  """PyAV decoder; VideoToolbox hwaccel when asked/available, else CPU. Returns (ctx, 'vt'|'cpu')."""
  import av
  if hw != "off":
    try:
      from av.codec.hwaccel import HWAccel, hwdevices_available
      if "videotoolbox" in hwdevices_available():
        c = av.CodecContext.create(codec, "r", hwaccel=HWAccel(device_type="videotoolbox", allow_software_fallback=False))
        return c, "vt"
    except Exception as e:  # noqa: BLE001
      if hw == "on":
        raise
      print(f"videotoolbox unavailable ({e}); CPU decode", file=sys.stderr)
    if hw == "on":
      raise RuntimeError("videotoolbox not available")
  c = av.CodecContext.create(codec, "r")
  c.thread_type = "SLICE"  # frame threading would add frames of latency
  return c, "cpu"


class Receiver:
  def __init__(self, endpoint: str, camera: str, token: str, cereal_dir=None, hw="auto", ctx=None):
    import zmq
    self.log = load_log(cereal_dir)
    self.camera, self.topic = camera, TOPIC[camera]
    self.ctx = ctx or zmq.Context.instance()
    self.sock = self.ctx.socket(zmq.SUB)
    self.sock.setsockopt(zmq.RCVHWM, 20)
    self.sock.setsockopt(zmq.PLAIN_USERNAME, b"bogpilot")
    self.sock.setsockopt(zmq.PLAIN_PASSWORD, token.encode())
    self.sock.setsockopt(zmq.SUBSCRIBE, self.topic.encode())
    self.sock.connect(endpoint)
    self.dec, self.dec_kind = make_decoder(CODEC[camera], hw)
    self.slot = None  # latest decoded frame only
    self.cv = threading.Condition()
    self.stop = threading.Event()
    self.stats = dict(packets=0, gaps=0, decoded=0, dropped_before_yolo=0, decode_errors=0)

  def run(self):
    import zmq
    synced, last = False, None
    pending = {}  # pts(frameId) -> meta, for matching decoded frames
    while not self.stop.is_set():
      if not self.sock.poll(100):
        continue
      _, dat = self.sock.recv_multipart()
      capT = time.time(); rx_ns = time.monotonic_ns()
      with self.log.Event.from_bytes(dat, traversal_limit_in_words=2**40) as ev:
        e = getattr(ev, self.topic)
        idx = e.idx
        fid, eid = int(idx.frameId), int(idx.encodeId)
        if last is not None and eid != last + 1:
          self.stats["gaps"] += 1
          synced = False  # dropped packets upstream: resync at next keyframe
        last = eid
        key = bool(idx.flags & KEYFRAME) and len(e.header) > 0
        if not synced and not key:
          continue
        synced = True
        buf = (bytes(e.header) if key else b"") + bytes(e.data)
        meta = dict(fid=fid, capT=capT, rx_ns=rx_ns, t_eof_ns=int(idx.timestampEof), t_log_ns=int(ev.logMonoTime))
      self.stats["packets"] += 1
      import av
      pkt = av.Packet(buf); pkt.pts = fid
      pending[fid] = meta
      t0 = time.perf_counter()
      try:
        frames = self.dec.decode(pkt)
      except av.error.FFmpegError:
        self.stats["decode_errors"] += 1; synced = False
        continue
      for fr in frames:
        if fr.format.name not in ("yuv420p", "nv12", "yuvj420p"):
          fr = fr.reformat(format="nv12")  # hw frames
        small = fr.to_ndarray(width=IN_W, height=LB_H, format="rgb24")
        m = pending.pop(fr.pts, None) or meta
        m["dec_ms"] = (time.perf_counter() - t0) * 1e3
        self.stats["decoded"] += 1
        with self.cv:
          if self.slot is not None:
            self.stats["dropped_before_yolo"] += 1
          self.slot = (letterbox(small), m)
          self.cv.notify()
        t0 = time.perf_counter()
      for k in [k for k in pending if k < fid - 40]:
        del pending[k]
    self.sock.close(linger=0)

  def latest(self, timeout=0.5):
    with self.cv:
      if self.slot is None:
        self.cv.wait(timeout)
      s, self.slot = self.slot, None
      return s


class StateVEgo:
  """Optional: newest vEgo from the comma's StateStream ("state" topic JSON)."""

  def __init__(self, endpoint: str, token: str, ctx):
    import zmq
    self.sock = ctx.socket(zmq.SUB)
    self.sock.setsockopt(zmq.PLAIN_USERNAME, b"bogpilot"); self.sock.setsockopt(zmq.PLAIN_PASSWORD, token.encode())
    self.sock.setsockopt(zmq.SUBSCRIBE, b"state"); self.sock.setsockopt(zmq.RCVHWM, 100); self.sock.connect(endpoint)
    self.vego = 0.0

  def poll(self):
    import zmq
    while True:
      try:
        m = self.sock.recv_multipart(zmq.NOBLOCK)
      except zmq.Again:
        return self.vego
      try:
        self.vego = float((json.loads(m[1]).get("car") or {}).get("vEgo") or 0.0)
      except (ValueError, IndexError):
        pass


def q(x):
  return dict(n=len(x), median=round(float(np.median(x)), 2), p95=round(float(np.percentile(x, 95)), 2),
              max=round(float(np.max(x)), 2)) if x else None


def main(argv=None):
  ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  ap.add_argument("--host", required=True, help="comma IP (or 127.0.0.1 for a local bench)")
  ap.add_argument("--port", type=int, default=8765)
  ap.add_argument("--camera", choices=tuple(TOPIC), default="road")
  ap.add_argument("--token-file", help="file holding the stream token (default: env BP_STREAM_TOKEN)")
  ap.add_argument("--cereal", help="BogPilot cereal dir (log.capnp)")
  ap.add_argument("--backend", choices=("coreml", "onnx", "none"), default="coreml" if platform.system() == "Darwin" else "onnx")
  ap.add_argument("--model", help="yolo11s .mlpackage (coreml) or .onnx (onnx), 416x640 input")
  ap.add_argument("--conf", type=float, default=0.35)
  ap.add_argument("--hw", choices=("auto", "on", "off"), default="auto", help="VideoToolbox decode")
  ap.add_argument("--udp", default="127.0.0.1:7778")
  ap.add_argument("--state", help="optional StateStream endpoint for vEgo, e.g. tcp://<comma IP>:8766")
  ap.add_argument("--secs", type=float, default=0, help="stop after N s (0 = run until Ctrl-C)")
  ap.add_argument("--idle-exit", type=float, default=0, help="stop after N s without frames once started (bench)")
  ap.add_argument("--stats-csv", help="per-frame timings CSV (no image data)")
  ap.add_argument("--stats-json", help="summary JSON")
  ap.add_argument("--same-clock", action="store_true", help="sender shares this host's monotonic clock (local bench): report end-to-end from logMonoTime")
  a = ap.parse_args(argv)
  token = (Path(a.token_file).read_text().strip() if a.token_file else os.environ.get("BP_STREAM_TOKEN", "").strip())
  if not token:
    sys.exit("no token: set BP_STREAM_TOKEN or --token-file")
  if a.backend != "none" and not a.model:
    sys.exit("--model required for backend " + a.backend)

  import zmq
  ctx = zmq.Context.instance()
  det = Detector(a.backend, a.model, a.conf)
  if a.backend != "none":
    warm = np.full((IN_H, IN_W, 3), 114, np.uint8)
    for _ in range(5):
      det(warm)
  rx = Receiver(f"tcp://{a.host}:{a.port}", a.camera, token, a.cereal, a.hw, ctx)
  st = StateVEgo(a.state, token, ctx) if a.state else None
  host, port = a.udp.split(":"); tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); dst = (host, int(port))
  feeder = FeederV2()
  th = threading.Thread(target=rx.run, daemon=True); th.start()
  print(f"receiving {a.camera} decode={rx.dec_kind} backend={a.backend}", file=sys.stderr)
  lf = open(a.stats_csv, "w") if a.stats_csv else None
  if lf:
    lf.write("frameId,dec_ms,wait_ms,yolo_ms,post_ms,rx_to_sent_ms,e2e_ms,n_det,n_sent\n")
  T = {k: [] for k in ("dec", "wait", "yolo", "post", "rx_to_sent", "e2e")}
  n = 0; t_start = time.monotonic(); t_last = None; last_fid = -1
  try:
    while True:
      now = time.monotonic()
      if a.secs and now - t_start > a.secs:
        break
      if a.idle_exit and t_last and now - t_last > a.idle_exit:
        break
      s = rx.latest()
      if s is None:
        continue
      img, m = s
      if m["fid"] <= last_fid:
        continue
      t_last = time.monotonic(); t1 = time.perf_counter(); wait_ms = (time.monotonic_ns() - m["rx_ns"]) / 1e6 - m["dec_ms"]
      dets = det(img); t2 = time.perf_counter()
      vego = st.poll() if st else 0.0
      out = feeder.step(dets, m["fid"] / 20.0, vego)
      sentT = time.time()
      tx.sendto(row(m["fid"], m["capT"], sentT, out, a.camera, m["t_eof_ns"], m["t_log_ns"]), dst)
      t3 = time.perf_counter(); done_ns = time.monotonic_ns()
      rts = (done_ns - m["rx_ns"]) / 1e6
      e2e = (done_ns - m["t_log_ns"]) / 1e6 if a.same_clock else None
      last_fid = m["fid"]; n += 1
      for k, v in (("dec", m["dec_ms"]), ("wait", wait_ms), ("yolo", (t2 - t1) * 1e3), ("post", (t3 - t2) * 1e3), ("rx_to_sent", rts), ("e2e", e2e)):
        if v is not None:
          T[k].append(v)
      if lf:
        lf.write(f"{m['fid']},{m['dec_ms']:.2f},{wait_ms:.2f},{(t2-t1)*1e3:.2f},{(t3-t2)*1e3:.2f},{rts:.2f},{'' if e2e is None else f'{e2e:.2f}'},{len(dets)},{len(out)}\n")
  except KeyboardInterrupt:
    pass
  finally:
    rx.stop.set(); th.join(2)
    if lf:
      lf.close()
  r = dict(camera=a.camera, decode=rx.dec_kind, backend=a.backend, rows_sent=n, **rx.stats,
           decode_ms=q(T["dec"]), slot_wait_ms=q(T["wait"]), yolo_ms=q(T["yolo"]), post_send_ms=q(T["post"]),
           rx_to_sent_ms=q(T["rx_to_sent"]), e2e_logmono_to_sent_ms=q(T["e2e"]), ids=feeder.nid)
  if a.stats_json:
    Path(a.stats_json).write_text(json.dumps(r, indent=1))
  print(json.dumps(r))
  return r


if __name__ == "__main__":
  main()
