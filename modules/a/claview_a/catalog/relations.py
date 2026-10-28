"""Catalog relations read by question interpretation (plan 04, 2.4-2.5): name candidates, where a name sits in each
table, how two names relate in the tables they share, table families and their bracket qualifiers, and the 2-gram
name index of the lexical path.

- Occurrences: every catalog name key -> (table ID, axis ID, value ID) of each value with that name, and
  (table ID, None, None) of each table with that name. Keys are the term-index keys (``text.name_variants``) plus the
  year-range-free forms (decision 6).
- Readings of two names in one table: ``in_series`` (one is the table name, the other a value of the table),
  ``below`` / ``above`` (ancestor on the same axis), ``sibling`` (same axis, neither an ancestor), ``other_axis``.
- Families: tables whose names are the same once every bracket part is removed ("가상통화량(원계열)",
  "가상통화량(계절조정계열)"). The bracket parts of a family with two or more tables are its qualifiers (2-5 "가족
  수식어"); a qualifier word in a question is found with the same word-boundary rule as names.
- Lexical: catalog names indexed by character 2-grams; a phrase gets the names that share the most 2-grams (overlap
  coefficient), top 5 (2.4 ``lexical`` path, only for fragments without another term).

Built once per layer (``relations_of``). Nothing here names a table, case or question.
"""

from __future__ import annotations
import collections
import re
from dataclasses import dataclass, field
from .index import TermIndex
from .spans import NameSpanIndex, read_particles
from .text import index_key, name_variants, strip_parens, strip_year_range


_BRACKET = re.compile(r"\(([^()]*)\)")
_HAS_DIGIT = re.compile(r"\d")
LEXICAL_TOP = 5
LEXICAL_MIN = 0.5  # least share of the phrase's 2-grams a lexical candidate must have


def _bigrams(k: str) -> set[str]:
    return {k[i:i + 2] for i in range(len(k) - 1)} if len(k) >= 2 else ({k} if k else set())


def family_core(name: str) -> str:
    """Index key of a table name without any bracket part ("가상통화량(원계열)" -> 가상통화량)."""
    return index_key(strip_parens(name or ""))


@dataclass
class CatalogRelations:
    occ: dict[str, list[tuple[str, str | None, str | None]]]
    parents: dict[tuple[str, str], dict[str, str | None]]  # (table, axis) -> value ID -> parent value ID
    axis_roles: dict[tuple[str, str], str]
    family_tables: dict[str, list[str]]  # family core key -> table IDs (two or more)
    table_family: dict[str, str]  # table ID -> family core key (families of two or more tables only)
    qualifiers: dict[str, set[str]]  # qualifier key -> family core keys it qualifies
    qualifier_tables: dict[str, set[str]]  # qualifier key -> tables whose name holds it
    qualifier_spans: NameSpanIndex | None
    bigram_index: dict[str, set[str]]
    key_names: dict[str, str]  # name key -> one catalog name (display)
    key_bigrams: dict[str, set[str]] = field(default_factory=dict)
    table_names: dict[str, str] = field(default_factory=dict)  # table ID -> table name

    # ------------------------------------------------------------ places and readings

    def ancestors(self, table: str, obj: str, item: str) -> list[str]:
        parents = self.parents.get((table, obj), {})
        out, cur, seen = [], parents.get(item), {item}
        while cur is not None and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = parents.get(cur)
        return out

    def occurrences(self, key: str) -> list[tuple[str, str | None, str | None]]:
        return self.occ.get(key, [])

    def tables_of(self, key: str) -> set[str]:
        return {o[0] for o in self.occ.get(key, [])}

    def readings(self, child: str, head: str) -> dict[str, set[str]]:
        """Table ID -> readings of name ``child`` against name ``head`` in each table holding both."""
        out: dict[str, set[str]] = collections.defaultdict(set)
        for oc in self.occ.get(child, []):
            for oh in self.occ.get(head, []):
                if oc[0] != oh[0]:
                    continue
                t = oc[0]
                if oh[1] is None and oc[1] is not None:
                    out[t].add("in_series")
                elif oc[1] is None and oh[1] is not None:
                    out[t].add("series_of")
                elif oc[1] is None or oh[1] is None:
                    continue
                elif oc[1] != oh[1]:
                    out[t].add("other_axis")
                elif oc[2] == oh[2]:
                    out[t].add("same")
                elif oh[2] in self.ancestors(t, oc[1], oc[2]):
                    out[t].add("below")
                elif oc[2] in self.ancestors(t, oh[1], oh[2]):
                    out[t].add("above")
                else:
                    out[t].add("sibling")
        return dict(out)

    def axis_role_of(self, key: str) -> set[str]:
        return {self.axis_roles.get((o[0], o[1]), "unknown") for o in self.occ.get(key, []) if o[1] is not None}

    # ------------------------------------------------------------ families

    def family_of_key(self, key: str) -> str | None:
        """Family core of a name key when the name is (the bracket-free form of) a family's tables."""
        if key in self.family_tables:
            return key
        fams = {self.table_family.get(t) for t, obj, _ in self.occ.get(key, []) if obj is None}
        fams.discard(None)
        return next(iter(fams)) if len(fams) == 1 else None

    def qualifies(self, qualifier_key: str, family: str | None) -> bool:
        return family is not None and family in self.qualifiers.get(qualifier_key, set())

    def find_qualifiers(self, text: str) -> list[dict]:
        return self.qualifier_spans.find(text) if self.qualifier_spans is not None else []

    # ------------------------------------------------------------ lexical

    def lexical(self, phrase_key: str, top: int = LEXICAL_TOP) -> list[tuple[str, float]]:
        grams = _bigrams(phrase_key)
        if not grams:
            return []
        counts: collections.Counter = collections.Counter()
        for g in grams:
            for k in self.bigram_index.get(g, ()):
                counts[k] += 1
        scored = []
        for k, n in counts.items():
            score = n / min(len(grams), len(self.key_bigrams.get(k) or grams))
            if n / len(grams) >= LEXICAL_MIN:
                scored.append((k, round(score, 4)))
        scored.sort(key=lambda x: (-x[1], len(x[0]), x[0]))
        return scored[:top]


