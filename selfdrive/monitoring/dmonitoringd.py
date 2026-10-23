#!/usr/bin/env python3
import gc

from cereal import car
import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import set_realtime_priority
from openpilot.selfdrive.monitoring.helpers import DriverMonitoring
from openpilot.selfdrive.monitoring.phone_alert import PhoneUseGate, phone_alert_enabled

WRONG_GEARS = (car.CarState.GearShifter.reverse, car.CarState.GearShifter.park)


def dmonitoringd_thread():
  gc.disable()
  set_realtime_priority(2)

  params = Params()
  pm = messaging.PubMaster(['driverMonitoringState'])
  sm = messaging.SubMaster(['driverStateV2', 'liveCalibration', 'carState', 'controlsState', 'modelV2', 'carControl'], poll='driverStateV2')

  DM = DriverMonitoring(rhd_saved=params.get_bool("IsRhdDetected"), always_on=params.get_bool("AlwaysOnDM"))

  # FrogPilot variables
  driver_view_enabled = params.get_bool("IsDriverViewEnabled")

  # BogPilot: opt-in phone-use alert (default off)
  phone_gate = PhoneUseGate()
  phone_enabled = phone_alert_enabled()

  # 20Hz <- dmonitoringmodeld
  while True:
    sm.update()
    if not sm.updated['driverStateV2']:
      # iterate when model has new output
      continue

    valid = sm.all_checks()
    phone_alert = False
    if valid:
      DM.run_step(sm)
      phone_alert = phone_gate.update(
        enabled=phone_enabled,
        v_ego=sm['carState'].vEgo,
        wrong_gear=sm['carState'].gearShifter in WRONG_GEARS,
        face_detected=DM.face_detected,
        low_std=DM.pose.low_std,
        eev1=DM.eev1,
        dm_alert_active=len(DM.current_events) > 0,
      )
    elif driver_view_enabled:
      DM.face_detected = sm['driverStateV2'].leftDriverData.faceProb > DM.settings._FACE_THRESHOLD or sm['driverStateV2'].rightDriverData.faceProb > DM.settings._FACE_THRESHOLD
    if not valid:
      phone_gate.reset()

    # publish
    dat = DM.get_state_packet(valid=valid or driver_view_enabled)
    dat.driverMonitoringState.phoneUseAlert = phone_alert
    pm.send('driverMonitoringState', dat)

    # load live always-on toggle
    if sm['driverStateV2'].frameId % 40 == 1:
      DM.always_on = params.get_bool("AlwaysOnDM")
      phone_enabled = phone_alert_enabled()

    # save rhd virtual toggle every 5 mins
    if (sm['driverStateV2'].frameId % 6000 == 0 and
     DM.wheelpos_learner.filtered_stat.n > DM.settings._WHEELPOS_FILTER_MIN_COUNT and
     DM.wheel_on_right == (DM.wheelpos_learner.filtered_stat.M > DM.settings._WHEELPOS_THRESHOLD)):
      params.put_bool_nonblocking("IsRhdDetected", DM.wheel_on_right)

def main():
  dmonitoringd_thread()


if __name__ == '__main__':
  main()
