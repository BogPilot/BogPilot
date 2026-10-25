"""Pre-AP pedal longitudinal (preap_pedal.py and its wiring).

Behaviour ported from NotAutopilot (nap-release e99f4dc, opendbc cc39c7f) and
BogGyver / Tinkla (0d99a1b), MIT. The parser and packer .so files are not built
on this host, so frames are checked against the DBC text with a small decoder
and CarState / CarController run on stand-ins.
"""
import math
import os
import re
from types import SimpleNamespace

import crcmod
import pytest
from cereal import car
from panda import Panda

from openpilot.selfdrive.car.tesla import preap_carcontroller as pcc
from openpilot.selfdrive.car.tesla import preap_carstate as pcs
from openpilot.selfdrive.car.tesla import preap_pedal as pp
from openpilot.selfdrive.car.tesla.safety_flags import preap_safety_param
from openpilot.selfdrive.car.tesla.values import CAR

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
BT = car.CarState.ButtonEvent.Type
LCS = car.CarControl.Actuators.LongControlState

CALIB = {"pedal_enabled": True, "pedal_can_bus": 2, "pedal_calib_done": True,
         "pedal_calib_zero": 12.0, "pedal_calib_factor": 1.0, "pedal_calib_min": -3.0, "pedal_calib_max": 99.6}


# ---- DBC text helpers --------------------------------------------------------

def _dbc_signals(path, msg_id):
  sigs, cur = {}, None
  for line in open(path, encoding="latin-1"):
    m = re.match(r"^BO_ (\d+) ", line)
    if m:
      cur = int(m.group(1))
      continue
    m = re.match(r"^ SG_ (\w+) : (\d+)\|(\d+)@([01])([+-]) \(([^,]+),([^)]+)\)", line)
    if m and cur == msg_id:
      sigs[m.group(1)] = (int(m.group(2)), int(m.group(3)), m.group(4) == "1", float(m.group(6)), float(m.group(7)))
  return sigs


