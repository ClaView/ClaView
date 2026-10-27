"""Append-only call reservations and response bodies for development replay.

Reservations survive crashes and count against the batch cap even without a response.
A short exclusive lock serialises reservations; an abandoned lock requires human review.
There is no implicit retry, overwrite, or live fallback from an archive miss.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArchiveError(Exception):
    """Only fixed, non-sensitive error codes travel outside this module."""


class ReplayMiss(ArchiveError):
    pass


class ResponseArchive:
    def __init__(self, root: Path | str, max_calls: int = 30):
        if type(max_calls) is not int or not 1 <= max_calls <= 90:
            raise ValueError("development call cap must be between 1 and 90")
        self.root, self.max_calls = Path(root), max_calls

    def path(self, call_id: str) -> Path:
        return self.root / (digest(call_id) + ".json")

    def reserve(self, call_id: str, request: dict, versions: dict, kind: str) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / ".reservation.lock"
        try:
            handle = lock.open("x", encoding="utf-8")
        except FileExistsError:
            raise ArchiveError("archive_busy") from None
        try:
            handle.close()
            manifest = self.root / "batch.json"
            policy = {"version": 1, "max_calls": self.max_calls, "transport_kind": kind}
            if manifest.exists():
                if json.loads(manifest.read_text(encoding="utf-8")) != policy:
                    raise ArchiveError("batch_conflict")
            else:
                with manifest.open("x", encoding="utf-8") as f:
                    f.write(canonical(policy))
            path = self.path(call_id)
            if path.exists():
                raise ArchiveError("already_reserved")
            count = sum(p.name != "batch.json" for p in self.root.glob("*.json"))
            if count >= self.max_calls:
                raise ArchiveError("call_limit")
            row = {"version": 1, "call_id": call_id, "request_hash": digest(request), "request": request,
                   "versions": versions, "transport_kind": kind, "status": "started", "started_at": now(),
                   "finished_at": None, "attempts": 0, "raw_response": None, "response_hash": None,
                   "usage": None, "latency_ms": None, "error_code": None, "http_status": None}
            with path.open("x", encoding="utf-8") as f:
                f.write(canonical(row))
            return row
        finally:
            lock.unlink()

    def update(self, row: dict) -> None:
        path = self.path(row["call_id"])
        tmp = self.root / (uuid.uuid4().hex + ".tmp")
        try:
            with tmp.open("x", encoding="utf-8") as f:
                f.write(canonical(row))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def read(self, call_id: str, request: dict) -> dict:
        try:
            row = json.loads(self.path(call_id).read_text(encoding="utf-8"))
            if (row["version"] != 1 or row["call_id"] != call_id or row["status"] != "received"
                    or row["request_hash"] != digest(request) or digest(row["request"]) != digest(request)
                    or not isinstance(row["raw_response"], str)
                    or row["response_hash"] != digest(row["raw_response"])):
                raise ReplayMiss("replay_miss")
            return row
        except (OSError, ValueError, KeyError, TypeError):
            raise ReplayMiss("replay_miss") from None

