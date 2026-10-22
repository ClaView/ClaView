"""New table selection (claview_v3) -> B request (plans 03 r2 1.1, 05).

The only module that knows the claview_v3 state shape. It reads the final outer state of claview_v3
(`question`, `as_of`, `analysis`, `selections`, `selection_route`) and builds a `BRequest`:

| claview_v3 | B request |
|---|---|
| `interpretation.requirements[].id` (+ `reading_id`) | `scopes[].scope_id`, `requirement_key` |
| `selections[]` with status auto / confirm, every table in order | `scopes[].tables`, `.decision` |
| status no_table, or no selection for the requirement | `skipped[]` (never sent to B) |
| requirement `condition_ids` + `shared_condition_ids` (in `conditions[]`) | `hints.conditions` |
| requirement `ambiguous_condition_ids` | `hints.ambiguous_condition_ids` (never applied) |
| `decomposition.condition_fields` (source, confirmed, diagnosis) | `hints.condition_fields` |
| `analysis.decomposition.inferred[requirement_id]` ([] kept; no key = absent) | `hints.inferred` |
| `analysis.judgement.negated`, `calc_hints` (question level) | `hints.negated`, `.calc_hints` |
| `analysis.mentions[].candidates` on the scope's tables (via `ref_map`) | `hints.value_rows` |
| `signals.facts.tables[t].indicator.occurrences[]` (value_id, exact) | `hints.value_mentions` |
| `selections[].reference_candidates`, else `facts.tables[t].indicator.…` | `hints.reference_…` |
| `selections[].alternatives[0]` (the first alternative bundle) | `scopes[].alternatives` |
| `selections[].bundle`, else `signals.plan17.bundle` when `b_handoff_ready` | `scopes[].bundle` |
| `decomposition.structure` (a supported version) + `decomposition.bindings` | `hints.structure` |
| `decomposition.structure_reason` (structure None) | `hints.structure_reason` |
| adapter findings (`plan17_bundle_not_ready`, `bundle_sources_differ`) | `adapter_notices` |
| `tables[].covers`, `facts.requirement.period_request` | `hints.covers`, `.a_period_request` |
| `selection_route`, `interpretation.relations`, `table_group_hint` | `route`, `relations`, ... |

Never read: `decomposition.alternative` (another reading's requirements are not this one's) and A's
`condition_fields[].applied` (B fills it). Input contract errors (a condition slot or a requirement
naming a condition that does not exist) go to the request's `input_errors`; B ends those scopes.
Whether B runs at all for a route that asks the user is the router's decision.
"""

from __future__ import annotations

import copy
from typing import Any

from .contract import (
    ADOPTED_MODEL_SOURCE,
    B_CONTRACT_VERSION,
    RULES_SOURCES,
    STRUCTURE_REASONS,
    STRUCTURE_VERSIONS,
    BRequest,
    BScope,
    Json,
    ValueMention,
    check_a_assumptions,
    check_bundle_shape,
    check_condition_bindings,
    check_decomposition_structure,
    check_mention_candidate,
    check_structure,
)

SENT_DECISIONS = ("auto", "confirm")
_ABSENT = object()  # `to_b_request` called without A's assumptions
CONDITION_FIELDS = ("id", "kind", "quote", "value", "attrs")
ROUTE_FIELDS = ("kind", "confirm", "partial", "reasons", "requirement_ids")
A_FIELD_KEYS = ("source", "confirmed", "period_diagnosis")
VALUE_ROLES = ("indicator", "target", "basis", "breakdown")


def _table(entry: Json) -> Json:
    return {
        "org_id": entry.get("org_id") or "",
        "table_id": entry["table_id"],
        "table_name": entry.get("table_name"),
    }


def _conditions(analysis: Json, ids: list[str]) -> list[Json]:
    by_id = {c["id"]: c for c in analysis.get("conditions") or []}
    return [
        {k: copy.deepcopy(by_id[i].get(k)) for k in CONDITION_FIELDS} for i in ids if i in by_id
    ]


def _is_exact(method: Any) -> bool:
    return isinstance(method, str) and method.startswith("exact")


