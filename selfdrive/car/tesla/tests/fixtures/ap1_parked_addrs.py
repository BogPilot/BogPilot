"""Redacted facts from a parked AP1 capture. No log, VIN, GPS, or dongle id.

The route was recorded on frog_ap1, not on this BogPilot tree. Counts are
can-RX frame counts from segment 0 of that route. No payload bytes are stored.
"""

ROUTE_LABEL = "ap1-parked-2026-10-03"

# One panda. These three buses carried traffic at the same time.
OBSERVED_BUSES = (0, 1, 2)

# DAS_control on the autopilot powertrain bus in this tree's interface gate.
# Absent on every bus in the capture, including bus 6, which was not seen.
ADDR_0X2BF = 0x2BF
ADDR_0X2BF_COUNT = 0

# Chassis DAS_control. Present. Counts are can RX on the observed buses only.
ADDR_0X2B9 = 0x2B9
ADDR_0X2B9_PRESENT = {
  0: True,
  1: True,
  2: True,
}
ADDR_0X2B9_CAN_COUNTS = {
  0: 148,
  1: 308,
  2: 1530,
}

# DTR_Dist_Rq raw byte on STW_ACTN_RQ (0x45). The seven documented detents.
DTR_DIST_RQ_RAW = (0, 33, 66, 100, 133, 166, 200)

CONTROLS_WENT_ACTIVE = False
VEGO_NEAR_ZERO = True

# Identity of the writer of the route. Not this tree's carParams.
SOURCE_BRANCH = "frog_ap1"
SOURCE_COMMIT = "73a16cdd"
SOURCE_CAR = "TESLA_AP1_MODELS"
SOURCE_FINGERPRINT = "fixed"
# carParams.dashcamOnly on that recording only. This tree still sets it true.
SOURCE_DASHCAM_ONLY = False

NOTE = (
  "Parked. Controls never went active and vEgo stayed near 0. "
  "Not an engagement test. BogPilot was not validated on this route. "
  "VIN, GPS, and dongle id are not stored."
)


def dual_panda_powertrain_seen(addr_0x2bf_count=ADDR_0X2BF_COUNT):
  """The interface long-control gate needs 0x2bf on bus 6. A zero count is not that gate.

  ap1_s does not require this address. A zero count still is not that gate.
  """
  return addr_0x2bf_count > 0
