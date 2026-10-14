"""One-time BogPilot toggle profile for the Tesla AP1 Model S.

AP1 is never auto-fingerprinted: owners pick "Tesla AP1 Model S" under
Vehicle Settings, which stores CarModel=TESLA_AP1_MODELS. The first time
BogPilot sees that (at boot, or offroad when settings close), it applies the
maintainer's recommended toggles below, once:

  - a Params key is written only if its current value still equals the
    frogpilot_default_params table default, so anything the owner already
    changed stays put
  - a /data/params_bogpilot file toggle is written only if the file is absent
  - then /data/params_bogpilot/AP1DefaultsApplied is written so it never runs
    again. "Reset toggles to default" (DoToggleReset) deletes that marker, so
    a reset re-applies the AP1 profile. "Reset to stock" leaves it alone.

No new Params keys (the prebuilt params_pyx.so raises on unknown keys): the
marker and the file toggles live in /data/params_bogpilot. Nothing here touches
CarParams, panda safety, or driving code; it only stores toggle values.

The profile only lists values that differ from the table defaults, except the
few in EXPLICIT_DEFAULT_KEYS that are spelled out on purpose. Keys that are
secret, personal, or tied to one device or car are never part of it (see
NEVER_PROFILE_KEYS); the tests enforce that.

Speed-limit offsets are kept in mph (AP1_SPEED_OFFSETS_MPH). On a metric device
(IsMetric) they are converted to km/h and rounded before writing, because the
Offset params are stored in the device's display unit.

Theme: every theme menu (Color Scheme, Distance Button, Icon Pack, Sound Pack,
Steering Wheel, Turn Signal) is set to the BogPilot theme, whose colors, icons,
sounds and wheel are copies of the stock openpilot files
(frogpilot/assets/bogpilot_theme). Colors and wheel still look exactly stock
through the stand-in in frogpilot_variables.resolve_bogpilot_theme. A separate
one-time step (marker AP1ThemeBogPilot2) also moves values that only pick the
stock look ("stock", or "none" for turn signals) to BogPilot, because those
render the same. It runs once per device even where the main profile already
ran, and never touches Distance Button, a "none" steering wheel, or a
downloaded or user-made theme.
"""

from pathlib import Path

AP1_CAR_MODEL = "TESLA_AP1_MODELS"

BOGPILOT_DIR = Path("/data/params_bogpilot")
MARKER_NAME = "AP1DefaultsApplied"
# "2": the 293184ad step (marker AP1ThemeBogPilot) left Color Scheme and Steering Wheel on stock, so the
# widened step must run once more on devices that already ran that one.
THEME_MARKER_NAME = "AP1ThemeBogPilot2"
OLD_THEME_MARKER_NAMES = ("AP1ThemeBogPilot",)
BOGPILOT_THEME = "BogPilot"

# BogPilot file-backed toggles (not Params keys). Absent file = that toggle's built-in default.
FILE_TOGGLE_KEYS = ("ConfidenceBall", "ConfidenceBallSide", "DeveloperHUD", "RemoteUIStream",
                    "EnableICIntegration", "RegenComfortBrake", "SwapSpeedGraph")

# Written as a pair, and only while the tuning level was never confirmed. Without
# TuningLevelConfirmed=1 the settings panel's first-open popup would drop a new
# owner to "Minimal" (TuningLevel 0). Terms and training (HasAcceptedTerms,
# CompletedTrainingVersion) are separate keys and are never touched here.
TUNING_LEVEL_KEYS = ("TuningLevel", "TuningLevelConfirmed")

# Params the profile must never carry: secrets, personal data, per-device or
# per-car state, connectivity and data-sharing choices, units, and the model
# (a fresh device has not downloaded it). All *Stock keys are excluded too.
NEVER_PROFILE_KEYS = frozenset({
  # secrets and accounts
  "GithubSshKeys", "GithubUsername", "MapboxPublicKey", "MapboxSecretKey", "AMapKey1", "AMapKey2",
  "WeatherToken", "SecOCKey", "SecOCKeys", "KonikDongleId", "DiscordUsername", "GsmApn", "FrogPilotStats",
  # personal places and recordings
  "FavoriteDestinations", "MapsSelected", "SearchInput", "RecordFront",
  # vehicle selection (the trigger itself, never written by the profile)
  "CarMake", "CarModel", "CarModelName", "ForceFingerprint",
  # per-car learned or hand-measured tuning
  "SteerRatio", "SteerDelay", "SteerFriction", "SteerKP", "SteerLatAccel", "LongitudinalActuatorDelay",
  "StartAccel", "StopAccel", "StoppingDecelRate", "VEgoStarting", "VEgoStopping", "LiveDelay",
  "CalibratedLateralAcceleration",
  # device bookkeeping
  "ThemesDownloaded", "ShownToggleDescriptions", "ExperimentalModeConfirmed", "UpdatedToggles",
  "MinimumBackupSize", "LastKnownTime",
  # connectivity, uploads, data sharing, updates
  "SshEnabled", "TetheringEnabled", "GsmRoaming", "NoUploads", "NoLogging", "DisableOnroadUploads",
  "UseKonikServer", "HigherBitrate", "DeviceManagement", "AutomaticUpdates",
  # units are the owner's locale, not an AP1 recommendation
  "IsMetric", "Fahrenheit", "UseSI",
  # driving model: a fresh device won't have the maintainer's model downloaded
  "Model",
})

