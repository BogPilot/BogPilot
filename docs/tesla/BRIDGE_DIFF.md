# frog_ap1 bridge diff classification

Phase 2 classification only. No product code is ported from this diff.

Not a product, no warranty, driver remains responsible, comply with local law.

Target car for the notes below: Tesla Model S AP1 (`ap1_s`). This document does not add pre-AP or AP2 actuation.

## Diff range actually used

Computed in `/workspace/inventory/frog_ap1` (shallow clone, but the objects below were already present; no fetch).

| Role | SHA | Subject |
| --- | --- | --- |
| Bridge tip | `73a16cdd50033cb9f57a03fd619e2129558eef43` | `fix` |
| Tip parent (not the fork point) | `8532c208c72761350fc58c2b8053a53f1e690b45` | `fix` |
| First AP1 commit | `b6eea89` `remove unnecessary overhead for Tesla AP1 support` | parent is the fork point |
| Fork point / diff base | `c1c9eecdd7d79e3cbe13f9d0cfb9893638a98c75` | `Merge pull request #252 from chrispypatt/frogpilot-pr` |
| May 7 2025 FrogPilot snapshot | `f9bebfc4dbab64b1d31d72a752ed1ce88cbfa51a` | parent of `c1c9eec`, not the AP1 base |

`git merge-base HEAD c1c9eecdd7d79e3cbe13f9d0cfb9893638a98c75` is `c1c9eec` itself. `git merge-base HEAD f9bebfc4dbab64b1d31d72a752ed1ce88cbfa51a` is `f9bebfc`. Diffing from `f9bebfc` is the wrong range: it also contains the Toyota SecOC merge that landed in `c1c9eec` before any AP1 commit (`safety_toyota.h`, `selfdrive/car/card.py`, and `selfdrive/car/toyota/{carcontroller,carstate,interface,toyotacan}.py`). Those six files are not AP1 and are not classified here.

Range used: `c1c9eecdd7d79e3cbe13f9d0cfb9893638a98c75..73a16cdd50033cb9f57a03fd619e2129558eef43`.

Commits in that range (16): `b6eea89`, `957af3e` (`FINGERPRINT=TESLA_AP1_MODELS`), `294176a` and `f62b633` (`no dashcam`), `846b93c`, `d7abd01`, `7566820`, `b210050`, `8b33323`, `9192508` (`update safety`), `d3aece9` (`no custom boot logo`), `dad04b2`, `d44edbf` (merge), `e61e02a`, `8532c20`, `73a16cd`.

### File stat

```
 launch_env.sh                              |   2 +
 panda/board/safety/safety_tesla.h          | 123 ++++++++---------------------
 selfdrive/car/tesla/carcontroller.py       |  34 +++-----
 selfdrive/car/tesla/carstate.py            |  63 ++++++---------
 selfdrive/car/tesla/interface.py           |  24 ++----
 selfdrive/car/tesla/teslacan.py            |  66 +++-------------
 selfdrive/car/tesla/values.py              |  17 +---
 selfdrive/frogpilot/frogpilot_functions.py |   9 ---
 8 files changed, 89 insertions(+), 249 deletions(-)
```

Angle-rate tables in `safety_tesla.h` (`TESLA_STEERING_LIMITS`) and `CarControllerParams.ANGLE_RATE_LIMIT_*` are not in the diff. This bridge did not drop those angle limits. It did remove the flag that blocks longitudinal TX, the hands-on PCM cancel path, and the dashcam-only gate.

## How this sits on today's BogPilot

Compared at BogPilot `32eb450370f2e91c371d72c4153bb9d2cb0a976b` (FrogPilot `1e23dec6352cef5a36a87be0af7d7a082b7c48a4` plus rebrand, docs, and the failing smoke test). Histories do not share commits with `frog_ap1`. Today's Tesla package still matches the pre-bridge safety shape, with one API rename: `_get_params` no longer takes `disable_openpilot_long`; it takes `frogpilot_toggles` and reads `frogpilot_toggles.disable_openpilot_long`. `selfdrive/frogpilot/frogpilot_functions.py` moved to `frogpilot/common/frogpilot_functions.py`.

