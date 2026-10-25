"""Pre-AP Model S pedal longitudinal (Comma Pedal gas interceptor). Pure, no CAN I/O.

MIT License. Ported from NotAutopilot (github.com/NotAutopilot/openpilot):
nap-release e99f4dc / opendbc cc39c7f car/tesla/pedal/controller.py
(compute_pedal_command, PedalZeroTorque), preap/carcontroller.py
(PreAPLongController engage grace and gas passthrough), preap/pedal_feedback.py,
preap/teslacan.py (create_pedal_command, create_action_request) and
preap/constants.py, plus nap-ap1 da5a320. The pedal protocol and the stalk
cancel come from BogGyver / Tinkla openpilot 0d99a1b (pedal firmware
panda/board/pedal/main.c, selfdrive/car/tesla/teslacan.py). Copyright (c)
NotAutopilot contributors, BogGyver, and the openpilot authors.

Used only when pedal_long_bus() (called from interface._get_params_preap) returns a bus: PreAPPedalEnabled,
a finished pedal calibration, the openpilot-long toggle on, and GAS_SENSOR seen
on exactly one of bus 0 / bus 2 during fingerprinting. Otherwise the car stays on
stock cruise and nothing here runs.

Braking is regen only (DI accel pedal below the zero-torque point). There is no
friction brake: NAP's iBooster work (napdbc f1a756d) is a bench scaffold and is
not ported.

CAN IDs (all cited against a DBC):
  0x551 GAS_COMMAND  opendbc/tesla_preap_pedal.dbc BO_ 1361 (Tinkla tesla_can.dbc BO_ 1361)
  0x552 GAS_SENSOR   opendbc/tesla_preap_pedal.dbc BO_ 1362 (Tinkla tesla_can.dbc BO_ 1362)
  0x45  STW_ACTN_RQ  opendbc/tesla_can.dbc BO_ 69
  0x368 DI_state     opendbc/tesla_can.dbc BO_ 872 (DI_cruiseState 12|4@1+, VAL_ 872)
  0x108 DI_torque1   opendbc/tesla_can.dbc BO_ 264 (DI_torqueMotor 16|13@1-)
"""
import math

from openpilot.common.numpy_fast import clip, interp

GAS_COMMAND_ADDR = 0x551
GAS_SENSOR_ADDR = 0x552
STW_ACTN_RQ_ADDR = 0x45

# tesla_preap_pedal.dbc GAS_COMMAND / GAS_SENSOR scaling (Tinkla, NAP PEDAL_M1/M2/D).
PEDAL_M1 = 0.0507968128
PEDAL_M2 = 0.1015936256
PEDAL_D = -22.85856576

# Same threshold as panda safety_tesla_preap.h PREAP_PEDAL_GAS_PRESSED (raw INTERCEPTOR_GAS).
PEDAL_GAS_PRESSED_RAW = 650
PEDAL_STATE_NO_FAULT = 0   # tesla_preap_pedal.dbc VAL_ 1362 STATE
PEDAL_SENSOR_TIMEOUT_S = 0.1    # panda PREAP_PEDAL_SENSOR_TIMEOUT_US
PEDAL_FAULT_DEBOUNCE_S = 0.2    # unhealthy this long -> accFaulted

# Panda max-gas cap (safety_tesla_preap.h PREAP_PEDAL_MAX_GAS, raw GAS_COMMAND vs m/s).
# The host stays PANDA_GAS_MARGIN_RAW under it so a command is never refused.
PANDA_MAX_GAS_BP = [0., 12., 40.]
PANDA_MAX_GAS_RAW = [1631., 1867., 2320.]
PANDA_GAS_MARGIN_RAW = 20

# DI_cruiseState (tesla_can.dbc VAL_ 872): OFF 0, STANDBY 1, FAULT 5 are "stock CC
# not driving". The panda uses the same split (preap_stock_cruise_engaged) and only
# lets a cancel through for ENABLED 2, STANDSTILL 3, OVERRIDE 4.
DI_CRUISE_IDLE = (0, 1, 5)
DI_CRUISE_CANCELABLE = (2, 3, 4)

# tesla_can.dbc VAL_ 69 SpdCtrlLvr_Stat.
LEVER_FWD = 1
LEVER_UP = (16, 4)     # UP_1ST, UP_2ND
LEVER_DOWN = (32, 8)   # DN_1ST, DN_2ND

