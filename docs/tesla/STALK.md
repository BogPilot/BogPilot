# AP1 stalk map

BogPilot is not a product. No warranty. The driver remains responsible. Comply with local law. This is research code; it has been driven on one AP1 Model S, which is not a safety validation.

The AP1 cruise stalk reports two signals on `STW_ACTN_RQ` (0x45): `SpdCtrlLvr_Stat` (tip up / down, pull, push forward) and `DTR_Dist_Rq` (twist for follow distance). This page covers both, as of tag `ap1-driving-milestone-4` (which includes the tip-engage hold, 1 mph tip floor, and SLC posted-limit guard).

## Cruise stalk (`SpdCtrlLvr_Stat`)

### Raw values

From `opendbc/tesla_can.dbc` (`VAL_ 69 SpdCtrlLvr_Stat`) and `BUTTONS` in `selfdrive/car/tesla/values.py`:

| Raw | DBC name | Stalk position | openpilot `ButtonType` |
| --- | --- | --- | --- |
| 0 | IDLE | at rest | none |
| 16 | UP_1ST | tip up, first position | `accelCruise` |
| 4 | UP_2ND | full tip up (second position) | `accelCruise` |
| 32 | DN_1ST | tip down, first position | `decelCruise` |
| 8 | DN_2ND | full tip down (second position) | `decelCruise` |
| 2 | RWD | pull toward the driver | `resumeCruise` |
| 1 | FWD | push away (cancel) | `cancel` |

`ButtonType` collapses first and second position into one button, so the tip code also reads the raw value: `carstate.py` stores `spd_ctrl_lvr`, `frogpilot_card.py` publishes it as `frogpilotCarState.spdCtrlLvr`, and `frogpilot_vcruise.py` treats 4 / 8 as a full tip (`tip_full`).

### Who does what

- The stock cruise (DI) still engages and cancels from the stalk. openpilot follows `carState.cruiseState.enabled` (pcmCruise); it does not send engage, tip, or pull stalk frames. The panda only lets openpilot send 0x45 with the cancel value (`safety_tesla.h`).
- The engage modes, tips, and pull below change openpilot's **software** set speed in `frogpilot/controls/lib/frogpilot_vcruise.py` (policy in `Ap1RaiseHoldoff`, `selfdrive/car/tesla/slc_raise.py`). They run only on AP1 with FrogPilot **Speed Limit Controller** turned on. With SLC off, openpilot's set speed is the car's own `DI_cruiseSet`, as on the stock stalk.
- Stalk levels are read continuously (`carstate.button_states` → `frogpilotCarState.accelPressed` / `decelPressed` / `resumePressed`), not from the ~10 ms `buttonEvents` edges, so short tips and pulls are not missed by the 20 Hz planner.
- The posted limit comes from FrogPilot SLC. On AP1 its dashboard source is the stock Mobileye `DAS_fusedSpeedLimit`, then the car's `UI_mapSpeedLimit` / `UI_mppSpeedLimit` (`selfdrive/car/tesla/speed_limit.py`). "Offset" is the FrogPilot SLC offset for that limit's band.
- Instrument cluster set speed: while engaged, `DAS_accSpeedLimit` on 0x389 carries openpilot's set speed (`hudControl.setSpeed`, DBC factor 0.4). controlsd raises that value to the planner set (`frogpilotPlan.vCruise`) whenever the planner set is higher than the DI-seeded set (`cluster_display_kph`). Under the openpilot overlay `DI_cruiseSet` sits near half the current speed, so in practice the cluster set speed follows the planner set: it drops to the curve speed control (CSC) target in a corner and returns to the SLC or tipped set after the curve, and it follows SLC limit changes and tips (a tipped or sticky set holds through limit changes, see below). Known cosmetic quirk: CSC starts its target at the current speed when it switches on, so the cluster set speed can briefly show the current speed even when the curve needs no slowdown (route 32 at 22:08:00: 40 for about 0.5 s with a 51 set). 0x389 goes out at about 2 Hz, so CSC dips shorter than about 0.5 s may not show. `cruise_set_mph` drops the `V_CRUISE_UNSET` value so a stopped car does not show ~90 mph. Requires `EnableICIntegration` (default on for AP1).

### Gesture map

| Gesture | Raw | When disengaged | When engaged |
| --- | --- | --- | --- |
| Tip up or down (first position or full tip) | 16, 32, 4, 8 | Stock cruise engages. openpilot latches the **current speed** (sticky) and holds it through posted-limit changes in both directions until a pull or a disengage. It does not jump to the posted limit. | First position: set ± 1 mph. Full tip: next multiple of 5 up / next-lower multiple of 5 down. |
| Short pull | 2 | Stock cruise engages. openpilot sets **posted limit + offset** and tracks the limit. | Clears any tip or sticky latch and returns to **posted limit + offset** (tracking). |
| Pull held ~1.23 s | 2 | Same as a short pull. The engage pull never toggles Experimental Mode. | One Experimental Mode toggle (see below). The pull itself also returns to posted limit + offset. |
| Short push forward | 1 | Nothing from openpilot. | Stock cancel. openpilot disengages; tip and sticky latch are cleared. |
| Push forward held ~1.23 s | 1 | One Experimental Mode toggle (see below). | Stock cancel. Does not toggle, even after it disengages. |

