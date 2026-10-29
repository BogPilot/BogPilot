# Hotspot notes (Phase 0, read-only)

Clones: FrogPilot `1e23dec`, earlytesla `501c7de`, frog_ap1 `73a16cd`, Tinkla opendbc `9c0b6fe`, Tinkla panda `f7751e4`.

## earlytesla `selfdrive/car/tesla/`

Package files: `interface.py`, `carstate.py`, `carcontroller.py`, `teslacan.py`, `values.py`, `radar_interface.py`, `ck_fingerprint.py`, `tunes.py`, `ACC_module.py`, `PCC_module.py`, `LONG_module.py`, `HUD_module.py`, plus `speed_utils/`, `ibooster_tools/`, `pedal_calibrator/`, `radar_tools/`, `tinkla/` assets. Not a `CarControllerBase` subclass.

`values.py`

- `CAR.AP2_MODELS = 'TESLA AP2 MODEL S'`, `AP1_MODELS = 'TESLA AP1 MODEL S'`, `PREAP_MODELS = 'TESLA MODEL S'`, `AP1_MODELX = 'TESLA AP1 MODEL X'`.
- `FINGERPRINTS`: legacy addr→len dicts (two AP1 S samples, one AP1 X, one 2013 pre-AP). Comment says iBooster pre-AP must be forced.
- DBC: `tesla_powertrain` + `tesla_radar` + chassis `tesla_can` or `tesla_can_pre1916` via `TinklaPost1916Fix` (default True → `tesla_can`).
- Bus maps: chassis 0 for all; radar 1; autopilot bus 2 except pre-AP −1; powertrain 4 for AP2 and AP1 X, 0 for AP1 S, −1 for pre-AP; `CAN_AP_POWERTRAIN` 6 / 2 / −1. `USE_REAL_PID` True only for pre-AP.
- `CruiseButtons`: `SpdCtrlLvr_Stat` comment `32 DN_1ST, 16 UP_1ST, 8 DN_2ND, 4 UP_2ND, 2 RWD, 1 FWD, 0 IDLE`.
- `CruiseState`: OFF, STANDBY, ENABLED, STANDSTILL, OVERRIDE, FAULT, PRE_FAULT, PRE_CANCEL.
- `CarControllerParams.RATE_LIMIT_UP/DOWN` angle rate tables; jerk ±8. Not the same numbers as current FrogPilot `ANGLE_RATE_LIMIT_*`.

`carstate.py` `class CarState(CarStateBase)`

- Methods: `__init__`, `_convert_to_DAS_fusedSpeedLimit`, `compute_speed`, `update`, `get_can_parser`, `get_cam_can_parser`.
- Follow: `self.cruise_distance = cp.vl["STW_ACTN_RQ"]["DTR_Dist_Rq"]`; if not 255, `ret.followDistanceS = int(self.cruise_distance/33)`. Param `TinklaFollowDistance` default 1.45 s. **No separate stalk module.**
- HAO: `enableHAO` clears `gasPressed` and sets `DAS_216_driverOverriding`.
- pre-AP: pedal interceptor `GAS_SENSOR` (`TinklaPedalCanZero` chooses bus), `enableHumanLongControl` from `TinklaForceTeslaPreAP` or autopilot disabled, `TinklaHasIBooster`, `TinklaHandsOnLevel` default 2, IC integration forced on unless pre-AP.
- Bug-shaped quirk (do not copy blindly): AP1 X sets `teslaModel = "S"` and AP1 S/pre-AP/AP2 set `teslaModel = "X"`.

`carcontroller.py` `class CarController`

- Owns `HUDController` and `LONGController`. Subscribes itself to `longitudinalPlan`, `radarState`, `modelV2`, `controlsState` (old process style; do not revive).
- `update(...)` gates AP cars on a ~1 s cruise delay (`frame - cruiseDelayFrame > 30`) because AP status is 2 Hz.
- Disengaged path cancels cruise unless `enableHumanLongControl`.

