"""BogPilot: launch-time build hardening (bogpilot_build.sh, bogpilot_qt_eglfs.sh, launch_chffrplus.sh). PC only."""
import os
import re
import subprocess
import tempfile

TICI = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ROOT = os.path.abspath(os.path.join(TICI, "..", "..", ".."))
BUILD_SH = os.path.join(TICI, "bogpilot_build.sh")
QT_SH = os.path.join(TICI, "bogpilot_qt_eglfs.sh")
LAUNCH = os.path.join(ROOT, "launch_chffrplus.sh")


def _w(path, data):
  with open(path, "w") as f:
    f.write(data)


def _r(path):
  with open(path) as f:
    return f.read()


def sh(script, **env):
  e = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
  e.update({k: str(v) for k, v in env.items()})
  return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e, timeout=60)


class TestBuildSh:
  def setup_method(self):
    self.d = tempfile.TemporaryDirectory()
    self.cnt = os.path.join(self.d.name, "count")
    self.marker = os.path.join(self.d.name, "built")
    self.env = {"BOGPILOT_BUILD_FAIL_COUNT": self.cnt, "BOGPILOT_BUILT_MARKER": self.marker,  "BOGPILOT_REBOOT_CMD": "echo FAKE_REBOOT",
                "BOGPILOT_HOLD_RETURN": "1", "BOGPILOT_HOLD_GRACE": "0"}

  def teardown_method(self):
    self.d.cleanup()

  def run_build(self, rc):
    fake = f'bash -c \'echo "MIN=$BOGPILOT_ERROR_REBOOT_MIN JOBS=$BOGPILOT_BUILD_JOBS NICE=$(nice)"; exit {rc}\''
    return sh(f'source "{BUILD_SH}"; bogpilot_run_build {fake}; echo "RC=$? LAST=$BOGPILOT_LAST_FAIL_MIN"', **self.env)

  def test_success_resets_count(self):
    _w(self.cnt, "2\n")
    r = self.run_build(0)
    assert "MIN=10 JOBS=4 NICE=19" in r.stdout
    assert "RC=0" in r.stdout
    assert not os.path.exists(self.cnt)

  def test_marker_skips_network_wait(self):
    repo = os.path.join(self.d.name, "repo")
    git = ["git", "-C", repo, "-c", "user.name=BogPilot", "-c", "user.email=bogpilot@users.noreply.github.com"]
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "t"], check=True)
    self.env["BOGPILOT_SRC_DIR"] = repo
    check = f'source "{BUILD_SH}"; bogpilot_build_is_current "{repo}" && echo CURRENT || echo STALE'
    assert "STALE" in sh(check, **self.env).stdout
    self.run_build(1)
    assert not os.path.exists(self.marker)
    self.run_build(0)
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert _r(self.marker).strip() == head
    assert "CURRENT" in sh(check, **self.env).stdout
    _w(self.marker, "0" * 40 + "\n")
    assert "STALE" in sh(check, **self.env).stdout

  def test_three_auto_reboots_then_stays(self):
    mins = []
    for i in range(5):
      r = self.run_build(1)
      assert re.search(r"RC=1 ", r.stdout)
      mins.append(re.search(r"MIN=(\d+)", r.stdout).group(1))
      assert _r(self.cnt).strip() == str(i + 1)
    assert mins == ["10", "10", "10", "0", "0"]

  def test_garbage_count_is_zero(self):
    _w(self.cnt, "x\n")
    assert "MIN=10" in self.run_build(1).stdout
    assert _r(self.cnt).strip() == "1"

  def test_env_overrides(self):
    r = sh(f'source "{BUILD_SH}"; bogpilot_run_build bash -c "echo MIN=\\$BOGPILOT_ERROR_REBOOT_MIN JOBS=\\$BOGPILOT_BUILD_JOBS; exit 1"',
           BOGPILOT_FAIL_REBOOT_MIN=5, BOGPILOT_FAIL_MAX_REBOOTS=1, BOGPILOT_BUILD_JOBS=2, **self.env)
    assert "MIN=5 JOBS=2" in r.stdout
    r = sh(f'source "{BUILD_SH}"; bogpilot_run_build bash -c "echo MIN=\\$BOGPILOT_ERROR_REBOOT_MIN; exit 1"',
           BOGPILOT_FAIL_MAX_REBOOTS=1, **self.env)
    assert "MIN=0" in r.stdout

  def test_hold_reboots_after_minutes(self):
    # 0 minutes = never reboot; sleep is faked so the test is instant
    r = sh(f'source "{BUILD_SH}"; sleep() {{ echo "SLEEP $1"; }}; bogpilot_fail_hold 10 "build failed"; echo "RET=$?"', **self.env)
    assert "SLEEP 600" in r.stdout
    assert "FAKE_REBOOT" in r.stdout
    r = sh(f'source "{BUILD_SH}"; sleep() {{ echo "SLEEP $1"; }}; bogpilot_fail_hold 0 "build failed"; echo "RET=$?"', **self.env)
    assert "FAKE_REBOOT" not in r.stdout
    assert "SLEEP" not in r.stdout
    assert "Not starting openpilot" in r.stdout

  def test_wait_network_bounded(self):
    with tempfile.TemporaryDirectory() as b:
      _w(os.path.join(b, "nm-online"), '#!/bin/bash\necho "nm-online $*" >&2\nexit 1\n')
      os.chmod(os.path.join(b, "nm-online"), 0o755)
      r = sh(f'export PATH="{b}:$PATH"; source "{BUILD_SH}"; bogpilot_wait_network; echo "RC=$?"', **self.env)
      assert "RC=0" in r.stdout
      assert "no network after 60s" in r.stdout


