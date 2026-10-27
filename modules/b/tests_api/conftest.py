"""modules/b/tests_api: B's HTTP layer. Its own pytest process: modules/b/tests blocks every socket,
including the loopback pair FastAPI's TestClient needs."""

import pytest

from claview_common.testing import block_external_network  # noqa: F401  (autouse fixture)


@pytest.fixture(autouse=True)
def synthetic_mode(monkeypatch):
    monkeypatch.setenv("CLAVIEW_MODE", "synthetic")
