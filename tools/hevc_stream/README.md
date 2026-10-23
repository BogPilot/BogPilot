# BogPilot HEVC stream → Mac (experimental)

The comma's `hevc_streamd` forwards the HEVC that encoderd already produces
(no re-encode) plus (opt-in) carState / liveCalibration / radarState to a Mac on the
same Wi-Fi (in-car travel router). Transport: ZMQ PUB, multipart
`[topic, raw cereal Event bytes]`, port 8765, bound only to wlan0's private IP.

## Comma settings (`/data/params_bogpilot/`, then restart openpilot)
| file | default | meaning |
|---|---|---|
| HevcStream | 1 | 0 disables |
| HevcStreamServices | wideRoadEncodeData,roadEncodeData | state topics (carState, liveCalibration, radarState, modelV2) are opt-in; adding them adds msgq readers that can evict driving readers (commIssue seen on a recorded drive) |
| HevcStreamIface | wlan0 | interface whose private IPv4 is bound (never 0.0.0.0/public) |
| HevcStreamPort | 8765 | |
| HevcStreamAllow | (none) | comma list of client IPs allowed (e.g. the Mac's reserved IP) |
| HevcStreamToken | (none) | shared token; client passes `--token` |
| HevcStreamDriver | (absent) | must be 1 before `driverEncodeData` is ever streamed |

Runs only onroad, cores 0-2, nice 10. Slow clients lose packets (SNDHWM 10); the
client resyncs at the next keyframe (encoderd: ~every 1 s on the comma).

## Mac setup
```
brew install ffmpeg
python3 -m venv ~/hevcvenv && source ~/hevcvenv/bin/activate
pip install pyzmq pycapnp numpy        # + ultralytics for --yolo
git clone -b bogpilot-tesla-hevc-stream <BogPilot repo>   # needs cereal/*.capnp
python tools/hevc_stream/mac_client.py --host <comma IP> --camera wide --jsonl - --max-frames 20
```
Decode uses ffmpeg `-hwaccel videotoolbox` (VideoToolbox HEVC *decoder*;
`hevc_videotoolbox` is the encoder name, not needed). `--no-hw` forces software.

## Feeding Buddy's feeder
`--jsonl out.jsonl` writes one line per decoded frame in the same schema as
`/workspace/out/yolo/export_seg56_wide.py`:
`frame, frame_id, t, t_mono_ns, camera:"wide", img_w:1928, img_h:1208, v_ego, standstill, steering_angle_deg, objects[]`
(+ `rpy_calib` from liveCalibration). `--yolo yolo11m.pt` fills `objects` with
yolo11m+ByteTrack boxes on the full 1928×1208 frame (imgsz 1280), so pixels match
the wide calibration. In Python, `Client(...).frames()` yields `(bgr_ndarray, meta)`.
Timestamps: `t_mono_ns` = encoder timestampEof (comma monotonic); v_ego = nearest carState by logMonoTime.
Note: frames are FIFO-matched to packets (openpilot encodes have no B-frames).
