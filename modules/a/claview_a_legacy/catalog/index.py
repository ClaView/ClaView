"""Term index: normalised catalog name -> every candidate with that name, and name-preserving spans in a text.

Indexed names
- tables: full name (catalog title), metadata name when it differs, name before the first parenthesis, English name
  -> type ``series_name``; tables that share a name are one candidate with several ``table_ids``
- classification values: Korean name and English name -> the value type from the axis-role table (``unknown``
  without one)
- optional: approved/draft synonyms (``path = dictionary``) and out-of-scope terms (type ``out_of_scope``)

- plan 09 P1: every catalog key holding a mark of ``names.NAME_MARKS`` ("/", ",", middle dots) is also indexed
  without them (same variant and path, so an exact name stays exact), and the derived names of
  ``names.derived_names`` are indexed with path ``lexical`` (one candidate per derived table name; a derived value
  name keeps its value). Neither adds a candidate to a key the catalog's own names hold; ``derived_keys`` lists the
  keys they add (question interpretation reads the catalog's own keys without them)
- plan 14 I8: every catalog key is also indexed under its joiner keys (``names.joiner_keys``: the and-word left out
  or written as a list joiner particle, a list mark written as the and-word or a joiner particle) exactly as the
  mark-free keys are (same candidate, variant and path; never on a key the catalog's own names hold; in
  ``derived_keys``)

Keys are ``text.index_key`` (NFKC, lower case, whitespace removed). Each name is indexed under its variants
(``text.name_variants``): the name itself, footnote removed, marks removed, parentheses removed. ``path`` is ``exact``
for the name itself, the footnote-free form and the English name, ``lexical`` for the marks-free and parenthesis-free
forms. Several candidates under one key are all kept.

``find_spans`` finds the parts of a text that are exactly a catalog name (longest match first, no overlap) and
returns their positions in the original text; plan 3-1 split_fragments uses them as name-preserving spans. Span keys
are catalog names only (no synonyms), at least two characters long and with at least one letter (a bare number such
as a year value is not a name to preserve).
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from ..reused.q_nodes import normalize
from .loader import Catalog
from .names import DERIVED_VARIANTS, derived_names, joiner_keys, unmarked_key
from .roles import AxisRoleTable
from .text import index_key, name_variants

MIN_SPAN_CHARS = 2
_SUB_ORDER = ("name", "no_footnote", "no_marks", "no_paren")
_BASE_FIELDS = frozenset({"key", "name", "type", "table_ids", "obj_id", "obj_name", "item_id", "path", "variant",
                          "kind", "status"})


@dataclass
class TermIndex:
    entries: dict[str, list[dict]]
    span_keys: frozenset[str]  # catalog-name keys usable as name-preserving spans
    stats: dict = field(default_factory=dict)
    span_lengths: tuple[int, ...] = ()
    derived_keys: frozenset[str] = frozenset()  # plan 09 P1: keys only derived names and mark-free forms hold

    def __post_init__(self):
        if not self.span_lengths:
            self.span_lengths = tuple(sorted({len(k) for k in self.span_keys}, reverse=True))

    def lookup(self, text: str) -> list[dict]:
        """Candidates whose indexed key equals the key of ``text`` (copies; callers may change them)."""
        return [dict(c, table_ids=list(c["table_ids"])) for c in self.entries.get(index_key(text), [])]

    def find_spans(self, text: str, min_chars: int = MIN_SPAN_CHARS) -> list[dict]:
        """Exact catalog-name spans in ``text``: longest first, no overlap, positions in the original text.

        Matching ignores whitespace, case and NFKC differences. Each span: {start, end, quote, key, candidates}
        where ``text[start:end] == quote``.
        """
        norm = normalize(text)
        chars: list[str] = []
        origin: list[int] = []
        for k, ch in enumerate(norm["query"]):
            if ch.isspace():
                continue
            for low in ch.lower():
                chars.append(low)
                origin.append(norm["offsets"][k])
        flat = "".join(chars)
        lengths = [n for n in self.span_lengths if n >= min_chars]
        found = []
        for i in range(len(flat)):
            for n in lengths:
                if i + n <= len(flat) and flat[i:i + n] in self.span_keys:
                    found.append((i, i + n))
        found.sort(key=lambda se: (-(se[1] - se[0]), se[0]))
        taken = [False] * len(flat)
        chosen = []
        for i, j in found:
            if any(taken[i:j]):
                continue
            for p in range(i, j):
                taken[p] = True
            chosen.append((i, j))
        out = []
        for i, j in sorted(chosen):
            start, end = origin[i], origin[j - 1] + 1
            k = flat[i:j]
            out.append({"start": start, "end": end, "quote": text[start:end], "key": k,
                        "candidates": len(self.entries.get(k, []))})
        return out


class _Collector:
    def __init__(self):
        self.groups: dict[tuple, dict] = {}

    def add(self, k: str, *, name, type_, table_ids, obj_id, obj_name, item_id, variant, kind, path, status="draft",
            **extra) -> dict:
        gk = (k, kind, name, type_, obj_id, obj_name, item_id, variant, path, status, tuple(sorted(extra.items())))
        cand = self.groups.get(gk)
        if cand is None:
            cand = {"key": k, "name": name, "type": type_, "table_ids": [], "obj_id": obj_id, "obj_name": obj_name,
                    "item_id": item_id, "path": path, "variant": variant, "kind": kind, "status": status, **extra}
            self.groups[gk] = cand
        for table_id in table_ids:
            if table_id not in cand["table_ids"]:
                cand["table_ids"].append(table_id)
        return cand

    def entries(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = collections.defaultdict(list)
        for cand in self.groups.values():
            cand["table_ids"].sort()
            out[cand["key"]].append(cand)
        return dict(out)


def _path(sub: str) -> str:
    return "exact" if sub in ("name", "no_footnote") else "lexical"


def build_index(catalog: Catalog, roles: AxisRoleTable | None = None, synonyms=None, out_of_scope=None) -> TermIndex:
    """Build the term index. ``synonyms`` is a ``synonyms.SynonymDict`` (only its usable entries are added) and
    ``out_of_scope`` an ``out_of_scope.OutOfScopeList``; both are optional."""
    col = _Collector()
    for table in catalog.tables.values():
        names = [("table_name", table.name), ("meta_name", table.meta_name), ("table_front", table.name_front),
                 ("table_eng", table.name_eng)]
        forms = [(_SUB_ORDER.index(sub), variant, name, sub, k)
                 for variant, name in names if name for sub, k in name_variants(name).items()]
        seen: set[str] = set()
        for _order, variant, name, sub, k in sorted(forms, key=lambda f: f[0]):  # stable: the name forms first
            if k in seen:
                continue
            seen.add(k)
            col.add(k, name=name, type_="series_name", table_ids=[table.table_id], obj_id=None, obj_name=None,
                    item_id=None, variant=variant if sub == "name" else f"{variant}:{sub}", kind="table",
                    path=_path(sub))
    for axis in catalog.axes():
        for it in axis.items:
            vr = roles.value(it.table_id, it.obj_id, it.item_id) if roles else None
            type_ = vr.type if vr else "unknown"
            status = vr.status if vr else "draft"
            for name, eng in ((it.name, False), (it.name_eng, True)):
                if not name:
                    continue
                for sub, k in name_variants(name).items():
                    variant = ("eng" if sub == "name" else f"eng:{sub}") if eng else sub
                    col.add(k, name=name, type_=type_, table_ids=[it.table_id], obj_id=axis.obj_id,
                            obj_name=axis.name, item_id=it.item_id, variant=variant, kind="value", status=status,
                            path=_path(sub))
    derived_keys = _add_derived(col, catalog, roles)
    catalog_entries = {k: [dict(c, table_ids=sorted(c["table_ids"])) for c in cands]
                       for k, cands in col.entries().items()}
    span_keys = frozenset(k for k in catalog_entries if len(k) >= MIN_SPAN_CHARS and any(ch.isalpha() for ch in k))

    if synonyms is not None:
        for entry in synonyms.usable():
            for official in entry.officials:
                for cand in catalog_entries.get(index_key(official), []):
                    col.add(entry.term_key, name=cand["name"], type_=cand["type"], table_ids=cand["table_ids"],
                            obj_id=cand["obj_id"], obj_name=cand["obj_name"], item_id=cand["item_id"],
                            variant="synonym", kind=cand["kind"], path="dictionary", status=entry.status,
                            synonym=entry.term, source=entry.source, ambiguous=entry.ambiguous)
    if out_of_scope is not None:
        for entry in out_of_scope.entries:
            col.add(entry.term_key, name=entry.term, type_="out_of_scope", table_ids=[], obj_id=None, obj_name=None,
                    item_id=None, variant="out_of_scope", kind="out_of_scope", path="exact", status=entry.status,
                    source=entry.source, reason=entry.reason)
    entries = col.entries()
    stats = {
        "keys": len(entries),
        "candidates": sum(len(c) for c in entries.values()),
        "keys_with_several_candidates": sum(1 for cands in entries.values() if len(cands) > 1),
        "catalog_keys": len(catalog_entries),
        "span_keys": len(span_keys),
        "span_keys_with_space": 0,
        "synonym_candidates": sum(1 for cands in entries.values() for c in cands if c["path"] == "dictionary"),
        "out_of_scope_candidates": sum(1 for cands in entries.values() for c in cands if c["type"] == "out_of_scope"),
    }
    names_with_space = {k for k, cands in catalog_entries.items() if k in span_keys
                        and any(" " in (c["name"] or "") for c in cands if c["variant"] in ("name", "table_name"))}
    stats["span_keys_with_space"] = len(names_with_space)
    return TermIndex(entries=entries, span_keys=span_keys, stats=stats, derived_keys=frozenset(derived_keys))


def _two_names_joined(name: str, names: set[str]) -> bool:
    """The name holds "A 및 B" with a name key (the catalog's own or derived) on both sides of the and-word."""
    from ..interpret.lexicon import AND_WORD

    words = [w for w in name.split(" ") if w]
    return any(words[k] == AND_WORD and index_key(words[k - 1]) in names and index_key(words[k + 1]) in names
               for k in range(1, len(words) - 1))


def _add_derived(col: _Collector, catalog: Catalog, roles: AxisRoleTable | None) -> set[str]:
    """Plan 09 P1: the mark-free keys of the catalog names (same variant and path as the key they come from; plan 14
    I8: their joiner keys too) and the derived names of ``names.derived_names`` (path lexical; one candidate per
    derived table name, a value's derived name keeps the value). Keys of the catalog's own names get nothing. Returns the keys added."""
    own = {c["key"] for c in col.groups.values()}
    added: set[str] = set()
    for cand in list(col.groups.values()):
        # the mark-free key (plan 09 P1) and the joiner keys (plan 14 I8) of the catalog key
        for k in dict.fromkeys([u for u in (unmarked_key(cand["key"]),) if u] + joiner_keys(cand["key"])):
            if k in own:
                continue
            extra = {x: cand[x] for x in cand if x not in _BASE_FIELDS}
            col.add(k, name=cand["name"], type_=cand["type"], table_ids=cand["table_ids"], obj_id=cand["obj_id"],
                    obj_name=cand["obj_name"], item_id=cand["item_id"], variant=cand["variant"], kind=cand["kind"],
                    path=cand["path"], status=cand["status"], **extra)
            added.add(k)
    tables: dict[str, dict] = {}  # derived table name key -> {name, variants, table_ids}
    derived = derived_names(catalog, roles)
    names = own | {d.key for d in derived}
    for d in derived:
        if _two_names_joined(d.name, names):
            continue  # user decision 10/19 21:45 (DN5b): "A 및 B" of two names reads as a list, never one name
        keys = [d.key] + [u for u in (unmarked_key(d.key),) if u and u not in own]
        for k in keys:
            if k in own:
                continue
            if d.obj_id is None:
                row = tables.setdefault(k, {"name": d.name, "variants": [], "table_ids": []})
                if d.variant not in row["variants"]:
                    row["variants"].append(d.variant)
                if d.table_id not in row["table_ids"]:
                    row["table_ids"].append(d.table_id)
            else:
                vr = roles.value(d.table_id, d.obj_id, d.item_id) if roles else None
                col.add(k, name=d.name, type_=vr.type if vr else "unknown", table_ids=[d.table_id], obj_id=d.obj_id,
                        obj_name=d.obj_name, item_id=d.item_id, variant=d.variant, kind="value", path="lexical",
                        status="draft")
            added.add(k)
    for k, row in tables.items():
        variant = "+".join(v for v in DERIVED_VARIANTS if v in row["variants"])
        col.add(k, name=row["name"], type_="series_name", table_ids=row["table_ids"], obj_id=None, obj_name=None,
                item_id=None, variant=variant, kind="table", path="lexical")
    return added