def _decode(dat, start, size, little):
  if little:
    v = int.from_bytes(dat, "little")
    return (v >> start) & ((1 << size) - 1)
  # Motorola: start is the MSB position (DBC bit numbering)
  v = int.from_bytes(dat, "big")
  msb = (start // 8) * 8 + (7 - start % 8)
  return (v >> (len(dat) * 8 - 1 - msb - size + 1)) & ((1 << size) - 1)


PEDAL_DBC = os.path.join(ROOT, "opendbc", "tesla_preap_pedal.dbc")
TESLA_DBC = os.path.join(ROOT, "opendbc", "tesla_can.dbc")


def test_pedal_dbc_ids_and_scaling():
  cmd = _dbc_signals(PEDAL_DBC, pp.GAS_COMMAND_ADDR)
  sen = _dbc_signals(PEDAL_DBC, pp.GAS_SENSOR_ADDR)
  assert cmd["GAS_COMMAND"][3:] == (pp.PEDAL_M1, pp.PEDAL_D)
  assert cmd["GAS_COMMAND2"][3:] == (pp.PEDAL_M2, pp.PEDAL_D)
  assert sen["INTERCEPTOR_GAS"][3:] == (pp.PEDAL_M1, pp.PEDAL_D)
  assert set(sen) == {"INTERCEPTOR_GAS", "INTERCEPTOR_GAS2", "STATE", "IDX", "CHECKSUM"}
  # No comment line may look like a DBC definition to opendbc's line parser.
  text = open(PEDAL_DBC).read()
  assert len(re.findall(r"^BO_ ", text, re.M)) == 2
  assert not re.search(r"^ SG_ (CHECKSUM|COUNTER)_PEDAL ", text, re.M)


def test_gas_command_matches_dbc_layout():
  sigs = _dbc_signals(PEDAL_DBC, pp.GAS_COMMAND_ADDR)
  addr, _, dat, bus = pp.create_gas_command(1500, 750, True, 9, 2)
  assert (addr, bus, len(dat)) == (0x551, 2, 6)
  got = {n: _decode(dat, s[0], s[1], s[2]) for n, s in sigs.items()}
  assert got == {"GAS_COMMAND": 1500, "GAS_COMMAND2": 750, "ENABLE": 1, "IDX": 9,
                 "CHECKSUM": pp.pedal_checksum(0x551, dat)}


def test_gas_command_disabled_is_zero():
  _, _, dat, _ = pp.create_gas_command(1500, 750, False, 3, 0)
  assert dat[:4] == b"\x00\x00\x00\x00" and dat[4] == 3
  assert dat[5] == (0x51 + 0x05 + 3) & 0xFF


def test_checksum_vector():
  # addr bytes 0x05 + 0x52, data 0x10 0x20 0x08 0x10 0x05
  assert pp.pedal_checksum(0x552, bytes((0x10, 0x20, 0x08, 0x10, 0x05))) == (0x57 + 0x10 + 0x20 + 0x08 + 0x10 + 0x05) & 0xFF


def test_raw_consistency_and_caps():
  for v in (0.0, 5.0, 12.0, 30.0, 45.0):
    for phys in (-10.0, 0.0, 40.0, 80.0, 200.0):
      r1, r2 = pp.pedal_raw(phys, v, 99.6)
      assert 0 <= r1 <= pp.max_gas_raw(v)
      assert abs(2 * r2 - r1) <= 4
  assert pp.max_gas_raw(0.0) == 1631 - pp.PANDA_GAS_MARGIN_RAW
  # calib max caps the physical value
  r1, _ = pp.pedal_raw(80.0, 40.0, 30.0)
  assert r1 == int((30.0 - pp.PEDAL_D) / pp.PEDAL_M1)


def test_host_constants_match_panda():
  h = open(os.path.join(ROOT, "panda", "board", "safety", "safety_tesla_preap.h")).read()
  assert int(re.search(r"#define PREAP_PEDAL_GAS_PRESSED (\d+)", h).group(1)) == pp.PEDAL_GAS_PRESSED_RAW
  tbl = re.search(r"PREAP_PEDAL_MAX_GAS = \{\s*\{([^}]*)\},\s*\{([^}]*)\}", h)
  assert [float(x) for x in tbl.group(1).split(",")] == pp.PANDA_MAX_GAS_BP
  assert [float(x) for x in tbl.group(2).split(",")] == pp.PANDA_MAX_GAS_RAW
  assert int(re.search(r"PREAP_PEDAL_SENSOR_TIMEOUT_US (\d+)", h).group(1)) == pp.PEDAL_SENSOR_TIMEOUT_S * 1e6
  assert Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL == 1 and Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO == 32


# ---- stalk cancel ------------------------------------------------------------

STW_LIVE = {"SpdCtrlLvr_Stat": 0, "VSL_Enbl_Rq": 1, "SpdCtrlLvrStat_Inv": 1, "DTR_Dist_Rq": 100,
            "TurnIndLvr_Stat": 1, "HiBmLvr_Stat": 0, "WprWashSw_Psd": 0, "WprWash_R_Sw_Posn_V2": 2,
            "StW_Lvr_Stat": 0, "StW_Cond_Flt": 0, "StW_Cond_Psd": 1, "HrnSw_Psd": 0,
            "StW_Sw00_Psd": 1, "StW_Sw03_Psd": 1, "StW_Sw09_Psd": 1, "StW_Sw15_Psd": 1,
            "WprSw6Posn": 2, "MC_STW_ACTN_RQ": 15, "CRC_STW_ACTN_RQ": 0}


def test_stalk_cancel_copies_live_frame():
  sigs = _dbc_signals(TESLA_DBC, 69)
  assert "MC_STW_ACTN_RQ" in sigs and "SpdCtrlLvr_Stat" in sigs
  addr, _, dat, bus = pp.create_stalk_cancel(STW_LIVE)
  assert (addr, bus, len(dat)) == (0x45, 0, 8)
  got = {n: _decode(dat, s[0], s[1], s[2]) for n, s in sigs.items()}
  for name, (_, _, little, _, _) in sigs.items():
    assert little, name
    if name == "SpdCtrlLvr_Stat":
      assert got[name] == 1
    elif name == "SpdCtrlLvrStat_Inv":
      assert got[name] == 0
    elif name == "MC_STW_ACTN_RQ":
      assert got[name] == 0   # 15 + 1 wraps
    elif name == "CRC_STW_ACTN_RQ":
      crc = crcmod.mkCrcFun(0x11d, initCrc=0x00, rev=False, xorOut=0xff)
      assert got[name] == crc(dat[:7]) == pp.crc8_stw(dat[:7])
    else:
      assert got[name] == STW_LIVE.get(name, 0), name


# ---- gate --------------------------------------------------------------------

def test_calibration_rules():
  assert pp.pedal_calibration(CALIB) is not None
  bad = [dict(pedal_calib_done=False), dict(pedal_calib_zero=None), dict(pedal_calib_factor=0.0),
         dict(pedal_calib_factor=-1.0), dict(pedal_calib_zero=0.0, pedal_calib_factor=1.0),
         dict(pedal_calib_zero=math.nan), dict(pedal_calib_max=5.0), dict(pedal_calib_factor="x")]
  for b in bad:
    assert pp.pedal_calibration(CALIB | b) is None, b
  c = pp.pedal_calibration(CALIB | {"pedal_calib_factor": 2.0})
  for di in (-5.0, 0.0, 33.0):
    assert c.pedal_to_di(c.di_to_pedal(di)) == pytest.approx(di)
  assert c.di_to_pedal(0.0) == pytest.approx(12.0 - 0.5)


def test_pedal_long_bus_gate():
  assert pp.pedal_long_bus(CALIB, 0, True) == 0
  assert pp.pedal_long_bus(CALIB, 2, True) == 2
  assert pp.pedal_long_bus(CALIB, None, True) is None
  assert pp.pedal_long_bus(CALIB, 0, False) is None
  assert pp.pedal_long_bus(CALIB | {"pedal_enabled": False}, 0, True) is None
  assert pp.pedal_long_bus(CALIB | {"pedal_calib_done": False}, 0, True) is None
  assert pp.pedal_long_bus({}, 0, True) is None


def test_safety_param_enable_pedal_only_when_asked():
  assert not preap_safety_param(CALIB, 0) & Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL
  p = preap_safety_param(CALIB, 0, pedal_long=True)
  assert p == Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL | Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO
  assert preap_safety_param(CALIB, 2, pedal_long=True) == Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL
  assert not preap_safety_param(CALIB, 0, pedal_long=True) & Panda.FLAG_TESLA_PREAP_PEDAL_CALIBRATION


@pytest.fixture
def iface(monkeypatch):
  import importlib
  import sys
  import types
  stub = types.ModuleType("openpilot.selfdrive.car.interfaces")
  stub.CarInterfaceBase = type("CarInterfaceBase", (), {})
  stub.ACCEL_MIN, stub.ACCEL_MAX = -3.5, 2.0
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.car.interfaces", stub)
  sys.modules.pop("openpilot.selfdrive.car.tesla.interface", None)
  yield importlib.import_module("openpilot.selfdrive.car.tesla.interface").CarInterface
  sys.modules.pop("openpilot.selfdrive.car.tesla.interface", None)


def _params(monkeypatch, iface, settings, fp, long_off=False):
  from openpilot.selfdrive.car.tesla import preap_settings
  monkeypatch.setattr(preap_settings, "load_preap_settings", lambda *a, **k: (settings, {}))
  cp = car.CarParams.new_message()
  cp.carFingerprint = CAR.TESLA_PREAP_MODELS
  return iface._get_params(cp, CAR.TESLA_PREAP_MODELS, fp, [], False, False,
                           SimpleNamespace(disable_openpilot_long=long_off))


def test_interface_gate_on(monkeypatch, iface):
  ret = _params(monkeypatch, iface, CALIB, {0: {}, 2: {0x552: 6}})
  assert ret.safetyConfigs[0].safetyParam == Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL
  assert ret.openpilotLongitudinalControl and not ret.pcmCruise
  assert list(ret.longitudinalTuning.kiV) == pytest.approx(pp.PEDAL_LONG_KI_V)
  assert list(ret.longitudinalTuning.kpV) == [0.0] * 4
  assert ret.longitudinalActuatorDelay == pytest.approx(0.4)
  assert ret.vEgoStopping == pytest.approx(0.1) and ret.stoppingDecelRate == pytest.approx(1.0)
  assert pp.preap_pedal_bus_from_cp(ret) == 2


@pytest.mark.parametrize("settings,fp,long_off", [
  (CALIB, {0: {}, 2: {0x552: 6}}, True),                              # toggle off
  (CALIB, {0: {}, 2: {}}, False),                                     # pedal not seen
  (CALIB, {0: {0x552: 6}, 2: {0x552: 6}}, False),                     # ambiguous bus
  (CALIB | {"pedal_calib_done": False}, {0: {}, 2: {0x552: 6}}, False),
  (CALIB | {"pedal_enabled": False}, {0: {}, 2: {0x552: 6}}, False),
])
def test_interface_gate_off_is_stock(monkeypatch, iface, settings, fp, long_off):
  ret = _params(monkeypatch, iface, settings, fp, long_off)
  assert not ret.safetyConfigs[0].safetyParam & Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL
  assert ret.pcmCruise and not ret.openpilotLongitudinalControl
  assert ret.longitudinalActuatorDelay == pytest.approx(0.5)
  assert pp.preap_pedal_bus_from_cp(ret) is None


def test_pid_accel_limits(monkeypatch, iface):
  monkeypatch.setattr(pp.ACCEL_LIMITS, "adaptive", False)
  ret = _params(monkeypatch, iface, CALIB, {0: {}, 2: {0x552: 6}})
  lo, hi = iface.get_pid_accel_limits(ret, 15.0, 30.0)
  assert lo == pp.REGEN_MAX and hi == pytest.approx(0.9)
  ap1 = SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS, openpilotLongitudinalControl=True)
  assert iface.get_pid_accel_limits(ap1, 15.0, 30.0) == (-3.5, 2.0)