STW_FRESH_S = 0.25         # panda PREAP_STALK_FRESH_US
CANCEL_PERIOD_FRAMES = 10  # NAP stock_cc_spoofer: at most one cancel per 100 ms

# NAP nap_conf.py / pedal/controller.py (DI = driver-intent percent before calibration).
PEDAL_DI_MIN = -5.
PEDAL_DI_ZERO = 0.
ACCEL_MAX = 2.5
REGEN_MAX = -1.5
PEDAL_BP = [0., 5., 12., 20., 30., 40.]
PEDAL_MAX_VALUES = [50., 58., 66., 74., 82., 90.]
PEDAL_RAMP_RATE_UP = 5.0     # DI per 50 Hz step
PEDAL_RAMP_RATE_DOWN = 2.5
ACCEL_DEADBAND = 0.15
PEDAL_HYST_GAP = 1.0
TORQUE_LEVEL_ACC = 0.0
TORQUE_LEVEL_DECEL = -30.0
MPH_TO_MS = 0.44704
ZERO_TORQUE_MIN_SPEED = 10.0 * MPH_TO_MS
ZERO_TORQUE_LOW_SPEED = 5.0 * MPH_TO_MS
# Not in NAP: bound what the zero-torque learner may adopt.
ZERO_TORQUE_DI_BOUNDS = (-2.0, 10.0)
ENGAGE_GRACE_FRAMES = 50     # NAP preap/carcontroller.py, 0.5 s at 100 Hz

# NAP preap/constants.py
ACCEL_PREAP_BP = [0.0, 1.3, 7.5, 15.0, 25.0, 40.0]
ACCEL_PREAP_PROFILES = {
  0: [0.3, 0.8, 1.1, 1.0, 0.85, 0.7],   # aggressive
  1: [0.3, 0.7, 1.0, 0.9, 0.8, 0.65],   # standard
  2: [0.3, 0.6, 0.9, 0.8, 0.7, 0.55],   # relaxed
}
PEDAL_LONG_K_BP = [0.0, 3.0, 6.0, 35.0]
PEDAL_LONG_KP_V = [0.0, 0.0, 0.0, 0.0]
PEDAL_LONG_KI_V = [0.05, 0.08, 0.10, 0.15]

# BRAKE! alert: regen cannot do more than about REGEN_MAX.
FCW_ACCEL = -2.0
FCW_FRAMES = 25          # 0.25 s at 100 Hz
STOP_ALERT_SPEED = 0.5   # m/s: planner is stopping but regen cannot hold the car
STOP_ALERT_FRAMES = 100


# ---- gate ------------------------------------------------------------------

class PedalCalibration:
  """NAP nap_conf di_to_pedal / pedal_to_di on PreAPPedalCalib* values."""

  def __init__(self, zero, factor, cmin, cmax):
    self.zero = float(zero)
    self.factor = float(factor)
    self.min = float(cmin)
    self.max = float(cmax)

  @property
  def coast(self):
    # NAP pedal_zero: calib_zero - 1 / factor
    return self.zero - 1.0 / self.factor

  def di_to_pedal(self, di):
    return self.coast + (di - PEDAL_DI_ZERO) / self.factor

  def pedal_to_di(self, pedal):
    return (pedal - self.coast) * self.factor + PEDAL_DI_ZERO


def pedal_calibration(settings):
  """PedalCalibration from load_preap_settings() values, or None if not usable.

  Needs PreAPPedalCalibDone and finite zero/factor/min/max with factor > 0 and
  min < zero < max. Like NAP's pedal_calibrated, a done flag whose zero and
  factor are still the NAP defaults (0.0, 1.0) is not trusted.
  """
  if not settings or not settings.get("pedal_calib_done"):
    return None
  vals = [settings.get(k) for k in ("pedal_calib_zero", "pedal_calib_factor", "pedal_calib_min", "pedal_calib_max")]
  if any(v is None for v in vals):
    return None
  try:
    zero, factor, cmin, cmax = (float(v) for v in vals)
  except (TypeError, ValueError):
    return None
  if not all(math.isfinite(v) for v in (zero, factor, cmin, cmax)):
    return None
  if factor <= 0 or not (cmin < zero < cmax):
    return None
  if zero == 0.0 and factor == 1.0:
    return None
  return PedalCalibration(zero, factor, cmin, cmax)


