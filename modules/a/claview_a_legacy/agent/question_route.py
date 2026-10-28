"""Question-level next way (plan 07 r4 S5, decisions 1 and 4; 9-1 to 9-3). Model off: no call.

One way per question, ``selection_route{kind, requirement_ids, options, default, reasons, partial, confirm}``, decided
after the selection recheck (V2) by the pure function ``route_of_state`` from the state only. Kinds: ``proceed``,
``ask_interpretation``, ``ask_selection``, ``no_table``. Rows, first matching wins:
0. every requirement no_table (states after V2) -> no_table, reasons unsupported / no_evidence / no_cover as found;
   no interpretation or no requirement -> ask_interpretation (open, ``no_requirement``). Some requirements no_table
   (or unsupported), or interpretation status partial -> the rows below over the others, ``partial`` true.
2. a requirement with several official names (``branch``) -> ask_interpretation, options = the official names.
3. open interpretation ask (no options): a requirement unresolved by judgement-table row 10 (no term) or row 6
   (two or more basis values) (``unresolved``), a model ask (``model_ask``; a model-step note ``ask:no_indicator``
   goes to row 5, a rejected model step to row 4, too many points stays here), a leftover word without a catalog
   candidate (``leftover_no_candidate``: ``word_suggestions`` of the word is empty),
   ``ambiguous_word``.
4. interpretation failures with candidates: inexact_term, mixed_reading, scope_ambiguous, ambiguous_attribution,
   unused_name, ungrounded_split, reattached, an appositive (a fragment of phrase kind appositive), a leftover word
   with candidates, another unresolved requirement, a rejected model step, a requirement without an indicator next
   to indicator requirements (``broad_mixed``). By 9-3: readings (S4) two or more with the same tables -> proceed
   (confirm, ``readings_same``); different -> ask_selection (options = the readings' tables); no reading ->
   ask_interpretation with the decision-point options the select node recorded, else open.
5. a no-indicator question (S3): one family -> proceed (confirm); two to five -> ask_selection (options = the
   families' tables); none, more than five or cut -> ask_interpretation (open: which indicator).
6. ask_selection: family alternatives (3.6 pending), an unset dimension of kind mixed_basis (decision 4).
7. proceed with ``confirm``: any confirm state (partial cover, period unknown, cut searches, other or more unset
   dimensions, unproven conditions, interpretation undecided, group_family, no_indicator, keep_all marks,
   unverified_structure ...), its reasons kept.
8. proceed.
Plan 07 r5 (user decision 10/19 20:11) replaces rows 3 and 4 by the Q5 decision rules: every requirement without a
table for lack of indicator evidence -> ask_interpretation (open, its options only those that give a table when
selected again, Q7); readings giving different tables -> ask_selection; an interpretation still undecided whose
requirements got tables goes on with confirm (its failures kept as reasons); cells left uncovered by elements the
question settled -> ask_selection (tables covering most), by uncertain elements -> proceed with confirm; a requirement
without indicator evidence next to answered ones -> proceed, partial (``missing_requirement``). A table ask needs two
options (Q7).
Options (decision 4): at most ``MAX_OPTIONS``; table options carry the table name and the axis values of the table
the question names, from ``selection_notes`` the select node recorded (``a_outcome.parts.select.question``); the
default is the current reading and choice. The router does not read this yet (user decision 10/19). Nothing here
names a table, case or question.
Plan 11 R1 (contract C): every question of ``questions`` also carries ``id``, ``version``, ``cause``, ``open``,
``effect_kind`` and options with ``id``, ``label``, ``assignment`` and ``effect`` (``contract/shapes.RouteQuestion``);
these fields change neither the way chosen nor the options' content or order.
"""

from __future__ import annotations


