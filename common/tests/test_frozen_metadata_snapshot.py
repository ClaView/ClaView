"""Pinned historical metadata is verified before any test files are materialized."""

from __future__ import annotations

import hashlib
import json
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from claview_common import testing


def payloads():
    catalog = {"documents": [{"doc_id": f"301:T{i}", "org_id": "301", "table_id": f"T{i}"} for i in range(349)]}
    raw = [{"orgId": "301", "tblId": f"T{i}", "meta": {}} for i in range(349)]
    return {
        "data/catalog/search_catalog.json": json.dumps(catalog).encode(),
        "data/raw/boK_meta_raw.jsonl": b"\n".join(json.dumps(row).encode() for row in raw) + b"\n",
        "data/mappings/runtime/unit_supplement.json": b'{"tables":{}}',
    }


def make_archive(path, files, *, extras=(), sizes=None, symlink=None):
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    manifest = {
        "schema_version": 1,
        "snapshot_id": "metadata_before_2026_11_03",
        "files": {
            name: {"sha256": hashes[name], "bytes": (sizes or {}).get(name, len(data))} for name, data in files.items()
        },
    }
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.items():
            if name == symlink:
                info = ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, data)
            else:
                archive.writestr(name, data)
        for name, data in extras:
            archive.writestr(name, data)
    return hashes


@pytest.fixture
def synthetic_archive(tmp_path, monkeypatch):
    archive = tmp_path / "synthetic.zip"
    files = payloads()
    hashes = make_archive(archive, files)
    monkeypatch.setattr(testing, "FROZEN_METADATA_ARCHIVE", archive)
    monkeypatch.setattr(testing, "FILE_SHA256", hashes)
    return archive, files


def test_repository_archive_has_verified_full_snapshot(tmp_path):
    catalog, raw = testing.frozen_metadata_snapshot(tmp_path / "frozen")
    docs = json.loads(catalog.read_text(encoding="utf-8-sig"))["documents"]
    records = [json.loads(line) for line in raw.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    assert len(docs) == len(records) == 349
    assert {row["doc_id"] for row in docs} == {f"{row['orgId']}:{row['tblId']}" for row in records}
    root = catalog.parents[2]
    for name, expected in testing.FILE_SHA256.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected


def test_matching_existing_files_are_reused_unchanged(tmp_path, synthetic_archive):
    destination = tmp_path / "frozen"
    first = testing.frozen_metadata_snapshot(destination)
    before = {path: path.stat().st_mtime_ns for path in destination.rglob("*") if path.is_file()}
    assert testing.frozen_metadata_snapshot(destination) == first
    assert {path: path.stat().st_mtime_ns for path in before} == before


def test_conflicting_destination_is_refused_before_any_writes(tmp_path, synthetic_archive):
    destination = tmp_path / "frozen"
    existing = destination / "data/raw/boK_meta_raw.jsonl"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"preserve existing test data")
    with pytest.raises(ValueError, match="different content"):
        testing.frozen_metadata_snapshot(destination)
    assert existing.read_bytes() == b"preserve existing test data"
    assert not (destination / "data/catalog").exists()


def test_changed_file_and_manifest_still_fail_pinned_digest(tmp_path, synthetic_archive):
    archive, files = synthetic_archive
    files["data/mappings/runtime/unit_supplement.json"] = b'{"tables":{"changed":true}}'
    make_archive(archive, files)
    destination = tmp_path / "frozen"
    with pytest.raises(ValueError, match="pinned manifest"):
        testing.frozen_metadata_snapshot(destination)
    assert not destination.exists()


def test_bad_manifest_size_is_refused(tmp_path, synthetic_archive):
    archive, files = synthetic_archive
    make_archive(archive, files, sizes={"data/raw/boK_meta_raw.jsonl": 1})
    with pytest.raises(ValueError, match="pinned manifest"):
        testing.frozen_metadata_snapshot(tmp_path / "frozen")


@pytest.mark.parametrize("name", ["extra.txt", "../outside.json", "/absolute.json", r"..\outside.json"])
def test_unknown_or_unsafe_members_are_refused(tmp_path, synthetic_archive, name):
    archive, files = synthetic_archive
    make_archive(archive, files, extras=[(name, b"extra")])
    destination = tmp_path / "frozen"
    with pytest.raises(ValueError, match="archive members"):
        testing.frozen_metadata_snapshot(destination)
    assert not destination.exists()


def test_duplicate_member_is_refused(tmp_path, synthetic_archive):
    archive, files = synthetic_archive
    with pytest.warns(UserWarning, match="Duplicate name"):
        make_archive(archive, files, extras=[("data/raw/boK_meta_raw.jsonl", files["data/raw/boK_meta_raw.jsonl"])])
    with pytest.raises(ValueError, match="duplicate"):
        testing.frozen_metadata_snapshot(tmp_path / "frozen")


def test_symlink_archive_member_is_refused(tmp_path, synthetic_archive):
    archive, files = synthetic_archive
    make_archive(archive, files, symlink="data/catalog/search_catalog.json")
    with pytest.raises(ValueError, match="Unsafe"):
        testing.frozen_metadata_snapshot(tmp_path / "frozen")


@pytest.mark.parametrize("change", ["duplicate_catalog", "duplicate_raw", "mismatch", "missing_table"])
def test_table_set_integrity_checked_after_digest_validation(tmp_path, synthetic_archive, monkeypatch, change):
    archive, files = synthetic_archive
    catalog = json.loads(files["data/catalog/search_catalog.json"])
    raw = [json.loads(line) for line in files["data/raw/boK_meta_raw.jsonl"].splitlines()]
    if change == "duplicate_catalog":
        catalog["documents"][-1] = catalog["documents"][0]
    elif change == "duplicate_raw":
        raw[-1] = raw[0]
    elif change == "mismatch":
        raw[-1]["tblId"] = "DIFFERENT"
    else:
        catalog["documents"].pop()
    files["data/catalog/search_catalog.json"] = json.dumps(catalog).encode()
    files["data/raw/boK_meta_raw.jsonl"] = b"\n".join(json.dumps(row).encode() for row in raw)
    monkeypatch.setattr(testing, "FILE_SHA256", make_archive(archive, files))
    destination = tmp_path / "frozen"
    with pytest.raises(ValueError):
        testing.frozen_metadata_snapshot(destination)
    assert not destination.exists()


def test_destination_symlink_is_refused(tmp_path, synthetic_archive, monkeypatch):
    destination = tmp_path / "frozen"
    destination.mkdir()
    path_type = type(destination)
    original = path_type.is_symlink
    monkeypatch.setattr(path_type, "is_symlink", lambda path: path == destination or original(path))
    with pytest.raises(ValueError, match="Unsafe"):
        testing.frozen_metadata_snapshot(destination)
    assert list(destination.iterdir()) == []