def pedal_long_bus(settings, learned_bus, long_toggle_on):
  """Bus (0 or 2) for openpilot pedal longitudinal, or None for stock cruise.

  All of: PreAPPedalEnabled, a usable calibration, the openpilot-long toggle on,
  and GAS_SENSOR seen on exactly one bus during fingerprinting (learned_bus).
  """
  if not settings or not settings.get("pedal_enabled") or not long_toggle_on:
    return None
  if learned_bus not in (0, 2):
    return None
  if pedal_calibration(settings) is None:
    return None
  return learned_bus


def preap_pedal_bus_from_cp(CP):
  """Pedal bus (0/2) when the installed Pre-AP safety param has ENABLE_PEDAL, else None."""
  from panda import Panda
  from openpilot.selfdrive.car.tesla.values import CAR
  configs = list(getattr(CP, "safetyConfigs", []) or [])
  if getattr(CP, "carFingerprint", None) != CAR.TESLA_PREAP_MODELS or not configs:
    return None
  param = int(configs[0].safetyParam)
  if not (param & Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL):
    return None
  return 0 if param & Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO else 2


def accel_profile(personality, adaptive):
  """NAP get_preap_accel_limits envelope. PreAPAdaptiveAccel follows
  LongitudinalPersonality (NAP); off pins the standard profile. NAP's planner
  follow cap (ACCEL_PREAP_FOLLOW) is planner-side and not ported."""
  if not adaptive:
    return ACCEL_PREAP_PROFILES[1]
  try:
    return ACCEL_PREAP_PROFILES.get(int(personality), ACCEL_PREAP_PROFILES[1])
  except (TypeError, ValueError):
    return ACCEL_PREAP_PROFILES[1]


def accel_max(v_ego, personality=1, adaptive=False):
  return float(interp(v_ego, ACCEL_PREAP_BP, accel_profile(personality, adaptive)))


# ---- frames ----------------------------------------------------------------

def pedal_checksum(addr, dat):
  """Additive pedal checksum (Tinkla pedal firmware, NAP teslacan.pedal_checksum):
  address bytes plus data bytes 0..4."""
  return ((addr & 0xFF) + ((addr >> 8) & 0xFF) + sum(dat[:5])) & 0xFF


def max_gas_raw(v_ego):
  return int(interp(v_ego, PANDA_MAX_GAS_BP, PANDA_MAX_GAS_RAW)) - PANDA_GAS_MARGIN_RAW


def pedal_raw(pedal_phys, v_ego, calib_max):
  """(raw GAS_COMMAND, raw GAS_COMMAND2) for a physical pedal value, clamped to the
  calibration max and under the panda speed cap. GAS_COMMAND2 = GAS_COMMAND // 2
  (M2 = 2 * M1), which the panda checks within 4 counts."""
  phys = min(float(pedal_phys), float(calib_max))
  raw1 = int((phys - PEDAL_D) / PEDAL_M1)
  raw1 = int(clip(raw1, 0, max_gas_raw(v_ego)))
  return raw1, raw1 // 2


def create_gas_command(raw1, raw2, enable, idx, bus):
  """0x551 GAS_COMMAND, packed as in NAP create_pedal_command (BE 16-bit values,
  byte 4 = ENABLE << 7 | IDX, byte 5 checksum). ENABLE=0 always sends zeros: the
  firmware faults (FAULT_INVALID) on a disabled command with nonzero gas."""
  if not enable:
    raw1 = raw2 = 0
  raw1 = int(clip(raw1, 0, 0xFFFF))
  raw2 = int(clip(raw2, 0, 0xFFFF))
  dat = bytes(((raw1 >> 8) & 0xFF, raw1 & 0xFF, (raw2 >> 8) & 0xFF, raw2 & 0xFF,
               ((1 if enable else 0) << 7) | (idx & 0x0F)))
  return [GAS_COMMAND_ADDR, 0, dat + bytes((pedal_checksum(GAS_COMMAND_ADDR, dat),)), bus]


def crc8_stw(dat):
  """CRC8 poly 0x1D, init 0xFF, xorout 0xFF (teslacan crcmod 0x11d initCrc 0
  xorOut 0xff, panda preap_compute_crc8)."""
  crc = 0xFF
  for b in dat:
    crc ^= b
    for _ in range(8):
      crc = ((crc << 1) ^ 0x1D) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
  return crc ^ 0xFF


def _sig(msg, name):
  return int(msg.get(name, 0) or 0)


