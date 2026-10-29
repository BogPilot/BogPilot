"""AP1 CAN → frogpilotCarState.dashboardSpeedLimit (m/s).

Pure decode tests plus a stubbed CarState.update for AP1-only wiring.
Stock fused frames come from fixtures (real payloads, no PII).

Not a product, no warranty, driver remains responsible, comply with local law.
"""

import importlib
import sys
import types
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.common.conversions import Conversions as CV
from openpilot.selfdrive.car.tesla.speed_limit import (
  dashboard_speed_limit_ms,
  fused_speed_limit_ms,
  mpp_speed_limit_ms,
  ui_map_speed_limit_ms,
  units_are_metric,
)
from openpilot.selfdrive.car.tesla.tests.fixtures.ap1_speed_limit_seq import (
  FUSED_NONE_PHYS,
  FUSED_TRANSITIONS,
  FUSED_UNKNOWN_PHYS,
  STOCK_FUSED_FRAMES,
  ZONE_SAMPLES,
)

ROOT = Path(__file__).resolve().parents[4]
MPH = CV.MPH_TO_MS


def test_units_default_mph():
  assert units_are_metric(0) is False
  assert units_are_metric(1) is True
  assert units_are_metric(None) is False
  assert units_are_metric("x") is False


def test_fused_sna_and_none_are_zero():
  assert fused_speed_limit_ms(None, False) == 0.0
  assert fused_speed_limit_ms(FUSED_UNKNOWN_PHYS, False) == 0.0
  assert fused_speed_limit_ms(FUSED_NONE_PHYS, False) == 0.0
  assert fused_speed_limit_ms(150.0, False) == 0.0
  assert fused_speed_limit_ms(155.0, True) == 0.0


def test_fused_zones_to_ms_mph():
  for mph in (5, 25, 30, 35, 40, 45, 70):
    assert fused_speed_limit_ms(float(mph), False) == pytest.approx(mph * MPH)


def test_ui_map_enum_and_sna():
  assert ui_map_speed_limit_ms(0, False) == 0.0
  assert ui_map_speed_limit_ms(30, False) == 0.0  # UNLIMITED
  assert ui_map_speed_limit_ms(31, False) == 0.0  # SNA
  assert ui_map_speed_limit_ms(None, False) == 0.0
  assert ui_map_speed_limit_ms(7, False) == pytest.approx(30 * MPH)   # LEQ_30
  assert ui_map_speed_limit_ms(10, False) == pytest.approx(45 * MPH)  # LEQ_45
  assert ui_map_speed_limit_ms(6, False) == pytest.approx(25 * MPH)


def test_mpp_zero_is_no_limit():
  assert mpp_speed_limit_ms(0, False) == 0.0
  assert mpp_speed_limit_ms(None, False) == 0.0
  assert mpp_speed_limit_ms(45.0, False) == pytest.approx(45 * MPH)


def test_priority_mobileye_then_ui_then_mpp():
  # Fused wins even when UI/mpp disagree
  assert dashboard_speed_limit_ms(45.0, 6, 25.0, 0) == pytest.approx(45 * MPH)
  # No fused → UI map enum
  assert dashboard_speed_limit_ms(0.0, 10, 25.0, 0) == pytest.approx(45 * MPH)
  assert dashboard_speed_limit_ms(None, 7, 55.0, 0) == pytest.approx(30 * MPH)
  # No fused, no UI → mpp
  assert dashboard_speed_limit_ms(0.0, 31, 40.0, 0) == pytest.approx(40 * MPH)
  # All empty
  assert dashboard_speed_limit_ms(0.0, 0, 0.0, 0) == 0.0
  assert dashboard_speed_limit_ms(155.0, 30, 0.0, 0) == 0.0


def test_zone_samples_from_logs():
  for fused, ui, mpp, units in ZONE_SAMPLES:
    got = dashboard_speed_limit_ms(fused, ui, mpp, units)
    assert got == pytest.approx(fused * MPH)
    # UI alone would match the same posted mph for these agreeing samples
    assert ui_map_speed_limit_ms(ui, False) == pytest.approx(fused * MPH)