# Keys deliberately listed even though the value equals the table default, so the
# profile states the maintainer's full choice for them.
# Theme params: all set to the BogPilot theme (equal to the table default, listed on purpose).
THEME_KEYS = ("CustomColors", "CustomDistanceIcons", "CustomIcons", "CustomSignals", "CustomSounds", "WheelIcon")

EXPLICIT_DEFAULT_KEYS = frozenset({"AccelerationProfile", "PersonalizeOpenpilot", "RandomThemes", *THEME_KEYS})

# Theme values that only select the stock look, per key, which the BogPilot theme
# reproduces exactly today. The one-time theme step moves these to BogPilot.
# Not listed on purpose: CustomDistanceIcons (BogPilot has its own distance icons,
# so stock ones would change) and WheelIcon "none" (no wheel drawn at all).
STOCK_LOOK_THEME_VALUES: dict[str, frozenset[str]] = {
  "CustomColors": frozenset({"stock"}),
  "CustomIcons": frozenset({"stock"}),
  "CustomSounds": frozenset({"stock"}),
  "CustomSignals": frozenset({"stock", "none"}),
  "WheelIcon": frozenset({"stock"}),
}

# Speed-limit controller offsets in mph, by posted-limit band (the bands are the
# settings labels for Offset1..Offset7 and speed_limit_controller.py's mph table).
AP1_SPEED_OFFSETS_MPH: dict[str, int] = {
  "Offset1": 4,                          # 0-24 mph limits: +4
  "Offset2": 5,                          # 25-34 mph: +5
  "Offset3": 5,                          # 35-44 mph: +5
  "Offset4": 5,                          # 45-54 mph: +5
  "Offset5": 7,                          # 55-64 mph: +7
  "Offset6": 8,                          # 65-74 mph: +8
  "Offset7": 10,                         # 75-99 mph: +10 (same as the table default)
}
MPH_TO_KPH = 1.609344

