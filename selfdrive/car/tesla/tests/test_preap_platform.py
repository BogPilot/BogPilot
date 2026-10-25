"""Pre-AP host platform (CAR.TESLA_PREAP_MODELS): car state latch, actuator
frames, safety param mapping and the interface params.

Ported behaviour from NotAutopilot (opendbc cc39c7f), MIT. The parser and
packer .so files are not built for this host, so CarState/CarController are
driven with small stand-ins for cp and TeslaCAN.
"""
from types import SimpleNamespace

import pytest
from cereal import car
from panda import ALTERNATIVE_EXPERIENCE, Panda

from openpilot.selfdrive.car.tesla import preap_carcontroller as pcc
from openpilot.selfdrive.car.tesla import preap_carstate as pcs
from openpilot.selfdrive.car.tesla.safety_flags import learn_pedal_bus, preap_safety_param
from openpilot.selfdrive.car.tesla.values import CAR

GS = car.CarState.GearShifter


# ---- engagement latch ------------------------------------------------------

def _latch():
  return pcs.PreapEngagement()


def _step(e, lever=0, gear=4, doors=False, brake=False, gas=False, sd=False, dog=True, dt=0.01):
  return e.update(dt, lever, gear, doors, brake, gas, sd, dog)


def test_pull_edge_engages_in_drive():
  e = _latch()
  assert not _step(e)
  assert _step(e, lever=2)
  assert _step(e, lever=2)  # held pull stays engaged
  assert _step(e)


@pytest.mark.parametrize("kw", [dict(gear=1), dict(gear=3), dict(doors=True), dict(brake=True), dict(sd=True)])
def test_pull_blocked(kw):
  e = _latch()
  _step(e, **kw)
  assert not _step(e, lever=2, **kw)


def test_held_pull_needs_new_edge():
  e = _latch()
  _step(e, lever=2, brake=True)  # edge while braking: no engage
  assert not _step(e, lever=2)    # still held: no new edge
  _step(e)
  assert _step(e, lever=2)


@pytest.mark.parametrize("kw", [dict(gear=3), dict(doors=True), dict(brake=True)])
def test_disengage_conditions(kw):
  e = _latch()
  _step(e, lever=2)
  assert not _step(e, **kw)


def test_steering_disengage_edge():
  e = _latch()
  _step(e, lever=2)
  assert not _step(e, sd=True)
  _step(e)
  assert _step(e, lever=2)


def test_gas_edge_respects_alt_experience():
  e = _latch()
  _step(e, lever=2)
  assert not _step(e, gas=True)
  e = _latch()
  _step(e, lever=2)
  assert _step(e, gas=True, dog=False)


def test_cancel_echo_window():
  e = _latch()
  _step(e, lever=2)
  assert _step(e, lever=1)          # stalk echo inside 600 ms is ignored
  for _ in range(60):
    _step(e)
  assert not _step(e, lever=1)


def test_epas_rejecting_codes():
  assert all(pcs.epas_rejecting(0, c) for c in (6, 7, 8, 9))
  assert not pcs.epas_rejecting(0, 3)
  assert not pcs.epas_rejecting(2, 6)


# ---- update_preap ----------------------------------------------------------

def _vl(**over):
  vl = {
    "ESP_B": {"ESP_vehicleSpeed": 50.0},
    "DI_torque1": {"DI_pedalPos": 0.0},
    "DI_torque2": {"DI_brakePedal": 0, "DI_gear": 4},
    "BrakeMessage": {"driverBrakeStatus": 1},
    "EPAS_sysStatus": {"EPAS_handsOnLevel": 0, "EPAS_eacStatus": 1, "EPAS_eacErrorCode": 0,
                       "EPAS_internalSAS": -3.0, "EPAS_torsionBarTorque": 0.0},
    "STW_ANGLHP_STAT": {"StW_AnglHP_Spd": 0.0},
    "DI_state": {"DI_speedUnits": 0, "DI_digitalSpeed": 31},
    "STW_ACTN_RQ": {"SpdCtrlLvr_Stat": 0},
    "GTW_carState": {d: 0 for d in pcs.DOORS} | {"BC_indicatorLStatus": 0, "BC_indicatorRStatus": 0},
  }
  for msg, sigs in over.items():
    vl[msg] = vl[msg] | sigs
  return SimpleNamespace(vl=vl)


class _CS:
  def __init__(self, alt=0):
    self.CP = SimpleNamespace(alternativeExperience=alt)
    self.preap_engagement = pcs.PreapEngagement()
    self.steering_pressed_cnt = 0

  def update_speed_kf(self, v):
    return v, 0.0

  def update_steering_pressed(self, pressed, n):
    self.steering_pressed_cnt = max(0, min(2 * n, self.steering_pressed_cnt + (1 if pressed else -1)))
    return self.steering_pressed_cnt > n