def completion2_route(state, layer=None):
    """Interactive policy. Only this function decides whether to ask.

    The legacy measurement policy below is intentionally unchanged. Table counts,
    scores and uncertainty are not question dimensions. Options retain trusted
    effects privately; the UI sees only labels and IDs.
    """
    from .assumptions import digest
    from .session import session_data, state_version

    analysis = state.get("analysis") or {}
    session = session_data(state)
    selections = state.get("selections") or []
    checked = ((state.get("verification") or {}).get("selection") or {}).get("states") or {}
    requirements = {r["id"]: r for r in (analysis.get("interpretation") or {}).get("requirements") or []}
    eligible, skipped = [], []
    for s in selections:
        rid = s["requirement_id"]
        record = (s.get("signals") or {}).get("plan17") or {}
        if checked.get(rid, s.get("status")) == "no_table" or not s.get("tables"):
            skipped.append({"requirement_id": rid, "reason": "no_table"})
        elif not s.get("bundle") or record.get("b_handoff_ready") is not True:
            skipped.append({"requirement_id": rid, "reason": "execution_unresolved"})
        elif _unresolved_indicator_without_support(requirements.get(rid, {}), s):
            skipped.append({"requirement_id": rid, "reason": "indicator_meaning_unsupported"})
        else:
            eligible.append(rid)

    def question(dimension, rids, options):
        key = digest([dimension, sorted(rids), options])
        if key in session.get("answered_dimensions", []):
            return None
        version = state_version(state)
        qid = "q:" + digest([state.get("request_id"), state.get("question_version"), version, key])
        opts = [{**o, "option_id": qid + f":{i + 1}"} for i, o in enumerate(options)]
        return {"kind": "ask", "requirement_ids": rids, "eligible": [], "skipped": skipped,
                "question": {"question_id": qid, "version": version, "dimension_key": key,
                    "dimension": dimension, "text": "어느 의미로 조회할까요?",
                    "requirement_ids": rids, "options": opts, "default": opts[0]["option_id"]}}

    # Verified interpretation alternatives can separate measurement concepts. A
    # changed target, condition attribution or requirement count alone is not this.
    readings = session.get("readings") or []
    if readings and not session.get("reading_answered"):
        def indicators(a):
            return sorted({(r.get("indicator") or {}).get("name") or (r.get("indicator") or {}).get("quote") or ""
                           for r in (a.get("interpretation") or {}).get("requirements") or []})
        base = indicators(analysis)
        for item in readings:
            other = indicators(item["analysis"])
            ours = sorted(t["table_id"] for s in selections for t in s.get("tables") or [])
            theirs = sorted(t["table_id"] for s in item["selections"] for t in s.get("tables") or [])
            if base and all(base) and other and all(other) and base != other and ours and theirs and ours != theirs:
                rids = [r["id"] for r in analysis["interpretation"]["requirements"]]
                out = question("measurement_concept", rids, [
                    {"label": " / ".join(base), "effect": {"kind": "keep_reading"}},
                    {"label": " / ".join(other), "effect": {"kind": "change_reading", "reading": item["id"]}}])
                if out:
                    return out
    # Price is the only basis axis currently approved as a question dimension.
    # Other mixed-basis axes, family IDs and assignment counts cannot imply it.
    for s in selections:
        rid = s["requirement_id"]
        if rid in (session.get("choices") or {}):
            continue
        decision = ((s.get("signals") or {}).get("decision_preview") or {}).get("decision") or {}
        price = any(d.get("kind") == "mixed_basis" and "price" in d.get("values", [])
                    and not d.get("set_by_question") for d in decision.get("dimensions") or [])
        if not price:
            continue
        alts = [a for a in decision.get("alternatives") or [] if a.get("tables") and
                not (set(a.get("gaps") or []) - {"unproven_cover"})]
        if len(alts) < 2:
            continue
        from ..select.tables import selection_catalog
        from ..select.candidates import family_key
        from ..select.cover import dimension_kinds, signature
        views = selection_catalog(layer).views if layer is not None else {}
        current = [t["table_id"] for t in s.get("tables") or []]
        # A mixed axis anywhere in the search pool does not establish an
        # ambiguity of the selected concept. Only comparable price variants of
        # the current singleton can supply this question's options.
        if len(current) != 1 or current[0] not in views:
            continue
        first = current[0]
        price_values = signature(views[first]).get("price", frozenset())
        if len(price_values) != 1:
            continue
        comparable = []
        for a in alts:
            if len(a["tables"]) != 1 or a["tables"][0] not in views:
                continue
            t = a["tables"][0]
            if t == first:
                comparable.insert(0, a)
                continue
            other_values = signature(views[t]).get("price", frozenset())
            kinds = dimension_kinds(views[first], views[t])
            if (family_key(layer, first) == family_key(layer, t)
                    and len(other_values) == 1 and other_values != price_values
                    and set(kinds) <= {"mixed_basis", "period", "source"}
                    and kinds.get("mixed_basis") == ["price"]):
                comparable.append(a)
        if len(comparable) < 2 or comparable[0]["tables"] != current:
            continue
        alts = comparable
        opts = [{"label": " / ".join(views[t].title if t in views else t for t in a["tables"]),
                 "effect": {"kind": "pick_assignment", "requirement_id": rid, "tables": a["tables"]}}
                for a in alts]
        out = question("price", [rid], opts)
        if out:
            return out
    uncertain = (analysis.get("judgement") or {}).get("certain") is not True or any(
        checked.get(s["requirement_id"], s.get("status")) != "auto" for s in selections)
    kind = "no_result" if not eligible else "partial" if skipped else "assume" if uncertain else "proceed"
    return {"kind": kind, "requirement_ids": [s["requirement_id"] for s in selections],
            "eligible": eligible, "skipped": skipped, "question": None}


def _unresolved_indicator_without_support(requirement, selection):
    """Block only unresolved meaning with explicit absence on every chosen table.

    Unknown/unchecked facts, unnamed indicators and uncertainty alone are not
    negative evidence. Context-based or partial indicator matches remain usable
    with assumptions. This gate does not delete/rerank selection candidates.
    """
    if requirement.get("status") != "unresolved" and requirement.get("indicator"):
        return False
    tables = selection.get("tables") or []
    facts = ((selection.get("signals") or {}).get("facts") or {}).get("tables") or {}
    for table in tables:
        indicator = (facts.get(table["table_id"]) or {}).get("indicator") or {}
        check = indicator.get("check") or {}
        if check.get("result") in ("match", "partial") or indicator.get("occurrences"):
            return False
        if indicator.get("absent") is not True and not (check.get("available") is True and check.get("result") == "none"):
            return False
    return bool(tables)

