# Coming from NotAutopilot

This page is for Tesla owners who already run **NotAutopilot (NAP)** on a comma device and want to try
BogPilot. The `bogpilot-tesla-notautopilot` branch is `bogpilot-tesla` with a few extras for NAP users:

- On the first boot it finds your NAP settings, backs them up and copies over the ones that BogPilot uses.
- It adds a **Tesla Car Type & NotAutopilot** page under Settings > Vehicles. That page has an Auto / AP1 / Pre-AP
  choice and shows what BogPilot detected and imported.

> Whatever you run, you are still the driver. Keep your hands on the wheel and your eyes on the road.

## Before you start

- **Your steering (EPAS) patch stays.** If NAP patched your EPAS firmware, that patch lives in the car,
  not on the comma device. Switching software does not undo it.
- **Your radar setup stays.** A radar that learned your car's VIN keeps it. That also lives in the car.
- **The panda gets reflashed automatically.** BogPilot puts its own panda firmware on at the first boot.
  That is normal.
- **Write down your NAP settings** (pedal on or off, radar on or off, radar offset) in case you need to
  set them again by hand.

## Option 1: switch over SSH and keep your settings (recommended)

You need SSH access to the device, the same access you would use for NAP's scripts.

```
cd /data
mv openpilot openpilot_nap
git clone --recurse-submodules --depth 1 -b bogpilot-tesla-notautopilot https://github.com/BogPilot/openpilot.git openpilot
sudo reboot
```

- The first boot can take several minutes while it builds.
- Your NAP folder stays as `/data/openpilot_nap`. **Keep it.** If NAP flashed your EPAS, that folder holds
  NAP's stock EPAS backup, and it makes switching back easy.
- After the reboot, open **Settings > Vehicles > Tesla Car Type & NotAutopilot**. "NotAutopilot Settings"
  should say **Imported**. Tap it to see exactly what was copied.

## Option 2: fresh install (settings are not kept)

1. Uninstall or factory reset the device. Uninstalling from NAP erases everything in `/data`, including
   your NAP settings.
2. On the setup screen, choose **Custom Software**.
3. Enter `installer.comma.ai/BogPilot/bogpilot-tesla-notautopilot`. The short form
   `BogPilot/bogpilot-tesla-notautopilot` also works.
4. Wait for the install, then reboot.
5. Set the pedal and radar settings again by hand.

## What carries over

The import runs once, on the first boot, before anything else touches your settings.

- It **never deletes** NAP files. Every NAP setting is copied byte for byte to
  `/data/params_bogpilot/nap_backup/` first.
- It only fills in a BogPilot setting that you haven't set yet, so your own changes always win.
- Everything it did is written to `/data/params_bogpilot/nap_backup/migration.log`.
- It does nothing on a device that never ran NAP.

| NotAutopilot setting | `nap_params.json` key | BogPilot setting (`/data/params_bogpilot/`) |
|---|---|---|
| NAPPedalEnabled | use_pedal | PreAPPedalEnabled |
| NAPPedalCanBus | pedal_can_zero (true = 0, false = 2) | PreAPPedalCanBus (0 or 2) |
| NAPPedalCalibDone | pedal_calibrated | PreAPPedalCalibDone (only if the calibration values were really changed, same check as NAP) |
| NAPPedalCalibMin / Max / Zero / Factor | pedal_calib_min / max / zero / factor | PreAPPedalCalibMin / Max / Zero / Factor |
| NAPAdaptiveAccel | | PreAPAdaptiveAccel |
| NAPRadarEnabled | radar_enabled | PreAPRadarEnabled |
| NAPRadarBehindNosecone | radar_behind_nosecone | PreAPRadarBehindNosecone |
| NAPRadarOffset | radar_offset | PreAPRadarOffset |
| NAPRadarHud | | PreAPRadarHud |
| NAPRadarUpsideDown | radar_upside_down | PreAPRadarUpsideDown |
| NAPRadarEpasType | radar_epas_type | PreAPRadarEpasType (0 to 4) |
| NAPRadarPosition | radar_position | PreAPRadarPosition (0 to 2) |

If NAP has both a setting file and `nap_params.json`, the setting file wins, the same as in NAP. Values
outside the allowed range are skipped and the BogPilot default is kept.

**Not copied, on purpose:**

| NotAutopilot setting | Why |
|---|---|
| NAPCarType, NAPForcePreAP | Recorded, but the car type is never forced. BogPilot picks AP1 or Pre-AP itself, so an AP1 car that once ran NAP isn't stuck on Pre-AP. To force it, use the Car Type choice in Settings. |
| NAPFollowDistance | In BogPilot, follow distance comes from the stalk. |
| double_pull_window_ms | BogPilot never changes stalk or set-speed behaviour. |
| accel_profile | Shown as a suggestion only: Chill → Eco, Standard → Standard, MadMax → Sport (Settings > Longitudinal). |
| NAPRadarIgnoreHwFail | It hides radar hardware faults. Turn it on again by hand only if you are sure. |
| NAPiBoosterEnabled, NAPBrakeFactor, NAPPedalProfile | Unfinished or unused in NAP. |
| NAPRadarDonorVin, NAPRadarReadVin, NAPRadarVinReadStatus | Backed up only. A VIN is never copied, logged or uploaded. |
| NAPEpasRiskAccepted, NAPActiveProfile, NAPScriptRunning | One-time NAP script state. |

Nothing is imported into speed, stalk or cruise settings. The OccViz and YOLO views stay off.

## Car type: Auto, AP1 or Pre-AP

Leave it on **Auto** unless the "Detected Car Type" row is wrong. Pick **Pre-AP** for a 2012-2014 Model S without
Autopilot hardware that uses NAP's add-ons (patched steering, pedal, radar). The change applies the next
time you turn the car on. The rows under it show which type BogPilot used, why, and whether it sees your
pedal and radar.

## Switching back to NotAutopilot

```
cd /data
mv openpilot openpilot_bogpilot
mv openpilot_nap openpilot
cp -n /data/params_bogpilot/nap_backup/params/NAP* /data/params/d/
sudo reboot
```

- BogPilot clears setting files it doesn't know when it starts, so the `cp` line puts your NAP settings back
  from the backup. `/data/nap_params.json` is never touched.
- Without the old folder, you can also reinstall NAP from its installer URL,
  `installer.comma.ai/NotAutopilot/openpilot/nap-release`. That also wipes settings if you uninstall first.
- To run the import again later, delete `/data/params_bogpilot/NAPMigrationDone` and reboot. It can only
  import what is still in `/data/params/d` or `/data/nap_params.json`.

## Credits

- **NotAutopilot**: [github.com/NotAutopilot/openpilot](https://github.com/NotAutopilot/openpilot), MIT
  license. NAP's setting names, defaults, Auto / Pre-AP / AP1 car type idea and pedal calibration check are
  the basis for this import.
- **Tinkla** by BogGyver ([github.com/BogGyver/openpilot](https://github.com/BogGyver/openpilot)): the original
  Pre-AP and AP1 Tesla openpilot work. The pedal, radar and EPAS tools that NAP and this branch rely on began there.
- **xnor-tech**: the Tesla openpilot fork that NAP is built on (comma → sunnypilot → xnor-tech → NAP). NAP's
  AP1 support comes from it.
