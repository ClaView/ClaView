"""Requirement targets scored by what each target gets, not by the number of statistics (user 2026-10-29 05:05 UTC),
in normal/error pairs on made-up tables and run records only: one statistic for several targets and several
statistics on one table are both fine; a missing target, a swapped table per target and a statistic placed on no
target are told apart; a word shared by two named tables is placed only by the model's own quotes (qualifiers),
never by the table chosen; a held policy never hides a sure error."""

from __future__ import annotations

from test_indep_38 import final, output, record, row  # noqa: F401 (shared made-up fixtures)
from test_score_split_16 import old_case, score

YEAR = "2024-01", "2024-12"
TWO = "2024년 가상지표와 다른지표 월별 추이"


def t(tid, text, tables, occurrence=1, qualifiers=()):
    return {"id": tid, "text": text, "occurrence": occurrence, "tables": list(tables),
            "qualifiers": [{"text": x, "occurrence": 1} for x in qualifiers]}


def run(question, stats, finals, splitters=(), groups=None, targets=(), ask=None, evidence=None):
    """stats: (sid, quote, chosen table); finals: sid -> final table."""
    m = output([(sid, quote) for sid, quote, _ in stats], [(sid, tab) for sid, _, tab in stats])
    m["periods"] = [{"quote": "2024년", "for": [sid for sid, _, _ in stats]}]
    m["frequencies"] = [{"quote": "월별", "for": [sid for sid, _, _ in stats]}] if "월별" in question else []
    m["splitters"] = [{"quote": qt, "axis": "other", "for": [sid]} for sid, qt in splitters]
    if evidence:
        m["choices"][0]["evidence"] = evidence
    rec = record(m, {sid: final(tables=[row(tab, *YEAR)]) for sid, tab in finals.items()}, question=question)
    case = old_case(groups or [["DT_X01"], ["DT_Y01"]], question=question, ask=ask, requirements=list(targets))
    return score(case, rec)


def marks(res):
    return {x["target"]: (x["model"], x["full"]) for x in res["requirements"]["targets"]}


TWO_TARGETS = [t("r1", "가상지표", ["DT_X01"]), t("r2", "다른지표", ["DT_Y01"])]


# ---------------------------------------------------------------- merged or split: neither is wrong by itself


def test_one_statistic_for_two_targets_on_one_table_is_right():
    targets = [t("r1", "가상지표", ["DT_X01"]), t("r2", "다른지표", ["DT_X01"])]
    res = run(TWO, [("s1", "가상지표와 다른지표", "DT_X01")], {"s1": "DT_X01"}, groups=[["DT_X01"]], targets=targets)
    assert marks(res) == {"r1": ("right", "right"), "r2": ("right", "right")}
    assert res["final_full"]["right"] is True and res["completed"]["right"] is True


def test_two_statistics_on_one_table_are_right():
    targets = [t("r1", "가상지표", ["DT_X01"]), t("r2", "다른지표", ["DT_X01"])]
    res = run(TWO, [("s1", "가상지표", "DT_X01"), ("s2", "다른지표", "DT_X01")], {"s1": "DT_X01", "s2": "DT_X01"},
              groups=[["DT_X01"]], targets=targets)
    assert res["final_full"]["right"] is True and res["completed"]["right"] is True


# ---------------------------------------------------------------- missing, swapped, placed on no target


def test_a_target_no_statistic_covers_is_missing():
    res = run(TWO, [("s1", "가상지표", "DT_X01")], {"s1": "DT_X01"}, targets=TWO_TARGETS)
    assert marks(res)["r2"] == ("missing", "missing") and res["completed"]["right"] is False


def test_tables_swapped_between_targets_are_wrong_although_the_set_is_the_same():
    res = run(TWO, [("s1", "가상지표", "DT_Y01"), ("s2", "다른지표", "DT_X01")], {"s1": "DT_Y01", "s2": "DT_X01"},
              targets=TWO_TARGETS)
    assert res["final_table_set"]["right"] is True
    assert marks(res) == {"r1": ("wrong", "wrong"), "r2": ("wrong", "wrong")} and res["completed"]["right"] is False


def test_a_statistic_placed_on_no_target_is_kept_apart_from_a_missing_target():
    res = run(TWO, [("s1", "가상지표", "DT_X01"), ("s2", "다른지표", "DT_Y01"), ("s3", "추이", "DT_Y01")],
              {"s1": "DT_X01", "s2": "DT_Y01", "s3": "DT_Y01"}, targets=TWO_TARGETS)
    assert marks(res) == {"r1": ("right", "right"), "r2": ("right", "right")}
    assert res["requirements"]["uncovered_statistics"] == ["s3"] and res["model_meaning"]["right"] is None


