# BogPilot UI: names and Tesla AP1 settings

Branch `bogpilot-tesla-ui`. Display text and the settings panel only. No car, controls or panda code changes.

## BogPilot name on screen
User-visible "FrogPilot" text in the Qt UI now reads "BogPilot": the drive stats heading, the
startup alert preset button, panel descriptions, backup and stats titles, the alerts panel, and the
error-log alert.

These still say FrogPilot on purpose, because they name something that really is FrogPilot's:
- the "Share Driving Data" toggle (the data goes to FrogPilot's server)
- the FrogPilot map style
- SpeedLimitFiller.frogpilot.com and the FrogPilot Discord guide
- FrogsGoMoo tunes and the frog-themed stat names

Code names, params (`FrogPilotStats` and others), file paths and cereal names are unchanged.

Only the English source strings changed. The `.ts`/`.qm` translation files were not regenerated.
The build's `lupdate` step updates them when the UI is rebuilt. Until a translation is updated,
other languages show the old translated text.

## Settings → BogPilot → Vehicle Settings → Tesla AP1 Settings
These are shown when the car is fingerprinted as `TESLA_AP1_MODELS`, or when there are no CarParams
yet. They are file-backed in `/data/params_bogpilot`, the same files the car code already reads.
A missing file means on, the current behavior. The UI writes `1` or `0`.

| Switch | File | Default | What it does |
|---|---|---|---|
| Dash Cluster Integration | `EnableICIntegration` | on | Shows openpilot on the dash cluster while engaged: cluster set speed (including curve and speed limit targets), Autosteer icon, hands-on reminders, path line. Off = stock cluster; openpilot sends no cluster frames. Read every control step, so it takes effect right away. |
| Earlier, Gentler Regen Stops | `RegenComfortBrake` | on | Lead approaches sized to regen (1.0 m/s² comfort brake instead of 2.5). Hard braking and stopped distance are unchanged. Read every planner step. |

From SSH, `echo 0 > /data/params_bogpilot/<file>` (or `echo 1`) still works. The switch shows
the new value the next time the panel opens.

Like every UI change, this needs the UI rebuilt on the comma. The comma runs the prebuilt `selfdrive/ui/ui`.
