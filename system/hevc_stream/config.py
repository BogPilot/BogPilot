"""BogPilot HEVC stream config (experimental, file-backed like EnableICIntegration).

All settings live under /data/params_bogpilot/ because the July prebuilt
params_pyx.so rejects new Params keys:

  HevcStream          1 (default) = on, 0/false/off = off
  HevcStreamServices  comma list, default DEFAULT_SERVICES
  HevcStreamIface     interface to bind, default wlan0 (its private IPv4 only)
  HevcStreamPort      TCP port, default 8765
  HevcStreamAllow     optional comma list of client IPv4s (ZAP allowlist)
  HevcStreamToken     optional shared token (ZMQ PLAIN; client user "bogpilot")
  HevcStreamDriver    must be 1 to permit driverEncodeData (default refused)

Pure functions only; no messaging/zmq imports, so this is unit-testable.
Not driving logic: nothing here is read by controlsd, card, or panda safety.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

PARAMS_DIR = Path("/data/params_bogpilot")

ENCODE_SERVICES = ("wideRoadEncodeData", "roadEncodeData", "driverEncodeData")
STATE_SERVICES = ("carState", "liveCalibration", "radarState", "modelV2")
ALLOWED_SERVICES = ENCODE_SERVICES + STATE_SERVICES
# Encode streams only by default. On a recorded drive (bundle-1013, 41a156b1) hevc_streamd's
# extra msgq readers on carState / liveCalibration / radarState pushed those queues
# past msgq NUM_READERS (15); every re-subscribe evicts ALL readers of the queue, so
# calibrationd / dmonitoringd / paramsd / torqued / lagd / plannerd kept missing
# messages and published valid=False -> commIssue on every engage. State topics are
# opt-in only (HevcStreamServices) until they are sourced without new msgq readers.
DEFAULT_SERVICES = ("wideRoadEncodeData", "roadEncodeData")
DEFAULT_IFACE = "wlan0"
DEFAULT_PORT = 8765
# Max forward rate for state topics (latest-only/conflated). carState is 100 Hz on device.
STATE_MAX_HZ = 20.0
SNDHWM = 10  # per-subscriber messages buffered before ZMQ PUB drops (~0.25 s of 2 cams + state)
CORES = [0, 1, 2]  # background cores; driving cores 4-7 are never used
NICE = 10

_OFF = {"0", "false", "off", "no"}


def _read(name: str, d: Path) -> str | None:
  try:
    return (d / name).read_text().strip()
  except (OSError, UnicodeDecodeError):
    return None


def enabled(text: str | None) -> bool:
  """Absent / unreadable / anything not explicitly off => on (experimental default)."""
  return text is None or text.strip().lower() not in _OFF


def parse_services(text: str | None, allow_driver: bool = False) -> tuple[str, ...]:
  if text is None or not text.strip():
    return DEFAULT_SERVICES
  out: list[str] = []
  for s in (x.strip() for x in text.replace(" ", ",").split(",")):
    if s and s in ALLOWED_SERVICES and s not in out:
      if s == "driverEncodeData" and not allow_driver:
        continue
      out.append(s)
  return tuple(out) if out else DEFAULT_SERVICES


def parse_port(text: str | None) -> int:
  try:
    p = int((text or "").strip())
  except ValueError:
    return DEFAULT_PORT
  return p if 1024 <= p <= 65535 else DEFAULT_PORT


def parse_allow(text: str | None) -> tuple[str, ...]:
  out = []
  for s in (x.strip() for x in (text or "").replace(" ", ",").split(",")):
    if not s:
      continue
    try:
      out.append(str(ipaddress.IPv4Address(s)))
    except ValueError:
      continue
  return tuple(out)


def safe_bind_ip(ip: str | None) -> str | None:
  """Only a concrete private / loopback IPv4 is bindable. Never 0.0.0.0 or a public IP."""
  if not ip:
    return None
  try:
    a = ipaddress.IPv4Address(ip)
  except ValueError:
    return None
  if a.is_unspecified or not (a.is_private or a.is_loopback):
    return None
  return str(a)


@dataclass
class StreamConfig:
  enabled: bool = True
  services: tuple[str, ...] = DEFAULT_SERVICES
  iface: str = DEFAULT_IFACE
  port: int = DEFAULT_PORT
  allow: tuple[str, ...] = field(default_factory=tuple)
  token: str | None = None

  @property
  def encode_services(self) -> tuple[str, ...]:
    return tuple(s for s in self.services if s in ENCODE_SERVICES)

  @property
  def state_services(self) -> tuple[str, ...]:
    return tuple(s for s in self.services if s in STATE_SERVICES)


def load(d: Path | None = None) -> StreamConfig:
  d = PARAMS_DIR if d is None else d
  allow_driver = (_read("HevcStreamDriver", d) or "").lower() in {"1", "true", "on", "yes"}
  iface = _read("HevcStreamIface", d) or DEFAULT_IFACE
  tok = _read("HevcStreamToken", d) or None
  return StreamConfig(
    enabled=enabled(_read("HevcStream", d)),
    services=parse_services(_read("HevcStreamServices", d), allow_driver),
    iface=iface.split()[0],
    port=parse_port(_read("HevcStreamPort", d)),
    allow=parse_allow(_read("HevcStreamAllow", d)),
    token=tok,
  )


def should_run(started: bool, d: Path | None = None) -> bool:
  """Manager gate: onroad only, and HevcStream not explicitly off."""
  return bool(started) and load(d).enabled
