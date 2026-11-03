from __future__ import annotations

import json
from pathlib import Path

import pytest

from claview_b.contracts import Config
from claview_b.metadata import MetadataStore
from claview_b.search import SearchIndex
from claview_common.testing import frozen_metadata_snapshot

ROOT = Path(__file__).resolve().parents[4]
GOLDEN_PATH = ROOT / "eval" / "cases" / "golden" / "golden_set_v1.json"
SCHEMA_PATH = ROOT / "eval" / "cases" / "b" / "period" / "golden_set_v1.schema.json"
PERIOD_AUGMENTED_PATH = ROOT / "eval" / "cases" / "b" / "period" / "golden_set_v1_period_augmented.json"
PERIOD_AUGMENTED_SCHEMA_PATH = (
    ROOT / "eval" / "cases" / "b" / "period" / "golden_set_v1_period_augmented.schema.json"
)


@pytest.fixture(scope="session")
def store() -> MetadataStore:
    return MetadataStore(ROOT / "data/catalog/search_catalog.json", ROOT / "data/raw/boK_meta_raw.jsonl")


@pytest.fixture(scope="session")
def historical_store(tmp_path_factory) -> MetadataStore:
    # Recorded golden periods stay paired with their original metadata coverage.
    catalog, metadata = frozen_metadata_snapshot(tmp_path_factory.mktemp("historical-b-metadata"))
    return MetadataStore(catalog, metadata)


@pytest.fixture(scope="session")
def search(store: MetadataStore) -> SearchIndex:
    return SearchIndex(store)


@pytest.fixture(scope="session")
def historical_search(historical_store: MetadataStore) -> SearchIndex:
    return SearchIndex(historical_store)


@pytest.fixture(scope="session")
def config() -> Config:
    return Config()


@pytest.fixture(scope="session")
def golden_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="session")
def golden_cases() -> list[dict]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8-sig"))["cases"]


@pytest.fixture(scope="session")
def period_augmented_schema() -> dict:
    return json.loads(PERIOD_AUGMENTED_SCHEMA_PATH.read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="session")
def period_augmented_cases() -> list[dict]:
    return json.loads(PERIOD_AUGMENTED_PATH.read_text(encoding="utf-8-sig"))["cases"]
