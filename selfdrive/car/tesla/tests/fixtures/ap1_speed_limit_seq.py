"""Decoded AP1 speed-limit sequences from real chassis CAN (no PII).

Values are physical DAS_fusedSpeedLimit (mph, DBC factor 5 already applied),
UI_mapSpeedLimit enum codes, UI_mppSpeedLimit physical mph, and
UI_mapSpeedLimitUnits (0=MPH). Source bus for fused is autopilot_chassis
(src 2) only — stock Mobileye, not cluster TX.

No GPS, VIN, dongle, or route names.
"""

# (fused_phys, ui_map_code, mpp_phys, units) samples where fused and UI agree
# on a posted zone. From logged stock frames.
ZONE_SAMPLES = (
  (25.0, 6, 25.0, 0),   # LEQ_25
  (35.0, 8, 35.0, 0),   # LEQ_35
  (40.0, 9, 40.0, 0),   # LEQ_40
  (45.0, 10, 45.0, 0),  # LEQ_45
  (70.0, 15, 70.0, 0),  # LEQ_70
)

# Fused-only transitions (stock src 2). UI may lag a frame; Mobileye wins.
FUSED_TRANSITIONS = (
  (25.0, 35.0),
  (35.0, 40.0),
  (40.0, 35.0),
  (40.0, 25.0),
  (70.0, 25.0),
  (70.0, 35.0),
  (35.0, 25.0),
  (45.0, 35.0),
  (45.0, 25.0),
)

# Real stock AutopilotStatus payloads (hex) with known fused physical mph.
# Unpacked by cluster.unpack → DAS_fusedSpeedLimit matches fused_phys.
STOCK_FUSED_FRAMES = (
  # fused_phys, hex
  (25.0, "0125000000006022"),
  (35.0, "010700000000b054"),
  (40.0, "010808000000f09d"),
  (45.0, "01090000000050f6"),
  (70.0, "010e050000007020"),
  (5.0, "012100000000803e"),  # LEQ_5 zone, not SNA
)

# SNA / NONE: raw 31 → physical 155
FUSED_NONE_PHYS = 155.0
FUSED_UNKNOWN_PHYS = 0.0
