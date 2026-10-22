from cereal import custom, log
from openpilot.common.realtime import DT_DMON
from openpilot.selfdrive.controls.lib.events import FROGPILOT_EVENTS, ET
from openpilot.selfdrive.monitoring import phone_alert as pa
from openpilot.selfdrive.monitoring.phone_alert import PhoneUseGate, phone_alert_enabled

FrogPilotEventName = custom.FrogPilotCarEvent.EventName
FrogPilotAudibleAlert = custom.FrogPilotCarControl.HUDControl.AudibleAlert

V = 30.  # m/s, above PHONE_MIN_SPEED
HOLD = int(round(pa.PHONE_HOLD_S / DT_DMON))


def step(g, n, eev1, **kw):
  args = dict(enabled=True, v_ego=V, wrong_gear=False, face_detected=True, low_std=True, eev1=eev1, dm_alert_active=False)
  args.update(kw)
  out = []
  for _ in range(n):
    out.append(g.update(**args))
  return out


def test_toggle_default_off(tmp_path):
  pa._phone_alert_path = tmp_path / "PhoneAlert"
  try:
    assert not phone_alert_enabled()
    pa._phone_alert_path.write_text("1\n")
    assert phone_alert_enabled()
    pa._phone_alert_path.write_text("0")
    assert not phone_alert_enabled()
  finally:
    pa._phone_alert_path = pa.PHONE_ALERT_PATH
  assert phone_alert_enabled(b"on") and not phone_alert_enabled("")


def test_fires_after_hold_then_cools_down():
  g = PhoneUseGate()
  assert not any(step(g, HOLD - 1, 0.9))
  assert step(g, 1, 0.9)[-1]
  assert g.fire_count == 1
  # alert stays on for PHONE_ALERT_S, then off; no refire inside the cooldown
  out = step(g, int(pa.PHONE_COOLDOWN_S / DT_DMON) - 5, 0.9)
  assert sum(out) == int(round(pa.PHONE_ALERT_S / DT_DMON)) - 1
  assert g.fire_count == 1
  step(g, HOLD + 10, 0.9)
  assert g.fire_count == 2


def test_short_dips_tolerated_but_low_values_do_not_fire():
  g = PhoneUseGate()
  seq = ([0.9] * 9 + [0.1]) * (HOLD // 10 + 1)
  fired = any(g.update(True, V, False, True, True, e, False) for e in seq)
  assert fired
  g = PhoneUseGate()
  assert not any(step(g, 20 * 60 * 5, pa.PHONE_EEV1_THRESH - 0.01))


def test_blocked_conditions():
  for kw in (dict(enabled=False), dict(v_ego=5.), dict(wrong_gear=True), dict(face_detected=False),
             dict(low_std=False), dict(dm_alert_active=True)):
    g = PhoneUseGate()
    assert not any(step(g, HOLD * 3, 0.9, **kw)), kw
    assert g.fire_count == 0


def test_stock_dm_alert_cancels_ours():
  g = PhoneUseGate()
  step(g, HOLD, 0.9)
  assert g.alert_active
  assert not step(g, 1, 0.9, dm_alert_active=True)[-1]


def test_cereal_and_event_wiring():
  dm = log.DriverMonitoringState.new_message()
  dm.phoneUseAlert = True
  assert dm.phoneUseAlert
  alert = FROGPILOT_EVENTS[FrogPilotEventName.phoneUseDetected][ET.PERMANENT]
  assert alert.audible_alert == FrogPilotAudibleAlert.phoneUse


def test_soundd_entry():
  from openpilot.selfdrive.ui.soundd import sound_list, BOGPILOT_SOUND_FALLBACKS
  fname, count, _ = sound_list[FrogPilotAudibleAlert.phoneUse]
  assert fname == "phone_use.wav" and count == 1
  assert BOGPILOT_SOUND_FALLBACKS[fname] == "prompt_distracted.wav"
