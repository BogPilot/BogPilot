# BogPilot video stream → MM2 (experimental, default OFF)

The comma's `hevc_streamd` forwards the road-camera HEVC that encoderd already
produces (no new encoder, no re-encode) to MM2 on the in-car travel router.
Transport: ZMQ PUB, multipart `[topic, raw cereal Event bytes]`, port 8765, bound
only to wlan0's private IP. Every packet is a cereal EncodeData Event, so it
carries the camera `idx.frameId` (plus encodeId / timestampEof).

## Comma settings (`/data/params_bogpilot/`, read when going onroad)
A missing file means OFF. Only `1` / `true` / `on` / `yes` turn a toggle on.

| file | default | meaning |
|---|---|---|
| VideoStream | off | master toggle: streams fcamera (`roadEncodeData`) |
| VideoStreamWide | off | also stream ecamera (`wideRoadEncodeData`) |
| VideoStreamQcam | off | manual fallback: stream qcamera (`qRoadEncodeData`, small H.264) instead of fcamera |
| HevcStream | off | legacy name for VideoStream (now also default off) |
| StateStreamToken (or HevcStreamToken) | none | shared token (ZMQ PLAIN). **Required** |
| StateStreamAllow (or HevcStreamAllow) | none | comma list of allowed client IPs (MM2's reserved IP). **Required** |
| HevcStreamIface | wlan0 | interface whose private IPv4 is bound (never 0.0.0.0/public) |
| HevcStreamPort | 8765 | |

Without both a token and an allowlist the streamer refuses to start. The driver
camera and the state topics are never streamed by this process (state goes over
StateStream).

Low impact by design: onroad only, SCHED_IDLE + nice 19, cores 0-2. The publisher
never blocks: SNDHWM 4 per subscriber, and after a stall only the newest 2 packets
per camera are sent; older ones are dropped, never queued. The client resyncs at
the next keyframe.

Heat guard (reads deviceState): pauses forwarding at maxTempC ≥ 75 °C, a 10 s CPU
average ≥ 85 %, or thermalStatus above green; resumes after 60 s below 70 °C / 75 %.
On the 3rd pause the stream turns off for the rest of the drive (process exits; the
manager starts it again on the next onroad).

## MM2 receiver: `bp_video_rx.py`
```
BP_STREAM_TOKEN=<token> python tools/hevc_stream/bp_video_rx.py --host <comma IP> \
    --model yolo11s.mlpackage [--camera road|wide|qroad] [--state tcp://<comma IP>:<state port>]
```
Needs `pyzmq pycapnp av numpy pillow coremltools` and this repo's `cereal/`.
Decode: PyAV with VideoToolbox (`--hw auto|on|off`). Detector: yolo11s at 416x640
(1928x1208 → 640x401, 7 px top pad), Core ML `CPU_AND_NE` (`--backend onnx` with an
.onnx export for Linux benches, `none` for pipeline-only timing). YOLO only sees the
newest decoded frame. Tracking, range and direction are the UE feeder v2 logic, and
each processed frame is one JSON datagram to UDP 127.0.0.1:7778:
`{render_t, frameId, capT, sentT, objects:[{id, cls, conf, d_m, lat_left_m, v_mps, dir}], cam, t_eof_ns, t_log_ns}`
(`render_t = frameId/20`; `cam`/`t_eof_ns`/`t_log_ns` are extra keys to ignore).
vEgo comes from `--state` (StateStream); without it vEgo = 0 and `dir` is not
meaningful. `--stats-csv/--stats-json` write timings only; no video is saved.

## Local bench
`bench_replay.py` replays a recorded fcamera.hevc through the real Publisher on
127.0.0.1 at 20 fps (stamping logMonoTime at send), so `bp_video_rx.py --host 127.0.0.1
--port 18765 --same-clock` reports decode / YOLO / end-to-end ms.

## mac_client.py (older reference client)
ffmpeg-pipe client that yields full-res BGR frames + nearest carState; used by the
loopback test. `--token` is now always required by the comma side.
