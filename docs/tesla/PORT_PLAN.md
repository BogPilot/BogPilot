# BogPilot Tesla early port plan

Phase 0 only. No product code in this inventory. Base is FrogPilot `1e23dec6352cef5a36a87be0af7d7a082b7c48a4`. Behavior donor is earlytesla `501c7de91b59c70510e9dc1585acfde9b7102c93`. Bridge `73a16cd` is a conflict map, not a merge source.

Research disclaimer for every later README, installer string, and release note: not a product, no warranty, driver remains responsible, comply with local law. Do not claim safe to drive until a human validates bench and car.

## What the bridge actually changed

`git diff c1c9eec..73a16cd` (8 files, −249/+89):

- `interface.py`: parent had `dashcamOnly = True` and dual-panda long detection (`0x2bf` on `CANBUS.autopilot_powertrain`, `FLAG_TESLA_LONG_CONTROL` plus `FLAG_TESLA_POWERTRAIN`). Bridge sets `dashcamOnly = False`, forces `openpilotLongitudinalControl`, and a single safety config with flags `0`.
- `values.py`: deletes `BUTTONS` (blinker and speed stalk). Replaces jerk/accel-to-speed with `ACCEL_MAX = 2.0`, `ACCEL_MIN = -3.48`, jerk ±4.9.
- `safety_tesla.h`: drops the powertrain/Raven flag machinery (no `TESLA_FLAG_*` left in the file).
- `launch_env.sh`: `export FINGERPRINT="TESLA_AP1_MODELS"`.
- `carcontroller.py` / `teslacan.py` / `carstate.py`: simplified long command; HUD still absent.
- `frogpilot_functions.py`: stops copying a custom boot logo.

**Do not port the bridge's safety weakening.** Keep current FrogPilot `dashcamOnly` until a panda safety mode for that platform exists and tests pass. Keep torque/angle rate limits and counter-steer disengage at least as strict as current `TESLA_STEERING_LIMITS`. If Tinkla is looser, keep the stricter check and record the gap in `DIVERGENCES.md`.

Current FrogPilot already ships a **dashcam-only** Tesla Model S interface (`TESLA_AP1_MODELS`, `TESLA_AP2_MODELS`, `TESLA_MODELS_RAVEN`) with angle steering, optional dual-panda longitudinal, and `# TODO: HUD control`. v1 adds pre-AP and splits AP1 S / AP1 X / AP2, it does not pretend the current package is empty.

## Safety rule

Do not weaken driver monitoring, torque or angle rate limits, or engage/disengage paths to make Tesla work. Unknown platform, stale checksum, missing cruise-available bit, or ambiguous fingerprint → controls not allowed (fail closed). No Python-only safety. No actuation stub that still shows engaged.

## Params (FrogPilot toggles, not a Tinkla silo)

Add under a **Tesla Early** category in `frogpilot/ui/layouts/settings/toggle_metadata.py`, read from `frogpilot/common/frogpilot_variables.py`. Do not rename existing FrogPilot flags The Pond or other cars use; alias if a name must change.

| Param | Values | Default |
| --- | --- | --- |
| `TeslaPlatform` | `preap`, `ap1_s`, `ap1_x`, `ap2` | unset until fingerprint matches; ambiguous → no longitudinal |
| `TeslaLongControl` | `off`, `lateral_only`, `full` | `off` until platform is known; AP2 UI offers `lateral_only` (stock ACC keeps long) vs `full` |
| `TeslaStalkFollow` | bool | on for early Tesla fingerprints only |

Tinkla equivalents to reimplement, not copy as a parallel settings tree: `TinklaFollowDistance`, `TinklaForceTeslaPreAP`, `TinklaHasIcIntegration`, `TinklaHasIBooster`, `TinklaEnableHAO` / `FLAG_TESLA_ENABLE_HAO`, `TinklaPedalCanZero`, `TinklaPost1916Fix`, `TinklaAutopilotDisabled`. Map behavior into the three params plus existing FrogPilot longitudinal/personality toggles.

## Feature contract

### Longitudinal and stalk

