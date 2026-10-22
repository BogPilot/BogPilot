"""Draft panda change for 0x309 IC discovery (libpanda, x86 build)."""

from types import SimpleNamespace

import pytest

from openpilot.selfdrive.car.tesla import ic_discovery as d


@pytest.fixture
def lp():
  from panda import Panda
  from panda.tests.libpanda import libpanda_py
  l = libpanda_py.libpanda
  l.set_timer(1000)
  assert l.set_safety_hooks(Panda.SAFETY_TESLA, Panda.FLAG_TESLA_AP1 | Panda.FLAG_TESLA_LONG_CONTROL) == 0
  l.set_controls_allowed(False)
  return SimpleNamespace(l=l, py=libpanda_py)


def _speed(lp, mph):
  raw = int(round((mph + 25) / 0.05))
  dat = bytearray(6)
  dat[2] = raw & 0xFF
  dat[3] = (raw >> 8) & 0x0F
  assert lp.l.safety_rx_hook(lp.py.make_CANPacket(0x118, 0, bytes(dat)))


def _tx(lp, dat):
  return bool(lp.l.safety_tx_hook(lp.py.make_CANPacket(0x309, 0, dat)))


def test_parked_disengaged_tx_allowed_and_stock_dropped(lp):
  dat = d.build_frame(d.MUX_LEAD, d.object_raws(5, d.TEST_DX_M, 0.0))
  assert lp.l.safety_fwd_hook(2, 0x309) == 0  # stock forwarded before any OP TX
  _speed(lp, 0.0)
  assert _tx(lp, dat)
  assert lp.l.safety_fwd_hook(2, 0x309) == -1  # stock copy dropped while OP sends
  lp.l.set_timer(1000 + 150000)  # 150 ms with no OP 0x309
  assert lp.l.safety_fwd_hook(2, 0x309) == 0


def test_rejected_when_engaged_or_moving(lp):
  dat = d.build_frame(d.MUX_LEAD, d.object_raws(5, d.TEST_DX_M, 0.0))
  _speed(lp, 0.0)
  lp.l.set_controls_allowed(True)
  assert not _tx(lp, dat)
  lp.l.set_controls_allowed(False)
  _speed(lp, 5.0)
  assert not _tx(lp, dat)
  assert lp.l.safety_fwd_hook(2, 0x309) == 0


def test_other_cluster_addrs_unchanged(lp):
  for addr in (0x399, 0x389, 0x239):
    assert lp.l.safety_fwd_hook(2, addr) == 0
