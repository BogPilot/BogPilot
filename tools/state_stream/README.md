# BogPilot StateStream (comma -> Mac, for the Unreal 3D view)

A ~20 Hz JSON snapshot of the car, GPS, radar leads and model lanes, published
over ZMQ PUB on the comma's **wlan0 private IP only**. Default **OFF**.

## Why it lives inside `frogpilot_process`
msgq allows 15 readers per topic (`NUM_READERS`). A new reader past that evicts
**all** readers of the topic, so planner/controls miss messages and you get
commIssue / "TAKE CONTROL IMMEDIATELY" (route 5b, hevc_streamd state topics).
StateStream adds **no msgq reader**: it reads the `SubMaster` that
`frogpilot_process` already has (`carState, radarState, modelV2,
liveLocationKalman, ...`), once per its 20 Hz modelV2 loop, after
`frogpilotPlan` is published.

Static reader count on this branch (onroad, AP1, comma 3X / qcom GNSS, one modeld):

| topic | readers | who |
|---|---|---|
| carState | **15 / 15 (full)** | navd, calibrationd, paramsd, lagd, torqued, modeld, plannerd, controlsd, radard, dmonitoringd, frogpilot_process, ui, ui(frogpilot sm), locationd, loggerd |
| liveCalibration | 9 | lagd, dmonitoringmodeld, modeld, card, controlsd, dmonitoringd, ui, locationd, loggerd |
| modelV2 | 8 | plannerd, controlsd, radard, dmonitoringd, card (Tesla carcontroller), frogpilot_process, ui, loggerd |
| liveLocationKalman | 9-10 | navd, paramsd, torqued, controlsd, timed, frogpilot_process, speed_limit_filler (if on), ui, loggerd (+map_renderer when mapsd runs) |
| radarState | 5 | plannerd, controlsd, frogpilot_process, ui, loggerd |
| liveTracks | 2-3 | ui(frogpilot sm), loggerd (+classic_modeld) |
| gpsLocation | 3 | locationd, ui(frogpilot sm), loggerd |

StateStream: **+0 on every topic.** (hevc_streamd state topics stay opt-in; leave them off.)
Note msgq slots are not reclaimed when a reader process restarts, so even
"spare" topics lose slots over a long session; that is why nothing new subscribes.

## Enable (on the comma)
```
mkdir -p /data/params_bogpilot
echo 1 > /data/params_bogpilot/StateStream
# optional
echo 192.168.x.y > /data/params_bogpilot/StateStreamAllow   # your Mac's IP
echo <secret>    > /data/params_bogpilot/StateStreamToken   # or reuse HevcStreamToken
```
Picked up within 2 s (no restart). `echo 0 >` (or delete) turns it off.

| file | default | |
|---|---|---|
| StateStream | off | `1/true/on/yes` enables |
| StateStreamIface | wlan0 | binds that interface's private IPv4 only (never 0.0.0.0/public) |
| StateStreamPort | 8766 | hevc_streamd is 8765 |
| StateStreamAllow / StateStreamToken | HevcStream* values | ZAP IP allowlist / PLAIN user `bogpilot` |
| StateStreamHz | 20 | 1..20 |
| StateStreamModelHz | 10 | modelV2 section rate |

## Mac client
```
pip3 install pyzmq
python3 tools/state_stream/state_client.py --host <comma IP> [--token T] --udp 127.0.0.1:7777
```
`--udp` re-sends each JSON as one UDP datagram for Unreal (UDP socket / plugin).

## Wire format
ZMQ multipart `[b"state", <utf-8 JSON>]`, one object per frame, ~1-3 kB.

```jsonc
{
 "v": 1, "frame": 1234,
 "mono": {"carState": ns, "radarState": ns, "modelV2": ns, "liveLocationKalman": ns},  // logMonoTime of each source
 "gps": {"valid": bool, "gpsOK": bool, "status": "valid",
         "lat": deg, "lon": deg, "alt": m, "headingDeg": 0..360 (calibrated NED yaw),
         "speed": m/s (NED ground speed), "accuracyM": m (ECEF pos std norm), "unixMs": int},
 "car": {"vEgo": m/s, "aEgo": m/s2, "standstill": bool, "steeringAngleDeg": deg,
         "yawRate": rad/s (carState; 0 on Tesla), "yawRateLkf": rad/s (locationd, use this),
         "leftBlinker", "rightBlinker", "hazard" (both blinkers), "leftBlindspot", "rightBlindspot",
         "brakePressed", "brakeLight", "gasPressed", "gear": "drive|reverse|park|neutral", "reverse", "doorOpen"},
 "leads": {"leadOne": [status, dRel m, yRel m, vRel m/s, aRel, vLead m/s, modelProb, radar],
           "leadTwo": [...]},
 "model": {                          // only every 1/StateStreamModelHz s (default 10 Hz)
   "laneLines": [4 x [[x,y,z] x 11]],   // calibrated road frame, m; every 3rd of 33 points
   "laneLineProbs": [4], "roadEdges": [2 x [[x,y,z] x 11]], "roadEdgeStds": [2],
   "leadsV3": [[prob, x, y, v] x <=2]},
 "tracks": [[trackId, dRel, yRel, vRel], ...],   // only if host subscribes liveTracks (not by default)
 "calib": {"rpyCalib": [r,p,y]}                   // only if host subscribes liveCalibration (not by default)
}
```
Missing sections = source not yet seen. `null` = non-finite value.

### Lights / body signals
| field | source | status |
|---|---|---|
| leftBlinker / rightBlinker / hazard | GTW_carState BC_indicator{L,R}Status -> carState | parsed today; hazard = both on |
| reverse / gear | DI_torque2 DI_gear -> carState.gearShifter | parsed today |
| doorOpen | GTW_carState doors + frunk + boot (any) | parsed today, aggregate only |
| brakeLight | `brakePressed or (aEgo <= -1.0 and not gas)` | **heuristic**; carState.brakeLightsDEPRECATED is not set on Tesla |
| headlights (low) | GTW_carState BC_headLight{L,R}Status (0x318) | in DBC, not exposed in carState (needs carstate change) |
| high beam (auto decision) | DAS_bodyControls DAS_highLowBeamDecision (0x3E9, bus 2) | in DBC, not parsed |
| regen light | DI_state DI_regenLight | in DBC, not exposed |
| ESP brake lamp | ESP_brakeLamp | in DBC, not parsed |

## Security / privacy
Binds only a private/loopback IPv4 (`safe_bind_ip`), optional allowlist + token.
Position is only ever in the live stream; StateStream never logs payloads.
Never touches panda safety, controlsd, card or planner outputs.
