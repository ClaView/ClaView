"""pipeline/tests_api: the pipeline HTTP API. Its own pytest process (README): loopback is allowed here.

The A guard's process-wide network block (pipeline/tests) refuses loopback too, which FastAPI's
TestClient needs; this suite blocks every non-loopback connection instead.
"""

from __future__ import annotations

import pytest

from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
from claview_common.testing import block_external_network  # noqa: F401  (autouse fixture)
from claview_pipeline import FlowSession


@pytest.fixture(scope="session")
def base() -> FlowSession:
    return FlowSession.load()


@pytest.fixture()
def make_pipeline(base):
    from claview_pipeline.staged import Pipeline

    def build(*, model: bool = True, client: bool = True):
        return Pipeline(
            model=SyntheticSelectionModel(base.store) if model else None,
            client=SyntheticFetchClient() if client else None,
            runs_dir=None,
            session=base,
        )

    return build