def test_fused_down_transitions_from_logs():
  """Logged stock fused drops (45→35, 45→25, 40→35, …)."""
  downs = [(a, b) for a, b in FUSED_TRANSITIONS if a > b]
  assert (45.0, 35.0) in downs
  assert (45.0, 25.0) in downs
  assert (40.0, 35.0) in downs
  assert (35.0, 25.0) in downs
  for a, b in downs:
    before = dashboard_speed_limit_ms(a, None, None, 0)
    after = dashboard_speed_limit_ms(b, None, None, 0)
    assert before == pytest.approx(a * MPH)
    assert after == pytest.approx(b * MPH)
    assert after < before


def test_stock_frame_unpack_matches_fused_phys():
  """cluster.unpack on real src-2 payloads → DAS_fusedSpeedLimit physical."""
  from openpilot.selfdrive.car.tesla import cluster as c
  for fused_phys, hexdat in STOCK_FUSED_FRAMES:
    vals = c.unpack(c.AUTOPILOT_STATUS, bytes.fromhex(hexdat))
    assert vals["DAS_fusedSpeedLimit"] == pytest.approx(fused_phys)
    assert fused_speed_limit_ms(vals["DAS_fusedSpeedLimit"], False) == pytest.approx(fused_phys * MPH)


def _install_carstate_stubs():
  """AArch64 .so stubs so CarState can import on this box."""
  if "opendbc.can.parser" not in sys.modules:
    parser = types.ModuleType("opendbc.can.parser")
    parser.CANParser = object
    sys.modules["opendbc.can.parser"] = parser
  if "opendbc.can.can_define" not in sys.modules:
    define = types.ModuleType("opendbc.can.can_define")

    class CANDefine:
      def __init__(self, *a, **k):
        self.dv = defaultdict(lambda: defaultdict(dict))
    define.CANDefine = CANDefine
    sys.modules["opendbc.can.can_define"] = define

  # CarStateBase
  interfaces = types.ModuleType("openpilot.selfdrive.car.interfaces")
  class CarStateBase:
    def __init__(self, CP, FPCP):
      self.CP = CP
      self.FPCP = FPCP
      self.out = None
    def update_speed_kf(self, v):
      return v, 0.0
  interfaces.CarStateBase = CarStateBase
  sys.modules["openpilot.selfdrive.car.interfaces"] = interfaces



class _Base:
  def __init__(self, CP, FPCP):
    self.CP = CP
    self.FPCP = FPCP
    self.out = None

  def update_speed_kf(self, v):
    return v, 0.0


class _Define:
  def __init__(self, dbc):
    self.dv = defaultdict(lambda: defaultdict(dict))


def _stub_usb1(monkeypatch):
  """Panda import needs a few usb1 constants on this box."""
  if "usb1" in sys.modules and hasattr(sys.modules["usb1"], "ENDPOINT_IN"):
    return
  usb1 = types.ModuleType("usb1")
  usb1.ENDPOINT_IN = 0x80
  usb1.ENDPOINT_OUT = 0x00
  usb1.TYPE_VENDOR = 0x40
  usb1.RECIPIENT_DEVICE = 0x00
  usb1.USBError = type("USBError", (Exception,), {})
  class USBContext:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def openByVendorIDAndProductID(self, *a, **k): return None
    def getDeviceList(self, *a, **k): return []
  usb1.USBContext = USBContext
  monkeypatch.setitem(sys.modules, "usb1", usb1)


@pytest.fixture
def carstate_mod(monkeypatch):
  # values.py pulls panda → usb1; realtime → tici hardware → smbus2 on this box
  _stub_usb1(monkeypatch)
  if "smbus2" not in sys.modules:
    smbus2 = types.ModuleType("smbus2")
    smbus2.SMBus = type("SMBus", (), {"__init__": lambda self, *a, **k: None})
    monkeypatch.setitem(sys.modules, "smbus2", smbus2)
  interfaces = types.ModuleType("openpilot.selfdrive.car.interfaces")
  interfaces.CarStateBase = _Base
  parser = types.ModuleType("opendbc.can.parser")

  class FakeParser:
    def __init__(self, dbc, messages, bus):
      self.messages = list(messages)
      self.bus = bus

  parser.CANParser = FakeParser
  define = types.ModuleType("opendbc.can.can_define")
  define.CANDefine = _Define
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.car.interfaces", interfaces)
  monkeypatch.setitem(sys.modules, "opendbc.can.parser", parser)
  monkeypatch.setitem(sys.modules, "opendbc.can.can_define", define)
  sys.modules.pop("openpilot.selfdrive.car.tesla.carstate", None)
  mod = importlib.import_module("openpilot.selfdrive.car.tesla.carstate")
  yield mod
  sys.modules.pop("openpilot.selfdrive.car.tesla.carstate", None)