def test_two_statistics_on_one_target_are_recorded_not_failed():
    stats = [("s1", "가상지표와 다른지표", "DT_X01"), ("s2", "가상지표", "DT_X01"), ("s3", "다른지표", "DT_Y01")]
    res = run(TWO, stats, {"s1": "DT_X01", "s2": "DT_X01", "s3": "DT_Y01"}, targets=TWO_TARGETS)
    r1 = next(x for x in res["requirements"]["targets"] if x["target"] == "r1")
    assert r1["covered_more_than_once"] and r1["model"] == "right"
    assert marks(res)["r2"] == ("wrong", "wrong")  # s1's table is not allowed for r2: never filled by s3


# ---------------------------------------------------------------- one word under two named tables

BASES = "2024년 신규 표와 잔액 표를 각각 사용해 가상지표 아래 고정형을 월별 비교"
QUAL = [t("r1", "고정형", ["DT_X01"], qualifiers=["신규"]), t("r2", "고정형", ["DT_X02"], qualifiers=["잔액"])]


def bases(splitters, chosen):
    stats = [(sid, "고정형", tab) for sid, tab in chosen]
    return run(BASES, stats, dict(chosen), splitters=splitters, groups=[["DT_X01"], ["DT_X02"]], targets=QUAL)


def test_a_shared_word_placed_by_the_model_s_own_quotes_is_right():
    res = bases([("s1", "신규"), ("s2", "잔액")], [("s1", "DT_X01"), ("s2", "DT_X02")])
    assert marks(res) == {"r1": ("right", "right"), "r2": ("right", "right")} and res["completed"]["right"] is True


def test_a_shared_word_with_tables_swapped_by_basis_is_wrong():
    res = bases([("s1", "신규"), ("s2", "잔액")], [("s1", "DT_X02"), ("s2", "DT_X01")])
    assert res["final_table_set"]["right"] is True
    assert marks(res) == {"r1": ("wrong", "wrong"), "r2": ("wrong", "wrong")}


def test_a_shared_word_without_any_qualifier_is_held_not_placed_by_its_table():
    res = bases([], [("s1", "DT_X01"), ("s2", "DT_X02")])
    assert marks(res) == {"r1": ("review", "review"), "r2": ("review", "review")}
    assert res["requirements"]["qualifier_review"] == ["s1", "s2"] and res["final_full"]["right"] is None


def test_a_word_repeated_at_two_places_quoted_once_is_held():
    q = "2024년 가상지표 아래 고정형과 다른지표 아래 고정형을 월별 비교"
    targets = [t("r1", "고정형", ["DT_X01"]), t("r2", "고정형", ["DT_Y01"], occurrence=2)]
    res = run(q, [("s1", "고정형", "DT_X01")], {"s1": "DT_X01"}, targets=targets)
    assert res["requirements"]["split_review"] == ["s1"] and res["final_full"]["right"] is None


# ---------------------------------------------------------------- a held policy never hides a sure error


def test_a_held_ask_policy_with_a_sure_evidence_error_is_not_complete():
    ask = {"expected": "policy_undecided"}
    res = run(TWO, [("s1", "가상지표", "DT_X01"), ("s2", "다른지표", "DT_Y01")], {"s1": "DT_X01", "s2": "DT_Y01"},
              targets=TWO_TARGETS, ask=ask, evidence=[{"kind": "title", "text": "카드에 없는 제목"}])
    assert res["model_meaning"]["ask"] == "review"
    assert res["evidence_contract"]["ok"] is False and res["completed"]["right"] is False


def test_a_held_ask_policy_alone_leaves_completion_held():
    ask = {"expected": "policy_undecided"}
    res = run(TWO, [("s1", "가상지표", "DT_X01"), ("s2", "다른지표", "DT_Y01")], {"s1": "DT_X01", "s2": "DT_Y01"},
              targets=TWO_TARGETS, ask=ask)
    assert marks(res) == {"r1": ("right", "right"), "r2": ("right", "right")}
    assert res["model_meaning"]["right"] is None


def test_a_target_left_uncovered_next_to_a_statistic_placed_on_no_target_is_held():
    """Statistics quoting only the tables' names (the targets named but not quoted): never placed by the table."""
    targets = [t("r1", "가상지표", ["DT_X01"]), t("r2", "다른지표", ["DT_X01"])]
    res = run(TWO, [("s1", "추이", "DT_X01")], {"s1": "DT_X01"}, groups=[["DT_X01"]], targets=targets)
    assert marks(res) == {"r1": ("review", "review"), "r2": ("review", "review")}
    assert res["final_full"]["right"] is None and res["completed"]["right"] is None


def test_a_held_target_does_not_hide_a_table_set_that_misses_a_group():
    res = run(TWO, [("s1", "추이", "DT_X01")], {"s1": "DT_X01"}, targets=TWO_TARGETS)
    assert marks(res)["r2"] == ("review", "review")
    assert res["final_table_set"]["right"] is False and res["completed"]["right"] is False
