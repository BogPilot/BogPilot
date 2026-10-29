# Safety replay

Offline tools that run recorded drive logs through the panda safety code
(`tests/libpanda`, the same C as the firmware, built for x86). Nothing here
flashes or changes the panda.

- `replay_drive.py`: upstream comma tool, any safety mode. It counts blocked
  TX and invalid RX.
- `replay_ap1.py`: BogPilot's Tesla AP1 tool, safety param 10. Details below.

## replay_ap1.py

From the repo root (needs `gcc`, `pycapnp`, `cffi`, and `zstandard` for `.zst`):

```
python panda/tests/safety_replay/replay_ap1.py <rlog | rlog.zst | rlog.bz2 | route dir> [...] [--summary]
```

A directory is searched recursively for `rlog*` files, sorted by segment
number. Useful options:

| option | what it does |
| --- | --- |
| `--summary` | counts and flags only, no timelines |
| `--json FILE` | full report as JSON |
| `--safety-rev REV` | build libpanda from another git revision's `panda/board` (for example the safety that was on the car) |
| `--rx-only` | skip openpilot's sendcan, so you see the safety code's behavior on its own |
| `--from-start` | run tesla safety from the first frame instead of following `pandaStates` |
| `--grace-actuator S`, `--grace-cluster S` | how long after a disengage a dropped stock frame is still OK (1 s / 5 s) |

What it reports:

1. **TX**: each openpilot frame the safety would block, with a decoded reason
   (angle rate, steer type while not engaged, accel while longitudinal not
   allowed, and so on). Blocks within 250 ms of an engage are tagged.
2. **controls_allowed**: every engage and disengage, and the rx frame or rx
   check timeout that caused it, next to openpilot's own enabled state.
3. **Stock DAS forwarding**: for 0x488, 0x2b9, 0x399, 0x389 and 0x239 from bus
   2, whether the panda would forward each frame to the chassis or drop it,
   as time runs. A stock frame dropped while openpilot is not engaged is
   flagged. That is the 2026-10-04 bug, where stock Autopilot and AEB were
   cut at boot.
4. **The car's own panda**, where the log has it: `pandaStates.controlsAllowed`,
   rejected TX echoes (src 192+bus), and forwarded echoes (src 128 copies of
   bus-2 frames, on tres/H7). If the replay disagrees with the car, the car was
   probably running different panda firmware.

The replay starts when `pandaStates` shows the panda in tesla mode and stops
when it leaves. The panda timer follows `logMonoTime`, and the replay runs
`safety_tick` at 1 Hz like the firmware does.

Exit code 0 means no FAIL flags, 1 means FAIL flags, 2 means bad input. Before
it runs, `tests/libpanda/libpanda.so` is rebuilt if it is older than any
`panda/board` source, so you never replay stale safety code.

Example: the Oct 4 boot logs against the safety that was on the car, then the fix:

```
python panda/tests/safety_replay/replay_ap1.py <boot logs> --summary --rx-only --safety-rev 6379b77c   # FAIL: stock 0x488/0x2b9 dropped while not engaged
python panda/tests/safety_replay/replay_ap1.py <boot logs> --summary --rx-only                        # PASS
```

Test with a synthetic fixture: `python -m pytest panda/tests/safety_replay/test_replay_ap1.py`.

A clean replay does not validate the car on the road.