### Engage classification

On the rising edge of `cruiseState.enabled`, `Ap1RaiseHoldoff` looks at which stalk input was pressed most recently within the last 0.8 s (`RECENT_S`; Tesla clears the stalk 80–240 ms before cruise reports enabled):

- UP or DN → **sticky**: the set speed is the current speed at engage (`latched_vego_ms`). Like a tipped set, it holds through posted-limit changes in both directions until a pull or a disengage: a higher limit does not raise it (a tip-engage at 15 in a school zone does not pick up a 25 or 35 sign) and a lower limit does not lower it. Only curve speed control can slow below it. An engaged tip ends the sticky latch and starts a tip from the latched speed. A tip-engage below 15 mph (for example about 9 mph) holds the same way.
- RWD (pull) → **SLC tracking**: the set speed is posted limit + offset, lifted after FrogPilot's `min()` merge so a low `DI_cruiseSet` cannot undo it, and it follows the limit up and down.
- No stalk input in the window → same as a pull.

### Engaged tips

- The tip base is openpilot's current software set: the sticky latch if active, else the current tip, else the SLC set (`set_hint` floors with `slc_desired`, never the stale `DI_cruiseSet`, which reads about half the current speed under openpilot's overlay).
- First position (16 / 32) on the press edge: base ± 1 mph (`TIP_STEP_MS`).
- Full tip (4 / 8), straight to the second position or reached during the same press: the next multiple of 5 above the base (`next_5_ms`: 50 → 55, 51 → 55) or the next-lower multiple of 5 (`prev_5_ms`: 50 → 45, 51 → 50). Example from route `0000002a`: at a set of 50, a full tip down gives 45, and again gives 40.
- Holding the full tip does one 5 mph step. There is no repeating scroll; release and tip again for the next step.
- Fallback: holding the first position for 0.45 s (`TIP_HOLD_S`) without reaching the second position also upgrades that press to the next-5 step once.
- Range: capped at `V_CRUISE_MAX` (145 km/h) and floored at 1 mph (`TIP_MIN_MS`). Tips can go below 15 mph (a full tip down from 15 gives 10, then ±1 to 9, 8, … 1); a further tip down stays at 1. The floor keeps a tip from reaching 0, which the code treats as "no tip" and which used to drop back to SLC tracking at posted limit + offset.
- **A tipped set is authority until a pull or a disengage.** Speed limit zone changes do not clear it in either direction: a higher posted limit does not raise it (a 15 mph school-zone tip does not pick up a 25 or 35 sign) and a lower posted limit does not lower it. Curve speed control can still slow below it. The tip is also written to SLC's `overridden_speed`, so FrogPilot's gas-pedal override returns to the tip.
- Tips change the software set only. openpilot sends no cancel or stalk frame for a tip.

### Posted-limit guard (SLC only)

`Ap1SlcLimitGuard` (`slc_raise.py`, applied in `frogpilot_vcruise.py` on AP1 with SLC on) filters the posted limit that SLC tracking and pull engage use, and the limit openpilot publishes as `frogpilotPlan.slcSpeedLimit`. It does not touch tips, tip-engage, or the tip floor.

- A posted limit under 15 mph is ignored, even if it persists: openpilot keeps the previous valid limit (or no limit if there was none). 15 mph itself is valid (school zones). Route `0000002a` had a ~1.4 s Mobileye 5 mph reading in a 45 zone that dropped the SLC-tracking set from 51 to about 6 mph; with the guard it stays at 51.
- A sudden drop, more than 15 mph or to below half the current limit (45 → 25, 65 → 45, 40 → 15), must persist for 2 s before SLC tracking follows it. Normal one-sign drops of 15 mph or less (45 → 30, 35 → 25, 30 → 15) apply immediately, as before.
- Rises apply immediately. "No limit" (0) passes through unchanged.
- The car's own cluster speed-limit sign comes from the car and is not changed.

### Pull while engaged

A pull (any length) clears the tip and the sticky latch. openpilot goes back to SLC tracking: posted limit + offset, following lower limits as well as higher ones (route `0000002a`: a 45 → 30 Mobileye drop now follows without cancel and re-engage). The stock DI treats a pull as resume and may change its own set; openpilot does not follow `DI_cruiseSet` here.

### Experimental Mode holds

Both are timing helpers driven from `carstate.py` at 100 Hz (`DT_CTRL`) and act through `frogpilot_card.py` with FrogPilot's existing `handle_experimental_mode`: with Conditional Experimental Mode on, that flips the CEM override (the same as the distance-button toggle); otherwise it toggles `ExperimentalMode`. Device-side only: no new CAN frame, no panda change, no new Params key. Each fires once per hold; returning the stalk to IDLE re-arms it.