`teslacan.py` `class TeslaCAN`

- Checksum, `create_ibst_command`, `create_lane_message`, `create_lead_car_object_message`, `create_body_controls_message`, `create_telemetry_road_info`, `create_steering_control`, `create_ap1_long_control`, `create_ap2_long_control`, `create_das_warningMatrix0/1/3`, `create_das_status`, `create_das_status2`, `create_brake_wipe_request`, `create_action_request`, `create_fake_DAS_msg`, `create_pedal_command_msg`, `create_longitudinal_commands`.

`HUD_module.py` `HUDController`: `compute_path_pinv`, `model_polyfit`, `showLeadCarOnICCanMessage`, `get_path_length_idx`, `update`. `IC_LANE_SCALE = 0.5`.

`LONG_module.py` `LONGController.update`. `PCC_module.py` `PCCController` (pre-AP), `STALK_DOUBLE_PULL_MS = 750`, `ENABLE_REGEN_MODS = False`, `MAX_BRAKE_VALUE` comment says iBooster value is TODO. `ACC_module.py` `ACCController` drives stock stalk buttons (up/dn 1st/2nd) rather than a direct accel torque on some paths.

`interface.py`: `CarInterface.get_params`, `update`, `apply`, `get_tesla_accel_limits`. Unsafe-mode bits include `UNSAFE_DISABLE_DISENGAGE_ON_GAS`. Do not port disabling gas disengage.

`radar_interface.py`: `RadarInterface`, Bosch msgs `0x310` step 3, `BOSCH_MAX_DIST = 250`.

## earlytesla panda `board/safety/safety_tesla.h`

- `tesla_hooks`: `.init = tesla_init`, `.rx = tesla_rx_hook`, `.tx = tesla_tx_hook`, `.fwd = tesla_fwd_hook`. Registered as `SAFETY_TESLA` 10 in `safety.h`.
- Flags: `FLAG_TESLA_POWERTRAIN=1`, `LONG_CONTROL=2`, `RADAR_BEHIND_NOSECONE=4`, `HAS_IC_INTEGRATION=8`, `HAS_AP=16`, `NEED_RADAR_EMULATION=32`, `ENABLE_HAO=64`, `HAS_IBOOSTER=128`.
- TX allow-lists: `TESLA_AP_TX_MSGS` (DAS steer `0x488`, long `0x2B9`/`0x209`, stalk `0x45`, HUD `0x399` `0x389` `0x239` `0x309` `0x3A9`, body `0x3E9`, warning matrices, fake `0x659`) and `TESLA_PREAP_TX_MSGS` (adds `0x214` EPB). PT panda: `TESLA_PT_TX_MSGS` `{0x2bf}`.
- Tests: `tests/safety/test_tesla.py` classes `TestTeslaSafety`, `TestTeslaSteeringSafety` (`test_angle_cmd_when_enabled/disabled`, `test_acc_buttons`), `TestTeslaLongitudinalSafety` (`test_no_aeb`, `test_acc_accel_limits`), `TestTeslaChassisLongitudinalSafety`, `TestTeslaPTLongitudinalSafety`.
- Angle rate lookups differ from current FrogPilot (`TESLA_LOOKUP_ANGLE_RATE_UP` speeds 2/7/17). Keep the stricter table when porting; do not copy numbers without a comparison note.

## earlytesla opendbc

Files: `tesla_can.dbc`, `tesla_can_pre1916.dbc`, `tesla_powertrain.dbc`, `tesla_radar.dbc`.

`tesla_can.dbc` `VAL_ 69 DTR_Dist_Rq`: 0 `ACC_DIST_1`, 33 `ACC_DIST_2`, 66 `ACC_DIST_3`, 100 `ACC_DIST_4`, 133 `ACC_DIST_5`, 166 `ACC_DIST_6`, 200 `ACC_DIST_7`, 255 `SNA`.