| Feature | Source | Destination | Risk | Test |
| --- | --- | --- | --- | --- |
| 7 follow detents. `DTR_Dist_Rq` on `STW_ACTN_RQ`: 0, 33, 66, 100, 133, 166, 200 = `ACC_DIST_1`..`ACC_DIST_7`; 255 = `SNA` (no change). Tinkla does `int(cruise_distance/33)` in `carstate.py` (~line 285) and stores seconds in `followDistanceS`. | `earlytesla-openpilot/selfdrive/car/tesla/carstate.py`; signal in both `tesla_can.dbc` files | `selfdrive/car/tesla/stalk_follow.py` pure function. Adapter only. Calls into `FrogPilotFollowing` / `controlsState.personality`. No magic numbers in `controlsd.py`. | med | Unit: all 7 detents → profile, plus hysteresis so edges do not flicker. `SNA` holds last profile. |
| Profile map. 7 detents onto Traffic, Aggressive, Standard, Relaxed, plus 3 intermediate steps. Personalities today are `LongitudinalPersonality` options Aggressive/Standard/Relaxed in `toggle_metadata.py`, plus traffic mode (`trafficModeEnabled`, `TRAFFIC_FOLLOW` in `frogpilot_following.py`). | Tinkla has no personality enum; FrogPilot `frogpilot/controls/lib/frogpilot_following.py` `get_T_FOLLOW` | same module `stalk_follow.py`; do not fork `long_mpc.py` | med | Table test of detent index → personality or traffic flag. Document the 7-row table in `docs/tesla/STALK.md`. |
| Human accel override (HAO). Tinkla `enableHAO` zeros `gasPressed` and sets `DAS_216_driverOverriding` so stock long does not fight the pedal. | `carstate.py` ~430; panda `FLAG_TESLA_ENABLE_HAO` | document per platform in `docs/tesla/PLATFORMS.md`. Implement only if it does not bypass `gas_pressed` disengage in current `safety_tesla.h`. If it conflicts, keep FrogPilot disengage-on-gas and record the gap. | high | Unit: gas pressed while engaged follows the stricter of Tinkla HAO vs current panda `gas_pressed` policy. State the chosen policy in the test name. |
| Human steer override. Tinkla `HSOController` plus `handsOnLimit` param (default 2). Current code: `hands_on_level > 0` sets `steeringPressed`; `>= 3` or `EAC_ERROR_HANDS_ON` cancels lateral and requests PCM cancel. | `selfdrive/car/modules/HSO_module.py`; current `carstate.py` / `carcontroller.py` | keep current hands-on cancel. Do not lower the level to make Tesla stay engaged. | high | Unit: hands-on ≥ 3 → no steer command and cancel requested. Counter-steer rate limit stays in panda. |
| pre-AP: lane keep, ALC, FCW, limited ACC regen only. Do not command friction brakes the car cannot accept. Tinkla pre-AP uses pedal interceptor (`PCC_module`, `create_pedal_command_msg`) and optional iBooster (`create_ibst_command`, `TinklaHasIBooster`). `CAN_POWERTRAIN` and `CAN_AUTOPILOT` are −1. | `PCC_module.py`, `LONG_module.py`, `teslacan.create_pedal_command_msg`, `create_ibst_command` | `selfdrive/car/tesla/carcontroller.py` behind a pre-AP branch. Pedal CAN and iBooster frames only if those signals exist in the pinned DBC or a supplied capture. Otherwise TODO and no long actuation. | high | Unit: pre-AP controller never emits a friction-brake command (`BrakeMessage` / iBooster apply) unless `TeslaPlatform=preap` and a named, sourced brake signal is enabled. Disengaged → no accel and no steer. |
| AP1 Model S and Model X: lane keep, ALC, FCW, full stop-and-go only if the reference actually commands it. Tinkla `create_ap1_long_control` writes chassis long (`DAS_control` / `DAS_longControl` style). `CruiseState.STANDSTILL = 3` exists. | `teslacan.create_ap1_long_control`, `ACC_module.py`, `LONG_module.update` | `carcontroller.py` + `teslacan.py` on chassis bus `CANBUS.chassis`. Separate fingerprints `ap1_s` and `ap1_x` (Tinkla `CAR.AP1_MODELS` vs `CAR.AP1_MODELX`). Current tree lumps "AP1 Model S" only. | high | Unit: engaged AP1 may emit long; disengaged emits none. Stop-and-go test only for the code path that references a real standstill command. If the port cannot show that command from DBC, mark stop-and-go deferred, do not fake engaged. |
| AP2/2.5 Model S/X: lateral on chassis CAN, longitudinal on powertrain CAN, dual-panda. UI `TeslaLongControl=lateral_only` vs `full`. Current FrogPilot already detects aux panda when `0x2bf` is on `CANBUS.autopilot_powertrain` (bus 6) and sets `FLAG_TESLA_POWERTRAIN` on the second safety config. Tinkla `CAN_POWERTRAIN = 4`, `CAN_AP_POWERTRAIN = 6`, `create_ap2_long_control`. | `values.py` `CANBUS`, `teslacan.create_ap2_long_control`, current `interface.py` `_get_params` | keep current dual-config pattern. `lateral_only` must not write powertrain long (`0x2bf` `DAS_control` on PT). | high | Unit: `lateral_only` packet list has no powertrain long ids. `full` writes them only when the second panda fingerprint is present. Ambiguous fingerprint → long refused. |

