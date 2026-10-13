from openpilot.selfdrive.car.tesla.lead_filter import Ap1LeadGlitchFilter


def L(d, v, radar=True, vrel=0.0):
  return {'status': True, 'dRel': d, 'vLead': v, 'vRel': vrel, 'radar': radar}


def run(seq, model=(0.9, 46.0, 10.6)):
  f = Ap1LeadGlitchFilter(0.05)
  return [f.update(x, model[0], model[1], model[2], 0.5) for x in seq]


def test_route58_174520_one_frame_roadside_jump_held():
  seq = [L(46.6, 10.8), L(46.4, 10.7), L(42.2, 0.3), L(45.6, 10.3)]
  out = run(seq, model=(0.76, 60.0, 20.0))  # model disagrees: not confirmed
  assert out[2]['vLead'] == 10.7 and abs(out[2]['dRel'] - 46.4) < 0.1
  assert out[3]['dRel'] == 45.6


def test_route58_174528_one_frame_phantom_close_held():
  seq = [L(17.8, 0.0), L(1.5, 0.3), L(16.8, 0.0)]
  out = run(seq, model=(0.0, 0.0, 0.0))
  assert out[1]['dRel'] > 15 and out[2]['dRel'] == 16.8


def test_real_cut_in_delayed_at_most_one_frame():
  seq = [L(40.0, 15.0), L(12.0, 12.0), L(11.8, 12.0), L(11.6, 12.0)]
  out = run(seq, model=(0.9, 60.0, 20.0))
  assert out[1]['dRel'] == 40.0
  assert out[2]['dRel'] == 11.8 and out[3]['dRel'] == 11.6


def test_close_lead_at_standstill_accepted_after_two_frames():
  seq = [L(2.9, 0.0), L(2.9, 0.0), L(2.8, 0.0)]
  out = run(seq, model=(0.0, 0.0, 0.0))
  assert out[0] == {'status': False}
  assert out[1]['dRel'] == 2.9 and out[2]['dRel'] == 2.8


def test_camera_confirmed_never_filtered():
  # Model lead (radar frame) agrees with the jumped radar lead.
  seq = [L(40.0, 15.0), L(12.0, 12.0)]
  out = run(seq, model=(0.9, 12.5, 12.5))
  assert out[1]['dRel'] == 12.0
  # Vision lead with prob above threshold is never filtered, even close.
  out = run([L(40.0, 15.0), L(2.0, 1.0, radar=False)], model=(0.9, 2.0, 1.0))
  assert out[1]['dRel'] == 2.0


def test_new_lead_or_lost_lead_not_delayed():
  out = run([{'status': False}, L(20.0, 5.0), {'status': False}], model=(0.0, 0.0, 0.0))
  assert out[1]['dRel'] == 20.0 and out[2] == {'status': False}


def test_never_two_held_frames_in_a_row():
  seq = [L(40.0, 15.0), L(20.0, 5.0), L(10.0, 1.0), L(9.9, 1.0)]
  out = run(seq, model=(0.9, 60.0, 20.0))
  assert out[1]['dRel'] == 40.0
  assert out[2]['dRel'] == 10.0
