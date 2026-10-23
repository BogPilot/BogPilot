"""BogPilot camera video stream config (file-backed, like EnableICIntegration).

All settings live under /data/params_bogpilot/ because the July prebuilt
params_pyx.so rejects new Params keys. DEFAULT OFF: nothing streams unless a
toggle file explicitly says 1/true/on/yes.

  VideoStream         1 = stream the road camera (fcamera, roadEncodeData). Absent/anything else = OFF.
  VideoStreamWide     1 = also stream the wide camera (ecamera, wideRoadEncodeData). Default OFF.
  VideoStreamQcam     1 = send the small qcamera (qRoadEncodeData, H.264) instead of the road
                      camera; manual low-bandwidth fallback. Default OFF.
  HevcStream          legacy toggle. Now also default OFF: only an explicit 1 enables
                      (road camera, same rules as VideoStream).
  HevcStreamIface     interface to bind, default wlan0 (its private IPv4 only)
  HevcStreamPort      TCP port, default 8765

Auth is REQUIRED and shared with the state stream:
  StateStreamToken (fallback HevcStreamToken)  shared token, ZMQ PLAIN user "bogpilot"
  StateStreamAllow (fallback HevcStreamAllow)  comma list of client IPv4s (ZAP allowlist)
The stream refuses to start unless BOTH a token and a non-empty allowlist are set.

Only camera encode topics are ever forwarded (one msgq reader per enabled camera).
State topics and the driver camera are never streamed from here.

Pure functions only; no messaging/zmq imports, so this is unit-testable.
Not driving logic: nothing here is read by controlsd, card, or panda safety.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

PARAMS_DIR = Path("/data/params_bogpilot")

ROAD = "roadEncodeData"
WIDE = "wideRoadEncodeData"
QROAD = "qRoadEncodeData"
ENCODE_SERVICES = (ROAD, WIDE, QROAD)
# Kept only so tests can assert they are never streamed (msgq reader eviction -> commIssue
# on a recorded drive when hevc_streamd subscribed to these).
STATE_SERVICES = ("carState", "liveCalibration", "radarState", "modelV2")
DEFAULT_SERVICES = (ROAD,)
DEFAULT_IFACE = "wlan0"
DEFAULT_PORT = 8765
SNDHWM = 4  # per-subscriber messages buffered before ZMQ PUB drops (~0.2 s at 20 fps)
MAX_BURST = 2  # after a stall, forward at most this many newest packets per camera; older ones are dropped
CORES = [0, 1, 2]  # background cores; driving cores 4-7 are never used
NICE = 19

_ON = {"1", "true", "on", "yes"}


def _read(name: str, d: Path) -> str | None:
  try:
    return (d / name).read_text().strip()
  except (OSError, UnicodeDecodeError):
    return None


def enabled(text: str | None) -> bool:
  """Default OFF: absent / unreadable / anything not explicitly 1/true/on/yes => off."""
  return text is not None and text.strip().lower() in _ON


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


def select_services(wide: bool, qcam: bool) -> tuple[str, ...]:
  return (QROAD if qcam else ROAD,) + ((WIDE,) if wide else ())


@dataclass
class StreamConfig:
  enabled: bool = False
  services: tuple[str, ...] = DEFAULT_SERVICES
  iface: str = DEFAULT_IFACE
  port: int = DEFAULT_PORT
  allow: tuple[str, ...] = field(default_factory=tuple)
  token: str | None = None

  @property
  def encode_services(self) -> tuple[str, ...]:
    return tuple(s for s in self.services if s in ENCODE_SERVICES)

  @property
  def auth_ok(self) -> bool:
    return bool(self.token) and len(self.allow) > 0

  def refusal(self) -> str | None:
    """Why the streamer must not run, or None if it may."""
    if not self.enabled:
      return "off (VideoStream not 1)"
    if not self.token:
      return "no token (StateStreamToken / HevcStreamToken)"
    if not self.allow:
      return "no allowlist (StateStreamAllow / HevcStreamAllow)"
    return None


def load(d: Path | None = None) -> StreamConfig:
  d = PARAMS_DIR if d is None else d
  iface = (_read("HevcStreamIface", d) or DEFAULT_IFACE).split()[0]
  tok = _read("StateStreamToken", d) or _read("HevcStreamToken", d) or None
  allow = parse_allow(_read("StateStreamAllow", d)) or parse_allow(_read("HevcStreamAllow", d))
  return StreamConfig(
    enabled=enabled(_read("VideoStream", d)) or enabled(_read("HevcStream", d)),
    services=select_services(enabled(_read("VideoStreamWide", d)), enabled(_read("VideoStreamQcam", d))),
    iface=iface,
    port=parse_port(_read("HevcStreamPort", d)),
    allow=allow,
    token=tok,
  )


def should_run(started: bool, d: Path | None = None) -> bool:
  """Manager gate: onroad only, toggle explicitly on, and token + allowlist both present."""
  return bool(started) and load(d).refusal() is None
