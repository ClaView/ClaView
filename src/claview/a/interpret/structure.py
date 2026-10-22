"""Structure signature of a question and the verified-structure check (certainty check 8, decision 10/18).

A judgement that passes the seven checks of plan 04 2-3 is certain only when the structure of the question is one the
specification cases have verified (``data/verified_structures.json``, built by scripts/a/build_verified_structures.py
from the cases expected certain; signatures only, no case ID or question). Otherwise the judgement fails with
``unverified_structure`` and stays undecided. The policy file ``data/certainty_policy.json`` turns the check on or
off (off: the seven checks alone, as before); the environment variable ``CLAVIEW_V3_VERIFIED_STRUCTURES_ONLY``
(1/0) overrides the file for a run (tests, measurements).

The signature is made of general features of the reading, never of words or names:
- ``clauses``: item clauses (clauses with list items; split at sentence ends, request verbs and connective endings);
  ``other_clauses``: designation clauses and clauses without items;
- ``items``: list items per group, per item clause;
- ``phrases``: phrase structures read (relation and its kind, appositive, negation, topic, complement, modifier and
  its kind, combined list, period-cut list, designation, broad question);
- ``context``: a table name read as the context of a value; ``relation_operands``: requirements under each relation;
- ``rows``: judgement-table rows used; ``drafts``: number of requirement drafts;
- ``conditions``: kind (basis with its axis, the marker values named in the policy file) and place of every attributed
  condition, calc hint and marker: in an item, in front of the first item of its clause, behind it, or in another
  clause; ``+`` when the same kind and place occurs twice or more.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from .. import paths

POLICY_FILE = paths.PACKAGE_DATA_DIR / "certainty_policy.json"
VERIFIED_FILE = paths.PACKAGE_DATA_DIR / "verified_structures.json"
POLICY_ENV = "CLAVIEW_V3_VERIFIED_STRUCTURES_ONLY"
_ON = frozenset({"1", "true", "on", "yes"})
_OFF = frozenset({"0", "false", "off", "no"})


@lru_cache(maxsize=1)
def policy() -> dict:
    return json.loads(POLICY_FILE.read_text(encoding="utf-8"))


def verified_only() -> bool:
    """The policy of check 8: the environment variable when set (1/0), else the policy file."""
    env = (os.environ.get(POLICY_ENV) or "").strip().lower()
    if env in _ON:
        return True
    if env in _OFF:
        return False
    return bool(policy().get("verified_structures_only", False))


SPLIT_ENV = "CLAVIEW_V3_SPLIT_GROUNDING"


def split_grounding() -> bool:
    """The policy of the split grounding check (user decision 10/18): the environment variable when set (1/0), else
    the policy file (default on)."""
    env = (os.environ.get(SPLIT_ENV) or "").strip().lower()
    if env in _ON:
        return True
    if env in _OFF:
        return False
    return bool(policy().get("split_grounding", True))


@lru_cache(maxsize=1)
def _verified_cached(mtime: float) -> frozenset:
    doc = json.loads(VERIFIED_FILE.read_text(encoding="utf-8"))
    return frozenset(signature_key(s["signature"]) for s in doc.get("structures", []))


def verified_keys() -> frozenset:
    if not VERIFIED_FILE.exists():
        return frozenset()
    return _verified_cached(VERIFIED_FILE.stat().st_mtime)


def signature_key(sig: dict) -> str:
    return json.dumps(sig, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _place(p: int, clause_of, item_spans: list[tuple[int, int, int]], first_start: dict[int, int]) -> str:
    c = clause_of(p)
    if c is None:
        return "other"
    if any(a <= p < b for a, b, _ in item_spans):
        return "in"
    return "front" if p < first_start[c] else "back"


def signature(reading, j, conditions: list[dict]) -> dict:
    """The structure signature of one reading (``phrases.Reading``) and its judgement (``table.Judged``)."""
    from .table import clause_ranges  # the clause spans of 2-4 (item clause, other clause)

    cfg = policy().get("signature", {})
    markers = {k: set(v) for k, v in cfg.get("markers", {}).items()}
    kinds = set(cfg.get("condition_kinds", []))
    groups = reading.groups
    item_groups = [g for g in groups if not g.designation and g.items]
    item_clauses = sorted({g.clause for g in item_groups})
    other = sorted({g.clause for g in groups if g.clause not in item_clauses})
    ranges = clause_ranges(reading)

    def clause_of(p: int):
        return next((c for c in item_clauses if ranges[c][0] <= p < ranges[c][1]), None)

    item_spans = [(it.start, it.end, g.clause) for g in item_groups for it in g.items]
    first_start = {c: min(a for a, _, cl in item_spans if cl == c) for c in item_clauses}
    phrases = set()
    for g in groups:
        if g.relation is not None:
            phrases.add(f"relation:{g.relation}")
        if g.appositive:
            phrases.add("appositive")
        if g.complement:
            phrases.add("complement")
        if g.modifier:
            phrases.add(f"modifier:{g.modifier_kind}")
        if g.combined:
            phrases.add("combined")
        if g.by_period:
            phrases.add("by_period")
        if g.designation:
            phrases.add("designation")
        if any(it.phrase == "topic" for it in g.items):
            phrases.add("topic")
    if reading.negated:
        phrases.add("negation")
    if any(d.broad for d in j.drafts):
        phrases.add("broad")
    places: list[str] = []
    for c in conditions:
        if c["kind"] in kinds:
            label = f"{c['kind']}.{c['attrs'].get('axis')}" if c["kind"] == "basis" else c["kind"]
        elif c["kind"] in markers and c["attrs"].get(c["kind"]) in markers[c["kind"]]:
            label = f"{c['kind']}.{c['attrs'][c['kind']]}"
        else:
            continue
        places.append(f"{label}@{_place(c['span'][0], clause_of, item_spans, first_start)}")
    places += [f"hint@{_place(t.start, clause_of, item_spans, first_start)}" for t in reading.toks
               if t.kind == "hint"]
    counted = sorted({p + ("+" if places.count(p) >= 2 else "") for p in places})
    rows = {p[2] for p in j.pairs} | {d.row for d in j.drafts if d.row is not None}
    return {
        "clauses": len(item_clauses), "other_clauses": len(other),
        "items": [[len(g.items) for g in item_groups if g.clause == c] for c in item_clauses],
        "phrases": sorted(phrases),
        "context": any(d.context is not None or bool(d.context_name) for d in j.drafts),
        "relation_operands": sorted(len(r.drafts) for r, g in zip(j.results, groups) if g.relation is not None),
        "rows": sorted(rows), "drafts": len(j.drafts), "conditions": counted,
    }
