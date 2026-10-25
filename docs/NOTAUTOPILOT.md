# Coming from NotAutopilot

This page is for Tesla owners who already run **NotAutopilot (NAP)** on a comma device and want to try
BogPilot. The `bogpilot-tesla-notautopilot` branch is `bogpilot-tesla` with a few extras for NAP users:

- On the first boot it finds your NAP settings, backs them up and copies over the ones that BogPilot uses.
- It adds a **Tesla Car Type & NotAutopilot** page under Settings > BogPilot > Vehicle Settings. That page has an Auto / AP1 / Pre-AP
  choice and shows what BogPilot detected and imported.

> Whatever you run, you are still the driver. Keep your hands on the wheel and your eyes on the road.

## Before you start

- **Do not use NAP's Uninstall button.** It wipes everything in `/data`, including your NAP settings.
  Switch over SSH instead (below).
- **Your steering (EPAS) patch stays.** If NAP patched your EPAS firmware, that patch lives in the car,
  not on the comma device. Switching software does not undo it.
- **Your radar setup stays.** A radar that learned your car's VIN keeps it. That also lives in the car.
- **The panda gets reflashed automatically.** BogPilot puts its own panda firmware on at the first boot.
  That is normal. See [Pre-AP needs the right panda firmware](#pre-ap-needs-the-right-panda-firmware).
- **Write down your NAP settings** (pedal on or off, radar on or off, radar offset) in case you need to
  set them again by hand.
- Do all of this parked, with the car off, and the device on Wi-Fi.

The steps are: connect over SSH, back up, switch, check.

## Step 1: Connect to the device over SSH

comma's official guide: [docs.comma.ai/how-to/connect-to-comma](https://docs.comma.ai/how-to/connect-to-comma/).
The short version:

1. Add an SSH key to your GitHub account
   ([GitHub's guide](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account)).
2. On the device, in NAP, open **Settings > Developer**. Turn on **Enable SSH**, then use **SSH Keys** to
   add your GitHub username. The device fetches your keys from GitHub once. If you change keys later,
   enter the username again.
3. Find the device's IP address under **Settings > Network > Advanced > IP Address**.
4. From a computer on the same Wi-Fi:

```
ssh comma@<comma-ip>
```

If your key isn't the default one, add `-i <path-to-your-key>`. The user is `comma` and the port is `22`.

## Step 2: Back up your NAP settings off the device

The switch below keeps everything on the device, and BogPilot makes its own copy on the first boot. Still,
keep one copy on your computer in case something goes wrong.

What NAP keeps on the device:

| What | Where |
|---|---|
| NAP settings, one file each | `/data/params/d/NAP*` |
| NAP's fallback settings file (not on every device) | `/data/nap_params.json` |
| EPAS firmware images, if you used NAP's EPAS tools | `/data/openpilot/scripts/nap/firmware/` |

About the EPAS folder: this was checked in NAP's `scripts/nap/flash_epas.py` on the `nap-release` and `nap-ap1`
branches. Reading your EPAS saves the stock image as `epas-firmware-0x7000-0x45fff.bin` in that folder.
Patching also saves `9e51ddd80606fbdaaf604c73c8dde0d1.modified.bin` there. NAP also ships its own stock image
under the same `epas-firmware-0x7000-0x45fff.bin` name, so that file being there doesn't prove it was read
from your car. Other NAP branches or older versions weren't checked and may save files somewhere else.
The command below saves the whole folder.

On the device (over SSH), while NAP is still installed:

```
cd /data
FILES="params/d/NAP*"
[ -f nap_params.json ] && FILES="$FILES nap_params.json"
[ -d openpilot/scripts/nap/firmware ] && FILES="$FILES openpilot/scripts/nap/firmware"
tar czf /data/nap_settings_backup.tar.gz $FILES
tar tzf /data/nap_settings_backup.tar.gz
```

The last line lists what went into the archive. You should see your `params/d/NAP...` files.

Then, on your computer (not in the SSH window):

```
scp comma@<comma-ip>:/data/nap_settings_backup.tar.gz .
```

> **Keep this backup private.** It can contain your car's VIN (NAP's radar setting `NAPRadarDonorVin`)
> and firmware read from your car. Don't post it in an issue, on Discord or anywhere public.

## Step 3: Switch to BogPilot over SSH (recommended)

On the device (over SSH):

```
df -h /data
cd /data
mv openpilot openpilot_nap
git clone --recurse-submodules --depth 1 -b bogpilot-tesla-notautopilot https://github.com/BogPilot/openpilot.git openpilot
sudo reboot
```

- `df -h /data` shows free space. Both versions stay on the device, so you need room for a second copy.
- Wait until the `git clone` line finishes without an error before you reboot. If it fails, see
  [Troubleshooting](#troubleshooting).
- The first boot can take several minutes while it builds.
- Your NAP folder stays as `/data/openpilot_nap`. **Keep it.** If NAP flashed your EPAS, that folder holds
  NAP's stock EPAS backup, and it makes switching back easy.
- On the first boot, before anything else runs, BogPilot copies every NAP setting file to
  `/data/params_bogpilot/nap_backup/` and imports the ones it uses. After that it clears setting files it
  doesn't know, so the `NAP*` files are gone from `/data/params/d`. The copies in `nap_backup` and your
  off-device backup stay.

## Step 4: Check that it worked

On the device, open **Settings > BogPilot > Vehicle Settings > VEHICLE SETTINGS > Tesla Car Type & NotAutopilot**. The **NotAutopilot Settings** row shows:

- **Imported (N)**: N settings were copied over. Tap the row to see each one.
- **Found, nothing to import**: NAP was found, but nothing needed copying (for example, everything was
  already set in BogPilot, or NAP was on its defaults).
- **Not found**: no NAP settings were on the device. See [Troubleshooting](#troubleshooting).

Over SSH you can also read the full result and the log:

```
cat /data/params_bogpilot/NAPMigrationStatus
cat /data/params_bogpilot/nap_backup/migration.log
ls /data/params_bogpilot/nap_backup/params/
```

`NAPMigrationStatus` should say `"result": "imported"`. Its `imported` part lists what was set, and its
`skipped` part lists what was left out and why. VINs are never written to either file.

## Other option: fresh install (settings are not kept)

1. Uninstall or factory reset the device. This erases everything in `/data`, including your NAP settings.
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

The page is at **Settings > BogPilot > Vehicle Settings > VEHICLE SETTINGS > Tesla Car Type & NotAutopilot**.

Leave it on **Auto** unless the "Detected Car Type" row is wrong. Pick **Pre-AP** for a 2012-2014 Model S without
Autopilot hardware that uses NAP's add-ons (patched steering, pedal, radar). The change applies the next
time you turn the car on. The rows under it show which type BogPilot used, why, and whether it sees your
pedal and radar.

## Pre-AP needs the right panda firmware

Pre-AP steering only works when the panda runs firmware that has **safety mode 37 (Tesla Pre-AP)**. That mode
is only built into DEBUG panda firmware (`ALLOW_DEBUG`), the same kind NAP uses. AP1 needs DEBUG firmware too.

**How you get it:** you don't do anything. BogPilot ships its panda firmware in `panda/board/obj/` and
flashes it at boot whenever the panda's firmware doesn't match. NAP's panda firmware is replaced at the first
boot.

> **Not ready yet on this branch.** The panda firmware currently shipped here was built before mode 37 was
> added, so it doesn't have it. Until a rebuilt firmware lands, a Pre-AP car will not steer: the panda falls
> back to a no-output mode and BogPilot shows a "Controls Mismatch" error. AP1 cars are not affected. This note
> will be removed once the rebuilt firmware is on the branch.

To check which panda firmware BogPilot ships, over SSH: `cat /data/openpilot/panda/board/obj/version`. It should end in
`-DEBUG`.

## Switching back to NotAutopilot

On the device (over SSH):

```
cd /data
mv openpilot openpilot_bogpilot
mv openpilot_nap openpilot
cp -n /data/params_bogpilot/nap_backup/params/NAP* /data/params/d/
sudo reboot
```

- BogPilot clears setting files it doesn't know when it starts, so the `cp` line puts your NAP settings back
  from the copy BogPilot made. `-n` means it never overwrites a file that is already there.
  `/data/nap_params.json` is never touched.
- BogPilot's own settings stay in `/data/params_bogpilot` and `/data/openpilot_bogpilot`. NAP ignores them.
- If `/data/params_bogpilot/nap_backup/params/` is missing, restore from your off-device backup instead.
  On your computer:

  ```
  scp nap_settings_backup.tar.gz comma@<comma-ip>:/data/
  ```

  Then on the device:

  ```
  mkdir -p /tmp/nap_restore
  tar xzf /data/nap_settings_backup.tar.gz -C /tmp/nap_restore
  cp -n /tmp/nap_restore/params/d/NAP* /data/params/d/
  [ -f /data/nap_params.json ] || cp /tmp/nap_restore/nap_params.json /data/ 2>/dev/null
  sudo reboot
  ```

- Without the old folder, you can also reinstall NAP from its installer URL,
  `installer.comma.ai/NotAutopilot/openpilot/nap-release`. Don't uninstall first, or your settings are wiped.

## Troubleshooting

**"NotAutopilot Settings" says Not found.** BogPilot found no `NAP*` files in `/data/params/d` and no
`/data/nap_params.json`. That happens after an uninstall or a factory reset. Put the files back from your
off-device backup, then reboot. The import runs again because it only marks itself done after it finds NAP:

```
mkdir -p /tmp/nap_restore
tar xzf /data/nap_settings_backup.tar.gz -C /tmp/nap_restore
cp -n /tmp/nap_restore/params/d/NAP* /data/params/d/
[ -f /data/nap_params.json ] || cp /tmp/nap_restore/nap_params.json /data/ 2>/dev/null
sudo reboot
```

(Copy the archive to the device first with `scp nap_settings_backup.tar.gz comma@<comma-ip>:/data/`.)

**I want to run the import again.** It runs once. To run it again, put the NAP files back and delete the
marker:

```
cp -n /data/params_bogpilot/nap_backup/params/NAP* /data/params/d/
rm /data/params_bogpilot/NAPMigrationDone
sudo reboot
```

It still won't change a BogPilot setting that already exists. To re-import one value, delete that file
from `/data/params_bogpilot/` first (for example `/data/params_bogpilot/PreAPRadarOffset`).

**`ssh` says "Permission denied (publickey)".** Check that SSH is on, and that the GitHub username under
**SSH Keys** is right. The device only fetches your keys once, so enter the username again after you add a
new key on GitHub. If your key isn't the default, use `ssh -i <path-to-your-key> comma@<comma-ip>`.

**`git clone` failed or stopped part way.** Usually it's no internet or not enough space (`df -h /data`).
Your NAP copy is safe in `/data/openpilot_nap`. Remove the half-finished clone and try again:

```
cd /data
rm -rf /data/openpilot
git clone --recurse-submodules --depth 1 -b bogpilot-tesla-notautopilot https://github.com/BogPilot/openpilot.git openpilot
```

Or go back to NAP for now with `mv openpilot_nap openpilot` and `sudo reboot`. Don't reboot while
`/data/openpilot` is missing or half-cloned.

**The first boot is slow.** It builds BogPilot and reflashes the panda. Several minutes is normal. Leave it on
power until the home screen shows.

**The car type is wrong.** Open **Settings > BogPilot > Vehicle Settings > VEHICLE SETTINGS > Tesla Car Type & NotAutopilot** and set **Car Type** to AP1 or Pre-AP. It applies the next
time you turn the car on.

**My pedal or radar settings look wrong.** Tap the **NotAutopilot Settings** row to see what was copied,
and read `/data/params_bogpilot/nap_backup/migration.log`. Values outside the allowed range are skipped and
the BogPilot default is kept. Set them again by hand in BogPilot.

## Credits

- **NotAutopilot**: [github.com/NotAutopilot/openpilot](https://github.com/NotAutopilot/openpilot), MIT
  license. NAP's setting names, defaults, Auto / Pre-AP / AP1 car type idea and pedal calibration check are
  the basis for this import.
- **Tinkla** by BogGyver ([github.com/BogGyver/openpilot](https://github.com/BogGyver/openpilot)): the original
  Pre-AP and AP1 Tesla openpilot work. The pedal, radar and EPAS tools that NAP and this branch rely on began there.
- **xnor-tech**: the Tesla openpilot fork that NAP is built on (comma → sunnypilot → xnor-tech → NAP). NAP's
  AP1 support comes from it.