def create_stalk_cancel(msg_stw, bus=0):
  """0x45 STW_ACTN_RQ cancel (SpdCtrlLvr_Stat FWD) copied from the live frame.

  Every tesla_can.dbc BO_ 69 signal is copied (unlike NAP, which zeroes
  StW_Sw07..15), except SpdCtrlLvr_Stat = 1, SpdCtrlLvrStat_Inv = 0,
  MC_STW_ACTN_RQ = live + 1 and a new CRC. This is what the panda checks
  (preap_stalk_cancel_allowed). Bit 51 is undefined in the DBC and sent as 0;
  if the real frame sets it the panda refuses the copy.
  """
  m = msg_stw
  b0 = (LEVER_FWD & 0x3F) | ((_sig(m, "VSL_Enbl_Rq") & 1) << 6)
  b1 = _sig(m, "DTR_Dist_Rq") & 0xFF
  b2 = ((_sig(m, "TurnIndLvr_Stat") & 3) | ((_sig(m, "HiBmLvr_Stat") & 3) << 2) |
        ((_sig(m, "WprWashSw_Psd") & 3) << 4) | ((_sig(m, "WprWash_R_Sw_Posn_V2") & 3) << 6))
  b3 = ((_sig(m, "StW_Lvr_Stat") & 7) | ((_sig(m, "StW_Cond_Flt") & 1) << 3) |
        ((_sig(m, "StW_Cond_Psd") & 3) << 4) | ((_sig(m, "HrnSw_Psd") & 3) << 6))
  b4 = sum((_sig(m, f"StW_Sw{i:02d}_Psd") & 1) << i for i in range(8))
  b5 = sum((_sig(m, f"StW_Sw{i + 8:02d}_Psd") & 1) << i for i in range(8))
  counter = (_sig(m, "MC_STW_ACTN_RQ") + 1) & 0x0F
  b6 = (_sig(m, "WprSw6Posn") & 7) | (counter << 4)
  dat = bytes((b0, b1, b2, b3, b4, b5, b6))
  return [STW_ACTN_RQ_ADDR, 0, dat + bytes((crc8_stw(dat),)), bus]


# ---- sensor ----------------------------------------------------------------

class PedalSensor:
  """GAS_SENSOR (0x552) health, from parsed tesla_preap_pedal.dbc values.

  Healthy = valid additive checksum, STATE NO_FAULT, IDX advanced within
  PEDAL_SENSOR_TIMEOUT_S. Same rules the panda applies before it lets ENABLE=1 out.
  """

  def __init__(self):
    self.t = 0.0
    self.raw1 = 0
    self.raw2 = 0
    self.state = 4
    self.idx = None
    self.idx_t = None
    self.checksum_ok = False
    self.unhealthy_t = 0.0

  @staticmethod
  def raw_from_phys(value, m):
    return int(round((float(value) - PEDAL_D) / m))

  def update(self, vals, dt):
    self.t += dt
    self.raw1 = int(clip(self.raw_from_phys(vals.get("INTERCEPTOR_GAS", 0.0), PEDAL_M1), 0, 0xFFFF))
    self.raw2 = int(clip(self.raw_from_phys(vals.get("INTERCEPTOR_GAS2", 0.0), PEDAL_M2), 0, 0xFFFF))
    self.state = int(vals.get("STATE", 4))
    idx = int(vals.get("IDX", 0)) & 0x0F
    dat = bytes(((self.raw1 >> 8) & 0xFF, self.raw1 & 0xFF, (self.raw2 >> 8) & 0xFF, self.raw2 & 0xFF,
                 ((self.state & 0x0F) << 4) | idx))
    self.checksum_ok = pedal_checksum(GAS_SENSOR_ADDR, dat) == (int(vals.get("CHECKSUM", -1)) & 0xFF)
    if self.checksum_ok and idx != self.idx:
      if self.idx is not None:
        self.idx_t = self.t
      self.idx = idx
    self.unhealthy_t = 0.0 if self.healthy else self.unhealthy_t + dt
    return self

  @property
  def fresh(self):
    return self.idx_t is not None and (self.t - self.idx_t) <= PEDAL_SENSOR_TIMEOUT_S

  @property
  def healthy(self):
    return self.checksum_ok and self.state == PEDAL_STATE_NO_FAULT and self.fresh

  @property
  def faulted(self):
    return self.unhealthy_t >= PEDAL_FAULT_DEBOUNCE_S

  @property
  def gas_pressed(self):
    return self.raw1 > PEDAL_GAS_PRESSED_RAW

  @property
  def phys(self):
    return self.raw1 * PEDAL_M1 + PEDAL_D


