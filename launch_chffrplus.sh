#!/usr/bin/bash

if [ -z "$BASEDIR" ]; then
  BASEDIR="/data/openpilot"
fi

source "$BASEDIR/launch_env.sh"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

# BogPilot: AGNOS downgrade helpers (staged-update guard, on-screen error)
source "$DIR/system/hardware/tici/bogpilot_agnos.sh"
# BogPilot: Qt UI on AGNOS 17+ (eglfs on DRM, no weston)
source "$DIR/system/hardware/tici/bogpilot_qt_eglfs.sh"

function agnos_init {
  # TODO: move this to agnos
  sudo rm -f /data/etc/NetworkManager/system-connections/*.nmmeta

  # set success flag for current boot slot
  sudo abctl --set_success

  # TODO: do this without udev in AGNOS
  # udev does this, but sometimes we startup faster
  sudo chgrp gpu /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0
  sudo chmod 660 /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0
  sudo chmod 0777 /cache

  # Check if AGNOS update is required
  if [ $(< /VERSION) != "$AGNOS_VERSION" ]; then
    AGNOS_PY="$DIR/system/hardware/tici/agnos.py"
    MANIFEST="$DIR/system/hardware/tici/agnos.json"
    # BogPilot: log everything to $BOGPILOT_AGNOS_LOG. If the downgrade fails (for example from
    # AGNOS 19.9), show the reason on screen and stop here instead of starting a black screen.
    bogpilot_log "AGNOS $(< /VERSION) installed, this branch needs AGNOS $AGNOS_VERSION"
    if $AGNOS_PY --verify $MANIFEST >> "$BOGPILOT_AGNOS_LOG" 2>&1; then
      sudo reboot && sleep 600
    fi
    $DIR/system/hardware/tici/bogpilot_updater $AGNOS_PY $MANIFEST
    bogpilot_agnos_failed "$DIR/launch_chffrplus.sh"
  fi
}

# BogPilot: on AGNOS builds without magic the Qt UI and the Qt build spinner use weston.
# With magic (AGNOS 14+, incl. 17.2) the build screen is raylib under magic and the UI runs on
# eglfs (see bogpilot_qt_eglfs.sh), because weston finds no outputs there. magic is only
# stopped for this boot, so switching to a raylib fork (NotAutopilot) keeps working.
function qt_display_init {
  if bogpilot_magic_display; then
    return
  fi
  if systemctl is-active --quiet weston-ready; then
    return
  fi
  if systemctl is-active --quiet magic; then
    sudo systemctl stop magic
  fi
  # magic leaves its fake socket behind; weston-ready would see it and finish too early
  sudo rm -f /var/tmp/weston/wayland-0 /var/tmp/weston/wayland-0.lock
  sudo systemctl start weston
  sudo systemctl start weston-ready
}

function launch {
  # Remove orphaned git lock if it exists on boot
  [ -f "$DIR/.git/index.lock" ] && rm -f $DIR/.git/index.lock

  # Check to see if there's a valid overlay-based update available. Conditions
  # are as follows:
  #
  # 1. The BASEDIR init file has to exist, with a newer modtime than anything in
  #    the BASEDIR Git repo. This checks for local development work or the user
  #    switching branches/forks, which should not be overwritten.
  # 2. The FINALIZED consistent file has to exist, indicating there's an update
  #    that completed successfully and synced to disk.

  # BogPilot: never swap in a staged update from another fork (e.g. a stock openpilot update
  # left in /data/safe_staging). It is moved to /data/safe_staging_foreign_<time>, not deleted.
  bogpilot_staging_guard "$BASEDIR" "$STAGING_ROOT"
  BOGPILOT_STAGING_OK=$?

  if [ -f "${BASEDIR}/.overlay_init" ]; then
    find ${BASEDIR}/.git -newer ${BASEDIR}/.overlay_init | grep -q '.' 2> /dev/null
    if [ $? -eq 0 ]; then
      echo "${BASEDIR} has been modified, skipping overlay update installation"
    else
      if [ "$BOGPILOT_STAGING_OK" -eq 0 ] && [ -f "${STAGING_ROOT}/finalized/.overlay_consistent" ]; then
        if [ ! -d /data/safe_staging/old_openpilot ]; then
          echo "Valid overlay update found, installing"
          LAUNCHER_LOCATION="${BASH_SOURCE[0]}"

          mv $BASEDIR /data/safe_staging/old_openpilot
          mv "${STAGING_ROOT}/finalized" $BASEDIR
          cd $BASEDIR

          echo "Restarting launch script ${LAUNCHER_LOCATION}"
          unset AGNOS_VERSION
          exec "${LAUNCHER_LOCATION}"
        else
          echo "openpilot backup found, not updating"
          # TODO: restore backup? This means the updater didn't start after swapping
        fi
      fi
    fi
  fi

  # handle pythonpath
  ln -sfn $(pwd) /data/pythonpath
  export PYTHONPATH="$DIR/frogpilot/third_party:$PWD"

  # BogPilot: comma 3 (tici) can't run AGNOS 17.2 builds. Stop before agnos_init so it is never flashed.
  if [ -f /sys/firmware/devicetree/base/model ]; then
    DEVICE_TYPE=$(cat /sys/firmware/devicetree/base/model | tr -d '\0' | awk -F'comma ' '{print $2}')
    if [ "$DEVICE_TYPE" = "tici" ]; then
      echo "============================================="
      echo "This BogPilot branch needs a comma 3X (AGNOS 17.2)."
      echo "comma 3 (tici): install the bogpilot-tesla branch."
      echo "============================================="
      sleep 30
      exit 1
    fi
  fi

  # hardware specific init
  if [ -f /AGNOS ]; then
    agnos_init
    qt_display_init
  fi

  # write tmux scrollback to a file
  tmux capture-pane -pq -S-1000 > /tmp/launch_log

  # start manager
  cd system/manager
  if [ ! -f $DIR/prebuilt ]; then
    ./build.py
  fi
  if [ -f /AGNOS ] && bogpilot_magic_display; then
    bogpilot_qt_eglfs_init "$DIR"
  fi
  ./manager.py

  # if broken, keep on screen error
  while true; do sleep 1; done
}

launch
