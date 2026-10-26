#!/usr/bin/bash
# BogPilot: run the Qt UI on AGNOS 17+ without weston.
#
# AGNOS 17.2 still ships weston 1.9 (32-bit armhf), but on this kernel it finds no display
# modes and advertises no wl_output, so a Qt wayland client only ever gets a black screen.
# The display is owned by the "magic" service instead (raylib UIs). We use magic for the build
# screen (common/bogpilot_screen.py), then stop it and run Qt directly on DRM/KMS with the
# eglfs_kms platform plugin and the Adreno EGL/GLES + msm gbm libraries.
#
# The eglfs plugin links libinput.so.10, which AGNOS 17 dropped. It and its small deps
# (libevdev, libmtdev, libwacom) are fetched once from the Ubuntu 24.04 (noble) archive into
# /data/bp_qtdeps, checked against pinned sha256 sums. Nothing binary lives in the repo.

BOGPILOT_QTDEPS="${BOGPILOT_QTDEPS:-/data/bp_qtdeps}"
BOGPILOT_QTDEPS_URL="https://launchpad.net/ubuntu/+archive/primary/+files"
BOGPILOT_QTDEPS_DEBS=(
  "libinput10_1.25.0-1ubuntu3.7_arm64.deb c83589bfe0be2e7d219a77966f80aed9acbdda55e93478fa1a0f509b5292aaf8"
  "libevdev2_1.13.1+dfsg-1build1_arm64.deb 4fd17e552a00e3a2dc5d39a6578b26a1ac973afc80384d165647d72441f3a064"
  "libmtdev1t64_1.1.6-1.1build1_arm64.deb 82822446ba3ca3afcfdafa7648b22686d058bacf1b0c7576668733b545d688d9"
  "libwacom9_2.10.0-2_arm64.deb b3f966d819a14690963357a1b9a46ce6da6540cb690783fae461d5d885fc3846"
)
BOGPILOT_QTDEPS_LIBS="libinput.so.10 libevdev.so.2 libmtdev.so.1 libwacom.so.9"

function bogpilot_magic_display {
  [ -f /usr/comma/magic.py ]
}

function bogpilot_qtdeps_ok {
  local l
  for l in $BOGPILOT_QTDEPS_LIBS libEGL.so.1 libGLESv2.so.2 libgbm.so.1; do
    [ -e "$BOGPILOT_QTDEPS/lib/$l" ] || return 1
  done
  return 0
}

function bogpilot_qtdeps_fetch {
  local entry deb sum
  mkdir -p "$BOGPILOT_QTDEPS/debs" "$BOGPILOT_QTDEPS/root" "$BOGPILOT_QTDEPS/lib" || return 1
  for entry in "${BOGPILOT_QTDEPS_DEBS[@]}"; do
    deb="${entry% *}"
    sum="${entry#* }"
    if ! echo "$sum  $BOGPILOT_QTDEPS/debs/$deb" | sha256sum -c --status 2>/dev/null; then
      curl -fsSL --retry 3 -m 120 -o "$BOGPILOT_QTDEPS/debs/$deb.part" "$BOGPILOT_QTDEPS_URL/$deb" || return 1
      echo "$sum  $BOGPILOT_QTDEPS/debs/$deb.part" | sha256sum -c --status || { rm -f "$BOGPILOT_QTDEPS/debs/$deb.part"; return 1; }
      mv "$BOGPILOT_QTDEPS/debs/$deb.part" "$BOGPILOT_QTDEPS/debs/$deb"
    fi
    dpkg-deb -x "$BOGPILOT_QTDEPS/debs/$deb" "$BOGPILOT_QTDEPS/root" || return 1
  done
  cp -a "$BOGPILOT_QTDEPS"/root/usr/lib/aarch64-linux-gnu/lib{input,evdev,mtdev,wacom}.so.* "$BOGPILOT_QTDEPS/lib/" || return 1
  # Qt loads libEGL.so.1/libGLESv2.so.2/libgbm.so.1; on AGNOS 17 those names point to mesa (glvnd),
  # which has no driver for the Adreno GPU. Point them at the vendor libraries instead.
  ln -sfn /usr/lib/aarch64-linux-gnu/libEGL_adreno.so "$BOGPILOT_QTDEPS/lib/libEGL.so.1"
  ln -sfn /usr/lib/aarch64-linux-gnu/libGLESv2_adreno.so "$BOGPILOT_QTDEPS/lib/libGLESv2.so.2"
  ln -sfn /usr/lib/aarch64-linux-gnu/libgbm.so "$BOGPILOT_QTDEPS/lib/libgbm.so.1"
  bogpilot_qtdeps_ok
}

