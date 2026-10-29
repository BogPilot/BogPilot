# BogPilot theme

BogPilot ships its own theme pack, named **BogPilot**. It is selected by
default. The original FrogPilot theme stays available in the theme picker.

## What the BogPilot theme looks like

Since the AP1 defaults update (October 2026) the BogPilot theme renders like
the stock openpilot look, matching the maintainer's own device:

| Component | BogPilot theme renders as | Before |
| --- | --- | --- |
| Colors (path, lane lines, lead marker, sidebar) | stock openpilot | FrogPilot green |
| Sidebar icons (home / flag / settings) | stock openpilot | animated frog icons |
| Engage / disengage sounds | stock openpilot | FrogPilot frog sounds |
| Turn-signal animation | none | FrogPilot frog animation + blind-spot frame |
| Steering wheel icon | stock openpilot wheel | frog wheel |
| Follow-distance icons | BogPilot icons (unchanged) | BogPilot icons |

The BogPilot pack (`frogpilot/assets/bogpilot_theme`) holds real copies of
the stock files, not links, so it is BogPilot's own theme to edit:

- `colors/colors.json`: copy of `stock_theme/colors/colors.json`
- `icons/button_flag.png`, `button_home.png`, `button_settings.png`: copies of
  `selfdrive/assets/images`
- `sounds/*.wav` (engage, disengage, prompt, prompt_distracted, refuse,
  warning_immediate, warning_soft): copies of `selfdrive/assets/sounds`
- `steering_wheel/wheel.png`: copy of `selfdrive/assets/img_chffr_wheel.png`
- `signals/`: no frames (same as Turn Signal "None")
- `distance_icons/`: BogPilot's own follow-distance icons

Icons, sounds, turn signals and distance icons are always read from the
BogPilot pack, so an edit there shows on the next boot.

### Colors and steering wheel: the "stock" stand-in

The prebuilt UI keys two looks on the literal value `stock`, and no theme file
can reproduce them:

- `CustomColors` (`frogpilot_ui.cc` `use_stock_colors`): with `stock` the UI
  draws the stock path (green HSL gradient, 0.4 → 0 alpha), lane lines with
  alpha = min(probability, 0.7), stock path edges, and the sidebar and
  developer sidebar in plain white. With a theme, the same colors.json values
  give a solid path at full alpha fading to 0.1 (much more opaque), lane lines
  at 0.70 × probability, path edges in the PathEdge color, and the TEMP status
  and developer sidebar text at alpha 178. The lead chevron is the same red
  either way.
- `WheelIcon` (`buttons.cc` `use_stock_wheel`): with `stock` the wheel button
  switches to the Experimental Mode icon while Experimental Mode is on. A theme
  wheel always shows the wheel image.

So `frogpilot_variables.py` (`resolve_bogpilot_theme`) passes `BogPilot` on as
`stock` for those two toggles only while the BogPilot `colors.json` /
`wheel.png` are still identical to the stock files. Edit either file and the
stand-in switches off by itself, and the edited asset shows (with theme-style
rendering for colors, and no Experimental Mode icon swap for the wheel).

The optional UI patch `bogpilot_theme_stock_rendering.patch` (kept outside this
branch; needs a device UI rebuild) makes both behaviours theme assets instead:
`"StockRendering": true` in a colors.json selects the stock rendering, and a
theme can ship `steering_wheel/experimental.*` for the Experimental Mode swap.

Holiday themes still take over during holiday weeks when "Holiday Themes" is on.

## How themes work here

Theme assets are files under `/data/themes`, selected by existing params and
linked at runtime by `frogpilot/assets/theme_manager.py`:

