"""Requirement targets (user 2026-10-28 08:13 UTC): CV-AB-17 and CV-AB-21 in standard_v3, scored per target with
made-up outputs. Merged or split statistics are allowed when every target is kept; the row count is never forced; a
quote that cannot be placed is review. No model, no network; expectations by hand."""

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
from tl_eval import score as S  # noqa: E402
from tl_eval import standard as ST  # noqa: E402

AS_OF = "2026-10-22"
FRESH = ("DT_404Y015", "생산자물가지수(특수분류)")
SUPPLY = ("DT_405Y006", "국내공급물가지수")


@pytest.fixture(scope="module")
def std():
    return {c["case_id"]: c for c in json.loads(ST.OUT_V3.read_text(encoding="utf-8"))["cases"]}


@pytest.fixture(scope="module")
def titles(layer):
    return {t: v.title for t, v in selection_catalog(layer).views.items()}


def ref_of(cards, table):
    for c in cards:
        num = table_numbers(c)
        if table in num:
            return f"{c['card_id']}.{num[table]}"
    raise KeyError(table)


def scored(layer, case, titles, quotes, table, title):
    q = case["question"]
    cards = build_cards(q, build_pool(q, layer), layer)
    o = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                         "missing", "alternatives")}
    o["statistics"] = [{"id": f"s{i + 1}", "quote": x, "name": x} for i, x in enumerate(quotes)]
    ids = [s["id"] for s in o["statistics"]]
    o["periods"] = [{"quote": "2024년", "for": ids}]
    o["frequencies"] = [{"quote": "월별", "for": ids}]
    o["choices"] = [{"for": [sid], "table": ref_of(cards, table), "evidence": [{"kind": "title", "text": title}]}
                    for sid in ids]
    out = fill_option_evidence(o, cards)
    state = run(q, layer, lambda p, r: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    return S.score_case(S.adapt(state), case, titles)


def marks(res):
    return {t["target"]: (t["model"], t["full"]) for t in res["requirements"]["targets"]}


def test_standard_v3_adds_targets_only_to_17_and_21():
    v2 = {c["case_id"]: c for c in json.loads(ST.OUT_V2.read_text(encoding="utf-8"))["cases"]}
    v3 = {c["case_id"]: c for c in json.loads(ST.OUT_V3.read_text(encoding="utf-8"))["cases"]}
    assert [k for k in v3 if v3[k] != v2[k]] == ["CV-AB-17", "CV-AB-21"]
    assert all({k: v for k, v in v3[k].items() if k != "requirements"} == v2[k] for k in v3)
    t = v3["CV-AB-21"]["requirements"]["targets"]
    assert [x["label"] for x in t] == ["원재료", "중간재", "최종재"] and all(x["answer_series"]["item_code"] for x in t)


def test_two_statistics_on_one_table_keep_both_targets(layer, std, titles):
    res = scored(layer, std["CV-AB-17"], titles, ["신선식품 생산자물가", "신선식품을 뺀 물가"], *FRESH)
    assert marks(res) == {"r1": ("right", "right"), "r2": ("right", "right")}
    assert res["model_meaning"]["right"] is True and res["final_full"]["right"] is True


def test_a_merged_statistic_keeps_every_target(layer, std, titles):
    res = scored(layer, std["CV-AB-21"], titles, ["원재료·중간재·최종재 물가"], *SUPPLY)
    assert marks(res) == {f"r{i}": ("right", "right") for i in (1, 2, 3)}
    assert res["model_meaning"]["right"] is True


def test_a_left_out_target_is_missing_whatever_the_table(layer, std, titles):
    res = scored(layer, std["CV-AB-21"], titles, ["원재료", "중간재"], *SUPPLY)
    assert marks(res)["r3"] == ("missing", "missing") and res["model_meaning"]["right"] is False
    assert res["model_table_set"]["right"] is True  # the set view cannot see it: sets never replace targets


def test_a_wrong_table_for_a_target_is_wrong(layer, std, titles):
    res = scored(layer, std["CV-AB-17"], titles, ["신선식품 생산자물가", "신선식품을 뺀 물가"],
                 "DT_404Y014", "생산자물가지수(기본분류)")
    assert {m for m, _ in marks(res).values()} == {"wrong"} and res["model_meaning"]["right"] is False


def test_a_quote_not_in_the_question_makes_an_uncovered_target_review(layer, std, titles):
    res = scored(layer, std["CV-AB-17"], titles, ["신선식품 생산자물가", "신선식품 제외 물가"], *FRESH)
    assert marks(res)["r2"] == ("review", "review") and res["model_meaning"]["right"] is not True


def test_a_statistic_covering_no_target_is_review_not_a_forced_row_count(layer, std, titles):
    res = scored(layer, std["CV-AB-21"], titles, ["원재료", "중간재", "최종재", "물가 흐름"], *SUPPLY)
    assert res["requirements"]["uncovered_statistics"] == ["s4"]
    assert res["model_meaning"]["right"] is None and res["final_full"]["right"] is None


def test_the_same_question_under_standard_v2_stays_held(layer, titles):
    v2 = {c["case_id"]: c for c in json.loads(ST.OUT_V2.read_text(encoding="utf-8"))["cases"]}
    res = scored(layer, v2["CV-AB-21"], titles, ["원재료", "중간재", "최종재"], *SUPPLY)
    assert res["requirements"] is None and res["model_meaning"]["right"] is None
