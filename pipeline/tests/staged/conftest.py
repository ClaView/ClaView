"""pipeline/tests/staged bootstrap: network blocked for the whole process; the golden replay tool importable.

Run as its own pytest process (README): it loads A (claview_a) and B (claview_b) together
through claview_pipeline, so it cannot share a process with modules/a/tests_tl.
"""

from __future__ import annotations

import sys
from pathlib import Path

from claview_a import _guard

_guard.install_network_block()

# The golden v3 replay tool (pipeline/scripts/golden_replay.py) is imported by its tests as a flat module.
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pytest  # noqa: E402

from types import SimpleNamespace
from claview_b_api.service import store as load_store  # noqa: E402
from claview_pipeline.synthetic import SyntheticFetchClient, SyntheticSelectionModel  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network_attempt():
    before = len(_guard.NETWORK_ATTEMPTS)
    yield
    assert _guard.NETWORK_ATTEMPTS[before:] == [], "a network transport was attempted"


@pytest.fixture(scope="session")
def base():
    return SimpleNamespace(store=load_store())


@pytest.fixture()
def make_pipeline(base):
    from flow_support import make_runner
    return lambda **kwargs: make_runner(base.store, **kwargs)