from typing import Any

Json = dict[str, Any]

ROUTES = ("proceed", "ask_interpretation", "ask_selection", "no_table")
MAX_OPTIONS = 5  # decision 4
MAX_FAMILIES = 5  # r4 S3: more families than this -> ask the indicator
VALUES_PER_TABLE = 4
# not_in_catalog: a split piece without grounding (user decision 10/19 21:00), a partial answer when others have tables
NO_TABLE_REASONS = ("unsupported", "no_evidence", "no_cover", "not_in_catalog")
OPEN_FAILURES = ("ambiguous_word",)
OPTION_FAILURES = ("inexact_term", "mixed_reading", "scope_ambiguous", "ambiguous_attribution", "unused_name",
                   "ungrounded_split", "reattached")
OPEN_ROWS = (10, 6)  # judgement-table rows of an unresolved requirement asked openly
MODEL_ASK = "model_ask"
MODEL_NO_INDICATOR_NOTE = "ask:no_indicator"
MODEL_TOO_MANY = "too_many_points"
NO_INDICATOR = "no_indicator"
MIXED_KIND = "mixed_basis"
INDICATOR_UNCHECKED = "indicator_unchecked"  # select/graph.py: indicator check n/a on every candidate
NOT_IN_CATALOG_REASON = "not_in_catalog"
UNUSED_NAME = "unused_name"


def _undecided_ask() -> str:
    import json

    from .. import paths

    return (json.loads(paths.SELECTION_POLICY.read_text(encoding="utf-8")).get("decision") or {}).get(
        "undecided_ask") or "off"


UNDECIDED_ASK = _undecided_ask()  # off | narrow | wide (plan 10 safety 1b)


# ---------------------------------------------------------------- notes recorded by the select node


def selection_notes(analysis: Json | None, selections: list[Json], layer, as_of: str | None = None,
                    policy: str | None = None, *, candidate_policy: str = "legacy",
                    selection_mode: str = "legacy") -> Json:
    """What the way needs that the selections do not hold (9-2: the select node records it, S5 only reads it):
    readings (S4), decision points of the interpretation (``model_step.prepare``, read only, no call), table labels
    (name and the axis values the question names) of every table an option may show, official names of branch
    requirements, catalog words of the leftover words."""
    from ..select.candidates import word_suggestions
    from ..select.readings import compare_readings
    from ..select.model_refs import adopted

    out: Json = {"readings": None, "points": [], "labels": {}, "branch_names": {}, "leftover": {}}
    if not analysis or not (analysis.get("interpretation") or {}).get("requirements"):
        return out
    extra = {"selection_mode": "refutation"} if selection_mode == "refutation" else {}
    out["readings"] = compare_readings(analysis, selections, layer, as_of, policy, candidate_policy=candidate_policy, **extra)
    judgement = analysis.get("judgement") or {}
    if layer is not None and judgement and not judgement.get("certain") and not adopted(analysis):
        from ..interpret import model_step as ms

        prep = ms.prepare(analysis["text"], analysis["conditions"], analysis["fragments"], analysis["terms"],
                          {**judgement, "certain": False}, layer)
        # plan 07 r5 Q7: an option is kept for an ask only when the selection gives a table with it; measured only
        # when some requirement got no table (an interpretation ask can only come from there, Q5 rule 1)
        measure = any(s.get("status") == "no_table" for s in selections)
        from ..select.readings import option_yields

        out["points"] = [{"id": p.id, "kind": p.kind, "form": p.form, "ask": p.ask,
                          "requirement_ids": list(p.requirement_ids),
                          "options": [{"id": o.id, "text": o.text, "effect": o.effect,
                                       "yields": measure and option_yields(analysis, prep, p, o, layer, as_of, policy,
                                                                           candidate_policy=candidate_policy, **extra)}
                                      for o in p.options if o.effect not in ms.ASK_EFFECTS]} for p in prep.points]
    if layer is not None:
        for w in judgement.get("leftover") or []:
            out["leftover"][w] = word_suggestions(w, layer)[:MAX_OPTIONS]
    terms_by_frag: dict[str, list[str]] = {}
    for t in analysis.get("terms") or []:
        if t.get("path") == "exact" and t.get("type") in ("indicator", "series_name"):
            terms_by_frag.setdefault(t["fragment_id"], []).append(t["name"])
    for r in analysis["interpretation"]["requirements"]:
        if "branch" in (r.get("reasons") or []):
            frag = (r.get("indicator") or {}).get("fragment_id")
            out["branch_names"][r["id"]] = list(dict.fromkeys(terms_by_frag.get(frag, [])))[:MAX_OPTIONS]
    tables: list[str] = []
    for s in selections:
        tables += [t["table_id"] for t in s.get("tables") or []]
        tables += [t for g in s.get("alternatives") or [] for t in g]
        tables += [t for m in s.get("unspecified") or [] for t in m.get("other_table_ids") or []]
        tables += ((s.get("signals") or {}).get("target_search") or {}).get("family_tables") or []
    for r in (out["readings"] or {}).get("readings") or []:
        tables += r["tables"]
    question_ns = "".join(((analysis.get("text") or {}).get("raw") or "").split())
    out["labels"] = {t: _table_label(layer, t, question_ns) for t in dict.fromkeys(tables)}
    return out


