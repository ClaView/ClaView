"""The kept structure of an adopted model reading (plan 19 contract joint, instruction of 2026-10-21 13:06, items 1-5):
``decomposition.structure`` (``contract/shapes.DecompositionStructure``), and the same for a kept alternative in its
own requirement IDs. The settling keeps flattening as before (``Requirement.breakdown`` quote strings, the negated
spans of ``judgement.negated``); this record only keeps what the proposal said, read by the same rules.

- Every ref is a ``SelectionRef`` made by ``selection_ref`` -- the one projection the selection refs use too
  (``decompose_run``), over the verification's source-range reader (``decompose_refs.RefReader``): span, quote, the
  chosen sense's name and the candidate rows of the quoted range only (a subquote never takes the whole name's rows,
  a whole ref never an inner part's). A positive ref of a requirement therefore equals its ``selection_refs`` item.
- ``requirements``: per settled requirement its targets (filter values), breakdown groups (``group_id`` b1, b2, ...;
  the proposed ``kind``; axis; values, empty = values not specified; markers; ``descendants`` exactly for the kind
  ``descendants``: ``unexpanded`` with the axis and value refs as written, whose candidate rows are the parent rows --
  no hierarchy is expanded here), basis refs and context ref.
- ``polarity``: every proposed link as proposed (``link_id`` p1, p2, ...; kind; markers, negative, positive), its
  ``requirement_ids`` the proposal's own scope translated to settled IDs (empty stays empty: every requirement; the
  scope is never replaced by which requirement a ref belongs to) and ``scope_status``: ``uncertain`` when a
  verification finding about the polarity scope (``SCOPE_CODES``, a polarity ambiguity the model listed, or an
  unresolved binding of a condition holding the marker) lies on the link, else ``clear``.
- ``confirmed_effects``: held empty (user decision of 2026-10-21): always ``[]`` in this version, whatever the
  verification grounds say. The field stays in the contract; the polarity links (kind, markers, negative, positive,
  requirement_ids, scope_status) are kept as recorded but are no confirmed refutation, and no attribution is judged
  here. The final check rejects a non-empty list (``check._structure_problems``).

Nothing here reads the catalog, adds a grammar rule or changes the settled result.
"""

from __future__ import annotations

import copy

from ..contract.shapes import STRUCTURE_VERSION
from .decompose_settle import Question

TARGET, BASIS, CONTEXT = "target", "basis", "context"
AXIS, VALUE, MARKER = "breakdown_axis", "breakdown_value", "breakdown_marker"
POL_MARKER, POL_NEGATIVE, POL_POSITIVE = "polarity_marker", "polarity_negative", "polarity_positive"
DESCENDANTS, UNEXPANDED = "descendants", "unexpanded"
CLEAR, UNCERTAIN = "clear", "uncertain"
STRUCTURE_UNRECORDED = "rules_structure_unrecorded"  # shapes.StructureReason: rules adopted or a fallback
# verification findings about the scope of a polarity marker (decompose_verify, 6.2)
SCOPE_CODES = frozenset({"unclear_scope", "polarity_incomplete", "polarity_unattached"})
AMBIGUITY, POLARITY_AMBIGUITY = "ambiguity", "polarity:"
CONDITION_UNRESOLVED = "condition_unresolved"


def selection_ref(q: Question, place: str, ref: dict) -> dict:
    """One ref projected for the selection (``shapes.SelectionRef``): the place, the segments, the span and quote read
    by ``RefReader``, the chosen sense's catalog name and those of its rows that are candidate rows of the quoted range
    (no chosen sense: no name, no rows)."""
    sense = q.senses.get(ref["sense_id"]) if ref["sense_id"] else None
    local_rows = q.candidate_rows(ref)  # plan 19 stage 3: the rows of the quoted range only (decompose_refs)
    return {"place": place, "segment_ids": list(ref["segment_ids"]), "span": q.span(ref), "quote": q.quote(ref),
            "name": sense["name"] if sense else None,
            "candidates": [dict(row) for row in sense["rows"] if row in local_rows] if sense else []}


