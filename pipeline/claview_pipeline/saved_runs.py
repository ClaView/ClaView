"""Server-owned screen snapshots. No client paths or checkpoints are accepted."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

RUN_ID = re.compile(r"[a-f0-9]{32}")


class SavedRunError(ValueError):
    pass


def _folder(root, run_id):
    if root is None or not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise SavedRunError("record_not_found")
    base = Path(root).resolve()
    folder = base / run_id
    if folder.is_symlink() or folder.resolve().parent != base:
        raise SavedRunError("record_invalid")
    return folder


def _json(path):
    if path.is_symlink():
        raise SavedRunError("record_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise SavedRunError("record_not_found") from None
    if not isinstance(value, dict):
        raise SavedRunError("record_invalid")
    return value


def read(root, run_id):
    folder = _folder(root, run_id)
    state, response = _json(folder / "result.json"), _json(folder / "response.json")
    if any(body.get("run_id") != run_id for body in (state, response)):
        raise SavedRunError("record_invalid")
    for field in ("query", "as_of", "status"):
        if state.get(field) != response.get(field):
            raise SavedRunError("record_invalid")
    return state, response


def listing(root, *, query=None, limit=30):
    if root is None or not Path(root).is_dir():
        return []
    found = []
    for folder in Path(root).iterdir():
        if not RUN_ID.fullmatch(folder.name):
            continue
        try:
            _, response = read(root, folder.name)
        except SavedRunError:
            continue
        if query is not None and response.get("query") != query:
            continue
        fields = ("run_id", "query", "as_of", "status", "finished_at", "claview_mode")
        found.append({k: response.get(k) for k in fields})
    return sorted(found, key=lambda row: row.get("finished_at") or "", reverse=True)[:limit]


def claimed(root, run_id):
    return (_folder(root, run_id) / "resume.json").exists()


def claim(root, run_id):
    folder = _folder(root, run_id)
    read(root, run_id)
    try:
        with (folder / "resume.json").open("x", encoding="utf-8") as stream:
            json.dump({"run_id": run_id, "status": "resuming"}, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise SavedRunError("record_already_resumed") from None


def complete(root, source, result):
    path = _folder(root, source) / "resume.json"
    if path.is_symlink():
        raise SavedRunError("record_invalid")
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump({"run_id": source, "status": "resumed", "next_run_id": result["run_id"]}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
