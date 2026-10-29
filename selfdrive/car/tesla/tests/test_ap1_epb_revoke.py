"""AP1 CarState: EPB EAC revoke → permanent steer fault; stock 0x488 counters.

Drive 18: after an EPAS code 7 latch the EPB set EPB_epasEACAllow to 0 and
kept it there until the car was power cycled. EPAS stayed INHIBITED and the
driver only got the silent steer warning while engaged for 20 s.
"""
from types import SimpleNamespace

from openpilot.selfdrive.car.tesla.tests.test_ap1_speed_limit import (  # noqa: F401 (fixture)
  _ap1_cans, _prep_define, carstate_mod,
)


def _cs(carstate_mod, fp=None):  # noqa: F811
  from openpilot.selfdrive.car.tesla.values import CAR
  CS = carstate_mod.CarState(SimpleNamespace(carFingerprint=fp or CAR.TESLA_AP1_MODELS), None)
  _prep_define(CS)
  return CS


def _update(CS, epb=None, stock_ctrs=None):
  cp, cp_cam = _ap1_cans(45.0)
  if epb is not None:
    cp.vl["EPB_epasControl"]["EPB_epasEACAllow"] = epb[-1]
    cp.vl_all["EPB_epasControl"]["EPB_epasEACAllow"] = list(epb)
  if stock_ctrs is not None:
    cp_cam.vl_all["DAS_steeringControl"]["DAS_steeringControlCounter"] = list(stock_ctrs)
  ret, _ = CS.update(cp, cp_cam, None)
  CS.out = ret
  return ret


def test_no_epb_frame_yet_is_not_a_fault(carstate_mod):  # noqa: F811
  CS = _cs(carstate_mod)
  # vl default would read 0 here; only a received frame counts.
  ret = _update(CS)
  assert CS.epb_eac_allow is None
  assert ret.steerFaultPermanent is False


def test_eac_allowed_is_not_a_fault(carstate_mod):  # noqa: F811
  CS = _cs(carstate_mod)
  assert _update(CS, epb=[1]).steerFaultPermanent is False
  assert _update(CS).steerFaultPermanent is False


def test_eac_revoked_is_permanent_until_allowed_again(carstate_mod):  # noqa: F811
  CS = _cs(carstate_mod)
  _update(CS, epb=[1])
  assert _update(CS, epb=[1, 0]).steerFaultPermanent is True
  # Steps without a new 0x214 frame keep the last value.
  for _ in range(5):
    assert _update(CS).steerFaultPermanent is True
  assert CS.epb_eac_revoked is True
  # Power cycle (frame back at 1) clears it.
  assert _update(CS, epb=[1]).steerFaultPermanent is False


def test_non_ap1_ignores_epb(carstate_mod):  # noqa: F811
  from openpilot.selfdrive.car.tesla.values import CAR
  CS = _cs(carstate_mod, CAR.TESLA_AP2_MODELS)
  assert _update(CS, epb=[0]).steerFaultPermanent is False


def test_stock_steer_counters_from_vl_all(carstate_mod):  # noqa: F811
  CS = _cs(carstate_mod)
  _update(CS, stock_ctrs=[7])
  assert CS.stock_steer_counters == [7]
  _update(CS)
  assert CS.stock_steer_counters == []
  _update(CS, stock_ctrs=[14, 15])
  assert CS.stock_steer_counters == [14, 15]


def test_parsers_ap1_only_frequency_zero(carstate_mod):  # noqa: F811
  from openpilot.selfdrive.car.tesla.values import CAR
  ap1 = SimpleNamespace(carFingerprint=CAR.TESLA_AP1_MODELS)
  ap2 = SimpleNamespace(carFingerprint=CAR.TESLA_AP2_MODELS)
  assert dict(carstate_mod.CarState.get_can_parser(ap1, None).messages)["EPB_epasControl"] == 0
  assert "EPB_epasControl" not in dict(carstate_mod.CarState.get_can_parser(ap2, None).messages)
  assert dict(carstate_mod.CarState.get_cam_can_parser(ap1, None).messages)["DAS_steeringControl"] == 0
  assert "DAS_steeringControl" not in dict(carstate_mod.CarState.get_cam_can_parser(ap2, None).messages)


def test_dbc_has_signals():
  from pathlib import Path
  root = Path(__file__).resolve().parents[4]
  dbc = (root / "opendbc/tesla_can.dbc").read_text()
  assert "BO_ 532 EPB_epasControl:" in dbc  # 0x214
  assert "SG_ EPB_epasEACAllow :" in dbc
  assert "BO_ 1160 DAS_steeringControl:" in dbc  # 0x488
  assert "SG_ DAS_steeringControlCounter :" in dbc
