"""Plan 10 D step (select/differences.py) and the questions of one turn (agent/question_route.py), each rule in
both directions, on made-up tables and made-up cover records. Table IDs and names are made up."""

from __future__ import annotations

from claview_a_legacy.agent.question_route import MAX_QUESTIONS, question_route
from claview_a_legacy.contract.check import check_selection_route
from claview_a_legacy.select import differences as df
from claview_a_legacy.select.state import decide_state


class _V:
    def __init__(self, title):
        self.title, self.periods, self.items, self.dims, self.axis_roles = title, [("Y", "2000", "2025")], [], [], {}


def _alt(fam, tables, score=10.0):
    return {"family": fam, "tables": tables, "covered": {1}, "complete": True, "truncated": False,
            "best_score": score, "score_sum": score, "tier": 1.0}


def _cover(words, sources, constraints=("B1",), basis=("B1",), tables=("A", "B", "C")):
    return {"requirement": {"constraints": list(constraints), "proof": {"basis": list(basis)}},
            "words": words, "word_sources": sources, "provided": {t: {1} for t in tables}, "cells": {1},
            "scores": {t: 1.0 for t in tables}}


# ---------------------------------------------------------------- (가) dimensions the question set


def test_set_dimension_settles_by_the_table_name():
    alts = [_alt("F", ["A"]), _alt("G", ["B"])]
    cover = _cover({"A": {"B1": "unknown"}, "B": {"B1": "match"}}, {"A": {"B1": "none"}, "B": {"B1": "title"}})
    kept, dropped, settled = df.set_dimension_filter(alts, cover, enabled=True)
    assert [a["family"] for a in kept] == ["G"] and settled
    assert dropped == [{"family": "F", "table_ids": ["A"], "condition_id": "B1"}]


def test_set_dimension_found_in_an_item_only_drops_nothing():
    alts = [_alt("F", ["A"]), _alt("G", ["B"])]
    cover = _cover({"A": {"B1": "unknown"}, "B": {"B1": "match"}}, {"A": {"B1": "none"}, "B": {"B1": "item"}})
    assert df.set_dimension_filter(alts, cover, enabled=True) == (alts, [], False)


def test_values_of_a_split_axis_and_ambiguous_words_never_filter():
    """Two values of one axis are cells (not constraints); a word outside the requirement's proof is not used."""
    alts = [_alt("F", ["A"]), _alt("G", ["B"])]
    words = {"A": {"B1": "unknown"}, "B": {"B1": "match"}}
    sources = {"A": {"B1": "none"}, "B": {"B1": "title"}}
    assert df.set_dimension_filter(alts, _cover(words, sources, constraints=()), enabled=True)[1] == []
    assert df.set_dimension_filter(alts, _cover(words, sources, basis=()), enabled=True)[1] == []
    assert df.set_dimension_filter(alts, _cover(words, sources), enabled=False)[1] == []


def test_settled_needs_every_set_word_by_the_name():
    alts = [_alt("F", ["A"]), _alt("G", ["B"])]
    words = {"A": {"B1": "unknown", "B2": "match"}, "B": {"B1": "match", "B2": "match"}}
    sources = {"A": {"B1": "none", "B2": "title"}, "B": {"B1": "title", "B2": "item"}}
    kept, dropped, settled = df.set_dimension_filter(alts, _cover(words, sources, ("B1", "B2"), ("B1", "B2")),
                                                     enabled=True)
    assert [a["family"] for a in kept] == ["G"] and dropped and not settled


def test_one_bundle_left_by_a_set_dimension_is_confirm_not_auto():
    analysis = {"interpretation": {"status": "ok", "requirements": [{"id": "r1", "status": "ok"}]},
                "judgement": {"certain": True}}
    chosen = {"tables": ["T1"], "complete": True, "truncated": False}
    recheck = {"floor": "auto", "tables": [], "covered": 1, "cells": 1, "unproven": []}
    kw = dict(unsupported=False, no_evidence=False, recheck=recheck, cover_flags=[], signals={})
    base = {"chosen": chosen, "contenders": [chosen], "unspecified": [], "added": [], "differences": []}
    assert decide_state(analysis, "r1", choice={**base, "d_resolved": True}, **kw) == ("confirm", ["d_resolved"])
    assert decide_state(analysis, "r1", choice=base, **kw) == ("auto", [])


