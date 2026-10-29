"""BogPilot: minimal stand-in for pyserial, used ONLY by the AGNOS updater on AGNOS 19.9.

AGNOS 19.9 ships without pyserial. The updater bundled in system/hardware/tici/updater_magic
imports it indirectly (tici/hardware.py -> lpa.py: `import serial`) but never opens a serial
port while flashing AGNOS. bogpilot_updater puts this directory on PYTHONPATH only when
`import serial` fails, so the import succeeds and any real use fails cleanly with
SerialException (which lpa.py already handles).
"""

BOGPILOT_STUB = True
__version__ = "0+bogpilot.stub"


class SerialException(OSError):
  pass


class SerialTimeoutException(SerialException):
  pass


class Serial:
  def __init__(self, *args, **kwargs):
    raise SerialException("pyserial is not installed on this AGNOS (BogPilot updater stub)")


VTIMESerial = Serial
