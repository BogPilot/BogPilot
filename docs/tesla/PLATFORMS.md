# Tesla early platforms

> **Current state (tag `ap1-driving-milestone-4`).** The first paragraph below is current: AP1 is not `dashcamOnly`, uses `TESLA_FLAG_AP1 | TESLA_FLAG_LONGITUDINAL_CONTROL`, and gets lateral (`0x488`) and chassis longitudinal (`0x2b9`) control. `interface.py` now sets `ret.dashcamOnly = dashcam_only_for_candidate(candidate)` (false only for AP1). The sections from "This commit" on are per-commit history from the Phase 3 layers. Their "not commanded", "not allowed", and "`dashcamOnly` stays true" lines describe those commits, not today's AP1 behavior. `platform.long_control_allowed` still returns false for every platform; it is not the gate actually used (that is `longitudinal_command_allowed` in `actuator_plan.py`).

BogPilot is not a product. No warranty. The driver remains responsible. Comply with local law. This commit is not validated on a bench or in a car.

`CAR.TESLA_AP1_MODELS` keeps `dashcamOnly` false. Chassis longitudinal on `0x2b9` is allowed for that candidate only, without requiring `0x2bf` and without `TESLA_FLAG_POWERTRAIN`. With no powertrain bus the safety flags are `TESLA_FLAG_AP1 | TESLA_FLAG_LONGITUDINAL_CONTROL` (`8|2` = 10). `tesla_tx_hook` uses `0x2b9` when the powertrain flag is unset. `interface.py` sets `openpilotLongitudinalControl` to `not frogpilot_toggles.disable_openpilot_long` for AP1, the same toggle as the `0x2bf` branch; `flags_for_candidate` does not see the toggle, so the panda flags stay 10 even if that toggle later turns the param off. `DAS_control` is still planned only when that param, `CC.enabled`, and `CC.longActive` are all true. Disengaged or long-inactive plans no `DAS_control`. AP1 plans `0x2b9` and does not plan `0x2bf`. There is no fixed set-speed of 0 or 145 and no friction-brake command. AP2 and Raven stay `dashcamOnly` true. `TESLA_STEERING_LIMITS` is unchanged and `TESLA_FLAG_AP1` stays 8. This is not a driving validation.

## This commit

Phase 3 layer 1, on the comma 3X / Tesla Model S AP1 target. Recognize a platform. Do not command lateral or longitudinal control.

`TeslaPlatform` in `selfdrive/car/tesla/values.py` has `preap`, `ap1_s`, `ap1_x`, and `ap2`. `classify_tesla_platform` in `selfdrive/car/tesla/platform.py` maps a resolved identity to one of those, or to `None`. `long_control_allowed` is false for `None` and for every platform, including `ap1_s`.

`None` means longitudinal is not allowed. Do not engage long. This commit does not change `controlsd`, panda safety, or `interface.py`. `dashcamOnly` stays as it was. Longitudinal stays refused until a later layer adds a panda safety mode for the platform.

## Control matrix

Nothing below is commanded by this commit.

| Platform | Production recognition | Lateral | Longitudinal |
| --- | --- | --- | --- |
| `preap` | unmatched (`None`) | not commanded | not allowed |
| `ap1_s` | `CAR.TESLA_AP1_MODELS`, or chassis bus 0 with `0x45`, `0x2b9`, and `0x488` | not commanded | not allowed |
| `ap1_x` | unmatched (`None`) | not commanded | not allowed |
| `ap2` | `CAR.TESLA_AP2_MODELS`, or `None` if AP1 is also present | not commanded | not allowed; full control not enabled |
| Raven (`CAR.TESLA_MODELS_RAVEN`) | not an early platform | unchanged by this commit | unchanged by this commit |

## How `ap1_s` is recognized

`CAR.TESLA_AP1_MODELS` is the AP1 Model S identity already in this tree. `selfdrive/car/tesla/fingerprints.py` `FW_VERSIONS` lists `TESLA_AP2_MODELS` and `TESLA_MODELS_RAVEN` only. There is no AP1 Model S firmware row. The classifier still maps that platform member, and the name `TESLA_AP1_MODELS`, to `ap1_s`. It does not look at `CarSpecs` or `dbc_dict`. Those are shared with `TESLA_AP2_MODELS` in `values.py`, so matching them would also match AP2.