def test_accel_profiles():
  assert pp.accel_max(15.0, 0, True) == pytest.approx(1.0)
  assert pp.accel_max(15.0, 2, True) == pytest.approx(0.8)
  assert pp.accel_max(15.0, 0, False) == pytest.approx(0.9)
  assert pp.accel_max(15.0, "bad", True) == pytest.approx(0.9)


def test_profile_status_text():
  from openpilot.selfdrive.car.tesla.preap_settings import profile_status
  from openpilot.selfdrive.car.tesla.preap_profile import PREAP_PLATFORM
  s = profile_status(PREAP_PLATFORM, "can", CALIB, pedal_bus_learned=0)["pedal"]
  assert "bus 0 (learned)" in s and "openpilot longitudinal" in s
  s = profile_status(PREAP_PLATFORM, "can", CALIB | {"pedal_calib_done": False}, pedal_bus_learned=0)["pedal"]
  assert "calibration missing, stock cruise" in s
  s = profile_status(PREAP_PLATFORM, "can", CALIB, pedal_bus_learned=None)["pedal"]
  assert "not seen" in s


# ---- sensor ------------------------------------------------------------------

def _sensor_vals(raw1=0, state=0, idx=0, bad_ck=False):
  raw2 = raw1 // 2
  dat = bytes(((raw1 >> 8) & 0xFF, raw1 & 0xFF, (raw2 >> 8) & 0xFF, raw2 & 0xFF, (state << 4) | idx))
  ck = pp.pedal_checksum(0x552, dat) ^ (0xFF if bad_ck else 0)
  return {"INTERCEPTOR_GAS": raw1 * pp.PEDAL_M1 + pp.PEDAL_D, "INTERCEPTOR_GAS2": raw2 * pp.PEDAL_M2 + pp.PEDAL_D,
          "STATE": state, "IDX": idx, "CHECKSUM": ck}


