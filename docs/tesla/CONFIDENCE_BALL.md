# BogPilot Confidence Ball

The comma four's confidence ball, ported to BogPilot's Qt driving screen on the comma 3X.

## What it shows

A small ball rides up and down the right edge of the driving screen. Its height and color come
from the driving model's own predictions of whether you are about to brake or take over steering:

    confidence = (1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs))

(`modelV2.meta.disengagePredictions`; an empty list counts as 1, so no data reads as 0%.)

| State | Ball |
|---|---|
| openpilot active, confidence above 50% | high, green (teal to green) |
| openpilot active, 20 to 50% | lower, orange |
| openpilot active, under 20% | low, red |
| you are overriding | white to grey |
| openpilot off | dark, slides out of the bottom |

"Active" means any engaged status, including Always On Lateral and traffic mode. The value is
smoothed with the same first-order filter as the comma four (0.5 s time constant, starting at
-0.5 so the ball slides in from below when you engage).

On route 0000002a the ball was green 92.8% of the engaged time, orange 6.5% and red 0.7%
(0.5% from the raw value; the rest is the half-second slide-in right after engaging).

## Where it sits

A 60 px strip on the far right of the onroad window that covers the 30 px status border and the
30 px camera margin. The ball is 54 px across and centered on the line between the border and the
camera view, so it never covers the Experimental button, pedal icons, compass, the bottom-right
buttons or the Developer HUD panel, which all end 30 px inside the camera view. It is hidden while
the map covers the right side of the screen.

## On / off

Visuals > **BogPilot Confidence Ball** (top of the page, all tuning levels). On by default.

The switch is a file because the comma's prebuilt `params_pyx.so` cannot store new Params keys:

    echo 0 > /data/params_bogpilot/ConfidenceBall   # hide
    echo 1 > /data/params_bogpilot/ConfidenceBall   # show (same as no file)

The driving screen re-reads the file every 2 seconds.

## Scope

Display only: `frogpilot/ui/qt/onroad/confidence_ball.h` (header only, no SConscript change),
three lines in `selfdrive/ui/qt/onroad/onroad_home.{h,cc}` and the settings switch in
`frogpilot/ui/qt/offroad/visual_settings.{h,cc}`. No car, controls, planner or panda changes.
It needs the screen code recompiled on the comma (`scons -j4 selfdrive/ui/ui`) to appear.

Ported from openpilot `selfdrive/ui/mici/onroad/confidence_ball.py` (commaai/openpilot a742df6,
MIT License, Copyright (c) 2018, Comma.ai, Inc.).