AP1 is also recognized from the chassis bus (bus 0) when `0x45` (`STW_ACTN_RQ`), `0x2b9` (`DAS_control`), and `0x488` (`DAS_steeringControl`) are present. Those names are in `opendbc/tesla_can.dbc`. `0x2bf` is powertrain `DAS_control` in the upstream Model 3 / second-panda check and is not required. Extra addresses do not change the result. A set that is only `0x2bf` does not match. pre-AP and AP2 are not fingerprinted by this rule and stay deferred. `dashcamOnly` is still true. This does not command the car.

BogPilot is not a product. No warranty. The driver remains responsible. Comply with local law. Not validated on a bench or in a car.

## `ap2` and Raven

`CAR.TESLA_AP2_MODELS` maps to `ap2` because it is a different platform member from `CAR.TESLA_AP1_MODELS`. Presented together, the two are ambiguous and the classifier returns `None`.

`CAR.TESLA_MODELS_RAVEN` does not map to `ap2`. Raven is not reclassified. It stays the existing platform and is not part of this early contract. A Raven identity next to a single AP1 or AP2 identity does not cast a second early vote.

Mapping `ap2` does not enable control.

## `preap` and `ap1_x`

Both members exist on the enum so the contract can name them. No production fingerprint, firmware entry, or CAN address set selects them. The classifier returns `None` for every production identity. It returns `preap` or `ap1_x` only when a test passes an `EarlyPlatformFixture` for that member. That fixture is not a bus fingerprint, and it cannot be built for `ap1_s` or `ap2`.

pre-AP and AP1 Model X are not enabled. AP2 full control is not enabled.

## Buses already named in this tree

These are the index comments already in `selfdrive/car/tesla/values.py` `CANBUS`. They are not install steps. This commit does not add an OBD-C, mirror-housing, or dual-harness procedure.

- Lateral harness: `chassis` 0, `radar` 1, `autopilot_chassis` 2
- Longitudinal harness: `powertrain` 4, `private` 5, `autopilot_powertrain` 6

Harness type for the car is not decided here.

## Engagement

Comma 3X. Tesla Model S AP1 (`ap1_s`). This commit does not enable lateral or longitudinal control. BogPilot is not a product. No warranty. The driver remains responsible. Comply with local law. Not validated on a bench or in a car.

`selfdrive/car/tesla/interface.py` still assigns dashcam mode before the safety config. That assignment was not changed:

```
ret.dashcamOnly = True
```

`frog_ap1` set this false. This tree does not.

The safety model is still `car.CarParams.SafetyModel.tesla`. The flag names in that file are unchanged:

- `Panda.FLAG_TESLA_RAVEN` is applied only when `candidate == CAR.TESLA_MODELS_RAVEN`. AP1 Model S does not take it.
- `Panda.FLAG_TESLA_LONG_CONTROL` is applied only when `0x2bf` is present on `CANBUS.autopilot_powertrain`.
- `Panda.FLAG_TESLA_POWERTRAIN` is applied only on the second panda config in that same branch.

The openpilot-long param in that branch is `not frogpilot_toggles.disable_openpilot_long`. The long flag above is set whenever `0x2bf` is present, even if that toggle disables the param.

AP1 Model S with no `0x2bf` on that bus gets one safety config and a flags value of `0`. That is not the bridge change that deleted the longitudinal gate. `panda/board/safety/safety_tesla.h` was not edited. `tesla_tx_hook` still refuses `DAS_control` when `TESLA_FLAG_LONGITUDINAL_CONTROL` is unset (`tesla_longitudinal` is false, and the else branch sets `violation`). Lateral TX still goes through `steer_angle_cmd_checks` and `TESLA_STEERING_LIMITS`. When the long flag is on, accel still goes through `longitudinal_accel_checks` and `TESLA_LONG_LIMITS`.

`CarController.update` was not executed in this environment. `opendbc/can/packer_pyx.so` is AArch64, and importing the controller also needs `parser_pyx` and `msgq`. The disengaged decision is `build_actuator_plan` in `selfdrive/car/tesla/actuator_plan.py`, which `update` calls, and `TeslaCAN.create_steering_control` packs it.

On an even frame with lateral inactive, the steering message is still `DAS_steeringControl` (`0x488` in `TESLA_TX_MSGS`) with `DAS_steeringControlType` 0. Type 0 is NONE. `tesla_tx_hook` does not treat 0 or 3 as steer control enabled. The angle field echoes the measured angle. It is not a request to move EPAS to the actuator angle. Odd frames send no steering message.

