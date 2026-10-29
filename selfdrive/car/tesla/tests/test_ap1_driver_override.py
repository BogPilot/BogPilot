"""AP1 driver steering input shows as override (grey border) again.

frog_ap1 and the stock Tesla port set carState.steeringPressed from any
non-zero EPAS_handsOnLevel. f8aadaa8 raised AP1 to >= 2 (TinklaHandsOnLevel),
but logged AP1 EPAS only reports levels 0, 1 and 3, so steerOverride (and the
prebuilt UI's grey STATUS_OVERRIDE border) only appeared on brief level 3
peaks. steeringPressed is back to level >= 1; the lateral pause stays at >= 2
in CarController.

CarState is run with stub CANParser / CANDefine / CarStateBase (the opendbc
and msgq .so files here are AArch64).

Not a product, no warranty, driver remains responsible, comply with local law.
"""

import importlib
import sys
import types
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.car.tesla.hso import (
  AP1_DRIVER_INPUT_LEVEL,
  AP1_HANDS_ON_LEVEL,
  ap1_driver_input,
  ap1_lat_active,
  ap1_steering_pressed,
)

ROOT = Path(__file__).resolve().parents[4]


def test_driver_input_is_any_hands_level_pause_stays_level_2():
  assert AP1_DRIVER_INPUT_LEVEL == 1
  assert AP1_HANDS_ON_LEVEL == 2
  assert [ap1_driver_input(lv) for lv in range(4)] == [False, True, True, True]
  # The lateral pause threshold did not move.
  assert [ap1_steering_pressed(lv) for lv in range(4)] == [False, False, True, True]
  assert ap1_lat_active(True, 1) is True
  assert ap1_lat_active(True, 3) is False


class _Base:
  def __init__(self, CP, FPCP):
    self.CP = CP
    self.FPCP = FPCP

  def update_speed_kf(self, v):
    return v, 0.0


class _Define:
  def __init__(self, dbc):
    self.dv = defaultdict(lambda: defaultdict(dict))


@pytest.fixture
def carstate_mod(monkeypatch):
  interfaces = types.ModuleType("openpilot.selfdrive.car.interfaces")
  interfaces.CarStateBase = _Base
  interfaces.CarControllerBase = type("CarControllerBase", (), {})
  interfaces.CarInterfaceBase = type("CarInterfaceBase", (), {})
  parser = types.ModuleType("opendbc.can.parser")
  parser.CANParser = object
  define = types.ModuleType("opendbc.can.can_define")
  define.CANDefine = _Define
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.car.interfaces", interfaces)
  monkeypatch.setitem(sys.modules, "opendbc.can.parser", parser)
  monkeypatch.setitem(sys.modules, "opendbc.can.can_define", define)
  sys.modules.pop("openpilot.selfdrive.car.tesla.carstate", None)
  mod = importlib.import_module("openpilot.selfdrive.car.tesla.carstate")
  yield mod
  sys.modules.pop("openpilot.selfdrive.car.tesla.carstate", None)


def _cans(hands_level):
  vl = defaultdict(lambda: defaultdict(float))
  vl["EPAS_sysStatus"]["EPAS_handsOnLevel"] = hands_level
  vl["EPAS_sysStatus"]["EPAS_torsionBarTorque"] = -1.5
  vl["BrakeMessage"]["driverBrakeStatus"] = 1
  vl["SDM1"]["SDM_bcklDrivStatus"] = 1
  vl_all = defaultdict(lambda: defaultdict(list))
  cp = SimpleNamespace(vl=vl, vl_all=vl_all)
  cp_cam = SimpleNamespace(vl=vl, vl_all=vl_all)
  return cp, cp_cam


def _steering_pressed(mod, fp, level):
  CS = mod.CarState(SimpleNamespace(carFingerprint=fp), None)
  cp, cp_cam = _cans(level)
  ret, _ = CS.update(cp, cp_cam, None)
  assert CS.hands_on_level == level
  return ret.steeringPressed


def test_ap1_level_1_sets_steering_pressed(carstate_mod):
  from openpilot.selfdrive.car.tesla.values import CAR
  got = [_steering_pressed(carstate_mod, CAR.TESLA_AP1_MODELS, lv) for lv in range(4)]
  assert got == [False, True, True, True]


def test_model3_mapping_unchanged(carstate_mod):
  from openpilot.selfdrive.car.tesla.values import CAR
  got = [_steering_pressed(carstate_mod, CAR.TESLA_AP2_MODELS, lv) for lv in range(4)]
  assert got == [False, True, True, True]


def test_override_chain_to_prebuilt_ui_grey_border():
  # steeringPressed -> steerOverride -> OVERRIDE_LATERAL -> State.overriding
  # -> prebuilt UI STATUS_OVERRIDE (grey). None of these were changed.
  interfaces = (ROOT / "selfdrive/car/interfaces.py").read_text()
  assert "if cs_out.steeringPressed:\n      events.add(EventName.steerOverride)" in interfaces
  events = (ROOT / "selfdrive/controls/lib/events.py").read_text()
  i = events.index("EventName.steerOverride: {")
  assert "ET.OVERRIDE_LATERAL" in events[i:i + 120]
  controlsd = (ROOT / "selfdrive/controls/controlsd.py").read_text()
  assert "elif self.contains_event_type(ET.OVERRIDE_LATERAL, ET.OVERRIDE_LONGITUDINAL):\n            self.state = State.overriding" in controlsd
  ui = (ROOT / "selfdrive/ui/ui.cc").read_text()
  assert "OpenpilotState::OVERRIDING) {\n      status = STATUS_OVERRIDE;" in ui


def test_controlsd_does_not_pause_lateral_on_steering_pressed():
  # A level 1 override keeps latActive (stock Tesla port). The AP1 pause at
  # >= 2 is CarController's ap1_lat_active / NONE frame.
  controlsd = (ROOT / "selfdrive/controls/controlsd.py").read_text()
  assert "ap1_hands_pause" not in controlsd
  assert '"TESLA_AP1_MODELS" and CS.steeringPressed' not in controlsd
  controller = (ROOT / "selfdrive/car/tesla/carcontroller.py").read_text()
  assert "ap1_lat_active(CC.latActive, CS.hands_on_level)" in controller


def test_inhibit_alert_not_counted_during_hands_pause(carstate_mod):
  sys.modules.pop("openpilot.selfdrive.car.tesla.interface", None)
  interface = importlib.import_module("openpilot.selfdrive.car.tesla.interface")
  sys.modules.pop("openpilot.selfdrive.car.tesla.interface", None)
  from cereal import car
  EventName = car.CarEvent.EventName

  class _Events:
    def __init__(self):
      self.names = []

    def add(self, name):
      self.names.append(name)

  def run(level, frames=150):
    fake = SimpleNamespace(CS=SimpleNamespace(eac_status="EAC_INHIBITED", hands_on_level=level),
                           _ap1_driver_yield_active=lambda: False)
    c = SimpleNamespace(enabled=True, latActive=True)
    ret = SimpleNamespace(steerFaultPermanent=False)
    ev = _Events()
    for _ in range(frames):
      interface.CarInterface._ap1_epas_inhibit_alert(fake, ev, c, ret)
    return ev.names

  # Level 3: EPAS INHIBITED because the driver is steering. No alert.
  assert run(3) == []
  # Level 0 / 1 with lat wanted: still alerts after ~1 s.
  assert EventName.steerTempUnavailableSilent in run(0)
  assert EventName.steerTempUnavailableSilent in run(1)