# Shows a status screen (raylib, via magic) until the deps are there. Gives up after
# BOGPILOT_QTDEPS_TRIES failed downloads (default 30, about 5 minutes) and returns 1.
BOGPILOT_QTDEPS_TRIES="${BOGPILOT_QTDEPS_TRIES:-30}"
BOGPILOT_QTDEPS_RETRY_SLEEP="${BOGPILOT_QTDEPS_RETRY_SLEEP:-10}"
BOGPILOT_QTDEPS_FAIL_COUNT="${BOGPILOT_QTDEPS_FAIL_COUNT:-/data/bp_qtdeps_fail_count}"
function bogpilot_qtdeps_ensure {
  local tries=0 screen_pid="" rc=0
  while ! bogpilot_qtdeps_ok; do
    if [ -z "$screen_pid" ] && [ -z "${BOGPILOT_NO_SCREEN:-}" ]; then
      echo "BogPilot: fetching UI libraries" | /usr/local/venv/bin/python "$1/common/bogpilot_screen.py" spinner &
      screen_pid=$!
    fi
    bogpilot_qtdeps_fetch && break
    tries=$((tries + 1))
    if [ "$tries" -ge "$BOGPILOT_QTDEPS_TRIES" ]; then
      echo "BogPilot: could not download UI libraries after $tries tries, giving up"
      rc=1
      break
    fi
    echo "BogPilot: could not download UI libraries (try $tries of $BOGPILOT_QTDEPS_TRIES), retrying in ${BOGPILOT_QTDEPS_RETRY_SLEEP}s"
    sleep "$BOGPILOT_QTDEPS_RETRY_SLEEP"
  done
  if [ -n "$screen_pid" ]; then
    kill "$screen_pid" 2>/dev/null
    wait "$screen_pid" 2>/dev/null
  fi
  return $rc
}

# The UI libraries could not be fetched: say so on screen (raylib, magic is still running) and stop.
# Reboots by itself like a failed build, at most BOGPILOT_FAIL_MAX_REBOOTS times in a row.
function bogpilot_qtdeps_failed {
  local dir="$1" count min
  count=$(bogpilot_fail_count "$BOGPILOT_QTDEPS_FAIL_COUNT")
  min=$(bogpilot_auto_reboot_min "$count")
  bogpilot_fail_count_bump "$BOGPILOT_QTDEPS_FAIL_COUNT" >/dev/null
  if [ -z "${BOGPILOT_NO_SCREEN:-}" ]; then
    BOGPILOT_ERROR_REBOOT_MIN="$min" /usr/local/venv/bin/python "$dir/common/bogpilot_screen.py" text \
      "BogPilot could not download its UI libraries.
 
Connect to Wi-Fi with internet access, then reboot. The download is needed only once.
Files: $BOGPILOT_QTDEPS" &
  fi
  bogpilot_fail_hold "$min" "UI libraries missing"
}

# Stop magic (it holds DRM master) and point Qt at eglfs_kms. Call after the build, before manager.
function bogpilot_qt_eglfs_init {
  local dir="$1"
  bogpilot_qtdeps_ensure "$dir" || return 1
  rm -f "$BOGPILOT_QTDEPS_FAIL_COUNT" 2>/dev/null
  sudo systemctl stop magic 2>/dev/null
  if systemctl is-active --quiet weston; then
    sudo systemctl stop weston-ready weston 2>/dev/null
  fi
  export LD_LIBRARY_PATH="$BOGPILOT_QTDEPS/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  export QT_QPA_PLATFORM=eglfs
  export QT_QPA_EGLFS_INTEGRATION=eglfs_kms
  export QT_QPA_EGLFS_KMS_CONFIG="$dir/system/hardware/tici/bogpilot_kms.json"
  export QT_QPA_EGLFS_ALWAYS_SET_MODE=1
  export QT_QPA_EGLFS_HIDECURSOR=1
  export QT_QPA_EGLFS_ROTATION="${BOGPILOT_QT_ROTATION:--90}"
  export QT_QPA_EGLFS_PHYSICAL_WIDTH=136
  export QT_QPA_EGLFS_PHYSICAL_HEIGHT=68
}