def _facts_tables(selection: Json) -> Json:
    return ((selection.get("signals") or {}).get("facts") or {}).get("tables") or {}


def _value_mentions(selection: Json, table_ids: list[str]) -> list[ValueMention]:
    tables = _facts_tables(selection)
    out: list[ValueMention] = []
    seen = set()
    for table_id in table_ids:
        indicator = (tables.get(table_id) or {}).get("indicator") or {}
        for occ in indicator.get("occurrences") or []:
            value_id = occ.get("value_id")
            if not value_id or not _is_exact(occ.get("method")):
                continue
            key = (table_id, occ.get("obj_id"), value_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "table_id": table_id,
                    "obj_id": occ.get("obj_id"),
                    "value_id": value_id,
                    "name": occ.get("name"),
                    "method": occ["method"],
                    "role": occ.get("value_role"),
                    "relation": occ.get("relation"),
                    "parents": copy.deepcopy(occ.get("parents") or []),
                }
            )
    return out


def _reference_candidates(selection: Json, table_ids: list[str]) -> list[Json]:
    """Model-reference candidates of the scope's tables: kept for B, never fetched.

    Read from the selection's public `reference_candidates` when it has one, else from
    `signals.facts.tables[t].indicator.reference_candidates` (`signals.plan17.reference_candidates`
    holds table IDs only and is not read). The public field is A's `MentionCandidate` list;
    its shape problems are input contract errors (`_reference_errors`), never dropped quietly.
    """
    if "reference_candidates" in selection:
        given = selection["reference_candidates"]
        given = given if isinstance(given, list) else []
    else:
        tables = _facts_tables(selection)
        given = [
            candidate
            for table_id in table_ids
            for candidate in (
                ((tables.get(table_id) or {}).get("indicator") or {}).get("reference_candidates")
                or []
            )
        ]
    return [
        {**copy.deepcopy(c), "fetch": False}
        for c in given
        if isinstance(c, dict) and c.get("table_id") in table_ids
    ]


def _reference_errors(selection: Json) -> list[str]:
    """Shape problems of the public `Selection.reference_candidates` (A's MentionCandidate
    rows, without a `fetch` field: B adds `fetch: false`)."""
    if "reference_candidates" not in selection:
        return []
    given = selection["reference_candidates"]
    if not isinstance(given, list):
        return ["selection.reference_candidates: not a list"]
    return [
        p
        for i, candidate in enumerate(given)
        for p in check_mention_candidate(candidate, f"selection.reference_candidates[{i}]")
    ]


def _bundle(selection: Json) -> tuple[bool, Any, list[Json]]:
    """Whether the selection gives a bundle, the bundle, and the adapter notices of reading it.

    Order (agreed with the table selection, plan 17/19): the public `Selection.bundle`; else
    `signals.plan17.bundle` (same shape) only when that record says `b_handoff_ready: true`; else
    none. A plan-17 bundle that is not ready is noticed; when both differ the public one is used.
    A public field that is present is the public bundle whatever its value (None included): the
    contract check reports a wrong one, and the plan-17 record never stands in for it.
    """
    plan17 = (selection.get("signals") or {}).get("plan17")
    plan17 = plan17 if isinstance(plan17, dict) else {}
    staged = plan17.get("bundle") if isinstance(plan17.get("bundle"), dict) else None
    ready = plan17.get("b_handoff_ready") is True
    notices = []
    if "bundle" in selection:
        public = selection["bundle"]
        if staged is not None and staged != public:  # whatever the plan-17 record's readiness
            notices.append(
                {"code": "bundle_sources_differ", "detail": "Selection.bundle used over plan17"}
            )
        return True, copy.deepcopy(public), notices
    if staged is not None and ready:
        return True, copy.deepcopy(staged), notices
    if staged is not None:
        notices.append(
            {
                "code": "plan17_bundle_not_ready",
                "detail": "signals.plan17.b_handoff_ready is not true",
            }
        )
    return False, None, notices


