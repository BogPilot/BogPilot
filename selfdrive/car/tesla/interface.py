#!/usr/bin/env python3
from cereal import car
from openpilot.selfdrive.car.tesla.hso import ap1_hso_event_names, ap1_steering_pressed
from openpilot.selfdrive.car.tesla.toggles import ap1_standstill_engage
from openpilot.selfdrive.car.tesla.safety_flags import dashcam_only_for_candidate, flags_for_candidate
from openpilot.selfdrive.car.tesla.values import CANBUS, CAR
from openpilot.selfdrive.car import get_safety_config
from openpilot.selfdrive.car.interfaces import CarInterfaceBase


class CarInterface(CarInterfaceBase):
  @staticmethod
  def _get_params(ret, candidate, fingerprint, car_fw, experimental_long, docs, frogpilot_toggles):
    ret.carName = "tesla"

    if candidate == CAR.TESLA_PREAP_MODELS:
      return CarInterface._get_params_preap(ret, fingerprint)

    # There is no safe way to do steer blending with user torque,
    # so the steering behaves like autopilot. This is not
    # how openpilot should be, hence dashcamOnly.
    # AP1 is an explicit user exception for the Mobileye chassis port,
    # not a Model 3 change.
    ret.dashcamOnly = dashcam_only_for_candidate(candidate)

    ret.steerControlType = car.CarParams.SteerControlType.angle

    ret.longitudinalActuatorDelay = 0.5 # s
    ret.radarTimeStep = (1.0 / 8) # 8Hz

    # 0x2bf on the auxiliary powertrain bus is the second-panda Model 3/Y harness.
    # AP1 does not need that frame. flags_for_candidate sets AP1|LONG (10) for
    # TESLA_AP1_MODELS with no powertrain bit. The command is still planned only
    # when this param, CC.enabled, and CC.longActive are all true.
    # AP1StandstillEngage only matters with openpilot long on.
    long_toggle_on = not getattr(frogpilot_toggles, "disable_openpilot_long", True)
    standstill = (candidate == CAR.TESLA_AP1_MODELS and long_toggle_on and
                  ap1_standstill_engage(candidate))
    safety_params = flags_for_candidate(candidate, fingerprint, standstill=standstill)
    has_powertrain_das = (CANBUS.autopilot_powertrain in fingerprint.keys()) and (0x2bf in fingerprint[CANBUS.autopilot_powertrain].keys())
    if has_powertrain_das:
      ret.openpilotLongitudinalControl = not frogpilot_toggles.disable_openpilot_long
      ret.safetyConfigs = [
        get_safety_config(car.CarParams.SafetyModel.tesla, safety_params[0]),
        get_safety_config(car.CarParams.SafetyModel.tesla, safety_params[1]),
      ]
    elif candidate == CAR.TESLA_AP1_MODELS:
      # Chassis 0x2b9 only. Same toggle as the powertrain branch. No 0x2bf.
      ret.openpilotLongitudinalControl = not frogpilot_toggles.disable_openpilot_long
      ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.tesla, safety_params[0])]
    else:
      ret.openpilotLongitudinalControl = False
      ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.tesla, safety_params[0])]

    # AP1 only: the stock DAS blind-spot flags (0x399, read-only) feed
    # carState.left/rightBlindspot. Other Teslas keep enableBsm False.
    ret.enableBsm = candidate == CAR.TESLA_AP1_MODELS

    ret.steerLimitTimer = 1.0
    ret.steerActuatorDelay = 0.25
    return ret

  @staticmethod
  def _get_params_preap(ret, fingerprint):
    """Pre-AP Model S: lateral only on SAFETY_TESLA_PREAP, stock cruise.

    Ported from NotAutopilot (opendbc cc39c7f interface.py Pre-AP branch), MIT.
    Settings come from /data/params_bogpilot (preap_settings.py), no user input.
    """
    from openpilot.selfdrive.car.tesla.preap_settings import load_preap_settings
    from openpilot.selfdrive.car.tesla.safety_flags import learn_pedal_bus, preap_safety_param
    try:
      settings, _ = load_preap_settings()
    except Exception:
      settings = {}
    param = preap_safety_param(settings, learn_pedal_bus(fingerprint))
    ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.teslaPreap, param)]
    ret.dashcamOnly = False
    ret.steerControlType = car.CarParams.SteerControlType.angle
    ret.openpilotLongitudinalControl = False
    ret.pcmCruise = True
    ret.radarUnavailable = not bool(settings.get("radar_enabled"))
    ret.radarTimeStep = (1.0 / 8)
    ret.longitudinalActuatorDelay = 0.5
    ret.enableBsm = False
    ret.minEnableSpeed = -1.
    ret.steerLimitTimer = 1.0
    ret.steerActuatorDelay = 0.25
    return ret

  def _update(self, c, frogpilot_toggles):
    ret, fp_ret = self.CS.update(self.cp, self.cp_cam, frogpilot_toggles)

    events = self.create_common_events(ret)
    if self.CP.carFingerprint == CAR.TESLA_AP1_MODELS:
      if self._ap1_driver_yield_active():
        # Resume hold: hands are back at 0 but 0x488 is still NONE. Keep
        # controlsd in overriding so the border stays grey until lat resumes.
        events.add(car.CarEvent.EventName.steerOverride)
      events = self._ap1_hso_events(events)
      events = self._ap1_epas_inhibit_alert(events, c, ret)
    ret.events = events.to_msg()

    return ret, fp_ret

  def _ap1_epas_inhibit_alert(self, events, c, ret):
    """Quiet non-disengage alert when EPAS stays INHIBITED while we want lat.

    controlsd clears steerTempUnavailableSilent when steerFaultTemporary is
    false unless CS.events still requests it; we keep adding it here.
    """
    from openpilot.common.realtime import DT_CTRL
    EventName = car.CarEvent.EventName
    # ~1 s at controlsd rate. card/_update runs at the same 100 Hz.
    inhibit_frames = int(1.0 / DT_CTRL)
    want_lat = bool(c.enabled) and bool(getattr(c, "latActive", False))
    # The hands pause sends NONE on purpose (EPAS goes INHIBITED at level 3);
    # that is the driver steering, not EPAS refusing control.
    want_lat = want_lat and not ap1_steering_pressed(self.CS.hands_on_level) and not self._ap1_driver_yield_active()
    inhibited = self.CS.eac_status == "EAC_INHIBITED"
    if want_lat and inhibited and not ret.steerFaultPermanent:
      self.ap1_inhibit_alert_frames = getattr(self, "ap1_inhibit_alert_frames", 0) + 1
    else:
      self.ap1_inhibit_alert_frames = 0
    if self.ap1_inhibit_alert_frames >= inhibit_frames:
      events.add(EventName.steerTempUnavailableSilent)
    return events

  def _ap1_driver_yield_active(self):
    """CarController's hands pause / resume hold from the last step."""
    y = getattr(getattr(self, "CC", None), "ap1_yield", None)
    return bool(getattr(y, "active", False))

  def _ap1_hso_events(self, events):
    """Tinkla HSO: a temporary steer warning does not soft-disable or block entry.

    steerUnavailable from EAC_FAULT is not rewritten.
    """
    from openpilot.selfdrive.controls.lib.events import Events
    EventName = car.CarEvent.EventName
    by_raw = {int(v): k for k, v in EventName.schema.enumerants.items()}
    names = ap1_hso_event_names([by_raw[int(name)] for name in events.names])
    rebuilt = Events()
    for name in names:
      rebuilt.add(getattr(EventName, name))
    return rebuilt