class StalkTracker:
  """Freshness of the live STW_ACTN_RQ (counter advanced within STW_FRESH_S)."""

  def __init__(self):
    self.t = 0.0
    self.counter = None
    self.counter_t = None

  def update(self, msg_stw, dt):
    self.t += dt
    c = _sig(msg_stw, "MC_STW_ACTN_RQ") & 0x0F
    if c != self.counter:
      if self.counter is not None:
        self.counter_t = self.t
      self.counter = c
    return self

  @property
  def fresh(self):
    return self.counter_t is not None and (self.t - self.counter_t) <= STW_FRESH_S


def stock_cc_active(di_cruise_state):
  return int(di_cruise_state) not in DI_CRUISE_IDLE


def stock_cc_cancelable(di_cruise_state):
  return int(di_cruise_state) in DI_CRUISE_CANCELABLE


def lever_buttons(lever, lever_prev):
  """[(button, pressed)] edges: UP_* -> accelCruise, DN_* -> decelCruise."""
  def kind(v):
    return "accel" if v in LEVER_UP else ("decel" if v in LEVER_DOWN else None)
  now, prev = kind(int(lever)), kind(int(lever_prev))
  out = []
  if prev is not None and prev != now:
    out.append((prev, False))
  if now is not None and now != prev:
    out.append((now, True))
  return out


# ---- controller ------------------------------------------------------------

class PedalZeroTorque:
  """NAP PedalZeroTorque: learns the pedal DI with ~zero motor torque. Learned
  values are bounded to ZERO_TORQUE_DI_BOUNDS (not in NAP)."""

  def __init__(self):
    self.value = PEDAL_DI_ZERO
    self._best_torque = TORQUE_LEVEL_DECEL

  def update(self, torque_level, current_pedal_di, v_ego):
    if v_ego < ZERO_TORQUE_MIN_SPEED:
      return
    lo, hi = ZERO_TORQUE_DI_BOUNDS
    if (TORQUE_LEVEL_DECEL < torque_level < TORQUE_LEVEL_ACC and abs(torque_level) < abs(self._best_torque)
        and lo <= current_pedal_di <= hi):
      self.value = float(current_pedal_di)
      self._best_torque = torque_level

  def get(self, v_ego):
    return PEDAL_DI_ZERO if v_ego < ZERO_TORQUE_LOW_SPEED else self.value


def compute_pedal_di(accel_request, v_ego, prev_pedal_di, zero_torque_di):
  """NAP compute_pedal_command in DI units (calibration applied by the caller)."""
  max_pedal_value = float(interp(v_ego, PEDAL_BP, PEDAL_MAX_VALUES))
  if abs(accel_request) < ACCEL_DEADBAND:
    accel_request = 0.0
  pedal_di = float(interp(accel_request, [REGEN_MAX, 0.0, ACCEL_MAX], [PEDAL_DI_MIN, zero_torque_di, max_pedal_value]))
  pedal_di = float(clip(pedal_di, PEDAL_DI_MIN, max_pedal_value))
  pedal_di = float(clip(pedal_di, prev_pedal_di - PEDAL_RAMP_RATE_DOWN, prev_pedal_di + PEDAL_RAMP_RATE_UP))
  if abs(pedal_di - prev_pedal_di) < PEDAL_HYST_GAP:
    pedal_di = prev_pedal_di
  return pedal_di


