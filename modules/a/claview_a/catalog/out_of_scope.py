"""Out-of-scope terms: format and loader only (contents deferred; the package file has 0 lines).

Line format (JSON Lines)::

    {"term": "...", "reason": "...", "source": "...", "status": "approved|draft"}

All four fields are required. A term marks a fragment as type ``out_of_scope`` only on an exact key match
(``text.index_key``) and only when its status is approved; draft lines are loaded and counted but not used.
"""

from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from .. import paths
from .text import index_key


REQUIRED_FIELDS = ("term", "reason", "source", "status")


@dataclass(frozen=True)
class OutOfScopeEntry:
    term: str
    term_key: str
    reason: str
    source: str
    status: str


@dataclass
class OutOfScopeList:
    all_entries: list[OutOfScopeEntry]

    @property
    def entries(self) -> list[OutOfScopeEntry]:
        """Usable entries (approved only)."""
        return [e for e in self.all_entries if e.status == "approved"]

    def lookup(self, text: str) -> list[OutOfScopeEntry]:
        k = index_key(text)
        return [e for e in self.entries if e.term_key == k]


def load_out_of_scope(path: Path | None = None) -> OutOfScopeList:
    path = Path(path or paths.OUT_OF_SCOPE)
    entries = []
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            for n, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                missing = [f for f in REQUIRED_FIELDS if not isinstance(row.get(f), str)]
                if missing:
                    raise ValueError(f"{path.name}:{n}: bad out-of-scope line (missing {missing})")
                entries.append(OutOfScopeEntry(row["term"], index_key(row["term"]), row["reason"], row["source"],
                                               row["status"]))
    return OutOfScopeList(entries)