def _table_label(layer, table_id: str, question_ns: str) -> Json:
    if layer is None:
        return {"table_id": table_id, "table_name": None, "values": []}
    from ..select.tables import selection_catalog

    view = selection_catalog(layer).views.get(table_id)
    if view is None:
        return {"table_id": table_id, "table_name": None, "values": []}
    values = []
    for it in view.items:
        if len(it.name_nospace) >= 2 and it.name_nospace in question_ns and it.name_raw not in values:
            values.append(it.name_raw)
    return {"table_id": table_id, "table_name": view.title, "values": values[:VALUES_PER_TABLE]}


# ---------------------------------------------------------------- the way


def route_of_state(state: Json, layer=None) -> Json:
    """``selection_route`` of an outer state after V2 (pure; ``layer`` is not needed, the select node recorded the
    option information)."""
    verification = state.get("verification") or {}
    part = (((state.get("a_outcome") or {}).get("parts") or {}).get("select") or {}).get("question") or {}
    return question_route(state.get("analysis"), state.get("selections") or [], part,
                          v2_states=(verification.get("selection") or {}).get("states"))


def _route(kind: str, reasons: list[str], requirement_ids: list[str] | None = None, options: list | None = None,
           default: Any = None, partial: bool = False, confirm: bool = False,
           questions: list | None = None) -> Json:
    if questions is None and kind == "ask_selection" and options:
        questions = [_flat_question(requirement_ids or [], reasons, options)]
    return {"kind": kind, "requirement_ids": list(dict.fromkeys(requirement_ids or [])), "options": options or [],
            "default": default, "reasons": list(dict.fromkeys(reasons)), "partial": partial, "confirm": confirm,
            "questions": questions or []}


def _row(r: Json) -> int | None:
    for x in r.get("reasons") or []:
        if x.startswith("row:") and x[4:].isdigit():
            return int(x[4:])
    return None


def _appositive_ids(analysis: Json, reqs: list[Json]) -> list[str]:
    spans = [f["span"] for f in analysis.get("fragments") or [] if f.get("phrase") == "appositive"]
    return [r["id"] for r in reqs if any(a < r["span"][1] and r["span"][0] < b for a, b in spans)]


def question_route(analysis: Json | None, selections: list[Json], notes: Json | None = None,
                   v2_states: dict | None = None) -> Json:
    """The way of one question (module docstring). Plan 11 R1 (C): every question also carries the contract fields
    (``_stamp``); which way is chosen and the options' content and order are as before."""
    return _stamp(_question_route(analysis, selections, notes, v2_states), analysis, selections)


def _stamp(route: Json, analysis: Json | None, selections: list[Json]) -> Json:
    """Plan 11 R1 (7-8): question IDs in order (q1, q2, ...), option IDs within a question (q1.o1, ...) and the
    versions of the analysis and the selections the questions were asked over (``contract.check.version_of``)."""
    questions = route.get("questions") or []
    if not questions:
        return route
    from ..contract.check import version_of

    version = {"analysis": version_of(analysis), "selection": version_of(selections)}
    for k, q in enumerate(questions, 1):
        q["id"] = f"q{k}"
        q["version"] = dict(version)
        for j, o in enumerate(q.get("options") or [], 1):
            o["id"] = f"q{k}.o{j}"
    return route


def _option_label(o: Json) -> str:
    """What an option shows: its tables' names (table ID when a table has no name), joined."""
    tables = o.get("tables") or [{"table_id": t} for t in o.get("table_ids") or []]
    return " / ".join(t.get("table_name") or t.get("table_id") or "" for t in tables)


def _option_effect(dimension: str, values: list) -> Json:
    return {"dimension": dimension, "value": values[0] if len(values) == 1 else None, "polarity": "+"}


def _contract_option(o: Json, dimension: str, assignment: dict) -> Json:
    """Plan 11 R1: the contract fields of one option (label, assignment, effect); the option ID comes with
    ``_stamp``. The plan 10 fields stay as they are."""
    o["label"] = _option_label(o)
    o["assignment"] = {rid: list(t) for rid, t in assignment.items()}
    o["effect"] = _option_effect(dimension, list(o.get("value") or []))
    return o


def _basis_axes() -> frozenset:
    from ..select.cover import AXIS_KINDS

    return frozenset(AXIS_KINDS)