def _ap1_cans(fused_phys, ui_code=6, mpp=25.0, units=0):
  vl = defaultdict(lambda: defaultdict(float))
  vl["ESP_B"]["ESP_vehicleSpeed"] = 40.0
  vl["DI_torque1"]["DI_pedalPos"] = 0.0
  vl["BrakeMessage"]["driverBrakeStatus"] = 1
  vl["EPAS_sysStatus"]["EPAS_handsOnLevel"] = 0
  vl["EPAS_sysStatus"]["EPAS_eacErrorCode"] = 0
  vl["EPAS_sysStatus"]["EPAS_eacStatus"] = 0
  vl["EPAS_sysStatus"]["EPAS_internalSAS"] = 0.0
  vl["EPAS_sysStatus"]["EPAS_torsionBarTorque"] = 0.0
  vl["STW_ANGLHP_STAT"]["StW_AnglHP_Spd"] = 0.0
  vl["DI_state"]["DI_cruiseState"] = 0
  vl["DI_state"]["DI_speedUnits"] = 0
  vl["DI_state"]["DI_digitalSpeed"] = 0
  vl["DI_torque2"]["DI_gear"] = 4
  vl["STW_ACTN_RQ"]["DTR_Dist_Rq"] = 255
  vl["STW_ACTN_RQ"]["SpdCtrlLvr_Stat"] = 0
  for door in ("BC_doorFlStatus", "BC_doorFrStatus", "BC_doorRlStatus", "BC_doorRrStatus"):
    vl["GTW_carState"][door] = 1
  vl["GTW_carState"]["BC_indicatorLStatus"] = 0
  vl["GTW_carState"]["BC_indicatorRStatus"] = 0
  vl["SDM1"]["SDM_bcklDrivStatus"] = 1
  vl["UI_driverAssistMapData"]["UI_mapSpeedLimit"] = ui_code
  vl["UI_gpsVehicleSpeed"]["UI_mppSpeedLimit"] = mpp
  vl["UI_gpsVehicleSpeed"]["UI_mapSpeedLimitUnits"] = units

  vl_cam = defaultdict(lambda: defaultdict(float))
  vl_cam["DAS_control"]["DAS_aebEvent"] = 0
  vl_cam["DAS_control"]["DAS_accState"] = 0
  vl_cam["AutopilotStatus"]["DAS_fusedSpeedLimit"] = fused_phys

  vl_all = defaultdict(lambda: defaultdict(list))
  vl_all_cam = defaultdict(lambda: defaultdict(list))
  vl_all_cam["DAS_control"]["DAS_controlCounter"] = [0]
  cp = SimpleNamespace(vl=vl, vl_all=vl_all, ts_nanos={})
  cp_cam = SimpleNamespace(vl=vl_cam, vl_all=vl_all_cam, ts_nanos={})
  return cp, cp_cam


def _prep_define(CS):
  CS.can_define.dv["EPAS_sysStatus"]["EPAS_eacErrorCode"] = {0: "EAC_ERROR_IDLE"}
  CS.can_define.dv["EPAS_sysStatus"]["EPAS_eacStatus"] = {0: "EAC_IDLE"}
  CS.can_define.dv["DI_state"]["DI_cruiseState"] = {0: "STANDBY"}
  CS.can_define.dv["DI_state"]["DI_speedUnits"] = {0: "MPH"}
  CS.can_define.dv["DI_torque2"]["DI_gear"] = {4: "DI_GEAR_D"}
  for door in ("BC_doorFlStatus", "BC_doorFrStatus", "BC_doorRlStatus", "BC_doorRrStatus"):
    CS.can_define.dv["GTW_carState"][door] = {1: "CLOSED"}