def _feed(sensor, n, raw1=0, state=0, bad_ck=False, start=0):
  # 50 Hz sensor sampled at 100 Hz: IDX advances every other step
  for i in range(n):
    sensor.update(_sensor_vals(raw1, state, ((start + i) // 2) % 16, bad_ck), 0.01)
  return sensor


def test_sensor_health():
  s = _feed(pp.PedalSensor(), 10)
  assert s.healthy and not s.faulted and not s.gas_pressed
  s = pp.PedalSensor()
  for _ in range(30):
    s.update(_sensor_vals(0, 0, 3), 0.01)   # IDX stuck
  assert not s.healthy and s.faulted
  assert not _feed(pp.PedalSensor(), 10, state=1).healthy
  assert not _feed(pp.PedalSensor(), 10, bad_ck=True).healthy
  assert not pp.PedalSensor().update({}, 0.01).healthy


def test_sensor_gas_pressed():
  assert _feed(pp.PedalSensor(), 4, raw1=651).gas_pressed
  assert not _feed(pp.PedalSensor(), 4, raw1=650).gas_pressed


def test_lever_buttons():
  assert pp.lever_buttons(16, 0) == [("accel", True)]
  assert pp.lever_buttons(0, 16) == [("accel", False)]
  assert pp.lever_buttons(8, 0) == [("decel", True)]
  assert pp.lever_buttons(2, 0) == [] and pp.lever_buttons(1, 0) == []


# ---- controller --------------------------------------------------------------

class _CS:
  def __init__(self, v=15.0):
    self.pedal_sensor = _feed(pp.PedalSensor(), 10)
    self.out = SimpleNamespace(vEgo=v, vEgoRaw=v, steeringAngleDeg=0.0)
    self.stock_cc_active = False
    self.stock_cc_cancelable = False
    self.stalk_fresh = True
    self.motor_torque = 0.0
    self.msg_stw_actn_req = dict(STW_LIVE)
    self.hands_on_level = 0


def _cc(enabled=True, long_active=True, accel=0.0, cancel=False, state=LCS.pid):
  return SimpleNamespace(enabled=enabled, longActive=long_active, latActive=enabled,
                         actuators=SimpleNamespace(accel=accel, longControlState=state, steeringAngleDeg=0.0),
                         cruiseControl=SimpleNamespace(cancel=cancel))


def _ctrl():
  return pp.PedalLongController(pp.pedal_calibration(CALIB), 2)


def _gas_frames(sends):
  return [s for s in sends if s[0] == 0x551]


def _raw1(msg):
  return (msg[2][0] << 8) | msg[2][1]


@pytest.mark.parametrize("why", ["disabled", "no_long", "unhealthy", "gas", "stock_cc"])
def test_controller_enable_requires_everything(why):
  c, cs = _ctrl(), _CS()
  cc = _cc()
  if why == "disabled":
    cc = _cc(enabled=False, long_active=False)
  elif why == "no_long":
    cc = _cc(long_active=False)
  elif why == "unhealthy":
    cs.pedal_sensor = pp.PedalSensor()
  elif why == "gas":
    cs.pedal_sensor = _feed(pp.PedalSensor(), 10, raw1=900)
  elif why == "stock_cc":
    cs.stock_cc_active = True
  for f in range(0, 200, 2):
    m = _gas_frames(c.update(cc, cs, f))[0]
    assert m[2][4] >> 7 == 0 and m[2][:4] == b"\x00\x00\x00\x00"


def test_controller_sends_every_other_frame_with_idx():
  c, cs = _ctrl(), _CS()
  idx = []
  for f in range(20):
    g = _gas_frames(c.update(_cc(accel=1.0), cs, f))
    assert len(g) == (1 if f % 2 == 0 else 0)
    idx += [m[2][4] & 0x0F for m in g]
    assert all(m[3] == 2 for m in g)
  assert idx == [i % 16 for i in range(10)]


def test_controller_grace_ramp_and_cap():
  c, cs = _ctrl(), _CS(v=40.0)
  raws, dis = [], []
  for f in range(0, 400, 2):
    m = _gas_frames(c.update(_cc(accel=2.5), cs, f))[0]
    assert m[2][4] >> 7 == 1
    raws.append(_raw1(m))
    dis.append(c.prev_di)
  # grace: first frames limited, ramp up at most 5 DI per step
  assert all(b - a <= pp.PEDAL_RAMP_RATE_UP + 1e-6 for a, b in zip(dis, dis[1:], strict=False))
  assert max(raws) <= pp.max_gas_raw(40.0)
  # regen: down ramp limited too, and never below PEDAL_DI_MIN
  for f in range(400, 800, 2):
    c.update(_cc(accel=-3.0), cs, f)
    dis.append(c.prev_di)
  assert all(a - b <= pp.PEDAL_RAMP_RATE_DOWN + 1e-6 for a, b in zip(dis, dis[1:], strict=False))
  assert min(dis) >= pp.PEDAL_DI_MIN


def test_controller_grace_blocks_regen_at_engage():
  c, cs = _ctrl(), _CS()
  c.update(_cc(accel=-1.5), cs, 0)
  assert c.prev_di >= pp.PEDAL_DI_ZERO


def test_compute_pedal_di_hysteresis_and_deadband():
  assert pp.compute_pedal_di(0.1, 20.0, 0.0, 0.0) == 0.0          # deadband
  assert pp.compute_pedal_di(0.2, 20.0, 10.0, 0.0) == pytest.approx(7.5)   # target 5.9, ramp down 2.5
  assert pp.compute_pedal_di(0.2, 20.0, 5.5, 0.0) == 5.5          # under the 1.0 hysteresis gap
  assert pp.compute_pedal_di(2.5, 20.0, 0.0, 0.0) == pytest.approx(5.0)    # ramp up 5


def test_zero_torque_learner_bounds():
  z = pp.PedalZeroTorque()
  z.update(-5.0, 3.0, 10.0)
  assert z.get(10.0) == 3.0 and z.get(1.0) == 0.0
  z.update(-1.0, 40.0, 10.0)   # out of bounds: ignored
  assert z.get(10.0) == 3.0
  z.update(-1.0, 4.0, 2.0)     # too slow: ignored
  assert z.get(10.0) == 3.0


def test_cancel_only_when_enabled_and_cancelable():
  c, cs = _ctrl(), _CS()
  cs.stock_cc_active = cs.stock_cc_cancelable = True
  assert not [s for s in c.update(_cc(enabled=False, long_active=False, cancel=True), cs, 0) if s[0] == 0x45]
  sends = c.update(_cc(cancel=True), cs, 2)
  assert [s for s in sends if s[0] == 0x45]
  # same live counter: no second copy; new counter but inside 10 frames: none
  assert not [s for s in c.update(_cc(cancel=True), cs, 14) if s[0] == 0x45]
  cs.msg_stw_actn_req = dict(STW_LIVE, MC_STW_ACTN_RQ=0)
  assert not [s for s in c.update(_cc(cancel=True), cs, 4) if s[0] == 0x45]
  assert [s for s in c.update(_cc(cancel=True), cs, 16) if s[0] == 0x45]
  cs.msg_stw_actn_req = dict(STW_LIVE, MC_STW_ACTN_RQ=1)
  cs.stalk_fresh = False
  assert not [s for s in c.update(_cc(cancel=True), cs, 40) if s[0] == 0x45]
  cs.stalk_fresh = True
  cs.stock_cc_cancelable = False
  assert not [s for s in c.update(_cc(cancel=True), cs, 60) if s[0] == 0x45]


def test_brake_alert():
  b = pp.BrakeAlert()
  assert not any(b.update(True, -2.5, False, 10.0) for _ in range(pp.FCW_FRAMES - 1))
  assert b.update(True, -2.5, False, 10.0)
  assert not b.update(False, -2.5, False, 10.0)
  b = pp.BrakeAlert()
  assert not any(b.update(True, -1.0, True, 2.0) for _ in range(pp.STOP_ALERT_FRAMES - 1))
  assert b.update(True, -1.0, True, 2.0)
  assert not pp.BrakeAlert().update(True, -1.0, True, 0.1)


def test_engage_events():
  assert pp.pedal_engage_events(True, False) == ["buttonEnable"]
  assert pp.pedal_engage_events(False, True) == ["buttonCancel"]
  assert pp.pedal_engage_events(True, True) == []


# ---- PreapController wiring ---------------------------------------------------

class _TeslaCAN:
  def create_steering_control(self, angle, enabled, cntr):
    return [0x488, 0, bytes(4), 0]


def test_preap_controller_without_pedal_sends_no_long():
  c = pcc.PreapController(_TeslaCAN())
  cs = _CS()
  addrs = {s[0] for f in range(20) for s in c.update(_cc(cancel=True), cs, f)}
  assert addrs == {0x488, 0x214}
  assert not c.pedal_missing and not c.brake_alert


def test_preap_controller_with_pedal():
  c = pcc.PreapController(_TeslaCAN(), pedal=_ctrl(), pedal_required=True)
  cs = _CS()
  addrs = {s[0] for f in range(20) for s in c.update(_cc(accel=0.5), cs, f)}
  assert addrs == {0x488, 0x214, 0x551}
  assert pcc.PreapController(_TeslaCAN(), pedal=None, pedal_required=True).pedal_missing


def test_preap_pedal_config(monkeypatch):
  cp_off = SimpleNamespace(carFingerprint=CAR.TESLA_PREAP_MODELS, safetyConfigs=[SimpleNamespace(safetyParam=0)])
  assert pcc.preap_pedal_config(cp_off) == (False, None)
  cp_on = SimpleNamespace(carFingerprint=CAR.TESLA_PREAP_MODELS,
                          safetyConfigs=[SimpleNamespace(safetyParam=Panda.FLAG_TESLA_PREAP_ENABLE_PEDAL |
                                                         Panda.FLAG_TESLA_PREAP_PEDAL_BUS_ZERO)])
  from openpilot.selfdrive.car.tesla import preap_settings
  monkeypatch.setattr(preap_settings, "load_preap_settings", lambda *a, **k: (CALIB, {}))
  req, ctrl = pcc.preap_pedal_config(cp_on)
  assert req and ctrl.bus == 0
  monkeypatch.setattr(preap_settings, "load_preap_settings", lambda *a, **k: (CALIB | {"pedal_calib_done": False}, {}))
  assert pcc.preap_pedal_config(cp_on) == (True, None)
  ap1 = SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS, safetyConfigs=[SimpleNamespace(safetyParam=1)])
  assert pp.preap_pedal_bus_from_cp(ap1) is None


# ---- update_preap in pedal mode ---------------------------------------------

def _vl(**over):
  vl = {
    "ESP_B": {"ESP_vehicleSpeed": 50.0},
    "DI_torque1": {"DI_pedalPos": 30.0, "DI_torqueMotor": -10.0},
    "DI_torque2": {"DI_brakePedal": 0, "DI_gear": 4},
    "BrakeMessage": {"driverBrakeStatus": 1},
    "EPAS_sysStatus": {"EPAS_handsOnLevel": 0, "EPAS_eacStatus": 1, "EPAS_eacErrorCode": 0,
                       "EPAS_internalSAS": 0.0, "EPAS_torsionBarTorque": 0.0},
    "STW_ANGLHP_STAT": {"StW_AnglHP_Spd": 0.0},
    "DI_state": {"DI_speedUnits": 0, "DI_digitalSpeed": 31, "DI_cruiseState": 1},
    "STW_ACTN_RQ": dict(STW_LIVE),
    "GTW_carState": {d: 0 for d in pcs.DOORS} | {"BC_indicatorLStatus": 0, "BC_indicatorRStatus": 0},
  }
  for msg, sigs in over.items():
    vl[msg] = vl[msg] | sigs
  return SimpleNamespace(vl=vl)


class _PedalCS:
  def __init__(self):
    self.CP = SimpleNamespace(alternativeExperience=0)
    self.preap_engagement = pcs.PreapEngagement()
    self.steering_pressed_cnt = 0
    self.pedal_sensor = pp.PedalSensor()
    self.stalk_tracker = pp.StalkTracker()
    self.cp_pedal = None
    self.i = 0

  def update_speed_kf(self, v):
    return v, 0.0

  def update_steering_pressed(self, pressed, n):
    return False

  def step(self, raw1=0, **over):
    self.cp_pedal = SimpleNamespace(vl={"GAS_SENSOR": _sensor_vals(raw1, 0, (self.i // 2) % 16)})
    self.i += 1
    return pcs.update_preap(self, _vl(**over))


def test_update_preap_pedal_gas_from_interceptor():
  cs = _PedalCS()
  ret = cs.step()
  # DI_pedalPos 30 % is the pedal output, not the driver: not gasPressed
  assert not ret.gasPressed
  assert cs.step(raw1=900).gasPressed
  assert cs.motor_torque == -10.0


def test_update_preap_pedal_cruise_is_stock_cc_and_latch_separate():
  cs = _PedalCS()
  ret = cs.step(STW_ACTN_RQ={"SpdCtrlLvr_Stat": 2})
  assert cs.preap_latch and not ret.cruiseState.enabled and ret.cruiseState.available
  ret = cs.step(DI_state={"DI_cruiseState": 2})
  assert ret.cruiseState.enabled and cs.stock_cc_active and cs.stock_cc_cancelable
  ret = cs.step(DI_state={"DI_cruiseState": 5})
  assert not ret.cruiseState.enabled and not cs.stock_cc_cancelable


def test_update_preap_pedal_lever_buttons_and_fault():
  cs = _PedalCS()
  cs.step()
  ret = cs.step(STW_ACTN_RQ={"SpdCtrlLvr_Stat": 16})
  assert [(b.type, b.pressed) for b in ret.buttonEvents] == [(BT.accelCruise, True)]
  ret = cs.step()
  assert [(b.type, b.pressed) for b in ret.buttonEvents] == [(BT.accelCruise, False)]
  assert not ret.accFaulted
  cs.cp_pedal = None
  for _ in range(30):
    ret = pcs.update_preap(cs, _vl())
  assert ret.accFaulted


def test_update_preap_stock_mode_unchanged():
  # No pedal_sensor attribute: same DI_pedalPos gas rule and latch-as-cruise as before.
  class _StockCS(_PedalCS):
    def __init__(self):
      super().__init__()
      self.pedal_sensor = None
  cs = _StockCS()
  ret = pcs.update_preap(cs, _vl(STW_ACTN_RQ={"SpdCtrlLvr_Stat": 2}, DI_torque1={"DI_pedalPos": 0.0}))
  assert ret.cruiseState.enabled and not ret.gasPressed and len(ret.buttonEvents) == 0
  ret = pcs.update_preap(cs, _vl(DI_torque1={"DI_pedalPos": 5.0}))
  assert ret.gasPressed and not ret.cruiseState.enabled