def _question_route(analysis: Json | None, selections: list[Json], notes: Json | None = None,
                    v2_states: dict | None = None) -> Json:
    notes = notes or {}
    interp = (analysis or {}).get("interpretation") or {}
    reqs = interp.get("requirements") or []
    if not reqs:
        return _route("ask_interpretation", ["no_requirement"], default={"keep": True})
    by_rid = {s["requirement_id"]: s for s in selections}
    states = {r["id"]: (by_rid.get(r["id"]) or {}).get("status", "no_table") for r in reqs}
    for rid, st in (v2_states or {}).items():
        if rid in states:
            states[rid] = st
    live = [r for r in reqs if states[r["id"]] != "no_table" and r.get("status") != "unsupported"]
    if not live:
        found = [x for r in reqs for x in (by_rid.get(r["id"]) or {}).get("reasons") or [] if x in NO_TABLE_REASONS]
        found += ["unsupported" for r in reqs if r.get("status") == "unsupported"]
        if found and set(found) == {"no_evidence"}:
            # plan 07 r5 Q5 rule 1: no indicator evidence -> no similar table; ask the indicator (options that give
            # a table when selected again only, Q7)
            return _route("ask_interpretation", ["no_indicator_evidence"], [r["id"] for r in reqs],
                          _point_options(notes), {"keep": True}, False, True)
        return _route("no_table", found or ["no_cover"], [r["id"] for r in reqs])
    partial = len(live) < len(reqs) or interp.get("status") == "partial"
    judgement = (analysis or {}).get("judgement") or {}
    failed = set(judgement.get("failed_conditions") or [])
    model = judgement.get("model") or {}
    notes_model = model.get("notes") or []
    ids = [r["id"] for r in live]
    keep = {"keep": True}

    # row 2: several official names
    # plan 10 section 3 (user decision 10/20 01:57): the branch is looked at after the selection. Its names are
    # asked only when the chosen table's period cannot be checked (period_check); a branch whose selection settled
    # one bundle goes on (confirm, branch_resolved); alternatives left are a table ask (rows below)
    branch = [r["id"] for r in live if "branch" in (r.get("reasons") or [])]
    no_period = [rid for rid in branch if ((by_rid.get(rid) or {}).get("signals") or {}).get("period_check")]
    if no_period:
        names = notes.get("branch_names") or {}
        options = [{"requirement_id": rid, "name": n, "default": k == 0, "source": "official_name"}
                   for rid in no_period for k, n in enumerate(names.get(rid) or [])][:MAX_OPTIONS]
        return _route("ask_interpretation", ["branch"], no_period, options, keep, partial, True)

    # plan 10 safety 1a (user 10/20 02:28): an indicator no candidate could be checked against is asked, never decided
    unchecked = [r["id"] for r in live if INDICATOR_UNCHECKED in ((by_rid.get(r["id"]) or {}).get("reasons") or [])]
    if unchecked:
        return _route("ask_interpretation", [INDICATOR_UNCHECKED], unchecked, _point_options(notes), keep, partial,
                      True)

    # plan 07 r5 Q5 (the decision rules, first wins) after rows 0-2: an interpretation still undecided whose
    # requirements got tables goes on (rule 4, confirm); the interpretation is asked only where no table came (rule 1)
    undecided_reasons = _undecided_reasons(analysis or {}, live, notes, failed, model, notes_model)
    question_no_indicator = NO_INDICATOR in failed or MODEL_NO_INDICATOR_NOTE in notes_model
    missing = [r["id"] for r in reqs if states[r["id"]] == "no_table" and r.get("status") != "unsupported"
               and "no_evidence" in ((by_rid.get(r["id"]) or {}).get("reasons") or [])]
    readings = notes.get("readings") or {}

    # rule 2: readings that give different tables -> ask the table (options: the readings' tables)
    if undecided_reasons and readings.get("status") == "differ":
        options = _reading_options(readings, notes)
        if len(options) >= 2:
            return _route("ask_selection", undecided_reasons + ["readings_differ"], ids, options,
                          {"reading": "interpretation"}, partial, True)

    # row 5: a no-indicator question
    if question_no_indicator:
        searched = [(r["id"], (by_rid[r["id"]].get("signals") or {}).get("target_search")) for r in live
                    if (by_rid.get(r["id"]) or {}).get("signals", {}).get("target_search") is not None]
        if searched:
            fams = max(rec.get("families", 0) for _, rec in searched)
            cut = any(rec.get("truncated") for _, rec in searched)
            sids = [rid for rid, _ in searched]
            if fams == 0 or fams > MAX_FAMILIES or cut:
                return _route("ask_interpretation", ["no_indicator_open"], sids, [], keep, partial, True)
            if fams == 1:
                return _route("proceed", [NO_INDICATOR], sids, [], None, partial, True)
            options, choices = _selection_options(sids, selections, notes, MAX_OPTIONS)
            return _route("ask_selection", ["target_families"], sids, options, {"choices": choices}, partial, True)

    # row 6: tables the code cannot tell apart
    # plan 10: the D record of the select node decides (ask / resolved / single); a selection without one (made by
    # hand) keeps the old row 6
    sel_ids, sel_reasons, d_resolved = [], [], []
    for r in live:
        s = by_rid.get(r["id"]) or {}
        d = (s.get("signals") or {}).get("dimensions")
        got = []
        if d is not None:
            got = list(d.get("ask_reasons") or [])
            if d.get("result") == "resolved":
                d_resolved.append(r["id"])
        else:
            if s.get("model_step") == "pending" or s.get("alternatives"):
                got.append("family_alternatives")
            if any(MIXED_KIND in (m.get("kind") or "").split("+") for m in s.get("unspecified") or []):
                got.append("unspecified_mixed_basis")
        if got:
            sel_ids.append(r["id"])
            sel_reasons += got
    if sel_reasons:
        sel_ids = _with_bundle_mates(sel_ids, live, by_rid)
        options, choices = _selection_options(sel_ids, selections, notes, MAX_OPTIONS)
        if len(options) >= 2:  # Q7: an ask needs two options that give a table
            return _route("ask_selection", sel_reasons, sel_ids, options, {"choices": choices}, partial, True,
                          _questions(sel_ids, selections, notes, sel_reasons))

    # plan 07 r5 Q5 rule 3: cells left uncovered by elements the question settled (exact targets and listed values,
    # a settled interpretation) -> ask the table (options: the tables covering most); by uncertain elements ->
    # proceed with the cover there is (confirm)
    short = [r["id"] for r in live if "partial_cover" in ((by_rid.get(r["id"]) or {}).get("reasons") or [])
             and not undecided_reasons and _settled_elements(analysis or {}, r)]
    if short:
        options, choices = _selection_options(short, selections, notes, MAX_OPTIONS)
        if len(options) >= 2:
            return _route("ask_selection", ["uncovered_cells"], short, options, {"choices": choices}, partial, True)

    # plan 10 safety 1b (user 10/20 02:28; switch ``undecided_ask`` of data/selection_policy.json): an undecided
    # interpretation that left a split piece out of the catalog (narrow), or a catalog name unused (wide), is asked
    not_in_cat = any(NOT_IN_CATALOG_REASON in ((by_rid.get(r["id"]) or {}).get("reasons") or []) for r in reqs)
    if undecided_reasons and UNDECIDED_ASK in ("narrow", "wide") and (
            not_in_cat or (UNDECIDED_ASK == "wide" and UNUSED_NAME in failed)):
        return _route("ask_interpretation", undecided_reasons + ["undecided_unanswered"], ids, _point_options(notes),
                      keep, partial, True)

    # rows 7-8: proceed (rule 4 confirm, rule 5 auto)
    confirm_reasons = list(undecided_reasons)
    if undecided_reasons and readings.get("status") == "same":
        confirm_reasons.append("readings_same")  # rule 4: the readings give the same tables
    if missing:
        confirm_reasons.append("missing_requirement")
    if any("not_in_catalog" in ((by_rid.get(r["id"]) or {}).get("reasons") or []) for r in reqs):
        confirm_reasons.append("not_in_catalog")  # a split piece no table holds: answered without it (partial)
    for r in live:
        if states[r["id"]] == "confirm":
            confirm_reasons += (by_rid.get(r["id"]) or {}).get("reasons") or ["confirm"]
            if any(not m.get("kind") for m in (by_rid.get(r["id"]) or {}).get("unspecified") or []):
                confirm_reasons.append("unspecified_keep_all")
    if "unverified_structure" in failed:
        confirm_reasons.append("unverified_structure")
    if d_resolved:
        confirm_reasons.append("d_resolved")  # plan 10 D: one bundle left by a set dimension or the score gap
    if branch:
        confirm_reasons.append("branch_resolved")  # plan 10 section 3: the selection settled the branch
    return _route("proceed", confirm_reasons, [r["id"] for r in live if states[r["id"]] == "confirm"], [], None,
                  partial, bool(confirm_reasons))