def _structure_state(decomposition: Any, requirement_ids: list[str]) -> Json:
    """How the interpretation's own structure (A plan 19, contract document of 2026-10-21) is
    read: {state, structure, reason, errors, notices}. User decision of 10/21 05:13.

    - no `structure` field (an older output): the old fields (state "absent"); a lone
      `structure_reason` there is a contract error (the record half written);
    - `structure: None` from the rules (`applied_source` "rules" or "rules_fallback") with the
      reason `rules_structure_unrecorded`: the old fields, the reason kept (state "null");
      a null structure of an adopted model reading, or without that reason, is a contract
      error (state "invalid");
    - a version outside `STRUCTURE_VERSIONS`: a contract error (`structure_unsupported_version`);
      the old question-level words are never brought back for it (state "invalid");
    - otherwise the agreed `DecompositionStructure` shape keyed by the reading's settled
      requirement IDs (`check_decomposition_structure`), only on a model reading, with the
      bindings it is carried with; any problem is a contract error (state "invalid"); else
      "valid".
    Every contract error is request-level: all scopes end before the model and the fetch.
    The alternative reading's structure (`decomposition.alternative.structure`) is never read.
    """
    out = {"state": "absent", "structure": None, "reason": None, "errors": [], "notices": []}

    def invalid(problems: list[str]) -> Json:
        errors = [{"code": "input_contract_error", "detail": p} for p in problems]
        return {**out, "state": "invalid", "errors": errors}

    if not isinstance(decomposition, dict):
        return out
    if "structure" not in decomposition:
        if decomposition.get("structure_reason") is not None:
            return invalid(["decomposition.structure_reason: given without a structure field"])
        return out
    structure = decomposition["structure"]
    source = decomposition.get("applied_source")
    from_rules = source in RULES_SOURCES
    reason = decomposition.get("structure_reason")
    if structure is None:
        if not from_rules:
            return invalid([f"decomposition.structure: null on an adopted reading ({source!r})"])
        if reason not in STRUCTURE_REASONS:
            return invalid([f"decomposition.structure_reason: {reason!r} does not name the null"])
        return {**out, "state": "null", "reason": reason}
    version = structure.get("version") if isinstance(structure, dict) else None
    if isinstance(version, str) and version not in STRUCTURE_VERSIONS:
        return invalid([f"structure_unsupported_version: {version!r}"])
    problems = (
        check_decomposition_structure(structure, requirement_ids)
        if isinstance(structure, dict)
        else ["decomposition.structure: not an object or None"]
    )
    if source != ADOPTED_MODEL_SOURCE:  # only an adopted model reading keeps a structure
        problems.append(f"decomposition.structure: given on a rules record ({source!r})")
    if reason is not None:
        problems.append("decomposition.structure_reason: given next to a structure")
    # the bindings B carries with the structure are A's ConditionBinding records, never dropped
    problems += (
        check_condition_bindings(decomposition["bindings"])
        if "bindings" in decomposition
        else ["decomposition.bindings: missing next to a structure"]
    )
    if problems:
        return invalid(problems)
    return {**out, "state": "valid", "structure": structure}


def _structure_slice(structure: Json, decomposition: Json, rid: str) -> Json:
    """The settled requirement `rid`'s part of a valid structure (carried as given).

    The structure names settled requirement IDs: `requirements[rid]`, the polarity links with no
    requirement IDs (the whole question) or naming `rid`, the confirmed effects naming `rid`.
    The condition bindings come from `decomposition.bindings` (the attribution's source of
    truth), which keep the reading's proposal IDs: there `rid` is translated by
    `decomposition.requirement_ids` (settled -> proposal) and only that ID is matched; a binding
    is the question's, one naming it, or an unresolved one naming none (every requirement a
    candidate).
    """
    mapping = decomposition.get("requirement_ids")
    proposal = mapping.get(rid, rid) if isinstance(mapping, dict) and mapping else rid
    bindings = decomposition["bindings"]  # checked with the structure (`_structure_state`)
    return copy.deepcopy(
        {
            "version": structure["version"],
            "source": "analysis.decomposition.structure",
            "requirement": structure["requirements"][rid],
            "polarity": [
                p
                for p in structure["polarity"]
                if p["requirement_ids"] == [] or rid in p["requirement_ids"]
            ],
            "confirmed_effects": [
                e for e in structure["confirmed_effects"] if rid in e["requirement_ids"]
            ],
            "condition_bindings": [
                b
                for b in bindings
                if b["scope"] == "question"
                or (b["scope"] == "unresolved" and b["requirement_ids"] == [])
                or proposal in b["requirement_ids"]
            ],
        }
    )


