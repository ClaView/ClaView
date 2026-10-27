"""Model decisions fed back into the judgement table (plan 06; user decision 10/19).

The model step does not edit settled requirements. Each choice becomes an override of 2.5, keyed by positions in
the question (stable across readings of the same question), and judge_fragments (the judgement table and the
certainty checks) and settle_requirements (2.7) run again with the overrides fixed. ``None`` or an empty
``Overrides`` is today's rule path exactly.

- ``names``: a name token's catalog name, the model's choice among the code's candidates (check 1 reads it as the
  name of the words);
- ``roles``: a name token read as an indicator (the catalog holds it on an indicator axis and as a target value);
- ``groups``: per item group (its start): ``reading`` "one" (one requirement over ``head`` with the other indicator
  items as breakdown), "split" (one requirement per lower item of a head + lower draft), "copy" (copies of one
  indicator per combination of basis values); ``scope`` "all" / "first" / "separate" over the targets ``x``;
- ``drafts``: per draft (the start of its indicator token): ``appositive`` "a" / "b" ("A, 즉 B"), ``gloss`` "a" / "b"
  / "keep" ("A(B)"): the appositive is resolved;
- ``attach``: an unused name token attached to a draft as a target or a breakdown item;
- ``places``: a condition's place: the draft whose indicator token starts there, or "common";
- ``ignored``: leftover words the model read as request or filler words (check 5 leaves them out);
- ``structure_confirmed``: every confirm point of check 8 was answered with a reading: check 8 does not apply.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from ..catalog.relations import IND_TYPES, TGT_TYPES, relations_of
from ..catalog.text import index_key
from .reading import Term, term_of


@dataclass
class Overrides:
    names: dict = field(default_factory=dict)  # (start, end) -> catalog name
    roles: dict = field(default_factory=dict)  # (start, end) -> "indicator"
    groups: dict = field(default_factory=dict)  # group start -> {"reading", "head", "scope", "x"}
    drafts: dict = field(default_factory=dict)  # indicator token start -> {"appositive" | "gloss": ...}
    attach: list = field(default_factory=list)  # ((start, end), indicator token start, "target" | "breakdown")
    places: dict = field(default_factory=dict)  # condition ID -> indicator token start, or "common"
    ignored: set = field(default_factory=set)
    structure_confirmed: bool = False

    def empty(self) -> bool:
        return not (self.names or self.roles or self.groups or self.drafts or self.attach or self.places
                    or self.ignored or self.structure_confirmed)


def role_tables(term: Term, layer) -> tuple[set, set]:
    """(tables reading the name as an indicator: an indicator-type candidate, a table name or a value of an
    indicator-role axis; tables reading it as a target: a target-type candidate or a value of another known axis)."""
    ind = {t for c in term.cands if c.get("type") in IND_TYPES for t in c.get("table_ids") or []}
    tgt = {t for c in term.cands if c.get("type") in TGT_TYPES for t in c.get("table_ids") or []}
    if layer is not None:
        rel = relations_of(layer)
        for t, obj, _item in rel.occurrences(term.key):
            role = "indicator" if obj is None else rel.axis_roles.get((t, obj), "unknown")
            if role == "indicator":
                ind.add(t)
            elif role != "unknown":
                tgt.add(t)
    return ind, tgt


def chosen_term(tok, layer, name: str) -> Term | None:
    """The term of a name token read as the catalog name the model chose (its own candidates, exact)."""
    if layer is None:
        return None
    base = term_of(tok, layer)
    found = layer.name_spans().lookup(name)
    cands = [c for c in found if c.get("name") == name] or found
    if not cands:
        return None
    types = {c["type"] for c in cands}
    ind, tgt = types & IND_TYPES, types & TGT_TYPES
    if types <= {"basis"}:
        rep = "basis"
    elif ind or "unknown" in types:
        rep = "indicator"
    elif tgt:
        rep = "target"
    else:
        rep = "indicator"
    term = Term(tok=tok, key=index_key(name), cands=cands, types=types, rep=rep, exact=True,
                mixed=bool(ind or "unknown" in types) and bool(tgt), weak=False,
                unknown_type=bool(types) and types <= {"unknown"},
                tables={t for c in cands for t in c.get("table_ids") or []},
                series=any(c.get("kind") == "table" for c in cands), family=base.family)
    term.model_chosen = name
    return term


def as_indicator(tok, layer) -> Term | None:
    """The term of a name token read as an indicator over its indicator tables (the catalog name stays its own)."""
    if layer is None:
        return None
    base = term_of(tok, layer)
    ind, _tgt = role_tables(base, layer)
    if not ind:
        return None
    term = dataclasses.replace(base, rep="indicator", types={"indicator"}, mixed=False, unknown_type=False,
                               tables=set(ind))
    term.role_read = True  # its indicator tables are the role tables (grounding reads them, not the candidates)
    return term


def indicator_term(tok, layer) -> Term | None:
    """The term of a name token read as an indicator (the model's role choice), over its indicator tables."""
    term = as_indicator(tok, layer)
    if term is None:
        return None
    term.model_chosen = _catalog_name_of(term_of(tok, layer))
    return term


def _catalog_name_of(term: Term) -> str:
    names = list(dict.fromkeys(c["name"] for c in term.cands if c.get("name")))
    return names[0] if len(names) == 1 else term.tok.quote


def apply_terms(reading, layer, ov: Overrides | None) -> None:
    """Fix the terms of the overridden name tokens before the items are read (term_of returns the fixed term)."""
    if ov is None or not (ov.names or ov.roles):
        return
    for tok in reading.toks:
        if tok.kind != "name":
            continue
        span = (tok.start, tok.end)
        term = None
        if span in ov.names:
            term = chosen_term(tok, layer, ov.names[span])
        elif ov.roles.get(span) == "indicator":
            term = indicator_term(tok, layer)
        if term is not None:
            tok._term = term


__all__ = ["Overrides", "apply_terms", "as_indicator", "chosen_term", "indicator_term", "role_tables"]
