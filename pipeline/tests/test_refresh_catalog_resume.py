"""Offline tests for raw-preserving JSON repair and verified response reuse."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pipeline.scripts import refresh_catalog as mod

FAKE_KEY = "offline-only-synthetic-key"
TABLE_ROW = {"ORG_ID": "301", "TBL_ID": "T1", "TBL_NM": "Synthetic table"}
INVALID_ESCAPE = rb'[{"ITM_ID":"I1","ITM_NM":"Up to \100 million","OBJ_ID":"ITEM"}]'


def safe_params(kind: str, identifier: str) -> dict:
    common = {
        "method": "getList" if kind == "LIST" else "getMeta",
        "format": "json",
        "jsonVD": "Y",
    }
    if kind == "LIST":
        return {**common, "vwCd": "MT_OTITLE", "parentListId": identifier}
    return {**common, "type": kind, "orgId": "301", "tblId": identifier}


def request_params(kind: str, identifier: str) -> dict:
    return {
        key: value for key, value in safe_params(kind, identifier).items() if key not in {"method", "format", "jsonVD"}
    }


def make_cache(root: Path, entries: list[tuple[str, str, object]]) -> Path:
    source = root / "source"
    (source / "responses").mkdir(parents=True)
    ledger = []
    for sequence, (kind, identifier, value) in enumerate(entries, 1):
        data = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
        relative = f"responses/{sequence:04d}_{kind}_{identifier}.json"
        (source / relative).write_bytes(data)
        ledger.append(
            {
                "sequence": sequence,
                "started_at": "2026-11-03T00:00:00+00:00",
                "kind": kind,
                "identifier": identifier,
                "parameters": safe_params(kind, identifier),
                "file": relative,
                "sha256": mod.digest(data),
                "bytes": len(data),
                "status": "invalid_json" if value is INVALID_ESCAPE else "ok" if value else "empty",
            }
        )
    write_ledger(source, ledger)
    return source


def read_ledger(source: Path) -> list[dict]:
    return [json.loads(line) for line in (source / "requests.jsonl").read_text(encoding="utf-8").splitlines()]


def write_ledger(source: Path, ledger: list[dict]) -> None:
    (source / "requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in ledger), encoding="utf-8")


def saved_bytes(source: Path) -> dict[str, bytes]:
    return {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}


@pytest.fixture
def no_network(monkeypatch):
    opener = Mock()
    opener.open.side_effect = AssertionError("Network calls are forbidden in reuse tests")
    monkeypatch.setattr(mod, "build_opener", lambda *_: opener)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    return opener


def test_only_invalid_backslash_escapes_are_normalized():
    raw = rb'[{"label":"Up to \100 million and \q","valid":"line\nbreak\\tail\u20a9"}]'
    value, count = mod.parse_metadata_json(raw)
    assert count == 2
    assert value == [{"label": r"Up to \100 million and \q", "valid": "line\nbreak\\tail\u20a9"}]


def test_valid_json_is_not_normalized():
    value, count = mod.parse_metadata_json(rb'[{"label":"valid \\ slash and \" quote"}]')
    assert value == [{"label": 'valid \\ slash and " quote'}]
    assert count == 0


@pytest.mark.parametrize(
    "raw",
    [
        rb'[{"label":"bad\q",}]',
        rb'[{"label":"invalid unicode \uZZZZ"}]',
        rb'[{"label":"unterminated}]',
        b'[{"label":"control\x01character"}]',
        rb'[{"label":"unclosed"}',
    ],
)
def test_other_json_errors_are_rejected(raw):
    with pytest.raises(json.JSONDecodeError):
        mod.parse_metadata_json(raw)


def test_reuse_verifies_original_bytes_and_uses_no_network(tmp_path, no_network):
    entries = [("LIST", "301", [TABLE_ROW]), ("TBL", "T1", [TABLE_ROW]), ("ITM", "T1", INVALID_ESCAPE)]
    source = make_cache(tmp_path, entries)
    before = saved_bytes(source)
    output = tmp_path / "new"
    output.mkdir()
    client = mod.Collector(output, FAKE_KEY, 1, 0.2, reuse=source)
    results = [client.get(kind, identifier, request_params(kind, identifier)) for kind, identifier, _ in entries]
    assert results[-1][0]["ITM_NM"] == r"Up to \100 million"
    assert client.count == 0
    assert client.reused_count == 3
    assert client.sequence == 3
    assert client.normalized_escapes == 1
    assert client.normalized_responses == 1
    no_network.open.assert_not_called()
    assert saved_bytes(source) == before
    assert (output / "responses" / "0003_ITM_T1.json").read_bytes() == INVALID_ESCAPE


def test_mixed_reuse_and_network_count_and_cap(tmp_path, no_network):
    entries = [("LIST", "301", [TABLE_ROW]), ("TBL", "T1", [TABLE_ROW]), ("ITM", "T1", INVALID_ESCAPE)]
    source = make_cache(tmp_path, entries)
    output = tmp_path / "new"
    output.mkdir()
    client = mod.Collector(output, FAKE_KEY, 1, 0.2, reuse=source)
    for kind, identifier, _ in entries:
        client.get(kind, identifier, request_params(kind, identifier))
    no_network.open.side_effect = None
    no_network.open.return_value = nullcontext(SimpleNamespace(read=lambda _: b'[{"UNIT_NM":"units"}]'))
    assert client.get("UNIT", "T1", request_params("UNIT", "T1")) == [{"UNIT_NM": "units"}]
    assert client.count == 1
    assert client.reused_count == 3
    assert client.sequence == 4
    assert no_network.open.call_count == 1
    with pytest.raises(mod.StopCollection, match="request_cap"):
        client.get("CMMT", "T1", request_params("CMMT", "T1"))
    assert no_network.open.call_count == 1


def test_invalid_cached_json_is_rejected_without_network_retry(tmp_path, no_network):
    source = make_cache(tmp_path, [("ITM", "T1", b'[{"ITM_NM":"invalid",}]')])
    ledger = read_ledger(source)
    ledger[0]["status"] = "invalid_json"
    write_ledger(source, ledger)
    output = tmp_path / "new"
    output.mkdir()
    with pytest.raises(mod.StopCollection, match="invalid_json"):
        client = mod.Collector(output, FAKE_KEY, 1, 0.2, reuse=source)
        client.get("ITM", "T1", request_params("ITM", "T1"))
    no_network.open.assert_not_called()


@pytest.mark.parametrize("tamper", ["hash", "bytes", "parameters", "outside", "absolute"])
def test_unverified_reuse_is_rejected_without_network(tmp_path, no_network, tamper):
    source = make_cache(tmp_path, [("LIST", "301", [TABLE_ROW])])
    ledger = read_ledger(source)
    if tamper == "hash":
        ledger[0]["sha256"] = "0" * 64
    elif tamper == "bytes":
        ledger[0]["bytes"] += 1
    elif tamper == "parameters":
        ledger[0]["parameters"]["method"] = "getData"
    else:
        outside = source / "outside.json"
        outside.write_bytes((source / ledger[0]["file"]).read_bytes())
        ledger[0]["file"] = "responses/../outside.json" if tamper == "outside" else str(outside.resolve())
    write_ledger(source, ledger)
    output = tmp_path / "new"
    output.mkdir()
    with pytest.raises(mod.StopCollection):
        client = mod.Collector(output, FAKE_KEY, 1, 0.2, reuse=source)
        client.get("LIST", "301", request_params("LIST", "301"))
    no_network.open.assert_not_called()


def test_main_reports_actual_network_reuse_and_normalization_counts(tmp_path, no_network, monkeypatch):
    entries = [
        ("LIST", "301", [TABLE_ROW]),
        ("TBL", "T1", [TABLE_ROW]),
        ("PRD", "T1", [{"PRD_SE": "year", "STRT_PRD_DE": "2020", "END_PRD_DE": "2025"}]),
        ("ITM", "T1", INVALID_ESCAPE),
        ("UNIT", "T1", []),
        ("CMMT", "T1", []),
    ]
    source = make_cache(tmp_path, entries)
    before = saved_bytes(source)
    old = tmp_path / "synthetic_old.json"
    old.write_text(json.dumps({"documents": [{"doc_id": "301:T1", "path_ids": "301"}]}), encoding="utf-8")
    output = tmp_path / "new"
    monkeypatch.setattr(mod.os, "environ", {"KOSIS_API_KEY": FAKE_KEY})
    monkeypatch.setattr(
        mod.sys,
        "argv",
        [
            "refresh_catalog",
            "--catalog",
            str(old),
            "--output",
            str(output),
            "--reuse",
            str(source),
            "--max-requests",
            "1",
        ],
    )
    assert mod.main() == 0
    manifest = json.loads((output / "collection_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "collected_not_applied"
    assert manifest["requests"] == manifest["network_requests"] == 0
    assert manifest["reused_responses"] == 6
    assert manifest["normalized_escapes"] == manifest["normalized_responses"] == 1
    assert saved_bytes(source) == before
    no_network.open.assert_not_called()
