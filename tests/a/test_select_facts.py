"""Plan 11 R0 record mode: table facets (catalog/facets.py) and evidence facts (select/facts.py), each fact in both
directions, on made-up tables. Nothing here changes a selection: the records are only read. Table IDs, names and
words are made up."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "spec"))
from spec_support import build_layer, itm, table  # noqa: E402

from claview.a.catalog.facets import facet_of  # noqa: E402
from claview.a.select import facts as F  # noqa: E402
from claview.a.select.cover import all_covers  # noqa: E402
from claview.a.select.tables import selection_catalog  # noqa: E402

IND = "BSI코드별"  # an axis name the synthetic layer-1 sheet reads as an indicator axis
TGT = "지역코드별"  # ... as a target axis


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    tables = [
        table("T_SUBJ", "가상물가지수", [itm("A", "1", "가상물가지수", obj_name=IND)]),
        table("T_COMP", "가상종합표", [itm("A", "1", "가상물가지수", obj_name=IND),
                                   itm("A", "2", "가상임금", obj_name=IND)]),
        table("T_UND", "가상기타표", [itm("A", "1", "가상물가지수", obj_name=IND),
                                  itm("A", "2", "가상가", "1", obj_name=IND),
                                  itm("A", "3", "가상나", "1", obj_name=IND)]),
        table("T_MENT", "가상지역표", [itm("R", "1", "가상서울", obj_name=TGT), itm("R", "2", "가상부산", obj_name=TGT)]),
        table("T_Q1", "가상물가(가상갑기준)", [itm("A", "1", "가상전체", obj_name=IND)]),
        table("T_Q2", "가상물가", [itm("A", "1", "가상전체", obj_name=IND)]),
        table("T_TREE", "가상나무표", [itm("A", "1", "가상갑", obj_name=IND), itm("A", "2", "가상가", "1", obj_name=IND),
                                   itm("A", "3", "가상을", obj_name=IND), itm("A", "4", "가상나", "3", obj_name=IND),
                                   itm("A", "5", "가상고아", "zz", obj_name=IND),
                                   itm("R", "9", "가상서울", obj_name=TGT, order=2)]),
        table("T_X1", "가상갑별 가상을별 가상수치", [itm("A", "1", "가상수치", obj_name=IND)]),
        table("T_X2", "가상수치표", [itm("G", "1", "가상v", obj_name="가상갑구분"),
                                 itm("H", "1", "가상w", obj_name="가상을구분", order=2)]),
        table("T_N1", "가상현황", [itm("R", "1", "가상동", obj_name=TGT), itm("R", "2", "가상서", obj_name=TGT)]),
        table("T_N2", "가상현황표", [itm("R", "1", "가상서", obj_name=TGT)]),
    ]
    return build_layer(tmp_path_factory.mktemp("select_facts"), tables)


def _analysis(name: str | None, conditions=(), own=(), reqs=None) -> dict:
    req = {"id": "r1", "quote": name or "", "span": [0, 1], "targets": [], "condition_ids": list(own),
           "shared_condition_ids": [], "ambiguous_condition_ids": [], "status": "ok", "reasons": [],
           "indicator": {"quote": name, "span": [0, 1], "fragment_id": None, "name": name, "path": "exact"}
           if name else None}
    return {"text": None, "conditions": list(conditions), "fragments": [], "terms": [],
            "judgement": {"requirement_draft": []}, "interpretation": {"requirements": reqs or [req]}}


def _occ(layer, tid: str, name: str) -> dict:
    return F.indicator_facts(_analysis(name), "r1", facet_of(layer, tid), [])


# ---------------------------------------------------------------- relation of an indicator occurrence


def test_subject_and_component(layer):
    subj = _occ(layer, "T_SUBJ", "가상물가지수")
    assert {o["relation"] for o in subj["occurrences"]} == {"subject_candidate"} and subj["weak"] is False
    comp = _occ(layer, "T_COMP", "가상물가지수")
    assert [o["relation"] for o in comp["occurrences"]] == ["component"] and comp["weak"] is False


def test_mention_and_undetermined_are_weak(layer):
    ment = _occ(layer, "T_MENT", "가상서울")
    assert [o["relation"] for o in ment["occurrences"]] == ["mention"]
    assert ment["weak"] is True and "mention_only" in ment["weak_reasons"]
    # the top value of an indicator axis with values under it and no sibling: never read as the subject
    und = _occ(layer, "T_UND", "가상물가지수")
    assert [o["relation"] for o in und["occurrences"]] == ["undetermined"]
    assert und["weak"] is True and "relation_undetermined" in und["weak_reasons"]


def test_name_and_name_with_a_qualifier(layer):
    plain = _occ(layer, "T_Q2", "가상물가")
    qual = _occ(layer, "T_Q1", "가상물가")
    assert [o["method"] for o in plain["occurrences"] if o["field"] == "title"] == ["exact"]
    assert [o["method"] for o in qual["occurrences"] if o["field"] == "title"] == ["exact_with_qualifier"]
    assert plain["weak"] is False and qual["weak"] is False
    part = _occ(layer, "T_Q2", "가상물")  # part of the name only
    assert part["weak"] is True and {o["method"] for o in part["occurrences"]} == {"partial"}


# ---------------------------------------------------------------- negation and X-byeol


def _views(layer):
    return selection_catalog(layer).views


def test_negation_present_vs_only(layer):
    v = _views(layer)
    assert F._negation(v["T_N1"], ["가상서"], False) == "forbidden_present"
    assert F._negation(v["T_N2"], ["가상서"], False) == "forbidden_only"
    # plan 11 R1b (C6, approved r4 14절): the table name alone never gives forbidden_only, a value axis must show it
    assert F._negation(v["T_N1"], ["가상서"], True) == "forbidden_present"
    assert F._negation(v["T_N1"], ["가상북"], False) == "absent"


def _byeol_analysis():
    conds = [{"id": f"B{k}", "kind": "basis", "quote": w, "value": w,
              "attrs": {"axis": "classification", "value": w, "polarity": "+"}}
             for k, w in ((1, "가상갑별"), (2, "가상을별"))]
    return _analysis("가상수치", conds, own=("B1", "B2"))


def test_title_byeol_vs_axis(layer):
    an = _byeol_analysis()
    c = an["conditions"][0]
    v = _views(layer)
    title = F._byeol(an, c, v["T_X1"], [])
    axis = F._byeol(an, c, v["T_X2"], [])
    assert title["title_proof"] and not title["axis_mapping"]
    assert axis["axis_mapping"] and not axis["title_proof"]


def test_two_title_byeol_words_are_not_two_axes(layer):
    an = _byeol_analysis()
    checks = [{"name": "basis", "verdict": "pass", "source_status": "match",
               "detail": {"condition_id": cid, "status": "match", "role": "splits_table", "source": "title"}}
              for cid in ("B1", "B2")]
    checks.append({"name": "cross_axis", "verdict": "n/a", "source_status": "unknown",
                   "detail": {"support": "unknown", "reason": "no_axis_evidence", "axes": {}}})
    _, cross = F.condition_facts(an, "r1", _views(layer)["T_X1"], checks)
    assert cross["title_only_words"] == 2 and cross["independent_axes"] == "unproven"
    checks[-1]["detail"]["support"] = "supported"
    _, cross2 = F.condition_facts(an, "r1", _views(layer)["T_X2"], checks)
    assert cross2["title_only_words"] == 0 and cross2["independent_axes"] == "supported"


# ---------------------------------------------------------------- can the requested values be read together


def test_combinability_possible_impossible_undetermined(layer):
    f = facet_of(layer, "T_TREE")
    under = F.cell_facts({"indicator": [("A", "1")], "target:x": [("A", "2")]}, f)  # 가상가 under 가상갑
    across = F.cell_facts({"indicator": [("A", "1")], "target:x": [("R", "9")]}, f)  # different axes
    branch = F.cell_facts({"indicator": [("A", "1")], "target:x": [("A", "4")]}, f)  # 가상나 under 가상을
    orphan = F.cell_facts({"indicator": [("A", "1")], "target:x": [("A", "5")]}, f)  # a broken parent link
    assert under["combinable"] == "possible" and across["combinable"] == "possible"
    assert branch["combinable"] == "impossible"
    assert orphan["combinable"] == "undetermined"
    # two targets are two cells: never paired
    assert F.cell_facts({"target:x": [("A", "2")], "target:y": [("A", "4")]}, f)["combinable"] == "n/a"


# ---------------------------------------------------------------- pool cut


def _channels(last: str) -> dict:
    rows = [{"table_id": f"T_NONE{k}", "score": 1.0, "rank": k + 1, "query_ids": ["q1"]} for k in range(10)]
    rows.append({"table_id": last, "score": 0.5, "rank": 11, "query_ids": ["q1"]})
    return {"title": {"ranked_full": rows, "merged": rows[:10]}, "item": {"ranked_full": [], "merged": []}}


def test_pool_cut_only_when_a_cut_table_holds_the_name(layer):
    an = _analysis("가상물가")
    hit = F.pool_cut(an, "r1", _channels("T_Q2"), [], layer, None)
    # plan 11 R1b (C4, approved r4 14절): a cut table holding the name is restored, so it is no cut any more
    assert not hit["pool_cut"] and hit["caps"]["channel:title"] == 1 and hit["restored"] == ["T_Q2"]
    assert hit["matched"] == [{"table_id": "T_Q2", "methods": ["exact"]}]
    miss = F.pool_cut(an, "r1", _channels("T_COMP"), [], layer, None)
    assert not miss["pool_cut"] and miss["cut"] == 1
    back = F.pool_cut(an, "r1", _channels("T_Q2"), [{"table_id": "T_Q2"}], layer, None)  # checked after all
    assert not back["pool_cut"] and back["cut"] == 0


# ---------------------------------------------------------------- every complete cover, with a cap


def test_covers_all_cap():
    prov = {t: {1, 2} for t in ("A", "B", "C", "D")} | {"E": {1}, "G": {2}}
    score = {t: 1.0 for t in prov}
    got = all_covers(list(prov), {1, 2}, prov, score, cap=10)
    assert not got["cut"] and [c["tables"] for c in got["covers"]] == [["A"], ["B"], ["C"], ["D"], ["E", "G"]]
    capped = all_covers(list(prov), {1, 2}, prov, score, cap=2)
    assert capped["cut"] and len(capped["covers"]) == 2
    assert all_covers(["E"], {1, 2}, prov, score) == {"covers": [], "cut": False}  # no complete cover


# ---------------------------------------------------------------- requirement identity (7-11)


def test_copied_requirements_keep_distinct_ids():
    conds = [{"id": f"B{k}", "kind": "basis", "value": v, "attrs": {"axis": "price", "value": v, "polarity": "+"}}
             for k, v in ((1, "가상갑"), (2, "가상을"))]

    def req(rid, own):
        return {"id": rid, "span": [0, 4], "condition_ids": list(own), "shared_condition_ids": [],
                "ambiguous_condition_ids": [], "indicator": None, "targets": [], "status": "ok", "reasons": []}

    an = {"conditions": conds, "fragments": [{"id": "f1", "span": [0, 4]}],
          "judgement": {"requirement_draft": [{"id": "d1", "fragment_ids": ["f1"]}, {"id": "d2", "fragment_ids": ["f1"]}]},
          "interpretation": {"requirements": [req("r1", ["B1"]), req("r2", ["B2"])]}}
    a, b = F.requirement_identity(an, "r1"), F.requirement_identity(an, "r2")
    assert a["fragment_ids"] == b["fragment_ids"] == ["f1"] and a["key"] != b["key"]
    same = {**an, "interpretation": {"requirements": [req("r1", ["B1"]), req("r2", ["B1"])]}}
    assert F.requirement_identity(same, "r1")["key"] == F.requirement_identity(same, "r2")["key"]


# ---------------------------------------------------------------- the records reach the selection, only as records


def test_select_tables_writes_the_new_fields(layer):
    from claview.a.interpret.pipeline import interpret_question
    from claview.a.select.graph import select_tables

    out = select_tables(interpret_question("가상물가지수 보여줘", layer), layer)
    for s in out["selections"]:
        sig = s["signals"]
        assert {"facts", "covers_all", "pool_cut", "pruned"} <= set(sig)
        assert set(sig["facts"]) == {"requirement", "tables"}
        assert "options_cut" in sig["pruned"]