def _requirement_fragments(requirement: Json) -> set:
    ids = {(requirement.get("indicator") or {}).get("fragment_id")}
    ids |= {t.get("fragment_id") for t in requirement.get("targets") or []}
    return {i for i in ids if i}


def _named_mentions(requirement: Json) -> set:
    return set(((requirement.get("refs") or {}).get("mention_ids")) or [])


def _value_rows(
    analysis: Json,
    requirement: Json,
    table_ids: list[str],
    others: set,
    others_named: set,
    structured: bool = False,
    words: bool = True,
) -> list[Json]:
    """Catalog values the question's mentions may stand for on the scope's tables.

    A mention belongs to the requirement when it shares a fragment with it (or is named in the
    requirement's `refs.mention_ids`); a mention bound to another requirement only (by a fragment
    or by that requirement's `refs.mention_ids`) is left out (two requirements on one table keep
    their own values); a mention bound to none is kept with `bound: false`.

    `negated` comes from the question-level negated words only without a requirement structure
    (rules, older outputs); under one (`structured`) it stays None: the global words are not
    applied again and an open polarity never becomes a settled exclusion. Without `words` (a
    structure that broke the contract) the question-level words are not read at all.
    """
    ref_map = analysis.get("ref_map") or {}
    mention_fragments = ref_map.get("mention_fragments") or {}
    segments = {s.get("segment_id"): s.get("quote") or "" for s in analysis.get("segments") or []}
    judgement_words = (analysis.get("judgement") or {}).get("negated") or [] if words else []
    negated = {w.strip() for w in judgement_words if w}
    mine = _requirement_fragments(requirement)
    named = _named_mentions(requirement)
    rows, seen = [], set()
    for mention in analysis.get("mentions") or []:
        mid = mention.get("mention_id")
        frags = set(mention_fragments.get(mid) or [])
        bound = bool(frags & mine) or mid in named
        if not bound and (frags & others or mid in others_named):
            continue  # another requirement's mention
        text = "".join(segments.get(s, "") for s in mention.get("segment_ids") or []).strip()
        is_negated = (
            None
            if structured
            else bool(text) and any(w and (w == text or w in text) for w in negated)
        )
        for c in mention.get("candidates") or []:
            if c.get("table_id") not in table_ids or c.get("role") not in VALUE_ROLES:
                continue
            key = (mid, c.get("table_id"), c.get("obj_id"), c.get("value_id"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "mention_id": mid,
                    "table_id": c["table_id"],
                    "obj_id": c.get("obj_id"),
                    "value_id": c.get("value_id"),
                    "value_name": c.get("value_name"),
                    "role": c["role"],
                    "path": c.get("path") or "",
                    "negated": is_negated,
                    "bound": bound,
                }
            )
    return rows