`build_actuator_plan` plans `DAS_control` only when `CP.openpilotLongitudinalControl`, `CC.enabled`, and `CC.longActive` are all true. `CarController.update` passes those two `CarControl` fields. If the openpilot-long param is false, or `CC.enabled` is false, or `CC.longActive` is false, the plan includes no `DAS_control` frame (`0x2b9` on chassis, `0x2bf` on powertrain): no `DAS_setSpeed` and no accel request. Stock DAS counters are left in place. AP1 with the param false stays the single-panda path and still sends no `DAS_control`. When the gate is open, the command contents are unchanged: `target_speed = max(v_ego + accel * ACCEL_TO_SPEED_MULTIPLIER, 0)`, `DAS_accelMin` / `DAS_accelMax` split from that accel, and the stock `acc_state`. There is no fixed set-speed of 145 or 0. That frame is a longitudinal command, not a cancel, and it is not planned while long control is inactive. This does not turn the param on. Interface still sets the param true only in the `0x2bf` branch above. `dashcamOnly` remains true. This is not a driving validation.

BogPilot is not a product. No warranty. The driver remains responsible. Comply with local law.

There is no pre-AP command builder. `long_control_allowed` is false for `preap`. The messages this controller can pack are `DAS_steeringControl`, `STW_ACTN_RQ`, and `DAS_control`. No iBooster apply and no friction-brake command. `BrakeMessage` (`0x20a` chassis, `0x1f8` powertrain) is an RX check in `safety_tesla.h`, not a TX message.

## Stored Tesla Early toggles

Not applied to `CarParams`. `selfdrive/car/tesla/interface.py` is unchanged, including `ret.dashcamOnly = True`. `panda/` is unchanged. `controlsd` does not read these params.

| Key | Stored values | Default | Applied |
| --- | --- | --- | --- |
| `TeslaLongControl` | `off`, `lateral_only`, `full` | `off` | no |
| `TeslaStalkFollow` | bool | on (`1`), early-Tesla preference only | no |
| `EnableICIntegration` | file `/data/params_bogpilot/EnableICIntegration` (not a Params key; July `params_pyx.so`) | AP1 default on when absent; non-AP1 off | no (gates cluster TX only) |

There is no `TeslaPlatform` param. `classify_tesla_platform` is the only platform source. The "Detected platform" row in `TESLA_EARLY_TOGGLES` is a label, not a fingerprint override. The on-device vehicle panel does not render this metadata tuple.

`tesla_long_control` and `tesla_stalk_follow` in `selfdrive/car/tesla/toggles.py` only parse a stored string. Unknown long-control text, including `0`, `1`, and `2`, is `off`.

BogPilot is not a product. No warranty. The driver remains responsible. Comply with local law.

## Parked capture

A parked route from 2026-10-03, recorded on a comma 3X running `frog_ap1` at `73a16cdd`, is summarized as `ap1-parked-2026-10-03` in `selfdrive/car/tesla/tests/fixtures/ap1_parked_addrs.py`. The log is not in this repo. VIN, GPS, and dongle id are not stored.

One panda. Buses 0, 1, and 2 were active. `0x2b9` (`DAS_control`) was present on buses 0, 1, and 2. `0x2bf` was absent (count 0), so this capture is not the dual-panda powertrain fingerprint. All seven `DTR_Dist_Rq` raw values were seen: 0, 33, 66, 100, 133, 166, 200. Controls never went active. `vEgo` stayed near 0. This is not an engagement test.

The route was recorded on `frog_ap1` with that build's `dashcamOnly` false and fingerprint `TESLA_AP1_MODELS` (`fixed`). This BogPilot tree was not validated on the route. `dashcamOnly` here stays true.

A later drive on the same `frog_ap1` build (`73a16cdd`) is summarized as `ap1-drive-engaged` in `selfdrive/car/tesla/tests/fixtures/ap1_drive_addrs.py`. The log is not in this repo. No payloads and no VIN are stored. The device RTC year is untrusted. On that recording only, the fingerprint was `TESLA_AP1_MODELS` (`fixed`), `dashcamOnly` was false, and the safety param was 0. One panda. `0x2bf` was absent. While `latActive` and `longActive`, sendcan on bus 0 contained `0x2b9` and `0x488`. Engaged about 88.5 s. Max `vEgo` about 14.2 m/s. The seven `DTR_Dist_Rq` raw values were already known from the parked capture. This does not validate BogPilot engagement. `dashcamOnly` here stays true.

BogPilot is not a product. No warranty. The driver remains responsible. Comply with local law.
