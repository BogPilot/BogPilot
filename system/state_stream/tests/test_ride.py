"""StateStream car.rideLevel / car.rideMoving (0x11B via frogpilotCarState)."""
from types import SimpleNamespace as NS

from openpilot.system.state_stream import payload
from openpilot.system.state_stream.tests.test_state_stream import SM, fake_sm


def _sm(fpcs_fields=None, with_fpcs=True):
  ns = fake_sm()
  if not with_fpcs:
    ns.d.pop("frogpilotCarState")
    ns.seen.pop("frogpilotCarState")
  elif fpcs_fields is not None:
    ns.d["frogpilotCarState"] = NS(ap1HeadlightsOn=False, ap1HighBeamLever=False, ap1HighBeamOn=False, **fpcs_fields)
  return SM(ns)


def test_ride_fields_streamed():
  for lvl in (3, 4, 5, 6):
    c = payload.build(_sm({"ap1RideLevel": lvl, "ap1RideMoving": lvl == 5}), 1, False)["car"]
    assert c["rideLevel"] == lvl and c["rideMoving"] is (lvl == 5)
    assert type(c["rideLevel"]) is int


def test_ride_unseen_is_zero():
  c = payload.build(_sm({"ap1RideLevel": 0, "ap1RideMoving": False}), 1, False)["car"]
  assert c["rideLevel"] == 0 and c["rideMoving"] is False
  c = payload.build(_sm(with_fpcs=False), 1, False)["car"]  # frogpilotCarState not seen yet
  assert c["rideLevel"] == 0 and c["rideMoving"] is False