IC BOs present here and **not** all present under the same names upstream: `DAS_object` 777, `DAS_status` 921, `DAS_telemetry` 937, plus `DAS_lanes` 569, `DAS_status2` 905, `DAS_bodyControls` 1001, `DAS_control` 697, `DAS_longControl` 521, `DAS_steeringControl` 1160.

## frog_ap1 Tesla package (bridge)

Same filenames as upstream Model S, not Tinkla. `CAR` platforms: `TESLA_AP1_MODELS`, `TESLA_AP2_MODELS`, `TESLA_MODELS_RAVEN` with Bosch vs Continental radar DBC. `BUTTONS` removed. `CarState.update(self, cp, cp_cam, frogpilot_toggles)` returns `(ret, fp_ret)` where `fp_ret` is `custom.FrogPilotCarState`. Parses `ESP_B`, `DI_torque1/2`, `EPAS_sysStatus`, `DI_state`, `GTW_carState`, `BrakeMessage`, `SDM1` or `DriverSeat` for Raven. Copies `STW_ACTN_RQ` into the parser list but **does not read `DTR_Dist_Rq`**. `CarController.update` sends steer every 2 frames and long every 4 frames even as a simplified `create_longitudinal_command` (no `openpilotLongitudinalControl` guard in this version). `interface._get_params` forces long on and `SafetyModel.tesla` flags 0, `dashcamOnly = False`. `fingerprints.py` is FW byte strings for AP2 and Raven only (no AP1 FW block). `launch_env.sh` exports `FINGERPRINT=TESLA_AP1_MODELS`.

`panda/board/safety/safety_tesla.h` on this branch has `TESLA_STEERING_LIMITS` and `TESLA_LONG_LIMITS` but **no `TESLA_FLAG_*` symbols**. `tesla_tx_hook` still angle-checks `0x488` and restricts `0x45` buttons. This is a fork of an older safety file, not the Tinkla flag set and not the current Raven/powertrain file.

FrogPilot code in this snapshot still lives at `selfdrive/frogpilot/`, not top-level `frogpilot/`.

## Current FrogPilot

`selfdrive/car/interfaces.py`: `CarInterfaceBase` (line 94), `RadarInterfaceBase` (440), `CarStateBase` (455), `CarControllerBase` (571). Tesla classes subclass these. Car process: `selfdrive/car/card.py`. Controls: `selfdrive/controls/controlsd.py`, `selfdrive/controls/radard.py`, `selfdrive/controls/lib/longitudinal_mpc_lib/`.

`selfdrive/car/tesla/values.py` `class CAR(Platforms)`: `TESLA_AP1_MODELS` docs "Tesla AP1 Model S", specs mass 2100 wheelbase 2.959 steerRatio 15, dbc `tesla_powertrain` / `tesla_radar_bosch_generated` / chassis `tesla_can`. `TESLA_AP2_MODELS` shares specs and dbc. `TESLA_MODELS_RAVEN` uses `tesla_radar_continental_generated`. `FW_QUERY_CONFIG` UDS tester-present to EPS, ADAS, iBooster, radar. `BUTTONS` has blinkers and `SpdCtrlLvr_Stat` accel/decel/cancel/resume. No distance button. `CANBUS` matches Tinkla's numeric map. `CarControllerParams.ANGLE_RATE_LIMIT_UP` speed bp 0/5/15, angles 10 / 1.6 / 0.3; down 10 / 7.0 / 0.8; jerk ±8; `ACCEL_TO_SPEED_MULTIPLIER = 3`.

`interface.py`: `dashcamOnly = True` with comment that steer blending is unsafe. Long + second panda only if fingerprint bus `autopilot_powertrain` contains `0x2bf`. Flags `Panda.FLAG_TESLA_RAVEN`, `FLAG_TESLA_LONG_CONTROL`, `FLAG_TESLA_POWERTRAIN`. `_get_params(..., frogpilot_toggles)` uses `frogpilot_toggles.disable_openpilot_long`.

