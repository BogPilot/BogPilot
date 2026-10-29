"""AP1 hands-on pause matches Tinkla HSO, not a FrogPilot cancel.

Not a product, no warranty, driver remains responsible, comply with local law.
This does not make the car safe to drive. EPAS error code 6 and a latched
hands-on code 3 do not by themselves block AP1 path lateral once hands are
below 2. Other error codes still do. Hands at or above 2 still send NONE.
"""

from collections import deque
from pathlib import Path

from openpilot.selfdrive.car.tesla.actuator_plan import (
  ACC_ON,
  AP1_ENGAGE_SOFT_START_FRAMES,
  EAC_ACTIVE,
  STEERING_CONTROL_ANGLE,
  STEERING_CONTROL_NONE,
  ap1_hold_measured_angle,
  build_actuator_plan,
)
from openpilot.selfdrive.car.tesla.hso import (
  AP1_HANDS_ON_LEVEL,
  ap1_hso_event_names,
  ap1_lat_active,
  ap1_steering_pressed,
)

ROOT = Path(__file__).resolve().parents[4]


def _plan(**kwargs):
  args = dict(
    frame=0,
    lat_active=False,
    hands_on_fault=False,
    openpilot_longitudinal_control=True,
    enabled=True,
    long_active=True,
    measured_angle_deg=8.0,
    requested_angle_deg=30.0,
    last_angle_deg=8.0,
    v_ego=15.0,
    accel=0.2,
    acc_state=ACC_ON,
    das_counters=deque([3]),
    pcm_cancel=False,
    chassis_das_only=True,
    eac_status=EAC_ACTIVE,
    soft_start=False,
  )
  args.update(kwargs)
  return build_actuator_plan(**args)


def test_hands_threshold_is_tinkla_level_2():
  assert AP1_HANDS_ON_LEVEL == 2
  assert ap1_steering_pressed(0) is False
  assert ap1_steering_pressed(1) is False
  assert ap1_steering_pressed(2) is True
  assert ap1_steering_pressed(3) is True
  # Path lateral pauses at the threshold even if controlsd still says lat active.
  assert ap1_lat_active(True, 2) is False
  assert ap1_lat_active(True, 1) is True
  assert ap1_lat_active(False, 0) is False


def test_hands_pause_sends_measured_none_and_keeps_long():
  plan = _plan(lat_active=False)
  assert plan.cancel is False
  assert len(plan.longitudinal) == 1
  assert plan.steer is not None
  assert plan.steer.enabled is False
  assert plan.steer.control_type == STEERING_CONTROL_NONE
  assert plan.steer.angle_deg == 8.0
  assert plan.apply_angle_last == 8.0


def test_hands_drop_resumes_path_angle_without_cancel():
  # v_ego 0 keeps the rate step large enough that the request is not clipped.
  plan = _plan(lat_active=True, requested_angle_deg=10.0, measured_angle_deg=0.0,
               last_angle_deg=0.0, v_ego=0.0, accel=0.0)
  assert plan.cancel is False
  assert len(plan.longitudinal) == 1
  assert plan.steer is not None
  assert plan.steer.enabled is True
  assert plan.steer.control_type == STEERING_CONTROL_ANGLE
  assert plan.steer.angle_deg == 10.0


def test_code_6_sends_measured_angle_not_planner():
  # Latched code 6: always measured ANGLE so EPAS can recover, even if lat on.
  plan = _plan(lat_active=True, requested_angle_deg=40.0, measured_angle_deg=0.0,
               last_angle_deg=0.0, v_ego=0.0,
               epas_error="EAC_ERROR_HIGH_ANGLE_REQ")
  assert plan.cancel is False
  assert plan.steer is not None
  assert plan.steer.enabled is True
  assert plan.steer.control_type == STEERING_CONTROL_ANGLE
  assert plan.steer.angle_deg == 0.0
  assert plan.steer.angle_deg != 40.0

  # Planner unavailable: measured wheel as ANGLE, not the far request, not NONE.
  held = _plan(lat_active=False, requested_angle_deg=25.0, measured_angle_deg=4.0,
               last_angle_deg=4.0, epas_error="EAC_ERROR_HIGH_ANGLE_REQ")
  assert held.cancel is False
  assert held.steer is not None
  assert held.steer.control_type == STEERING_CONTROL_ANGLE
  assert held.steer.angle_deg == 4.0
  assert held.steer.angle_deg != 25.0
  assert len(held.longitudinal) == 1



