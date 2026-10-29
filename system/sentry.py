"""Install exception handler for process crash."""
import sentry_sdk
import traceback
from datetime import datetime
from enum import Enum

from openpilot.common.swaglog import cloudlog

from openpilot.frogpilot.common.frogpilot_variables import ERROR_LOGS_PATH

class SentryProject(Enum):
  # BogPilot: no crash-report destination. Upstream openpilot sent to comma's Sentry project;
  # FrogPilot replaced both DSNs with FrogPilot's own project, which only accepted FrogAi
  # origins (so BogPilot devices never reported anyway). Both are removed; init() is a no-op
  # and crashes are only written to the local error logs by capture_exception().
  SELFDRIVE = "disabled-selfdrive"
  SELFDRIVE_NATIVE = "disabled-selfdrive-native"


def report_tombstone(fn: str, message: str, contents: str) -> None:
  cloudlog.error({'tombstone': message})

  with sentry_sdk.configure_scope() as scope:
    scope.set_extra("tombstone_fn", fn)
    scope.set_extra("tombstone", contents)
    sentry_sdk.capture_message(message=message)
    sentry_sdk.flush()


def capture_block():
  with sentry_sdk.push_scope() as scope:
    sentry_sdk.capture_message("Blocked user from using the development branch", level='info')
    sentry_sdk.flush()


def capture_exception(*args, crash_log=True, **kwargs) -> None:
  exc_text = traceback.format_exc()

  phrases_to_check = [
    "already exists. To overwrite it, set 'overwrite' to True",
    "failed after retry",
  ]

  if any(phrase in exc_text for phrase in phrases_to_check):
    return

  save_exception(exc_text, crash_log)
  cloudlog.error("crash", exc_info=kwargs.get('exc_info', 1))

  try:
    sentry_sdk.capture_exception(*args, **kwargs)
    sentry_sdk.flush()  # https://github.com/getsentry/sentry-python/issues/291
  except Exception:
    cloudlog.exception("sentry exception")


def set_tag(key: str, value: str) -> None:
  sentry_sdk.set_tag(key, value)


def save_exception(exc_text: str, crash_log) -> None:
  files = [
    ERROR_LOGS_PATH / datetime.now().astimezone().strftime("%Y-%m-%d--%H-%M-%S.log"),
    ERROR_LOGS_PATH / "error.txt"
  ]

  for file_path in files:
    if file_path.name == "error.txt" and crash_log:
      lines = exc_text.splitlines()[-10:]
      file_path.write_text("\n".join(lines))
    else:
      file_path.write_text(exc_text)


def init(project: SentryProject) -> bool:
  """BogPilot: never initialise Sentry, so nothing leaves the device.

  sentry_sdk.capture_* calls elsewhere are no-ops without init(); local error logs
  (ERROR_LOGS_PATH) are still written by capture_exception(). If a DSN is ever added
  back, pass include_local_variables=False so stack frames' locals (params, keys,
  positions) are not uploaded.
  """
  return False
