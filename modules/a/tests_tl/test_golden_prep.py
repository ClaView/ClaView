"""Scoring links for the golden preparation (user 2026-10-28 07:56 UTC): made-up outputs scored against the draft
standard of the 26 golden questions new to the path (standard_golden26, user 2026-10-28 08:13 UTC). No model, no network; expectations by hand."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from conftest import fill_option_evidence  # noqa: E402
from claview_a.cards import build_cards, table_numbers  # noqa: E402
from claview_a.contract import Settings  # noqa: E402
from claview_a.graph import run  # noqa: E402
from claview_a.pool import build_pool  # noqa: E402
from claview_a.search.tables import selection_catalog  # noqa: E402
from tl_eval import files as F  # noqa: E402
from tl_eval import score as S  # noqa: E402

AS_OF = "2026-10-22"
DRAFT = F.external("standard_golden26.json")  # fixed 08:13 UTC (kept outside the repository)
NEW, BAL = "DT_121Y006", "DT_121Y015"


@pytest.fixture(scope="module")
def std():
    return {c["case_id"]: c for c in json.loads(DRAFT.read_text(encoding="utf-8"))["cases"]}


@pytest.fixture(scope="module")
def titles(layer):
    return {t: v.title for t, v in selection_catalog(layer).views.items()}


def ref_of(cards, table):
    for c in cards:
        num = table_numbers(c)
        if table in num:
            return f"{c['card_id']}.{num[table]}"
    raise KeyError(table)


def scored(layer, case, titles, build):
    q = case["question"]
    cards = build_cards(q, build_pool(q, layer), layer)
    out = fill_option_evidence(build(cards), cards)
    state = run(q, layer, lambda p, r: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    return state, S.score_case(S.adapt(state), case, titles)


def loan_two_tables(stats, tables_of, splitters=()):
    """Statistics with their quotes, each placed on a table; 2024 monthly."""
    def build(cards):
        o = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                             "missing", "alternatives")}
        o["statistics"] = [{"id": f"s{i + 1}", "quote": q, "name": q} for i, q in enumerate(stats)]
        ids = [s["id"] for s in o["statistics"]]
        o["splitters"] = [{"quote": q, "axis": a, "for": f} for q, a, f in splitters]
        o["periods"] = [{"quote": "2024년", "for": ids}]
        o["frequencies"] = [{"quote": "월별", "for": ids}]
        o["choices"] = [{"for": [sid], "table": ref_of(cards, t), "evidence": [{"kind": "title", "text": "예금은행 대출금리"}]}
                        for sid, t in zip(ids, tables_of)]
        return o
    return build


SPLIT = [("신규취급액", "loan_basis", ["s1", "s2"]), ("잔액 기준", "loan_basis", ["s3", "s4"])]


def test_several_statistics_on_one_table_each_is_a_right_table_set(layer, std, titles):
    """GV4-13: 대기업·중소기업 on each of the two tables = 4 statistics, 2 per table."""
    _, res = scored(layer, std["GV4-13"], titles, loan_two_tables(
        ["대기업대출", "중소기업대출", "대기업대출", "중소기업대출"], [NEW, NEW, BAL, BAL], SPLIT))
    assert res["model_table_set"]["right"] is True and res["final_table_set"]["right"] is True
    assert res["model_meaning"]["right"] is None  # per-statistic mapping held: several statistics per group


def test_a_missing_table_and_an_extra_table_are_told_apart(layer, std, titles):
    _, missing = scored(layer, std["GV4-13"], titles, loan_two_tables(
        ["대기업대출", "중소기업대출"], [NEW, NEW], [("신규취급액", "loan_basis", ["s1", "s2"])]))
    assert missing["model_table_set"]["right"] is False and missing["model_table_set"]["missing_groups"] == [[BAL]]
    _, extra = scored(layer, std["GV4-13"], titles, loan_two_tables(
        ["대기업대출", "중소기업대출", "대기업대출", "중소기업대출"], [NEW, NEW, BAL, "DT_121Y002"], SPLIT))
    assert extra["model_table_set"]["right"] is False and "DT_121Y002" in extra["model_table_set"]["extra_tables"]


def test_an_unneeded_ask_on_a_multi_table_question_is_counted(layer, std, titles):
    def build(cards):
        o = loan_two_tables(["대기업대출"], [NEW])(cards)
        o["choices"] = []
        o["asks"] = [{"for": "s1", "axis": "loan_basis", "options": [
            {"value": "신규취급액", "table": ref_of(cards, NEW)}, {"value": "잔액기준", "table": ref_of(cards, BAL)}]}]
        return o
    _, res = scored(layer, std["GV4-13"], titles, build)
    assert res["ask_measures"]["ask_unneeded"] == [["s1", "loan_basis"]] and res["model_meaning"]["right"] is False


def test_the_needed_classification_ask_of_cv_ab_16(layer, std, titles):
    case = std["CV-AB-16"]
    def build(cards):
        o = loan_two_tables(["공산품과 서비스의 생산자물가"], ["DT_404Y014"])(cards)
        o["periods"] = [{"quote": "2024년", "for": ["s1"]}]
        o["choices"] = []
        o["asks"] = [{"for": "s1", "axis": "classification", "options": [
            {"value": "기본분류", "table": ref_of(cards, "DT_404Y014")},
            {"value": "품목별", "table": ref_of(cards, "DT_404Y016")}]}]
        return o
    _, res = scored(layer, case, titles, build)
    assert res["final_ask"] == "right" and res["ask_measures"]["ask_missing"] == []
    assert res["model_table_set"]["right"] == "na"  # the ask is scored by the ask contract


def test_meaning_and_evidence_are_kept_apart(layer, std, titles):
    def build(cards):
        o = loan_two_tables(["대기업대출", "대기업대출"], [NEW, BAL],
                            [("신규취급액", "loan_basis", ["s1"]), ("잔액 기준", "loan_basis", ["s2"])])(cards)
        o["choices"][1]["evidence"] = [{"kind": "item", "text": "없는 항목"}]
        return o
    _, res = scored(layer, std["GV4-01"], titles, build)
    assert res["model_table_set"]["right"] is True and res["evidence_contract"]["ok"] is False
    assert res["completed"]["right"] is not True


def test_an_old_edition_representative_and_the_final_edition_are_scored_apart(layer, std, titles):
    """GV4-07 asks 2024 M2 말잔·원계열: an old-edition representative is outside the answer group (model set wrong)
    while code's final edition by period is the answer table (final set right)."""
    case = std["GV4-07"]
    def build(cards):
        o = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                             "missing", "alternatives")}
        o["statistics"] = [{"id": "s1", "quote": "만기 2년 미만 정기예적금", "name": "a"},
                           {"id": "s2", "quote": "예금취급기관 발행 금", "name": "b"}]
        o["splitters"] = [{"quote": "원계열", "axis": "seasonal", "for": ["s1", "s2"]}]
        o["periods"] = [{"quote": "2024년", "for": ["s1", "s2"]}]
        o["frequencies"] = [{"quote": "월말", "for": ["s1", "s2"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, "DT_101Y002"),
                         "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"}]},
                        {"for": ["s2"], "table": ref_of(cards, "DT_171Y002"),
                         "evidence": [{"kind": "title", "text": "Lf 상품별 구성내역"}]}]
        return o
    state, res = scored(layer, case, titles, build)
    finals = {r["table_id"] for d in state["record"]["decisions"] for r in d["final_tables"]}
    if "DT_161Y008" in finals:  # code's period choice reached the current edition
        assert res["model_table_set"]["right"] is False and "DT_101Y002" in res["model_table_set"]["extra_tables"]
        assert res["final_table_set"]["missing_groups"] == []
    else:  # when code cannot finish the period choice, the record says why instead of guessing
        assert all(d["reasons"] for d in state["record"]["decisions"] if not d["final_tables"])