- **Engaged pull hold** (`stalk_pull_hold.py`, `PULL_HOLD_S = 1.23`, `PULL_HOLD_ENABLED = True`): RWD held continuously for 1.23 s, armed only if cruise was already enabled when the pull started. The engage pull itself never toggles. Known tradeoff: the stock DI treats a held pull as resume, and about 0.7 s into the hold the car's own set speed may jump to its remembered set (drive 18: 15 → 31 mph).
- **Disengaged forward hold** (`stalk_fwd_hold.py`, `FWD_HOLD_S = 1.23`, `FWD_HOLD_ENABLED = True`): FWD held continuously for 1.23 s, armed only if cruise was not enabled when the push started. A cancel push that starts engaged never toggles, even after it disengages, and engaging during the hold aborts it. Short forward pushes keep their normal cancel meaning.

## Follow distance (`DTR_Dist_Rq`)

### Scale

`DTR_Dist_Rq` on `STW_ACTN_RQ` is the traffic-aware cruise stalk distance request. `opendbc/tesla_can.dbc`, `opendbc/tesla_powertrain.dbc`, and Tinkla `selfdrive/car/tesla/carstate.py` (the `cruise_distance` comment: pos1=0 through pos7=200, SNA=255) all use the same seven values. No third scale. Nothing was added to `docs/tesla/DIVERGENCES.md`.

| Raw | Detent | Profile | Follow seconds |
| --- | --- | --- | --- |
| 0 | ACC_DIST_1 | traffic | 1.00 |
| 33 | ACC_DIST_2 | closer_than_aggressive | 1.125 |
| 66 | ACC_DIST_3 | aggressive | 1.25 |
| 100 | ACC_DIST_4 | between_aggressive_and_standard | 1.35 |
| 133 | ACC_DIST_5 | standard | 1.45 |
| 166 | ACC_DIST_6 | between_standard_and_relaxed | 1.60 |
| 200 | ACC_DIST_7 | relaxed | 1.75 |

ACC_DIST_1 is the closest gap. ACC_DIST_7 is the farthest. 255 is SNA and is not a detent.

Named follow times are stock FrogPilot defaults, not live params and not a new param:

- Traffic uses `TRAFFIC_FOLLOW` at `v_ego >= CRUISING_SPEED` (5 m/s), which is 1.00 seconds. The same curve is 0.50 seconds at 0 m/s. This module does not see speed, so it does not apply that interpolation.
- Aggressive, standard, and relaxed are `get_T_FOLLOW` with `custom_personalities` false: 1.25, 1.45, and 1.75 seconds. Those match the `AggressiveFollow`, `StandardFollow`, and `RelaxedFollow` defaults.

The three intermediate rows are the midpoint of the neighboring named times. Each midpoint is equidistant from those neighbors, so picking the nearest cereal personality would be an arbitrary collapse. `long_mpc` and `frogpilot_following` are not modified.

### Hysteresis

Implemented in `selfdrive/car/tesla/stalk_follow.py` (`map_stalk_follow`). Exact raw values in the table commit that row.

SNA (255) and `None` hold the previous decision, including its validity. The first sample, with no ready history, is not ready and is not a guessed profile.

A non-exact value moves at most one detent. It has to fall strictly inside the previous detent's neighbor span (between the adjacent table raws, excluding those endpoints), and it has to be at least halfway to that neighbor. The halfway point belongs to the farther detent: `raw >= midpoint` steps away from ACC_DIST_1, and `raw < midpoint` steps toward it. Repeating the same raw does not step again.

Halfway points: 16.5, 49.5, 83, 116.5, 149.5, 183. On an upward integer sweep the steps are 17, 50, 83, 117, 150, 183. On a downward sweep they are 182, 149, 116, 82, 49, 16.

Any other raw holds the last profile and is invalid. That includes 1 (inside the closest span but not halfway, and outside every farther detent's neighbor span) and 999 (outside 0..200). The first unknown sample is not ready.

### Wiring

`selfdrive/car/tesla/carstate.py` passes `DTR_Dist_Rq` through `map_stalk_follow` and publishes the follow seconds on the existing `carState.cruiseState.speedOffset` (0 when not ready). `FrogPilotFollowing` (`frogpilot/controls/lib/frogpilot_following.py`) replaces the personality follow time with that value via `apply_stalk_t_follow`, only for AP1 while controls are enabled. `frogpilot_card.py` (`_apply_ap1_stalk`, `ap1_stalk_commands`) turns detent 1 into traffic mode and writes the existing `LongitudinalPersonality` param for detents 3, 5, and 7 (`aggressive`, `standard`, `relaxed`) so the on-screen personality icon changes. Detents 2, 4, and 6 are follow times only and keep the last named personality. Cereal is not extended and there is no new persisted param. `long_mpc` and `get_T_FOLLOW` are not modified.
