"""AP1 regen-sized comfort braking preference.

On 2012–2016 Model S AP1 the brake pedal is friction-only; regen happens on
accelerator lift and tops out around 1–1.5 m/s² (about 1.0 at 30–35 mph). Stock openpilot COMFORT_BRAKE
is 2.5 m/s², so lead approaches start late and need friction. This module
exposes a file-backed toggle and the AP1 comfort-brake target used by the
longitudinal MPC lead cost (runtime x_obstacle adjustment; acados solver
unchanged).

July prebuilt params_pyx.so cannot store new Tesla* Params keys, so this is
file-backed under /data/params_bogpilot/RegenComfortBrake (same pattern as
EnableICIntegration). Absent file + AP1 fingerprint => on. Explicit 0/false/off
=> off. Non-AP1 => always off (stock 2.5 m/s²).

Change on device:
  echo 0 > /data/params_bogpilot/RegenComfortBrake   # off (stock)
  echo 1 > /data/params_bogpilot/RegenComfortBrake   # on
then restart openpilot.

Not applied to CarParams, panda safety, or the ACCEL_MIN braking floor.
Hard accel limits stay at ACCEL_MIN so FCW/AEB/cut-in/danger still get full
braking. STOP_DISTANCE is unchanged.
"""

from pathlib import Path

# Must match long_mpc.COMFORT_BRAKE. Kept here to avoid importing the MPC module
# (acados) from this lightweight toggle helper.
STOCK_COMFORT_BRAKE = 2.5

# Regen-sized comfort target for AP1 lead approaches (m/s²).
# Route 0000002a: at 30–35 mph regen alone topped out near 1.0 m/s² actual
# decel before the friction brakes joined, so the comfort target is 1.0.
# At v = 0 the lead cost reduces to STOP_DISTANCE for any value here, so the
# stopped gap does not change.
AP1_REGEN_COMFORT_BRAKE = 1.0

REGEN_COMFORT_DIR = Path("/data/params_bogpilot")
REGEN_COMFORT_PATH = REGEN_COMFORT_DIR / "RegenComfortBrake"
# Tests may override the path via this module attribute.
_regen_comfort_path = REGEN_COMFORT_PATH


def regen_comfort_brake_enabled(fingerprint: str | None, stored: str | bytes | None = None) -> bool:
  """Whether AP1 regen-sized comfort braking is active.

  Non-AP1 fingerprints are always off. AP1: default on when the file is absent.
  `stored` overrides the file when not None (tests).
  """
  from openpilot.selfdrive.car.tesla.values import CAR
  if fingerprint != CAR.TESLA_AP1_MODELS:
    return False
  if stored is not None:
    text = _as_text(stored)
  else:
    text = _read_regen_comfort_file()
  if text == "":
    return True
  if text in ("1", "true", "on"):
    return True
  if text in ("0", "false", "off"):
    return False
  return True


def ap1_comfort_brake(fingerprint: str | None, stored: str | bytes | None = None) -> float:
  """Comfort-brake (m/s²) for the lead MPC desired-distance cost.

  AP1 with toggle on => AP1_REGEN_COMFORT_BRAKE. Otherwise stock.
  """
  if regen_comfort_brake_enabled(fingerprint, stored=stored):
    return AP1_REGEN_COMFORT_BRAKE
  return STOCK_COMFORT_BRAKE


def set_regen_comfort_brake(enabled: bool, path: Path | None = None) -> None:
  """Write the file-backed RegenComfortBrake preference."""
  target = path if path is not None else _regen_comfort_path
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text("1" if enabled else "0")


def _read_regen_comfort_file() -> str:
  try:
    if _regen_comfort_path.is_file():
      return _regen_comfort_path.read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    pass
  return ""


def _as_text(stored: str | bytes | None) -> str:
  if stored is None:
    return ""
  if isinstance(stored, bytes):
    stored = stored.decode()
  return stored.strip()