class PedalLongController:
  """Sends 0x551 every 2 frames and the 0x45 cancel when needed.

  ENABLE=1 only when openpilot is enabled with longActive, the sensor is healthy,
  the driver is off the gas and stock cruise is not driving. Anything else is
  ENABLE=0 with zero gas, which hands the pedal back to the driver's foot.
  """

  def __init__(self, calib, bus, personality=1, adaptive=False):
    self.calib = calib
    self.bus = bus
    self.personality = personality
    self.adaptive = adaptive
    self.idx = 0
    self.prev_di = 0.0
    self.zero_torque = PedalZeroTorque()
    self.prev_long = False
    self.prev_enable = False
    self.engage_frame = -10 ** 9
    self.engage_a_max = 0.0
    self.last_cancel_frame = -10 ** 9
    self.last_cancel_counter = None
    self.enable = False

  def _gas(self, raw1, raw2, enable):
    msg = create_gas_command(raw1, raw2, enable, self.idx, self.bus)
    self.idx = (self.idx + 1) % 16
    return msg

  def update(self, CC, CS, frame):
    sends = []
    sensor = CS.pedal_sensor
    v_ego = float(CS.out.vEgo)
    long_req = bool(CC.enabled) and bool(CC.longActive)
    if long_req and not self.prev_long:
      self.engage_frame = frame
      self.engage_a_max = accel_max(v_ego, self.personality, self.adaptive)
    self.prev_long = long_req

    if frame % 2 == 0:
      enable = (long_req and sensor.healthy and not sensor.gas_pressed and not CS.stock_cc_active)
      interceptor_di = self.calib.pedal_to_di(sensor.phys)
      self.zero_torque.update(CS.motor_torque, self.prev_di, v_ego)
      if enable:
        if not self.prev_enable:
          self.prev_di = max(interceptor_di, self.zero_torque.get(v_ego))
        accel = float(CC.actuators.accel)
        elapsed = frame - self.engage_frame
        if elapsed < ENGAGE_GRACE_FRAMES:
          accel = max(0.0, min(accel, elapsed / ENGAGE_GRACE_FRAMES * self.engage_a_max))
        self.prev_di = compute_pedal_di(accel, v_ego, self.prev_di, self.zero_torque.get(v_ego))
        raw1, raw2 = pedal_raw(self.calib.di_to_pedal(self.prev_di), v_ego, self.calib.max)
        sends.append(self._gas(raw1, raw2, True))
      else:
        self.prev_di = max(interceptor_di, PEDAL_DI_ZERO) if sensor.gas_pressed else 0.0
        sends.append(self._gas(0, 0, False))
      self.enable = enable
      self.prev_enable = enable

    # Stock CC cancel: only while openpilot is enabled (controlsd sets
    # cruiseControl.cancel whenever stock CC is on and pcmCruise is False).
    stw = CS.msg_stw_actn_req or {}
    counter = _sig(stw, "MC_STW_ACTN_RQ") & 0x0F
    if (CC.enabled and CC.cruiseControl.cancel and CS.stock_cc_cancelable and CS.stalk_fresh and
        frame - self.last_cancel_frame >= CANCEL_PERIOD_FRAMES and counter != self.last_cancel_counter):
      sends.append(create_stalk_cancel(stw, 0))
      self.last_cancel_frame = frame
      self.last_cancel_counter = counter
    return sends


class BrakeAlert:
  """BRAKE! (fcw) when regen cannot do what the plan asks: accel below FCW_ACCEL,
  or the planner stopping above STOP_ALERT_SPEED, for a while, with long active."""

  def __init__(self):
    self.hard_frames = 0
    self.stop_frames = 0

  def update(self, long_active, accel, stopping, v_ego):
    hard = long_active and accel < FCW_ACCEL
    stop = long_active and stopping and v_ego > STOP_ALERT_SPEED
    self.hard_frames = self.hard_frames + 1 if hard else 0
    self.stop_frames = self.stop_frames + 1 if stop else 0
    return self.hard_frames >= FCW_FRAMES or self.stop_frames >= STOP_ALERT_FRAMES


def pedal_engage_events(latch_now, latch_prev):
  """'buttonEnable' on the latch rising edge, 'buttonCancel' on the falling edge."""
  if latch_now and not latch_prev:
    return ["buttonEnable"]
  if latch_prev and not latch_now:
    return ["buttonCancel"]
  return []


class AccelLimitSource:
  """PreAPAdaptiveAccel (read once per process) and LongitudinalPersonality
  (re-read about once a second) for get_pid_accel_limits. Any read error falls
  back to the standard profile."""

  def __init__(self, refresh_s=1.0):
    self.refresh_s = refresh_s
    self.adaptive = None
    self.personality = 1
    self._t = None

  def _load_adaptive(self):
    try:
      from openpilot.selfdrive.car.tesla.preap_settings import load_preap_settings
      settings, _ = load_preap_settings()
      return bool(settings.get("adaptive_accel"))
    except Exception:
      return False

  def _read_personality(self):
    try:
      from openpilot.common.params import Params
      return int(Params().get("LongitudinalPersonality") or 1)
    except Exception:
      return 1

  def accel_max(self, v_ego, now):
    if self.adaptive is None:
      self.adaptive = self._load_adaptive()
    if self.adaptive and (self._t is None or now - self._t >= self.refresh_s):
      self.personality = self._read_personality()
      self._t = now
    return accel_max(v_ego, self.personality, self.adaptive)


ACCEL_LIMITS = AccelLimitSource()
