# Rename map

Tinkla path (`earlytesla-openpilot` @ `501c7de`) → `frog_ap1` @ `73a16cd` → current FrogPilot @ `1e23dec`.

`frog_ap1` did not carry the Tinkla package. Its `selfdrive/car/tesla/` is the comma/FrogPilot Model S port (AP1 S, AP2 S, Raven), then stripped. "no equivalent" means the file is absent, not that the behavior was ported.

## Car interface

| Tinkla | frog_ap1 | Current FrogPilot |
| --- | --- | --- |
| `selfdrive/car/tesla/interface.py` (`CarInterface`) | `selfdrive/car/tesla/interface.py` | `selfdrive/car/tesla/interface.py` |
| `selfdrive/car/tesla/carstate.py` (`CarState`) | `selfdrive/car/tesla/carstate.py` | `selfdrive/car/tesla/carstate.py` |
| `selfdrive/car/tesla/carcontroller.py` (`CarController`, not a `CarControllerBase`) | `selfdrive/car/tesla/carcontroller.py` (`CarControllerBase`) | `selfdrive/car/tesla/carcontroller.py` |
| `selfdrive/car/tesla/values.py` (`CAR.PREAP_MODELS`, `AP1_MODELS`, `AP1_MODELX`, `AP2_MODELS`, message-count `FINGERPRINTS`) | `selfdrive/car/tesla/values.py` (`CAR.TESLA_AP1_MODELS`, `TESLA_AP2_MODELS`, `TESLA_MODELS_RAVEN`, `PlatformConfig`) | same path, same three platforms (no pre-AP, no AP1 X) |
| `selfdrive/car/tesla/teslacan.py` (`TeslaCAN`, IC + AP1/AP2 long + pedal + ibooster) | `selfdrive/car/tesla/teslacan.py` (steer + one `DAS_control` long) | `selfdrive/car/tesla/teslacan.py` (steer, long, stalk cancel) |
| `selfdrive/car/tesla/radar_interface.py` | `selfdrive/car/tesla/radar_interface.py` | `selfdrive/car/tesla/radar_interface.py` |
| `selfdrive/car/tesla/ck_fingerprint.py` | no equivalent (FW fingerprint in `fingerprints.py`) | no equivalent (`FW_QUERY_CONFIG` lives in `values.py`; brand fingerprints in `selfdrive/car/fingerprints.py`) |
| `selfdrive/car/tesla/fingerprints.py` | `selfdrive/car/tesla/fingerprints.py` (`FW_VERSIONS` only) | no separate file; FW query config is `values.py` |
| `selfdrive/car/tesla/tunes.py` (`LongTunes`, pedal calib) | no equivalent | no equivalent |
| `selfdrive/car/tesla/ACC_module.py` (`ACCController`) | no equivalent | no equivalent |
| `selfdrive/car/tesla/PCC_module.py` (`PCCController`, pre-AP regen/pedal) | no equivalent | no equivalent |
| `selfdrive/car/tesla/LONG_module.py` (`LONGController`) | no equivalent | no equivalent |
| `selfdrive/car/tesla/HUD_module.py` (`HUDController`) | no equivalent (`# TODO: HUD control` in carcontroller) | no equivalent (same TODO) |
| `selfdrive/car/tesla/speed_utils/` | no equivalent | no equivalent |
| `selfdrive/car/tesla/ibooster_tools/` | no equivalent | no equivalent |
| `selfdrive/car/tesla/pedal_calibrator/` | no equivalent | no equivalent |
| `selfdrive/car/tesla/radar_tools/` | no equivalent | no equivalent |
| `selfdrive/car/modules/HSO_module.py` (`HSOController`) | no equivalent (hands-on level in carstate) | no equivalent (hands-on level in carstate) |
| `selfdrive/car/modules/ALC_module.py` | no equivalent (ALC is planner/model, not a car module) | no equivalent |
| `selfdrive/car/modules/BLNK_module.py` | no equivalent | no equivalent |
| `selfdrive/car/modules/CFG_module.py` (Tinkla params) | no equivalent | FrogPilot toggles: `frogpilot/ui/layouts/settings/toggle_metadata.py`, `frogpilot/common/frogpilot_variables.py` |
| `selfdrive/car/modules/teslaEpasFlasher/` | no equivalent | no equivalent. Do not port. It flashes EPAS firmware. |
| stalk follow map (inline `CarState.update`, `DTR_Dist_Rq`) | no equivalent (BUTTONS list deleted) | no `stalk_follow.py`. `BUTTONS` in `values.py` covers blinkers and speed stalk only, not `DTR_Dist_Rq` |
| base classes in `selfdrive/car/interfaces.py` (old names) | `CarInterfaceBase`, `CarStateBase`, `CarControllerBase`, `RadarInterfaceBase` | same classes, still `selfdrive/car/interfaces.py`. Runtime process is `selfdrive/car/card.py` plus `selfdrive/controls/controlsd.py`. |

## Controls and UI

