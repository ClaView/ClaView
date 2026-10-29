"""Independent evaluation standard and scoring (plan 38; users 2026-10-28 16:23 and 18:00 UTC), on made-up tables,
questions and run records only: no evaluation question, no model, no network. Expectations by hand, in normal/error
pairs."""

from __future__ import annotations

import copy
import json

import pytest

from tl_eval import indep as I
from tl_eval import report as R
from tl_eval import score as S

AS_OF = "2026-10-29"
TABLES = [("DT_X01", "가상지표(원계열)", ["원계열"]), ("DT_X02", "가상지표(계절조정)", ["계절조정"]),
          ("DT_X03", "가상지표(구)", ["구"]), ("DT_Y01", "다른지표", []), ("DT_Z01", "관계없는지표", [])]
TITLES = {t: ti for t, ti, _ in TABLES}
COVERAGE = {t: {"M": (2000 * 12, 2026 * 12 + 6), "Q": (2000 * 12, 2026 * 12 + 5)} for t in TITLES}
QUESTION = "2024년 가상지표 월별 추이를 보여 줘"


def part(table, a, b, freq="M"):
    return {"table": table, "frequency": freq, "from": a, "to": b}


def combo(cid, *parts, status="confirmed"):
    return {"id": cid, "status": status, "parts": list(parts)}


def final_of(*combos, **kw):
    return {"frequency": "M", "basis": [], "point_in_period": None, "relative": [], "combinations": list(combos), **kw}


def target(**kw) -> dict:
    t = {"id": "r1", "text": "가상지표", "occurrence": 1, "representative": ["DT_X01"], "equivalents": {},
         "ask": None, "final": final_of(combo("c1", part("DT_X01", "2024-01", "2024-12"))), "holds": {}}
    t.update(kw)
    return t


def answer(targets=None, expected="select", type_="T1", qid="Q001", question=QUESTION) -> dict:
    return {"qid": qid, "type": type_, "question": question, "expected": expected,
            "targets": targets if targets is not None else [target()]}


def case_of(row: dict, part_: str = "indep60") -> dict:
    return I.build([row], AS_OF, part_, tables=set(TITLES), coverage=COVERAGE)["cases"][0]


def output(stats, choices=(), asks=(), missing=()) -> dict:
    return {"statistics": [{"id": sid, "quote": q, "name": q} for sid, q in stats], "splitters": [], "periods": [],
            "frequencies": [], "calculations": [], "alternatives": [], "missing": list(missing),
            "choices": [{"for": [sid], "table": t, "evidence": [{"kind": "title", "text": TITLES[t]}]}
                        for sid, t in choices],
            "asks": list(asks)}


def option(value: str, table: str) -> dict:
    return {"value": value, "table": table, "evidence": [{"kind": "title", "text": TITLES[table]}]}


def row(table: str, start: str, end: str, freq: str = "M", more=()) -> dict:
    return {"table_id": table, "frequency": freq, "period": [{"start": start, "end": end}, *more]}


def final(outcome="selected", tables=(), asks=(), quotes=()) -> dict:
    return {"outcome": outcome, "tables": list(tables), "asks": list(asks), "representative": None,
            "frequency_quotes": list(quotes)}


def record(model: dict, finals: dict, question: str = QUESTION, error=None, route="handoff") -> dict:
    card = {"card_id": "g1", "name": "가상 지표",
            "tables": [{"table_id": t, "title": ti, "brackets": br, "bracket_words": []} for t, ti, br in TABLES],
            "matched": []}
    return {"question": question, "as_of": AS_OF, "run_info": {"versions": {"graph": "x"}, "as_of": AS_OF},
            "error": error, "pool": list(TITLES), "cards": [card], "model": model,
            "checks": {sid: {"reasons": [], "warnings": [], "citation": []} for sid in finals},
            "corrections": {sid: [] for sid in finals}, "final": finals, "diagnostics": {}, "route": route,
            "answer_contract": "tl-contract-2026-10-28.3"}


def scored(case: dict, rec: dict) -> dict:
    return I.score_case(rec, case, TITLES, COVERAGE)


