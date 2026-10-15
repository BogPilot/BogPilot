"""AP1 exterior lights: pure decode, parser config safety, and an east-drive replay."""
import glob
import os
import re
from pathlib import Path

import pytest

from openpilot.selfdrive.car.tesla.ap1_lights import ap1_lights

ROOT = Path(__file__).resolve().parents[4]
EAST = sorted(glob.glob("/workspace/realdata/east/*/rlog"), key=lambda p: int(p.split("/")[-2].rsplit("--", 1)[1]))


def test_decode():
  vl = {"GTW_carState": {"BC_headLightLStatus": 1, "BC_headLightRStatus": 1}, "STW_ACTN_RQ": {"HiBmLvr_Stat": 1}}
  assert ap1_lights(vl, {"DAS_bodyControls": {"DAS_highLowBeamDecision": 2}}) == (True, True, True)
  assert ap1_lights(vl, {"DAS_bodyControls": {"DAS_highLowBeamDecision": 1}}) == (True, True, False)
  assert ap1_lights({}, {}) == (False, False, False)  # absent -> off
  off = {"GTW_carState": {"BC_headLightLStatus": 0, "BC_headLightRStatus": 0}, "STW_ACTN_RQ": {"HiBmLvr_Stat": 0}}
  assert ap1_lights(off, {"DAS_bodyControls": {"DAS_highLowBeamDecision": 3}}) == (False, False, False)


def test_parser_entry_cannot_affect_canvalid():
  src = (ROOT / "selfdrive/car/tesla/carstate.py").read_text()
  assert 'messages.append(("DAS_bodyControls", 0))' in src  # frequency 0 = never missing / timed out
  dbc = (ROOT / "opendbc/tesla_can.dbc").read_text()
  body = dbc.split("BO_ 1001 DAS_bodyControls")[1].split("BO_ ")[0]
  # opendbc only checks signals literally named COUNTER / CHECKSUM
  assert not re.search(r"SG_ (COUNTER|CHECKSUM) ", body)


def test_lights_not_used_by_control_code():
  for f in ["selfdrive/controls/controlsd.py", "selfdrive/controls/lib/longitudinal_planner.py", "selfdrive/car/tesla/carcontroller.py",
            "frogpilot/controls/frogpilot_planner.py", "frogpilot/controls/frogpilot_card.py"]:
    t = (ROOT / f).read_text()
    assert "ap1HighBeam" not in t and "ap1HeadlightsOn" not in t, f


def _parser():
  try:
    from opendbc.can.parser import CANParser
  except ImportError:
    return None
  return CANParser


@pytest.mark.skipif(not EAST, reason="needs /workspace/realdata/east")
def test_replay_east_toggles():
  CANParser = _parser()
  if CANParser is None:
    pytest.skip("opendbc parser not built here")
  from cereal import log
  cp = CANParser("tesla_can", [("GTW_carState", 10), ("STW_ACTN_RQ", 10)], 0)
  cp_cam = CANParser("tesla_can", [("DAS_bodyControls", 0)], 2)
  prev, changes, n = None, 0, 0
  for p in EAST:
    with open(p, "rb") as fh:
      raw = fh.read()
    for e in log.Event.read_multiple_bytes(raw, traversal_limit_in_words=2**63 - 1):
      if e.which() != "can":
        continue
      frames = [(m.address, bytes(m.dat), m.src) for m in e.can if m.src in (0, 2) and m.address in (69, 792, 1001)]
      if not frames:
        continue
      cp.update_strings([(e.logMonoTime, frames)])
      cp_cam.update_strings([(e.logMonoTime, frames)])
      assert cp_cam.can_valid  # frequency-0 entry never invalidates the camera parser
      cur = ap1_lights(cp.vl, cp_cam.vl)
      n += 1
      if prev is not None and cur[2] != prev[2]:
        changes += 1
      prev = cur
  assert n > 1000
  print(f"\neast: high-beam (DAS decision) toggles {changes}")
  assert 25 <= changes <= 28  # raw scan: 27 decision changes (incl. one 0 at shutdown)
