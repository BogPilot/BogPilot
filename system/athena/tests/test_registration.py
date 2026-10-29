from pathlib import Path

from openpilot.common.params import Params
from openpilot.system.athena.registration import register, is_registered_device, UNREGISTERED_DONGLE_ID
from openpilot.system.hardware import HARDWARE
from openpilot.system.hardware.hw import Paths


class TestRegistration:
  def setup_method(self):
    self.params = Params()
    persist_dir = Path(Paths.persist_root()) / "comma"
    persist_dir.mkdir(parents=True, exist_ok=True)
    self.dongle_id = persist_dir / "dongle_id"

  def test_valid_cache_params(self):
    dongle = "DONGLE_ID_123"
    self.params.put("DongleId", dongle)
    assert register() == dongle
    assert is_registered_device()

  def test_valid_cache_persist(self):
    self.params.remove("DongleId")
    dongle = "PERSIST_DONGLE"
    with open(self.dongle_id, "w") as f:
      f.write(dongle)
    assert register() == dongle
    assert self.params.get("DongleId", encoding="utf8") == dongle

  def test_offline_mint(self):
    self.params.remove("DongleId")
    if self.dongle_id.is_file():
      self.dongle_id.unlink()
    dongle = register()
    assert dongle is not None
    assert dongle.startswith("offline-")
    serial = HARDWARE.get_serial() or "unknown"
    assert dongle == f"offline-{serial}"
    assert self.params.get("DongleId", encoding="utf8") == dongle
    assert register() == dongle

  def test_unregistered_reminted(self):
    self.params.put("DongleId", UNREGISTERED_DONGLE_ID)
    if self.dongle_id.is_file():
      self.dongle_id.unlink()
    dongle = register()
    assert dongle.startswith("offline-")