def one_stat(rows, choice="DT_X01"):
    return record(output([("s1", "가상지표")], [("s1", choice)]), {"s1": final(tables=rows)})


# ---------------------------------------------------------------- build: lossless copy, refusals


def test_build_copies_every_field_as_confirmed():
    ask = {"axes": [{"axis": "seasonal", "values": [{"value": "원계열", "tables": ["DT_X01", "DT_X03"]},
                                                    {"value": "계절조정", "tables": ["DT_X02"]}]},
                    {"axis": "price", "values": [{"value": "명목", "tables": ["DT_X01"]},
                                                 {"value": "실질", "tables": ["DT_Y01"]}]}]}
    t = target(representative=["DT_X01", "DT_X02"], equivalents={"DT_X01": ["DT_X03"]}, ask=ask,
               representative_held=["DT_Y01"], holds={"ask": True},
               out_of_scope={"series": ["총지수"], "calculation": [], "display": []},
               final=final_of(combo("c1", part("DT_X01", "2015-01", "2024-12")),
                              combo("c2", part("DT_X03", "2015-01", "2019-12"), part("DT_X01", "2020-01", "2024-12")),
                              combo("c3", part("DT_X02", "2015-01", "2024-12"), status="held")))
    src = answer([t], expected="ask", type_="T3")
    src["holds_why"] = {"ask": "policy"}
    std = I.build([copy.deepcopy(src)], AS_OF, "indep60", tables=set(TITLES), coverage=COVERAGE)
    case = std["cases"][0]
    assert {k: v for k, v in case.items() if k not in ("case_id", "part", "as_of")} == src
    assert (case["case_id"], case["part"], case["as_of"], std["as_of"]) == ("IND-Q001", "indep60", AS_OF, AS_OF)
    assert I.is_indep(std) and std["version"] == I.INDEP_STANDARD_VERSION


def test_build_never_turns_final_candidates_into_equivalents():
    src = answer()
    src["targets"][0]["final_candidates"] = ["DT_X03"]  # a field the confirmed structure does not have
    with pytest.raises(I.StandardError, match="unknown field final_candidates"):
        I.build([src], AS_OF, "indep60", tables=set(TITLES))
    assert case_of(answer())["targets"][0]["equivalents"] == {}


@pytest.mark.parametrize("change, message", [
    (lambda r: r["targets"][0].update(text="없는 말"), "not found at occurrence"),
    (lambda r: r["targets"][0].update(occurrence=2), "not found at occurrence 2"),
    (lambda r: r["targets"][0]["final"]["combinations"][0]["parts"].append(part("DT_X03", "2024-06", "2025-01")),
     "months overlap inside the combination"),
    (lambda r: r["targets"][0]["final"]["combinations"].append(combo("c2", part("DT_X01", "2024-01", "2024-12"))),
     "the same as another combination"),
    (lambda r: r["targets"][0]["final"]["combinations"].append(combo("c1", part("DT_X03", "2024-01", "2024-12"))),
     "ids missing or repeated"),
    (lambda r: r["targets"][0]["final"].update(combinations=[]), "needs representative tables and combinations"),
    (lambda r: r["targets"][0]["final"]["combinations"][0].update(status="held"), "no confirmed combination"),
    (lambda r: r["targets"][0].pop("holds"), "missing field holds"),
    (lambda r: r["targets"][0]["final"]["combinations"][0]["parts"][0].update(**{"from": "2024.01"}), "part range"),
    (lambda r: r["targets"][0]["final"]["combinations"][0]["parts"][0].update(points=["2024-01"]), "either from/to"),
    (lambda r: r["targets"][0]["final"]["combinations"][0]["parts"][0].update(to="2027-01"), "catalog coverage"),
    (lambda r: r["targets"][0].update(representative=["DT_ZZZ"]), "not in the catalog"),
    (lambda r: r["targets"][0].update(equivalents={"DT_X02": ["DT_X03"]}), "not a representative table"),
    (lambda r: r["targets"][0].update(representative_held=["DT_X01"]), "both a confirmed and a held"),
    (lambda r: r.update(joint_combinations=[{"r1": "c9"}]), "joint combination"),
    (lambda r: r.update(expected="ask"), "no target has an ask"),
])
def test_build_refuses_what_it_cannot_copy_as_is(change, message):
    src = answer()
    change(src)
    with pytest.raises(I.StandardError, match=message):
        I.build([src], AS_OF, "indep60", tables=set(TITLES), coverage=COVERAGE)