# Recommended Params values. Strings are stored exactly as the settings UI writes them.
AP1_PARAM_PROFILE: dict[str, str] = {
  # --- Driving / longitudinal ---
  "ConditionalExperimental": "0",        # Conditional Experimental Mode off (no automatic EM switching)
  "CECurves": "1",                       # CE child: switch to EM for curves (inert while CE is off)
  "CENavigationIntersections": "1",      # CE child: EM for nav intersections (inert while CE is off)
  "CustomPersonalities": "1",            # custom follow-distance personalities on
  "RelaxedFollow": "2.000000",           # Relaxed personality follow time 2.0 s (default 1.75 s)
  # Profile enums (longitudinal_settings.cc): acceleration 0 Standard, 1 Eco, 2 Sport, 3 Sport+;
  # deceleration 0 Standard, 1 Eco, 2 Eco+.
  "AccelerationProfile": "2",            # acceleration profile Sport (same as the table default, listed on purpose)
  "DecelerationProfile": "2",            # deceleration profile Eco+ (coasts more, brakes as softly as possible; default Eco)
  "NewLongAPI": "0",                     # comma new long API off (Hyundai-only, no effect on Tesla)
  "SLCOverride": "2",                    # SLC override after driving faster: Max Set Speed
  "SLCPriority1": "Dashboard",           # SLC source 1: Dashboard (BogPilot feeds AP1 cluster limits here)
  "SLCPriority3": "Navigation",          # SLC source 3: Navigation
  "SLCLookaheadHigher": "1.000000",      # look 1 s ahead for a higher upcoming limit
  "SLCLookaheadLower": "1.000000",       # look 1 s ahead for a lower upcoming limit
  # Offset1..Offset7 come from AP1_SPEED_OFFSETS_MPH (unit-aware), not from this table.

  # --- Lateral ---
  "AlwaysOnLateral": "0",                # Always On Lateral off
  "NNFFLite": "1",                       # NNFF-Lite on (inert on AP1: angle steering skips it)

  # --- Visuals / screen ---
  "DeveloperUI": "1",                    # Developer UI parent on (Developer HUD, border metrics)
  "BorderMetrics": "1",                  # screen-edge status border (blind spot / torque / signal)
  "DeveloperSidebar": "0",               # 300 px developer sidebar off (keeps the full driving view)
  "LeadInfo": "0",                       # lead metrics off: the Developer HUD already shows lead data
  "FPSCounter": "0",                     # FPS counter off
  "AdjacentLeadsUI": "0",                # adjacent radar leads off
  "ShowStoppingPoint": "0",              # model stopping-point marker off
  "ShowStoppingPointMetrics": "0",       # stopping-point distance off
  "ShowSLCOffset": "0",                  # hide the SLC offset under the speed limit
  "OnroadDistanceButton": "1",           # driving-personality button on the driving screen
  "CameraView": "0",                     # camera view Auto (default Wide)
  "DriverCamera": "1",                   # show driver camera in reverse
  "MapStyle": "0",                       # map style: stock openpilot
  # --- Theme: BogPilot in every theme menu (assets are copies of the stock look) ---
  "PersonalizeOpenpilot": "1",           # Custom Themes on, so the theme selections below apply (table default)
  "RandomThemes": "0",                   # no random theme per drive (table default)
  "CustomColors": BOGPILOT_THEME,        # Color Scheme (renders stock while its colors.json equals stock)
  "WheelIcon": BOGPILOT_THEME,           # Steering Wheel (renders stock while its wheel.png equals stock)
  "CustomDistanceIcons": BOGPILOT_THEME, # Distance Button
  "CustomIcons": BOGPILOT_THEME,         # Icon Pack (copies of the stock icons)
  "CustomSounds": BOGPILOT_THEME,        # Sound Pack (copies of the stock sounds)
  "CustomSignals": BOGPILOT_THEME,       # Turn Signal (BogPilot has no frames, same as "None")

  # --- Alerts / sounds ---
  "AlertVolumeControl": "1",             # per-alert volume control on
  # Engage/disengage chimes muted: current Tesla firmware forces the car's own AP engage/disengage
  # sounds on, so AP1 owners already hear the car and a BogPilot chime on top is redundant.
  "EngageVolume": "0.000000",            # engage chime muted (default 101 = auto)
  "DisengageVolume": "0.000000",         # disengage chime muted (default 101 = auto); warnings unaffected
  "CustomAlerts": "1",                   # FrogPilot custom alerts on
  "GreenLightAlert": "1",                # green light alert
  "LeadDepartingAlert": "1",             # lead departing alert

  # --- System ---
  "PreferredSchedule": "1",              # OSM map update schedule: Weekly (default Monthly)
  # Forced telemetry opt-out: FrogPilot telemetry and stats uploads off (default 1 = on). This is the one
  # data-sharing key the profile sets, and only toward sharing less. Same once-only rule as the rest, so an
  # owner who later opts in keeps that choice.
  "FrogPilotTelemetry": "0",
  "TuningLevel": "3",                    # Developer tuning level (needed for the level-3 toggles above)
  "TuningLevelConfirmed": "1",           # mark the level chosen so the first-open popup keeps Developer
}

# Recommended /data/params_bogpilot file toggles (written only when the file is absent).
AP1_FILE_TOGGLE_PROFILE: dict[str, str] = {
  "DeveloperHUD": "1",                   # Developer HUD on (default off)
}


def _text(value) -> str | None:
  if value is None:
    return None
  if isinstance(value, bytes):
    return value.decode("utf-8", errors="replace")
  return str(value)


def values_equal(a, b) -> bool:
  """Stored-value equality: exact text, or the same number ("5" == "5.000000")."""
  a, b = _text(a), _text(b)
  if a == b:
    return True
  if a is None or b is None:
    return False
  try:
    return abs(float(a) - float(b)) < 1e-6
  except ValueError:
    return False


def _default_table() -> dict[str, str]:
  # Lazy import: frogpilot_variables pulls in the car interfaces.
  from openpilot.frogpilot.common.frogpilot_variables import frogpilot_default_params
  return {key: _text(value) for key, value, _, _ in frogpilot_default_params}


def _persistent_fingerprint(params) -> str | None:
  try:
    raw = params.get("CarParamsPersistent")
    if not raw:
      return None
    from cereal import car
    with car.CarParams.from_bytes(raw) as cp:
      return str(cp.carFingerprint)
  except Exception:
    return None


def _is_metric(params) -> bool:
  try:
    return _text(params.get("IsMetric")) == "1"
  except Exception:
    return False


def speed_offsets_for_units(metric: bool) -> dict[str, str]:
  """Offset1..7 as stored text: mph as-is, or converted to km/h and rounded on a metric device."""
  out = {}
  for key, mph in AP1_SPEED_OFFSETS_MPH.items():
    value = round(mph * MPH_TO_KPH) if metric else mph
    out[key] = f"{value:.6f}"
  return out