`carcontroller.py`: `create_steering_control` at 50 Hz-ish (every 2 frames), `create_longitudinal_commands` only if `CP.openpilotLongitudinalControl`, PCM cancel spam on `hands_on_fault` via `create_action_request` on chassis and autopilot chassis. `# TODO: HUD control`.

`carstate.py`: `hands_on_level` from `EPAS_handsOnLevel`; `steeringPressed` if level > 0; copies `STW_ACTN_RQ` to `msg_stw_actn_req`. No follow-distance field.

`teslacan.py`: `create_steering_control`, longitudinal helpers, `create_action_request`. Checksum helper. No IC builders.

`panda/board/safety/safety_tesla.h`: `TESLA_STEERING_LIMITS`, `TESLA_LONG_LIMITS` (max_accel 425 ≈ 2 m/s², min_accel 287 ≈ −3.52). Flags `TESLA_FLAG_POWERTRAIN=1`, `LONGITUDINAL_CONTROL=2`, `RAVEN=4`. TX `0x488`, `0x45` (bus 0 and 2), `0x2b9`; PT TX `0x2bf`. `tesla_init` picks `tesla_pt_rx_checks` / `tesla_raven_rx_checks` / `tesla_rx_checks`. `tesla_fwd_hook` blocks stock `0x488` and, if longitudinal and not stock AEB, blocks DAS control (`0x2bf` on PT else `0x2b9`). Hooks struct at line 244. No `panda/tests/safety/test_tesla.py` in this tree (only `panda/tests/safety_replay`).

opendbc Tesla files: `tesla_can.dbc`, `tesla_powertrain.dbc`, `tesla_radar_bosch_generated.dbc`, `tesla_radar_continental_generated.dbc`. `DTR_Dist_Rq` table matches Tinkla (line 737). `BO_ 921` is `AutopilotStatus`, not `DAS_status`. No `BO_` for 777 or 937.

## FrogPilot extension package (do not fork)

- `frogpilot/controls/lib/frogpilot_following.py`: `FrogPilotFollowing.update` uses traffic tables or `get_T_FOLLOW(aggressive_follow, standard_follow, relaxed_follow, custom_personalities, controlsState.personality)`.
- `frogpilot/controls/frogpilot_planner.py`: writes `desiredFollowDistance`, SLC fields.
- `frogpilot/controls/lib/conditional_experimental_mode.py`: `ConditionalExperimentalMode`.
- `frogpilot/system/speed_limit_filler.py`: `SpeedLimitFiller`.
- `frogpilot/system/the_pond/`: The Pond.
- `frogpilot/ui/layouts/settings/toggle_metadata.py`: `ToggleDefinition`, `LongitudinalPersonality` buttons Aggressive/Standard/Relaxed, `ConditionalExperimental`, personality profiles `AggressiveFollow` default text 1.25 s. Distance-button routing already exists in `frogpilot/common/frogpilot_variables.py` (`personality_profile_via_distance`, `traffic_mode_via_distance`) for cars that have a distance button. Tesla stalk should call that adapter, not a new longitudinal planner.
- UI panels: `frogpilot/ui/qt/onroad/`, `frogpilot/ui/frogpilot_ui.cc` (`always_on_lateral_active`, `traffic_mode_enabled`).

## Docs that are not harness manuals

`Dockerfile.tesla_openpilot` starts `FROM ghcr.io/boggyver/openpilot-base:latest`. Behavior reference only; do not resurrect it.

`docs/CARS.md` / `docs/INTEGRATION.md` / `README.md` in earlytesla have no Tesla Model S/X harness section. EPAS flasher README is a firmware tool (`selfdrive/car/modules/teslaEpasFlasher/README.md`).
