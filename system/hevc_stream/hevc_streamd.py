#!/usr/bin/env python3
"""hevc_streamd: forward already-encoded camera HEVC + a few state topics to a Mac over ZMQ.

- No re-encoding; encoderd is untouched. Raw cereal Event bytes are forwarded as-is
  as ZMQ multipart [topic, event_bytes].
- PUB socket with a small SNDHWM: a slow client loses messages instead of queueing.
- Binds only to the private IPv4 of HevcStreamIface (default wlan0). Optional IP
  allowlist and shared token (ZMQ ZAP / PLAIN).
- Background cores 0-2, nice 10. Onroad only (manager gate in process_config).
"""
from __future__ import annotations

import os
import socket
import struct
import time

import zmq

from openpilot.system.hevc_stream import config as hcfg


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


class Publisher:
  """ZMQ side; independent of msgq so it can be loopback-tested on a PC."""

  def __init__(self, bind_ip: str, port: int, allow=(), token: str | None = None, ctx: zmq.Context | None = None):
    ip = hcfg.safe_bind_ip(bind_ip)
    if ip is None:
      raise ValueError(f"refusing to bind non-private address {bind_ip!r}")
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
    self.sock.setsockopt(zmq.SNDHWM, hcfg.SNDHWM)
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


def main() -> None:
  from openpilot.common.realtime import set_core_affinity
  import cereal.messaging as messaging

  cfg = hcfg.load()
  try:
    os.nice(hcfg.NICE)
  except OSError:
    pass
  set_core_affinity(hcfg.CORES)

  bind_ip = None
  while bind_ip is None:  # wait for the travel router to hand wlan0 an address
    bind_ip = hcfg.safe_bind_ip(iface_ipv4(cfg.iface))
    if bind_ip is None:
      time.sleep(2.0)
  pub = Publisher(bind_ip, cfg.port, cfg.allow, cfg.token)

  poller = messaging.Poller()
  enc = {s: messaging.sub_sock(s, poller=poller, conflate=False) for s in cfg.encode_services}
  state = {s: messaging.sub_sock(s, conflate=True) for s in cfg.state_services}
  min_dt = 1.0 / hcfg.STATE_MAX_HZ
  last = dict.fromkeys(state, 0.0)

  while True:
    poller.poll(50)
    for s, sock in enc.items():
      for dat in messaging.drain_sock_raw(sock):
        pub.send(s, dat)
    now = time.monotonic()
    for s, sock in state.items():
      if now - last[s] < min_dt:
        continue
      dat = sock.receive(non_blocking=True)
      if dat is not None:
        pub.send(s, dat)
        last[s] = now


if __name__ == "__main__":
  main()
