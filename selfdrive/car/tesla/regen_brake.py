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


# ColdComfortRegen: file-backed, default OFF (absent / empty / unknown = off).
# July prebuilt params_pyx.so cannot store a new key. Not a Params key, and not
# applied to CarParams or panda safety.
#
# When on, two biases make a lead approach start earlier. Neither raises the
# braking floor and neither changes STOP_DISTANCE (both terms are zero at v = 0):
#   * cool outside air (<= 15 C): comfort target drops slightly, t_follow rises slightly
#   * limited regen (ambient <= 5 C, the existing hard ceiling, or a torque plateau):
#     comfort eases further (1.0 -> 0.7 when RegenComfortBrake is on) and t_follow
#     rises more
# The early aTarget for a closing, decelerating lead lives in regen_aware.ColdComfortEarly.
# When this toggle is off, ap1_comfort_brake() is what callers already used.
COLD_COMFORT_PATH = REGEN_COMFORT_DIR / "ColdComfortRegen"
_cold_comfort_path = COLD_COMFORT_PATH

# Must match regen_aware.COLD_AMBIENT_C. This module does not change that power ceiling.
HARD_COLD_AMBIENT_C = 5.0
COOL_AMBIENT_C = 15.0

# Drops are subtracted from the current comfort target (lower = earlier). Cool is a
# nudge; limited is the larger ease (1.0 -> 0.7). The floor stays positive so the
# v = 0 gap term cannot explode. A drop never increases comfort.
COOL_COMFORT_DROP = 0.05
LIMITED_COMFORT_DROP = 0.30
COLD_COMFORT_FLOOR = 0.60

# Added to t_follow (seconds). Cool is the small bias; limited is larger.
# Callers clamp with their own cap (frogpilot MAX_T_FOLLOW).
COOL_T_FOLLOW_ADD = 0.15
LIMITED_T_FOLLOW_ADD = 0.40
_T_FOLLOW_CAP_DEFAULT = 3.0


def cold_comfort_regen_enabled(fingerprint: str | None, stored: str | bytes | None = None) -> bool:
  """Whether AP1 cold/cool comfort regen is active.

  Non-AP1 fingerprints are always off. AP1: default off when the file is absent.
  Explicit 1/true/on => on. Anything else, including empty, => off.
  `stored` overrides the file when not None (tests).
  """
  from openpilot.selfdrive.car.tesla.values import CAR
  if fingerprint != CAR.TESLA_AP1_MODELS:
    return False
  if stored is not None:
    text = _as_text(stored)
  else:
    text = _read_toggle_file(_cold_comfort_path)
  return text.lower() in ("1", "true", "on")


def set_cold_comfort_regen(enabled: bool, path: Path | None = None) -> None:
  """Write the file-backed ColdComfortRegen preference."""
  target = path if path is not None else _cold_comfort_path
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text("1" if enabled else "0")


def ap1_ambient_c(value) -> float | None:
  """Outside-air °C from frogpilotCarState, or None when the signal is missing."""
  try:
    amb = float(value)
  except (TypeError, ValueError):
    return None
  if amb != amb:  # NaN
    return None
  return amb


def cold_comfort_level(ambient_c: float | None, plateau_limited: bool) -> str:
  """'limited', 'cool', or 'none'.

  Hard cold (<= 5 C) and a measured torque plateau are 'limited'. The soft cool
  band (<= 15 C) is only 'cool'. Warmer air with no plateau is 'none'.
  """
  if plateau_limited or (ambient_c is not None and ambient_c <= HARD_COLD_AMBIENT_C):
    return "limited"
  if ambient_c is not None and ambient_c <= COOL_AMBIENT_C:
    return "cool"
  return "none"


def apply_cold_comfort_brake(base: float, ambient_c: float | None, plateau_limited: bool, enabled: bool) -> float:
  """Comfort brake after the cold/cool bias. Off => `base` unchanged (never higher)."""
  if not enabled:
    return base
  level = cold_comfort_level(ambient_c, plateau_limited)
  if level == "limited":
    dropped = base - LIMITED_COMFORT_DROP
  elif level == "cool":
    dropped = base - COOL_COMFORT_DROP
  else:
    return base
  return max(COLD_COMFORT_FLOOR, dropped)


def apply_cold_t_follow(t_follow: float, ambient_c: float | None, plateau_limited: bool, enabled: bool,
                        cap: float = _T_FOLLOW_CAP_DEFAULT) -> float:
  """t_follow after the cold/cool bias. Off, or a non-positive follow time, is unchanged.

  A positive follow time only stays the same or grows, and never passes `cap`.
  """
  if not enabled or t_follow <= 0.0:
    return t_follow
  level = cold_comfort_level(ambient_c, plateau_limited)
  if level == "limited":
    bumped = t_follow + LIMITED_T_FOLLOW_ADD
  elif level == "cool":
    bumped = t_follow + COOL_T_FOLLOW_ADD
  else:
    return t_follow
  return min(cap, bumped)


def _read_regen_comfort_file() -> str:
  return _read_toggle_file(_regen_comfort_path)


def _read_toggle_file(path: Path) -> str:
  try:
    if path.is_file():
      return path.read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    pass
  return ""


def _as_text(stored: str | bytes | None) -> str:
  if stored is None:
    return ""
  if isinstance(stored, bytes):
    stored = stored.decode()
  return stored.strip()