Today still has, and this diff tries to delete:

- `ret.dashcamOnly = True` in `selfdrive/car/tesla/interface.py`
- panda flags `TESLA_FLAG_POWERTRAIN`, `TESLA_FLAG_LONGITUDINAL_CONTROL`, `TESLA_FLAG_RAVEN`, and long TX denied when the long flag is off (`safety_tesla.h` tx hook else branch)
- dual-panda long only when `0x2bf` is on `CANBUS.autopilot_powertrain`
- `BUTTONS` stalk parsing, `create_action_request` cancel, `create_longitudinal_commands` on chassis and powertrain, gated by `CP.openpilotLongitudinalControl`
- hands-on cancel only when `steer_warning == "EAC_ERROR_HANDS_ON"` and `hands_on_level >= 3`
- Raven EPAS / seatbelt branches
- jerk ±8 and `ACCEL_TO_SPEED_MULTIPLIER = 3`
- no `FINGERPRINT` or `SKIP_FW_QUERY` in `launch_env.sh`
- FrogPilot boot-logo copy, at the new path

None of the bridge's replacement behavior (dashcam off, flags 0, forced fingerprint, ungated DAS_control) is in today's tree.

## Per-file classification

Labels: still relevant, obsolete API, Tesla-only, accidental, REJECT safety weakening. A delta can carry more than one label. Nothing here is ported.

### `launch_env.sh`

| Delta | Class | Today |
| --- | --- | --- |
| `export FINGERPRINT="TESLA_AP1_MODELS"` | REJECT safety weakening (forced fingerprint) | Absent. Fingerprint is not pinned to AP1 Model S. |
| `export SKIP_FW_QUERY="1"` | REJECT safety weakening (skips FW identity so the forced fingerprint sticks) | Absent. `FW_QUERY_CONFIG` is still active. |

Not Tesla-only behavior we want. An AP1 Model S must still fingerprint. Forcing `TESLA_AP1_MODELS` would also hide a mismatch with AP2 or Raven.

### `selfdrive/car/tesla/interface.py`

| Delta | Class | Today |
| --- | --- | --- |
| `dashcamOnly = True` → `False`, and the comment that steer is not torque-blended | REJECT safety weakening | Still `True`, comment kept. |
| Drop the `0x2bf` / aux-panda test. Always `openpilotLongitudinalControl = not disable_openpilot_long` | REJECT safety weakening. Also Tesla-only in intent (AP1 long is not the AP2 powertrain harness) but the patch turns long on with no harness check | Still gated. Long stays off unless powertrain `0x2bf` is present. |
| Single `safetyConfigs` entry with flags `0` | REJECT safety weakening (zero safety flags). Drops `FLAG_TESLA_LONG_CONTROL`, `FLAG_TESLA_POWERTRAIN`, and `FLAG_TESLA_RAVEN` | Flags still set from candidate and harness. |
| `_get_params(..., disable_openpilot_long, experimental_long, docs)` | obsolete API | Today's signature ends in `frogpilot_toggles`. |

AP1 Model S note, not a port: current code enables openpilot long only for the powertrain interceptor (`0x2bf`). That is the AP2 dual-panda path. AP1 chassis long is a later layer, behind panda safety and tests, not by zeroing flags.

### `selfdrive/car/tesla/values.py`

