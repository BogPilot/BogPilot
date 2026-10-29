# BogPilot Tesla docs

Status: **AP1 Model S driving, tag `ap1-driving-milestone-4`** (2026-10-07) on `bogpilot-tesla`. openpilot steers (angle control on `0x488`) and controls longitudinal (`0x2b9`) on AP1 through one harness and one panda. Other Tesla platforms stay `dashcamOnly`. Research code, not a product or a safety certification. Install: `installer.comma.ai/BogPilot/bogpilot-tesla`.

This file started as the Phase 1 note (base tree, docs, identity, no actuation). Phase 1 is long done; [`PHASE1_STATUS.md`](PHASE1_STATUS.md) is kept as history.

## AP1 longitudinal smoothing (milestone 4)

AP1 only, in `DAS_control` 0x2b9 and the planner (details in [`DIVERGENCES.md`](DIVERGENCES.md)). Panda safety, `ACCEL_MIN` / `ACCEL_MAX`, `STOP_DISTANCE` and the set-speed logic are unchanged.

- **Brake onset ramp** (`long_smooth.py`, `Ap1AccelSmoother`): regen ramps in at `AP1_BRAKE_ONSET_JERK` = 2.0 m/s³, blending to 8 m/s³ as the request goes from -1.0 to -2.0 m/s². At or below -2.0, FCW, the stopping state, below 1 m/s, inactive and the gas-neutral frame pass through on the same step.
- **Drive release 2.0** (`long_smooth.py`): a falling request while drive torque is still applied is released at `AP1_DRIVE_RELEASE_JERK` = 2.0 m/s³ with a 0.2 s soft start and a taper onto the request (+1.3 → 0 in about 0.8 s, was 0.25 s at 5 m/s³). A lift into braking blends back to 5 m/s³ by -0.5 m/s².
- **Throttle cap** (`throttle_gate.py`, `longitudinal_planner.py`): the model's `allowThrottle` flag becomes a cut fraction with a fast attack (0.15 s) and slow release (1.5 s) that blends the MPC max accel toward the coast limit. Braking limits, FCW and the lead cost are unchanged.
- **Standstill hold floor** (`long_smooth.py`): at standstill without FCW a request below `AP1_STANDSTILL_HOLD_ACCEL` = -1.0 m/s² is held at -1.0 (relaxed at 1 m/s³), so the DI latches less HOLD pressure. Below 1 m/s `DAS_jerkMax` is `AP1_LAUNCH_JERK_MAX` = 1.5; `DAS_jerkMin` stays 8.

## AP1 cruise and stalk (milestone 3)

With FrogPilot Speed Limit Controller on, AP1 adds (details and code references in [`STALK.md`](STALK.md) and [`DIVERGENCES.md`](DIVERGENCES.md)):

- **SLC raise:** openpilot's set speed can go above the stock limit to posted limit + offset (`slc_raise.py`, `frogpilot_vcruise.py`). Posted limit from the stock Mobileye sign first, then the car's map limit (`speed_limit.py`).
- **Cluster set speed:** `DAS_accSpeedLimit` on 0x389 carries openpilot's set speed (DBC factor 0.4), with a guard against the UNSET value that showed ~90 mph when stopped.
- **Engage:** tip up/down latches the current speed (sticky) and holds it through speed limit changes until a pull or disengage; pull engages at posted limit + offset.
- **Engaged tips:** first position ±1 mph; full tip (raw `SpdCtrlLvr_Stat` 4 / 8) next multiple of 5. A tipped set stays through speed limit zone changes until a pull or disengage. A pull returns to posted limit + offset and follows lower limits too. Tips are floored at 1 mph.
- **Posted-limit guard:** SLC ignores limits under 15 mph and waits 2 s before following a very large sudden drop (`Ap1SlcLimitGuard`).
- **Experimental Mode:** hold the stalk pulled ~2 s while engaged (`stalk_pull_hold.py`), or forward ~2 s while disengaged (`stalk_fwd_hold.py`).
- **Soft-steer resume:** after a firm override, 0.3 s at hands level 0 before lateral eases back in (`hso.AP1_RESUME_HOLD_S`).

## Doc index

- [`STALK.md`](STALK.md): full AP1 stalk map (engage, tips, pull, forward, Experimental holds, follow distance).
- [`DIVERGENCES.md`](DIVERGENCES.md): every known difference from upstream FrogPilot or Tinkla.
- [`PLATFORMS.md`](PLATFORMS.md): Tesla platform recognition and safety flags.
- [`THEME.md`](THEME.md): BogPilot theme pack, color editing, startup alert note.
- Phase 0 / 1 history: `SOURCES.md`, `RENAME_MAP.md`, `PORT_PLAN.md`, `HOTSPOTS.md`, `BRIDGE_DIFF.md`, `PHASE1_STATUS.md`.

## Pinned SHAs

| Role | Repo | Branch | SHA |
| --- | --- | --- | --- |
| Architecture base | https://github.com/FrogAi/FrogPilot | `FrogPilot` | `1e23dec6352cef5a36a87be0af7d7a082b7c48a4` |
| Tesla behavior donor | https://github.com/bbqgloves/earlytesla-openpilot | `tesla_unity_dev` | `501c7de91b59c70510e9dc1585acfde9b7102c93` |
| Conflict map only (do not merge) | https://github.com/bbqgloves/openpilot | `frog_ap1` | `73a16cdd50033cb9f57a03fd619e2129558eef43` |

`SOURCES.md` has the pin notes. `RENAME_MAP.md` is the path map. `PORT_PLAN.md` is the feature plan. `HOTSPOTS.md` is the Phase 0 read of the hot files. Those four files were copied from the Phase 0 inventory. Clone paths recorded in them are where the sources were read, not this product tree. Product docs live in `docs/tesla/` in this repo.

Safety replay: [`panda/tests/safety_replay/README.md`](../../panda/tests/safety_replay/README.md). `replay_ap1.py` runs recorded rlogs through the AP1 panda safety before a change goes on the road.

## Disclaimer

BogPilot is not a product. No warranty. The driver remains responsible. Comply with local law. AP1 has been driven by the maintainer on one Model S with a comma 3X; that is a personal impression, not a bench or safety validation.
