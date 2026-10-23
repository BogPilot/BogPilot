# BogPilot StateStream (comma -> Mac, for the Unreal 3D view)

A ~20 Hz JSON snapshot of the car, GPS, radar leads and model lanes, published
over ZMQ PUB on the comma's **wlan0 private IP only**. Default **OFF**.

## Why it lives inside `frogpilot_process`
msgq allows 15 readers per topic (`NUM_READERS`). A new reader past that evicts
**all** readers of the topic, so planner/controls miss messages and you get
commIssue / "TAKE CONTROL IMMEDIATELY" (seen on a recorded drive, hevc_streamd state topics).
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
 "meta": {"hz": 20, "modelHz": 10, "modelSrcHz": 19.8},  // send rate, model section rate, measured modelV2 rate (null until known)
 "mono": {"carState": ns, "radarState": ns, "modelV2": ns, "liveLocationKalman": ns},  // logMonoTime of each source
 "gps": {"valid": bool, "gpsOK": bool, "status": "valid",
         "lat": deg, "lon": deg, "alt": m, "headingDeg": 0..360 (calibrated NED yaw),
         "speed": m/s (NED ground speed), "accuracyM": m (ECEF pos std norm), "unixMs": int},
 "car": {"vEgo": m/s, "aEgo": m/s2, "standstill": bool, "steeringAngleDeg": deg,
         "yawRate": rad/s (carState; 0 on Tesla), "yawRateLkf": rad/s (locationd, use this),
         "leftBlinker", "rightBlinker", "hazard" (both blinkers), "leftBlindspot", "rightBlindspot",
         "brakePressed", "brakeLight", "gasPressed", "gear": "drive|reverse|park|neutral", "reverse", "doorOpen",
         "rideLevel": 0|3|4|5|6 (air suspension; 0 = not seen), "rideMoving": bool},
 "leads": {"leadOne": [status, dRel m, yRel m, vRel m/s, aRel, vLead m/s, modelProb, radar,
                      radarTrackId (-1 = vision-only/none), handoff],
           "leadTwo": [...]},              // handoff: present in the previous snapshot with a different radarTrackId
 "model": {                          // only every 1/StateStreamModelHz s (default 10 Hz)
   "laneLines": [4 x [[x,y,z] x 11]],   // calibrated road frame, m; every 3rd of 33 points
   "laneLineProbs": [4], "roadEdges": [2 x [[x,y,z] x 11]], "roadEdgeStds": [2],
   "leadsV3": [[prob, x, y, v] x <=2]},
 "tracks": [[trackId, dRel, yRel, vRel], ...],   // only if host subscribes liveTracks (not by default)
 "calib": {"rpyCalib": [r,p,y]}                   // only if host subscribes liveCalibration (not by default)
}
```
Missing sections = source not yet seen. `null` = non-finite value.
Older builds send 8-element lead lists and no `meta`; clients must accept both.

The state topic is 20 Hz but the model section is only in every 2nd message (StateStreamModelHz 10).
Show model rate as `meta.modelHz` / a received-model-sections count and model age as
`mono.carState - mono.modelV2` of the last message that carried `model`; do not show
`"model" in msg` (it alternates true/false by design).

### Telemetry
- No subscriber connected (ZMQ socket monitor on the PUB socket): tick() skips payload.build and
  the sender skips JSON. Unknown count (old pyzmq without monitor events) = keep sending.
  A new subscriber gets the active route resent immediately.
- Every 60 s, while StateStream is enabled, one cloudlog event `state_stream_summary` (rlog
  logMessage) with counts/rates/timings only, never positions: clients, connects/disconnects,
  authOk/authFail, queued/sent/dropped/errors, skippedNoClient, routeSent, bytes,
  rates {state, model, modelSrc, route, kBps}, encodeMsMean/Max, tickMsMean, hostCpuPct,
  senderCpuPct, gaps {host, send, max, thresholdS 0.5}, leadSwitches, wifi
  (from /proc/net/wireless + /sys/class/net/<iface>/statistics: level dBm min/max/mean,
  link quality, tx/rx error and drop deltas; null if unreadable). An all-zero
  /proc/net/wireless row is "unknown", not 0 dBm; signal then comes from
  `wpa_cli -i <iface> signal_poll` (rssi_dbm, avg_rssi_dbm, linkspeed_mbps, freq_mhz;
  NOISE=9999 -> null), else `nmcli -t -f IN-USE,SIGNAL dev wifi` (signal_pct), else
  `source: "unavailable"`. Probed at most every 10 s in a side thread with a 1 s timeout.
  `operstate` from sysfs. Network names are never requested or logged.
- Send gaps are measured on capture timestamps. ZMQ PUB never blocks, so network losses show up
  as Wi-Fi errors / disconnects on the comma and as gaps on the receiver (`gap 0.85s` lines).
- `stream_view.py` (stdlib, Python 3.9+): `LeadSmoother` holds the displayed lead until a new
  radarTrackId has persisted 0.5 s (marks HANDOFF) and reports every raw switch; `ModelRate`;
  `GapStats`. Used by `state_client.py` and the Mac receiver.

### Lights / body signals
`"lights": {"low": bool, "highArmed": bool, "high": bool}` from frogpilotCarState (AP1 only):

| field | source | notes |
|---|---|---|
| lights.low | 0x318 GTW_carState BC_headLight{L,R}Status == 1 (bus 0) | low beams on |
| lights.highArmed | 0x45 STW_ACTN_RQ HiBmLvr_Stat == 1 (bus 0) | lever held in high-beam detent = Auto High Beams armed |
| lights.high | 0x3E9 DAS_bodyControls DAS_highLowBeamDecision == 2 (bus 2) | high beams actually lit (camera decision) |
| car.leftBlinker / rightBlinker / hazard | GTW_carState indicators -> carState | hazard = both on |
| car.reverse / gear | DI_torque2 DI_gear -> carState.gearShifter | |
| car.doorOpen | GTW_carState doors + frunk + boot (any) | aggregate only |
| car.brakeLight | `brakePressed or (aEgo <= -1.0 and not gas)` | heuristic; carState.brakeLightsDEPRECATED is unset on Tesla |
| (not streamed) | DI_regenLight, ESP_brakeLamp | in DBC, not parsed |

### Ride level (air suspension)
`car.rideLevel` / `car.rideMoving`, from frogpilotCarState `ap1RideLevel` / `ap1RideMoving`
(AP1 only; `selfdrive/car/tesla/ap1_ride.py`). Raw frame: bus 0, **0x11B**, 5 bytes, ~10 Hz;
added to `opendbc/tesla_can.dbc` as `BP_airSuspension` (BogPilot-decoded, not stock opendbc),
parsed at frequency 0 so it can never affect canValid. +0 msgq readers (carState parser path, like lights).

| field | source | values |
|---|---|---|
| car.rideLevel | byte 1 high nibble (`BP_rideLevel`, bits 12-15) | 3 Low, 4 Standard, 5 High, 6 Very High; **0 = not seen yet** (or out-of-range) |
| car.rideMoving | byte 0 bit 4, 0x10 (`BP_rideMoving`) | true while the suspension moves between levels (also short self-levelling blips with no level change) |

One level for the whole car, not per corner and not a measured height: map each level to a height on the client.
Auto (speed) changes post the new level about 5 s before rideMoving sets; manual changes move immediately,
so animate on rideMoving, or on the level change if you want to lead it.

Recorded-drive check: low on 99.9 % (off only at shutdown), highArmed on 100 %,
high on 6.4 % with 26 on/off switches.

## Security / privacy
Binds only a private/loopback IPv4 (`safe_bind_ip`), optional allowlist + token.
Position is only ever in the live stream; StateStream never logs payloads.
Never touches panda safety, controlsd, card or planner outputs.

## Route section (topic `route`)

When navd publishes `navRoute` (destination set in The Pond, or a reroute), StateStream thins it
to ~1 point per 9 m and sends it as numbered chunks on ZMQ topic `route` (same socket/port as
`state`; a client subscribed only to `state` never sees them). Each message is one JSON object
of at most 1000 bytes:

    {"v":1,"route":{"h":"3fa9c021","i":0,"n":7,"pts":[[lat,lon],...]}}
    {"v":1,"route":{"cleared":true}}

- `h`: 8-hex route id; changes on every new route/reroute. Drop chunks of any other `h`.
- `i` / `n`: chunk index (0-based) / chunk total. Route = concat of `pts` for i = 0..n-1.
- `pts`: [latitude, longitude] in degrees, 6 decimals, in driving order; first = start, last = destination.
- Full set is sent immediately on a new route and resent every 10 s while active.
- `cleared` is sent immediately and repeated at +10 s and +20 s; then nothing until a new route.
- Never log coordinates.