def test_update_preap_engages_and_reports():
  cs = _CS()
  ret = pcs.update_preap(cs, _vl())
  assert not ret.cruiseState.enabled and ret.cruiseState.available
  ret = pcs.update_preap(cs, _vl(STW_ACTN_RQ={"SpdCtrlLvr_Stat": 2}))
  assert ret.cruiseState.enabled
  assert ret.gearShifter == GS.drive
  assert ret.steeringAngleDeg == pytest.approx(3.0)
  assert not ret.brakePressed and not ret.gasPressed and not ret.doorOpen
  assert not ret.steerFaultTemporary and not ret.seatbeltUnlatched


@pytest.mark.parametrize("over", [
  {"BrakeMessage": {"driverBrakeStatus": 2}},
  {"DI_torque2": {"DI_brakePedal": 1, "DI_gear": 4}},
  {"EPAS_sysStatus": {"EPAS_handsOnLevel": 2}},
  {"EPAS_sysStatus": {"EPAS_eacStatus": 0, "EPAS_eacErrorCode": 7}},
  {"GTW_carState": {"DOOR_STATE_RR": 1}},
  {"DI_torque2": {"DI_gear": 3}},
])
def test_update_preap_disengages(over):
  cs = _CS()
  pcs.update_preap(cs, _vl())
  assert pcs.update_preap(cs, _vl(STW_ACTN_RQ={"SpdCtrlLvr_Stat": 2})).cruiseState.enabled
  assert not pcs.update_preap(cs, _vl(**over)).cruiseState.enabled


def test_update_preap_gas_with_disable_disengage_on_gas():
  cs = _CS(alt=ALTERNATIVE_EXPERIENCE.DISABLE_DISENGAGE_ON_GAS)
  pcs.update_preap(cs, _vl())
  pcs.update_preap(cs, _vl(STW_ACTN_RQ={"SpdCtrlLvr_Stat": 2}))
  ret = pcs.update_preap(cs, _vl(DI_torque1={"DI_pedalPos": 10.0}))
  assert ret.gasPressed and ret.cruiseState.enabled


def test_eac_inhibited_is_not_a_fault():
  ret = pcs.update_preap(_CS(), _vl(EPAS_sysStatus={"EPAS_eacStatus": 0}))
  assert not ret.steerFaultTemporary and not ret.steerFaultPermanent
  ret = pcs.update_preap(_CS(), _vl(EPAS_sysStatus={"EPAS_eacStatus": 3}))
  assert ret.steerFaultPermanent


# ---- actuator frames -------------------------------------------------------

class _TeslaCAN:
  def create_steering_control(self, angle, enabled, counter):
    return [0x488, 0, ("steer", round(angle, 3), enabled, counter), 0]


def _cc(lat=False, angle=0.0):
  return SimpleNamespace(latActive=lat, actuators=SimpleNamespace(steeringAngleDeg=angle))


def _ccs(hands=0, meas=5.0, v=20.0):
  return SimpleNamespace(hands_on_level=hands, out=SimpleNamespace(steeringAngleDeg=meas, vEgoRaw=v))


def test_frames_cadence_and_counter():
  c = pcc.PreapController(_TeslaCAN())
  addrs = [[m[0] for m in c.update(_cc(), _ccs(), f)] for f in range(4)]
  assert addrs == [[0x488, 0x214], [], [0x488, 0x214], []]
  m = c.update(_cc(), _ccs(), 30)
  assert m[0][2][3] == 15 and m[1][2][1] == 15


def test_inactive_tracks_measured_and_type_none():
  c = pcc.PreapController(_TeslaCAN())
  steer = c.update(_cc(lat=False, angle=40.0), _ccs(meas=5.0), 0)[0][2]
  assert steer[1] == 5.0 and steer[2] is False


def test_active_rate_limited_and_hands_pause():
  c = pcc.PreapController(_TeslaCAN())
  c.update(_cc(), _ccs(meas=0.0), 0)
  steer = c.update(_cc(lat=True, angle=40.0), _ccs(meas=0.0, v=20.0), 2)[0][2]
  assert steer[2] is True and 0.0 < steer[1] <= 0.3 + 1e-6
  steer = c.update(_cc(lat=True, angle=40.0), _ccs(hands=3, meas=1.0), 4)[0][2]
  assert steer[2] is False and steer[1] == 1.0


def test_active_clamped_to_360():
  c = pcc.PreapController(_TeslaCAN())
  c.apply_angle_last = 359.0
  steer = c.update(_cc(lat=True, angle=900.0), _ccs(v=0.0), 0)[0][2]
  assert steer[1] == 360.0


def test_epas_control_frame():
  addr, _, dat, bus = pcc.create_epas_control(5, 1)
  assert (addr, bus, len(dat)) == (0x214, 0, 3)
  assert dat[0] & 0x07 == 1 and dat[1] & 0x0F == 5
  assert dat[2] == (0x14 + 0x02 + dat[0] + dat[1]) & 0xFF


def test_radar_vin_frames_only_with_radar():
  c = pcc.PreapController(_TeslaCAN())
  assert all(m[0] != 0x560 for f in range(30) for m in c.update(_cc(), _ccs(), f))
  c = pcc.PreapController(_TeslaCAN(), {"vin": "", "position": 1, "epas_type": 2})
  frags = [m[2] for f in range(30) for m in c.update(_cc(), _ccs(), f) if m[0] == 0x560]
  assert [d[0] for d in frags] == [0, 1, 2]
  assert frags[0][2] == 1 + (1 << 1) + (2 << 3)
  assert frags[1][1:] == b" " * 7  # empty VIN -> spaces (this-car passthrough)


