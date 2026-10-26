"""BogPilot: build job count and thermal pause (common/bogpilot_build_guard.py)."""
import importlib.util
import os
import signal
import tempfile
import time

_P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bogpilot_build_guard.py")
_spec = importlib.util.spec_from_file_location("bogpilot_build_guard", _P)
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


def _w(path, data):
  with open(path, "w") as f:
    f.write(data)


def _r(path):
  with open(path) as f:
    return f.read()


class TestJobs:
  def test_device_default_4(self):
    assert g.build_jobs(8, True, {}) == 4

  def test_pc_uses_all(self):
    assert g.build_jobs(16, False, {}) == 16

  def test_env_override_and_cap(self):
    assert g.build_jobs(8, True, {"BOGPILOT_BUILD_JOBS": "2"}) == 2
    assert g.build_jobs(8, True, {"BOGPILOT_BUILD_JOBS": "64"}) == 8
    assert g.build_jobs(None, True, {"BOGPILOT_BUILD_JOBS": "junk"}) == 2
    assert g.build_jobs(1, True, {}) == 1


class TestTemp:
  def test_cpu_usr_zones_preferred(self):
    zones = [("lmh-dcvs-00", 75.0), ("cpu0-silver-usr", 61.2), ("cpu3-gold-usr", 70.5), ("gpu0-usr", 99.0),
             ("cpu0-silver-step", 80.0)]
    assert g.cpu_temp_c(zones) == 70.5

  def test_fallback_and_bogus(self):
    assert g.cpu_temp_c([("cpu-thermal", 55.0), ("cpu-bad", 500.0)]) == 55.0
    assert g.cpu_temp_c([("gpu", 50.0)]) is None
    assert g.cpu_temp_c([]) is None

  def test_read_zones(self):
    with tempfile.TemporaryDirectory() as d:
      for i, (n, t) in enumerate((("cpu0-silver-usr", "48100"), ("vbat", ""), ("cpu3-gold-usr", "52300"))):
        z = os.path.join(d, f"thermal_zone{i}")
        os.makedirs(z)
        _w(os.path.join(z, "type"), n + "\n")
        _w(os.path.join(z, "temp"), t + "\n")
      zones = g.read_zones(os.path.join(d, "thermal_zone*"))
      assert zones == [("cpu0-silver-usr", 48.1), ("cpu3-gold-usr", 52.3)]
      assert g.cpu_temp_c(zones) == 52.3


class TestGovernor:
  def test_hysteresis(self):
    gov = g.ThermalGovernor(90, 75, 300)
    assert gov.step(85, 0) is None
    assert gov.step(91, 1) == "pause"
    assert gov.step(80, 2) is None
    assert gov.step(75, 3) == "resume"
    assert gov.step(80, 4) is None

  def test_max_pause(self):
    gov = g.ThermalGovernor(90, 75, 300)
    gov.step(95, 0)
    assert gov.step(95, 299) is None
    assert gov.step(95, 300) == "resume"

  def test_no_reading_never_pauses(self):
    gov = g.ThermalGovernor(90, 75, 300)
    assert gov.step(None, 0) is None
    gov.step(95, 1)
    assert gov.step(None, 2) == "resume"

  def test_from_env(self):
    gov = g.ThermalGovernor.from_env({"BOGPILOT_BUILD_TEMP_PAUSE": "80", "BOGPILOT_BUILD_TEMP_RESUME": "85"})
    assert (gov.pause_c, gov.resume_c) == (80.0, 75.0)


class TestPauser:
  def test_pause_resume_signals_and_status(self):
    temps = iter([95.0, 95.0, 70.0] + [70.0] * 100)
    sent, status = [], []
    p = g.ThermalPauser(1234, status=status.append, gov=g.ThermalGovernor(90, 75, 300), read_temp=lambda: next(temps),
                        interval=0.01, kill=lambda pg, sig: sent.append((pg, sig)))
    p.start()
    time.sleep(0.3)
    p.stop()
    assert sent[0] == (1234, signal.SIGSTOP)
    assert sent[1] == (1234, signal.SIGCONT)
    assert sent[-1] == (1234, signal.SIGCONT)  # stop() always resumes
    assert status[0].startswith("Cooling down (95 C)")
    assert status[1] is None

  def test_real_process_group(self):
    import subprocess
    proc = subprocess.Popen(["sleep", "5"], start_new_session=True)
    try:
      p = g.ThermalPauser(proc.pid, gov=g.ThermalGovernor(90, 75, 300), read_temp=lambda: 99.0, interval=0.01).start()
      time.sleep(0.2)
      with open(f"/proc/{proc.pid}/stat") as f:
        assert f.read().split(") ")[1][0] == "T"  # stopped
      p.stop()
      time.sleep(0.05)
      with open(f"/proc/{proc.pid}/stat") as f:
        assert f.read().split(") ")[1][0] in "SR"
    finally:
      proc.kill()
      proc.wait()
