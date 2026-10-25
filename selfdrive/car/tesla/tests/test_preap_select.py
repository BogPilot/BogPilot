"""get_car() glue for AP1 / Pre-AP selection (preap_select.py)."""
import json
from types import SimpleNamespace

from openpilot.selfdrive.car.tesla import preap_select as ps
from openpilot.selfdrive.car.tesla.preap_profile import AP1_PLATFORM, CHASSIS_ADDRS, PREAP_PLATFORM, ApEcuObserver

KNOWN = {"TESLA_AP1_MODELS": 1, "TESLA_PREAP_MODELS": 1, "HONDA_CIVIC": 1}
MS = 10_000_000


def _batch(t_ns, addrs, src=0):
  return SimpleNamespace(logMonoTime=t_ns, can=[SimpleNamespace(address=a, src=src) for a in addrs])


def _feed(obs, seconds, addrs=CHASSIS_ADDRS, src=0, start=0):
  for i in range(int(seconds * 100)):
    obs.update_can_msg(_batch(start + i * MS, addrs, src))
  return start + int(seconds * 100) * MS


def test_known_car_model():
  assert ps.known_car_model(b"TESLA_AP1_MODELS", KNOWN) == "TESLA_AP1_MODELS"
  assert ps.known_car_model("TESLA_AP1_MODELS", KNOWN) == "TESLA_AP1_MODELS"
  assert ps.known_car_model(b"TESLA_MODEL_S_PREAP", KNOWN) is None  # unknown name -> empty
  assert ps.known_car_model(None, KNOWN) is None


def test_configured_device_keeps_original_block(tmp_path):
  obs = ApEcuObserver()
  _feed(obs, 2.0)  # looks like Pre-AP on CAN, but CarModel wins
  assert ps.choose(None, False, None, "TESLA_AP1_MODELS", "", obs, tmp_path) is None
  assert ps.choose("HONDA_CIVIC", False, None, None, "", obs, tmp_path) is None
  assert ps.choose(None, False, None, None, "HONDA_CIVIC", obs, tmp_path) is None


def test_unconfigured_device_auto(tmp_path):
  obs = ApEcuObserver()
  _feed(obs, 2.0)
  assert ps.choose(None, False, None, None, "", obs, tmp_path) == (PREAP_PLATFORM, "can")
  obs = ApEcuObserver()
  _feed(obs, 0.5, CHASSIS_ADDRS + (0x488, 0x2B9))
  assert ps.choose(None, False, None, None, "", obs, tmp_path) == (AP1_PLATFORM, "can")


def test_device_frames_never_count(tmp_path):
  obs = ApEcuObserver()
  t = _feed(obs, 2.0)
  _feed(obs, 0.5, (0x488, 0x2B9), src=128, start=t)  # our own TX echoes
  assert ps.choose(None, False, None, None, "", obs, tmp_path) == (PREAP_PLATFORM, "can")


def test_override_and_nap_type(tmp_path):
  obs = ApEcuObserver()
  (tmp_path / "TeslaCarProfile").write_text("preap")
  assert ps.choose(None, False, None, "TESLA_AP1_MODELS", "", obs, tmp_path) == (PREAP_PLATFORM, "override")
  (tmp_path / "TeslaCarProfile").unlink()
  (tmp_path / "NAPImport.json").write_text(json.dumps({"NAPCarType": 1}))
  assert ps.choose(None, False, None, None, "", obs, tmp_path) == (PREAP_PLATFORM, "nap")
  assert ps.choose(None, False, None, "TESLA_AP1_MODELS", "", obs, tmp_path) is None  # CarModel beats NAP


def test_nap_auto_still_observes(tmp_path):
  obs = ApEcuObserver()
  _feed(obs, 0.5)
  (tmp_path / "NAPImport.json").write_text(json.dumps({"NAPCarType": 0}))
  assert ps.needs_more_observation(None, False, None, "", obs, tmp_path)
  (tmp_path / "NAPImport.json").write_text(json.dumps({"NAPCarType": 2}))
  assert not ps.needs_more_observation(None, False, None, "", obs, tmp_path)


def test_needs_more_observation(tmp_path):
  obs = ApEcuObserver()
  assert not ps.needs_more_observation(None, False, None, "", obs, tmp_path)  # no Tesla traffic
  _feed(obs, 0.5)
  assert ps.needs_more_observation(None, False, None, "", obs, tmp_path)
  assert not ps.needs_more_observation("HONDA_CIVIC", False, None, "", obs, tmp_path)
  assert ps.needs_more_observation("HONDA_CIVIC", True, None, "", obs, tmp_path)
  assert not ps.needs_more_observation(None, False, "TESLA_AP1_MODELS", "", obs, tmp_path)
  assert not ps.needs_more_observation(None, False, None, "X", obs, tmp_path)
  assert not ps.needs_more_observation(None, False, None, "", None, tmp_path)
  (tmp_path / "TeslaCarProfile").write_text("ap1")
  assert not ps.needs_more_observation(None, False, None, "", obs, tmp_path)


def test_observe_more_stops_on_answer_and_on_limit():
  obs = ApEcuObserver()
  t = _feed(obs, 0.5)
  state = {"t": t}

  def nxt():
    state["t"] += MS
    return _batch(state["t"], CHASSIS_ADDRS)
  clock = iter(range(10_000)).__next__
  assert ps.observe_more(obs, nxt, clock=lambda: clock() * 0.001) == PREAP_PLATFORM

  obs = ApEcuObserver()
  _feed(obs, 0.5, CHASSIS_ADDRS + (0x488,))  # ambiguous: never answers
  ticks = iter(range(10_000)).__next__
  assert ps.observe_more(obs, nxt, clock=lambda: ticks() * 0.1, limit_s=1.0) is None


def test_write_status(tmp_path):
  (tmp_path / "PreAPPedalEnabled").write_text("1")
  st = ps.write_status(PREAP_PLATFORM, "can", {0: {0x552: 6}, 2: {}}, tmp_path)
  on_disk = json.loads((tmp_path / "TeslaProfileStatus").read_text())
  assert on_disk == st
  assert set(st) == {"profile", "source", "reason", "pedal", "radar"}
  assert st["profile"] == "preap" and st["source"] == "auto"
  assert "bus 0 (learned)" in st["pedal"]
  st = ps.write_status(AP1_PLATFORM, "existing", {}, tmp_path)
  assert st["profile"] == "ap1" and st["source"] == "carmodel" and st["pedal"] == "n/a"


def test_write_status_skips_non_tesla(tmp_path):
  assert ps.write_status("HONDA_CIVIC", "existing", {}, tmp_path) is None
  assert ps.write_status("MOCK", "existing", {}, tmp_path) is None
  assert not (tmp_path / "TeslaProfileStatus").exists()