# ---------------------------------------------------------------- D record: family score gap, unset dimensions


def _sel(tables=("A",), reasons=(), gap=None, marks=(), alternatives=()):
    return {"requirement_id": "r1", "status": "confirm", "tables": [{"table_id": t} for t in tables],
            "unspecified": list(marks), "reasons": list(reasons), "model_step": "skipped",
            "alternatives": [list(a) for a in alternatives], "signals": {"score_gap": gap}}


def _state(contenders, chosen_family="F", **cover_kw):
    alts = [_alt(f, t) for f, t in contenders]
    return {"choice": {"chosen": next(a for a in alts if a["family"] == chosen_family), "contenders": alts},
            "cover": _cover({}, {}, **cover_kw)}


def test_family_score_gap_resolves_or_asks():
    state = _state([("F", ["A"]), ("G", ["B"])])
    policy = {"family_score_gap": 4.0, "ask_unset_kinds": []}
    got = df.dimension_record(_sel(gap=6.0), state, {}, policy)
    assert got["result"] == "resolved" and got["resolved_by"] == ["score_gap"]
    assert df.dimension_record(_sel(gap=3.0), state, {}, policy)["ask_reasons"] == ["family_alternatives"]
    assert df.dimension_record(_sel(gap=None), state, {}, policy)["result"] == "ask"
    off = {"family_score_gap": None, "ask_unset_kinds": []}
    assert df.dimension_record(_sel(gap=60.0), state, {}, off)["result"] == "ask"


def test_family_score_gap_never_decides_an_undecided_interpretation():
    state = _state([("F", ["A"]), ("G", ["B"])])
    got = df.dimension_record(_sel(gap=60.0, reasons=["interpretation_undecided"]), state, {},
                              {"family_score_gap": 4.0, "ask_unset_kinds": []})
    assert got["result"] == "ask"


def _mark(kind="other", others=("B",)):
    return {"dimension": "x", "kind": kind, "table_ids": ["A"], "other_table_ids": list(others),
            "other_values": ["을기준"]}


VIEWS = {"A": _V("가상표(갑기준)"), "B": _V("가상표(을기준)")}


def test_unset_dimension_kinds_are_asked_by_the_policy():
    state = _state([("F", ["A"])])
    sel = _sel(marks=[_mark()])
    asked = df.dimension_record(sel, state, VIEWS, {"family_score_gap": None, "ask_unset_kinds": ["other"]})
    assert asked["result"] == "ask" and asked["ask_reasons"] == ["unspecified_dimension"]
    assert [b["source"] for b in asked["bundles"]] == ["current", "replacement"]
    kept = df.dimension_record(sel, state, VIEWS, {"family_score_gap": None, "ask_unset_kinds": ["mixed_basis"]})
    assert kept["result"] == "single" and kept["ask_reasons"] == []


def test_a_value_the_question_writes_removes_the_stand_in_lacking_it():
    state = _state([("F", ["A"])])
    policy = {"family_score_gap": None, "ask_unset_kinds": ["other"]}
    got = df.dimension_record(_sel(marks=[_mark()]), state, VIEWS, policy, qtext="가상표갑기준을보여줘")
    assert got["result"] == "resolved" and got["resolved_by"] == ["written_value"]
    assert [d["class"] for d in got["dimensions"]] == ["set"]
    other = df.dimension_record(_sel(marks=[_mark()]), state, VIEWS, policy, qtext="가상표를보여줘")
    assert other["result"] == "ask"


# ---------------------------------------------------------------- route: D record, questions, contract


def _q(*rids):
    return {"interpretation": {"status": "ok", "requirements": [
        {"id": r, "status": "ok", "indicator": {"name": "갑"}, "reasons": [], "span": [0, 1]} for r in rids]},
        "judgement": {"certain": True, "failed_conditions": []}, "fragments": []}


def _d(result, ask=(), bundles=None, dims=None):
    bundles = bundles or [{"table_ids": ["A"], "source": "current", "complete": True, "value": []},
                          {"table_ids": ["B"], "source": "family", "complete": True, "value": [], "family": "G"}]
    dims = dims or [{"dimension": "family", "class": "family", "kind": "family", "values": ["F", "G"], "gap": 1.0}]
    return {"bundles": bundles, "dimensions": dims, "result": result, "ask_reasons": list(ask),
            "resolved_by": ["score_gap"] if result == "resolved" else [], "gap": 1.0, "set_would_drop": []}


