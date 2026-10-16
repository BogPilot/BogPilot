"""AP1 air-suspension ride level, for display / the BogPilot state stream only.

Pure: takes the chassis CANParser .vl dict. Nothing in controls, planning or panda safety reads it.

  0x11B bus 0, BP_airSuspension (BogPilot DBC entry; decoded from routes 0x51/0x52, 5 bytes, ~10 Hz)
    BP_rideLevel  byte 1 high nibble: 3 Low, 4 Standard, 5 High, 6 Very High
    BP_rideMoving byte 0 bit 4 (0x10): suspension moving between levels (also short self-levelling blips)
One level for the whole car, not a measured height. Auto changes post the new level
about 5 s before the moving bit sets; manual changes move immediately.
Absent message (vl default 0) or an out-of-range level reads as level 0, not moving.
"""

RIDE_LEVELS = {3: "Low", 4: "Standard", 5: "High", 6: "Very High"}


def ap1_ride(vl):
  m = vl.get("BP_airSuspension", {}) if hasattr(vl, "get") else {}
  lvl = int(m.get("BP_rideLevel", 0) or 0)
  if lvl not in RIDE_LEVELS:
    return 0, False
  return lvl, bool(int(m.get("BP_rideMoving", 0) or 0))
