#!/usr/bin/env python3
"""BogPilot bench test: inject XTRA into the EG25-G without ModemManager's enabled state (no SIM).

Run on the comma over SSH with the car OFF (offroad, qcomgpsd not running):
  cd /data/openpilot && PYTHONPATH=/data/openpilot python3 tools/xtra_bench_inject.py --dry-run
  cd /data/openpilot && PYTHONPATH=/data/openpilot python3 tools/xtra_bench_inject.py            # auto mode
Options: --mode auto|port|inhibit, --port /dev/ttyUSB3, --file /path/xtra3grc.bin, --no-time, --budget 15
Prints only modem state, port ownership and the XTRA status line (+QGPSXTRADATA: <valid min>,"<xtra time>").
It never queries or prints a position.
"""
import argparse
import datetime
import os
import subprocess
import sys
import time

from openpilot.system.qcomgpsd import xtra_inject as xi

URL = "http://xtrapath3.izatcloud.net/xtra3grc.bin"


def ntp_synced() -> bool:
  try:
    return subprocess.check_output("timedatectl show -p NTPSynchronized --value", shell=True,
                                   encoding="utf8", timeout=5).strip() == "yes"
  except Exception:
    return False


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--mode", choices=["auto", "port", "inhibit"], default="auto")
  ap.add_argument("--port", default=None)
  ap.add_argument("--file", default=None)
  ap.add_argument("--no-time", action="store_true", help="skip AT+QGPSXTRATIME")
  ap.add_argument("--budget", type=float, default=15.0)
  ap.add_argument("--dry-run", action="store_true", help="only print state, ports and XTRA status")
  ap.add_argument("--force", action="store_true", help="run even if qcomgpsd is running")
  a = ap.parse_args()

  if subprocess.run("pgrep -f system.qcomgpsd.qcomgpsd", shell=True, capture_output=True).returncode == 0 and not a.force:
    sys.exit("qcomgpsd is running (car on?). Turn the car off / go offroad first, or pass --force.")

  st = xi.mm_status()
  print(f"ModemManager: state={st.get('state')} failed_reason={st.get('failed_reason')} {st.get('error', '')}")
  for p in sorted(set(list(st.get("ports", {})) + xi.DEFAULT_AT_PORTS + sorted(xi.NEVER_PORTS))):
    if p.startswith("/dev/tty"):
      print(f"  {p:14s} kind={st.get('ports', {}).get(p, '?'):6s} exists={os.path.exists(p)} "
            f"open_by_others={xi.port_open_by_others(p)}")
  print(f"system clock NTP-synced: {ntp_synced()}  (UTC now {datetime.datetime.now(datetime.timezone.utc):%Y/%m/%d %H:%M:%S})")
  for c in ("AT+QGPS?", "AT+QGPSXTRA?", "AT+QGPSXTRADATA?"):
    try:
      print(f"  mmcli {c:18s} -> {xi.mm_at(c)}")
    except Exception as e:
      print(f"  mmcli {c:18s} -> error {str(e).strip()[:160]}")
  r = subprocess.run("mmcli --help-modem 2>&1 | grep -c -- --inhibit; mmcli --version | head -1", shell=True,
                     capture_output=True, text=True)
  print(f"mmcli supports --inhibit: {r.stdout.split()[0] if r.stdout else '?'}  ({' '.join(r.stdout.split()[1:])})")
  if a.dry_run:
    return

  path = a.file
  if path is None:
    import requests
    path = "/tmp/xtra3grc_bench.bin"
    t = time.monotonic()
    resp = requests.get(URL, timeout=10)
    resp.raise_for_status()
    with open(path, "wb") as f:
      f.write(resp.content)
    print(f"downloaded {len(resp.content)} bytes in {time.monotonic() - t:.1f}s")

  port = a.port
  allow_inhibit = a.mode in ("auto", "inhibit")
  if a.mode == "inhibit" and port is None:
    # force the inhibit path by pretending no free port exists
    xi._pick_port = lambda status: None
  if a.mode == "port":
    allow_inhibit = False

  time_ok = (not a.no_time) and ntp_synced()
  res = xi.inject_direct(path, time_trusted=time_ok, budget_s=a.budget, allow_inhibit=allow_inhibit,
                         port=port, log=lambda s: print("  " + s))
  print(f"\nmode={res['mode']} port={res['port']} elapsed={res.get('elapsed_s')}s ok={res['ok']}")
  print(f"XTRA before: {res.get('before')}   after: {res.get('after')}   (valid minutes, XTRA epoch)")
  if res.get("error"):
    print(f"error: {res['error']}")
  if res.get("xtra_was_disabled"):
    print("NOTE: XTRA was disabled and has been enabled; reboot the modem/device and run again.")
  if res.get("reprobe_needed"):
    print("ModemManager was inhibited; waiting for it to re-probe the modem ...")
    t = time.monotonic()
    while time.monotonic() - t < 40:
      try:
        xi.mm_at("AT", timeout=3)
        print(f"  modem back after {time.monotonic() - t:.1f}s")
        break
      except Exception:
        time.sleep(1)
    else:
      print("  modem not back after 40s (check `mmcli -L`)")
  try:
    print(f"mmcli AT+QGPSXTRADATA? now -> {xi.mm_at('AT+QGPSXTRADATA?')}")
  except Exception as e:
    print(f"mmcli AT+QGPSXTRADATA? now -> error {str(e)[:160]}")


if __name__ == "__main__":
  main()
