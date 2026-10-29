#!/usr/bin/env python3
"""Tesla safety: AP1 cluster frames (0x399, 0x389, 0x239) and their forward rule.

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
CLUSTER = {0x399: 750000, 0x389: 750000, 0x239: 150000}
OTHER_DAS = (0x309, 0x3a9, 0x3e9, 0x329, 0x369)


def pkt(addr, dat, bus=0):
  return libpanda_py.make_CANPacket(addr, bus, bytes(dat))


def status(state):
  dat = bytearray(8)
  dat[0] = state & 0x0F
  return dat


class TestTeslaAp1Cluster(unittest.TestCase):
  def init(self, param):
    l.set_timer(1000)
    self.assertEqual(l.set_safety_hooks(Panda.SAFETY_TESLA, param), 0)
    l.set_controls_allowed(False)

  def tx(self, addr, dat=None):
    return l.safety_tx_hook(pkt(addr, dat if dat is not None else bytes(8)))

  def test_cluster_tx_only_with_ap1_flag(self):
    for param in (0, Panda.FLAG_TESLA_LONG_CONTROL, Panda.FLAG_TESLA_RAVEN,
                  Panda.FLAG_TESLA_POWERTRAIN | Panda.FLAG_TESLA_LONG_CONTROL):
      self.init(param)
      for addr in CLUSTER:
        self.assertFalse(self.tx(addr, status(2)), (param, hex(addr)))
    self.init(AP1)
    for addr in CLUSTER:
      self.assertTrue(self.tx(addr, status(2)), hex(addr))

  def test_cluster_tx_wrong_bus_or_length_rejected(self):
    self.init(AP1)
    for addr in CLUSTER:
      self.assertFalse(l.safety_tx_hook(pkt(addr, bytes(8), bus=2)))
      self.assertFalse(l.safety_tx_hook(pkt(addr, bytes(7))))

  def test_other_das_frames_still_not_allowed(self):
    self.init(AP1)
    for addr in OTHER_DAS:
      self.assertFalse(self.tx(addr), hex(addr))

  def test_active_autopilot_state_needs_controls_allowed(self):
    self.init(AP1)
    for state in range(16):
      l.set_controls_allowed(False)
      self.assertEqual(self.tx(0x399, status(state)), state not in (3, 4, 5), state)
      l.set_controls_allowed(True)
      self.assertTrue(self.tx(0x399, status(state)), state)
    # Only 0x399 carries that state. Same bits on 0x389 / 0x239 are not checked.
    l.set_controls_allowed(False)
    self.assertTrue(self.tx(0x389, status(5)))
    self.assertTrue(self.tx(0x239, status(5)))

  def test_blocked_cluster_tx_does_not_change_controls_allowed(self):
    self.init(AP1)
    l.set_controls_allowed(True)
    self.assertTrue(self.tx(0x399, status(5)))
    self.assertTrue(l.get_controls_allowed())

  def test_stock_cluster_forwarded_without_op_tx(self):
    for param in (0, AP1, Panda.FLAG_TESLA_RAVEN):
      self.init(param)
      for addr in list(CLUSTER) + list(OTHER_DAS):
        self.assertEqual(l.safety_fwd_hook(2, addr), 0, (param, hex(addr)))

  def test_stock_cluster_dropped_only_while_op_recently_sent(self):
    for addr, timeout in CLUSTER.items():
      self.init(AP1)
      t = 5_000_000
      l.set_timer(t)
      self.assertTrue(self.tx(addr, status(2)))
      self.assertEqual(l.safety_fwd_hook(2, addr), -1, hex(addr))
      l.set_timer(t + timeout - 1)
      self.assertEqual(l.safety_fwd_hook(2, addr), -1, hex(addr))
      l.set_timer(t + timeout)
      self.assertEqual(l.safety_fwd_hook(2, addr), 0, hex(addr))
      # Other cluster addresses are tracked separately.
      l.set_timer(t)
      self.tx(addr, status(2))
      for other in CLUSTER:
        if other != addr:
          self.assertEqual(l.safety_fwd_hook(2, other), 0, (hex(addr), hex(other)))
      # Bus 0 to 2 is unchanged.
      self.assertEqual(l.safety_fwd_hook(0, addr), 2)

  def test_rejected_tx_does_not_start_substitution(self):
    self.init(AP1)
    l.set_controls_allowed(False)
    self.assertFalse(self.tx(0x399, status(5)))
    self.assertEqual(l.safety_fwd_hook(2, 0x399), 0)

  def test_no_drop_without_ap1_even_after_tx_attempt(self):
    self.init(Panda.FLAG_TESLA_LONG_CONTROL)
    for addr in CLUSTER:
      self.assertFalse(self.tx(addr, status(2)))
      self.assertEqual(l.safety_fwd_hook(2, addr), 0)

  def test_init_clears_substitution(self):
    self.init(AP1)
    self.tx(0x239)
    self.assertEqual(l.safety_fwd_hook(2, 0x239), -1)
    self.init(AP1)
    self.assertEqual(l.safety_fwd_hook(2, 0x239), 0)

  def test_existing_steer_forward_rule_unchanged(self):
    self.init(AP1)
    self.assertEqual(l.safety_fwd_hook(2, 0x488), 0)
    self.tx(0x399, status(2))
    self.assertEqual(l.safety_fwd_hook(2, 0x488), 0)
    self.assertEqual(l.safety_fwd_hook(2, 0x2b9), 0)

  def test_hold_clear_rule_unchanged(self):
    self.init(AP1)
    self.assertTrue(self.tx(0x349, bytes(8)))
    self.assertFalse(self.tx(0x349, bytes([2, 0, 0, 0, 0, 0, 0, 0])))
    self.assertEqual(l.safety_fwd_hook(2, 0x349), 0)


if __name__ == "__main__":
  unittest.main()