# ---------------------------------------------------------------- plan 07 r5 Q5 helpers


def _undecided_reasons(analysis: Json, live: list[Json], notes: Json, failed: set, model: Json,
                       notes_model: list) -> list[str]:
    """Why the interpretation is not settled (the old rows 3 and 4: open and option interpretation failures), as
    reasons of a confirm (rule 4) or of a table ask when the readings give different tables (rule 2)."""
    out = [f for f in OPEN_FAILURES + OPTION_FAILURES if f in failed]
    for r in live:
        if r.get("status") == "unresolved":
            out.append("unresolved")
        if MODEL_ASK in (r.get("reasons") or []):
            out.append(MODEL_ASK)
    if any(n.startswith(MODEL_TOO_MANY) for n in notes_model):
        out.append(MODEL_TOO_MANY)
    if model.get("outcome") == "rejected":
        out.append("model_rejected")
    leftover = notes.get("leftover") or {}
    if leftover:
        out.append("leftover_with_candidate" if any(leftover.values()) else "leftover_no_candidate")
    if _appositive_ids(analysis, live):
        out.append("appositive")
    if any(r.get("indicator") is None for r in live) and NO_INDICATOR not in failed:
        out.append("broad_mixed")
    return list(dict.fromkeys(out))


def _settled_elements(analysis: Json, r: Json) -> bool:
    """Every target and listed value of the requirement is an exact catalog term and its indicator name is exact (no
    derived or lexical name): cells it leaves uncovered come from what the question settled."""
    ind = r.get("indicator") or {}
    if ind.get("path") != "exact":
        return False
    return all(t.get("name") for t in r.get("targets") or [])