| Tinkla | frog_ap1 | Current FrogPilot |
| --- | --- | --- |
| `selfdrive/controls/controlsd.py` (old process model) | `selfdrive/controls/controlsd.py` | `selfdrive/controls/controlsd.py` still present; longitudinal MPC in `selfdrive/controls/lib/longitudinal_mpc_lib/` |
| follow distance scattered in carstate / `TinklaFollowDistance` | `selfdrive/frogpilot/` (package not yet split) | `frogpilot/controls/lib/frogpilot_following.py` (`FrogPilotFollowing`), personality via `controlsState.personality` |
| `selfdrive/controls/lib/longcontrol.py` era | FrogPilot planner under `selfdrive/frogpilot/` | `frogpilot/controls/frogpilot_planner.py`, `frogpilot/controls/lib/frogpilot_vcruise.py` |
| radard separate | `selfdrive/controls/radard.py` still in that tree | `selfdrive/controls/radard.py` still exists; `selfdrive/car/card.py` is the car process |
| Qt settings silo / Tinkla params | `selfdrive/frogpilot/frogpilot_functions.py` | `frogpilot/ui/layouts/settings/toggle_metadata.py` (`ToggleDefinition`). Onroad UI: `frogpilot/ui/qt/onroad/`. The Pond: `frogpilot/system/the_pond/` |
| IC written from `HUDController` (not Qt) | not implemented | not implemented (carcontroller TODO) |

## opendbc

| Tinkla (`BogGyver/opendbc` @ `9c0b6fe`) | frog_ap1 `opendbc/` (vendored) | Current FrogPilot `opendbc/` (vendored) |
| --- | --- | --- |
| `tesla_can.dbc` (also `tesla_can_pre1916.dbc` selected by `TinklaPost1916Fix`) | `tesla_can.dbc` | `tesla_can.dbc` |
| `tesla_powertrain.dbc` | `tesla_powertrain.dbc` | `tesla_powertrain.dbc` |
| `tesla_radar.dbc` | `tesla_radar_bosch_generated.dbc`, `tesla_radar_continental_generated.dbc` | same generated Bosch/Continental names. No `tesla_radar.dbc`. |
| no `tesla_can_pre1916.dbc` on FrogPilot | no pre-1916 file | no pre-1916 file |
| n/a | `tesla_model3_party.dbc`, `tesla_model3_vehicle.dbc` (Model 3, out of v1 scope) | not present under those names |
| generators under Tinkla opendbc if any | `opendbc/generator/tesla/` (`tesla_radar_bosch.py`, `tesla_radar_continental.py`) | no `opendbc/generator/tesla/` in the pinned tree |

`DTR_Dist_Rq` on `STW_ACTN_RQ` (addr 69) exists in both Tinkla and current `tesla_can.dbc` with the same 7-value table (`ACC_DIST_1`..`ACC_DIST_7`, `SNA` 255).

IC id rename: Tinkla `BO_ 921 DAS_status` is `BO_ 921 AutopilotStatus` in current `tesla_can.dbc`. Tinkla `DAS_object` (777) and `DAS_telemetry` (937) are not `BO_` lines in current `tesla_can.dbc` or `tesla_powertrain.dbc`.

## panda safety

| Tinkla `BogGyver/panda` @ `f7751e4` | frog_ap1 | Current FrogPilot |
| --- | --- | --- |
| `board/safety/safety_tesla.h` (`tesla_hooks`, `SAFETY_TESLA` 10) | `panda/board/safety/safety_tesla.h` (flags stripped) | `panda/board/safety/safety_tesla.h` (`tesla_hooks`, `TESLA_FLAG_POWERTRAIN/LONGITUDINAL_CONTROL/RAVEN`) |
| `board/safety.h` registers `SAFETY_TESLA` | `panda/board/safety.h` same id 10 | `panda/board/safety.h` same id 10 |
| `tests/safety/test_tesla.py` (`TestTeslaSteeringSafety`, `TestTeslaChassisLongitudinalSafety`, `TestTeslaPTLongitudinalSafety`) | `panda/tests/safety/test_tesla.py` | no `panda/tests/safety/`; replay helper `panda/tests/safety_replay` only |
| `examples/tesla_tester.py` | `panda/examples/tesla_tester.py` | no equivalent found |

## Docs and launch

| Tinkla | frog_ap1 | Current FrogPilot |
| --- | --- | --- |
| `README.md` (stock comma text, not a Tesla harness guide) | `README.md` (FrogPilot marketing, `frogpilot.download`) | `README.md` |
| `docs/CARS.md`, `docs/INTEGRATION.md` (stock comma; no Tesla section found) | stock `docs/` | `selfdrive/car/CARS_template.md`, `docs/` |
| `Dockerfile.tesla_openpilot` (`ghcr.io/boggyver/openpilot-base`) | no equivalent | no equivalent. Do not resurrect the old launcher. |
| `launch_openpilot.sh`, `launch_chffrplus.sh`, `launch_env.sh` | `launch_env.sh` exports `FINGERPRINT=TESLA_AP1_MODELS` | `launch_env.sh` present; do not force a global fingerprint |

## Destination names for BogPilot (not created this phase)

Future tree matches current FrogPilot. New Tesla-early files land beside the existing package, not a second process model:

- `selfdrive/car/tesla/stalk_follow.py` (new; does not exist in any of the three trees)
- platform enum extension in `selfdrive/car/tesla/values.py`
- toggles in `frogpilot/ui/layouts/settings/toggle_metadata.py` under a Tesla Early category
- docs under `docs/tesla/`
