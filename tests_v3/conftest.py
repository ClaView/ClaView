"""tests_v3 bootstrap: the boundary is set before any test module is imported.

pytest imports this file before it collects test modules, so the module body below runs first:
1. fail at once if a forbidden (old-pipeline) module is already loaded,
2. install the claview_v3 guard (import hook, old-source audit hook, network blocking).
At session end sys.modules and the guard's records are checked again.

Run separately from tests_v3_compare (never in one pytest process):
    PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 -m pytest tests_v3 -q -p no:cacheprovider
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from claview_v3 import _guard  # noqa: E402  (the package __init__ is empty; _guard imports only paths)

_already = _guard.loaded_forbidden()
if _already:
    raise RuntimeError(f"tests_v3: forbidden modules loaded before collection: {_already}")
_guard.install()

import pytest  # noqa: E402


@pytest.fixture
def refusals():
    """For counterexample tests that trigger the guard on purpose: exposes and then clears only their records."""
    v0, n0 = len(_guard.VIOLATIONS), len(_guard.NETWORK_ATTEMPTS)

    class Records:
        @staticmethod
        def violations():
            return list(_guard.VIOLATIONS[v0:])

        @staticmethod
        def network():
            return list(_guard.NETWORK_ATTEMPTS[n0:])

    yield Records
    del _guard.VIOLATIONS[v0:]
    del _guard.NETWORK_ATTEMPTS[n0:]


@pytest.fixture(autouse=True)
def _no_unexpected_refusal(request):
    """Any other test that makes the guard refuse something fails, even if it swallowed the exception."""
    v0, n0 = len(_guard.VIOLATIONS), len(_guard.NETWORK_ATTEMPTS)
    yield
    if "refusals" in request.fixturenames:
        return
    assert _guard.VIOLATIONS[v0:] == [] and _guard.NETWORK_ATTEMPTS[n0:] == [], "guard refused an operation"


def pytest_sessionfinish(session, exitstatus):
    found = _guard.problems()
    if found:
        sys.stderr.write("tests_v3 boundary check failed at session end:\n")
        for line in found:
            sys.stderr.write(f"  {line}\n")
        session.exitstatus = 1
