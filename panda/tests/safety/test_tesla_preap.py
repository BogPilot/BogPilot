#!/usr/bin/env python3
"""Tesla Pre-AP safety (SAFETY_TESLA_PREAP = 37), BogPilot port of NotAutopilot
opendbc safety/modes/tesla_preap.h (MIT, cc39c7f / 21a45cf; Tinkla lineage).

Runs against tests/libpanda/libpanda.so (x86 build of board/safety). Covers the
mode registration, the narrowed TX whitelist, stalk-pull engagement and every
disengage path, angle limits, always-on lateral neutralization, the pedal and
radar flags, and the GTW radar emulation output on bus 1.

Not a driving validation.
"""
import unittest

from panda import Panda
from panda.tests.libpanda import libpanda_py

l = libpanda_py.libpanda
ALT_EXP_ALWAYS_ON_LATERAL = 32
ALT_EXP_DISABLE_DISENGAGE_ON_GAS = 1

LEVER_IDLE, LEVER_CANCEL, LEVER_PULL = 0, 1, 2
GEAR_P, GEAR_D = 1, 4


def pkt(addr, dat, bus=0):
  return libpanda_py.make_CANPacket(addr, bus, bytes(dat))


def epas_status(angle_deg=0.0, hands=0, eac_status=1, err=0):
  raw = int(round(angle_deg * 10)) + 8192
  dat = bytearray(8)
  dat[2] = (err & 0x0F) << 4
  dat[4] = ((hands & 0x03) << 6) | ((raw >> 8) & 0x3F)
  dat[5] = raw & 0xFF
  dat[6] = (eac_status & 0x07) << 5
  return pkt(0x370, dat)


def stalk(lever):
  dat = bytearray(8)
  dat[0] = lever & 0x3F
  return pkt(0x45, dat)


def di_torque2(gear=GEAR_D, brake=False):
  dat = bytearray(6)
  dat[1] = ((1 if brake else 0) << 7) | ((gear & 0x07) << 4)
  return pkt(0x118, dat)


def brake_message(applied):
  dat = bytearray(8)
  dat[0] = (2 if applied else 1) << 2
  return pkt(0x20A, dat)


def doors(front_left_open=False):
  dat = bytearray(8)
  dat[1] = (1 if front_left_open else 2) << 4
  return pkt(0x318, dat)


def esp_b(speed_kph):
  raw = int(round(speed_kph * 100))
  dat = bytearray(8)
  dat[5] = (raw >> 8) & 0xFF
  dat[6] = raw & 0xFF
  return pkt(0x155, dat)


def di_torque1(gas):
  dat = bytearray(8)
  dat[6] = gas
  return pkt(0x108, dat)


def steer(angle_deg, enabled, bus=0):
  raw = int(round(angle_deg * 10)) + 16384
  dat = bytearray(4)
  dat[0] = (raw >> 8) & 0x7F
  dat[1] = raw & 0xFF
  dat[2] = (1 if enabled else 0) << 6
  return pkt(0x488, dat, bus)


def epas_control(mode):
  dat = bytearray(3)
  dat[0] = mode & 0x07
  return pkt(0x214, dat)


def pedal_checksum(addr, dat):
  # Comma Pedal: address bytes + data bytes 0-4 (Tinkla pedal/main.c, NAP teslacan.py)
  return ((addr & 0xFF) + ((addr >> 8) & 0xFF) + sum(dat[:5])) & 0xFF