def build_relations(layer) -> CatalogRelations:
    catalog, roles = layer.catalog, layer.roles
    occ: dict[str, list] = collections.defaultdict(list)
    key_names: dict[str, str] = {}

    def add(name: str | None, place: tuple) -> None:
        if not name:
            return
        forms = list(name_variants(name).values())
        stripped = strip_year_range(name)
        if stripped:
            forms += list(name_variants(stripped).values())
        for k in dict.fromkeys(forms):
            if place not in occ[k]:
                occ[k].append(place)
            key_names.setdefault(k, name)

    parents: dict[tuple[str, str], dict[str, str | None]] = {}
    axis_roles: dict[tuple[str, str], str] = {}
    for table in catalog.tables.values():
        for name in (table.name, table.meta_name, table.name_front, table.name_eng):
            add(name, (table.table_id, None, None))
        for axis in table.axes:
            parents[(table.table_id, axis.obj_id)] = {it.item_id: it.parent_id for it in axis.items}
            ar = roles.axis(table.table_id, axis.obj_id)
            axis_roles[(table.table_id, axis.obj_id)] = ar.role if ar else "unknown"
            for it in axis.items:
                for name in (it.name, it.name_eng):
                    add(name, (table.table_id, axis.obj_id, it.item_id))
    # plan 09 P1: the derived names and mark-free keys of the term index (``names.derived_names``) sit where the
    # name they come from sits (a derived table name: in its tables; a value's: at that value)
    for k in sorted(layer.index.derived_keys):
        for c in layer.index.entries.get(k, []):
            for t in c["table_ids"]:
                place = (t, None, None) if c["kind"] == "table" else (t, c["obj_id"], c["item_id"])
                if place not in occ[k]:
                    occ[k].append(place)
            key_names.setdefault(k, c["name"])

    by_core: dict[str, list[str]] = collections.defaultdict(list)
    for table in catalog.tables.values():
        by_core[family_core(table.name)].append(table.table_id)
    family_tables = {core: tids for core, tids in by_core.items() if core and len(tids) >= 2}
    table_family = {t: core for core, tids in family_tables.items() for t in tids}
    qualifiers: dict[str, set[str]] = collections.defaultdict(set)
    qualifier_tables: dict[str, set[str]] = collections.defaultdict(set)
    for core, tids in family_tables.items():
        for t in tids:
            for m in _BRACKET.finditer(catalog.tables[t].name):
                for part in re.split(r"[,·]", m.group(1)):
                    part = part.strip()
                    k = index_key(part)
                    if len(k) >= 2 and not _HAS_DIGIT.search(k):
                        qualifiers[k].add(core)
                        qualifier_tables[k].add(t)
    qualifier_spans = None
    if qualifiers:
        entries = {k: [{"key": k, "name": k, "type": "basis", "table_ids": sorted(qualifier_tables[k]),
                        "obj_id": None, "obj_name": None, "item_id": None, "path": "exact", "variant": "qualifier",
                        "kind": "qualifier", "status": "draft"}] for k in qualifiers}
        qualifier_spans = NameSpanIndex(index=TermIndex(entries=entries, span_keys=frozenset(entries)),
                                        particles=read_particles())

    bigram_index: dict[str, set[str]] = collections.defaultdict(set)
    key_bigrams: dict[str, set[str]] = {}
    for k in layer.index.span_keys:
        grams = _bigrams(k)
        key_bigrams[k] = grams
        for g in grams:
            bigram_index[g].add(k)
    return CatalogRelations(occ=dict(occ), parents=parents, axis_roles=axis_roles, family_tables=family_tables,
                            table_family=table_family, qualifiers=dict(qualifiers),
                            qualifier_tables=dict(qualifier_tables), qualifier_spans=qualifier_spans,
                            bigram_index=dict(bigram_index), key_names=key_names, key_bigrams=key_bigrams,
                            table_names={t.table_id: t.name for t in catalog.tables.values()})


def relations_of(layer) -> CatalogRelations:
    """The relations of a catalog layer, built once and kept on the layer."""
    if "relations" not in layer._derived:
        layer._derived["relations"] = build_relations(layer)
    return layer._derived["relations"]