def _point_options(notes: Json) -> list[Json]:
    """Interpretation options of the decision points that give a table when the selection runs again with them
    (``selection_notes`` marks ``yields``; Q7), at most ``MAX_OPTIONS``."""
    out: list[Json] = []
    for p in notes.get("points") or []:
        for o in p["options"]:
            if o.get("yields") and len(out) < MAX_OPTIONS:
                out.append({"requirement_id": None, "point": p["id"], "kind": p["kind"], "text": o["text"],
                            "effect": o["effect"], "default": False, "source": "point"})
    return out


# ---------------------------------------------------------------- options (decision 4)


def _labelled(table_ids: list[str], notes: Json) -> list[Json]:
    labels = notes.get("labels") or {}
    return [labels.get(t) or {"table_id": t, "table_name": None, "values": []} for t in table_ids]


def _reading_options(readings: Json, notes: Json) -> list[Json]:
    out: list[Json] = []
    for r in readings.get("readings") or []:
        if r["tables"] and all(r["tables"] != o["table_ids"] for o in out) and len(out) < MAX_OPTIONS:
            out.append({"requirement_id": None, "table_ids": list(r["tables"]),
                        "tables": _labelled(r["tables"], notes), "default": r["source"] == "interpretation",
                        "source": f"reading:{r['source']}"})
    return out


def _groups(selection: Json) -> list[list[str]]:
    """Candidate table groups of one requirement: the current one, the contending families, the current group with
    a marked table replaced by each left-out table of an unset dimension, the target families (S3)."""
    current = [t["table_id"] for t in selection.get("tables") or []]
    groups = [current]
    groups += [list(a) for a in selection.get("alternatives") or []]
    for m in selection.get("unspecified") or []:
        for other in m.get("other_table_ids") or []:
            groups.append([other if t in m.get("table_ids", []) else t for t in current] if current else [other])
    for t in ((selection.get("signals") or {}).get("target_search") or {}).get("family_tables") or []:
        groups.append([t])
    out = []
    for g in groups:
        g = list(dict.fromkeys(g))
        if g and g not in out:
            out.append(g)
    return out


def _selection_options(requirement_ids: list[str], selections: list[Json], notes: Json,
                       cap: int) -> tuple[list[Json], dict]:
    """(options, default choices): every asked requirement's current group first (its default), then the other
    candidate groups requirement by requirement in turn; at most ``cap``."""
    by_rid = {s["requirement_id"]: s for s in selections}
    per_req = [(rid, _groups(by_rid[rid])) for rid in requirement_ids if rid in by_rid]
    defaults = [{"requirement_id": rid, "table_ids": g[0], "default": True, "source": "current"}
                for rid, g in per_req if g]
    rest = [[{"requirement_id": rid, "table_ids": x, "default": False, "source": "candidate"} for x in g[1:]]
            for rid, g in per_req]
    options = list(defaults)
    k = 0
    while len(options) < cap and any(rest):
        if rest[k % len(rest)]:
            options.append(rest[k % len(rest)].pop(0))
        k += 1
    options = options[:cap]
    for o in options:
        o["tables"] = _labelled(o["table_ids"], notes)
    return options, {d["requirement_id"]: d["table_ids"] for d in defaults}


# ---------------------------------------------------------------- plan 10: questions of one turn


MAX_QUESTIONS = 3  # plan 10 section 3: questions asked in one turn; the rest wait (deferred)


def _with_bundle_mates(ids: list[str], live: list[Json], by_rid: dict) -> list[str]:
    """A bundle (``table_group_hint``) is asked as a whole: one asked member brings the other live members."""
    live_ids = [r["id"] for r in live]
    out = list(ids)
    for rid in ids:
        mates = (((by_rid.get(rid) or {}).get("signals") or {}).get("bundle") or {}).get("requirement_ids") or []
        out += [m for m in mates if m in live_ids and m not in out]
    return out


def _option_rows(bundles: list[Json], notes: Json) -> list[Json]:
    rows: list[Json] = []
    for b in bundles:
        if not b["table_ids"] or any(r["table_ids"] == b["table_ids"] for r in rows):
            continue
        rows.append({"value": b.get("value") or [], "table_ids": list(b["table_ids"]),
                     "tables": _labelled(b["table_ids"], notes), "complete": bool(b.get("complete", True)),
                     "default": b.get("source") == "current", "source": b.get("source") or "candidate"})
    return rows[:MAX_OPTIONS]


