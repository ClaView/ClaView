"""Condition fields, period diagnosis and the inferred frequency hint (plan 15 r3 step 4: 7.4, 7.5, appendix A 1-2).

They are record fields (``Analysis.decomposition.condition_fields`` and ``.inferred``, ``fields_of``): the conditions
and requirements of the rule path are left as they were (existing expectations compare them whole).

``condition_fields``: per condition ``{source, confirmed, applied[, period_diagnosis]}`` from the settled requirements:

- ``source``: ``question`` (every condition of 2.2 is written in the question, whoever attributed it; ``user`` is for
  conditions a later answer sets). Not ``attrs.source`` (the extraction method).
- ``confirmed`` (``data/decompose_policy.json`` ``condition_fields``): the attribution is settled (own, shared or
  common of some requirement and ambiguous nowhere), no failed source names the condition, and its kind passes its
  check: a period the shared period resolver (``claview_a_legacy.period_core``) supports with a reference-date probe;
  a frequency code; a basis value with a polarity; a unit value; a breakdown over a name mention
  with catalog candidates; an exclusion text. Calculation, relation, function and display conditions are never
  confirmed by their position. ``confirmed`` is no table lookup and no query result.
- ``applied``: None (B fills it).
- ``period_diagnosis`` (period conditions only): ``malformed`` (a unit value out of range, or a range whose ends
  resolve but run backwards), ``ok`` (the resolver resolves it with the reference date), else ``unsupported`` (the
  resolver does not support the expression). The extraction status ``unresolved`` is not mapped to it directly.

``inferred_hints``: per requirement ``[{kind: frequency, value, from_condition_id, rule: period_unit}]`` only when the
requirement has no frequency condition (own, shared or ambiguous) and the question no unattributed one, it has no
ambiguous period, no malformed period (an invalid unit, a backwards range), and every unit of its periods is one and
the same month or quarter unit; ``[]`` otherwise. A hint is never a condition, frequency evidence or a table-bundle split.

The reference date: the one given (the request's fixed date), else the probe date of 2.2 (``attrs.extract_status``
is read "as if a reference date were known" with it too).
"""

from __future__ import annotations

from typing import get_args

from ..contract.shapes import FrequencyCode, InferredHint
from .conditions import _PROBE_AS_OF
from .decompose_payload import POLICY

DEFAULT_AS_OF = _PROBE_AS_OF
_FIELDS = POLICY["condition_fields"]
CHECKED = frozenset(_FIELDS["checked_kinds"])
NEVER = frozenset(_FIELDS["never_confirmed"])
CATALOG_KINDS = frozenset(_FIELDS["catalog_kinds"])
_HINT = POLICY["inferred_frequency"]
UNIT_CODES: dict[str, str] = dict(_HINT["unit_codes"])
HINT_VALUES = frozenset(_HINT["hint_values"])
FREQUENCY_CODES = frozenset(get_args(FrequencyCode))


def period_diagnosis(attrs: dict, as_of: str | None = None) -> str:
    from ..period import diagnose_period

    return diagnose_period(attrs, as_of or DEFAULT_AS_OF)


def period_units(attrs: dict) -> set[str]:
    """Frequency codes of the units a period is written in (a recent unit, or the sub-unit of each end)."""
    if attrs.get("form") == "recent":
        unit = attrs.get("unit")
        return {UNIT_CODES.get(unit, unit)} if unit else set()
    out = set()
    for expr in (attrs.get("start_expr"), attrs.get("end_expr")):
        if expr:
            sub = expr.get("sub_unit")
            out.add(UNIT_CODES.get(sub, sub) if sub else UNIT_CODES["year"])
    return out


def _requirements(interpretation: dict | None) -> list[dict]:
    return list((interpretation or {}).get("requirements") or [])


def attributed_ids(interpretation: dict | None) -> tuple[set[str], set[str]]:
    """(attributed, ambiguous) condition IDs over every requirement."""
    attributed = set((interpretation or {}).get("common_condition_ids") or [])
    ambiguous: set[str] = set()
    for r in _requirements(interpretation):
        attributed |= set(r.get("condition_ids") or []) | set(r.get("shared_condition_ids") or [])
        ambiguous |= set(r.get("ambiguous_condition_ids") or [])
    return attributed, ambiguous


