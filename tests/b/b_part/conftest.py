from __future__ import annotations

import json
from pathlib import Path

import pytest

from claview.b.contracts import Config
from claview.b.metadata import MetadataStore
from claview.b.search import SearchIndex

ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = ROOT / "data" / "catalog" / "search_catalog.json"
METADATA_PATH = ROOT / "data" / "raw" / "boK_meta_raw.jsonl"
GOLDEN_PATH = ROOT / "eval" / "cases" / "golden_set_v1.json"
SCHEMA_PATH = ROOT / "eval" / "cases" / "b_period" / "golden_set_v1.schema.json"
PERIOD_AUGMENTED_PATH = ROOT / "eval" / "cases" / "b_period" / "golden_set_v1_period_augmented.json"
PERIOD_AUGMENTED_SCHEMA_PATH = (
    ROOT / "eval" / "cases" / "b_period" / "golden_set_v1_period_augmented.schema.json"
)


@pytest.fixture(scope="session")
def store() -> MetadataStore:
    return MetadataStore(CATALOG_PATH, METADATA_PATH)


@pytest.fixture(scope="session")
def search(store: MetadataStore) -> SearchIndex:
    return SearchIndex(store)


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
