#!/usr/bin/env python3
"""hevc_streamd: forward already-encoded camera video to a Mac over ZMQ (DEFAULT OFF).

- No re-encoding; encoderd is untouched. Raw cereal EncodeData Event bytes are
  forwarded as-is as ZMQ multipart [topic, event_bytes]. Each Event already carries
  idx.frameId (camera frame counter), idx.timestampEof and logMonoTime, so frames
  line up with rlog / state-stream data without new fields.
- Road camera only by default (VideoStreamWide / VideoStreamQcam are opt-in).
- Refuses to start without BOTH a token and an IP allowlist (shared with StateStream).
- Binds only to the private IPv4 of HevcStreamIface (default wlan0).
- Drops, never queues: PUB with SNDHWM 4 + NOBLOCK, and after a stall only the
  newest MAX_BURST packets per camera are forwarded (the client resyncs at the next keyframe).
- Lowest priority: SCHED_IDLE + nice 19 on background cores 0-2. Onroad only (manager gate).
- Heat/CPU guard (guard.py): pause at 75 C / 85 % CPU / thermal yellow; the 3rd pause
  turns streaming off for the rest of the drive (process exits until next onroad).
"""
from __future__ import annotations

import os
import socket
import struct
import sys
import time

import zmq

from openpilot.system.hevc_stream import config as hcfg
from openpilot.system.hevc_stream.guard import OFF, HeatGuard

GUARD_PERIOD_S = 0.5  # deviceState is read conflated at 2 Hz


def iface_ipv4(iface: str) -> str | None:
  """IPv4 of an interface via SIOCGIFADDR (no subprocess)."""
  try:
    import fcntl
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
      packed = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", iface[:15].encode()))
    finally:
      s.close()
    return socket.inet_ntoa(packed[20:24])
  except OSError:
    return None


def newest(batch: list, max_burst: int = hcfg.MAX_BURST) -> tuple[list, int]:
  """Frame-drop policy: keep only the newest max_burst packets of a drained batch.
  Returns (to_send, dropped)."""
  if len(batch) <= max_burst:
    return batch, 0
  return batch[-max_burst:], len(batch) - max_burst


def lower_priority() -> None:
  """SCHED_IDLE + nice 19 + background cores. Best effort; never raises."""
  try:
    os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
  except (AttributeError, OSError):
    pass
  try:
    os.nice(hcfg.NICE)
  except OSError:
    pass
  try:
    if (os.cpu_count() or 0) >= 8:
      os.sched_setaffinity(0, set(hcfg.CORES))
  except (AttributeError, OSError):
    pass


class Publisher:
  """ZMQ side; independent of msgq so it can be loopback-tested on a PC.

  require_auth=True (used by hevc_streamd) refuses to construct without both a token
  and an allowlist. StateStream reuses this class with its own optional-auth rules."""

  def __init__(self, bind_ip: str, port: int, allow=(), token: str | None = None, ctx: zmq.Context | None = None,
               require_auth: bool = False, sndhwm: int = hcfg.SNDHWM):
    ip = hcfg.safe_bind_ip(bind_ip)
    if ip is None:
      raise ValueError(f"refusing to bind non-private address {bind_ip!r}")
    if require_auth and not (token and allow):
      raise ValueError("refusing to start without both a token and an IP allowlist")
    self.ctx = ctx or zmq.Context.instance()
    self.auth = None
    if allow or token:
      from zmq.auth.thread import ThreadAuthenticator
      self.auth = ThreadAuthenticator(self.ctx)
      self.auth.start()
      if allow:
        self.auth.allow(*allow)
      if token:
        self.auth.configure_plain(domain="bogpilot", passwords={"bogpilot": token})
    self.sock = self.ctx.socket(zmq.PUB)
    self.sock.setsockopt(zmq.SNDHWM, sndhwm)
    self.sock.setsockopt(zmq.LINGER, 0)
    if self.auth is not None:
      self.sock.setsockopt(zmq.ZAP_DOMAIN, b"bogpilot")
      if token:
        self.sock.setsockopt(zmq.PLAIN_SERVER, 1)
    self.sock.bind(f"tcp://{ip}:{port}")
    self.sent = 0

  def send(self, topic: str, dat: bytes) -> None:
    # PUB never blocks: past SNDHWM ZMQ silently drops for that subscriber.
    self.sock.send_multipart([topic.encode(), dat], flags=zmq.NOBLOCK, copy=False)
    self.sent += 1

  def close(self) -> None:
    self.sock.close(0)
    if self.auth is not None:
      self.auth.stop()


def _thermal_hot(ds) -> bool:
  try:
    return ds.thermalStatus.raw >= 1  # yellow, red, danger
  except AttributeError:
    return str(ds.thermalStatus) != "green"


def main() -> None:
  import cereal.messaging as messaging

  cfg = hcfg.load()
  why = cfg.refusal()
  if why is not None:
    print(f"hevc_streamd: not starting: {why}", file=sys.stderr)
    return
  lower_priority()

  bind_ip = None
  while bind_ip is None:  # wait for the travel router to hand wlan0 an address
    bind_ip = hcfg.safe_bind_ip(iface_ipv4(cfg.iface))
    if bind_ip is None:
      time.sleep(2.0)
  pub = Publisher(bind_ip, cfg.port, cfg.allow, cfg.token, require_auth=True)

  poller = messaging.Poller()
  enc = {s: messaging.sub_sock(s, poller=poller, conflate=False) for s in cfg.encode_services}
  dev = messaging.sub_sock("deviceState", conflate=True)
  guard = HeatGuard()
  next_guard = 0.0
  dropped = 0

  while True:
    poller.poll(50)
    now = time.monotonic()
    if now >= next_guard:
      next_guard = now + GUARD_PERIOD_S
      dat = dev.receive(non_blocking=True)
      if dat is not None:
        ds = messaging.log_from_bytes(dat).deviceState
        cpu = list(ds.cpuUsagePercent)
        state = guard.update(now, float(ds.maxTempC), sum(cpu) / len(cpu) if cpu else 0.0, _thermal_hot(ds))
        if state == OFF:
          print(f"hevc_streamd: guard off for this drive after {guard.trips} pauses ({guard.reason})", file=sys.stderr)
          pub.close()
          return
    for s, sock in enc.items():
      batch = messaging.drain_sock_raw(sock)
      if not guard.forwarding:
        dropped += len(batch)
        continue
      send, d = newest(batch)
      dropped += d
      for dat in send:
        pub.send(s, dat)


if __name__ == "__main__":
  main()
