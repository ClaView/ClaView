"""Run the validator in subprocesses so its network block stays isolated."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "pipeline/scripts/verify_catalog_snapshot.py"


def fixture_files(folder: Path, mutation: str | None = None) -> tuple[Path, Path]:
    """A synthetic table exercises real adapters without reading runtime metadata."""
    folder.mkdir()
    catalog = folder / "catalog.json"
    metadata = folder / "metadata.jsonl"
    doc = {
        "org_id": "301",
        "table_id": "TEST_METADATA",
        "title": "검증용 통계표",
        "path": "검증 > 통계표",
        "path_ids": "TEST > TEST_METADATA",
        "category": "검증",
    }
    periods = [{"PRD_SE": "월", "STRT_PRD_DE": "2023.01", "END_PRD_DE": "2024.12"}]
    if mutation == "duplicate":
        periods.append(copy.deepcopy(periods[0]))
        periods[-1]["END_PRD_DE"] = "2025.01"
    elif mutation == "unknown":
        periods[0]["PRD_SE"] = {"unsupported": "일"}
    elif mutation == "integer":
        periods[0]["STRT_PRD_DE"] = 202301
    raw = {
        "orgId": "301",
        "tblId": "TEST_METADATA",
        "name": "검증용 통계표",
        "meta": {
            "TBL": [{"TBL_NM": "검증용 통계표"}],
            "PRD": periods,
            "ITM": [
                {"OBJ_ID": "ITEM", "ITM_ID": "VALUE", "ITM_NM": "검증값", "UNIT_NM": ""},
                {
                    "OBJ_ID": "GROUP",
                    "OBJ_ID_SN": "1",
                    "OBJ_NM": "대상별",
                    "ITM_ID": "ALL",
                    "ITM_NM": "전체",
                    "UNIT_NM": "",
                },
            ],
            "UNIT": [],
            "CMMT": [],
        },
    }
    catalog.write_text(json.dumps({"documents": [doc]}, ensure_ascii=False), encoding="utf-8")
    metadata.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog, metadata


def run_validator(catalog: Path, metadata: Path, output: Path, baseline: Path | None = None):
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (catalog, metadata)]
    command = [
        sys.executable,
        "-B",
        "-X",
        "utf8",
        str(SCRIPT),
        "--repo",
        str(ROOT),
        "--catalog",
        str(catalog),
        "--metadata",
        str(metadata),
        "--out",
        str(output),
    ]
    if baseline is not None:
        command.extend(["--baseline", str(baseline)])
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert output.exists(), (result.stdout, result.stderr)
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (catalog, metadata)]
    return result, json.loads(output.read_text(encoding="utf-8"))


def test_real_adapters_agree_and_blank_units_are_allowed(tmp_path):
    catalog, metadata = fixture_files(tmp_path / "valid")
    output = tmp_path / "baseline.json"
    result, report = run_validator(catalog, metadata, output)
    assert result.returncode == 0, report["errors"]
    assert report["summary"]["a_b_period_tables_compared"] == 1
    assert report["summary"]["a_b_period_tables_equal"] == 1
    assert report["periods"]["301:TEST_METADATA"]["A"] == {"M": {"start": "202301", "end": "202412"}}
    assert report["summary"]["item_unit_counts"] == {"blank": 2}
    assert report["external_calls"] == 0
    repeated, second = run_validator(catalog, metadata, tmp_path / "comparison.json", baseline=output)
    assert repeated.returncode == 0
    assert second["summary"]["new_errors"] == 0
    assert second["baseline_comparison"]["periods"] == {"added": {}, "removed": {}, "changed": {}}
    assert not second["baseline_comparison"]["requires_human_review"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("duplicate", "duplicate_raw_frequency"),
        ("unknown", "unsupported_raw_frequency"),
        ("integer", "raw_period_must_be_string"),
    ],
)
def test_malformed_saved_period_is_rejected_without_changing_input(tmp_path, mutation, expected):
    catalog, metadata = fixture_files(tmp_path / mutation, mutation)
    result, report = run_validator(catalog, metadata, tmp_path / "result.json")
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert expected in {row["code"] for row in report["errors"]}
    assert not report["summary"]["structural_valid"]
    assert report["external_calls"] == 0