def test_radar_config_follows_safety_flag(tmp_path):
  cfg = SimpleNamespace(safetyParam=0)
  assert pcc.preap_radar_config(SimpleNamespace(safetyConfigs=[cfg]), tmp_path) is None
  (tmp_path / "PreAPRadarPosition").write_text("2")
  cfg = SimpleNamespace(safetyParam=Panda.FLAG_TESLA_PREAP_RADAR_EMULATION)
  out = pcc.preap_radar_config(SimpleNamespace(safetyConfigs=[cfg]), tmp_path)
  assert out == {"vin": "", "position": 2, "epas_type": 0}


# ---- safety param mapping --------------------------------------------------

def test_learn_pedal_bus():
  assert learn_pedal_bus({0: {}, 2: {}}) is None
  assert learn_pedal_bus({0: {0x552: 6}}) == 0
  assert learn_pedal_bus({2: {0x552: 6}}) == 2
  assert learn_pedal_bus({0: {0x552: 6}, 2: {0x552: 6}}) is None


def test_preap_safety_param():
  assert preap_safety_param({}) == 0
  assert preap_safety_param({"radar_enabled": True}) == Panda.FLAG_TESLA_PREAP_RADAR_EMULATION
  assert preap_safety_param({"radar_enabled": True, "radar_behind_nosecone": True}) == (
    Panda.FLAG_TESLA_PREAP_RADAR_EMULATION | Panda.FLAG_TESLA_PREAP_RADAR_BEHIND_NOSECONE)
  assert preap_safety_param({"radar_behind_nosecone": True}) == 0
  assert preap_safety_param({"pedal_enabled": True, "pedal_can_bus": 0}) == Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO
  assert preap_safety_param({"pedal_enabled": True, "pedal_can_bus": 2}) == 0
  assert preap_safety_param({"pedal_enabled": True, "pedal_can_bus": 2}, 0) == Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO
  assert preap_safety_param({"pedal_enabled": False, "pedal_can_bus": 0}) == 0
  # pedal TX and calibration are never enabled from the host
  for s in ({"pedal_enabled": True}, {"pedal_enabled": True, "radar_enabled": True}):
    p = preap_safety_param(s, 0)
    assert not p & Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL
    assert not p & Panda.FLAG_TESLA_PREAP_PEDAL_CALIBRATION


# ---- interface -------------------------------------------------------------

@pytest.fixture
def iface(monkeypatch):
  import importlib
  import sys
  import types
  stub = types.ModuleType("openpilot.selfdrive.car.interfaces")
  stub.CarInterfaceBase = type("CarInterfaceBase", (), {})
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.car.interfaces", stub)
  sys.modules.pop("openpilot.selfdrive.car.tesla.interface", None)
  yield importlib.import_module("openpilot.selfdrive.car.tesla.interface").CarInterface
  sys.modules.pop("openpilot.selfdrive.car.tesla.interface", None)


def test_interface_preap_params(monkeypatch, iface):
  from openpilot.selfdrive.car.tesla import preap_settings
  CarInterface = iface
  monkeypatch.setattr(preap_settings, "load_preap_settings",
                      lambda *a, **k: ({"radar_enabled": True, "pedal_enabled": True, "pedal_can_bus": 2}, {}))
  ret = CarInterface._get_params(car.CarParams.new_message(), CAR.TESLA_PREAP_MODELS, {0: {0x552: 6}, 2: {}},
                                 [], False, False, SimpleNamespace(disable_openpilot_long=False))
  assert [c.safetyModel for c in ret.safetyConfigs] == [car.CarParams.SafetyModel.teslaPreap]
  assert ret.safetyConfigs[0].safetyParam == (Panda.FLAG_TESLA_PREAP_RADAR_EMULATION |
                                              Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO)
  assert ret.pcmCruise and not ret.openpilotLongitudinalControl and not ret.dashcamOnly
  assert not ret.radarUnavailable and not ret.enableBsm
  assert ret.steerControlType == car.CarParams.SteerControlType.angle


def test_interface_ap1_unchanged(iface):
  CarInterface = iface
  ret = CarInterface._get_params(car.CarParams.new_message(), CAR.TESLA_AP1_MODELS, {0: {}, 2: {}},
                                 [], False, False, SimpleNamespace(disable_openpilot_long=True))
  assert [c.safetyModel for c in ret.safetyConfigs] == [car.CarParams.SafetyModel.tesla]
  assert ret.safetyConfigs[0].safetyParam == Panda.FLAG_TESLA_AP1 | Panda.FLAG_TESLA_LONG_CONTROL
  assert ret.enableBsm


def test_platform_registered():
  assert CAR.TESLA_PREAP_MODELS.value == "TESLA_PREAP_MODELS"
  from openpilot.selfdrive.car.tesla.preap_profile import PREAP_PLATFORM
  assert PREAP_PLATFORM == CAR.TESLA_PREAP_MODELS