### Cluster and UX

| Feature | Source | Destination | Risk | Test |
| --- | --- | --- | --- | --- |
| IC coexistence: engagement, lane lines/status, speed, follow state, without fighting stock Autopilot graphics. Tinkla `HUDController.update`, `showLeadCarOnICCanMessage`, `TeslaCAN.create_das_status`, `create_das_status2`, `create_lane_message`, `create_lead_car_object_message`, `create_telemetry_road_info`, warning matrices. Panda forwards/modifies those ids when `FLAG_TESLA_HAS_IC_INTEGRATION`. | `HUD_module.py`, `teslacan.py` | frames built in `carcontroller.py` / `teslacan.py`, not Qt. On-device UI may mirror state only. | high | If a signal is missing from current DBC, defer that frame and name the signal. Known now: `0x399` is `AutopilotStatus` in current DBC vs `DAS_status` in Tinkla. `DAS_object` (777) and `DAS_telemetry` (937) are absent as `BO_` in current `tesla_can.dbc` / `tesla_powertrain.dbc`. Do not invent them. `DAS_lanes` (569), `DAS_status2` (905), `DAS_bodyControls` (1001) exist in both. |
| FrogPilot themes, Always On Lateral, Conditional Experimental Mode, Speed Limit Controller, model selector, The Pond stay intact for non-Tesla and are optional on Tesla when the interface supports them. | `frogpilot/` package | do not fork those modules. Tesla adapter must not change their params. | low | Non-Tesla car tests unchanged. Tesla fingerprint does not alter theme/pond code paths. |
| Tesla-only toggles under Tesla Early, not a second settings silo. | Tinkla `CFG_module` / `load_bool_param` | `toggle_metadata.py` | low | Toggle definitions load; absent on non-Tesla UI category filter if the panel is car-scoped. |

### Fingerprints

| Feature | Source | Destination | Risk | Test |
| --- | --- | --- | --- | --- |
| Distinct ids: pre-AP Model S, AP1 Model S, AP1 Model X, AP2 Model S/X. Current platforms are only AP1 S, AP2 S, Raven (Raven is not the v1 AP2 early contract; keep it so existing cars do not move). Tinkla fingerprints are legacy message-count dicts in `values.py` `FINGERPRINTS`, not FW 2.0. Modern car uses `FW_QUERY_CONFIG` and `fingerprints.py` FW versions. | Tinkla `values.py` `CAR` and `FINGERPRINTS`; current `values.py` `CAR(Platforms)` | extend `selfdrive/car/tesla/values.py`. Refuse longitudinal if match is ambiguous across preap/ap1/ap2. | high | Matrix test: each fixture selects one platform; a mixed or empty fingerprint selects none and sets long off. Non-Tesla fingerprints must not select Tesla (`selfdrive/car/fingerprints.py`). |
| `launch_env.sh` forced `FINGERPRINT=TESLA_AP1_MODELS` on the bridge | `frog_ap1/launch_env.sh` | do not port. | high | Boot without the env var still fingerprint-matches. |

