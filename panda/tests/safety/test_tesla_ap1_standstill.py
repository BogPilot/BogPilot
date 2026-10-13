#!/usr/bin/env python3
"""Tesla safety: AP1 standstill experiment (TESLA_FLAG_AP1_STANDSTILL, 16).

While longitudinal is not allowed, 0x2b9 may carry exactly the standby shape:
setSpeed 0, accState 0 or 4, aebEvent 0, accelMax <= 0, accelMin in [min, 0].
Runs against tests/libpanda/libpanda.so. Not a driving validation.
"""
import unittest

from panda import Panda
from panda.tests.libpanda import libpanda_py

l = libpanda_py.libpanda
AP1 = Panda.FLAG_TESLA_AP1 | Panda.FLAG_TESLA_LONG_CONTROL
AP1_SS = AP1 | Panda.FLAG_TESLA_AP1_STANDSTILL


def das_control(set_speed_kph=0.0, acc_state=4, aeb=0, accel_min=-1.4, accel_max=0.0, jerk_min=-0.46, jerk_max=0.476, counter=0):
  """Pack 0x2b9 per opendbc tesla_can.dbc BO_ 697 (little endian)."""
  v = 0
  v |= int(round(set_speed_kph / 0.1)) & 0xFFF
  v |= (acc_state & 0xF) << 12
  v |= (aeb & 0x3) << 16
  v |= (int(round((jerk_min + 15.232) / 0.03)) & 0x1FF) << 18
  v |= (int(round(jerk_max / 0.059)) & 0xFF) << 27
  v |= (int(round((accel_min + 15) / 0.04)) & 0x1FF) << 35
  v |= (int(round((accel_max + 15) / 0.04)) & 0x1FF) << 44
  v |= (counter & 0x7) << 53
  dat = bytearray(v.to_bytes(8, "little"))
  dat[7] = (0xb9 + 0x02 + sum(dat[:7])) & 0xFF
  return bytes(dat)


class TestTeslaAp1Standstill(unittest.TestCase):
  def init(self, param, allowed=False):
    l.set_timer(1000)
    self.assertEqual(l.set_safety_hooks(Panda.SAFETY_TESLA, param), 0)
    l.set_controls_allowed(allowed)

  def tx(self, dat):
    return l.safety_tx_hook(libpanda_py.make_CANPacket(0x2b9, 0, dat))

  def test_standby_accepted_only_with_flag(self):
    self.init(AP1)
    self.assertFalse(self.tx(das_control()))
    self.init(AP1_SS)
    self.assertTrue(self.tx(das_control()))
    self.assertTrue(self.tx(das_control(acc_state=0)))

  def test_flag_needs_ap1_and_long(self):
    for p in (Panda.FLAG_TESLA_AP1_STANDSTILL, Panda.FLAG_TESLA_AP1_STANDSTILL | Panda.FLAG_TESLA_LONG_CONTROL,
              Panda.FLAG_TESLA_AP1_STANDSTILL | Panda.FLAG_TESLA_AP1):
      self.init(p)
      self.assertFalse(self.tx(das_control()), p)

  def test_only_standby_values(self):
    self.init(AP1_SS)
    bad = [
      das_control(set_speed_kph=1.0),
      das_control(set_speed_kph=409.5),  # SNA
      das_control(acc_state=3), das_control(acc_state=5), das_control(acc_state=15),
      das_control(aeb=1), das_control(aeb=2),
      das_control(accel_max=0.04), das_control(accel_max=1.8),  # Tinkla's +1.8 is rejected
      das_control(accel_min=0.04, accel_max=0.04),
      das_control(accel_min=-4.0),  # below panda min_accel
      das_control(accel_min=0.0, accel_max=-0.4),  # min > max
    ]
    for d in bad:
      self.assertFalse(self.tx(d), d.hex())
    for d in (das_control(accel_min=-3.5), das_control(accel_min=0.0, accel_max=0.0), das_control(accel_min=-1.4, accel_max=-0.2)):
      self.assertTrue(self.tx(d), d.hex())

  def test_standby_rejected_during_stock_aeb(self):
    self.init(AP1_SS)
    aeb = bytearray(8); aeb[2] = 1
    l.safety_rx_hook(libpanda_py.make_CANPacket(0x2b9, 2, bytes(aeb)))
    self.assertFalse(self.tx(das_control()))

  def test_engaged_path_unchanged(self):
    for p in (AP1, AP1_SS):
      self.init(p, allowed=True)
      self.assertTrue(self.tx(das_control(set_speed_kph=50, accel_min=0.0, accel_max=1.5)), p)
      self.assertTrue(self.tx(das_control(set_speed_kph=50, accel_min=-3.5, accel_max=0.0)), p)
      self.assertFalse(self.tx(das_control(set_speed_kph=50, accel_min=0.0, accel_max=2.2)), p)
      self.assertFalse(self.tx(das_control(set_speed_kph=50, aeb=1, accel_min=0.0, accel_max=1.0)), p)
      # disengaged, non-standby: still rejected with and without the flag
      l.set_controls_allowed(False)
      self.assertFalse(self.tx(das_control(set_speed_kph=50, accel_min=0.0, accel_max=1.5)), p)
      self.assertTrue(self.tx(das_control(set_speed_kph=50, accel_min=0.0, accel_max=0.0)), p)  # inactive frame

  def test_standby_tx_arms_recent_drop_like_any_op_2b9(self):
    self.init(AP1_SS)
    self.assertEqual(l.safety_fwd_hook(2, 0x2b9), 0)
    self.assertTrue(self.tx(das_control()))
    self.assertEqual(l.safety_fwd_hook(2, 0x2b9), -1)
    l.set_timer(1000 + 60000)
    self.assertEqual(l.safety_fwd_hook(2, 0x2b9), 0)


if __name__ == "__main__":
  unittest.main()
