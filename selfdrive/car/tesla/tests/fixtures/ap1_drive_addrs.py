"""Redacted facts from an engaged AP1 drive. No log, VIN, GPS, or payloads.

The route was recorded on frog_ap1, not on this BogPilot tree. Addresses and
scalars only. No payload bytes are stored. The device RTC year is untrusted.
"""

ROUTE_LABEL = "ap1-drive-engaged"

# Identity of the writer of the route. Not this tree's carParams.
SOURCE_BRANCH = "frog_ap1"
SOURCE_COMMIT = "73a16cdd"
SOURCE_CAR = "TESLA_AP1_MODELS"
SOURCE_FINGERPRINT = "fixed"
# carParams on that recording only. This tree still sets dashcamOnly true.
SOURCE_DASHCAM_ONLY = False
SOURCE_SAFETY_PARAM = 0

# One panda. 0x2bf was not on the bus.
PANDA_COUNT = 1
ADDR_0X2BF = 0x2BF
ADDR_0X2BF_ABSENT = True

# While latActive and longActive, sendcan on bus 0 contained these addresses.
# Addresses only. No payloads.
SENDCAN_BUS = 0
LAT_ACTIVE = True
LONG_ACTIVE = True
SENDCAN_ADDRS_WHILE_ACTIVE = (0x2B9, 0x488)

ENGAGED_S = 88.5
MAX_VEGO_MPS = 14.2

DEVICE_RTC_YEAR_UNTRUSTED = True

# Same seven DTR_Dist_Rq raw values already recorded on the parked fixture.
DTR_DIST_RQ_RAW = (0, 33, 66, 100, 133, 166, 200)

NOTE = (
  "Engaged on the frog_ap1 recording only. "
  "This does not validate BogPilot engagement. "
  "dashcamOnly stays true in this tree. "
  "VIN and payloads are not stored. Device RTC year is untrusted."
)
