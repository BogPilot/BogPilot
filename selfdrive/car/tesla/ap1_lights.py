"""AP1 exterior light state, for display / the BogPilot state stream only.

Pure: takes CANParser .vl dicts. Nothing in controls, planning or panda safety reads it.

  low beams     0x318 GTW_carState     BC_headLightLStatus / BC_headLightRStatus == 1 (bus 0, already parsed)
  high armed    0x45  STW_ACTN_RQ      HiBmLvr_Stat == 1 HIBM_ON_PSD: lever held in the high-beam
                                       detent; with Auto High Beams on this means "armed" (bus 0, already parsed)
  high lit      0x3E9 DAS_bodyControls DAS_highLowBeamDecision == 2 DAS_HIGH_BEAM_ON (bus 2, camera decision)
Absent message (vl default 0) reads as off.
"""

HEADLIGHT_ON = 1
HIBM_LEVER_ON = 1
HIGH_BEAM_ON = 2


def ap1_lights(vl, vl_cam):
  gtw = vl.get("GTW_carState", {}) if hasattr(vl, "get") else {}
  stw = vl.get("STW_ACTN_RQ", {}) if hasattr(vl, "get") else {}
  das = vl_cam.get("DAS_bodyControls", {}) if hasattr(vl_cam, "get") else {}
  low = int(gtw.get("BC_headLightLStatus", 0) or 0) == HEADLIGHT_ON or int(gtw.get("BC_headLightRStatus", 0) or 0) == HEADLIGHT_ON
  armed = int(stw.get("HiBmLvr_Stat", 0) or 0) == HIBM_LEVER_ON
  high = int(das.get("DAS_highLowBeamDecision", 0) or 0) == HIGH_BEAM_ON
  return bool(low), bool(armed), bool(high)