def test_build_keeps_boundary_apart():
    b0 = answer([target(representative=[], final=final_of())], expected="missing", type_="B0")
    assert case_of(b0, "boundary")["expected"] == "missing"
    with pytest.raises(I.StandardError, match="does not belong to part indep60"):
        I.build([b0], AS_OF, "indep60", tables=set(TITLES))
    with_table = answer([target()], expected="missing", type_="B0")
    with pytest.raises(I.StandardError, match="holds no table"):
        I.build([with_table], AS_OF, "boundary", tables=set(TITLES))


# ---------------------------------------------------------------- combinations: "or" between, "all" inside


def two_ways(held_third=False):
    """Combination A: the new table alone; combination B: the old table, then the new one."""
    combos = [combo("A", part("DT_X01", "2024-01", "2024-12")),
              combo("B", part("DT_X03", "2024-01", "2024-06"), part("DT_X01", "2024-07", "2024-12"))]
    if held_third:
        combos.append(combo("H", part("DT_X02", "2024-01", "2024-12"), status="held"))
    t = target(equivalents={"DT_X01": ["DT_X03"]}, final=final_of(*combos))
    return case_of(answer([t]))


def test_the_new_table_alone_matches_combination_a():
    res = scored(two_ways(), one_stat([row("DT_X01", "2024-01", "2024-12")]))
    assert res["final_full"]["right"] is True and res["completed"]["right"] is True
    assert res["targets"][0]["final_detail"]["s1"]["combination"] == "A"


def test_old_then_new_matches_combination_b():
    res = scored(two_ways(), one_stat([row("DT_X03", "2024-01", "2024-06"), row("DT_X01", "2024-07", "2024-12")]))
    assert res["final_full"]["right"] is True and res["targets"][0]["final_detail"]["s1"]["combination"] == "B"


@pytest.mark.parametrize("rows, why", [
    ([row("DT_X01", "2024-01", "2024-12"), row("DT_X03", "2024-01", "2024-06")], "A plus an old part: repeated"),
    ([row("DT_X03", "2024-01", "2024-06")], "the old part only: missing"),
    ([row("DT_X03", "2024-01", "2024-05"), row("DT_X01", "2024-07", "2024-12")], "a gap"),
    ([row("DT_X03", "2024-01", "2024-07"), row("DT_X01", "2024-07", "2024-12")], "an overlap"),
    ([row("DT_X03", "2024-01", "2024-06"), row("DT_X01", "2024-07", "2025-01")], "a month outside the request"),
    ([row("DT_X03", "2024-01", "2024-09"), row("DT_X01", "2024-10", "2024-12")], "parts of A and B mixed"),
    ([row("DT_X01", "2024-01", "2024-12", "Q")], "another frequency"),
])
def test_combination_errors_are_wrong(rows, why):
    res = scored(two_ways(), one_stat(rows))
    assert res["final_full"]["right"] is False, why


def test_wrong_rows_report_what_is_missing_and_extra():
    res = scored(two_ways(), one_stat([row("DT_X03", "2024-01", "2024-05"), row("DT_X01", "2024-07", "2024-12")]))
    near = res["targets"][0]["final_detail"]["s1"]["problems"][0]
    assert near["nearest"] == "B" and near["missing_count"] == 1 and near["extra_count"] == 0


def test_another_statistic_never_fills_a_gap():
    rec = record(output([("s1", "가상지표"), ("s2", "가상지표")], [("s1", "DT_X01"), ("s2", "DT_X01")]),
                 {"s1": final(tables=[row("DT_X03", "2024-01", "2024-06")]),
                  "s2": final(tables=[row("DT_X01", "2024-07", "2024-12")])})
    assert scored(two_ways(), rec)["final_full"]["right"] is False


