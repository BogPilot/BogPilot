#!/usr/bin/bash

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

if [ -z "$AGNOS_VERSION" ]; then
  export AGNOS_VERSION="10.1"
fi

export STAGING_ROOT="/data/safe_staging"

# BogPilot: block comma/Konik phone-home at the process env level
# (covers the prebuilt Qt ui binary which still getenv("API_HOST") / ATHENA_HOST)
export API_HOST=""
export ATHENA_HOST=""
