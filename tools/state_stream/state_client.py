#!/usr/bin/env python3
"""Mac/PC client for BogPilot StateStream (JSON over ZMQ SUB).

  pip install pyzmq
  python3 state_client.py --host <comma wlan0 IP> [--port 8766] [--token T] [--udp 127.0.0.1:7777]

Prints a one-line summary per second. With --udp it re-sends each JSON payload
as one UDP datagram (easy to read from Unreal via a UDP socket / plugin).
Position values are printed only if --show-gps is given.
"""
from __future__ import annotations

import argparse
import json
import socket
import time

import zmq

TOPIC = b"state"


class Client:
  def __init__(self, endpoint: str, token: str | None = None, ctx: zmq.Context | None = None):
    self.ctx = ctx or zmq.Context.instance()
    self.sock = self.ctx.socket(zmq.SUB)
    self.sock.setsockopt(zmq.RCVHWM, 10)
    self.sock.setsockopt(zmq.LINGER, 0)
    if token:
      self.sock.setsockopt(zmq.PLAIN_USERNAME, b"bogpilot")
      self.sock.setsockopt(zmq.PLAIN_PASSWORD, token.encode())
    self.sock.setsockopt(zmq.SUBSCRIBE, TOPIC)
    self.sock.connect(endpoint)

  def recv(self, timeout_ms: int = 1000) -> dict | None:
    if not self.sock.poll(timeout_ms):
      return None
    topic, dat = self.sock.recv_multipart()
    return json.loads(dat)

  def close(self) -> None:
    self.sock.close(0)


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument("--host", required=True)
  ap.add_argument("--port", type=int, default=8766)
  ap.add_argument("--token")
  ap.add_argument("--udp", help="forward each payload to host:port as UDP")
  ap.add_argument("--show-gps", action="store_true")
  a = ap.parse_args()
  cl = Client(f"tcp://{a.host}:{a.port}", a.token)
  udp = None
  if a.udp:
    h, p = a.udp.rsplit(":", 1)
    udp = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM), (h, int(p)))
  n, t0 = 0, time.monotonic()
  while True:
    m = cl.recv()
    if m is None:
      continue
    n += 1
    if udp:
      udp[0].sendto(json.dumps(m, separators=(",", ":")).encode(), udp[1])
    if time.monotonic() - t0 >= 1.0:
      car, g, ld = m.get("car", {}), m.get("gps", {}), m.get("leads", {}).get("leadOne")
      gps = f"{g.get('lat')},{g.get('lon')}" if a.show_gps else ("fix" if g.get("valid") else "nofix")
      print(f"{n:3d} Hz  v={car.get('vEgo')} steer={car.get('steeringAngleDeg')} gps={gps} hdg={g.get('headingDeg')} "
            f"lead={ld[1] if ld and ld[0] else None} model={'model' in m}")
      n, t0 = 0, time.monotonic()


if __name__ == "__main__":
  main()
