#!/usr/bin/env python3
"""BogPilot theme pack and one-time FrogPilot -> BogPilot migration."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from openpilot.frogpilot.common.frogpilot_variables import (
  BOGPILOT_STOCK_ALIAS_FILES, BOGPILOT_THEME, FROGPILOT_THEME_DEFAULTS, migrate_theme_params, resolve_bogpilot_theme,
)

BOGPILOT_THEME_PATH = Path(__file__).resolve().parents[1] / "bogpilot_theme"
BOGPILOT_THEME_COMPONENTS = ("colors", "distance_icons", "icons", "signals", "sounds")
STOCK_THEME_PATH = Path(__file__).resolve().parents[1] / "stock_theme"


class FakeParams:
  def __init__(self, values=None):
    self.values = dict(values or {})

  def get(self, key, encoding=None):
    value = self.values.get(key)
    if value is None:
      return None
    if encoding:
      return value if isinstance(value, str) else value.decode(encoding)
    return value.encode() if isinstance(value, str) else value

  def put(self, key, value):
    self.values[key] = value if isinstance(value, str) else value.decode()


def copy_bogpilot_theme(theme_save_path):
  theme_pack_destination = theme_save_path / "theme_packs" / BOGPILOT_THEME
  shutil.rmtree(theme_pack_destination, ignore_errors=True)
  for component in BOGPILOT_THEME_COMPONENTS:
    source_folder_path = BOGPILOT_THEME_PATH / component
    if source_folder_path.is_dir():
      shutil.copytree(source_folder_path, theme_pack_destination / component, dirs_exist_ok=True)

  steering_wheel_save_path = theme_save_path / "steering_wheels" / f"{BOGPILOT_THEME}.png"
  steering_wheel_save_path.parent.mkdir(parents=True, exist_ok=True)
  shutil.copy2(BOGPILOT_THEME_PATH / "steering_wheel/wheel.png", steering_wheel_save_path)


class TestBogPilotTheme(unittest.TestCase):
  def test_bogpilot_theme_assets_exist(self):
    for component in BOGPILOT_THEME_COMPONENTS:
      self.assertTrue((BOGPILOT_THEME_PATH / component).is_dir(), component)
    self.assertTrue((BOGPILOT_THEME_PATH / "steering_wheel" / "wheel.png").is_file())
    self.assertTrue((BOGPILOT_THEME_PATH / "colors" / "colors.json").is_file())

  def test_copy_bogpilot_theme_installs_pack(self):
    with tempfile.TemporaryDirectory() as tmp:
      save_path = Path(tmp)
      copy_bogpilot_theme(save_path)

      pack = save_path / "theme_packs" / BOGPILOT_THEME
      for component in BOGPILOT_THEME_COMPONENTS:
        self.assertTrue((pack / component).is_dir(), component)
      self.assertTrue((save_path / "steering_wheels" / f"{BOGPILOT_THEME}.png").is_file())

  def test_bogpilot_theme_is_real_copies_of_stock(self):
    for path in BOGPILOT_THEME_PATH.rglob("*"):
      self.assertFalse(path.is_symlink(), path)
    stock_colors = json.loads((STOCK_THEME_PATH / "colors" / "colors.json").read_text())
    self.assertEqual(json.loads((BOGPILOT_THEME_PATH / "colors" / "colors.json").read_text()), stock_colors)
    for name in ("button_flag.png", "button_home.png", "button_settings.png"):
      self.assertEqual((BOGPILOT_THEME_PATH / "icons" / name).read_bytes(), (STOCK_THEME_PATH / "icons" / name).read_bytes())
    stock_sounds = sorted(f.name for f in (STOCK_THEME_PATH / "sounds").glob("*.wav"))
    self.assertEqual(sorted(f.name for f in (BOGPILOT_THEME_PATH / "sounds").iterdir()), stock_sounds)
    for name in stock_sounds:
      self.assertEqual((BOGPILOT_THEME_PATH / "sounds" / name).read_bytes(), (STOCK_THEME_PATH / "sounds" / name).read_bytes())
    self.assertEqual((BOGPILOT_THEME_PATH / "steering_wheel" / "wheel.png").read_bytes(),
                     (STOCK_THEME_PATH / "steering_wheel" / "wheel.png").read_bytes())
    # no turn-signal frames: the UI finds no images and draws no signal animation (same as "None")
    self.assertEqual([f for f in (BOGPILOT_THEME_PATH / "signals").iterdir() if not f.name.startswith(".")], [])
    self.assertEqual(sorted(f.name for f in (BOGPILOT_THEME_PATH / "distance_icons").iterdir()),
                     ["aggressive.png", "relaxed.png", "standard.png", "traffic.png"])

  def test_resolve_bogpilot_theme_passes_assets_through(self):
    self.assertEqual(set(BOGPILOT_STOCK_ALIAS_FILES), {"color_scheme", "wheel_image"})
    for name in ("icon_pack", "sound_pack", "signal_icons", "distance_icons"):
      self.assertEqual(resolve_bogpilot_theme(name, BOGPILOT_THEME), BOGPILOT_THEME)
    # colors / wheel: "stock" only while the BogPilot file is still identical to stock
    for name in ("color_scheme", "wheel_image"):
      self.assertEqual(resolve_bogpilot_theme(name, BOGPILOT_THEME), "stock")
      self.assertEqual(resolve_bogpilot_theme(name, "bogpilot"), "stock")
      self.assertEqual(resolve_bogpilot_theme(name, "frog"), "frog")
      self.assertEqual(resolve_bogpilot_theme(name, "stock"), "stock")
      self.assertIsNone(resolve_bogpilot_theme(name, None))

  def test_edited_bogpilot_assets_show_through(self):
    with tempfile.TemporaryDirectory() as tmp:
      edited = Path(tmp) / "bogpilot_theme"
      shutil.copytree(BOGPILOT_THEME_PATH, edited)
      colors = json.loads((edited / "colors" / "colors.json").read_text())
      colors["Path"]["red"] = 1
      (edited / "colors" / "colors.json").write_text(json.dumps(colors))
      (edited / "steering_wheel" / "wheel.png").write_bytes(b"edited")
      for name in ("color_scheme", "wheel_image"):
        self.assertEqual(resolve_bogpilot_theme(name, BOGPILOT_THEME, bogpilot_path=edited), BOGPILOT_THEME)

  def test_installed_pack_has_stock_files(self):
    with tempfile.TemporaryDirectory() as tmp:
      save_path = Path(tmp)
      copy_bogpilot_theme(save_path)
      pack = save_path / "theme_packs" / BOGPILOT_THEME
      self.assertFalse(any(path.is_symlink() for path in pack.rglob("*")))
      self.assertTrue((pack / "icons" / "button_home.png").is_file())
      self.assertFalse((pack / "icons" / "button_home.gif").exists())
      self.assertEqual((pack / "sounds" / "engage.wav").read_bytes(), (STOCK_THEME_PATH / "sounds" / "engage.wav").read_bytes())
      self.assertEqual((save_path / "steering_wheels" / f"{BOGPILOT_THEME}.png").read_bytes(),
                       (STOCK_THEME_PATH / "steering_wheel" / "wheel.png").read_bytes())

  def test_migrate_theme_params_moves_frogpilot_defaults_once(self):
    with tempfile.TemporaryDirectory() as tmp:
      marker = Path(tmp) / ".bogpilot_theme_migrated"
      store = FakeParams(dict(FROGPILOT_THEME_DEFAULTS))

      self.assertTrue(migrate_theme_params(store, marker_path=marker))
      for key in FROGPILOT_THEME_DEFAULTS:
        self.assertEqual(store.values[key], BOGPILOT_THEME)
      self.assertTrue(marker.exists())

      # Going back to FrogPilot after the one-time migration must stick
      store.put("CustomColors", "frog")
      self.assertFalse(migrate_theme_params(store, marker_path=marker))
      self.assertEqual(store.values["CustomColors"], "frog")

  def test_migrate_theme_params_leaves_custom_theme_alone(self):
    with tempfile.TemporaryDirectory() as tmp:
      marker = Path(tmp) / ".bogpilot_theme_migrated"
      store = FakeParams({
        "CustomColors": "cyberpunk",
        "CustomDistanceIcons": "stock",
        "CustomIcons": "frog-animated",
        "CustomSignals": "frog",
        "CustomSounds": "frog",
        "WheelIcon": "frog",
      })
      migrate_theme_params(store, marker_path=marker)
      self.assertEqual(store.values["CustomColors"], "cyberpunk")
      self.assertEqual(store.values["CustomIcons"], BOGPILOT_THEME)

  def test_migrate_theme_params_normalizes_lowercase_bogpilot(self):
    with tempfile.TemporaryDirectory() as tmp:
      marker = Path(tmp) / ".bogpilot_theme_migrated"
      marker.write_text("1\n")
      store = FakeParams({"CustomColors": "bogpilot"})
      migrate_theme_params(store, marker_path=marker)
      self.assertEqual(store.values["CustomColors"], BOGPILOT_THEME)


if __name__ == "__main__":
  unittest.main()
