#!/usr/bin/env python3
import datetime
import os
import subprocess
import time
from typing import NoReturn

from timezonefinder import TimezoneFinder

import cereal.messaging as messaging
from openpilot.common.time import system_time_valid
from openpilot.common.bogpilot_clock import maybe_save_last_known_time
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import AGNOS


def set_timezone(timezone):
  valid_timezones = subprocess.check_output('timedatectl list-timezones', shell=True, encoding='utf8').strip().split('\n')
  if timezone not in valid_timezones:
    cloudlog.error(f"Timezone not supported {timezone}")
    return

  cloudlog.debug(f"Setting timezone to {timezone}")
  try:
    if AGNOS:
      tzpath = os.path.join("/usr/share/zoneinfo/", timezone)
      subprocess.check_call(f'sudo su -c "ln -snf {tzpath} /data/etc/tmptime && \
                              mv /data/etc/tmptime /data/etc/localtime"', shell=True)
      subprocess.check_call(f'sudo su -c "echo \"{timezone}\" > /data/etc/timezone"', shell=True)
    else:
      subprocess.check_call(f'sudo timedatectl set-timezone {timezone}', shell=True)
  except subprocess.CalledProcessError:
    cloudlog.exception(f"Error setting timezone to {timezone}")


def _as_utc(new_time: datetime.datetime) -> datetime.datetime:
  """Aware UTC datetime. A naive value is taken to already be UTC (never local)."""
  if new_time.tzinfo is None:
    return new_time.replace(tzinfo=datetime.timezone.utc)
  return new_time.astimezone(datetime.timezone.utc)


def date_set_command(new_time: datetime.datetime) -> str:
  """Shell command that sets the system clock to new_time (UTC wall clock string, TZ=UTC)."""
  utc_naive = _as_utc(new_time).replace(tzinfo=None)
  return f"TZ=UTC date -s '{utc_naive.strftime('%Y-%m-%d %H:%M:%S.%f')}'"


def set_time(new_time, now=None):
  """Set the system clock to new_time.

  new_time must be timezone-aware, or naive UTC. Both the comparison and the
  `date -s` string are in UTC. Previously GPS time was built with a local-naive
  fromtimestamp() and compared to local-naive now(), then handed to
  `TZ=UTC date -s`, so on a device whose timezone was set (e.g. America/New_York)
  the clock landed exactly the UTC offset behind (4 h in EDT) whenever GPS had to
  correct it.
  """
  new_time = _as_utc(new_time)
  now = datetime.datetime.now(datetime.timezone.utc) if now is None else _as_utc(now)
  diff = now - new_time
  # Match upstream: correct both a clock stuck in the past (AGNOS epoch) and
  # one slightly ahead. Without abs(), GPS could never advance a 2023 clock.
  if abs(diff) < datetime.timedelta(seconds=10):
    cloudlog.debug(f"Time diff too small: {diff}")
    return

  cloudlog.debug(f"Setting time to {new_time.isoformat()}")
  try:
    subprocess.run(date_set_command(new_time), shell=True, check=True)
  except subprocess.CalledProcessError:
    cloudlog.exception("timed.failed_setting_time")


def gps_time_from_llk(unix_timestamp_millis: int) -> datetime.datetime:
  """GPS unix milliseconds -> aware UTC datetime (independent of the device timezone)."""
  return datetime.datetime.fromtimestamp(unix_timestamp_millis / 1000., tz=datetime.timezone.utc)


def main() -> NoReturn:
  """
    timed has two responsibilities:
    - getting the current time
    - getting the current timezone

    GPS directly gives time, and timezone is looked up from GPS position.
    AGNOS will also use NTP to update the time.
  """

  params = Params()

  # Restore timezone from param
  tz = params.get("Timezone", encoding='utf8')
  tf = TimezoneFinder()
  if tz is not None:
    cloudlog.debug("Restoring timezone from param")
    set_timezone(tz)

  pm = messaging.PubMaster(['clocks'])
  sm = messaging.SubMaster(['liveLocationKalman'])
  while True:
    sm.update(1000)

    msg = messaging.new_message('clocks')
    msg.valid = system_time_valid()
    msg.clocks.wallTimeNanos = time.time_ns()
    pm.send('clocks', msg)

    # Persist a durable last-known stamp (~every 5 min once the clock is valid)
    # so the next boot is near real time even before GPS/NTP. Runs whether or
    # not GPS is up; rate-limited inside maybe_save_last_known_time.
    maybe_save_last_known_time()

    llk = sm['liveLocationKalman']
    if not llk.gpsOK or (time.monotonic() - sm.logMonoTime['liveLocationKalman']/1e9) > 0.2:
      continue

    # set time
    # TODO: account for unixTimesatmpMillis being a (usually short) time in the past
    gps_time = gps_time_from_llk(llk.unixTimestampMillis)
    set_time(gps_time)

    # set timezone
    pos = llk.positionGeodetic.value
    if len(pos) == 3:
      gps_timezone = tf.timezone_at(lat=pos[0], lng=pos[1])
      if gps_timezone is None:
        cloudlog.critical(f"No timezone found based on {pos=}")
      else:
        set_timezone(gps_timezone)
        params.put_nonblocking("Timezone", gps_timezone)

    time.sleep(10)

if __name__ == "__main__":
  main()