def _hints(
    analysis: Json,
    requirement: Json,
    selection: Json,
    table_ids: list[str],
    others: set,
    others_named: set,
    read: Json,
) -> Json:
    own = list(requirement.get("condition_ids") or [])
    shared = [i for i in requirement.get("shared_condition_ids") or [] if i not in own]
    # kept as given: an id that is also applied is a producer error that check_request reports
    ambiguous = list(requirement.get("ambiguous_condition_ids") or [])
    indicator = requirement.get("indicator") or {}
    facts = ((selection.get("signals") or {}).get("facts") or {}).get("requirement") or {}
    judgement = analysis.get("judgement") or {}
    decomposition = analysis.get("decomposition")
    structure = (
        _structure_slice(read["structure"], decomposition, requirement["id"])
        if read["state"] == "valid"
        else None
    )
    structured = structure is not None
    # the old question-level words only on the old paths (no structure field, a rules null): a
    # structure, or one that broke the contract (unsupported version, ...), never brings them back
    words = read["state"] in ("absent", "null")
    hints = {
        "text": requirement.get("quote") or "",
        "indicator": indicator.get("name"),
        "targets": [t["name"] for t in requirement.get("targets") or [] if t.get("name")],
        # under a requirement structure its own breakdowns and polarity replace the old words
        "breakdown": list(requirement.get("breakdown") or []) if words else [],
        "conditions": _conditions(analysis, own + shared),
        "ambiguous_condition_ids": ambiguous,
        "negated": list(judgement.get("negated") or []) if words else [],
        "calc_hints": [
            {"quote": h.get("quote"), "kind": h.get("kind")}
            for h in judgement.get("calc_hints") or []
            if isinstance(h, dict) and h.get("kind")
        ],
        "value_mentions": _value_mentions(selection, table_ids),
        "value_rows": _value_rows(
            analysis, requirement, table_ids, others, others_named, structured, words
        ),
        "reference_candidates": _reference_candidates(selection, table_ids),
        "covers": {
            t["table_id"]: copy.deepcopy(t.get("covers") or {})
            for t in selection.get("tables") or []
        },
        "a_period_request": copy.deepcopy(facts.get("period_request")),
    }
    if structured:
        hints["structure"] = structure
    elif read["state"] == "null" and read["reason"]:
        hints["structure_reason"] = read["reason"]
    if isinstance(decomposition, dict):
        fields = decomposition.get("condition_fields")
        if isinstance(fields, dict):
            hints["condition_fields"] = {
                cid: {k: copy.deepcopy(fields[cid][k]) for k in A_FIELD_KEYS if k in fields[cid]}
                for cid in own + shared
                if isinstance(fields.get(cid), dict)
            }
        inferred = decomposition.get("inferred")
        if isinstance(inferred, dict) and requirement["id"] in inferred:
            hints["inferred"] = copy.deepcopy(inferred[requirement["id"]])
    return hints


def _input_errors(analysis: Json) -> list[Json]:
    """Input contract errors: slots or requirements naming conditions that do not exist."""
    known = {c.get("id") for c in analysis.get("conditions") or []}
    errors = []
    decomposition = analysis.get("decomposition")
    if isinstance(decomposition, dict):
        fields = decomposition.get("condition_fields")
        if fields is not None and not isinstance(fields, dict):
            errors.append({"code": "input_contract_error", "detail": "condition_fields_not_object"})
        for cid in sorted((fields or {}) if isinstance(fields, dict) else {}):
            if cid not in known:
                errors.append(
                    {"code": "input_contract_error", "detail": f"condition_fields_unknown_id:{cid}"}
                )
    for requirement in (analysis.get("interpretation") or {}).get("requirements") or []:
        ids = list(requirement.get("condition_ids") or []) + list(
            requirement.get("shared_condition_ids") or []
        )
        for cid in ids:
            if cid not in known:
                errors.append(
                    {
                        "code": "input_contract_error",
                        "detail": f"requirement_unknown_condition:{cid}",
                        "scope_id": requirement["id"],
                    }
                )
    return errors