def test_one_table_used_by_two_statistics_is_not_a_repeat():
    t1 = target(id="r1", text="가상지표")
    t2 = target(id="r2", text="다른지표", representative=["DT_X01"],
                final=final_of(combo("c1", part("DT_X01", "2024-01", "2024-12"))))
    c = case_of(answer([t1, t2], question="2024년 가상지표와 다른지표 월별 추이"))
    rec = record(output([("s1", "가상지표"), ("s2", "다른지표")], [("s1", "DT_X01"), ("s2", "DT_X01")]),
                 {"s1": final(tables=[row("DT_X01", "2024-01", "2024-12")]),
                  "s2": final(tables=[row("DT_X01", "2024-01", "2024-12")])},
                 question="2024년 가상지표와 다른지표 월별 추이")
    assert scored(c, rec)["final_full"]["right"] is True


def points_case():
    t = target(final=final_of(combo("c1", {"table": "DT_X01", "frequency": "M", "points": ["2024-03", "2024-12"]})))
    return case_of(answer([t], question="2024년 3월과 12월 가상지표를 보여 줘"))


def test_two_points_given_exactly_are_right_and_the_span_between_is_wrong():
    q = "2024년 3월과 12월 가상지표를 보여 줘"
    exact = record(output([("s1", "가상지표")], [("s1", "DT_X01")]),
                   {"s1": final(tables=[row("DT_X01", "2024-03", "2024-03", more=[{"start": "2024-12",
                                                                                    "end": "2024-12"}])])},
                   question=q)
    span = record(output([("s1", "가상지표")], [("s1", "DT_X01")]),
                  {"s1": final(tables=[row("DT_X01", "2024-03", "2024-12")])}, question=q)
    assert scored(points_case(), exact)["final_full"]["right"] is True
    assert scored(points_case(), span)["final_full"]["right"] is False


def test_an_extra_statistic_with_an_unrelated_table_is_wrong():
    rec = record(output([("s1", "가상지표"), ("s2", "월별 추이")], [("s1", "DT_X01"), ("s2", "DT_Z01")]),
                 {"s1": final(tables=[row("DT_X01", "2024-01", "2024-12")]),
                  "s2": final(tables=[row("DT_Z01", "2024-01", "2024-12")])})
    res = scored(two_ways(), rec)
    assert res["final_full"]["right"] is False and "extra_statistic_table" in res["labels"]
    assert res["placement"]["extra_wrong"] == ["s2"]


def test_an_extra_table_inside_the_statistic_is_wrong():
    rows = [row("DT_X01", "2024-01", "2024-12"), row("DT_Y01", "2024-01", "2024-12")]
    assert scored(two_ways(), one_stat(rows))["final_full"]["right"] is False


def test_a_held_combination_is_held_never_accepted():
    res = scored(two_ways(held_third=True), one_stat([row("DT_X02", "2024-01", "2024-12")]))
    assert res["final_full"]["right"] == "na" and res["held"]["full"] == ["r1"]
    other = scored(two_ways(held_third=True), one_stat([row("DT_Y01", "2024-01", "2024-12")]))
    assert other["final_full"]["right"] is False  # a held alternative does not hold every mismatch


def test_a_held_representative_holds_only_the_meaning():
    t = target(representative_held=["DT_Y01"],
               final=final_of(combo("c1", part("DT_X01", "2024-01", "2024-12")),
                              combo("h1", part("DT_Y01", "2024-01", "2024-12"), status="held")))
    c = case_of(answer([t]))
    res = scored(c, one_stat([row("DT_Y01", "2024-01", "2024-12")], choice="DT_Y01"))
    assert res["model_meaning"]["right"] == "na" and res["held"]["model"] == ["r1"]
    assert res["final_full"]["right"] == "na"
    bad = scored(c, one_stat([row("DT_Z01", "2024-01", "2024-12")], choice="DT_Z01"))
    assert bad["model_meaning"]["right"] is False and bad["final_full"]["right"] is False


