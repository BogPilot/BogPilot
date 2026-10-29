#!/usr/bin/env python3
"""Tesla AP1 safety: desired_angle_last follows the measured wheel while disengaged.

openpilot sends no 0x488 while disengaged on AP1 (stock Mobileye 0x488 is
forwarded instead), so without a resync the first 0x488 after an engage was
rate checked against the last angle of the previous engagement. With
TESLA_FLAG_AP1, each EPAS_sysStatus (0x370) received while neither
controls_allowed nor always-on lateral is active now sets desired_angle_last
to the measured angle. The rate window around that reference is the same
Tinkla table as before; nothing else changes.

Runs against tests/libpanda/libpanda.so (x86 build of board/safety). Build it
with `scons -u tests/libpanda/libpanda.so` from panda/, and have cffi on the
path. No upstream tests/safety/common.py is used; this checkout has none.

Not a driving validation.
"""
import unittest

from panda import Panda
from panda.tests.libpanda import libpanda_py

l = libpanda_py.libpanda
AP1 = Panda.FLAG_TESLA_AP1 | Panda.FLAG_TESLA_LONG_CONTROL  # 10, what AP1 installs
ALT_EXP_ALWAYS_ON_LATERAL = 32

# DI_state cruise states (byte 1 >> 4)
STANDBY = 1
ENABLED = 2

# 8 m/s gives vehicle_speed.min - 1 = 7.0 m/s exactly, a Tinkla breakpoint:
# up 4.0 deg/frame, down 5.0 deg/frame. In 0.1 deg CAN units with the +1
# fudge from steer_angle_cmd_checks: 41 up and 51 down.
SPEED_MPS = 8.0
DELTA_UP = 41
DELTA_DOWN = 51


def pkt(addr, dat, bus=0):
  return libpanda_py.make_CANPacket(addr, bus, bytes(dat))


def epas_status(angle_deg):
  # EPAS_internalSAS: (0.1 * raw) - 819.2, 14 bits in byte 4 [5:0] and byte 5.
  raw = int(round(angle_deg * 10)) + 8192
  dat = bytearray(8)
  dat[4] = (raw >> 8) & 0x3F
  dat[5] = raw & 0xFF
  return pkt(0x370, dat)


def di_state(cruise_state):
  dat = bytearray(8)
  dat[1] = (cruise_state & 0x0F) << 4
  return pkt(0x368, dat)


def di_torque2(speed_mps):
  # ((0.05 * raw) - 25) * MPH_TO_MPS, 12 bits in byte 3 [3:0] and byte 2.
  raw = int(round(((speed_mps / 0.447) + 25) / 0.05))
  dat = bytearray(6)
  dat[2] = raw & 0xFF
  dat[3] = (raw >> 8) & 0x0F
  return pkt(0x118, dat)


def steer_cmd(angle_can, control_type=1):
  # DAS_steeringControl in the panda's 1/10 deg unit: raw = angle_can + 16384.
  raw = angle_can + 16384
  dat = bytearray(4)
  dat[0] = (raw >> 8) & 0x7F
  dat[1] = raw & 0xFF
  dat[2] = (control_type & 0x03) << 6
  return pkt(0x488, dat)