def test_latched_hands_on_sends_angle_after_hands_drop():
  # Old behavior: code 3 kept steerFaultTemporary set, lat stayed off, and
  # 0x488 stayed type NONE for the rest of the boot.
  from openpilot.selfdrive.car.tesla.steer_fault import steer_fault_temporary
  assert steer_fault_temporary("EAC_ERROR_HANDS_ON", True) is False
  assert steer_fault_temporary("EAC_ERROR_HANDS_ON", False) is False
  assert steer_fault_temporary("EAC_ERROR_HIGH_ANGLE_REQ", False) is True

  # Planner not active, cruise enabled, hands back below 2, not EAC_FAULT.
  # Name is HANDS_ON, not IDLE. Command is ANGLE at the measured wheel.
  held = _plan(lat_active=False, hands_on_level=1, eac_fault=False,
               requested_angle_deg=25.0, measured_angle_deg=4.0,
               last_angle_deg=4.0, epas_error="EAC_ERROR_HANDS_ON")
  assert held.cancel is False
  assert held.steer is not None
  assert held.steer.control_type == STEERING_CONTROL_ANGLE
  assert held.steer.control_type != STEERING_CONTROL_NONE
  assert held.steer.angle_deg == 4.0
  assert held.steer.angle_deg != 25.0
  assert len(held.longitudinal) == 1

  # Planner active but latched code 3: measured ANGLE until EPAS clears it.
  planned = _plan(lat_active=True, hands_on_level=0, eac_fault=False,
                  requested_angle_deg=40.0, measured_angle_deg=0.0,
                  last_angle_deg=0.0, v_ego=0.0,
                  epas_error="EAC_ERROR_HANDS_ON")
  assert planned.steer is not None
  assert planned.steer.control_type == STEERING_CONTROL_ANGLE
  assert planned.steer.angle_deg == 0.0

  # Hands still at or above 2: pause, measured angle, type NONE. Cruise stays.
  paused = _plan(lat_active=True, hands_on_level=2, eac_fault=False,
                 requested_angle_deg=25.0, measured_angle_deg=4.0,
                 last_angle_deg=4.0, epas_error="EAC_ERROR_HANDS_ON")
  assert paused.cancel is False
  assert len(paused.longitudinal) == 1
  assert paused.steer is not None
  assert paused.steer.control_type == STEERING_CONTROL_NONE
  assert paused.steer.enabled is False
  assert paused.steer.angle_deg == 4.0

  level3 = _plan(lat_active=True, hands_on_level=3,
                 epas_error="EAC_ERROR_HANDS_ON")
  assert level3.steer.control_type == STEERING_CONTROL_NONE
  assert level3.cancel is False


def test_other_epas_codes_still_send_measured_none():
  plan = _plan(lat_active=False, requested_angle_deg=25.0, measured_angle_deg=4.0,
               epas_error="EAC_ERROR_HIGH_ANGLE_RATE_REQ")
  assert plan.cancel is False
  assert plan.steer is not None
  assert plan.steer.control_type == STEERING_CONTROL_NONE
  assert plan.steer.angle_deg == 4.0
  assert plan.steer.enabled is False


def test_code_6_hands_on_still_none_and_does_not_cancel():
  plan = _plan(lat_active=True, requested_angle_deg=25.0, measured_angle_deg=4.0,
               last_angle_deg=4.0, hands_on_level=2,
               epas_error="EAC_ERROR_HIGH_ANGLE_REQ")
  assert plan.cancel is False
  assert len(plan.longitudinal) == 1
  assert plan.steer is not None
  assert plan.steer.control_type == STEERING_CONTROL_NONE
  assert plan.steer.angle_deg == 4.0


def test_code_6_eac_fault_does_not_send_angle():
  plan = _plan(lat_active=True, requested_angle_deg=10.0, measured_angle_deg=4.0,
               last_angle_deg=4.0, eac_fault=True,
               epas_error="EAC_ERROR_HIGH_ANGLE_REQ")
  assert plan.steer is not None
  assert plan.steer.control_type == STEERING_CONTROL_NONE
  assert plan.steer.angle_deg == 4.0


def test_code_6_while_disengaged_sends_no_steering_frame():
  plan = _plan(enabled=False, long_active=False, lat_active=False,
               epas_error="EAC_ERROR_HIGH_ANGLE_REQ")
  assert plan.steer is None
  assert plan.longitudinal == ()


def test_model3_code_6_does_not_take_the_ap1_angle_fallback():
  plan = _plan(chassis_das_only=False, enabled=True, lat_active=False,
               epas_error="EAC_ERROR_HIGH_ANGLE_REQ", measured_angle_deg=4.0)
  assert plan.steer is None


def test_disengaged_ap1_still_sends_no_steering_frame():
  plan = _plan(enabled=False, long_active=False, lat_active=False)
  assert plan.steer is None
  assert plan.longitudinal == ()
  assert plan.cancel is False


def test_non_ap1_lat_inactive_still_sends_no_steering_frame():
  plan = _plan(chassis_das_only=False, enabled=True, lat_active=False)
  assert plan.steer is None
  assert plan.cancel is False


