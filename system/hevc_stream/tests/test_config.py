from pathlib import Path

from openpilot.system.hevc_stream import config as c


def w(d: Path, k, v):
  (d / k).write_text(v)


def test_defaults_on_absent(tmp_path):
  cfg = c.load(tmp_path)
  assert cfg.enabled and cfg.services == c.DEFAULT_SERVICES
  assert cfg.iface == "wlan0" and cfg.port == c.DEFAULT_PORT and cfg.allow == () and cfg.token is None
  assert "driverEncodeData" not in cfg.services and "modelV2" not in cfg.services


def test_off_values(tmp_path):
  for v in ("0", "false", "OFF", " no\n"):
    w(tmp_path, "HevcStream", v)
    assert not c.load(tmp_path).enabled
    assert not c.should_run(True, tmp_path)
  for v in ("1", "on", "garbage"):
    w(tmp_path, "HevcStream", v)
    assert c.load(tmp_path).enabled


def test_onroad_only(tmp_path):
  assert c.should_run(True, tmp_path) and not c.should_run(False, tmp_path)


def test_services_filtering(tmp_path):
  w(tmp_path, "HevcStreamServices", "wideRoadEncodeData, carState,bogus,carState,modelV2")
  cfg = c.load(tmp_path)
  assert cfg.services == ("wideRoadEncodeData", "carState", "modelV2")
  assert cfg.encode_services == ("wideRoadEncodeData",) and cfg.state_services == ("carState", "modelV2")
  w(tmp_path, "HevcStreamServices", "bogus")
  assert c.load(tmp_path).services == c.DEFAULT_SERVICES


def test_driver_cam_refused_unless_opted_in(tmp_path):
  w(tmp_path, "HevcStreamServices", "driverEncodeData,wideRoadEncodeData")
  assert c.load(tmp_path).services == ("wideRoadEncodeData",)
  w(tmp_path, "HevcStreamDriver", "1")
  assert "driverEncodeData" in c.load(tmp_path).services


def test_port_allow_token(tmp_path):
  w(tmp_path, "HevcStreamPort", "80")
  assert c.load(tmp_path).port == c.DEFAULT_PORT
  w(tmp_path, "HevcStreamPort", "9000")
  w(tmp_path, "HevcStreamAllow", "192.168.8.10, nope 10.0.0.2")
  w(tmp_path, "HevcStreamToken", "s3cret\n")
  cfg = c.load(tmp_path)
  assert cfg.port == 9000 and cfg.allow == ("192.168.8.10", "10.0.0.2") and cfg.token == "s3cret"


def test_safe_bind_ip():
  assert c.safe_bind_ip("192.168.8.20") == "192.168.8.20"
  assert c.safe_bind_ip("10.1.2.3") and c.safe_bind_ip("127.0.0.1")
  for bad in ("0.0.0.0", "8.8.8.8", "", None, "wlan0", "::"):
    assert c.safe_bind_ip(bad) is None


def test_cores_not_driving():
  assert set(c.CORES) <= {0, 1, 2} and c.NICE > 0


def test_publisher_refuses_public_bind():
  import pytest
  from openpilot.system.hevc_stream.hevc_streamd import Publisher
  for bad in ("0.0.0.0", "1.1.1.1"):
    with pytest.raises(ValueError):
      Publisher(bad, 18765)


def test_process_registered():
  src = Path(__file__).resolve().parents[3] / "system/manager/process_config.py"
  s = src.read_text()
  assert '"hevc_streamd", "system.hevc_stream.hevc_streamd", hevc_stream' in s


def test_default_has_no_state_readers():
  # Route 5b: extra msgq readers on state topics evicted driving readers (commIssue).
  assert c.DEFAULT_SERVICES == ("wideRoadEncodeData", "roadEncodeData")
  assert not set(c.DEFAULT_SERVICES) & set(c.STATE_SERVICES)
