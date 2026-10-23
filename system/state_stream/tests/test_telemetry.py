"""StateStream telemetry: 60 s summary, no-subscriber skip, model-rate meta, lead track handoffs, Wi-Fi parse."""
import json
import socket
import time
from types import SimpleNamespace as NS

import pytest

from openpilot.system.state_stream import payload
from openpilot.system.state_stream import telemetry as tel
from openpilot.system.state_stream.streamer import StateStreamer
from openpilot.system.state_stream.tests.test_state_stream import SM, SlowPub, fake_sm

WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
  wlan0: 0000   58.  -52.  -256        0      0      0      5      0        3
"""


class CountPub(SlowPub):
  """Fake publisher that reports a subscriber count (like ClientTracker does for real zmq)."""
  def __init__(self, clients):
    super().__init__(0.0)
    self.clients = clients


class Wifi:
  def __init__(self):
    self.n = 0

  def sample(self):
    self.n += 1

  def window(self):
    return {"samples": self.n, "level": -52}


def _wait(cond, t=2.0):
  end = time.monotonic() + t
  while time.monotonic() < end:
    if cond():
      return True
    time.sleep(0.01)
  return False


def _mk(tmp_path, pub, **kw):
  (tmp_path / "StateStream").write_text("1")
  s = StateStreamer(cfg_dir=tmp_path, publisher_factory=lambda cfg: pub, **kw)
  assert _wait(lambda: s.active)
  return s


def _sm_updated():
  sm = SM(fake_sm())
  sm.updated = {"modelV2": True}
  return sm


def test_parse_proc_wireless():
  w = tel.parse_proc_wireless(WIRELESS, "wlan0")
  assert w == {"link": 58, "level": -52, "noise": None, "discardRetry": 5, "missedBeacon": 3}
  assert tel.parse_proc_wireless(WIRELESS, "wlan1") is None
  assert tel.parse_proc_wireless("", "wlan0") is None
  assert tel.parse_proc_wireless(WIRELESS.replace("-52.", "204."), "wlan0")["level"] == -52  # unsigned dBm


def test_wifi_sampler_files(tmp_path):
  proc = tmp_path / "wireless"
  proc.write_text(WIRELESS)
  st = tmp_path / "net/wlan0/statistics"
  st.mkdir(parents=True)
  for k, v in (("tx_errors", 1), ("tx_dropped", 2), ("rx_dropped", 3), ("tx_bytes", 1000)):
    (st / k).write_text(str(v))
  w = tel.WifiSampler("wlan0", proc=proc, sys_net=tmp_path / "net")
  w.window()  # baseline for deltas
  w.sample()
  w.sample()
  (st / "tx_dropped").write_text("7")
  out = w.window()
  assert out["samples"] == 2 and out["levelMin"] == -52 and out["linkMean"] == 58 and out["d_tx_dropped"] == 5
  assert tel.WifiSampler("wlan0", proc=tmp_path / "missing", sys_net=tmp_path).window()["samples"] == 0


def test_gap_counter():
  g = tel.GapCounter(0.5)
  for t in (0.0, 0.05, 0.1, 0.8, 0.85, 2.0):
    g.mark(t)
  assert g.take() == (2, 1.15)
  g.reset_ref()
  g.mark(10.0)
  assert g.take() == (0, 0.0)


def test_summary_logged_every_period(tmp_path):
  events = []
  wifi = Wifi()
  s = _mk(tmp_path, CountPub(1), log_event=lambda name, **kw: events.append((name, kw)), wifi=wifi, summary_period=0.5)
  sm = _sm_updated()
  t_end = time.monotonic() + 1.3
  while time.monotonic() < t_end:
    s.tick(sm, True)
    time.sleep(0.01)
  time.sleep(0.2)
  s.stop()
  assert len(events) >= 2 and all(n == "state_stream_summary" for n, _ in events)
  ev = events[-1][1]
  for k in ("sent", "bytes", "dropped", "errors", "clients", "skippedNoClient", "rates", "encodeMsMean", "encodeMsMax",
            "hostCpuPct", "senderCpuPct", "gaps", "wifi", "leadSwitches", "routeSent", "totals"):
    assert k in ev, k
  assert ev["clients"] == 1 and ev["sent"] > 0 and ev["bytes"] > 0 and ev["rates"]["state"] > 5
  assert ev["rates"]["modelSrc"] > 20  # 100 Hz fake host loop, every tick 'updated'
  assert ev["wifi"]["level"] == -52
  blob = json.dumps(events)
  assert '"lat"' not in blob and '"lon"' not in blob and "pts" not in blob  # never positions


def test_summary_not_logged_when_disabled(tmp_path):
  events = []
  s = StateStreamer(cfg_dir=tmp_path, publisher_factory=lambda cfg: pytest.fail("must not bind"),
                    log_event=lambda name, **kw: events.append(name), wifi=Wifi(), summary_period=0.1)
  time.sleep(0.4)
  s.stop()
  assert events == []


def test_no_subscriber_skips_build_and_encode(tmp_path, monkeypatch):
  pub = CountPub(0)
  s = _mk(tmp_path, pub, wifi=Wifi())
  assert _wait(lambda: s.clients == 0)
  built = []
  real = payload.build
  monkeypatch.setattr(payload, "build", lambda *a, **k: built.append(1) or real(*a, **k))
  sm = _sm_updated()
  for _ in range(30):
    s.tick(sm, True)
    time.sleep(0.01)
  assert built == [] and s.queued == 0 and s.skipped == 30 and pub.msgs == []
  assert s.model_src == 30   # still measured while idle
  pub.clients = 1
  assert _wait(lambda: s.clients == 1)
  for _ in range(30):
    s.tick(sm, True)
    time.sleep(0.01)
  time.sleep(0.2)
  s.stop()
  assert built and len(pub.msgs) > 0


def test_unknown_client_count_keeps_sending(tmp_path):
  pub = SlowPub(0.0)   # no .clients, no .sock: count unknown -> behave like before
  s = _mk(tmp_path, pub, wifi=Wifi())
  for _ in range(20):
    s.tick(SM(fake_sm()), True)
    time.sleep(0.01)
  time.sleep(0.2)
  s.stop()
  assert s.clients is None and len(pub.msgs) > 0


def test_model_rate_meta(tmp_path):
  pub = CountPub(1)
  s = _mk(tmp_path, pub, wifi=Wifi())
  s._model_src_hz = 19.8   # as measured by _update_model_src_hz
  sm = _sm_updated()
  t_end = time.monotonic() + 0.6
  while time.monotonic() < t_end:
    s.tick(sm, True)
    time.sleep(0.01)
  time.sleep(0.2)
  s.stop()
  metas = [m["meta"] for _, m in pub.msgs]
  assert metas and all(m == {"hz": 20.0, "modelHz": 10.0, "modelSrcHz": 19.8} for m in metas)
  # model section only on ~half of the snapshots, but meta tells the client why
  nm = sum("model" in m for _, m in pub.msgs)
  assert 0.3 * len(pub.msgs) <= nm <= 0.7 * len(pub.msgs)


def test_update_model_src_hz():
  s = StateStreamer.__new__(StateStreamer)
  s.model_src, s._msrc_n0, s._msrc_t0, s._model_src_hz = 0, 0, None, None
  s._update_model_src_hz(100.0)
  s.model_src = 99
  s._update_model_src_hz(105.0)
  assert s._model_src_hz == 19.8


def _lead(tid, status=True, radar=True, d=20.0):
  return NS(status=status, dRel=d, yRel=0.0, vRel=0.0, aRel=0.0, vLead=10.0, modelProb=0.0, radar=radar, radarTrackId=tid)


def test_lead_track_id_and_handoff():
  lt = payload.LeadTracker()
  seq = [_lead(5), _lead(5), _lead(7), _lead(7), _lead(5), _lead(-1, radar=False), _lead(0, status=False), _lead(9)]
  out = [payload._lead(ld, lt.update("leadOne", ld)) for ld in seq]
  assert [o[8] for o in out] == [5, 5, 7, 7, 5, -1, 0, 9]
  assert [o[9] for o in out] == [False, False, True, False, True, True, False, False]  # absent->9 = acquisition
  assert lt.switches == 3
  lt.reset()
  assert lt.update("leadOne", _lead(1)) is False
  # older radarState without the field -> -1, never raises
  old = NS(status=True, dRel=1.0, yRel=0.0, vRel=0.0, aRel=0.0, vLead=0.0, modelProb=0.0, radar=True)
  assert payload._lead(old)[8:] == [-1, False]


def test_streamed_leads_carry_track_id(tmp_path):
  pub = CountPub(1)
  s = _mk(tmp_path, pub, wifi=Wifi())
  sm = _sm_updated()
  rs = sm.d["radarState"]
  for i in range(40):
    rs.leadOne = _lead(3 if (i // 4) % 2 == 0 else 4)
    s.tick(sm, True)
    time.sleep(0.03)
  time.sleep(0.2)
  s.stop()
  leads = [m["leads"]["leadOne"] for _, m in pub.msgs]
  assert {ld[8] for ld in leads} == {3, 4}
  assert any(ld[9] for ld in leads) and s.leads.switches >= 3


def test_client_tracker_counts_real_zmq_subscribers():
  zmq = pytest.importorskip("zmq")
  from openpilot.system.hevc_stream.hevc_streamd import Publisher
  with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    port = so.getsockname()[1]
  ctx = zmq.Context()
  pub = Publisher("127.0.0.1", port, token="t0k", ctx=ctx)
  ct = tel.ClientTracker(pub)
  assert ct.mon is not None and ct.count == 0

  def sub(tok):
    sk = ctx.socket(zmq.SUB)
    sk.setsockopt(zmq.LINGER, 0)
    sk.setsockopt(zmq.PLAIN_USERNAME, b"bogpilot")
    sk.setsockopt(zmq.PLAIN_PASSWORD, tok)
    sk.setsockopt(zmq.SUBSCRIBE, b"state")
    sk.connect(f"tcp://127.0.0.1:{port}")
    return sk

  def until(cond):
    return _wait(lambda: (joined.append(ct.poll()), cond())[1], 3.0)

  joined = []
  a = sub(b"t0k")
  assert until(lambda: ct.count == 1 and ct.auth_ok == 1) and any(joined)
  bad = sub(b"wrong")
  assert until(lambda: ct.rejected >= 1)
  bad.close(0)
  assert until(lambda: ct.count == 1)       # rejected peer is dropped by ZAP, not counted
  b = sub(b"t0k")
  assert until(lambda: ct.count == 2 and ct.auth_ok == 2)
  a.close(0)
  assert until(lambda: ct.count == 1)
  b.close(0)
  assert until(lambda: ct.count == 0)
  ct.close()
  pub.close()
  ctx.term()


def test_route_resend_on_new_subscriber():
  from openpilot.system.state_stream import route as sroute
  rs = sroute.RouteSchedule()
  rs.set_route([(0.0, 0.001 * k) for k in range(100)], 0.0)
  first = rs.due(0.0, 99)
  assert first and rs.due(1.0, 99) == []
  rs.resend_now()
  assert rs.due(1.0, 99) == first
