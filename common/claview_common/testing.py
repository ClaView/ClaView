"""Test helpers shared by the API suites.

`block_external_network` refuses every socket connection that is not loopback. The API suites
need loopback (FastAPI's TestClient runs an event loop, which on Windows builds its self-pipe
with a local socket pair); anything else is a test bug and fails loudly.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import stat
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

import pytest

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_GETADDRINFO = socket.getaddrinfo
LOCAL_NAMES = {"localhost", "testserver"}


def _is_loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    if host in LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _refuse(host):
    raise AssertionError(f"Network access is forbidden in offline tests (host {host!r}).")


def install(monkeypatch: pytest.MonkeyPatch) -> None:
    def connect(self, address):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            _refuse(address[0])
        return _REAL_CONNECT(self, address)

    def connect_ex(self, address):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            _refuse(address[0])
        return _REAL_CONNECT_EX(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not _is_loopback(host):
            _refuse(host)
        return _REAL_GETADDRINFO(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    install(monkeypatch)
    yield


FROZEN_METADATA_ARCHIVE = Path(__file__).resolve().parents[2] / "eval" / "fixtures" / "metadata_before_2026_11_03.zip"
FILE_SHA256 = {
    "data/catalog/search_catalog.json": "0ff158a8717c1c5fae924d36ddb086c7538d59c3768a93bb4914349f8844f551",
    "data/raw/boK_meta_raw.jsonl": "b604e909d40f42286b1ce31c11b173dd022477c7840c5873b35650e8563d921e",
    "data/mappings/runtime/unit_supplement.json": "4bee75568ae7c4a1ac1193d04e0c69599663e44d3ec94e7fa7fd2b7a742b13e7",
}


def _snapshot_table_keys(rows: object, org_field: str, table_field: str) -> set[str]:
    if not isinstance(rows, list) or len(rows) != 349:
        raise ValueError("Frozen metadata must contain exactly 349 tables")
    keys = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid frozen metadata table row")
        org_id, table_id = row.get(org_field), row.get(table_field)
        if org_id != "301" or not isinstance(table_id, str) or not table_id:
            raise ValueError("Invalid frozen metadata table identity")
        key = f"{org_id}:{table_id}"
        if org_field == "org_id" and row.get("doc_id") != key:
            raise ValueError("Frozen catalog document identity differs")
        keys.append(key)
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate frozen metadata table identity")
    return set(keys)


def frozen_metadata_snapshot(destination: Path) -> tuple[Path, Path]:
    """Materialize the pinned historical test snapshot without altering existing files.

    Current runtime data stays separate. Every archive member and table identity is
    checked before writing; the colocated unit supplement preserves the historical
    MetadataStore lookup behavior.
    """
    destination = Path(destination)
    members = {"manifest.json", *FILE_SHA256}
    try:
        with ZipFile(FROZEN_METADATA_ARCHIVE) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(set(names)) != len(names) or set(names) != members:
                raise ValueError("Unexpected or duplicate frozen metadata archive members")
            for info in infos:
                member = PurePosixPath(info.filename)
                if (
                    member.is_absolute()
                    or ".." in member.parts
                    or "\\" in info.filename
                    or info.is_dir()
                    or stat.S_ISLNK(info.external_attr >> 16)
                ):
                    raise ValueError("Unsafe frozen metadata archive member")
            if archive.getinfo("manifest.json").file_size > 1_000_000:
                raise ValueError("Frozen metadata manifest is too large")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != 1
                or manifest.get("snapshot_id") != "metadata_before_2026_11_03"
                or not isinstance(manifest.get("files"), dict)
                or set(manifest["files"]) != set(FILE_SHA256)
            ):
                raise ValueError("Invalid frozen metadata manifest")
            contents = {}
            for name, expected_hash in FILE_SHA256.items():
                record = manifest["files"][name]
                if (
                    not isinstance(record, dict)
                    or record.get("sha256") != expected_hash
                    or type(record.get("bytes")) is not int
                    or not 0 <= record["bytes"] <= 100_000_000
                    or archive.getinfo(name).file_size != record["bytes"]
                ):
                    raise ValueError("Frozen metadata member differs from pinned manifest")
                data = archive.read(name)
                if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != expected_hash:
                    raise ValueError("Frozen metadata member digest or size differs")
                contents[name] = data
    except (BadZipFile, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise ValueError("Invalid frozen metadata archive") from exc

    try:
        catalog = json.loads(contents["data/catalog/search_catalog.json"].decode("utf-8-sig"))
        raw = [
            json.loads(line)
            for line in contents["data/raw/boK_meta_raw.jsonl"].decode("utf-8-sig").splitlines()
            if line.strip()
        ]
        if not isinstance(catalog, dict):
            raise ValueError("Invalid frozen metadata catalog")
        catalog_keys = _snapshot_table_keys(catalog.get("documents"), "org_id", "table_id")
        raw_keys = _snapshot_table_keys(raw, "orgId", "tblId")
        if catalog_keys != raw_keys:
            raise ValueError("Frozen catalog and metadata table identities differ")
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid frozen metadata documents") from exc

    paths = {name: destination.joinpath(*PurePosixPath(name).parts) for name in contents}
    for name, path in paths.items():
        for ancestor in (destination, *path.parents[: len(path.parts) - len(destination.parts) - 1]):
            if ancestor.is_symlink() or (ancestor.exists() and not ancestor.is_dir()):
                raise ValueError("Unsafe frozen metadata destination directory")
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != contents[name])):
            raise ValueError("Frozen metadata destination already contains different content")
    for name, path in paths.items():
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as handle:
                handle.write(contents[name])
    return paths["data/catalog/search_catalog.json"], paths["data/raw/boK_meta_raw.jsonl"]
