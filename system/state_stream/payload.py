"""Build one compact StateStream snapshot from an existing SubMaster.

Runs on the host loop (frogpilot_process), so it only copies a few scalars and
downsampled lists out of capnp readers into plain Python values. No I/O, no
logging, no serialization here: the sender thread does JSON + ZMQ.

Sources are ONLY services the host SubMaster already reads, so no msgq reader is
added (on a recorded drive carState was at NUM_READERS=15). GPS comes from
liveLocationKalman (fused GNSS), not gpsLocation/gpsLocationExternal.
liveTracks / liveCalibration are included only if the host already subscribes
to them (it does not by default).

Never log the output: it carries position.
"""
from __future__ import annotations

import math

# Model points: 33 on the ModelConstants.T_IDXS grid; every 3rd point -> 11 per line.
MODEL_STRIDE = 3
BRAKE_DECEL = -1.0  # m/s^2: heuristic "brake light" when no pedal (Tesla lights on strong regen)


def _f(x, nd=3):
  x = float(x)
  return round(x, nd) if math.isfinite(x) else None


def _xyz(line, stride=MODEL_STRIDE):
  xs, ys, zs = line.x, line.y, line.z
  n = min(len(xs), len(ys), len(zs))
  return [[_f(xs[i], 2), _f(ys[i], 2), _f(zs[i], 2)] for i in range(0, n, stride)]


def _lead(ld):
  # [status, dRel, yRel, vRel, aRel, vLead, modelProb, radar]
  return [bool(ld.status), _f(ld.dRel, 2), _f(ld.yRel, 2), _f(ld.vRel, 2), _f(ld.aRel, 2),
          _f(ld.vLead, 2), _f(ld.modelProb, 2), bool(ld.radar)]


def car_section(cs, lkf_yaw=None, fpcs=None):
  brake_light = bool(cs.brakePressed) or (float(cs.aEgo) <= BRAKE_DECEL and not cs.gasPressed)
  return {
    "vEgo": _f(cs.vEgo), "aEgo": _f(cs.aEgo), "standstill": bool(cs.standstill),
    "steeringAngleDeg": _f(cs.steeringAngleDeg, 2),
    "yawRate": _f(cs.yawRate, 4), "yawRateLkf": lkf_yaw,
    "leftBlinker": bool(cs.leftBlinker), "rightBlinker": bool(cs.rightBlinker),
    "hazard": bool(cs.leftBlinker and cs.rightBlinker),
    "leftBlindspot": bool(cs.leftBlindspot), "rightBlindspot": bool(cs.rightBlindspot),
    "brakePressed": bool(cs.brakePressed), "brakeLight": brake_light,
    "gasPressed": bool(cs.gasPressed), "gear": str(cs.gearShifter),
    "reverse": str(cs.gearShifter) == "reverse", "doorOpen": bool(cs.doorOpen),
    # 0x11B ride level 3..6 (0 = not seen) and moving-between-levels bit, via frogpilotCarState.
    "rideLevel": int(fpcs.ap1RideLevel) if fpcs is not None else 0,
    "rideMoving": bool(fpcs.ap1RideMoving) if fpcs is not None else False,
  }


def gps_section(lkf):
  pg = lkf.positionGeodetic
  v = lkf.velocityNED.value
  o = lkf.calibratedOrientationNED.value
  pe = lkf.positionECEF.std
  acc = math.sqrt(pe[0] ** 2 + pe[1] ** 2 + pe[2] ** 2) if len(pe) >= 3 else None
  return {
    "valid": bool(pg.valid) and bool(lkf.gpsOK),
    "gpsOK": bool(lkf.gpsOK), "status": str(lkf.status),
    "lat": _f(pg.value[0], 7) if len(pg.value) > 2 else None,
    "lon": _f(pg.value[1], 7) if len(pg.value) > 2 else None,
    "alt": _f(pg.value[2], 1) if len(pg.value) > 2 else None,
    "headingDeg": _f(math.degrees(o[2]) % 360.0, 2) if len(o) > 2 else None,
    "speed": _f(math.hypot(v[0], v[1]), 2) if len(v) > 1 else None,
    "accuracyM": _f(acc, 2) if acc is not None else None,
    "unixMs": int(lkf.unixTimestampMillis),
  }


def model_section(md):
  return {
    "laneLines": [_xyz(l) for l in md.laneLines],
    "laneLineProbs": [_f(p) for p in md.laneLineProbs],
    "roadEdges": [_xyz(l) for l in md.roadEdges],
    "roadEdgeStds": [_f(s) for s in md.roadEdgeStds],
    "leadsV3": [[_f(l.prob), _f(l.x[0], 2), _f(l.y[0], 2), _f(l.v[0], 2)] for l in (md.leadsV3[i] for i in range(min(2, len(md.leadsV3)))) if len(l.x)],
  }


def build(sm, frame: int, include_model: bool) -> dict:
  """sm: SubMaster-like (sm[svc], sm.logMonoTime[svc], sm.seen[svc])."""
  mono = sm.logMonoTime
  out = {"v": 1, "frame": frame, "mono": {}}
  seen = sm.seen

  lkf_yaw = None
  if seen.get("liveLocationKalman"):
    lkf = sm["liveLocationKalman"]
    av = lkf.angularVelocityCalibrated.value
    lkf_yaw = _f(av[2], 4) if len(av) > 2 else None
    out["gps"] = gps_section(lkf)
    out["mono"]["liveLocationKalman"] = mono["liveLocationKalman"]
  if seen.get("carState"):
    out["car"] = car_section(sm["carState"], lkf_yaw, sm["frogpilotCarState"] if seen.get("frogpilotCarState") else None)
    out["mono"]["carState"] = mono["carState"]
  if seen.get("frogpilotCarState"):
    f = sm["frogpilotCarState"]
    out["lights"] = {"low": bool(f.ap1HeadlightsOn), "highArmed": bool(f.ap1HighBeamLever), "high": bool(f.ap1HighBeamOn)}
    out["mono"]["frogpilotCarState"] = mono["frogpilotCarState"]
  if seen.get("radarState"):
    rs = sm["radarState"]
    out["leads"] = {"leadOne": _lead(rs.leadOne), "leadTwo": _lead(rs.leadTwo)}
    out["mono"]["radarState"] = mono["radarState"]
  if include_model and seen.get("modelV2"):
    out["model"] = model_section(sm["modelV2"])
    out["mono"]["modelV2"] = mono["modelV2"]
  # Extras only when the host already subscribes (never added by StateStream).
  if seen.get("liveTracks"):
    out["tracks"] = [[int(t.trackId), _f(t.dRel, 2), _f(t.yRel, 2), _f(t.vRel, 2)] for t in sm["liveTracks"]]
    out["mono"]["liveTracks"] = mono["liveTracks"]
  if seen.get("liveCalibration"):
    out["calib"] = {"rpyCalib": [_f(x, 5) for x in sm["liveCalibration"].rpyCalib]}
    out["mono"]["liveCalibration"] = mono["liveCalibration"]
  return out