### Harness notes (cite only; do not invent install steps)

No Tesla install procedure was found in pinned `README.md`, `docs/CARS.md`, or `docs/INTEGRATION.md` (those files are stock comma text). Do not write OBD-C, mirror-housing, or dual-harness steps from memory.

Cite only:

- Bus map comments in Tinkla and current `values.py` `class CANBUS`: chassis 0, radar 1, autopilot chassis 2, powertrain 4, private 5, autopilot powertrain 6. Labeled "Lateral harness" and "Longitudinal harness".
- Current `interface.py`: longitudinal harness assumed only when an auxiliary panda shows `0x2bf` on `CANBUS.autopilot_powertrain`.
- Tinkla `selfdrive/car/modules/teslaEpasFlasher/README.md` says to connect a comma panda to EPAS and run a patcher. That is firmware flashing. **Out of scope. Do not port the flasher, binaries, or patch steps.**
- Human still needs to supply harness type and target comma device (3X vs 4). Historical note in the mission: pre-AP Unity was comma 3X class. Pinned README only lists comma two/three generically. Do not claim comma 4 on pre-AP.

Write `docs/tesla/PLATFORMS.md` as a control matrix (what is commanded on which bus), not a shop manual.

### Explicit non-goals for v1

- HW3/HW4 Model 3/Y (already upstream/FrogPilot; `frog_ap1` even has `tesla_model3_*.dbc`, leave them).
- FSD computer flashing, MCU unlocks, any vehicle security bypass.
- EPAS or radar firmware flashing (`teslaEpasFlasher`, `radarFlasher`).
- Claiming comma 4 on pre-AP without a reference stack that runs there.
- Merging Tinkla or `frog_ap1` git history.
- Copying Tesla firmware, Autopilot binaries, maps, or weights.
- Replacing Tinkla behavior with any third-party legacy-Tesla fork outside the approved source order.
- Force-push of `main` / `BogPilot` after first public release (Phase 5; not this phase).

## Phase order

1. Fingerprints and `TeslaPlatform` enum. No actuation. Ambiguous → long refused.
2. `CarState` parsers: speed, steering angle, cruise state, stalk detents, blinkers, doors, EPAS status. Parse only signals present in pinned `tesla_can.dbc` / `tesla_powertrain.dbc` or a supplied capture.
3. `stalk_follow.py` and the 7-detent unit test. Wire to FrogPilot personalities.
4. Lateral controller (angle, as both Tinkla `create_steering_control` and current `DAS_steeringControl` do) behind panda `safety_tesla.h`. Port extra safety hooks in a dedicated commit with torque/angle rate and counter-steer tests. Current API is `RxCheck`, `safety_config tesla_init(uint16_t)`, `tesla_tx_hook`. Tinkla's `AddrCheckStruct` / `CANPacket_t *` hooks do not drop in.
5. Longitudinal: regen-only pre-AP, full AP1 if the reference command exists, dual-bus AP2. `TeslaLongControl`.
6. IC status frames from the car controller, or explicit deferral naming the missing signal (`DAS_object`, `DAS_telemetry`, and the `DAS_status` vs `AutopilotStatus` rename are the known gaps).
7. FrogPilot toggle wiring and UI copy (Tesla Early). Themes, AOL, CEM, SLC, model selector, The Pond unchanged.
8. Harness-mode docs that cite the bus map above and say what the human has not yet confirmed. No invented install steps.

## Fail-closed CAN

If a signal is not in the reference DBC, a captured route, or an upstream openpilot definition, mark TODO and do not actuate on it. Do not stub "engaged" with silent no-op commands.

Replay: no captured route was in these clones. Until the human supplies one route per platform, any future tag is `alpha-unverified-on-bus`.

## Non-regression

Existing FrogPilot car tests must still pass. Non-Tesla fingerprints must not select this interface. `pre-commit` / ruff / existing type checks stay as on the pinned base. Tests use recorded CAN, not live hardware.

## Suggested commit slices (later; not done now)

`tesla(ap1): …` one concern each. Do not start until this plan file exists (it does, as of Phase 0).