| Delta | Class | Today |
| --- | --- | --- |
| Delete `BUTTONS` (blinker, accel/decel cruise, cancel, resume on `STW_ACTN_RQ`) | accidental relative to the AP1 stalk contract. Removes Tesla-only parsing the port still needs. Not a safety-flag edit, but it deletes the stalk events Phase 3 must keep | `BUTTONS` still present and parsed in `carstate.py`. |
| `JERK_LIMIT_*` ±8 → ±4.9 | Tesla-only tuning, stricter jerk, not a dropped angle limit | Still ±8. |
| Remove `ACCEL_TO_SPEED_MULTIPLIER = 3`. Add `ACCEL_MAX = 2.0`, `ACCEL_MIN = -3.48` | Tesla-only constants for the rewritten long command. `2.0` / `-3.48` match the existing panda `TESLA_LONG_LIMITS` comment (`max_accel` 2 m/s^2, TODO limit min to -3.48). They do not by themselves authorize actuation | Multiplier `3` remains. Panda limits unchanged. |
| `ANGLE_RATE_LIMIT_UP` / `DOWN` | unchanged, not a delta | Same tables as `TESLA_STEERING_LIMITS`. |

Do not apply the jerk/accel constants in this phase. They only feed the ungated long command classified below. Writing them now would also change jerk on the existing dual-panda long path, which is out of scope for AP1 Model S.

### `selfdrive/car/tesla/carstate.py`

| Delta | Class | Today |
| --- | --- | --- |
| Stop parsing `BUTTONS` into `buttonEvents`. Drop `msg_stw_actn_req` | accidental (same stalk deletion as `values.py`) | Still parsed. `msg_stw_actn_req` still copied for cancel. |
| Always read `EPAS_sysStatus` on the chassis parser. Delete the Raven `EPAS3P_sysStatus` branch | Tesla-only AP1 simplification that deletes Raven. Not safe to apply on the shared interface | Raven branch still selected by `CAR.TESLA_MODELS_RAVEN`. AP1 already uses `EPAS_sysStatus`. |
| `steerFaultPermanent`: `EPAS_eacStatus == "EAC_FAULT"` replaced by a lookup of `EPAS_eacErrorCode` (and the temporary-fault lookup uses the same error-code map twice) | accidental (status vs error-code mixup) | Still uses `EPAS_eacStatus` for permanent and `steer_warning not in (IDLE, HANDS_ON)` for temporary. |
| `steerFaultTemporary` becomes `eac_status == "EAC_INHIBITED"` only | Tesla-only, and it drops every other non-idle warning | Broader temporary-fault test remains. |
| New `steering_disengage` (hands-on ≥ 3, or inhibited plus `EAC_ERROR_HIGH_ANGLE_RATE_SAFETY`) | Tesla-only, unused by the paired controller. The high-angle clause is stricter on paper but is wired to the mixed-up status variable | Field does not exist. Hands-on cancel stays in `carcontroller.py`. |
| Seatbelt always `SDM1`, drop Raven `DriverSeat` | Tesla-only AP1 path, deletes Raven | Both branches remain. AP1 already uses `SDM1`. |
| Cruise enabled/available, gear, doors, blinkers, stock AEB, `standstill = False` | unchanged | Same. |
| `acc_state` + `das_control_counters` replaced by a single `das_control` copy | Tesla-only, only supports the rejected long rewrite | Counter deque remains. |

CAN parser functions are not in this diff. The bridge did not retarget the parser when it deleted the Raven update branch.

### `selfdrive/car/tesla/carcontroller.py`

| Delta | Class | Today |
| --- | --- | --- |
| `lkas_enabled = CC.latActive and hands_on_level < 3`. Drops `steer_warning == "EAC_ERROR_HANDS_ON"` | REJECT safety weakening (driver-override disengage). A hands-on error below level 3 no longer drops lateral | Both conditions still required. |
| Remove PCM cancel spam via `create_action_request` on chassis and autopilot chassis when hands-on or `CC.cruiseControl.cancel` | REJECT safety weakening (removes the stock-stalk cancel path). Camera driver monitoring is not in this file and was not an excuse to remove this path | Cancel still spams counters 0..15 on both buses. |
| Every 4 frames, always call `create_longitudinal_command` (no `CP.openpilotLongitudinalControl` gate). `state = 13` on cancel, else `DAS_accState`. Accel clipped to the new min/max | REJECT safety weakening (long TX with long control off). State `13` is an unlabeled magic value, not a documented cancel | Long sends only inside `openpilotLongitudinalControl`, using stock counters, min/max accel split, and `ACCEL_TO_SPEED_MULTIPLIER`. |
| `import numpy` for `np.clip` | accidental | Not imported. |