def test_joint_combinations_refuse_an_unlisted_pair():
    q = "2024년 가상지표와 다른지표 월별 추이"
    ways = final_of(combo("A", part("DT_X01", "2024-01", "2024-12")),
                    combo("B", part("DT_X03", "2024-01", "2024-06"), part("DT_X01", "2024-07", "2024-12")))
    t1 = target(id="r1", text="가상지표", equivalents={"DT_X01": ["DT_X03"]}, final=copy.deepcopy(ways))
    t2 = target(id="r2", text="다른지표", equivalents={"DT_X01": ["DT_X03"]}, final=copy.deepcopy(ways))
    src = answer([t1, t2], question=q)
    src["joint_combinations"] = [{"r1": "A", "r2": "A"}, {"r1": "B", "r2": "B"}]
    c = case_of(src)

    def rec(r1, r2):
        return record(output([("s1", "가상지표"), ("s2", "다른지표")], [("s1", "DT_X01"), ("s2", "DT_X01")]),
                      {"s1": final(tables=r1), "s2": final(tables=r2)}, question=q)
    a = [row("DT_X01", "2024-01", "2024-12")]
    b = [row("DT_X03", "2024-01", "2024-06"), row("DT_X01", "2024-07", "2024-12")]
    assert scored(c, rec(a, a))["final_full"]["right"] is True
    mixed = scored(c, rec(a, b))
    assert mixed["final_full"]["right"] is False and "combination_not_jointly_allowed" in mixed["labels"]


def test_months_outside_the_catalog_coverage_are_wrong():
    c = case_of(answer([target(final=final_of(combo("c1", part("DT_X01", "2026-01", "2026-07"))))]))
    assert scored(c, one_stat([row("DT_X01", "2026-01", "2026-07")]))["final_full"]["right"] is True
    assert scored(c, one_stat([row("DT_X01", "2026-01", "2026-08")]))["final_full"]["right"] is False


def test_right_tables_and_months_with_an_evidence_error_are_not_complete():
    rec = one_stat([row("DT_X01", "2024-01", "2024-12")])
    rec["model"]["choices"][0]["evidence"] = [{"kind": "title", "text": "카드에 없는 제목"}]
    res = scored(two_ways(), rec)
    assert res["final_full"]["right"] is True and res["completed"]["right"] is False
    assert "scorer_evidence_errors" in res["completed"]["blocked_by"]


# ---------------------------------------------------------------- asks: per axis, per value, several tables


def ask_case(hold=False):
    ask = {"axes": [{"axis": "seasonal", "values": [{"value": "원계열", "tables": ["DT_X01", "DT_X03"]},
                                                    {"value": "계절조정", "tables": ["DT_X02"]}]}]}
    t = target(representative=["DT_X01", "DT_X02"], ask=ask, holds={"ask": hold}, final=final_of())
    return case_of(answer([t], expected="ask", type_="T3"))


def asked(options, axis="seasonal"):
    a = {"for": "s1", "axis": axis, "options": options}
    fa = {"for": "s1", "axis": axis, "options": [{"value": o["value"], "table_id": o["table"]} for o in options]}
    return record(output([("s1", "가상지표")], asks=[a]), {"s1": final("ask", asks=[fa])}, route="ask")


def test_ask_on_the_axis_with_every_value_once_is_right():
    res = scored(ask_case(), asked([option("원계열", "DT_X01"), option("계절조정", "DT_X02")]))
    assert res["ask"]["right"] is True and res["model_meaning"]["right"] is True and res["final_full"]["right"] is True


def test_another_allowed_table_for_a_value_is_right():
    assert scored(ask_case(), asked([option("원계열", "DT_X03"), option("계절조정", "DT_X02")]))["ask"]["right"] is True


@pytest.mark.parametrize("options", [
    [option("원계열", "DT_X01")],
    [option("원계열", "DT_X01"), option("원계열", "DT_X03"), option("계절조정", "DT_X02")],
    [option("원계열", "DT_X01"), option("계절조정", "DT_Y01")],
])
def test_ask_value_errors_are_wrong(options):
    assert scored(ask_case(), asked(options))["ask"]["right"] is False


def test_ask_where_none_is_confirmed_is_wrong_and_a_selection_where_an_ask_is_due_is_wrong():
    rec = asked([option("원계열", "DT_X01"), option("계절조정", "DT_X02")])
    assert scored(case_of(answer()), rec)["ask"]["right"] is False
    assert scored(ask_case(), one_stat([row("DT_X01", "2024-01", "2024-12")]))["ask"]["right"] is False


