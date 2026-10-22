"""attach_terms (detail 2.4, plan 04 2-1 and 2-5): catalog terms of every fragment.

Paths in order: out-of-scope list -> approved dictionary entries -> exact catalog names (word-boundary name spans,
year-range-free names included) -> for a fragment that got none of these, the 2-gram lexical search (top 5). Each
term carries its type (series name, indicator, target, basis, total header, compound, out of scope, unknown) and the
tables it comes from. Family qualifiers are basis terms of the tables whose names hold them. The catalog relations
between terms (table name + value, upper/lower, other axis, family qualifier) and the conditions inside a fragment
are read by judge_fragments from the same reading of the question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..catalog.relations import relations_of
from ..contract.shapes import Condition, Fragment, Term, TextView
from .phrases import key_of, read_question, split_ending

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer


def _exact(c: dict) -> bool:
    return c.get("path") == "exact" or c.get("variant", "").split(":")[-1] in ("no_marks", "no_footnote",
                                                                                 "no_year_range")


def _grouped(fid: str, cands: list[dict], path_of, status_of, score=None) -> list[Term]:
    groups: dict[tuple, dict] = {}
    for c in cands:
        k = (c.get("type") or "unknown", c.get("name") or "", path_of(c))
        g = groups.setdefault(k, {"tables": [], "objs": set(), "names": set(), "status": status_of(c)})
        g["tables"] += [t for t in c.get("table_ids", []) if t not in g["tables"]]
        g["objs"].add((c.get("obj_id"), c.get("obj_name")))
    out: list[Term] = []
    for (type_, name, path), g in groups.items():
        obj = next(iter(g["objs"])) if len(g["objs"]) == 1 else (None, None)
        out.append({"fragment_id": fid, "name": name, "type": type_, "table_ids": sorted(g["tables"]),
                    "obj_id": obj[0], "obj_name": obj[1], "path": path, "status": g["status"], "score": score})
    return out


def attach_terms(fragments: list[Fragment], layer: CatalogLayer | None, text: TextView | None = None,
                 conditions: list[Condition] | None = None) -> list[Term]:
    """Terms of the fragments in fragment order. Without a catalog layer there are none. ``text`` and
    ``conditions`` give the reading of the question the fragments came from (the pipeline passes them); without them
    the names are looked up in each fragment quote alone."""
    if layer is None or not fragments:
        return []
    rel = relations_of(layer)
    spans = layer.name_spans()
    toks_by_frag: dict[str, list] = {}
    if text is not None and conditions is not None:
        reading = read_question(text, conditions, layer)
        for f in fragments:
            a, b = f["span"]
            toks_by_frag[f["id"]] = [t for t in reading.toks if a <= t.start and t.end <= b]
    out: list[Term] = []
    for f in fragments:
        fid = f["id"]
        if fid in toks_by_frag:
            names = [(t.lookup or t.quote, t.kind, t.lookup is not None) for t in toks_by_frag[fid]
                     if t.kind in ("name", "qual") and not t.negated and not t.alias]
            alias = [t for t in toks_by_frag[fid] if t.kind == "name" and t.alias and not t.negated]
            words = [t for t in toks_by_frag[fid] if t.kind == "word" and t.label == "unknown"]
        else:
            names = [(s["quote"], "name", False) for s in spans.find(f["quote"])]
            alias, words = [], []
        found: list[Term] = []
        for t in alias:  # plan 05 H: a table head alias, a catalog guess (never exact)
            found += _grouped(fid, t.alias, lambda c: "lexical", lambda c: "draft")
        for w in words:  # out-of-scope list and approved dictionary entries (2.4 order)
            stem = w.stem
            cands = layer.index.lookup(stem)
            oos = [c for c in cands if c.get("type") == "out_of_scope"]
            dic = [c for c in cands if c.get("path") == "dictionary" and c.get("status") == "approved"]
            found += _grouped(fid, oos, lambda c: "exact", lambda c: c.get("status", "draft"))
            found += _grouped(fid, dic, lambda c: "dictionary", lambda c: "approved")
        for quote, kind, completed in names:
            if kind == "qual":
                k = key_of(quote)
                found.append({"fragment_id": fid, "name": quote, "type": "basis",
                              "table_ids": sorted(rel.qualifier_tables.get(k, ())), "obj_id": None, "obj_name": None,
                              "path": "exact", "status": "approved", "score": None})
                continue
            cands = spans.lookup(quote)
            if completed:  # a list member completed by the 2.2 list rules: a catalog guess, not an exact name
                found += _grouped(fid, cands, lambda c: "lexical", lambda c: "draft")
                continue
            found += _grouped(fid, cands, lambda c: "exact" if _exact(c) else "lexical",
                              lambda c: "approved" if _exact(c) else "draft")
        if not found and (words or fid not in toks_by_frag):  # lexical only for unnamed words
            phrase = "".join(w.stem for w in words) if words else split_ending(key_of(f["quote"]))[0]
            for k, score in rel.lexical(phrase):
                found += _grouped(fid, spans.lookup(k) or layer.index.lookup(k), lambda c: "lexical",
                                  lambda c: "draft", score=score)
        out += found
    return out