def _profile_for_units(params) -> dict[str, str]:
  profile = dict(AP1_PARAM_PROFILE)
  profile.update(speed_offsets_for_units(_is_metric(params)))
  return profile


def _is_converted_default(key, current, table, metric) -> bool:
  """On a metric device the settings UI converted the mph table default to km/h (truncating), e.g. 5 -> 8.

  Treat that converted value as "still default" so a metric owner who never touched an offset gets the profile.
  """
  if not metric or key not in AP1_SPEED_OFFSETS_MPH:
    return False
  try:
    default_mph = float(table[key])
    cur = float(_text(current))
  except (TypeError, ValueError, KeyError):
    return False
  return cur in (int(default_mph * MPH_TO_KPH), round(default_mph * MPH_TO_KPH))


def is_ap1(params) -> bool:
  try:
    if _text(params.get("CarModel")) == AP1_CAR_MODEL:
      return True
  except Exception:
    pass
  return _persistent_fingerprint(params) == AP1_CAR_MODEL


def marker_path(bogpilot_dir: Path | None = None) -> Path:
  return Path(bogpilot_dir if bogpilot_dir is not None else BOGPILOT_DIR) / MARKER_NAME


def theme_marker_path(bogpilot_dir: Path | None = None) -> Path:
  return Path(bogpilot_dir if bogpilot_dir is not None else BOGPILOT_DIR) / THEME_MARKER_NAME


def clear_ap1_defaults_marker(bogpilot_dir: Path | None = None) -> None:
  """Called on DoToggleReset so the next run re-applies the AP1 profile."""
  directory = Path(bogpilot_dir if bogpilot_dir is not None else BOGPILOT_DIR)
  for path in (marker_path(bogpilot_dir), theme_marker_path(bogpilot_dir), *(directory / n for n in OLD_THEME_MARKER_NAMES)):
    try:
      path.unlink(missing_ok=True)
    except Exception:
      pass


def _apply_theme_once(params, directory: Path) -> list[str]:
  """Move theme values that only pick the stock look to BogPilot, once per device."""
  marker = theme_marker_path(directory)
  if marker.exists():
    return []
  written = []
  for key, stock_values in STOCK_LOOK_THEME_VALUES.items():
    current = _text(params.get(key))
    if current is not None and current.strip().lower() in stock_values:
      params.put(key, BOGPILOT_THEME)
      written.append(key)
  directory.mkdir(parents=True, exist_ok=True)
  marker.write_text("1\n")
  return written


def apply_ap1_defaults_once(params=None, bogpilot_dir: Path | None = None, default_table: dict | None = None,
                            refresh: bool = True) -> list[str]:
  """Apply the AP1 profile once. Returns the keys written ([] when it did nothing).

  Never raises: a problem here must not block boot or the toggle loop.
  """
  try:
    if params is None:
      from openpilot.common.params import Params
      params = Params()
    directory = Path(bogpilot_dir if bogpilot_dir is not None else BOGPILOT_DIR)
    marker = marker_path(directory)

    if not is_ap1(params) or (marker.exists() and theme_marker_path(directory).exists()):
      return []

    written = []
    if not marker.exists():
      written += _apply_profile(params, directory, default_table)
      marker.write_text("1\n")
    written += [key for key in _apply_theme_once(params, directory) if key not in written]

    if written and refresh:
      try:
        from openpilot.frogpilot.common.frogpilot_variables import update_frogpilot_toggles
        update_frogpilot_toggles()
      except Exception:
        pass
    return written
  except Exception:
    return []


def _apply_profile(params, directory: Path, default_table: dict | None) -> list[str]:
  table = default_table if default_table is not None else _default_table()
  metric = _is_metric(params)
  written = []

  # Tuning level: only for an owner who never confirmed one, and only as a pair.
  tuning_unset = all(values_equal(params.get(k), table.get(k)) or params.get(k) is None for k in TUNING_LEVEL_KEYS)

  for key, value in _profile_for_units(params).items():
    if key not in table or key in NEVER_PROFILE_KEYS:
      continue
    if key in TUNING_LEVEL_KEYS and not tuning_unset:
      continue
    current = params.get(key)
    if current is None or values_equal(current, table[key]) or _is_converted_default(key, current, table, metric):
      params.put(key, value)
      written.append(key)

  directory.mkdir(parents=True, exist_ok=True)
  for key, value in AP1_FILE_TOGGLE_PROFILE.items():
    path = directory / key
    if not path.exists():
      path.write_text(value)
      written.append(key)
  return written
