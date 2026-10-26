#!/usr/bin/env bash
# BogPilot: safe on-device build, sourced by launch_chffrplus.sh
#   bogpilot_wait_network     bounded wait for the network before building (never blocks forever)
#   bogpilot_run_build        ./build.py at low priority, with the auto-reboot budget for its error screen
#   bogpilot_fail_hold        after a failure: never start the UI or manager; reboot by itself if allowed
#
# A failed build used to fall through into the Qt setup and the manager, which left a frozen error
# screen with no network. Now a failure stops here. The error screen reboots by itself after
# BOGPILOT_FAIL_REBOOT_MIN minutes, at most BOGPILOT_FAIL_MAX_REBOOTS times in a row (counted in
# /data/bp_build_fail_count, cleared after a good build), so a device that can never build does not
# reboot forever but one that failed on a transient problem recovers on its own.

BOGPILOT_BUILD_FAIL_COUNT="${BOGPILOT_BUILD_FAIL_COUNT:-/data/bp_build_fail_count}"
BOGPILOT_FAIL_REBOOT_MIN="${BOGPILOT_FAIL_REBOOT_MIN:-10}"
BOGPILOT_FAIL_MAX_REBOOTS="${BOGPILOT_FAIL_MAX_REBOOTS:-3}"
BOGPILOT_NET_WAIT="${BOGPILOT_NET_WAIT:-60}"
BOGPILOT_BUILD_JOBS="${BOGPILOT_BUILD_JOBS:-4}"
BOGPILOT_BUILT_MARKER="${BOGPILOT_BUILT_MARKER:-/data/bp_built_commit}"

# Usage: bogpilot_fail_count <file>  -> prints the count (0 when missing or garbage)
bogpilot_fail_count() {
  local n=""
  [ -f "$1" ] && n=$(head -c 16 "$1" 2>/dev/null | tr -cd '0-9')
  echo "${n:-0}"
}

# Usage: bogpilot_fail_count_bump <file>  -> prints the new count
bogpilot_fail_count_bump() {
  local n
  n=$(( $(bogpilot_fail_count "$1") + 1 ))
  echo "$n" > "$1" 2>/dev/null
  echo "$n"
}

# Usage: bogpilot_auto_reboot_min <failures so far>  -> minutes until auto reboot, 0 = stay on screen
bogpilot_auto_reboot_min() {
  if [ "$1" -lt "$BOGPILOT_FAIL_MAX_REBOOTS" ]; then
    echo "$BOGPILOT_FAIL_REBOOT_MIN"
  else
    echo 0
  fi
}

# Bounded wait for NetworkManager to report a connection. Returns 0 either way: the build still
# runs offline (everything it needs is in the repo), this only gives Wi-Fi time to come up first.
bogpilot_wait_network() {
  local t="$BOGPILOT_NET_WAIT" i
  if command -v nm-online >/dev/null 2>&1; then
    nm-online -q -t "$t" >/dev/null 2>&1 || echo "BogPilot: no network after ${t}s, building anyway"
    return 0
  fi
  for ((i = 0; i < t; i++)); do
    ip route get 1.1.1.1 >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "BogPilot: no network after ${t}s, building anyway"
  return 0
}

# Usage: bogpilot_head <repo dir>  -> the checked out commit, empty when unknown
bogpilot_head() {
  git -C "$1" rev-parse HEAD 2>/dev/null
}

# Usage: bogpilot_build_is_current <repo dir>
# True when this commit already built fine on this device, so a boot without Wi-Fi does not wait.
bogpilot_build_is_current() {
  local head
  head=$(bogpilot_head "$1")
  [ -n "$head" ] && [ -f "$BOGPILOT_BUILT_MARKER" ] && [ "$(cat "$BOGPILOT_BUILT_MARKER" 2>/dev/null)" = "$head" ]
}

bogpilot_low_prio() {
  local cmd=(nice -n 19)
  command -v ionice >/dev/null 2>&1 && cmd+=(ionice -c3)
  "${cmd[@]}" "$@"
}

# Usage: bogpilot_run_build [build command...]   (default ./build.py, run from system/manager)
# Sets BOGPILOT_LAST_FAIL_MIN for bogpilot_fail_hold. After a good build the commit is recorded in
# BOGPILOT_BUILT_MARKER (repo dir: BOGPILOT_SRC_DIR, else $DIR).
bogpilot_run_build() {
  local count rc
  [ $# -gt 0 ] || set -- ./build.py
  count=$(bogpilot_fail_count "$BOGPILOT_BUILD_FAIL_COUNT")
  BOGPILOT_LAST_FAIL_MIN=$(bogpilot_auto_reboot_min "$count")
  BOGPILOT_ERROR_REBOOT_MIN="$BOGPILOT_LAST_FAIL_MIN" BOGPILOT_BUILD_JOBS="$BOGPILOT_BUILD_JOBS" bogpilot_low_prio "$@"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    rm -f "$BOGPILOT_BUILD_FAIL_COUNT"
    bogpilot_head "${BOGPILOT_SRC_DIR:-${DIR:-.}}" > "$BOGPILOT_BUILT_MARKER" 2>/dev/null || true
    return 0
  fi
  count=$(bogpilot_fail_count_bump "$BOGPILOT_BUILD_FAIL_COUNT")
  echo "BogPilot: build failed (code $rc, failure $count in a row)"
  return "$rc"
}

bogpilot_reboot() {
  echo "BogPilot: rebooting ($*)"
  if [ -n "${BOGPILOT_REBOOT_CMD:-}" ]; then
    $BOGPILOT_REBOOT_CMD
  else
    sudo reboot
  fi
}

# Usage: bogpilot_fail_hold <minutes until reboot, 0 = never> <reason>
# Does not return (unless BOGPILOT_HOLD_RETURN is set, for tests). The error screen itself does the
# timed reboot; this is the backstop when the screen could not be shown or exited early.
bogpilot_fail_hold() {
  local min="${1:-0}" reason="${2:-failed}"
  echo "BogPilot: $reason. Not starting openpilot."
  if [ "$min" -gt 0 ] 2>/dev/null; then
    sleep $(( min * 60 + ${BOGPILOT_HOLD_GRACE:-30} ))
    bogpilot_reboot "$reason, ${min} min passed"
  fi
  [ -n "${BOGPILOT_HOLD_RETURN:-}" ] && return 1
  while true; do sleep 60; done
}