def _breakdown(q: Question, n: int, b: dict) -> dict:
    axis = selection_ref(q, AXIS, b["axis"]) if b["axis"] is not None else None
    values = [selection_ref(q, VALUE, x) for x in b["values"]]
    descendants = None
    if b["kind"] == DESCENDANTS:
        descendants = {"status": UNEXPANDED,
                       "source_refs": copy.deepcopy(([axis] if axis is not None else []) + values)}
    return {"group_id": f"b{n}", "kind": b["kind"], "axis": axis, "values": values,
            "markers": [selection_ref(q, MARKER, x) for x in b["marker_refs"]], "descendants": descendants}


def _requirement(q: Question, r: dict) -> dict:
    return {"targets": [selection_ref(q, TARGET, x) for x in r["targets"]],
            "breakdowns": [_breakdown(q, n, b) for n, b in enumerate(r["breakdowns"], start=1)],
            "basis": [selection_ref(q, BASIS, x) for x in r["basis_refs"]],
            "context": selection_ref(q, CONTEXT, r["context"]) if r["context"] is not None else None}


def _link_segments(p: dict, fields=("marker_refs", "negative_refs", "positive_refs")) -> set[str]:
    return {s for f in fields for ref in p[f] for s in ref["segment_ids"]}


def _marker_conditions(q: Question, p: dict) -> set[str]:
    msegs = _link_segments(p, ("marker_refs",))
    return {cid for cid, segs in q.cond_segs.items() if set(segs) & msegs}


def scope_status(q: Question, p: dict, issues: list[dict]) -> str:
    """``uncertain`` when a finding about the polarity scope lies on the link (module docstring), else ``clear``."""
    segs = _link_segments(p)
    cids = _marker_conditions(q, p)
    for it in issues:
        on = set(it["segment_ids"]) & segs
        if it["code"] in SCOPE_CODES and on:
            return UNCERTAIN
        if it["code"] == AMBIGUITY and it["detail"].startswith(POLARITY_AMBIGUITY) and on:
            return UNCERTAIN
        if it["code"] == CONDITION_UNRESOLVED and set(it["condition_ids"]) & cids:
            return UNCERTAIN
    return CLEAR


def build_structure(q: Question, reading: dict, issues: list[dict], requirement_ids: dict[str, str],
                    grounds: dict | None = None) -> dict:
    """``DecompositionStructure`` of one verified reading. ``issues``: its verification findings (the proposal-level
    ones too for the primary reading); ``requirement_ids``: settled ID -> proposal ID (``settle_reading``).
    ``grounds`` (``decompose_verify.polarity_grounds``) is accepted for the caller's shape but not read: confirmed
    effects are held empty in this version (module docstring)."""
    del grounds  # held: no confirmed effect is made from the verification grounds this round
    rid_of = {p: r for r, p in requirement_ids.items()}
    polarity = []
    for n, p in enumerate(reading["polarity_links"], start=1):
        polarity.append({
            "link_id": f"p{n}", "kind": p["kind"],
            "markers": [selection_ref(q, POL_MARKER, x) for x in p["marker_refs"]],
            "negative": [selection_ref(q, POL_NEGATIVE, x) for x in p["negative_refs"]],
            "positive": [selection_ref(q, POL_POSITIVE, x) for x in p["positive_refs"]],
            "requirement_ids": [rid_of[x] for x in dict.fromkeys(p["requirement_ids"]) if x in rid_of],
            "scope_status": scope_status(q, p, issues)})
    return {"version": STRUCTURE_VERSION,
            "requirements": {rid_of[r["id"]]: _requirement(q, r) for r in reading["requirements"]},
            "polarity": polarity, "confirmed_effects": []}
