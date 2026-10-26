# bogpilot_pydeps

Stand-ins for Python modules the bundled AGNOS updater (`system/hardware/tici/updater_magic`)
imports but that newer AGNOS releases no longer ship.

- `serial/`: a tiny pyserial stand-in (not pyserial). AGNOS 19.9 dropped pyserial, so
  `updater_magic` crashed on `import serial` (via `tici/hardware.py` -> `lpa.py`) before it could
  show anything. The updater never opens a serial port; any use raises `SerialException`.
  `system/hardware/tici/bogpilot_updater` adds this directory to PYTHONPATH only when the system
  has no pyserial. It is not on the normal PYTHONPATH.
