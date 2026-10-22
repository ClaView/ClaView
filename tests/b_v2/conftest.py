from __future__ import annotations

import pytest
from b_v2_support import CATALOG_PATH, METADATA_PATH

from claview.metadata import MetadataStore


@pytest.fixture(scope="session")
def store() -> MetadataStore:
    return MetadataStore(CATALOG_PATH, METADATA_PATH)