def test_two_axes_each_scored():
    ask = {"axes": [{"axis": "seasonal", "values": [{"value": "원계열", "tables": ["DT_X01"]},
                                                    {"value": "계절조정", "tables": ["DT_X02"]}]},
                    {"axis": "price", "values": [{"value": "명목", "tables": ["DT_X03"]},
                                                 {"value": "실질", "tables": ["DT_Y01"]}]}]}
    c = case_of(answer([target(representative=["DT_X01"], ask=ask, final=final_of())], expected="ask", type_="T3"))
    one = {"for": "s1", "axis": "seasonal", "options": [option("원계열", "DT_X01"), option("계절조정", "DT_X02")]}
    two = {"for": "s1", "axis": "price", "options": [option("명목", "DT_X03"), option("실질", "DT_Y01")]}
    both = record(output([("s1", "가상지표")], asks=[one, two]), {"s1": final("ask")}, route="ask")
    only = record(output([("s1", "가상지표")], asks=[one]), {"s1": final("ask")}, route="ask")
    assert scored(c, both)["ask"]["right"] is True and scored(c, only)["ask"]["right"] is False


# ---------------------------------------------------------------- holds: only the held item leaves the denominator


def test_ask_hold_keeps_tables_scored():
    c = case_of(answer([target(holds={"ask": True})]))
    selected = scored(c, one_stat([row("DT_X01", "2024-01", "2024-12")]))
    assert selected["ask"]["right"] == "na" and selected["held"]["ask"] == ["r1"]
    assert selected["model_meaning"]["right"] is True and selected["final_full"]["right"] is True
    wrong_period = scored(c, one_stat([row("DT_X01", "2024-01", "2024-11")]))
    assert wrong_period["final_full"]["right"] is False  # the tables and months are still scored
    a = {"for": "s1", "axis": "seasonal", "options": [option("원계열", "DT_X01"), option("계절조정", "DT_X02")]}
    asking = scored(c, record(output([("s1", "가상지표")], asks=[a]), {"s1": final("ask")}, route="ask"))
    assert asking["held"]["full"] == ["r1"] and asking["final_full"]["right"] == "na"
    agg = I.aggregate([selected, wrong_period, asking])["indep60"]
    assert agg["ask_right"]["denominator"] == 0 and agg["final_full"]["denominator"] == 2
    assert agg["held_targets"]["ask"] == 3 and agg["held_targets"]["full"] == 1


def test_errors_after_a_run_are_never_held():
    res = scored(case_of(answer()), one_stat([row("DT_Y01", "2024-01", "2024-12")]))
    assert res["final_full"]["right"] is False and res["held"]["full"] == []


# ---------------------------------------------------------------- boundary questions


def b0_case():
    return case_of(answer([target(text="가상지표", representative=[], final=final_of())],
                          expected="missing", type_="B0"), "boundary")


def test_boundary_verdicts():
    right = record(output([("s1", "가상지표")], missing=[{"for": "s1", "why": "없음"}]), {"s1": final("not_in_pool")},
                   route="fail")
    chose = one_stat([row("DT_X01", "2024-01", "2024-12")])
    a = {"for": "s1", "axis": "seasonal", "options": [option("원계열", "DT_X01"), option("계절조정", "DT_X02")]}
    asking = record(output([("s1", "가상지표")], asks=[a]), {"s1": final("ask")}, route="ask")
    broken = record(None, {}, error={"stage": "parse", "code": "json_invalid"}, route="fail")
    bad_quote = record(output([("s1", "없는 인용")], missing=[{"for": "s1", "why": "없음"}]),
                       {"s1": final("not_in_pool")}, route="fail")
    silent = record(output([("s1", "가상지표")]), {"s1": final("failed")}, route="fail")
    got = {k: scored(b0_case(), r)["boundary"]["verdict"] for k, r in
           (("right", right), ("chose", chose), ("asking", asking), ("broken", broken), ("bad_quote", bad_quote),
            ("silent", silent))}
    assert got == {"right": "right", "chose": "wrong", "asking": "wrong", "broken": "run_or_contract_error",
                   "bad_quote": "run_or_contract_error", "silent": "review"}
    agg = I.aggregate([scored(b0_case(), r) for r in (right, chose, broken)])
    assert agg["boundary"]["boundary"] == {"right": 1, "wrong": 1, "run_or_contract_error": 1, "review": 0}
    assert "indep60" not in agg