class TestQtDeps:
  def test_retries_capped(self):
    script = "\n".join([
      f'source "{BUILD_SH}"; source "{QT_SH}"; bogpilot_qtdeps_ok() {{ return 1; }}',
      'bogpilot_qtdeps_fetch() { echo FETCH; return 1; }; sleep() { :; }',
      'bogpilot_qtdeps_ensure /nonexistent; echo "RC=$?"',
    ])
    r = sh(script, BOGPILOT_NO_SCREEN=1, BOGPILOT_QTDEPS_TRIES=4)
    assert r.stdout.count("FETCH") == 4
    assert "giving up" in r.stdout
    assert "RC=1" in r.stdout

  def test_success_returns_0(self):
    script = "\n".join([
      f'source "{BUILD_SH}"; source "{QT_SH}"; n=0; bogpilot_qtdeps_ok() {{ [ -f "$T/ok" ]; }}',
      'bogpilot_qtdeps_fetch() { touch "$T/ok"; }; bogpilot_qtdeps_ensure /nonexistent; echo "RC=$?"',
    ])
    with tempfile.TemporaryDirectory() as t:
      r = sh(script, BOGPILOT_NO_SCREEN=1, T=t)
    assert "RC=0" in r.stdout

  def test_failed_holds_with_budget(self):
    with tempfile.TemporaryDirectory() as t:
      cnt = os.path.join(t, "qcount")
      script = f'source "{BUILD_SH}"; source "{QT_SH}"; sleep() {{ echo "SLEEP $1"; }}; bogpilot_qtdeps_failed /x; echo "RC=$?"'
      env = dict(BOGPILOT_NO_SCREEN=1, BOGPILOT_QTDEPS_FAIL_COUNT=cnt, BOGPILOT_REBOOT_CMD="echo FAKE_REBOOT",
                 BOGPILOT_HOLD_RETURN=1, BOGPILOT_HOLD_GRACE=0)
      outs = [sh(script, **env).stdout for _ in range(4)]
      assert ([("FAKE_REBOOT" in o) for o in outs]) == [True, True, True, False]
      assert _r(cnt).strip() == "4"


class TestLaunchScript:
  def setup_method(self):
    self.src = _r(LAUNCH)

  def test_sources_build_helpers(self):
    assert 'source "$DIR/system/hardware/tici/bogpilot_build.sh"' in self.src

  def test_failed_build_never_reaches_manager(self):
    m = re.search(r"if ! bogpilot_run_build \./build\.py; then\n\s*bogpilot_fail_hold ", self.src)
    assert m is not None
    i_build, i_qt, i_mgr = self.src.index("bogpilot_run_build ./build.py"), self.src.index('bogpilot_qt_eglfs_init "$DIR"'), \
      self.src.index("./manager.py")
    assert i_build < i_qt
    assert i_qt < i_mgr
    assert not re.search(r"\n\s*\./build\.py\n", self.src)  # no unguarded build call left

  def test_network_wait_before_build(self):
    assert self.src.index("bogpilot_wait_network") < self.src.index("bogpilot_run_build ./build.py")
    assert 'if [ -f /AGNOS ] && ! bogpilot_build_is_current "$DIR"; then\n      bogpilot_wait_network' in self.src

  def test_qtdeps_failure_handled(self):
    assert 'if ! bogpilot_qt_eglfs_init "$DIR"; then\n      bogpilot_qtdeps_failed "$DIR"' in self.src

  def test_syntax(self):
    for f in (LAUNCH, BUILD_SH, QT_SH):
      assert subprocess.run(["bash", "-n", f]).returncode == 0, f
