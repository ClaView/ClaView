"""Unit tests of the table-selection pieces (plan 04, 4-1 to 4-6) on made-up data.

Least cover (exact search beats a greedy pick, dominated tables, the 12-table cut), unspecified dimensions (cap 3,
most recent start first, a mate that loses on a basis word stays out), the decision-state table row by row, query
groups, the cover recheck, period resolution and the snapshot lag, retry words, the sub-graph wiring and the record
settings. Table IDs and names are made up.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "spec"))
from spec_support import build_layer, itm, table  # noqa: E402

from claview_a import paths  # noqa: E402
from claview_a.interpret.pipeline import interpret_question  # noqa: E402
from claview_a.select import candidates as cand  # noqa: E402
from claview_a.select import cover as cv  # noqa: E402
from claview_a.select import graph as sg  # noqa: E402
from claview_a.select.evidence import CHECK_ORDER, resolve_period  # noqa: E402
from claview_a.select.state import collect_selections, decide_state  # noqa: E402


# ---------------------------------------------------------------- least cover


def _equal(names) -> tuple[dict, dict]:
    return {t: 1.0 for t in names}, {t: k for k, t in enumerate(names)}


def test_exact_cover_beats_the_greedy_pick():
    prov = {"S1": {1, 2, 3, 4}, "S2": {1, 2, 5}, "S3": {3, 4, 6}, "S4": {5}, "S5": {6}}
    score, order = _equal(prov)
    got = cv.best_cover(list(prov), {1, 2, 3, 4, 5, 6}, prov, score, order)
    assert sorted(got["tables"]) == ["S2", "S3"] and got["complete"] and not got["truncated"]


def test_same_size_goes_to_the_higher_rank_sum():
    prov = {"A": {1}, "B": {2}, "C": {1, 2}, "D": {1, 2}}
    score, order = {"A": 1.0, "B": 1.0, "C": 3.0, "D": 5.0}, {"A": 0, "B": 1, "C": 2, "D": 3}
    assert cv.best_cover(list(prov), {1, 2}, prov, score, order)["tables"] == ["D"]


def test_dominated_tables_are_removed_first():
    prov = {"A": {1}, "B": {1, 2}, "C": {1, 2}}
    score, order = {"A": 1.0, "B": 2.0, "C": 2.0}, {"A": 0, "B": 1, "C": 2}
    assert cv._undominated(list(prov), prov, score, order) == ["B"]


def test_more_than_twelve_tables_is_marked_truncated():
    names = [f"T{k:02d}" for k in range(14)]
    prov = {t: {k} for k, t in enumerate(names)}
    score = {t: float(20 - k) for k, t in enumerate(names)}
    order = {t: k for k, t in enumerate(names)}
    got = cv.best_cover(names, set(range(14)), prov, score, order)
    assert got["truncated"] and got["complete"] and len(got["tables"]) == 14


def test_no_complete_group_takes_the_most_cells():
    prov = {"A": {1}, "B": {1, 2}}
    score, order = _equal(prov)
    got = cv.best_cover(list(prov), {1, 2, 3}, prov, score, order)
    assert got["tables"] == ["B"] and not got["complete"] and got["covered"] == {1, 2}


# ---------------------------------------------------------------- unspecified dimensions


class _View:
    def __init__(self, title, start):
        self.title, self.periods = title, [("M", start, "2026.08")]


VIEWS = {"MR": _View("가상총통화(말잔, 원계열)", "2005.01"), "MS": _View("가상총통화(말잔, 계절조정계열)", "2004.01"),
         "PR": _View("가상총통화(평잔, 원계열)", "2003.01"), "PS": _View("가상총통화(평잔, 계절조정계열)", "2002.01")}
CELLS = {("x",)}


def test_unspecified_puts_in_three_most_recent_and_marks_more():
    """4-4: the cap keeps the three most recent starts; the first pick (PS, the oldest) is not kept by force
    (Codex r1 #11)."""
    prov = {t: set(CELLS) for t in VIEWS}
    tables, marks = cv.unspecified(["PS"], list(VIEWS), prov, set(VIEWS), VIEWS)
    assert marks == [{"dimension": "seasonal+stock", "table_ids": ["MR", "MS", "PR"], "more": True}]
    assert tables == ["MR", "MS", "PR"]


def test_unspecified_leaves_out_a_mate_that_loses_on_a_basis_word():
    prov = {t: set(CELLS) for t in ("MR", "MS")}
    words = {"MR": {"B1": "match"}, "MS": {"B1": "conflict"}}
    tables, marks = cv.unspecified(["MR"], ["MR", "MS"], prov, {"MR", "MS"}, VIEWS, words)
    assert tables == ["MR"] and marks == []


def test_chosen_tables_that_differ_by_a_qualifier_are_marked():
    """Codex r1 #3: two tables both needed for the cover (one gives 갑, the other 을) whose basis differs
    (명목/실질) are an unspecified dimension as well."""
    views = {"N": _View("가상지표(명목)", "2000.01"), "R": _View("가상지표(실질)", "2000.01")}
    prov = {"N": {("갑",)}, "R": {("을",)}}
    tables, marks = cv.unspecified(["N", "R"], ["N", "R"], prov, {"N", "R"}, views)
    assert tables == ["N", "R"]
    assert marks == [{"dimension": "price", "table_ids": ["N", "R"], "more": False}]


def test_chosen_tables_that_differ_by_year_range_only_are_not_marked():
    views = {"A": _View("가상재무표(1998~2007)", "1998.01"), "C": _View("가상재무표(2009~)", "2009.01")}
    prov = {"A": {("a",)}, "C": {("c",)}}
    assert cv.unspecified(["A", "C"], ["A", "C"], prov, {"A", "C"}, views) == (["A", "C"], [])


class _Item:
    def __init__(self, name):
        self.name_raw = name


def _v(title, values=()):
    view = _View(title, "2000.01")
    view.items = [_Item(v) for v in values]
    return view


def test_dimension_names_come_from_basis_axes():
    dim = cv.dimension_of
    assert dim(_v("가상표(명목)"), _v("가상표(실질)")) == "price"
    assert dim(_v("가상표(1998~2007)"), _v("가상표(2009~)")) is None
    assert dim(_v("가상표(업권별, 분기)"), _v("가상표(용도별, 분기)")) == "classification+qualifier"
    assert dim(_v("가상표(가상기관, 분기)"), _v("가상표(다른기관, 분기)")) == "qualifier"
    # Codex r2 #3: the same title, basis told apart by the axis values
    assert dim(_v("가상통화량", ["가상통화량", "원계열"]), _v("가상통화량", ["가상통화량", "계절조정계열"])) == "seasonal"


# ---------------------------------------------------------------- decision state (plan 04, 4-6, one row each)


ANALYSIS = {"interpretation": {"status": "ok", "requirements": [{"id": "r1", "status": "ok"}]},
            "judgement": {"certain": True}}
CHOSEN = {"tables": ["T1"], "complete": True, "truncated": False}
CHOICE = {"chosen": CHOSEN, "contenders": [CHOSEN], "unspecified": [], "added": [], "differences": []}
RECHECK = {"floor": "auto", "tables": [], "covered": 1, "cells": 1, "unproven": []}


def _miss(check, cid=None, status=None):
    return {"table_id": "T1", "check": check, "condition_id": cid, "status": status}


def _state(analysis=ANALYSIS, *, unsupported=False, no_evidence=False, choice=CHOICE, recheck=RECHECK, flags=(),
           signals=None):
    return decide_state(analysis, "r1", unsupported=unsupported, no_evidence=no_evidence, choice=choice,
                        recheck=recheck, cover_flags=list(flags), signals=signals)


def _with(**kw) -> dict:
    out = copy.deepcopy(CHOICE)
    out.update(kw)
    return out


@pytest.mark.parametrize("kwargs, want", [
    ({"unsupported": True}, ("no_table", ["unsupported"])),
    ({"no_evidence": True}, ("no_table", ["no_evidence"])),
    ({"recheck": {**RECHECK, "floor": "no_table"}}, ("no_table", ["no_cover"])),
    ({"choice": {**CHOICE, "chosen": None}}, ("no_table", ["no_cover"])),
    ({"recheck": {**RECHECK, "floor": "confirm"}}, ("confirm", ["partial_cover"])),
    ({"recheck": {**RECHECK, "unproven": [_miss("unit", "U1", "conflict")]}}, ("confirm", ["unproven_condition"])),
    ({"recheck": {**RECHECK, "unproven": [_miss("period", None, "unknown")]}}, ("confirm", ["period_unknown"])),
    ({"signals": {"term_tables_truncated": True}}, ("confirm", ["term_tables_truncated"])),
    ({"choice": _with(unspecified=[{"dimension": "price", "table_ids": ["T1", "T2"], "more": False}])},
     ("confirm", ["unspecified"])),
    ({"choice": _with(unspecified=[{"dimension": "price", "table_ids": ["T1", "T2", "T3"], "more": True}])},
     ("confirm", ["unspecified", "unspecified_more"])),
    ({"choice": _with(contenders=[CHOSEN, {"tables": ["T9"]}])}, ("confirm", ["family_alternatives"])),
    ({"flags": ["period_unknown"]}, ("confirm", ["period_unknown"])),
    ({"flags": ["period_lag"]}, ("confirm", ["period_unknown"])),
    ({"choice": _with(chosen={**CHOSEN, "truncated": True})}, ("confirm", ["search_truncated"])),
    ({}, ("auto", [])),
], ids=["unsupported", "no_evidence", "recheck_none", "no_choice", "partial", "unproven", "period_check_unknown",
        "term_tables_cut",
        "unspecified", "unspecified_more",
        "families", "period_unknown", "period_lag", "truncated", "auto"])
def test_decision_state_rows(kwargs, want):
    assert _state(**kwargs) == want


def test_undecided_interpretation_is_confirm():
    undecided = {**ANALYSIS, "judgement": {"certain": False}}
    assert _state(undecided) == ("confirm", ["interpretation_undecided"])
    ask = {"interpretation": {"status": "ask", "requirements": [{"id": "r1", "status": "ok"}]}, "judgement": {}}
    assert _state(ask) == ("confirm", ["interpretation_undecided"])


# ---------------------------------------------------------------- query groups


def _sel(rid, tables, status="auto"):
    return {"requirement_id": rid, "tables": [{"table_id": t} for t in tables], "status": status}


def test_requirements_sharing_a_table_are_one_query_group():
    sels = [_sel("r1", ["T1"]), _sel("r2", ["T2"]), _sel("r3", ["T1", "T3"]), _sel("r4", [], "no_table")]
    assert collect_selections(sels) == [{"requirement_ids": ["r1", "r3"], "table_ids": ["T1", "T3"]},
                                        {"requirement_ids": ["r2"], "table_ids": ["T2"]}]


def test_a_later_table_joins_two_groups():
    sels = [_sel("r1", ["T1"]), _sel("r2", ["T2"]), _sel("r3", ["T2", "T1"])]
    assert collect_selections(sels) == [{"requirement_ids": ["r1", "r2", "r3"], "table_ids": ["T1", "T2"]}]


# ---------------------------------------------------------------- periods


def _ex(year=None, alias=None, unit=None, value=None, end=False):
    return {"text": "", "year": year, "alias": alias, "sub_unit": unit, "sub_value": value, "end_mark": end}


def _p(form, start=None, end=None, **kw):
    out = {"form": form, "start_expr": start, "end_expr": end, "open_end": form == "since", "amount": None,
           "unit": None, "relative": False, "invalid": False, "extract_status": "explicit"}
    out.update(kw)
    return out


@pytest.mark.parametrize("attrs, as_of, want", [
    (_p("range", _ex("2010"), _ex("2012")), None, ("2010", "2012")),
    (_p("range", _ex("2026", unit="M", value="1"), _ex(None, unit="M", value="6")), None, ("2026-01", "2026-06")),
    (_p("since", _ex("2015")), None, ("2015", None)),
    (_p("point", _ex("2026", unit="H", value="상")), None, ("2026-01", "2026-06")),
    (_p("point", _ex(alias="작년", end=True), relative=True), "2026-10-12", ("2025-12", "2025-12")),
    (_p("since", _ex(alias="올해"), relative=True), "2026-10-12", ("2026", None)),
    (_p("recent", amount=3, unit="year", relative=True), "2026-10-12", ("2024", "2026")),
])
def test_periods_resolve_from_the_written_form(attrs, as_of, want):
    got = resolve_period(attrs, as_of)
    assert got["status"] == "explicit" and (got["start"], got["end"]) == want


@pytest.mark.parametrize("attrs", [
    _p("since", _ex(alias="올해"), relative=True),  # no reference date
    _p("recent", amount=6, unit="month", relative=True, extract_status="unresolved"),
    _p("point", _ex("2026", unit="M", value="13"), invalid=True),
])
def test_periods_that_cannot_resolve_are_unresolved(attrs):
    assert resolve_period(attrs, None)["status"] == "unresolved"


# ---------------------------------------------------------------- with a synthetic catalog


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    tables = [
        table("T_M", "가상월간표", [itm("A", "1", "가상수치", unit="%"), itm("A", "2", "가상수치합계", unit="%")],
              periods=(("월", "2000.01", "2026.08"),)),
        table("T_O", "가상다른표", [itm("A", "1", "가상수치표본", unit="%")], periods=(("년", "2000", "2010"),)),
        table("T_Y", "가상연간표", [itm("A", "1", "가상연수치", unit="%")], periods=(("년", "2000", "2025"),)),
    ]
    return build_layer(tmp_path_factory.mktemp("select_units"), tables)


def _requirement_cells(question, layer):
    analysis = interpret_question(question, layer)
    views = {"T_M": cv.selection_catalog(layer).views["T_M"]}
    return analysis, cv.requested_cells(analysis, "r1", views, None)


def test_explicit_period_past_the_data_is_not_replaced(layer):
    """Codex r1 #4: an explicit period is never cut back to the data or replaced by the latest data."""
    _, req = _requirement_cells("2026년 9월 가상수치를 보여줘", layer)
    assert [p[0] for p in req["periods"]] == ["2026-09~2026-09"] and not req["flags"]
    out = sg.select_tables(interpret_question("2026년 9월 가상수치를 보여줘", layer), layer)["selections"][0]
    assert out["status"] == "no_table" and out["reasons"] == ["no_cover"]


def test_relative_period_inside_the_snapshot_lag_keeps_its_cells(layer):
    """Codex r2 #7: a relative period keeps its resolved cells (the open year stays a cell); period_lag only marks."""
    analysis = interpret_question("최근 2년 가상수치를 보여줘", layer)
    views = {"T_Y": cv.selection_catalog(layer).views["T_Y"]}
    req = cv.requested_cells(analysis, "r1", views, "2026-10-12")
    assert [p[0] for p in req["periods"]] == ["2025-01~2025-12", "2026-01~2026-12"] and "period_lag" in req["flags"]


def test_request_past_the_lag_keeps_an_open_segment(layer):
    _, req = _requirement_cells("2026년 가상수치를 보여줘", layer)
    assert [p[0] for p in req["periods"]] == ["2026-01~2026-08", "2026-09~2026-12"] and not req["flags"]


def test_verify_choice_drops_a_table_covering_nothing(layer):
    analysis = interpret_question("2005년 가상수치를 보여줘", layer)
    got = cv.verify_choice(analysis, "r1", ["T_M", "T_O"], layer, None, candidates=["T_M", "T_O"])
    assert [t["table_id"] for t in got["tables"]] == ["T_M", "T_O"] and got["floor"] == "auto"
    late = interpret_question("2015년 가상수치를 보여줘", layer)
    got = cv.verify_choice(late, "r1", ["T_O"], layer, None, candidates=["T_M", "T_O"])
    assert got["dropped"] == ["T_O"] and got["floor"] == "no_table"


def test_retry_words_skip_what_was_searched(layer):
    analysis = interpret_question("가상수치를 보여줘", layer)
    words = cand.suggestions(analysis, "r1", layer, used=["가상수치"])
    assert "가상수치" not in words
    assert all(w not in {t["name"] for t in analysis["terms"]} for w in words)


def test_select_tables_without_interpretation_is_empty(layer):
    analysis = interpret_question("가상수치를 보여줘", layer)
    # R3: interpreted requirements fill decisions; absent interpretation still has no decision units.
    assert sg.select_tables(analysis, layer)["decisions"]
    out = sg.select_tables({**analysis, "interpretation": None}, layer)
    # plan 11 R1 (7-11, pre-approved): select_tables also returns facts and decisions, empty here
    assert out == {"selections": [], "selection_groups": [], "evidence": {"evidence": [], "evidence_rows": {}},
                   "facts": {}, "decisions": {}}


def test_out_of_scope_requirement_is_no_table_without_search(layer):
    analysis = interpret_question("가상수치를 보여줘", layer)
    analysis = copy.deepcopy(analysis)
    analysis["interpretation"]["requirements"][0]["status"] = "unsupported"
    s = sg.select_tables(analysis, layer)["selections"][0]
    assert s["status"] == "no_table" and s["reasons"] == ["unsupported"] and s["signals"]["pool"] == 0


# ---------------------------------------------------------------- wiring and settings


def test_requirement_graph_has_the_plan_nodes():
    nodes = set(sg.REQUIREMENT_GRAPH.get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == {"build_queries", "search_catalog", "expand_candidates", "check_evidence", "retry_words",
                     "cover_cells", "choose_table", "verify_choice", "decide_state"}
    outer = set(sg.SELECT_GRAPH.get_graph().nodes) - {"__start__", "__end__"}
    assert outer == {"split_by_requirement", "select_requirement", "collect_selections"}


def test_every_check_is_in_record_mode():
    filters = json.loads(paths.EVIDENCE_FILTERS.read_text(encoding="utf-8"))
    assert set(filters["checks"]) == set(CHECK_ORDER)
    assert set(filters["checks"].values()) == {"record"}


# ---------------------------------------------------------------- Codex r1 findings (one case each)


def test_families_compete_whatever_their_evidence_tier(layer):
    """#2: two complete alternatives of different families stay in competition (pending, confirm) even when their
    indicator tiers differ; the tier only orders them."""
    from claview_a.select.state import choose_table

    a = {"family": "table:T_M", "tables": ["T_M"], "covered": {1}, "complete": True, "truncated": False,
         "best_score": 10.0, "score_sum": 10.0, "tier": 1.0, "proven": True}
    b = {**a, "family": "table:T_O", "tables": ["T_O"], "tier": 0.5, "best_score": 5.0, "score_sum": 5.0}
    views = cv.selection_catalog(layer).views
    cover = {"alternatives": [b, a], "cells": {1}, "views": {t: views[t] for t in ("T_M", "T_O")},
             "provided": {"T_M": {1}, "T_O": {1}}, "usable": ["T_M", "T_O"], "words": {}}
    got = choose_table(cover, layer)
    assert got["chosen"]["family"] == "table:T_M" and len(got["contenders"]) == 2
    assert got["model_step"] == "pending" and len(got["differences"]) == 2


def test_ambiguous_basis_word_gives_no_points():
    """#6: a condition whose attribution is ambiguous (verdict n/a) does not move the rank score."""
    checks = [{"name": "basis", "verdict": "n/a", "source_status": "match", "detail": {"condition_id": "B1"}}]
    assert cv.rank_score({"fused_score": 1.0}, checks, "", None) == 1.0


def test_partial_covers_prefer_fewer_tables_before_rank():
    """#7: same covered cells -> fewer tables -> rank-score sum (plan 04, 4-3)."""
    f1 = {"family": "F1", "tables": ["A", "B"], "covered": {1, 2}, "complete": False, "tier": 1.0,
          "best_score": 22.0, "score_sum": 40.0, "proven": True}
    f2 = {"family": "F2", "tables": ["C"], "covered": {1, 2}, "complete": False, "tier": 1.0,
          "best_score": 21.0, "score_sum": 21.0, "proven": True}
    assert [a["family"] for a in cv.order_alternatives([f1, f2])] == ["F2", "F1"]


def _entry(rid, tid, *, result="match", fails=()):
    checks = [{"name": "indicator", "verdict": "pass" if result != "none" else "fail", "source_status": result,
               "detail": {"result": result, "available": result is not None}}]
    checks += [{"name": n, "verdict": "fail", "source_status": "conflict", "detail": {"condition_id": "B1"}}
               for n in fails]
    return {"requirement_id": rid, "table_id": tid, "checks": checks}


def test_record_counts_per_requirement():
    """#8: an answer table that fails a check where it has indicator evidence is dropped, even if another
    requirement keeps it as a candidate without indicator evidence."""
    from claview_a.select.record import drop_record

    got = {"evidence": {"evidence": [_entry("r1", "T", fails=("basis",)), _entry("r2", "T", result=None)]},
           "selections": [{"requirement_id": "r1", "signals": {}}, {"requirement_id": "r2", "signals": {}}]}
    rec = drop_record(got, [["T"]], ("basis", "period"))
    assert rec["in_pool"] == 1 and rec["dropped"] == {"basis": 1, "period": 0}
    kept = {"evidence": {"evidence": [_entry("r1", "T", fails=("basis",)), _entry("r1", "U")]},
            "selections": [{"requirement_id": "r1", "signals": {}}]}
    assert drop_record(kept, [["T", "U"]], ("basis",))["dropped"] == {"basis": 0}
    none = {"evidence": {"evidence": [_entry("r1", "T", result="none")]},
            "selections": [{"requirement_id": "r1", "signals": {}}]}
    assert drop_record(none, [["T"]], ("basis",))["no_evidence"] == 1


def test_linked_values_are_read_inside_the_bracket(tmp_path):
    """#9: a listed value written earlier in the question too ("대기업 실적과 ...(대기업, 중견기업)") is linked at
    its place inside the bracket."""
    from spec_catalogs import J4_LINKED
    from spec_support import cond

    from claview_a.interpret.text import normalize_text
    from claview_a.select.evidence import EvidenceInput, check_basis

    q = "대기업 실적과 수출을 기업규모별(대기업, 중견기업)로"
    syn = build_layer(tmp_path, J4_LINKED)
    b1 = cond(q, "B1", "basis", "기업규모별", value="기업규모별",
              attrs={"axis": "classification", "value": "기업규모별", "polarity": "+"})
    analysis = {"text": normalize_text(q), "conditions": [b1],
                "interpretation": {"requirements": [{"id": "r1", "condition_ids": ["B1"]}]}}
    rows = [{"condition_id": None, "obj_id": "A", "item_id": k, "name": n, "term": n, "parent_path": [], "depth": 0,
             "match_method": "exact", "polarity": "+"} for k, n in (("1", "대기업"), ("2", "중견기업"))]
    checks, _ = check_basis(EvidenceInput(analysis, syn), "r1", "SYN_K", [{"name": "matched_items",
                                                                             "detail": {"rows": rows}}])
    assert checks[0]["detail"]["status"] == "match" and checks[0]["detail"]["role"] == "in_table"


def test_term_tables_over_the_cap_keep_the_best_searched():
    """#10: a term in 31 tables adds the 30 best by search score (then catalog order) and says it was cut."""
    ids = [f"T{k:02d}" for k in range(31)]
    analysis = {"interpretation": {"requirements": [{"id": "r1", "span": [0, 2], "indicator": {
        "name": "가상", "quote": "가상", "span": [0, 2], "fragment_id": "g1", "path": "exact"}}]},
        "fragments": [{"id": "g1", "span": [0, 2]}],
        "terms": [{"fragment_id": "g1", "name": "가상", "type": "series_name", "table_ids": ids, "path": "exact"}]}
    channels = {"title": {"ranked_full": [{"table_id": "T30", "score": 2.0}], "merged": []},
                "item": {"ranked_full": [], "merged": []}}
    tables, truncated = cand.term_tables(analysis, "r1", channels)
    assert truncated and len(tables) == cand.TERM_TABLE_CAP and tables[0] == "T30" and "T29" not in tables


def test_search_stays_bounded_after_the_cut():
    """#12: 30 tables x 30 cells (one cell each) end fast: at most 12 tables are searched exactly, the rest is
    added greedily and the result is marked truncated."""
    import time

    names = [f"T{k:02d}" for k in range(30)]
    prov = {t: {k} for k, t in enumerate(names)}
    score = {t: float(40 - k) for k, t in enumerate(names)}
    order = {t: k for k, t in enumerate(names)}
    t0 = time.time()
    got = cv.best_cover(names, set(range(30)), prov, score, order)
    assert time.time() - t0 < 2.0
    assert got["truncated"] and got["complete"] and len(got["tables"]) == 30

# ---------------------------------------------------------------- Codex r2 findings


def test_order_has_no_proof_step():
    """r2 #8: same cells and table count -> rank-score sum decides (plan 04, 4-3); being proven is no step."""
    a = {"family": "A", "tables": ["A1"], "covered": {1}, "complete": True, "tier": 1.0, "best_score": 40.0,
         "score_sum": 40.0, "proven": False}
    b = {**a, "family": "B", "tables": ["B1"], "best_score": 20.0, "score_sum": 20.0, "proven": True}
    assert [x["family"] for x in cv.order_alternatives([b, a])] == ["A", "B"]


def test_cut_term_tables_that_give_the_target_are_restored(tmp_path):
    """Plan 17: all direct candidates survive the search cap, including the target's table beyond the cap.
    A fully restored direct list is not an unfinished search."""
    tables = [table(f"T{k:02d}", f"가상표{k:02d}", [itm("A", "1", "가상지표"),
                                                 itm("REG", "2", "부산" if k < 31 else "서울", obj_name="지역", order=2)],
                    periods=(("년", "2000", "2025"),)) for k in range(1, 32)]
    syn = build_layer(tmp_path, tables)
    ids = [f"T{k:02d}" for k in range(1, 32)]
    q = "가상지표 서울"
    analysis = {"text": {"raw": q}, "conditions": [], "fragments": [{"id": "g1", "span": [0, 4]}],
                "terms": [{"fragment_id": "g1", "name": "가상지표", "type": "series_name", "table_ids": ids,
                           "path": "exact"}],
                "interpretation": {"requirements": [{"id": "r1", "span": [0, 7], "condition_ids": [],
                                                     "indicator": {"name": "가상지표", "quote": "가상지표",
                                                                   "span": [0, 4], "fragment_id": "g1",
                                                                   "path": "exact"},
                                                     "targets": [{"name": "서울", "quote": "서울", "span": [5, 7]}]}]}}
    channels = {"title": {"ranked_full": [], "merged": []}, "item": {"ranked_full": [], "merged": []}}
    info: dict = {}
    pool = cand.expand_candidates(analysis, "r1", channels, syn, info, candidate_policy="union")
    assert "T31" in {p["table_id"] for p in pool}
    assert not info["term_tables_truncated"] and info["term_tables_restored"] == ["T31"]
    assert {p["table_id"] for p in pool} == set(ids)


def test_a_part_of_a_listed_word_is_no_link(tmp_path):
    """r2 #9: "기업" matched before the bracket is not the listed "대기업" (a part of the word); only whole listed
    words link, so one linked value is left and the link stays unproven."""
    from spec_support import cond

    from claview_a.interpret.text import normalize_text
    from claview_a.select.evidence import EvidenceInput, check_basis

    q = "기업 수출을 기업규모별(대기업, 중견기업)로"
    syn = build_layer(tmp_path, [table("SYN", "가상 서비스 수출(기관형태별)",
                                       [itm("A", "1", "기업", obj_name="구분"), itm("A", "2", "중견기업", obj_name="구분")],
                                       periods=(("년", "2010", "2025"),))])
    b1 = cond(q, "B1", "basis", "기업규모별", value="기업규모별",
              attrs={"axis": "classification", "value": "기업규모별", "polarity": "+"})
    analysis = {"text": normalize_text(q), "conditions": [b1],
                "interpretation": {"requirements": [{"id": "r1", "condition_ids": ["B1"]}]}}
    rows = [{"condition_id": None, "obj_id": "A", "item_id": k, "name": n, "term": n, "parent_path": [], "depth": 0,
             "match_method": "exact", "polarity": "+"} for k, n in (("1", "기업"), ("2", "중견기업"))]
    checks, _ = check_basis(EvidenceInput(analysis, syn), "r1", "SYN", [{"name": "matched_items",
                                                                           "detail": {"rows": rows}}])
    assert checks[0]["detail"]["status"] == "unknown"

# ---------------------------------------------------------------- representative policy (decision 5 as changed)


class _AxItem:
    def __init__(self, obj_id, name):
        self.obj_id, self.name_raw = obj_id, name


def _kv(title, values=(), periods=(("Y", "2000", "2025"),), agency=None):
    view = _View(title, "2000")
    view.periods = list(periods)
    view.items = [_AxItem("A", v) for v in values]
    view.dims = [("A", "항목")]
    if agency:
        view.dims.append(("SRC", "출처"))
        view.items.append(_AxItem("SRC", agency))
    return view


@pytest.mark.parametrize("a, b, kinds", [
    (_kv("가상표(원계열)"), _kv("가상표(계절조정계열)"), {"mixed_basis"}),
    (_kv("가상표", ["원계열"]), _kv("가상표", ["계절조정계열"]), {"mixed_basis"}),
    (_kv("가상표(실적)"), _kv("가상표(전망)"), {"survey"}),
    (_kv("가상표(분기)"), _kv("가상표(분기 및 연간)"), {"frequency"}),
    (_kv("가상표(예금은행)"), _kv("가상표(비은행)"), {"other"}),
    (_kv("가상표", agency="집계기관갑"), _kv("가상표", agency="집계기관을"), {"other"}),
    (_kv("가상표", periods=(("Y", "2000", "2024"),)), _kv("가상표"), {"source"}),
    (_kv("가상표(1998~2007)"), _kv("가상표(2009~)"), {"period"}),
    (_kv("가상표", periods=(("Q", "2000 1/4", "2025 4/4"),)), _kv("가상표"), {"frequency"}),
], ids=["basis_title", "basis_values", "survey", "frequency_words", "other_words", "agency_values", "source",
        "period", "frequencies_only"])
def test_dimension_kinds_from_axis_values_and_qualifiers(a, b, kinds):
    assert set(cv.dimension_kinds(a, b)) == kinds


def test_latest_group_keeps_basis_values_apart():
    """Codex r3 #2: "the latest" is computed within one basis value; a year-range split stays one group."""
    assert cv.latest_group(_kv("가상산출(명목)"), "f") != cv.latest_group(_kv("가상산출(실질)"), "f")
    assert cv.latest_group(_kv("가상물량표(2000~2010)"), "f") == cv.latest_group(_kv("가상물량표(2011~)"), "f")


def test_policy_file_defaults_to_representative():
    policy = json.loads(paths.SELECTION_POLICY.read_text(encoding="utf-8"))
    assert policy["unspecified_policy"] == "representative" and cv.DEFAULT_POLICY == "representative"
    assert set(policy["basis_axis_kinds"].values()) <= {"mixed_basis", "survey", "other"}

def test_survey_convention_applies_beside_a_mixed_basis():
    """Codex r4 #6: dimensions one after another: the 실적 convention holds although a seasonal (mixed basis)
    difference is marked; the 전망 table ranked first does not become the representative."""
    views = {"OR": _kv("가상업황조사(전망, 원계열)"), "AR": _kv("가상업황조사(실적, 원계열)"),
             "AS": _kv("가상업황조사(실적, 계절조정계열)"), "OS": _kv("가상업황조사(전망, 계절조정계열)")}
    prov = {t: {("x",)} for t in views}
    score = {"OR": 30.0, "AR": 20.0, "AS": 10.0, "OS": 10.0}
    order = {t: k for k, t in enumerate(views)}
    members = {"OR": list(views)}
    tables, marks, conventions = cv.representatives(["OR"], members, prov, set(views), views, {}, score, order,
                                                    {("x",)})
    # user decision 2026-10-20 01:57 (plan 10: conventions emptied): no 실적 default, the survey view is marked
    assert tables == ["OR"] and conventions == []
    assert marks and "mixed_basis" in marks[0]["kind"] and "survey" in marks[0]["kind"]
    assert marks[0]["table_ids"] == ["OR"]
