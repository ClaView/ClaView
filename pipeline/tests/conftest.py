"""tests/pipeline bootstrap: network is blocked for the whole process before any test runs.

Only A's network block is installed (not its import guard), because this suite loads
A (claview_a_legacy) and B (claview) together. Run it as its own pytest process (README).
"""

from __future__ import annotations

import sys
from pathlib import Path

from claview_a_legacy import _guard

_guard.install_network_block()

HERE = str(Path(__file__).resolve().parent)
if HERE not in sys.path:
    sys.path.append(HERE)

import pytest  # noqa: E402

from claview_pipeline import FlowSession  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network_attempt():
    before = len(_guard.NETWORK_ATTEMPTS)
    yield
    assert _guard.NETWORK_ATTEMPTS[before:] == [], "a network transport was attempted"


@pytest.fixture(scope="session")
def base() -> FlowSession:
    """The real 349-table catalog loaded once (A layer, B metadata store), no B clients."""
    return FlowSession.load()
