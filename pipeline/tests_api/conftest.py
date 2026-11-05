"""pipeline/tests_api: the pipeline HTTP API. Its own pytest process (README): loopback is allowed here.

The A guard's process-wide network block (pipeline/tests) refuses loopback too, which FastAPI's
TestClient needs; this suite blocks every non-loopback connection instead.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from claview_b_api.service import store as load_store
from claview_common.testing import block_external_network  # noqa: F401  (autouse fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))


@pytest.fixture(scope="session")
def base():
    return SimpleNamespace(store=load_store())


@pytest.fixture()
def make_pipeline(base):
    from flow_support import make_runner
    return lambda **kwargs: make_runner(base.store, **kwargs)