def test_temp_steer_event_is_silent_and_fault_is_not():
  assert ap1_hso_event_names(["steerTempUnavailable", "pcmEnable"]) == [
    "steerTempUnavailableSilent",
    "pcmEnable",
  ]
  assert ap1_hso_event_names(["steerTempUnavailable", "steerTempUnavailableSilent"]) == [
    "steerTempUnavailableSilent",
  ]
  assert ap1_hso_event_names(["steerUnavailable"]) == ["steerUnavailable"]
  assert "steerTempUnavailable" not in ap1_hso_event_names(["steerTempUnavailable"])


def test_wiring_keeps_model3_cancel_and_ap1_pause():
  carstate = (ROOT / "selfdrive/car/tesla/carstate.py").read_text()
  assert "ap1_driver_input(self.hands_on_level)" in carstate
  assert "self.hands_on_level > 0" in carstate
  controller = (ROOT / "selfdrive/car/tesla/carcontroller.py").read_text()
  assert "hands_on_fault = (not ap1)" in controller
  assert "ap1_lat_active(CC.latActive, CS.hands_on_level)" in controller
  assert "epas_error=CS.steer_warning if ap1 else None" in controller
  assert "eac_fault=bool(CS.eac_fault) if ap1 else False" in controller
  assert "hands_on_level=CS.hands_on_level if ap1 else 0" in controller
  assert "eac_status=CS.eac_status if ap1 else None" in controller
  assert "soft_start=soft_start" in controller
  assert "self.eac_status = steer_status" in carstate
  # controlsd no longer clears latActive on AP1 steeringPressed (now any
  # hands level). The >= 2 pause is the ap1_lat_active gate above.
  controlsd = (ROOT / "selfdrive/controls/controlsd.py").read_text()
  assert "ap1_hands_pause" not in controlsd
  interface = (ROOT / "selfdrive/car/tesla/interface.py").read_text()
  assert "ap1_hso_event_names" in interface
  assert "CAR.TESLA_AP1_MODELS" in interface
  assert "_ap1_epas_inhibit_alert" in interface
  assert "Steering not active" in (ROOT / "selfdrive/controls/lib/events.py").read_text()
  # Hold clear and stalk follow stay wired.
  assert "ap1_should_send_hold_clear" in controller
  card = (ROOT / "frogpilot/controls/frogpilot_card.py").read_text()
  assert "TeslaStalkFollow" not in card


def test_inhibited_holds_measured_angle():
  plan = _plan(lat_active=True, requested_angle_deg=25.0, measured_angle_deg=4.0,
               last_angle_deg=4.0, v_ego=0.0, eac_status="EAC_INHIBITED")
  assert plan.steer is not None
  assert plan.steer.control_type == STEERING_CONTROL_ANGLE
  assert plan.steer.angle_deg == 4.0
  assert plan.steer.angle_deg != 25.0


def test_available_holds_measured_until_active():
  plan = _plan(lat_active=True, requested_angle_deg=25.0, measured_angle_deg=4.0,
               last_angle_deg=4.0, v_ego=0.0, eac_status="EAC_AVAILABLE")
  assert plan.steer.control_type == STEERING_CONTROL_ANGLE
  assert plan.steer.angle_deg == 4.0


def test_active_uses_planner_angle():
  plan = _plan(lat_active=True, requested_angle_deg=10.0, measured_angle_deg=0.0,
               last_angle_deg=0.0, v_ego=0.0, eac_status=EAC_ACTIVE)
  assert plan.steer.control_type == STEERING_CONTROL_ANGLE
  assert plan.steer.angle_deg == 10.0


def test_engage_soft_start_holds_measured_even_when_active():
  plan = _plan(lat_active=True, requested_angle_deg=10.0, measured_angle_deg=3.0,
               last_angle_deg=3.0, v_ego=0.0, eac_status=EAC_ACTIVE, soft_start=True)
  assert plan.steer.control_type == STEERING_CONTROL_ANGLE
  assert plan.steer.angle_deg == 3.0
  assert AP1_ENGAGE_SOFT_START_FRAMES == 30


def test_hands_pause_still_none_while_inhibited():
  plan = _plan(lat_active=True, hands_on_level=2, requested_angle_deg=25.0,
               measured_angle_deg=4.0, last_angle_deg=4.0, eac_status="EAC_INHIBITED")
  assert plan.steer.control_type == STEERING_CONTROL_NONE
  assert plan.steer.angle_deg == 4.0
  assert plan.cancel is False


def test_ap1_hold_measured_helper():
  assert ap1_hold_measured_angle(True, True, False, False, "EAC_INHIBITED", None, False) is True
  assert ap1_hold_measured_angle(True, True, False, False, EAC_ACTIVE, None, False) is False
  assert ap1_hold_measured_angle(True, True, False, False, EAC_ACTIVE, "EAC_ERROR_HIGH_ANGLE_REQ", False) is True
  assert ap1_hold_measured_angle(True, True, False, False, EAC_ACTIVE, None, True) is True
  assert ap1_hold_measured_angle(True, True, False, True, "EAC_INHIBITED", None, False) is False
  assert ap1_hold_measured_angle(True, True, False, False, None, None, False) is False