| Param | Asset | Default |
| --- | --- | --- |
| `CustomColors` | `theme_packs/<name>/colors` | `BogPilot` |
| `CustomIcons` | `theme_packs/<name>/icons` | `BogPilot` |
| `CustomSounds` | `theme_packs/<name>/sounds` | `BogPilot` |
| `CustomSignals` | `theme_packs/<name>/signals` | `BogPilot` |
| `CustomDistanceIcons` | `theme_packs/<name>/distance_icons` | `BogPilot` |
| `WheelIcon` | `steering_wheels/<name>.png` | `BogPilot` |

On boot, `ThemeManager.copy_default_theme()` still installs the FrogPilot
`frog` / `frog-animated` packs (from `holiday_themes/world_frog_day`), and
additionally installs the BogPilot pack from
`frogpilot/assets/bogpilot_theme/` into `/data/themes`.

The theme picker in the prebuilt UI lists packs by scanning
`/data/themes/theme_packs` (and holiday themes / Stock). Because BogPilot is
installed there as a real pack, it appears as **BogPilot** without a UI
rebuild. No new param keys were added (the July `params_pyx.so` would reject
them).

## Editing BogPilot colors later

Edit the source copy, then reboot (or re-run theme install) so it is copied
into `/data/themes`:

```
frogpilot/assets/bogpilot_theme/colors/colors.json
```

Keys: `LaneLines`, `LeadMarker`, `Path`, `PathEdge`, `Sidebar1`, `Sidebar2`,
`Sidebar3` (each with `red` / `green` / `blue` / `alpha`).

Other assets live under the same folder:

- `icons/` — sidebar / home / settings icons
- `sounds/` — openpilot alert wavs (any missing file falls back to stock)
- `signals/` — turn-signal frames (empty: no animation)
- `distance_icons/` — follow-distance button icons
- `steering_wheel/wheel.png` — top-right wheel icon

Do not edit `/data/themes/...` alone; that tree is overwritten from
`bogpilot_theme` on boot.

## Startup alert is not part of themes

The boot splash ("Your m∞v" / "Hands present, mind at ease" and its dark
background) is **not** controlled by theme packs. It comes from:

- `selfdrive/controls/lib/events.py` (`customStartupAlert`)
- `frogpilot/common/frogpilot_variables.py` (`STARTUP_MESSAGE_*`,
  `migrate_startup_messages`)
- `system/manager/manager.py` (boot migration)

See commit `4759e262` for the prebuilt-device startup alert fix.

## Migration

On first boot of a build that includes this theme, devices whose theme params
are still the stock FrogPilot defaults (`frog`, `frog-animated`, `stock`
distance icons) are moved to `BogPilot`. A one-time marker
`/data/themes/.bogpilot_theme_migrated` prevents that from happening again, so
choosing FrogPilot later keeps FrogPilot. A non-default theme the user already
picked is left alone.

## AP1 installs

The Tesla AP1 profile (`selfdrive/car/tesla/ap1_defaults.py`) sets every theme
menu to BogPilot (Custom Themes on, Random Themes off):

| Menu | Param | AP1 value |
| --- | --- | --- |
| Color Scheme | `CustomColors` | `BogPilot` |
| Steering Wheel | `WheelIcon` | `BogPilot` |
| Icon Pack | `CustomIcons` | `BogPilot` |
| Sound Pack | `CustomSounds` | `BogPilot` |
| Turn Signal | `CustomSignals` | `BogPilot` |
| Distance Button | `CustomDistanceIcons` | `BogPilot` |

Color Scheme and Steering Wheel look exactly stock through the stand-in above
(while the BogPilot `colors.json` / `wheel.png` equal the stock files).

A one-time step (`/data/params_bogpilot/AP1ThemeBogPilot2`) also moves values
that only pick the stock look (`stock`, or `none` for Turn Signal) to BogPilot
for Color Scheme, Steering Wheel, Icon Pack, Sound Pack and Turn Signal. Stock
distance icons, a "None" steering wheel and any other theme are left alone. The
marker is new ("2") because the earlier step (`AP1ThemeBogPilot`) left Color
Scheme and Steering Wheel on stock; "Reset toggles to default" clears both.
