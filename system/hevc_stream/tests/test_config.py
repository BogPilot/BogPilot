from pathlib import Path

import pytest

from openpilot.system.hevc_stream import config as c


def w(d: Path, k, v):
  (d / k).write_text(v)


def auth(d: Path):
  w(d, "StateStreamToken", "test-token\n")
  w(d, "StateStreamAllow", "192.168.8.10")


def test_missing_files_mean_off(tmp_path):
  cfg = c.load(tmp_path)
  assert not cfg.enabled and cfg.services == ("roadEncodeData",)
  assert cfg.iface == "wlan0" and cfg.port == c.DEFAULT_PORT and cfg.allow == () and cfg.token is None
  assert not c.should_run(True, tmp_path)


def test_missing_video_stream_is_off_even_with_auth(tmp_path):
  auth(tmp_path)
  assert not c.load(tmp_path).enabled and not c.should_run(True, tmp_path)


def test_missing_legacy_hevc_stream_is_off(tmp_path):
  auth(tmp_path)
  w(tmp_path, "VideoStream", "0")
  assert not c.load(tmp_path).enabled  # no HevcStream file: off (was ON before this branch)


def test_only_explicit_on_values_enable(tmp_path):
  auth(tmp_path)
  for name in ("VideoStream", "HevcStream"):
    for v in ("0", "false", "OFF", " no\n", "", "garbage", "2"):
      w(tmp_path, name, v)
      assert not c.load(tmp_path).enabled, (name, v)
    for v in ("1", "on", "TRUE", " yes\n"):
      w(tmp_path, name, v)
      assert c.load(tmp_path).enabled and c.should_run(True, tmp_path), (name, v)
    w(tmp_path, name, "0")


def test_onroad_only(tmp_path):
  auth(tmp_path)
  w(tmp_path, "VideoStream", "1")
  assert c.should_run(True, tmp_path) and not c.should_run(False, tmp_path)


@pytest.mark.parametrize("missing", ["token", "allow", "both"])
def test_refuses_without_token_or_allowlist(tmp_path, missing):
  w(tmp_path, "VideoStream", "1")
  if missing not in ("token", "both"):
    w(tmp_path, "StateStreamToken", "test-token")
  if missing not in ("allow", "both"):
    w(tmp_path, "StateStreamAllow", "192.168.8.10")
  cfg = c.load(tmp_path)
  assert cfg.enabled and not cfg.auth_ok and cfg.refusal() is not None
  assert not c.should_run(True, tmp_path)


def test_allowlist_of_only_garbage_is_refused(tmp_path):
  w(tmp_path, "VideoStream", "1")
  w(tmp_path, "StateStreamToken", "test-token")
  w(tmp_path, "StateStreamAllow", "not-an-ip, 999.1.1.1")
  assert c.load(tmp_path).refusal() is not None


def test_state_stream_auth_first_then_legacy_fallback(tmp_path):
  w(tmp_path, "HevcStreamToken", "legacy")
  w(tmp_path, "HevcStreamAllow", "10.0.0.2")
  cfg = c.load(tmp_path)
  assert cfg.token == "legacy" and cfg.allow == ("10.0.0.2",)
  auth(tmp_path)
  cfg = c.load(tmp_path)
  assert cfg.token == "test-token" and cfg.allow == ("192.168.8.10",)


def test_camera_toggles(tmp_path):
  assert c.load(tmp_path).services == ("roadEncodeData",)
  w(tmp_path, "VideoStreamWide", "1")
  assert c.load(tmp_path).services == ("roadEncodeData", "wideRoadEncodeData")
  w(tmp_path, "VideoStreamQcam", "1")
  assert c.load(tmp_path).services == ("qRoadEncodeData", "wideRoadEncodeData")
  w(tmp_path, "VideoStreamWide", "0")
  assert c.load(tmp_path).services == ("qRoadEncodeData",)


def test_never_streams_driver_or_state_topics(tmp_path):
  for wide in ("0", "1"):
    for q in ("0", "1"):
      w(tmp_path, "VideoStreamWide", wide)
      w(tmp_path, "VideoStreamQcam", q)
      s = set(c.load(tmp_path).services)
      assert "driverEncodeData" not in s and not s & set(c.STATE_SERVICES)
      assert s <= set(c.ENCODE_SERVICES)


def test_port(tmp_path):
  w(tmp_path, "HevcStreamPort", "80")
  assert c.load(tmp_path).port == c.DEFAULT_PORT
  w(tmp_path, "HevcStreamPort", "9000")
  assert c.load(tmp_path).port == 9000


def test_safe_bind_ip():
  assert c.safe_bind_ip("192.168.8.20") == "192.168.8.20"
  assert c.safe_bind_ip("10.1.2.3") and c.safe_bind_ip("127.0.0.1")
  for bad in ("0.0.0.0", "8.8.8.8", "", None, "wlan0", "::"):
    assert c.safe_bind_ip(bad) is None


def test_lowest_priority_settings():
  assert set(c.CORES) <= {0, 1, 2} and c.NICE == 19 and c.SNDHWM <= 4 and c.MAX_BURST <= 2


def test_publisher_refuses_public_bind():
  from openpilot.system.hevc_stream.hevc_streamd import Publisher
  for bad in ("0.0.0.0", "1.1.1.1"):
    with pytest.raises(ValueError):
      Publisher(bad, 18765)


@pytest.mark.parametrize("token,allow", [(None, ("127.0.0.1",)), ("t", ()), (None, ())])
def test_publisher_require_auth_refuses(token, allow):
  from openpilot.system.hevc_stream.hevc_streamd import Publisher
  with pytest.raises(ValueError):
    Publisher("127.0.0.1", 18770, allow=allow, token=token, require_auth=True)


def test_main_refuses_without_auth(tmp_path, monkeypatch, capsys):
  from openpilot.system.hevc_stream import hevc_streamd
  w(tmp_path, "VideoStream", "1")
  monkeypatch.setattr(c, "PARAMS_DIR", tmp_path)
  monkeypatch.setattr(hevc_streamd, "Publisher", lambda *a, **k: pytest.fail("must not bind"))
  monkeypatch.setitem(__import__("sys").modules, "cereal.messaging", type(__import__("sys"))("stub"))
  hevc_streamd.main()  # returns without binding
  assert "not starting" in capsys.readouterr().err


def test_process_registered():
  src = Path(__file__).resolve().parents[3] / "system/manager/process_config.py"
  s = src.read_text()
  assert '"hevc_streamd", "system.hevc_stream.hevc_streamd", hevc_stream' in s
