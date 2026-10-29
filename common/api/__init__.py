import jwt
import os
import requests
from datetime import datetime, timedelta
from openpilot.system.hardware.hw import Paths
from openpilot.system.version import get_version

from openpilot.frogpilot.common.frogpilot_utilities import use_konik_server

API_HOST = os.getenv('API_HOST', '')  # BogPilot: comma API disabled; api_get never dials out
KONIK_API_HOST = os.getenv('API_HOST', '')  # BogPilot: Konik API disabled

KEYS = {"id_rsa": "RS256",
        "id_ecdsa": "ES256"}

class Api:
  def __init__(self, dongle_id):
    self.dongle_id = dongle_id
    with open(Paths.persist_root()+'/comma/id_rsa') as f:
      self.private_key = f.read()

  def get(self, *args, **kwargs):
    return self.request('GET', *args, **kwargs)

  def post(self, *args, **kwargs):
    return self.request('POST', *args, **kwargs)

  def request(self, method, endpoint, timeout=None, access_token=None, **params):
    return api_get(endpoint, method=method, timeout=timeout, access_token=access_token, **params)

  def get_token(self, expiry_hours=1):
    now = datetime.utcnow()
    payload = {
      'identity': self.dongle_id,
      'nbf': now,
      'iat': now,
      'exp': now + timedelta(hours=expiry_hours)
    }
    token = jwt.encode(payload, self.private_key, algorithm='RS256')
    if isinstance(token, bytes):
      token = token.decode('utf8')
    return token


class _BogPilotBlockedResponse:
  """Stand-in when comma/Konik API calls are refused (BogPilot offline policy)."""
  status_code = 503
  text = '{"error":"bogpilot_api_disabled"}'
  def json(self):
    return {"error": "bogpilot_api_disabled"}


def api_get(endpoint, method='GET', timeout=None, access_token=None, **params):
  # BogPilot: never phone home to api.commadotai.com / api.konik.ai. Callers that
  # check status_code get 503; do not perform the HTTP request.
  return _BogPilotBlockedResponse()


def get_key_pair() -> tuple[str, str, str] | tuple[None, None, None]:
  for key in KEYS:
    if os.path.isfile(Paths.persist_root() + f'/comma/{key}') and os.path.isfile(Paths.persist_root() + f'/comma/{key}.pub'):
      with open(Paths.persist_root() + f'/comma/{key}') as private, open(Paths.persist_root() + f'/comma/{key}.pub') as public:
        return KEYS[key], private.read(), public.read()
  return None, None, None
