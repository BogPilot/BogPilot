"""AP1 ride level (0x11B): pure decode, DBC layout via CANParser with synthetic frames, isolation."""
import glob
import pickle
import re
from pathlib import Path

import pytest

from openpilot.selfdrive.car.tesla.ap1_ride import ap1_ride

ROOT = Path(__file__).resolve().parents[4]
PKLS = sorted(glob.glob("/workspace/out/park_can/0000005*.pkl"))  # local-only CAN extracts (no GPS)


def frame(level, moving=False, b3=0x83):
  # synthetic 0x11B payload, 5 bytes like the car: b0 bit4 = moving, b1 high nibble = level
  return bytes([0x10 if moving else 0x00, (level << 4) | 0x0, 0x03, b3, 0xD0])


def test_decode_pure():
  for lvl in (3, 4, 5, 6):
    assert ap1_ride({"BP_airSuspension": {"BP_rideLevel": lvl, "BP_rideMoving": 0}}) == (lvl, False)
  assert ap1_ride({"BP_airSuspension": {"BP_rideLevel": 3, "BP_rideMoving": 1}}) == (3, True)
  assert ap1_ride({}) == (0, False)                                                      # never seen
  assert ap1_ride({"BP_airSuspension": {"BP_rideLevel": 9, "BP_rideMoving": 1}}) == (0, False)  # out of range


def test_dbc_entry_layout_and_no_counter():
  dbc = (ROOT / "opendbc/tesla_can.dbc").read_text()
  body = dbc.split("BO_ 283 BP_airSuspension: 5 XXX")[1].split("BO_ ")[0]
  assert " SG_ BP_rideMoving : 4|1@1+ " in body and " SG_ BP_rideLevel : 12|4@1+ " in body
  assert not re.search(r"SG_ (COUNTER|CHECKSUM) ", body)
  assert dbc.count("BO_ 283 ") == 1
  src = (ROOT / "selfdrive/car/tesla/carstate.py").read_text()
  assert 'messages.append(("BP_airSuspension", 0))' in src  # frequency 0: cannot change canValid


def test_not_used_by_control_or_safety_code():
  for f in ["selfdrive/controls/controlsd.py", "selfdrive/controls/lib/longitudinal_planner.py", "selfdrive/car/tesla/carcontroller.py",
            "frogpilot/controls/frogpilot_planner.py", "frogpilot/controls/frogpilot_card.py"]:
    t = (ROOT / f).read_text()
    assert "ap1Ride" not in t and "BP_airSuspension" not in t, f
  for f in glob.glob(str(ROOT / "panda/board/safety/**/*.h"), recursive=True):
    assert "0x11B" not in Path(f).read_text() or "BP_" not in Path(f).read_text()


def _parser():
  try:
    from opendbc.can.parser import CANParser
    CANParser("tesla_can", [("BP_airSuspension", 0)], 0)
  except Exception:
    return None
  return CANParser


def test_canparser_synthetic_frames():
  CANParser = _parser()
  if CANParser is None:
    pytest.skip("opendbc parser not built here")
  cp = CANParser("tesla_can", [("BP_airSuspension", 0)], 0)
  assert ap1_ride(cp.vl) == (0, False)
  t = 1
  for lvl, mv, want in [(4, False, (4, False)), (3, True, (3, True)), (3, False, (3, False)), (6, True, (6, True))]:
    cp.update_strings([(t, [(0x11B, frame(lvl, mv), 0)])]); t += 10**8
    assert ap1_ride(cp.vl) == want
    assert cp.can_valid
  cp.update_strings([(t, [(0x11B, frame(5), 2)])])  # other bus ignored
  assert ap1_ride(cp.vl) == (6, True)


@pytest.mark.skipif(not PKLS, reason="needs local /workspace/out/park_can extracts")
def test_real_route_levels_match_raw_nibble():
  CANParser = _parser()
  if CANParser is None:
    pytest.skip("opendbc parser not built here")
  for p in PKLS:
    with open(p, "rb") as fh:
      frames = pickle.load(fh)[0][(0, 0x11B)]
    cp = CANParser("tesla_can", [("BP_airSuspension", 0)], 0)
    levels = set()
    for i, (ts, dat) in enumerate(frames):
      cp.update_strings([(int(ts * 1e9), [(0x11B, dat, 0)])])
      got = ap1_ride(cp.vl)
      assert got == (dat[1] >> 4, bool(dat[0] & 0x10))
      levels.add(got[0])
    assert levels == {3, 4, 5, 6}
