#!/usr/bin/env bash
# BogPilot: helpers sourced by launch_chffrplus.sh
#   bogpilot_staging_guard  move a staged update from another fork out of the way (never deletes)
#   bogpilot_agnos_failed   show "AGNOS downgrade failed" on screen and stop, instead of a black screen

BOGPILOT_AGNOS_LOG="${BOGPILOT_AGNOS_LOG:-/data/tmp/bogpilot_agnos.log}"
BOGPILOT_TICI_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

bogpilot_log() {
  mkdir -p "$(dirname "$BOGPILOT_AGNOS_LOG")" 2>/dev/null
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$BOGPILOT_AGNOS_LOG"
}

# normalize: scheme, user@ and .git dropped, host:path -> host/path, lowercased
bogpilot_norm_url() {
  local u
  u=$(echo "$1" | tr 'A-Z' 'a-z')
  u="${u#*://}"
  u="${u#*@}"
  u="${u/://}"
  u="${u%/}"
  u="${u%.git}"
  u="${u%/}"
  echo "$u"
}

bogpilot_remote_url() {
  local cfg="$1/.git/config"
  [ -f "$cfg" ] || return 1
  git config --file "$cfg" --get remote.origin.url 2>/dev/null
}

# Usage: bogpilot_staging_guard <basedir> <staging_root>
# Returns 0 when the staged update (if any) may be installed, 1 when it must be skipped.
# A staged update is foreign when it has no git remote, or its remote differs from the
# installed one (a different branch of the same fork is fine, so in-app branch switching
# keeps working). Foreign updates are moved to /data/safe_staging_foreign_<timestamp>.
bogpilot_staging_guard() {
  local basedir="$1" staging="$2"
  local finalized="$staging/finalized"
  [ -d "$finalized" ] || return 0

  local cur new reason=""
  cur=$(bogpilot_remote_url "$basedir")
  new=$(bogpilot_remote_url "$finalized")
  if [ -z "$new" ]; then
    reason="staged update has no git remote"
  elif [ -n "$cur" ] && [ "$(bogpilot_norm_url "$cur")" != "$(bogpilot_norm_url "$new")" ]; then
    reason="staged update is from $new, installed fork is $cur"
  fi
  [ -z "$reason" ] && return 0

  local parent="${BOGPILOT_FOREIGN_PARENT:-$(dirname "$staging")}"
  local dest="$parent/safe_staging_foreign_$(date +%s)"
  while [ -e "$dest" ]; do dest="${dest}_"; done
  bogpilot_log "staging guard: $reason. Moving $finalized to $dest"
  if mkdir -p "$dest" 2>/dev/null || sudo mkdir -p "$dest" 2>/dev/null; then
    if mv "$finalized" "$dest/finalized" 2>/dev/null || sudo mv "$finalized" "$dest/finalized" 2>/dev/null; then
      { echo "$reason"; echo "moved by BogPilot launch_chffrplus.sh on $(date)"; } > "$dest/REASON.txt" 2>/dev/null \
        || echo "$reason" | sudo tee "$dest/REASON.txt" >/dev/null 2>&1
      return 0
    fi
  fi
  bogpilot_log "staging guard: could not move $finalized, skipping the staged update"
  return 1
}

# Usage: bogpilot_agnos_failed <launcher path>
# Never returns. Keeps the AGNOS display server (magic) running and shows the error with
# Retry / Reboot buttons. Falls back to the Qt text window, then to the console.
bogpilot_agnos_failed() {
  local launcher="$1"
  local log="$BOGPILOT_AGNOS_LOG"
  local screen="$BOGPILOT_TICI_DIR/bogpilot_error_screen.py"
  local keep="${BOGPILOT_AGNOS_LOG_KEEP:-/data/bogpilot_agnos_last_failure.log}"

  # the updater reboots on success; don't flash an error while the system goes down
  if [ -z "$BOGPILOT_TEST_SYSTEM_STATE" ] && [ "$(systemctl is-system-running 2>/dev/null)" = "stopping" ]; then
    sleep 600
  fi

  local py=python3
  [ -x /usr/local/venv/bin/python ] && py=/usr/local/venv/bin/python

  local reason
  reason=$("$py" "$screen" --log "$log" --print-reason 2>/dev/null)
  [ -z "$reason" ] && reason=$(grep -v '^\s*$' "$log" 2>/dev/null | tail -n 1 | cut -c1-200)
  [ -z "$reason" ] && reason="unknown error"
  local msg="AGNOS downgrade failed: $reason. See $log"

  bogpilot_log "AGNOS downgrade failed (needs AGNOS ${AGNOS_VERSION:-?}, running $(cat /VERSION 2>/dev/null || echo unknown)): $reason"
  # /data/tmp is wiped on every boot; keep a copy
  cp -f "$log" "$keep" 2>/dev/null

  echo "================================================="
  echo "BogPilot: $msg"
  echo "A copy of the log is kept at $keep"
  echo "================================================="

  local loops=0
  while true; do
    loops=$((loops + 1))
    if [ -n "$BOGPILOT_FAIL_MAX_LOOPS" ] && [ "$loops" -gt "$BOGPILOT_FAIL_MAX_LOOPS" ]; then
      return 99
    fi

    "$py" "$screen" --log "$log" --reason "$reason" --keep "$keep" 2>&1 | tee -a "$log"
    local rc=${PIPESTATUS[0]}
    case "$rc" in
      0)
        bogpilot_log "retry requested on the error screen"
        ${BOGPILOT_RETRY_CMD:-exec "$launcher"}
        ;;
      10)
        bogpilot_log "reboot requested on the error screen"
        ${BOGPILOT_REBOOT_CMD:-sudo reboot}
        sleep "${BOGPILOT_REBOOT_WAIT:-600}"
        ;;
      *)
        bogpilot_log "error screen unavailable (code $rc)"
        local text="${BOGPILOT_TEXT_DIR:-$BOGPILOT_TICI_DIR/../../../selfdrive/ui}"
        if [ -x "$text/text" ] && { [ -n "$BOGPILOT_TEST_WESTON" ] && [ "$BOGPILOT_TEST_WESTON" = "1" ] || \
             { [ -z "$BOGPILOT_TEST_WESTON" ] && systemctl is-active --quiet weston-ready 2>/dev/null; }; }; then
          bogpilot_log "showing the error with the Qt text window"
          (cd "$text" && ./text "BogPilot: $msg")
        else
          echo "BogPilot: $msg"
          sleep "${BOGPILOT_CONSOLE_WAIT:-30}"
        fi
        ;;
    esac
  done
}