def _over_catalog_name(c: dict, segments: list[dict], mentions: list[dict]) -> bool:
    spans = {s["segment_id"]: s["span"] for s in segments}
    a, b = c["span"]
    for m in mentions:
        if m["kind"] != "name" or not m["candidates"]:
            continue
        lo, hi = spans[m["segment_ids"][0]][0], spans[m["segment_ids"][-1]][1]
        if lo < b and a < hi:
            return True
    return False


def _kind_ok(c: dict, as_of: str, segments: list[dict], mentions: list[dict]) -> bool:
    kind, attrs = c["kind"], c.get("attrs") or {}
    if kind == "period":
        return period_diagnosis(attrs, as_of) == "ok"
    if kind == "frequency":
        return (attrs.get("value") or c.get("value")) in FREQUENCY_CODES
    if kind == "basis":
        return bool(attrs.get("value") or c.get("value")) and attrs.get("polarity") in ("+", "-")
    if kind == "unit":
        return bool(c.get("value") or attrs.get("value"))
    if kind == "exclude":
        return bool(attrs.get("text") or c.get("value"))
    if kind in CATALOG_KINDS:
        return _over_catalog_name(c, segments, mentions)
    return bool(c.get("value"))


def condition_fields(conditions: list[dict], interpretation: dict | None, judgement: dict | None,
                     segments: list[dict] | None = None, mentions: list[dict] | None = None,
                     as_of: str | None = None) -> dict[str, dict]:
    """Condition ID -> {source, confirmed, applied[, period_diagnosis]} (module docstring)."""
    as_of = as_of or DEFAULT_AS_OF
    segments, mentions = segments or [], mentions or []
    attributed, ambiguous = attributed_ids(interpretation)
    failed = {cid for row in (judgement or {}).get("failed_sources") or [] for cid in row.get("condition_ids") or []}
    out: dict[str, dict] = {}
    for c in conditions:
        kind, cid = c["kind"], c["id"]
        settled = cid in attributed and cid not in ambiguous and cid not in failed
        confirmed = kind in CHECKED and kind not in NEVER and settled and _kind_ok(c, as_of, segments, mentions)
        row = {"source": "question", "confirmed": confirmed, "applied": None}
        if kind == "period":
            row["period_diagnosis"] = period_diagnosis(c.get("attrs") or {}, as_of)
        out[cid] = row
    return out


def inferred_hints(req: dict, conditions: list[dict], interpretation: dict | None,
                   as_of: str | None = None) -> list[InferredHint]:
    """The inferred frequency hint of one requirement (module docstring)."""
    by_id = {c["id"]: c for c in conditions}
    amb = set(req.get("ambiguous_condition_ids") or [])
    counted = (set(req.get("condition_ids") or []) | set(req.get("shared_condition_ids") or [])) - amb
    attributed, ambiguous = attributed_ids(interpretation)
    freq = [c for c in conditions if c["kind"] == "frequency"]
    if any(c["id"] in counted | amb or c["id"] not in attributed | ambiguous for c in freq):
        return []  # an explicit frequency wins (an unattributed one may be this requirement's)
    if any(by_id[x]["kind"] == "period" for x in amb if x in by_id):
        return []
    periods = [c for c in conditions if c["kind"] == "period" and c["id"] in counted]
    if not periods or any(period_diagnosis(c.get("attrs") or {}, as_of) == "malformed" for c in periods):
        return []
    units: set[str] = set()
    for c in periods:
        got = period_units(c.get("attrs") or {})
        if not got:
            return []
        units |= got
    if len(units) != 1:
        return []
    (unit,) = units
    if unit not in HINT_VALUES:
        return []
    return [{"kind": "frequency", "value": unit, "from_condition_id": periods[0]["id"], "rule": "period_unit"}]


def fields_of(analysis: dict, as_of: str | None = None) -> dict:
    """``{condition_fields, inferred}`` of an analysis (the record fields of step 4): condition ID -> fields and
    requirement ID -> hint. The analysis is not changed (its conditions and requirements stay as they are)."""
    interp = analysis.get("interpretation")
    return {"condition_fields": condition_fields(analysis["conditions"], interp, analysis.get("judgement"),
                                                 analysis.get("segments"), analysis.get("mentions"), as_of),
            "inferred": {r["id"]: inferred_hints(r, analysis["conditions"], interp, as_of)
                         for r in _requirements(interp)}}
