# Credits

BogPilot stays under the MIT license. Do not relicense.

This tree is a research fork of FrogPilot / openpilot. Not a product. No warranty. The driver remains responsible. Comply with local law. Not safe to drive until a human validates it on a bench and in a car. This tree has not been validated on a bus (no captured route). `dashcamOnly` remains true.

## Attribution

- **comma.ai and the openpilot contributors.** Upstream openpilot. The rewritten FrogPilot history in this repository roots at openpilot v0.9.7 (`e05eeaaaa0783401eab433cb0d678dd9f598ebe7`). That root commit carries the MIT `LICENSE` text (Copyright (c) 2018, Comma.ai, Inc.). The README badge still points at MIT.

- **FrogAi / FrogPilot contributors.** Architecture and shared framework donor. Pinned base: branch `FrogPilot`, SHA `1e23dec6352cef5a36a87be0af7d7a082b7c48a4`, https://github.com/FrogAi/FrogPilot.

- **BogGyver / Tinkla.** Tesla early (pre-AP / AP1 / AP2 Model S/X) behavior reference only. Clone used here: https://github.com/bbqgloves/earlytesla-openpilot branch `tesla_unity_dev` SHA `501c7de91b59c70510e9dc1585acfde9b7102c93`. Upstream history: BogGyver/openpilot. Site context: https://tinkla.us/. Interface code and documented behavior only. Do not copy Tesla firmware, Autopilot binaries, maps, or neural weights. The AP1 instrument-cluster frames (`selfdrive/car/tesla/cluster.py`, AP1 cluster rules in `panda/board/safety/safety_tesla.h`) follow Tinkla's `HUD_module.py`, `teslacan.py`, and earlytesla-panda `f7751e4` `TESLA_AP_FWD_MODDED`; BogPilot's own code, see `docs/tesla/DIVERGENCES.md`. One cluster bit field (0x389 CSA state, 32|2) is taken from Tinkla's `tesla_can.dbc` in `BogGyver/opendbc` `9c0b6fe`.

- **bbqgloves bridge.** https://github.com/bbqgloves/openpilot branch `frog_ap1` SHA `73a16cdd50033cb9f57a03fd619e2129558eef43`. Used as a conflict map of which FrogPilot-era files were bent for AP1. Not git-merged into BogPilot. See `docs/tesla/BRIDGE_DIFF.md`.

Pinned SHAs and the source hierarchy are in `docs/tesla/SOURCES.md`. Safety and behavior disagreements with those trees are in `docs/tesla/DIVERGENCES.md`.