def test_a_held_policy_item_is_neither_right_nor_wrong(layer, std, titles):
    case = std["CV-AB-10"]
    def build(cards):
        o = loan_two_tables(["지역별 수출 규모"], ["DT_301Y015"])(cards)
        o["periods"] = [{"quote": "최근 10년간", "for": ["s1"]}]
        o["frequencies"] = []
        o["choices"][0]["evidence"] = [{"kind": "title", "text": "지역별 경상수지"}]
        return o
    _, res = scored(layer, case, titles, build)
    assert res["model_meaning"]["ask"] == "review"
    assert res["ask_measures"]["ask_missing"] == "review" and res["ask_measures"]["ask_unneeded"] == "review"


def test_cv_ab_10_says_its_ask_policy_is_undecided_and_an_ask_there_is_held(layer, std, titles):
    case = std["CV-AB-10"]
    assert case["ask"]["expected"] == "policy_undecided" and S.ask_policy_held(case)

    def build(cards):
        o = loan_two_tables(["지역별 수출 규모"], ["DT_301Y015"])(cards)
        o["periods"] = [{"quote": "최근 10년간", "for": ["s1"]}]
        o["frequencies"] = []
        o["choices"] = []
        tables = [t for c in cards for t in table_numbers(c)][:2]
        o["asks"] = [{"for": "s1", "axis": "classification", "options": [
            {"value": "기본분류", "table": ref_of(cards, tables[0])}, {"value": "품목별", "table": ref_of(cards, tables[1])}]}]
        return o
    _, res = scored(layer, case, titles, build)
    assert res["model_meaning"]["ask"] == "review" and res["ask_measures"]["ask_unneeded"] == "review"
    assert res["model_table_set"]["right"] is None and res["model_table_set"]["held"] == "ask_policy_undecided"
    assert res["final_full"]["right"] is not True and res["completed"]["right"] is not True
