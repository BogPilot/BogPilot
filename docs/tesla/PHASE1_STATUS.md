# Phase 1 status

> **Historical (Phase 1).** This page records the Phase 1 base tree. It is no longer the state of `bogpilot-tesla`: AP1 Model S now has openpilot lateral and longitudinal control (tag `ap1-driving-milestone-4`), `TeslaPlatform` exists in `selfdrive/car/tesla/values.py`, and `test_early_platform_smoke.py` no longer fails on the missing enum. Current status: [`README.md`](README.md), [`STALK.md`](STALK.md), [`DIVERGENCES.md`](DIVERGENCES.md).

Branch `bogpilot-tesla` is the pinned FrogPilot SHA plus the Phase 1 docs, rebrand, and this red test. No Tesla actuation. Not a tagged release. Not safe to drive.

## Smoke test

Command (this box has no compiled `params_pyx.so`, so root `conftest.py` cannot load; `--noconftest` reaches the assertion the device suite will hit once the image is built):

```
PYTHONPATH=/workspace/BogPilot pytest --noconftest selfdrive/car/tesla/tests/test_early_platform_smoke.py
```

Result: exit 1. `TeslaPlatform` is missing from `openpilot.selfdrive.car.tesla.values`.

```
selfdrive/car/tesla/tests/test_early_platform_smoke.py:15: in test_early_tesla_platform_enum
    assert not missing, "early Tesla platforms are not in this tree yet (Phase 3)"
E   AssertionError: early Tesla platforms are not in this tree yet (Phase 3)
E   assert not ['preap', 'ap1_s', 'ap1_x', 'ap2']
FAILED selfdrive/car/tesla/tests/test_early_platform_smoke.py::test_early_tesla_platform_enum
1 failed
```

The enum is intentionally not added in Phase 1. Phase 3 makes this test pass.