def test_carstate_ap1_sets_dashboard_from_stock_fused_only(carstate_mod):
  from openpilot.selfdrive.car.tesla.values import CAR
  CP = SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS)
  CS = carstate_mod.CarState(CP, None)
  _prep_define(CS)
  cp, cp_cam = _ap1_cans(45.0, ui_code=6, mpp=25.0)
  _ret, fp_ret = CS.update(cp, cp_cam, None)
  assert fp_ret.dashboardSpeedLimit == pytest.approx(45 * MPH)

  # Stock SNA/NONE on fused → UI map fallback (25)
  cp, cp_cam = _ap1_cans(155.0, ui_code=6, mpp=40.0)
  _ret, fp_ret = CS.update(cp, cp_cam, None)
  assert fp_ret.dashboardSpeedLimit == pytest.approx(25 * MPH)


def test_carstate_non_ap1_leaves_dashboard_unset(carstate_mod):
  from openpilot.selfdrive.car.tesla.values import CAR
  CP = SimpleNamespace(carFingerprint=CAR.TESLA_AP2_MODELS)
  CS = carstate_mod.CarState(CP, None)
  _prep_define(CS)
  cp, cp_cam = _ap1_cans(45.0)
  # AP2 update still runs; dashboardSpeedLimit must stay at cereal default 0
  _ret, fp_ret = CS.update(cp, cp_cam, None)
  assert fp_ret.dashboardSpeedLimit == 0.0


def test_parser_lists_ui_msgs_ap1_only_frequency_zero(carstate_mod):
  from openpilot.selfdrive.car.tesla.values import CAR
  ap1 = carstate_mod.CarState.get_can_parser(
    SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS), None)
  other = carstate_mod.CarState.get_can_parser(
    SimpleNamespace(carFingerprint=CAR.TESLA_AP2_MODELS), None)
  ap1_msgs = dict(ap1.messages)
  other_msgs = dict(other.messages)
  assert ap1_msgs["UI_driverAssistMapData"] == 0
  assert ap1_msgs["UI_gpsVehicleSpeed"] == 0
  assert "UI_driverAssistMapData" not in other_msgs
  assert "UI_gpsVehicleSpeed" not in other_msgs

  cam = carstate_mod.CarState.get_cam_can_parser(
    SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS), None)
  cam_names = {n for n, _f in cam.messages}
  assert "AutopilotStatus" in cam_names


def test_dashboard_camera_far_above_map_uses_map():
  # Drive 35 seg 11: DAS_fusedSpeedLimit 80 while UI_mapSpeedLimit code 8 (35) and mpp 35.
  assert dashboard_speed_limit_ms(80.0, 8, 35.0, 0) == pytest.approx(35 * MPH)
  assert dashboard_speed_limit_ms(80.0, 0, 35.0, 0) == pytest.approx(35 * MPH)  # mpp only


def test_dashboard_camera_normal_cases_unchanged():
  assert dashboard_speed_limit_ms(45.0, 6, 25.0, 0) == pytest.approx(45 * MPH)  # +20: sign leads map
  assert dashboard_speed_limit_ms(40.0, 6, 25.0, 0) == pytest.approx(40 * MPH)
  assert dashboard_speed_limit_ms(30.0, 10, 45.0, 0) == pytest.approx(30 * MPH)  # lower camera always wins
  assert dashboard_speed_limit_ms(35.0, 1, 0.0, 0) == pytest.approx(35 * MPH)    # stray map 5: not trusted
  assert dashboard_speed_limit_ms(80.0, 0, 0.0, 0) == pytest.approx(80 * MPH)    # no map: camera as before
  assert dashboard_speed_limit_ms(0.0, 8, 35.0, 0) == pytest.approx(35 * MPH)    # no camera: map as before


def test_dashboard_camera_far_above_map_metric():
  kph = CV.KPH_TO_MS
  assert dashboard_speed_limit_ms(130.0, 0, 50.0, 1) == pytest.approx(50 * kph)
  assert dashboard_speed_limit_ms(80.0, 0, 50.0, 1) == pytest.approx(80 * kph)   # +30 km/h: unchanged
