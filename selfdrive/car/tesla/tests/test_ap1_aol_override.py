"""AP1 Always On Lateral driver override.

With AOL lateral active and openpilot not engaged (CarControl.enabled False, latActive True), a
driver override must follow the engaged path: keep 0x488 owned by openpilot (type NONE at the
measured wheel) through the hands pause, then hold the measured wheel (ANGLE) until EPAS is
EAC_ACTIVE and the latched HANDS_ON code clears. With steer_session left at None (AOL off) the
plan is exactly the pre-change plan.
"""
from collections import deque
from openpilot.selfdrive.car.tesla.actuator_plan import build_actuator_plan

BASE = dict(frame=0, hands_on_fault=False, openpilot_longitudinal_control=True, long_active=False,
            measured_angle_deg=2.0, requested_angle_deg=-10.0, last_angle_deg=2.0, v_ego=15.0, accel=0.0,
            acc_state=0, pcm_cancel=False, chassis_das_only=True)


def plan(**kw):
  a = dict(BASE, das_counters=deque()); a.update(kw)
  return build_actuator_plan(**a)


def test_aol_hands_pause_keeps_none_frames():
  p = plan(lat_active=False, enabled=False, hands_on_level=3, eac_status="EAC_INHIBITED",
           epas_error="EAC_ERROR_HANDS_ON", steer_session=True)
  assert p.steer is not None and not p.steer.enabled and p.steer.angle_deg == 2.0


def test_aol_resume_holds_measured_until_active():
  p = plan(lat_active=True, enabled=False, hands_on_level=1, eac_status="EAC_AVAILABLE",
           epas_error="EAC_ERROR_HANDS_ON", steer_session=True)
  assert p.steer.enabled and abs(p.steer.angle_deg - 2.0) < 1e-6
  assert p.longitudinal == ()


def test_aol_off_unchanged():
  for lat, en, hands, eac, err in [(True, True, 0, "EAC_ACTIVE", "EAC_ERROR_IDLE"),
                                    (False, True, 3, "EAC_INHIBITED", "EAC_ERROR_HANDS_ON"),
                                    (False, False, 0, "EAC_AVAILABLE", "EAC_ERROR_IDLE"),
                                    (True, True, 1, "EAC_AVAILABLE", "EAC_ERROR_HANDS_ON")]:
    kw = dict(lat_active=lat, enabled=en, hands_on_level=hands, eac_status=eac, epas_error=err)
    assert plan(**kw) == plan(steer_session=en, **kw)
