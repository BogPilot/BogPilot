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


def pedal_cmd(raw_gas, enable, bus=2):
  dat = bytearray(6)
  dat[0] = (raw_gas >> 8) & 0xFF
  dat[1] = raw_gas & 0xFF
  dat[4] = 0x80 if enable else 0
  return pkt(0x551, dat, bus)


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
  def _setup(self, param):
    l.set_alternative_experience(0)
    self.assertEqual(l.set_safety_hooks(Panda.SAFETY_TESLA_PREAP, param), 0)
    l.init_tests()
    l.safety_rx_hook(di_torque2(GEAR_D))
    l.safety_rx_hook(doors(False))
    l.safety_rx_hook(epas_status(0.0))

  def test_pedal_bus_selection(self):
    self._setup(Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL)
    self.assertTrue(l.safety_tx_hook(pedal_cmd(0, False, bus=2)))
    self.assertFalse(l.safety_tx_hook(pedal_cmd(0, False, bus=0)))
    self._setup(Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL | Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO)
    self.assertTrue(l.safety_tx_hook(pedal_cmd(0, False, bus=0)))
    self.assertFalse(l.safety_tx_hook(pedal_cmd(0, False, bus=2)))

  def test_pedal_enable_needs_controls_and_no_brake(self):
    self._setup(Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL)
    self.assertFalse(l.safety_tx_hook(pedal_cmd(800, True)))
    l.safety_rx_hook(stalk(LEVER_PULL))
    self.assertTrue(l.get_controls_allowed())
    self.assertTrue(l.safety_tx_hook(pedal_cmd(800, True)))
    self.assertFalse(l.safety_tx_hook(pedal_cmd(501, False)))
    self.assertTrue(l.safety_tx_hook(pedal_cmd(500, False)))

  def test_pedal_sensor_gas_threshold(self):
    self._setup(Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL)
    l.safety_rx_hook(stalk(LEVER_PULL))
    l.safety_rx_hook(pkt(0x552, bytes((0x02, 0x8A, 0, 0, 0, 0)), bus=2))  # 650: not pressed
    self.assertTrue(l.get_controls_allowed())
    l.safety_rx_hook(pkt(0x552, bytes((0x02, 0x8B, 0, 0, 0, 0)), bus=2))  # 651: pressed
    self.assertFalse(l.get_controls_allowed())

  def test_calibration_flag_is_silent(self):
    self._setup(Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL | Panda.FLAG_TESLA_PREAP_PEDAL_CALIBRATION)
    l.safety_rx_hook(stalk(LEVER_PULL))
    self.assertFalse(l.get_controls_allowed())
    self.assertFalse(l.safety_tx_hook(epas_control(1)))
    self.assertFalse(l.safety_tx_hook(steer(0.0, False)))
    self.assertFalse(l.safety_tx_hook(pedal_cmd(0, False)))


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
