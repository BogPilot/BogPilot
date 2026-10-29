"""Regression for false planner FCW on engage after FrogPilot zero-accel coast.

See selfdrive/controls/lib/fcw_gate.py. Observed on AP1: EventName.fcw
("BRAKE!" / "Risk of Collision") within ~50 ms of pcmEnable after coasting
toward a lead while disengaged. stockFcw/stockAeb and model hardBrakePredicted
were idle; longitudinalPlan.fcw was already true for seconds before engage
because frogpilotPlan min/maxAcceleration were 0 and crash_cnt latched.
"""

from openpilot.selfdrive.controls.lib.fcw_gate import planner_fcw


class TestFcwEngageGate:
  def test_reset_state_clears_latched_crash_cnt(self):
    fcw, cnt = planner_fcw(crash_cnt=50, standstill=False, reset_state=True)
    assert fcw is False and cnt == 0.0

  def test_engaged_with_zero_accel_min_still_reports_fcw(self):
    # Narrowed gate does NOT look at accel_min; engaged crash_cnt>2 must fire.
    fcw, cnt = planner_fcw(crash_cnt=3, standstill=False, reset_state=False)
    assert fcw is True and cnt == 3.0

  def test_engaged_braking_plan_still_reports_fcw(self):
    fcw, cnt = planner_fcw(crash_cnt=3, standstill=False, reset_state=False)
    assert fcw is True and cnt == 3.0
    fcw, cnt = planner_fcw(crash_cnt=2, standstill=False, reset_state=False)
    assert fcw is False and cnt == 2.0

  def test_standstill_suppresses_fcw_when_engaged(self):
    fcw, cnt = planner_fcw(crash_cnt=3, standstill=True, reset_state=False)
    assert fcw is False and cnt == 3.0
