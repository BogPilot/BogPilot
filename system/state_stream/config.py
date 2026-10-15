"""BogPilot StateStream config (file-backed under /data/params_bogpilot, like HevcStream).

  StateStream        1/true/on = on. Absent or anything else = OFF (default off).
  StateStreamIface   interface to bind, default wlan0 (its private IPv4 only)
  StateStreamPort    TCP port, default 8766 (hevc_streamd uses 8765)
  StateStreamAllow   optional comma list of client IPv4s (ZAP allowlist); falls back to HevcStreamAllow
  StateStreamToken   optional shared token (ZMQ PLAIN, user "bogpilot"); falls back to HevcStreamToken
  StateStreamHz      max send rate, default 20 (clamped 1..20)
  StateStreamModelHz modelV2 lane/edge rate, default 10 (clamped 1..20)

Pure functions only (no zmq / messaging imports) so it is unit-testable.
Not driving logic: nothing here is read by controlsd, card, plannerd or panda safety.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from openpilot.system.hevc_stream.config import parse_allow, safe_bind_ip  # noqa: F401  (re-exported)

PARAMS_DIR = Path("/data/params_bogpilot")
DEFAULT_IFACE = "wlan0"
DEFAULT_PORT = 8766
DEFAULT_HZ = 20.0
DEFAULT_MODEL_HZ = 10.0
QUEUE_MAX = 2      # snapshots waiting for the sender thread; extra are dropped, host never waits
SNDHWM = 4         # per-subscriber ZMQ buffer (~0.2 s at 20 Hz), then PUB drops
TOPIC = "state"
_ON = {"1", "true", "on", "yes"}


def _read(name: str, d: Path) -> str | None:
  try:
    return (d / name).read_text().strip()
  except (OSError, UnicodeDecodeError):
    return None


def enabled(text: str | None) -> bool:
  """Default OFF: only an explicit 1/true/on/yes enables the stream."""
  return text is not None and text.strip().lower() in _ON


def parse_port(text: str | None) -> int:
  try:
    p = int((text or "").strip())
  except ValueError:
    return DEFAULT_PORT
  return p if 1024 <= p <= 65535 else DEFAULT_PORT


def parse_hz(text: str | None, default: float) -> float:
  try:
    hz = float((text or "").strip())
  except ValueError:
    return default
  if hz != hz:  # NaN
    return default
  return min(max(hz, 1.0), 20.0)


@dataclass
class StateStreamConfig:
  enabled: bool = False
  iface: str = DEFAULT_IFACE
  port: int = DEFAULT_PORT
  allow: tuple[str, ...] = field(default_factory=tuple)
  token: str | None = None
  hz: float = DEFAULT_HZ
  model_hz: float = DEFAULT_MODEL_HZ


def load(d: Path | None = None) -> StateStreamConfig:
  d = PARAMS_DIR if d is None else d
  iface = (_read("StateStreamIface", d) or DEFAULT_IFACE).split()[0]
  allow = parse_allow(_read("StateStreamAllow", d) or _read("HevcStreamAllow", d))
  token = _read("StateStreamToken", d) or _read("HevcStreamToken", d) or None
  return StateStreamConfig(
    enabled=enabled(_read("StateStream", d)),
    iface=iface,
    port=parse_port(_read("StateStreamPort", d)),
    allow=allow,
    token=token,
    hz=parse_hz(_read("StateStreamHz", d), DEFAULT_HZ),
    model_hz=parse_hz(_read("StateStreamModelHz", d), DEFAULT_MODEL_HZ),
  )