Angle command still goes through `apply_std_steer_angle_limits` in both trees. That part was not weakened here. HUD is still `# TODO: HUD control` on both sides.

### `selfdrive/car/tesla/teslacan.py`

| Delta | Class | Today |
| --- | --- | --- |
| Delete `create_action_request` (copy `STW_ACTN_RQ`, force `SpdCtrlLvr_Stat = 1`, recompute CRC) | REJECT safety weakening (no stalk cancel frame) | Function still present. |
| Replace `create_longitudinal_commands` (chassis `0x2b9` and powertrain `0x2bf`, speed setpoint, separate min/max accel, stock counter) with chassis-only `create_longitudinal_command` | Tesla-only for a future AP1 chassis long path, but this patch is the actuator for the ungated controller. Do not port it as written. Powertrain delete is also the wrong edit for AP2 and is out of scope | Both buses still packed. |
| When `active`, `DAS_setSpeed = 0` if `accel < 0` else `145` (kph) | REJECT safety weakening (fail-open set speed). The bridge's own comment says this jerks after a gas override | Set speed is `speed * MS_TO_KPH` from the caller. |
| `DAS_accelMin = accel`, `DAS_accelMax = max(accel, 0)`, counter `(frame // 4) % 8` | Tesla-only command shape, tied to the rejected sender | Stock counter and independent min/max remain. |
| Steering `create_steering_control` and checksum helper | unchanged | Same. |

### `panda/board/safety/safety_tesla.h`

| Delta | Class | Today |
| --- | --- | --- |
| Delete `TESLA_FLAG_POWERTRAIN`, `TESLA_FLAG_LONGITUDINAL_CONTROL`, `TESLA_FLAG_RAVEN`. `tesla_init` ignores `param` | REJECT safety weakening (zero safety flags) | All three flags still selected in `tesla_init`. |
| Delete `TESLA_PT_TX_MSGS`, `tesla_pt_rx_checks`, `tesla_raven_rx_checks` | REJECT as part of flag removal. Also drops AP2/Raven rx coverage. AP1 Model S already uses `tesla_rx_checks` when those flags are off | All three tables remain. |
| RX angle only from `0x370` bus 0. Speed only `0x118`, gas `0x108`, brake `0x20a`, cruise `0x368` | Tesla-only AP1 (non-Raven, non-powertrain) addresses. Today's code already uses these when the flags are clear. Deleting the other addresses is not an AP1 add | Flag-selected addresses remain. |
| Stock AEB bit read on `0x2b9` even when long is off | Tesla-only / slightly stricter AEB observe. Not worth porting alone; the old code only needed it while long intercept was armed | Still gated on `tesla_longitudinal`. |
| Delete powertrain relay check (`0x2bf` must not be received on bus 0). Keep `0x488` check only | REJECT safety weakening for the powertrain panda. Irrelevant to a single-panda AP1, and harmful if the shared safety mode is reused | Both checks remain. |
| `0x488` angle hook and `0x45` "cancel lever only" hook: condition no longer mentions `tesla_powertrain`, bodies kept | not a limit drop. Angle-rate check still calls `steer_angle_cmd_checks(..., TESLA_STEERING_LIMITS)`. Stalk TX still rejects non-cancel | Same checks, skipped only on the powertrain panda. |
| `0x2b9` TX: accel and AEB checks run with no `tesla_longitudinal` gate. The `else { violation = true; }` branch is gone | REJECT safety weakening. Flags `0` used to forbid DAS_control. The hook now allows it whenever raw accel is inside `TESLA_LONG_LIMITS` | Long flag off still sets `violation = true` for the DAS_control address. |
| Fwd hook always blocks stock `0x2b9` (unless `tesla_stock_aeb`) and `0x488` | REJECT safety weakening (always takes over chassis long, not only when long control was requested) | `0x2b9` blocked only if `tesla_longitudinal && !tesla_stock_aeb`. |
| `TESLA_LONG_LIMITS` and `TESLA_STEERING_LIMITS` numeric tables | unchanged | Same, including min accel TODO `-3.48`. |

