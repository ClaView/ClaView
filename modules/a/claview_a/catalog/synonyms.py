"""Synonym dictionary: the approved file (package data, empty for now) and the optional external draft.

Line format (JSON Lines, one official name x kind per line)::

    {"official": "...", "synonyms": ["...", ...], "kind": "alias|layman", "source": "...", "status": "approved|draft",
     "negatives": [...], "source_ref": "...", "ambiguous": false, "note": "..."}

``official``, ``synonyms``, ``source`` and ``status`` are required. Only ``status == "approved"`` lines of the
approved file are used. The external draft (paths.EXTERNAL_SYNONYMS, outside git, personal implementation stage
only) is read when the file is present and ``include_external`` is on, and skipped otherwise; its lines keep their
own ``source`` (external:…) and ``status`` (draft). Nothing from the external file is copied into package code or
data.

Ambiguity is judged again here. A word attached to several official names whose parenthesis-free keys are the same
("M2(말잔)" and "M2(평잔)" -> "m2") is one group, not an ambiguity: entries with the same word and the same
parenthesis-free official key are merged, and a word is ambiguous only if it still points at two or more different
keys (or is a single character). The file's own flag is kept as ``ambiguous_source``. Officials of the source
dictionary that are not in the file cannot be seen here, so the re-judgment counts only what the file holds.
"""

from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from .text import bkey, index_key

REQUIRED_FIELDS = ("official", "synonyms", "source", "status")


@dataclass(frozen=True)
class SynonymEntry:
    term: str
    term_key: str
    officials: tuple[str, ...]
    official_key: str  # parenthesis-free key shared by the merged officials
    kinds: tuple[str, ...]
    source: str
    status: str
    origin: str  # approved / external
    ambiguous: bool  # judged here
    ambiguous_source: bool  # flag carried in the file (any merged line)
    negatives: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()


@dataclass
class SynonymDict:
    entries: list[SynonymEntry]
    include_external: bool
    external_present: bool
    counts: dict = field(default_factory=dict)

    def usable(self) -> list[SynonymEntry]:
        """Approved entries, plus the external draft entries when that switch is on."""
        return [e for e in self.entries if (e.origin == "approved" and e.status == "approved")
                or (e.origin == "external" and self.include_external)]

    def lookup(self, text: str) -> list[SynonymEntry]:
        k = index_key(text)
        return [e for e in self.usable() if e.term_key == k]


def _read_lines(path: Path, origin: str) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for n, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = [f for f in REQUIRED_FIELDS if f not in row]
            if missing or not isinstance(row.get("synonyms"), list):
                raise ValueError(f"{Path(path).name}:{n}: bad synonym line (missing {missing or ['synonyms list']})")
            row["_origin"], row["_line"] = origin, f"{Path(path).name}:{n}"
            rows.append(row)
    return rows


def _merge(rows: list[dict]) -> tuple[list[SynonymEntry], dict]:
    groups: dict[tuple, dict] = {}
    words = 0
    for row in rows:
        official = str(row["official"])
        okey = bkey(official) or index_key(official)
        for term in row["synonyms"]:
            term = str(term)
            tkey = index_key(term)
            if not tkey:
                continue
            words += 1
            gk = (row["_origin"], row["source"], tkey, okey)
            g = groups.setdefault(gk, {"term": term, "officials": [], "kinds": set(), "status": set(),
                                       "ambiguous_source": False, "negatives": set(), "refs": []})
            if official not in g["officials"]:
                g["officials"].append(official)
            g["kinds"].add(str(row.get("kind") or ""))
            g["status"].add(str(row["status"]))
            g["ambiguous_source"] = g["ambiguous_source"] or bool(row.get("ambiguous"))
            g["negatives"].update(str(x) for x in row.get("negatives") or [])
            g["refs"].append(str(row.get("source_ref") or row["_line"]))
    keys_by_word: dict[str, set[str]] = collections.defaultdict(set)
    for (_origin, _source, tkey, okey) in groups:
        keys_by_word[tkey].add(okey)
    entries = []
    for (origin, source, tkey, okey), g in groups.items():
        ambiguous = len(keys_by_word[tkey]) >= 2 or len(tkey) == 1
        status = "approved" if g["status"] == {"approved"} else sorted(g["status"] - {"approved"} or {"draft"})[0]
        entries.append(SynonymEntry(g["term"], tkey, tuple(g["officials"]), okey, tuple(sorted(g["kinds"] - {""})),
                                    source, status, origin, ambiguous, g["ambiguous_source"],
                                    tuple(sorted(g["negatives"])), tuple(g["refs"])))
    src_flag: dict[str, bool] = collections.defaultdict(bool)
    for e in entries:
        src_flag[e.term_key] |= e.ambiguous_source
    now = {e.term_key: e.ambiguous for e in entries}
    counts = {
        "words": words,
        "entries": len(entries),
        "distinct_words": len(keys_by_word),
        "ambiguous_words_source": sum(1 for v in src_flag.values() if v),
        "ambiguous_words_now": sum(1 for v in now.values() if v),
        "ambiguous_resolved_by_merge": sum(1 for k, v in src_flag.items() if v and not now[k]),
        "ambiguous_new": sum(1 for k, v in now.items() if v and not src_flag[k]),
    }
    return entries, counts


def load_synonyms(approved_path: Path | None = None, external_path: Path | None = None,
                  include_external: bool = True) -> SynonymDict:
    approved_path = Path(approved_path or paths.SYNONYMS_APPROVED)
    external_path = Path(external_path or paths.EXTERNAL_SYNONYMS)
    approved_rows = _read_lines(approved_path, "approved") if approved_path.exists() else []
    external_present = external_path.exists()
    external_rows = _read_lines(external_path, "external") if (include_external and external_present) else []
    entries, counts = _merge(approved_rows + external_rows)
    counts.update({
        "approved_lines": len(approved_rows),
        "approved_lines_not_approved": sum(1 for r in approved_rows if r["status"] != "approved"),
        "approved_entries": sum(1 for e in entries if e.origin == "approved" and e.status == "approved"),
        "external_lines": len(external_rows),
        "external_entries": sum(1 for e in entries if e.origin == "external"),
    })
    return SynonymDict(entries, include_external, external_present, counts)
