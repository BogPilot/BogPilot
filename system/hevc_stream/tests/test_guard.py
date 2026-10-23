from openpilot.system.hevc_stream.guard import OFF, PAUSED, RUN, HeatGuard


def feed(g, t0, secs, temp, cpu, hot=False, dt=0.5):
  t = t0
  while t < t0 + secs:
    g.update(t, temp, cpu, hot)
    t += dt
  return t


def test_starts_running_and_stays_when_cool():
  g = HeatGuard()
  feed(g, 0, 120, 55, 40)
  assert g.state == RUN and g.forwarding and g.trips == 0


def test_temp_trip_pauses_and_resumes_after_hold():
  g = HeatGuard()
  assert g.update(0, 75.0, 10) == PAUSED and not g.forwarding and g.trips == 1
  t = feed(g, 1, 30, 72, 10)          # below 75 but not below 70: stays paused
  assert g.state == PAUSED
  t = feed(g, t, 59, 65, 10)          # cool, but < 60 s
  assert g.state == PAUSED
  feed(g, t, 2, 65, 10)
  assert g.state == RUN


def test_cpu_trip_uses_10s_average():
  g = HeatGuard()
  g.update(0, 50, 100)                # one spike: avg 100 -> trips immediately
  assert g.state == PAUSED
  g2 = HeatGuard()
  feed(g2, 0, 10, 50, 50)
  g2.update(10, 50, 100)              # spike within a 10 s window of 50 % samples: avg < 85
  assert g2.state == RUN
  feed(g2, 10.5, 12, 50, 95)          # sustained 95 %
  assert g2.state == PAUSED


def test_thermal_status_trips():
  g = HeatGuard()
  assert g.update(0, 50, 10, True) == PAUSED


def test_hot_sample_resets_resume_hold():
  g = HeatGuard()
  g.update(0, 80, 10)
  t = feed(g, 1, 50, 60, 10)
  g.update(t, 71, 10)                 # one warm sample resets the 60 s hold
  t = feed(g, t + 0.5, 50, 60, 10)
  assert g.state == PAUSED


def test_three_pauses_then_off_for_drive():
  g = HeatGuard()
  t = 0.0
  for i in (1, 2):
    g.update(t, 80, 10)
    assert g.state == PAUSED and g.trips == i
    t = feed(g, t + 1, 61, 60, 10)
    assert g.state == RUN
  assert g.update(t, 80, 10) == OFF and g.trips == 3
  feed(g, t + 1, 300, 40, 5)          # never comes back this drive
  assert g.state == OFF and not g.forwarding