class TestTeslaAp1AngleResync(unittest.TestCase):
  def init(self, param=AP1):
    self.assertEqual(l.set_safety_hooks(Panda.SAFETY_TESLA, param), 0)
    l.init_tests()
    l.set_timer(1000)
    for _ in range(6):
      self.assertTrue(l.safety_rx_hook(di_torque2(SPEED_MPS)))
    self.assertEqual(l.get_vehicle_speed_min(), int(SPEED_MPS * 100))
    self.assertTrue(l.safety_rx_hook(di_state(STANDBY)))
    self.assertFalse(l.get_controls_allowed())

  def wheel(self, angle_deg):
    # Six samples so angle_meas min and max both equal the new angle.
    for _ in range(6):
      self.assertTrue(l.safety_rx_hook(epas_status(angle_deg)))

  def engage(self):
    self.assertTrue(l.safety_rx_hook(di_state(ENABLED)))
    self.assertTrue(l.get_controls_allowed())

  def disengage(self):
    self.assertTrue(l.safety_rx_hook(di_state(STANDBY)))
    self.assertFalse(l.get_controls_allowed())

  def tx(self, angle_can, control_type=1):
    return l.safety_tx_hook(steer_cmd(angle_can, control_type))

  def drive_then_reengage(self, wheel_after_deg):
    """Engage at 0, ramp openpilot to +5.0 deg, disengage, move the wheel, re-engage."""
    self.wheel(0)
    self.engage()
    for a in (0, 30, 50):
      self.assertTrue(self.tx(a), a)
    self.assertEqual(l.get_desired_angle_last(), 50)
    self.disengage()
    self.wheel(wheel_after_deg)
    self.engage()

  # ---- the approved change ----
  def test_first_cmd_after_reengage_near_measured_accepted(self):
    for wheel in (-30.0, -4.0, 0.0, 12.5, 90.0):
      self.init()
      self.drive_then_reengage(wheel)
      self.assertEqual(l.get_desired_angle_last(), int(round(wheel * 10)))
      self.assertTrue(self.tx(int(round(wheel * 10))), wheel)

  def test_first_cmd_after_reengage_far_from_measured_rejected(self):
    for wheel in (-30.0, 12.5, 90.0):
      for offset in (-200, -100, 100, 200):  # 10 and 20 deg from the wheel
        self.init()
        self.drive_then_reengage(wheel)
        self.assertFalse(self.tx(int(round(wheel * 10)) + offset), (wheel, offset))

  def test_stale_previous_angle_rejected_after_wheel_moved(self):
    # Before the resync this was the only command accepted after re-engage.
    self.init()
    self.drive_then_reengage(-30.0)
    self.assertFalse(self.tx(50))

  def test_first_cmd_window_is_the_normal_rate_window(self):
    # Window around the measured wheel is exactly the Tinkla rate step at 8 m/s.
    # Positive reference: away from zero is "up", toward zero is "down".
    cases = (
      (+DELTA_UP, True), (+DELTA_UP + 1, False),
      (-DELTA_DOWN, True), (-DELTA_DOWN - 1, False),
    )
    for wheel_can in (300, -300):
      for step, ok in cases:
        signed = step if wheel_can > 0 else -step
        self.init()
        self.drive_then_reengage(wheel_can / 10)
        self.assertEqual(self.tx(wheel_can + signed), ok, (wheel_can, step))

  # ---- what must not change ----
  def test_no_resync_while_controls_allowed(self):
    self.init()
    self.wheel(0)
    self.engage()
    self.assertTrue(self.tx(0))
    self.wheel(20.0)
    self.assertEqual(l.get_desired_angle_last(), 0)
    # Rate is still checked against the last command, not the wheel.
    self.assertTrue(self.tx(DELTA_DOWN))  # from 0, the <=0 side uses the down rate going positive
    self.assertFalse(self.tx(200))

  def test_no_resync_with_always_on_lateral(self):
    self.init()
    l.set_alternative_experience(ALT_EXP_ALWAYS_ON_LATERAL)
    self.assertTrue(l.get_acc_main_on())
    l.set_desired_angle_last(50)
    self.wheel(-30.0)
    self.assertEqual(l.get_desired_angle_last(), 50)

  def test_resync_with_aol_flag_but_main_off(self):
    # AOL only gates the rate check when acc main (or lkas) is on; same gate here.
    self.init()
    l.set_alternative_experience(ALT_EXP_ALWAYS_ON_LATERAL)
    self.assertTrue(l.safety_rx_hook(di_state(0)))  # OFF
    self.assertFalse(l.get_acc_main_on())
    l.set_desired_angle_last(50)
    self.wheel(-30.0)
    self.assertEqual(l.get_desired_angle_last(), -300)

  def test_no_resync_without_ap1_flag(self):
    for param in (0, Panda.FLAG_TESLA_LONG_CONTROL):
      self.init(param)
      l.set_desired_angle_last(50)
      self.wheel(-30.0)
      self.assertEqual(l.get_desired_angle_last(), 50, param)

  def test_angle_control_still_rejected_while_disengaged(self):
    self.init()
    self.wheel(-30.0)
    self.assertFalse(self.tx(-300, control_type=1))

  def test_inactive_angle_rule_unchanged(self):
    # Type NONE (0) / DISABLED (3) must be within 0.1 deg of the measured wheel.
    for control_type in (0, 3):
      self.init()
      self.wheel(-30.0)
      self.assertTrue(self.tx(-300, control_type))
      self.assertTrue(self.tx(-299, control_type))
      self.assertTrue(self.tx(-301, control_type))
      self.assertFalse(self.tx(-298, control_type))
      self.assertFalse(self.tx(-302, control_type))
      self.assertFalse(self.tx(50, control_type))

  def test_rate_limit_after_first_frame_unchanged(self):
    self.init()
    self.drive_then_reengage(-30.0)
    self.assertTrue(self.tx(-300))
    # From -30.0 (negative side): away from zero uses up (41), toward zero down (51).
    self.assertTrue(self.tx(-300 - DELTA_UP))
    self.assertFalse(self.tx(-300 - DELTA_UP - DELTA_UP - 1))


if __name__ == "__main__":
  unittest.main()
