#!/usr/bin/bash

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

if [ -z "$AGNOS_VERSION" ]; then
  # BogPilot: same AGNOS as NotAutopilot (openpilot 0.11.x), so switching between them never reflashes.
  # comma 3 (tici) is not supported on this branch; launch_chffrplus.sh stops before any flash.
  export AGNOS_VERSION="17.2"
fi

export STAGING_ROOT="/data/safe_staging"