def _flat_question(requirement_ids: list[str], reasons: list[str], options: list[Json]) -> Json:
    """One question from a flat table option list (readings, target families, uncovered cells)."""
    kept = [o for o in options if o.get("table_ids")][:MAX_OPTIONS]
    rows = [{"value": [], "table_ids": list(o["table_ids"]), "tables": o.get("tables") or [], "complete": True,
             "default": bool(o.get("default")), "source": o.get("source") or "candidate"}
            for o in kept]
    rids = list(dict.fromkeys(requirement_ids))
    dimension = "+".join(reasons) or "table"
    # plan 11 R1 (C): options of readings fix a reading (their requirements are numbered anew: no assignment);
    # the others give the tables to the option's requirement (all the question's requirements when it names none)
    reading = any(str(r["source"]).startswith("reading:") for r in rows)
    for o, row in zip(kept, rows):
        assignment = {} if reading else {rid: row["table_ids"] for rid in
                                         ([o["requirement_id"]] if o.get("requirement_id") in rids else rids)}
        _contract_option(row, dimension, assignment)
    # the ask cause: the last reason is the one that made the table ask (readings_differ, target_families,
    # uncovered_cells); the reasons before it are what left the interpretation undecided
    return {"requirement_ids": rids, "dimension": dimension, "values": [], "options": rows, "deferred": False,
            "cause": reasons[-1] if reasons else "table", "open": len(rows) < 2,
            "effect_kind": "change_reading" if reading else "pick_assignment"}


def _questions(requirement_ids: list[str], selections: list[Json], notes: Json, reasons: list[str]) -> list[Json]:
    """Plan 10 section 3-4: the questions of one turn, from the D records: per requirement and dimension, options
    value -> cover bundle (only bundles giving every cell the current one gives); the same dimension with the same
    options over several requirements is one question; past ``MAX_QUESTIONS`` the rest is deferred (those
    requirements go on with confirm). A requirement without a D record gets one question over its table groups."""
    by_rid = {s["requirement_id"]: s for s in selections}
    out: list[Json] = []

    def put(rid: str, dimension: str, values: list, rows: list[Json], cause: str, effect_kind: str) -> None:
        rows = [o for o in rows if o["complete"]]
        if len(rows) < 2:
            return
        key = (dimension, [o["table_ids"] for o in rows])
        for q in out:
            if (q["dimension"], [o["table_ids"] for o in q["options"]]) == key:
                q["requirement_ids"].append(rid)
                return
        out.append({"requirement_ids": [rid], "dimension": dimension, "values": list(values), "options": rows,
                    "deferred": False, "cause": cause, "open": False, "effect_kind": effect_kind})

    def kind_of(dimension: str, rows: list[Json]) -> str:
        # plan 11 R1 (F6): a basis-axis dimension whose every option stands for one value sets a condition; any
        # other dimension (family, qualifier, source, agency, other, joined axes) picks an assignment
        rows = [o for o in rows if o["complete"]]
        if dimension in _basis_axes() and rows and all(len(o.get("value") or []) == 1 for o in rows):
            return "set_condition"
        return "pick_assignment"

    for rid in requirement_ids:
        s = by_rid.get(rid) or {}
        d = (s.get("signals") or {}).get("dimensions")
        if d is None:
            groups = _groups(s) if s else []
            put(rid, "table", [], _option_rows([{"table_ids": g, "source": "current" if k == 0 else "candidate"}
                                                for k, g in enumerate(groups)], notes),
                reasons[0] if reasons else "table", "pick_assignment")
            continue
        bundles = d.get("bundles") or []
        current = [b for b in bundles if b["source"] == "current"]
        if "family_alternatives" in (d.get("ask_reasons") or []):
            fam = next((x for x in d.get("dimensions") or [] if x["class"] == "family"), None)
            put(rid, "family", (fam or {}).get("values") or [],
                _option_rows(current + [b for b in bundles if b["source"] == "family"], notes),
                "family_alternatives", "pick_assignment")
        for x in d.get("dimensions") or []:
            if x["class"] != "unset" or not any(r.startswith("unspecified") for r in d.get("ask_reasons") or []):
                continue
            rows = _option_rows(current + [b for b in bundles if b["source"] == "replacement"
                                           and b.get("dimension") == x["dimension"]], notes)
            cause = "unspecified_mixed_basis" if MIXED_KIND in (x.get("kind") or "").split("+") \
                else "unspecified_dimension"
            put(rid, x["dimension"], x.get("values") or [], rows, cause, kind_of(x["dimension"], rows))
    for k, q in enumerate(out):
        q["deferred"] = k >= MAX_QUESTIONS
        for o in q["options"]:  # plan 11 R1: every requirement the question is for gets the option's tables
            _contract_option(o, q["dimension"], {rid: o["table_ids"] for rid in q["requirement_ids"]})
    return out
