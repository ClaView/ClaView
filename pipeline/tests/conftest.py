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


@pytest.fixture(scope="session")
def historical_catalog_components(tmp_path_factory):
    """Pin historical golden dates without rolling back runtime metadata."""
    from claview_a.catalog.layer import load_layer as load_current
    from claview_a_legacy.catalog.layer import load_layer as load_legacy
    from claview_b.metadata import MetadataStore
    from claview_common.testing import frozen_metadata_snapshot

    catalog, metadata = frozen_metadata_snapshot(tmp_path_factory.mktemp("historical_catalog"))
    legacy = load_legacy(search_catalog=catalog, meta_raw=metadata, include_external_synonyms=False)
    current = load_current(search_catalog=catalog, meta_raw=metadata)
    return legacy, current, MetadataStore(catalog, metadata)


@pytest.fixture
def historical_metadata(historical_catalog_components, monkeypatch):
    """Only tests asserting the old snapshot's dates request this fixture."""
    from claview_a import entry
    from claview_a_api import screen as a_screen
    from claview_b_api import service as b_service
    from claview_pipeline import session

    legacy, current, store = historical_catalog_components
    monkeypatch.setattr(entry, "_LAYER", [current])
    monkeypatch.setattr(a_screen, "layer", lambda: current)
    monkeypatch.setattr(b_service, "store", lambda: store)
    monkeypatch.setattr(session, "_default_layer", lambda: legacy)
    monkeypatch.setattr(session, "_default_store", lambda: store)
    return FlowSession.load(layer=legacy, store=store)