def _rsel(rid, d, tables=("A",), alternatives=(("B",),), bundle=None):
    s = {"requirement_id": rid, "status": "confirm", "tables": [{"table_id": t} for t in tables],
         "model_step": "pending", "unspecified": [], "reasons": ["family_alternatives"],
         "alternatives": [list(a) for a in alternatives], "signals": {"dimensions": d}}
    if bundle:
        s["signals"]["bundle"] = {"requirement_ids": list(bundle)}
    return s


def test_route_follows_the_d_record():
    resolved = question_route(_q("r1"), [_rsel("r1", _d("resolved"))])
    assert resolved["kind"] == "proceed" and "d_resolved" in resolved["reasons"] and resolved["confirm"]
    asked = question_route(_q("r1"), [_rsel("r1", _d("ask", ["family_alternatives"]))])
    assert asked["kind"] == "ask_selection" and asked["reasons"] == ["family_alternatives"]
    assert [q["dimension"] for q in asked["questions"]] == ["family"]
    assert [o["table_ids"] for o in asked["questions"][0]["options"]] == [["A"], ["B"]]
    assert check_selection_route(asked) == []


def test_options_that_are_not_whole_bundles_are_left_out():
    bundles = [{"table_ids": ["A"], "source": "current", "complete": True, "value": []},
               {"table_ids": ["B"], "source": "family", "complete": False, "value": []},
               {"table_ids": ["C"], "source": "family", "complete": True, "value": []}]
    got = question_route(_q("r1"), [_rsel("r1", _d("ask", ["family_alternatives"], bundles))])
    assert [o["table_ids"] for o in got["questions"][0]["options"]] == [["A"], ["C"]]


def test_a_bundle_is_asked_as_a_whole():
    sels = [_rsel("r1", _d("ask", ["family_alternatives"]), bundle=("r1", "r2")),
            _rsel("r2", _d("single"), tables=("A",), alternatives=(), bundle=("r1", "r2"))]
    got = question_route(_q("r1", "r2"), sels)
    assert got["kind"] == "ask_selection" and got["requirement_ids"] == ["r1", "r2"]


def test_questions_past_three_are_deferred_and_same_ones_merge():
    rids = [f"r{k}" for k in range(1, 6)]
    sels = []
    for k, rid in enumerate(rids):
        b = [{"table_ids": [f"A{k}"], "source": "current", "complete": True, "value": []},
             {"table_ids": [f"B{k}"], "source": "family", "complete": True, "value": []}]
        sels.append(_rsel(rid, _d("ask", ["family_alternatives"], b), tables=(f"A{k}",)))
    got = question_route(_q(*rids), sels)
    assert [q["deferred"] for q in got["questions"]] == [False] * MAX_QUESTIONS + [True] * 2
    assert check_selection_route(got) == []
    same = [_rsel(r, _d("ask", ["family_alternatives"])) for r in ("r1", "r2")]
    merged = question_route(_q("r1", "r2"), same)
    assert len(merged["questions"]) == 1 and merged["questions"][0]["requirement_ids"] == ["r1", "r2"]


def _route_with(questions):
    return {"kind": "ask_selection", "requirement_ids": ["r1"], "options": [], "default": None, "reasons": [],
            "partial": False, "confirm": True, "questions": questions}


def _question(options, deferred=False, rids=("r1",)):
    return {"requirement_ids": list(rids), "dimension": "family", "values": [], "options": options,
            "deferred": deferred}


def test_contract_checks_the_questions():
    two = [{"table_ids": ["A"], "complete": True}, {"table_ids": ["B"], "complete": True}]
    assert check_selection_route(_route_with([_question(two)]), {"r1"}) == []
    assert check_selection_route(_route_with([_question(two[:1])]))  # one option only
    six = [{"table_ids": [f"T{k}"], "complete": True} for k in range(6)]
    assert check_selection_route(_route_with([_question(six)]))
    part = [two[0], {"table_ids": ["B"], "complete": False}]
    assert check_selection_route(_route_with([_question(part)]))
    assert check_selection_route(_route_with([_question(two, rids=("r9",))]), {"r1"})
    assert check_selection_route(_route_with([_question(two)] * 4))
    assert check_selection_route(_route_with([_question(two)] * 3 + [_question(two, deferred=True)])) == []