def pedal_cmd(raw_gas, enable, bus=2, idx=0, raw2=None, bad_checksum=False):
  # GAS_COMMAND 0x551 (tesla_preap_pedal.dbc BO_ 1361)
  raw2 = (raw_gas // 2) if raw2 is None else raw2
  dat = bytearray(6)
  dat[0], dat[1] = (raw_gas >> 8) & 0xFF, raw_gas & 0xFF
  dat[2], dat[3] = (raw2 >> 8) & 0xFF, raw2 & 0xFF
  dat[4] = (0x80 if enable else 0) | (idx & 0x0F)
  dat[5] = pedal_checksum(0x551, dat) ^ (0xFF if bad_checksum else 0)
  return pkt(0x551, dat, bus)


def gas_sensor(raw_gas, state=0, idx=0, bus=2, bad_checksum=False):
  # GAS_SENSOR 0x552 (BO_ 1362): STATE 39|4 high nibble, IDX 35|4 low nibble of byte 4
  dat = bytearray(6)
  dat[0], dat[1] = (raw_gas >> 8) & 0xFF, raw_gas & 0xFF
  dat[2], dat[3] = ((raw_gas // 2) >> 8) & 0xFF, (raw_gas // 2) & 0xFF
  dat[4] = ((state & 0x0F) << 4) | (idx & 0x0F)
  dat[5] = pedal_checksum(0x552, dat) ^ (0xFF if bad_checksum else 0)
  return pkt(0x552, dat, bus)


def di_state(cruise_state):
  # DI_state 0x368 DI_cruiseState 12|4@1+ (tesla_can.dbc BO_ 872)
  dat = bytearray(8)
  dat[1] = (cruise_state & 0x0F) << 4
  return pkt(0x368, dat)


def crc8_stw(data):
  # CRC-8 poly 0x1D, init 0xFF, xorout 0xFF (NAP crcmod 0x11d initCrc 0 xorOut 0xff)
  crc = 0xFF
  for b in data:
    crc ^= b
    for _ in range(8):
      crc = ((crc << 1) ^ 0x1D) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
  return crc ^ 0xFF


def stalk_frame(lever, rest=(0x00, 0x00, 0x00, 0x00, 0x00), wiper=0, counter=0, vsl=1, inv=0):
  # STW_ACTN_RQ 0x45 (tesla_can.dbc BO_ 69) with a valid CRC
  dat = bytearray(8)
  dat[0] = (lever & 0x3F) | (vsl << 6) | (inv << 7)
  dat[1:6] = bytes(rest)
  dat[6] = ((counter & 0x0F) << 4) | (wiper & 0x0F)
  dat[7] = crc8_stw(dat[:7])
  return dat


def radar_cfg(rec, use_radar=True, position=0, epas_type=0, vin=" " * 17):
  # Layout: NAP preap/teslacan.py create_radar_vin_msg
  v = vin.ljust(17)[:17]
  if rec == 0:
    flags = (1 if use_radar else 0) | ((position & 3) << 1) | ((epas_type & 7) << 3)
    dat = bytes((0, 1, flags, 0, 0, ord(v[0]), ord(v[1]), ord(v[2])))
  elif rec == 1:
    dat = bytes((1, *(ord(c) for c in v[3:10])))
  else:
    dat = bytes((2, *(ord(c) for c in v[10:17])))
  return pkt(0x560, dat)


DLC_TO_LEN = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]


def drain(q):
  out = []
  p = libpanda_py.ffi.new("CANPacket_t[1]")
  while l.can_pop(q, p):
    out.append((p[0].addr, p[0].bus, bytes(p[0].data[0:DLC_TO_LEN[p[0].data_len_code]])))
  return out


class TestTeslaPreapSafety(unittest.TestCase):
  PARAM = 0

  def setUp(self):
    l.set_alternative_experience(0)
    self.assertEqual(l.set_safety_hooks(Panda.SAFETY_TESLA_PREAP, self.PARAM), 0)
    l.init_tests()
    l.set_timer(0)
    drain(l.tx2_q)
    self._rx(di_torque2(GEAR_D))
    self._rx(doors(False))
    self._rx(epas_status(0.0))

  def _rx(self, p):
    return l.safety_rx_hook(p)

  def _tx(self, p):
    return l.safety_tx_hook(p)

  def _engage(self):
    self._rx(stalk(LEVER_IDLE))
    self._rx(stalk(LEVER_PULL))
    self._rx(stalk(LEVER_IDLE))

  # --- registration ---
  def test_mode_registered(self):
    self.assertEqual(l.get_current_safety_mode(), Panda.SAFETY_TESLA_PREAP)
    self.assertEqual(Panda.SAFETY_TESLA_PREAP, 37)

  def test_fwd_blocks_everything(self):
    for bus in range(3):
      for addr in (0x45, 0x370, 0x488, 0x2B9, 0x3E9):
        self.assertEqual(l.safety_fwd_hook(bus, addr), -1)

  # --- engagement ---
  def test_pull_edge_engages_in_drive(self):
    self.assertFalse(l.get_controls_allowed())
    self._engage()
    self.assertTrue(l.get_controls_allowed())

  def test_held_pull_needs_new_edge(self):
    self._rx(stalk(LEVER_PULL))
    self.assertTrue(l.get_controls_allowed())
    self._rx(di_torque2(GEAR_P))
    self._rx(di_torque2(GEAR_D))
    self._rx(stalk(LEVER_PULL))  # still held: no new edge
    self.assertFalse(l.get_controls_allowed())

  def test_no_engage_outside_drive_or_doors_or_brake(self):
    self._rx(di_torque2(GEAR_P))
    self._engage()
    self.assertFalse(l.get_controls_allowed())
    self._rx(di_torque2(GEAR_D))
    self._rx(doors(True))
    self._engage()
    self.assertFalse(l.get_controls_allowed())
    self._rx(doors(False))
    self._rx(brake_message(True))
    self._engage()
    self.assertFalse(l.get_controls_allowed())

  def test_gear_and_door_disengage(self):
    self._engage()
    self._rx(di_torque2(GEAR_P))
    self.assertFalse(l.get_controls_allowed())
    self._rx(di_torque2(GEAR_D))
    self._engage()
    self._rx(doors(True))
    self.assertFalse(l.get_controls_allowed())

  def test_hands_on_and_epas_reject_disengage(self):
    self._engage()
    self._rx(epas_status(0.0, hands=1))
    self.assertTrue(l.get_controls_allowed())
    self._rx(epas_status(0.0, hands=2))
    self.assertFalse(l.get_controls_allowed())
    self._rx(epas_status(0.0, hands=0))
    self._engage()
    self.assertTrue(l.get_controls_allowed())
    for err in (6, 7, 8, 9):
      self._rx(epas_status(0.0, eac_status=0, err=err))
      self.assertFalse(l.get_controls_allowed(), err)
      self._rx(epas_status(0.0))
      self._engage()

  def test_no_engage_while_hands_on(self):
    self._rx(epas_status(0.0, hands=3))
    self._engage()
    self.assertFalse(l.get_controls_allowed())

  def test_brake_disengages(self):
    for msg_on, msg_off in ((brake_message(True), brake_message(False)),
                            (di_torque2(GEAR_D, brake=True), di_torque2(GEAR_D, brake=False))):
      self._engage()
      self.assertTrue(l.get_controls_allowed())
      self._rx(msg_on)
      self.assertFalse(l.get_controls_allowed())
      self._rx(msg_off)
      self._rx(esp_b(0))

  def test_gas_disengages_unless_alt_exp(self):
    self._engage()
    self._rx(di_torque1(10))
    self.assertFalse(l.get_controls_allowed())
    self._rx(di_torque1(0))
    l.set_alternative_experience(ALT_EXP_DISABLE_DISENGAGE_ON_GAS)
    self._engage()
    self._rx(di_torque1(10))
    self.assertTrue(l.get_controls_allowed())

  def test_cancel_echo_window(self):
    l.set_timer(1_000_000)
    self._engage()
    l.set_timer(1_300_000)  # 300 ms after the pull: treated as echo
    self._rx(stalk(LEVER_CANCEL))
    self.assertTrue(l.get_controls_allowed())
    l.set_timer(1_700_000)
    self._rx(stalk(LEVER_CANCEL))
    self.assertFalse(l.get_controls_allowed())

  def test_vehicle_speed_and_moving(self):
    self._rx(esp_b(0.3))
    self.assertFalse(l.get_vehicle_moving())
    self._rx(esp_b(10))
    self.assertTrue(l.get_vehicle_moving())

  # --- always-on lateral ---
  def test_always_on_lateral_neutralized(self):
    l.set_alternative_experience(ALT_EXP_ALWAYS_ON_LATERAL)
    self._rx(epas_status(0.0))
    self.assertFalse(l.get_acc_main_on())
    self.assertFalse(self._tx(steer(0.5, True)))
    self._engage()
    self.assertTrue(l.get_acc_main_on())
    self._rx(di_torque2(GEAR_P))
    self._rx(epas_status(0.0))
    self.assertFalse(l.get_acc_main_on())
    self.assertFalse(self._tx(steer(0.5, True)))

  # --- steering TX ---
  def test_steer_requires_controls(self):
    self.assertFalse(self._tx(steer(0.5, True)))
    self._engage()
    self.assertTrue(self._tx(steer(0.5, True)))

  def test_inactive_frame_must_track_measured_angle(self):
    self._rx(epas_status(10.0))
    self.assertTrue(self._tx(steer(10.0, False)))
    self.assertFalse(self._tx(steer(20.0, False)))

  def test_angle_rate_limit_at_standstill(self):
    self._engage()
    l.set_desired_angle_last(0)
    # standstill: 10 deg/frame up (+0.1 deg fudge)
    self.assertTrue(self._tx(steer(10.0, True)))
    self.assertFalse(self._tx(steer(25.0, True)))

  def test_max_angle_clamp(self):
    self._engage()
    l.set_desired_angle_last(3600)
    self._rx(epas_status(360.0))
    self.assertTrue(self._tx(steer(360.0, True)))
    self.assertFalse(self._tx(steer(361.0, True)))

  def test_bad_control_type_blocked(self):
    self._engage()
    p = steer(0.0, True)
    p[0].data[2] = 2 << 6
    self.assertFalse(self._tx(p))

  def test_steer_wrong_bus_blocked(self):
    self._engage()
    self.assertFalse(self._tx(steer(0.0, True, bus=2)))

  def test_epas_control_mode(self):
    self.assertTrue(self._tx(epas_control(0)))
    self.assertTrue(self._tx(epas_control(1)))
    for mode in range(2, 8):
      self.assertFalse(self._tx(epas_control(mode)))

  def test_dropped_nap_messages_blocked(self):
    self._engage()
    for addr, ln in ((0x2B9, 8), (0x45, 8), (0x3E9, 8), (0x641, 8)):
      for bus in (0, 1, 2):
        self.assertFalse(self._tx(pkt(addr, bytes(ln), bus)), hex(addr))

  # --- pedal ---
  def test_pedal_blocked_without_flag(self):
    self._engage()
    for bus in (0, 2):
      self.assertFalse(self._tx(pedal_cmd(0, False, bus)))
      self.assertFalse(self._tx(pedal_cmd(800, True, bus)))

  # --- radar ---
  def test_radar_config_consumed(self):
    for rec in range(3):
      self.assertFalse(self._tx(radar_cfg(rec)))

  def test_no_radar_output_without_flag(self):
    for rec in range(3):
      self._tx(radar_cfg(rec))
    self._rx(pkt(0x108, bytes(8)))
    self.assertEqual(drain(l.tx2_q), [])


class TestTeslaPreapRadar(TestTeslaPreapSafety):
  PARAM = Panda.FLAG_TESLA_PREAP_RADAR_EMULATION

  def test_no_radar_output_without_flag(self):
    pass  # flag is set in this class; covered by the base class

  def _send_cfg(self, **kw):
    for rec in range(3):
      self._tx(radar_cfg(rec, **kw))

  def test_no_output_until_config_complete(self):
    self._tx(radar_cfg(0))
    self._tx(radar_cfg(1))
    self._rx(pkt(0x108, bytes(8)))
    self.assertEqual(drain(l.tx2_q), [])
    self._tx(radar_cfg(2))
    self._rx(pkt(0x108, bytes(8)))
    out = drain(l.tx2_q)
    self.assertEqual([(a, b) for a, b, _ in out], [(0x109, 1)])

  def test_use_radar_false_stays_silent(self):
    self._send_cfg(use_radar=False)
    self._rx(pkt(0x108, bytes(8)))
    self.assertEqual(drain(l.tx2_q), [])

  def test_readdress_table(self):
    self._send_cfg()
    table = {0x45: 0x219, 0x108: 0x109, 0x145: 0x149, 0x20A: 0x159, 0x308: 0x209, 0x30A: 0x2D9}
    for src, dst in table.items():
      data = bytes(range(1, 9))
      self._rx(pkt(src, data))
      out = drain(l.tx2_q)
      self.assertEqual(out, [(dst, 1, data)], hex(src))

  def test_bus1_input_not_emulated(self):
    self._send_cfg()
    self._rx(pkt(0x108, bytes(8), bus=1))
    self.assertEqual(drain(l.tx2_q), [])

  def test_car_config_patch(self):
    self._send_cfg(position=1, epas_type=3)
    self._rx(pkt(0x398, bytes(8)))
    (addr, bus, data), = drain(l.tx2_q)
    self.assertEqual((addr, bus), (0x2A9, 1))
    lo = int.from_bytes(data[0:4], "little")
    hi = int.from_bytes(data[4:8], "little")
    self.assertEqual(lo, 0x100 | 0x440)
    self.assertEqual(hi, 0x10000000 | (1 << 4) | (3 << 12))

  def test_vin_feed_passthrough_without_donor(self):
    self._send_cfg()
    data = bytes((0x11, 1, 2, 3, 4, 5, 6, 7))
    self._rx(pkt(0x405, data))
    self.assertEqual(drain(l.tx2_q), [(0x2B9, 1, data)])

  def test_vin_feed_donor_substitution(self):
    donor = "ABCDEFG2IJKLMNOPQ"  # synthetic, not a real VIN
    self._send_cfg(vin=donor)
    self._rx(pkt(0x405, bytes((0x10, 9, 9, 9, 9, 9, 9, 9))))
    (_, _, d0), = drain(l.tx2_q)
    self.assertEqual(d0, bytes((0x10, 0, 0, 0, 0)) + donor[0:3].encode())
    self._rx(pkt(0x405, bytes((0x11, 9, 9, 9, 9, 9, 9, 9))))
    (_, _, d1), = drain(l.tx2_q)
    self.assertEqual(d1, bytes((0x11,)) + donor[3:10].encode())
    self._rx(pkt(0x405, bytes((0x12, 9, 9, 9, 9, 9, 9, 9))))
    (_, _, d2), = drain(l.tx2_q)
    self.assertEqual(d2, bytes((0x12,)) + donor[10:17].encode())
    # 4WD bit from donor char 8 == '2'
    self._rx(pkt(0x398, bytes(8)))
    (_, _, cfg), = drain(l.tx2_q)
    self.assertEqual(cfg[0] & 0x08, 0x08)

  def test_synthetic_frames(self):
    self._send_cfg()
    self._rx(pkt(0x115, bytes(8)))
    self.assertEqual([a for a, _, _ in drain(l.tx2_q)], [0x129, 0x1A9])
    self._rx(pkt(0x118, bytes(6)))
    self.assertEqual([a for a, _, _ in drain(l.tx2_q)], [0x119, 0x169])
    self._rx(pkt(0x0E, bytes(8)))
    self.assertEqual([a for a, _, _ in drain(l.tx2_q)], [0x199])

  def test_steering_angle_sna_replacement_crc(self):
    self._send_cfg()
    data = bytes((0, 0, 0x3F, 0xFF, 0, 0, 0, 0))
    self._rx(pkt(0x0E, data))
    (_, _, out), = drain(l.tx2_q)
    lo = int.from_bytes(out[0:4], "little")
    self.assertEqual((lo >> 16) & 0xFFFF, 0x0020)
    self.assertEqual(out[4] & 0x0F, 0x04)
    # CRC-8 poly 0x1D, init 0xFF, xorout 0xFF over the first 7 bytes
    crc = 0xFF
    for b in out[:7]:
      crc ^= b
      for _ in range(8):
        crc = ((crc << 1) ^ 0x1D) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    self.assertEqual(out[7], crc ^ 0xFF)


class TestTeslaPreapPedal(unittest.TestCase):
  """Pedal longitudinal (flag 1). Stricter than NAP f1a756d: checksum, counter,
  sensor state and freshness, max gas, stock-cruise interlock, cancel spoof."""
  PARAM = Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL
  BUS = 2

  def setUp(self):
    self._setup(self.PARAM)

  def _setup(self, param):
    l.set_alternative_experience(0)
    self.assertEqual(l.set_safety_hooks(Panda.SAFETY_TESLA_PREAP, param), 0)
    l.init_tests()
    self.t = 0
    l.set_timer(0)
    self.tx_idx = 0
    self.rx_idx = 0
    l.safety_rx_hook(di_torque2(GEAR_D))
    l.safety_rx_hook(doors(False))
    l.safety_rx_hook(epas_status(0.0))
    l.safety_rx_hook(di_state(1))  # stock cruise STANDBY

  def _tick(self, us=10000):
    self.t += us
    l.set_timer(self.t)

  def _sensor(self, raw=0, state=0, bus=None, **kw):
    bus = self.BUS if bus is None else bus
    ok = l.safety_rx_hook(gas_sensor(raw, state, self.rx_idx, bus, **kw))
    self.rx_idx = (self.rx_idx + 1) % 16
    return ok

  def _cmd(self, raw, enable, bus=None, **kw):
    bus = self.BUS if bus is None else bus
    kw.setdefault("idx", self.tx_idx)
    ok = l.safety_tx_hook(pedal_cmd(raw, enable, bus, **kw))
    self.tx_idx = (kw["idx"] + 1) % 16
    return ok

  def _speed(self, kph):
    for _ in range(6):
      l.safety_rx_hook(esp_b(kph))

  def _engaged_healthy(self):
    self._sensor(0)
    l.safety_rx_hook(stalk(LEVER_IDLE))
    l.safety_rx_hook(stalk(LEVER_PULL))
    l.safety_rx_hook(stalk(LEVER_IDLE))
    self.assertTrue(l.get_controls_allowed())

  # --- flag and bus gating ---
  def test_pedal_bus_selection(self):
    self.assertTrue(self._cmd(0, False, bus=2))
    self.assertFalse(self._cmd(0, False, bus=0))
    self._setup(Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL | Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO)
    self.assertTrue(self._cmd(0, False, bus=0))
    self.assertFalse(self._cmd(0, False, bus=2))

  def test_sensor_only_on_configured_bus(self):
    self._engaged_healthy()
    l.safety_rx_hook(gas_sensor(2000, 0, 0, bus=0))  # driver gas on the wrong bus: ignored
    self.assertTrue(l.get_controls_allowed())
    self.assertTrue(self._cmd(1000, True))

  def test_no_pedal_without_flag(self):
    self._setup(0)
    l.safety_rx_hook(stalk(LEVER_PULL))
    self.assertFalse(self._cmd(0, False))
    self.assertFalse(self._cmd(800, True))

  def test_calibration_flag_is_silent(self):
    self._setup(Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL | Panda.FLAG_TESLA_PREAP_PEDAL_CALIBRATION)
    l.safety_rx_hook(stalk(LEVER_PULL))
    self.assertFalse(l.get_controls_allowed())
    self.assertFalse(l.safety_tx_hook(epas_control(1)))
    self.assertFalse(l.safety_tx_hook(steer(0.0, False)))
    self.assertFalse(self._cmd(0, False))

  # --- ENABLE=1 preconditions ---
  def test_enable_needs_controls(self):
    self._sensor(0)
    self.assertFalse(self._cmd(800, True))
    self._engaged_healthy()
    self.assertTrue(self._cmd(800, True))

  def test_enable_blocked_while_braking(self):
    self._engaged_healthy()
    l.safety_rx_hook(brake_message(True))
    self.assertFalse(l.get_controls_allowed())
    self.assertFalse(self._cmd(800, True))
    self.assertTrue(self._cmd(0, False))

  def test_enable_needs_fresh_healthy_sensor(self):
    l.safety_rx_hook(stalk(LEVER_PULL))
    self.assertTrue(l.get_controls_allowed())
    self.assertFalse(self._cmd(800, True))  # never seen
    self._sensor(0, state=4)                # FAULT_STARTUP
    self.assertFalse(self._cmd(800, True))
    self._sensor(0, state=5)                # FAULT_TIMEOUT
    self.assertFalse(self._cmd(800, True))
    self._sensor(0, state=0)
    self.assertTrue(self._cmd(800, True))
    self._tick(99000)
    self.assertTrue(self._cmd(800, True))
    self._tick(2000)                        # > 100 ms since last GAS_SENSOR
    self.assertFalse(self._cmd(800, True))
    self._sensor(0)
    self.assertTrue(self._cmd(800, True))

  def test_sensor_bad_checksum_and_counter(self):
    self._engaged_healthy()
    self.assertFalse(self._sensor(0, bad_checksum=True))
    self.assertFalse(l.get_controls_allowed())
    self.assertFalse(self._cmd(800, True))
    self._setup(self.PARAM)
    self._engaged_healthy()
    for _ in range(6):  # counter stuck: wrong_counters reaches the limit
      l.safety_rx_hook(gas_sensor(0, 0, 3, self.BUS))
    self.assertFalse(l.get_controls_allowed())

  def test_sensor_gas_threshold(self):
    self._engaged_healthy()
    self._sensor(650)  # NAP: <= 650 raw is resting noise
    self.assertTrue(l.get_controls_allowed())
    self.assertTrue(self._cmd(800, True))
    self._sensor(651)
    self.assertFalse(l.get_controls_allowed())
    self.assertFalse(self._cmd(800, True))

  def test_gas_override_without_disengage(self):
    l.set_alternative_experience(ALT_EXP_DISABLE_DISENGAGE_ON_GAS)
    self._engaged_healthy()
    self._sensor(900)
    self.assertTrue(l.get_controls_allowed())
    self.assertFalse(self._cmd(800, True))  # longitudinal not allowed while gas pressed
    self.assertTrue(self._cmd(0, False))
    self._sensor(0)
    self._sensor(0)
    self.assertTrue(self._cmd(800, True))

  def test_di_torque1_ignored_in_pedal_mode(self):
    # DI_pedalPos reflects the pedal output, so it must not count as driver gas
    self._engaged_healthy()
    l.safety_rx_hook(di_torque1(80))
    self.assertTrue(l.get_controls_allowed())

  def test_stock_cruise_interlock(self):
    self._engaged_healthy()
    for cs in (2, 3, 4, 6, 7):
      l.safety_rx_hook(di_state(cs))
      self.assertFalse(self._cmd(800, True), cs)
      self.assertTrue(self._cmd(0, False), cs)
    for cs in (0, 1, 5):
      l.safety_rx_hook(di_state(cs))
      self.assertTrue(self._cmd(800, True), cs)

  def test_stale_di_state_blocks_enable(self):
    self._engaged_healthy()
    self.assertTrue(self._cmd(800, True))
    self._tick(500000)
    self._sensor(0)
    self.assertFalse(self._cmd(800, True))
    l.safety_rx_hook(di_state(1))
    self.assertTrue(self._cmd(800, True))

  # --- command contents ---
  def test_max_gas_cap_vs_speed(self):
    self._engaged_healthy()
    for kph, cap in ((0, 1631), (12 * 3.6, 1867), (40 * 3.6, 2320), (60 * 3.6, 2320)):
      self._speed(kph)
      self._sensor(0)
      self.assertTrue(self._cmd(cap - 1, True), kph)
      self.assertFalse(self._cmd(cap + 2, True), kph)

  def test_cmd2_must_match(self):
    self._engaged_healthy()
    self.assertTrue(self._cmd(1000, True, raw2=500))
    self.assertTrue(self._cmd(1001, True, raw2=500))
    self.assertFalse(self._cmd(1000, True, raw2=520))
    self.assertFalse(self._cmd(1000, True, raw2=0))

  def test_disabled_cmd_must_be_zero(self):
    self.assertTrue(self._cmd(0, False))
    self.assertFalse(self._cmd(1, False, raw2=0))
    self.assertFalse(self._cmd(0, False, raw2=1))
    self.assertFalse(self._cmd(500, False))  # NAP allowed this; the firmware faults on it

  def test_checksum_required(self):
    self._engaged_healthy()
    self.assertFalse(self._cmd(0, False, bad_checksum=True))
    self.assertFalse(self._cmd(800, True, bad_checksum=True))
    self.assertTrue(self._cmd(800, True))

  def test_idx_must_be_sequential(self):
    self._engaged_healthy()
    self.assertTrue(self._cmd(800, True, idx=5))
    self.assertTrue(self._cmd(800, True, idx=6))
    self.assertFalse(self._cmd(800, True, idx=8))   # skipped 7
    self.assertTrue(self._cmd(800, True, idx=9))    # resyncs on the attempted value
    self.assertTrue(self._cmd(800, True, idx=10))
    for i in range(11, 40):
      self.assertTrue(self._cmd(800, True, idx=i % 16))
    self.assertTrue(self._cmd(800, True, idx=8))    # wraps: 7 -> 8
    self.assertFalse(self._cmd(800, True, idx=8))   # repeated IDX

  def test_unused_bits_must_be_zero(self):
    self._engaged_healthy()
    p = pedal_cmd(800, True, self.BUS, idx=0)
    p.data[4] |= 0x10
    p.data[5] = pedal_checksum(0x551, bytes(p.data[0:6]))
    self.assertFalse(l.safety_tx_hook(p))

  # --- stock-cruise cancel spoof (0x45) ---
  def _cancel_setup(self, rest=(0x12, 0x34, 0x00, 0x80, 0x01), wiper=3, counter=7):
    self._engaged_healthy()
    l.safety_rx_hook(di_state(2))  # stock cruise ENABLED
    live = stalk_frame(LEVER_IDLE, rest, wiper, counter)
    l.safety_rx_hook(pkt(0x45, live))
    return rest, wiper, counter

  def test_cancel_spoof_allowed(self):
    rest, wiper, counter = self._cancel_setup()
    self.assertTrue(l.safety_tx_hook(pkt(0x45, stalk_frame(LEVER_CANCEL, rest, wiper, counter + 1))))

  def test_cancel_spoof_once_per_real_frame(self):
    rest, wiper, counter = self._cancel_setup()
    frame = pkt(0x45, stalk_frame(LEVER_CANCEL, rest, wiper, counter + 1))
    self.assertTrue(l.safety_tx_hook(frame))
    self.assertFalse(l.safety_tx_hook(frame))

  def test_cancel_spoof_must_copy_live_frame(self):
    rest, wiper, counter = self._cancel_setup()
    bad = [
      stalk_frame(LEVER_PULL, rest, wiper, counter + 1),            # engage
      stalk_frame(16, rest, wiper, counter + 1),                    # UP_1ST
      stalk_frame(LEVER_IDLE, rest, wiper, counter + 1),
      stalk_frame(LEVER_CANCEL, (0x13, 0x34, 0, 0x80, 1), wiper, counter + 1),  # DTR changed
      stalk_frame(LEVER_CANCEL, (0x12, 0x34, 0x01, 0x80, 1), wiper, counter + 1),  # switch pressed
      stalk_frame(LEVER_CANCEL, rest, 0, counter + 1),              # wiper changed
      stalk_frame(LEVER_CANCEL, rest, wiper, counter),              # counter not advanced
      stalk_frame(LEVER_CANCEL, rest, wiper, counter + 2),
      stalk_frame(LEVER_CANCEL, rest, wiper, counter + 1, vsl=0),
      stalk_frame(LEVER_CANCEL, rest, wiper, counter + 1, inv=1),
    ]
    for dat in bad:
      self.assertFalse(l.safety_tx_hook(pkt(0x45, dat)), dat.hex())
    good = stalk_frame(LEVER_CANCEL, rest, wiper, counter + 1)
    good[7] ^= 0x01
    self.assertFalse(l.safety_tx_hook(pkt(0x45, good)))  # bad CRC
    self.assertFalse(l.safety_tx_hook(pkt(0x45, stalk_frame(LEVER_CANCEL, rest, wiper, counter + 1), bus=2)))

  def test_cancel_spoof_needs_stock_cruise_engaged(self):
    rest, wiper, counter = self._cancel_setup()
    for cs in (0, 1, 5, 6, 7):
      l.safety_rx_hook(di_state(cs))
      self.assertFalse(l.safety_tx_hook(pkt(0x45, stalk_frame(LEVER_CANCEL, rest, wiper, counter + 1))), cs)

  def test_cancel_spoof_needs_controls(self):
    rest, wiper, counter = self._cancel_setup()
    l.safety_rx_hook(doors(True))
    self.assertFalse(l.get_controls_allowed())
    self.assertFalse(l.safety_tx_hook(pkt(0x45, stalk_frame(LEVER_CANCEL, rest, wiper, counter + 1))))

  def test_cancel_spoof_needs_recent_stalk(self):
    rest, wiper, counter = self._cancel_setup()
    self._tick(260000)
    self._sensor(0)
    l.safety_rx_hook(di_state(2))
    self.assertFalse(l.safety_tx_hook(pkt(0x45, stalk_frame(LEVER_CANCEL, rest, wiper, counter + 1))))

  def test_cancel_spoof_blocked_without_pedal_flag(self):
    self._setup(0)
    l.safety_rx_hook(stalk(LEVER_PULL))
    l.safety_rx_hook(di_state(2))
    l.safety_rx_hook(pkt(0x45, stalk_frame(LEVER_IDLE, counter=1)))
    self.assertFalse(l.safety_tx_hook(pkt(0x45, stalk_frame(LEVER_CANCEL, counter=2))))


class TestTeslaPreapPedalBus0(TestTeslaPreapPedal):
  PARAM = Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL | Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO
  BUS = 0

  def test_pedal_bus_selection(self):
    self.assertTrue(self._cmd(0, False, bus=0))
    self.assertFalse(self._cmd(0, False, bus=2))

  def test_sensor_only_on_configured_bus(self):
    self._engaged_healthy()
    l.safety_rx_hook(gas_sensor(2000, 0, 0, bus=2))
    self.assertTrue(l.get_controls_allowed())
    self.assertTrue(self._cmd(1000, True))


class TestTeslaPreapMapping(unittest.TestCase):
  def test_enum_matches_cereal(self):
    from cereal import car
    self.assertEqual(int(car.CarParams.SafetyModel.teslaPreap), Panda.SAFETY_TESLA_PREAP)
    self.assertEqual(Panda.SAFETY_TESLA_PREAP, 37)

  def test_existing_tesla_unchanged(self):
    from cereal import car
    self.assertEqual(int(car.CarParams.SafetyModel.tesla), Panda.SAFETY_TESLA)


if __name__ == "__main__":
  unittest.main()