# ---------------------------------------------------------------- end point, report, aggregate


def test_end_point_is_compared_but_kept_out_of_completion():
    t = target()
    t["final"]["point_in_period"] = "end"
    c = case_of(answer([t]))
    end = {"quote": "월말", "for": "s1", "frequency": "M", "point_in_period": "end"}
    with_end = record(output([("s1", "가상지표")], [("s1", "DT_X01")]),
                      {"s1": final(tables=[row("DT_X01", "2024-01", "2024-12")], quotes=[end])})
    assert scored(c, with_end)["point_in_period"]["right"] is True
    res = scored(c, one_stat([row("DT_X01", "2024-01", "2024-12")]))
    assert res["point_in_period"]["right"] is False and res["completed"]["right"] is True


def test_report_scores_an_indep_standard_and_refuses_another_as_of():
    other_q = "2024년 다른지표 월별 추이"
    std = I.build([answer(), answer(qid="Q002", question=other_q,
                                    targets=[target(text="다른지표", representative=["DT_Y01"],
                                                    final=final_of(combo("c1", part("DT_Y01", "2024-01",
                                                                                    "2024-12"))))])],
                  AS_OF, "indep60", tables=set(TITLES), coverage=COVERAGE)
    ok = one_stat([row("DT_X01", "2024-01", "2024-12")])
    other = copy.deepcopy(ok)
    other["as_of"] = other["run_info"]["as_of"] = "2026-10-22"
    got = R.evaluate({"IND-Q001": ok, "IND-Q002": other}, std, TITLES, COVERAGE, "sha")
    assert [r["case_id"] for r in got["results"]] == ["IND-Q001"]
    assert got["unverified"][0]["case_id"] == "IND-Q002" and got["evaluator"]["indep"] == I.INDEP_SCORE_VERSION
    assert got["aggregate"]["indep60"]["final_full"] == {"right": 1, "wrong": 0, "review": 0, "na": 0,
                                                         "denominator": 1}
    assert set(got["aggregate"]["by_type"]) == {"T1"}
    json.dumps(got, ensure_ascii=False)


# ---------------------------------------------------------------- table_sets on the other standards (.14)


def old_case(final_):
    return {"representative": {"confirmed_groups": [["DT_X01", "DT_X03"]]}, "ask": {"expected": "not_expected"},
            "new_policy": {"status": "same_as_source"},
            "final": {"status": "confirmed", "frequency": "M", **final_}}


def old_rec(rows):
    return {"model": {"choices": [{"table": "DT_X01", "for": ["s1"]}]}, "pool": list(TITLES), "cards": [],
            "final": {"s1": {"tables": rows, "asks": []}}}


def test_table_sets_checks_periods_instead_of_skipping_them():
    want = {"periods": [{"from": "2024-01", "to": "2024-01"}, {"from": "2024-03", "to": "2024-03"}]}
    assert S.table_sets(old_rec([row("DT_X01", "2024-01", "2024-01")]), old_case(want))["final"]["right"] is False
    both = [row("DT_X01", "2024-01", "2024-01"), row("DT_X03", "2024-03", "2024-03")]
    assert S.table_sets(old_rec(both), old_case(want))["final"]["right"] is True


def test_table_sets_joins_an_edition_split_inside_one_statistic():
    want = {"period": {"from": "2024-01", "to": "2024-12"}}
    split = [row("DT_X03", "2024-01", "2024-06"), row("DT_X01", "2024-07", "2024-12")]
    assert S.table_sets(old_rec(split), old_case(want))["final"]["right"] is True
    overlap = [row("DT_X03", "2024-01", "2024-07"), row("DT_X01", "2024-07", "2024-12")]
    assert S.table_sets(old_rec(overlap), old_case(want))["final"]["right"] is False