def to_b_request(state: Json, *, a_assumptions: Any = _ABSENT) -> BRequest:
    """The B request of one claview_v3 final state.

    `a_assumptions`: A's public assumption envelope ({version, items}) the A->B link passes;
    left out, the request has none (an older input). It is carried as given in
    `BRequest.a_assumptions`; its shape and its requirements (against the state's own A
    requirement list, not the scope IDs) are checked (`check_a_assumptions`), and a problem is
    a request-level input contract error.
    """
    analysis = state.get("analysis") or {}
    interpretation = analysis.get("interpretation") or {}
    decomposition = (
        analysis.get("decomposition") if isinstance(analysis.get("decomposition"), dict) else {}
    )
    selections = {s["requirement_id"]: s for s in state.get("selections") or []}
    requirements = interpretation.get("requirements") or []
    fragments_of = {r["id"]: _requirement_fragments(r) for r in requirements}
    scopes: list[BScope] = []
    skipped = []
    read = _structure_state(analysis.get("decomposition"), [r["id"] for r in requirements])
    scope_errors: list[Json] = list(read["errors"])  # request-level: every scope ends
    for requirement in requirements:
        rid = requirement["id"]
        selection = selections.get(rid)
        if selection is None:
            skipped.append({"scope_id": rid, "reason": "not_selected"})
            continue
        if selection.get("status") not in SENT_DECISIONS or not selection.get("tables"):
            skipped.append({"scope_id": rid, "reason": selection.get("status") or "not_selected"})
            continue
        tables = []
        for entry in selection["tables"]:
            table = _table(entry)
            if all(t["table_id"] != table["table_id"] for t in tables):
                tables.append(table)
        others = (
            set().union(*[f for r, f in fragments_of.items() if r != rid])
            if requirements
            else set()
        )
        others_named = set().union(
            set(), *[_named_mentions(r) for r in requirements if r["id"] != rid]
        )
        scope: BScope = {
            "scope_id": rid,
            "tables": tables,
            "decision": selection["status"],
            "hints": _hints(
                analysis,
                requirement,
                selection,
                [t["table_id"] for t in tables],
                others,
                others_named,
                read,
            ),
            "requirement_key": {
                "reading_id": requirement.get("reading_id") or decomposition.get("reading_id"),
                "requirement_id": rid,
            },
        }
        first_alternative = (selection.get("alternatives") or [None])[0]
        if first_alternative:
            scope["alternatives"] = [
                {"table_ids": list(first_alternative), "fetch": False, "source": "alternatives[0]"}
            ]
        given, bundle, notices = _bundle(selection)
        notices = list(read["notices"]) + notices
        if given:
            scope["bundle"] = bundle
            scope_errors.extend(
                {"code": "input_contract_error", "detail": detail, "scope_id": rid}
                for detail in check_bundle_shape(bundle, [t["table_id"] for t in tables])
            )
        scope_errors.extend(
            {"code": "input_contract_error", "detail": detail, "scope_id": rid}
            for detail in _reference_errors(selection)
        )
        if "structure" in scope["hints"]:
            scope_errors.extend(
                {"code": "input_contract_error", "detail": detail, "scope_id": rid}
                for detail in check_structure(scope["hints"]["structure"])
            )
        if notices:
            scope["adapter_notices"] = notices
        scopes.append(scope)
    route = state.get("selection_route")
    request: BRequest = {
        "question": state.get("question") or (analysis.get("text") or {}).get("raw") or "",
        "as_of": state.get("as_of"),
        "scopes": scopes,
        "skipped": skipped,
        "route": {k: copy.deepcopy(route.get(k)) for k in ROUTE_FIELDS} if route else None,
        "relations": copy.deepcopy(interpretation.get("relations") or []),
        "table_group_hint": copy.deepcopy(interpretation.get("table_group_hint") or []),
        "source": "claview_v3",
        "contract_version": B_CONTRACT_VERSION,
    }
    if a_assumptions is not _ABSENT:
        request["a_assumptions"] = copy.deepcopy(a_assumptions)
        scope_errors += [
            {"code": "input_contract_error", "detail": p}
            for p in check_a_assumptions(a_assumptions, [r["id"] for r in requirements])
        ]
    errors = _input_errors(analysis) + scope_errors
    if errors:
        request["input_errors"] = errors
    return request


def to_legacy_a_result(request: BRequest) -> Json:
    """The current B input `{"tables": [...]}`: every scope's tables in scope order, each once.

    Lets the existing B graph run on a new request while the per-scope graph is built
    (plan 03 r2, step 2).
    """
    tables: list[Json] = []
    seen = set()
    for scope in request["scopes"]:
        for table in scope["tables"]:
            key = (table["org_id"], table["table_id"])
            if key not in seen:
                seen.add(key)
                tables.append(dict(table))
    return {"tables": tables}
