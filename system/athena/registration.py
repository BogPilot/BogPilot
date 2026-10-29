#!/usr/bin/env python3
"""BogPilot: offline device identity — no comma/Konik registration phone-home."""
from pathlib import Path

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.alertmanager import set_offroad_alert
from openpilot.system.hardware import HARDWARE, PC  # noqa: F401  (PC kept for call-site parity)
from openpilot.system.hardware.hw import Paths


UNREGISTERED_DONGLE_ID = "UnregisteredDevice"


def is_registered_device() -> bool:
  dongle = Params().get("DongleId", encoding='utf-8')
  return dongle not in (None, UNREGISTERED_DONGLE_ID)


def register(show_spinner=False, register_konik=False) -> str | None:
  """Assign a local DongleId without contacting comma/Konik servers.

  Prefer an existing Params/persist value. Otherwise mint a stable offline id
  from the hardware serial so manager/logging still have an identity. Never
  calls api_get / pilotauth. register_konik is accepted for call-site
  compatibility and ignored (no Konik pilotauth either). show_spinner is
  ignored — there is nothing to wait on.
  """
  params = Params()

  dongle_id: str | None = params.get("DongleId", encoding='utf8')
  persist = Path(Paths.persist_root() + "/comma/dongle_id")
  if dongle_id is None and persist.is_file():
    with open(persist) as f:
      dongle_id = f.read().strip() or None

  if not dongle_id or dongle_id == UNREGISTERED_DONGLE_ID:
    try:
      serial = HARDWARE.get_serial() or "unknown"
    except Exception:
      serial = "unknown"
    # Stable local id — not a comma-issued dongle. Prefix marks offline mint.
    dongle_id = f"offline-{serial}"
    cloudlog.warning(f"BogPilot offline registration: minted {dongle_id} (no API)")

  params.put("DongleId", dongle_id)
  try:
    persist.parent.mkdir(parents=True, exist_ok=True)
    persist.write_text(dongle_id)
  except Exception:
    cloudlog.exception("BogPilot offline registration: persist write failed")

  # Clear register-failure / unofficial-hardware alert if present (10.1 alert key)
  try:
    set_offroad_alert("Offroad_UnofficialHardware", False)
  except Exception:
    pass
  return dongle_id


if __name__ == "__main__":
  print(register())