### `selfdrive/frogpilot/frogpilot_functions.py`

| Delta | Class | Today |
| --- | --- | --- |
| Delete the boot-logo copy onto `/usr/comma/bg.jpg` | accidental. Not Tesla, not safety. Branding only | Still copied from `frogpilot/common/frogpilot_functions.py` (path moved; obsolete API if replayed at the old path). BogPilot rebrand does not depend on this deletion. |

## Ported: none

No delta was applied. Each REJECT was left out for the reason in the tables. Short list:

| Rejected change | Why it was not applied |
| --- | --- |
| `FINGERPRINT=TESLA_AP1_MODELS` and `SKIP_FW_QUERY=1` | Forces AP1 Model S and skips FW identity. Ambiguous or wrong cars would look like AP1. |
| `dashcamOnly = False` | Turns a dashcam interface into an actuation interface with no new panda tests. |
| `safetyConfigs` flags `0` and `tesla_init` ignoring `param` | Removes long, powertrain, and Raven safety selection. |
| Long TX and fwd intercept of `0x2b9` without `TESLA_FLAG_LONGITUDINAL_CONTROL` | Today's panda treats DAS_control as forbidden unless that flag is set. The bridge allows it. |
| Ungated `create_longitudinal_command` every 4 frames, including `DAS_setSpeed` 0 or 145 | Sends longitudinal commands when long control is off, and uses a fail-open set speed. |
| Delete `create_action_request` / PCM cancel on hands-on and cruise cancel | Removes the disengage path that does not depend on an unlabeled DAS state `13`. |
| Lateral stays on unless `hands_on_level >= 3`, ignoring `EAC_ERROR_HANDS_ON` | Drops a hands-on error disengage that today's controller still requires. |
| Delete `BUTTONS` | Not a safety-flag zero, but it removes stalk events AP1 Model S still needs. Left in place. Called out here so it is not smuggled in as "Tesla-only cleanup". |

Also not ported, even where the label is Tesla-only rather than REJECT: Raven branch deletions, the eac status/error mixup, unused `steering_disengage`, jerk ±4.9, and `ACCEL_MIN`/`ACCEL_MAX`. They either break a car this phase is not changing or only make sense after a safety-reviewed AP1 long path exists.

Checked and not present in this diff, so not "fixed" by refusing them: edits that lower `TESLA_STEERING_LIMITS` or `ANGLE_RATE_LIMIT_*`. Those tables are identical on both sides of `c1c9eec..73a16cd`.

## Phase 3 layer 1 (AP1 Model S only)

Do this later, not in this commit:

- Add platform id `ap1_s` with no actuation. No steer request, no DAS_control, no stalk-cancel writer, no `dashcamOnly = False`.
- Do not remove `CAR.TESLA_AP1_MODELS` or its current dashcam behavior until fingerprint tests exist. `TESLA_AP1_MODELS` stays a dashcam platform. `ap1_s` is an additional id, not a rename that silently engages.
- Do not force `FINGERPRINT`. Do not set panda flags to 0. Do not enter pre-AP regen or AP2 powertrain long in that layer.
- Keep `BUTTONS` and the hands-on plus PCM-cancel path. Stalk follow mapping is a later layer (`stalk_follow.py`), not a deletion of `STW_ACTN_RQ` parsing.
- Refuse longitudinal while the fingerprint is ambiguous. `ap1_s` must not be selected by an AP2 or Raven fixture.

## Disclaimer

Not a product, no warranty, driver remains responsible, comply with local law.
