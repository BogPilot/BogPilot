# Developer HUD (branch `bogpilot-tesla-HUD`)

A live data panel on the right side of the driving screen, modeled on sunnypilot's
Qt "Developer UI". Display only: no panda, safety, controls, planner or car code changes.

## Attribution
Metric choice, formulas and color thresholds follow sunnypilot's
`selfdrive/ui/sunnypilot/qt/onroad/developer_ui/developer_ui.cc` (branch `master-dev-c3`),
whose file header is the MIT License, Copyright (c) 2021-, Haibin Wen, sunnypilot, and
a number of other contributors. That header is kept in `frogpilot/ui/qt/onroad/developer_hud.{h,cc}`.
The two-column layout, the angle-control substitute and the Qt painting code are BogPilot's own.

## Turning it on
Settings → Visuals → Developer UI → Developer Metrics → **Developer HUD** (tuning level 3).
The toggle is file-backed, like `EnableICIntegration`: the prebuilt `params_pyx.so` cannot store
new Params keys. Over SSH:

    echo 1 > /data/params_bogpilot/DeveloperHUD   # on
    echo 0 > /data/params_bogpilot/DeveloperHUD   # off

The UI re-reads the file every 2 s. Absent file = off. `/data/params_bogpilot` survives Params cleanup.

## What it shows
| Cell | Source | Notes |
|---|---|---|
| ACCEL | `carState.aEgo` | m/s² |
| REL DIST | `radarState.leadOne.dRel` | ft or m per unit setting; orange < 15 m, red < 5 m |
| LEAD SPD | `carState.vEgo + leadOne.vRel` | mph or km/h; orange closing, red closing > 10 mph |
| REL SPEED | `leadOne.vRel` | same colors |
| LAT ACCEL | `controlsState.desiredCurvature · v² − liveParameters.roll · g` | desired, "-" when not steering |
| REAL STEER | `carState.steeringAngleDeg` | orange > 90°, red > 180° |
| DESIRED STEER | `controlsState.lateralControlState.angleState.steeringAngleDesiredDeg` | angle cars (Tesla AP1); torque cars show FRICTION (`liveTorqueParameters.frictionCoefficientFiltered`) instead |
| ACTUAL LAT | `controlsState.curvature · v² − roll · g` | m/s² |
| ALTITUDE | `gpsLocationExternal` / `gpsLocation` `.altitude` | ft or m; "-" without a fix |
| MEM % | `deviceState.memoryUsagePercent` | orange > 85% |

Green = openpilot steering (`carControl.latActive`), grey = driver overriding (`carState.steeringPressed`).

## Placement (2160×1080)
Top-right corner under the Experimental Mode button; moves left of the pedal icons when
"Gas / Brake Pedal Indicators" is on; scales down (to 60%) to stay above the bottom-right
compass / weather / map row. Hidden with the big map and in reverse.

## Prebuilt UI caveat
This branch changes Qt sources only. The repo ships a prebuilt aarch64 `selfdrive/ui/ui`
(and a `prebuilt` marker), so the device keeps running the old UI until the UI is rebuilt
on the comma (`scons -j4 selfdrive/ui/ui`) or a rebuilt binary is committed.
