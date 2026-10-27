"""Plan 07 (table-selection connection) on made-up data, each rule in both directions.

S1 breakdown check in record mode, S2 bundle-level common table, S3 questions without an indicator searched by
their targets, S4 undecided readings compared by their tables, S5 the question-level way and its ask options.
Table IDs and names are made up.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "spec"))
from spec_support import build_layer, itm, table  # noqa: E402

from claview_a.agent.question_route import MAX_OPTIONS, question_route, route_of_state, selection_notes  # noqa: E402
from claview_a.contract.check import check_selection_route  # noqa: E402
from claview_a.interpret.pipeline import interpret_question  # noqa: E402
from claview_a.interpret.text import normalize_text  # noqa: E402
from claview_a.select import candidates as cand  # noqa: E402
from claview_a.select import cover as cv  # noqa: E402
from claview_a.select.bundle import (bundle_tables, bundles, common_family, indicator_tier, no_worse,  # noqa: E402
                                      own_tables)
from claview_a.select.evidence import EvidenceInput, check_breakdown  # noqa: E402
from claview_a.select.readings import compare_readings, reading_candidates  # noqa: E402
from claview_a.select.record import drop_record  # noqa: E402
from claview_a.select.state import decide_state  # noqa: E402


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    tables = [
        # one family (bracket qualifiers only differ): 가상물가(가) / 가상물가(나)
        table("P_A", "가상물가(가)", [itm("A", "1", "가상물가", obj_name="항목"),
                                     itm("A", "2", "가상곡물", parent="1", obj_name="항목"),
                                     itm("B", "1", "을지역", obj_name="지역"), itm("B", "2", "갑지역", obj_name="지역")]),
        table("P_B", "가상물가(나)", [itm("A", "1", "가상물가", obj_name="항목"),
                                     itm("B", "1", "을지역", obj_name="지역"), itm("B", "2", "갑지역", obj_name="지역")]),
        # another family holding 갑지역 only
        table("Q_X", "가상고용", [itm("A", "1", "가상고용률", obj_name="항목"), itm("B", "1", "갑지역", obj_name="지역")]),
        table("T_M", "가상월간표", [itm("A", "1", "가상수치", unit="%")], periods=(("월", "2000.01", "2026.08"),)),
        table("T_Y", "가상연간표", [itm("A", "1", "가상연수치", unit="%")], periods=(("년", "2000", "2025"),)),
    ]
    return build_layer(tmp_path_factory.mktemp("select_plan07"), tables)


def _analysis(question: str, requirement: dict, **interp) -> dict:
    req = {"id": "r1", "quote": question, "span": [0, len(question)], "indicator": None, "measure_kind": None,
           "targets": [], "condition_ids": [], "shared_condition_ids": [], "ambiguous_condition_ids": [],
           "status": "ok", "reasons": [], **requirement}
    return {"text": normalize_text(question), "conditions": [], "fragments": [], "terms": [],
            "judgement": {"certain": True, "pairs": [], "requirement_draft": [], "failed_conditions": []},
            "interpretation": {"status": "ok", "common_condition_ids": [], "relations": [],
                               "display_condition_ids": [], "requirements": [req], "reasons": [], **interp}}


def _ind(name: str) -> dict:
    return {"quote": name, "span": [0, len(name)], "fragment_id": None, "name": name, "path": "exact"}


# ---------------------------------------------------------------- S1 breakdown (record mode)


def _breakdown_analysis(entries: list[str], named: dict[str, str | None], indicator: str | None = "가상물가",
                        joined: tuple[str, ...] = ()):
    """Question "가상물가를 A, B ..." with one fragment per entry (entries in ``joined`` share one fragment) and the
    exact term ``named[entry]`` on it (None: no exact term)."""
    q = "가상물가를 " + ", ".join(entries)
    an = _analysis(q, {"indicator": _ind(indicator) if indicator else None, "breakdown": entries})
    frags, terms = [], []
    for k, e in enumerate(entries):
        at = q.find(e)
        if e in joined and frags and frags[-1].get("joined"):
            frags[-1]["span"][1] = at + len(e)
            continue
        frags.append({"id": f"g{k}", "quote": e, "span": [at, at + len(e)], "role": "core", "protected_by": None,
                      "joined": e in joined})
        if named.get(e):
            terms.append({"fragment_id": f"g{k}", "name": named[e], "type": "indicator", "table_ids": [],
                          "obj_id": None, "obj_name": None, "path": "exact", "status": "approved", "score": None})
    for f in frags:
        f.pop("joined")
    an["fragments"], an["terms"] = frags, terms
    return an


def _breakdown(layer, table_id: str, an: dict):
    return check_breakdown(EvidenceInput(an, layer), "r1", table_id)


def test_named_lower_item_under_the_indicator_passes(layer):
    an = _breakdown_analysis(["가상곡물과", "지역별"], {"가상곡물과": "가상곡물"})
    checks, rows = _breakdown(layer, "P_A", an)
    assert len(checks) == 1 and checks[0]["name"] == "breakdown"
    assert checks[0]["detail"]["status"] == "match" and checks[0]["verdict"] == "pass"
    assert [(r["word"], r["under"]) for r in checks[0]["detail"]["rows"]] == [("가상곡물", True)]  # 지역별 not here
    assert set(rows) == set(checks[0]["evidence_ids"])


def test_named_lower_item_missing_in_the_table_fails(layer):
    an = _breakdown_analysis(["가상곡물"], {"가상곡물": "가상곡물"})
    checks, _ = _breakdown(layer, "P_B", an)
    assert checks[0]["detail"]["status"] == "none" and checks[0]["verdict"] == "fail"
    two = _breakdown_analysis(["가상곡물", "을지역"], {"가상곡물": "가상곡물", "을지역": "을지역"})
    some, _ = _breakdown(layer, "P_B", two)
    assert some[0]["detail"]["status"] == "partial" and some[0]["verdict"] == "pass"


def test_unnamed_and_axis_entries_get_no_check(layer):
    """r4 9-4: no exact term, or the words over two fragments, or an "X별" axis -> not judged (no check)."""
    assert _breakdown(layer, "P_A", _breakdown_analysis([], {})) == ([], {})
    assert _breakdown(layer, "P_A", _breakdown_analysis(["가상곡물"], {"가상곡물": None})) == ([], {})
    assert _breakdown(layer, "P_A", _breakdown_analysis(["지역별"], {"지역별": "지역별"})) == ([], {})
    over_two = _breakdown_analysis(["가상곡물"], {"가상곡물": "가상곡물"})
    over_two["fragments"] = [{**over_two["fragments"][0], "id": "g0", "span": [6, 8]},
                             {**over_two["fragments"][0], "id": "g9", "span": [8, 10]}]
    assert _breakdown(layer, "P_A", over_two) == ([], {})


def test_breakdown_is_record_only_in_the_rank_score():
    fail = [{"name": "breakdown", "verdict": "fail", "source_status": "none", "detail": {"status": "none"}}]
    passed = [{"name": "breakdown", "verdict": "pass", "source_status": "match", "detail": {"status": "match"}}]
    assert cv.rank_score({"fused_score": 1.0}, fail, "", None) == cv.rank_score({"fused_score": 1.0}, passed, "",
                                                                                   None) == 1.0


def test_breakdown_drops_are_recorded():
    def entry(tid, verdict):
        return {"requirement_id": "r1", "table_id": tid, "checks": [
            {"name": "indicator", "verdict": "pass", "source_status": "match",
             "detail": {"result": "match", "available": True}},
            {"name": "breakdown", "verdict": verdict, "source_status": "x", "detail": {}}]}

    got = {"evidence": {"evidence": [entry("T", "fail")]}, "selections": [{"requirement_id": "r1", "signals": {}}]}
    assert drop_record(got, [["T"]], ("breakdown",))["dropped"] == {"breakdown": 1}
    kept = {"evidence": {"evidence": [entry("T", "fail"), entry("U", "pass")]},
            "selections": [{"requirement_id": "r1", "signals": {}}]}
    assert drop_record(kept, [["T", "U"]], ("breakdown",))["dropped"] == {"breakdown": 0}


# ---------------------------------------------------------------- S2 bundles


def _interp(reqs: list[dict], **kw) -> dict:
    return {"interpretation": {"requirements": reqs, "relations": kw.get("relations", []),
                               "table_group_hint": kw.get("hint", [])}}


def _r(rid, name=None, status="ok"):
    return {"id": rid, "status": status, "indicator": {"name": name} if name else None}


def test_bundles_from_the_same_table_hint_only():
    """r4 decision 5: table_group_hint bundles; relations and copies of one indicator do not (r1 test replaced)."""
    an = _interp([_r("r1", "갑"), _r("r2", "을"), _r("r3", "병"), _r("r4", "병"), _r("r5", "정")],
                 hint=[["r1", "r2"], ["r2", "r5"]], relations=[{"kind": "ratio", "requirement_ids": ["r3", "r4"]}])
    assert bundles(an) == [["r1", "r2", "r5"]]


def test_unrelated_and_unsupported_requirements_are_no_bundle():
    an = _interp([_r("r1", "갑"), _r("r2", "갑"), _r("r3", "갑", status="unsupported")],
                 hint=[["r1", "r3"]], relations=[{"kind": "compare", "requirement_ids": ["r1", "r2"]}])
    assert bundles(an) == []


def _alt(fam, tables, covered, complete=True, score=10.0):
    return {"family": fam, "tables": tables, "covered": set(covered), "complete": complete, "truncated": False,
            "best_score": score, "score_sum": score, "tier": 1.0}


def _cover(alts, usable, prov, cells, scores=None):
    return {"alternatives": alts, "usable": usable, "provided": prov, "cells": set(cells), "siblings": usable,
            "scores": scores or {t: 1.0 for t in usable}, "order": {t: k for k, t in enumerate(usable)},
            "words": {}, "named": set()}


def _choice(alt):
    return {"chosen": alt}


def test_common_family_needs_a_complete_alternative_for_every_requirement():
    f1, g1 = _alt("F", ["A"], {1}), _alt("G", ["G1"], {1}, score=20.0)
    f2 = _alt("F", ["B"], {2})
    covers = {"r1": _cover([g1, f1], ["A", "G1"], {"A": {1}, "G1": {1}}, {1}),
              "r2": _cover([f2], ["B"], {"B": {2}}, {2})}
    assert common_family(["r1", "r2"], covers, {"r1": _choice(g1), "r2": _choice(f2)}) == "F"
    partial = {**covers, "r2": _cover([_alt("F", ["B"], {2}, complete=False)], ["B"], {"B": {2}}, {2, 3})}
    # r5 Q0 (bundle first): the family covering the most (requirement, cell) pairs, a partial one included
    assert common_family(["r1", "r2"], partial, {"r1": _choice(g1)}) == "F"
    mixed = {"r1": _cover([_alt("mixed", ["A"], {1})], ["A"], {"A": {1}}, {1}),
             "r2": _cover([_alt("mixed", ["B"], {2})], ["B"], {"B": {2}}, {2})}
    assert common_family(["r1", "r2"], mixed, {}) is None


def _family_covers(layer, scores_a, scores_b):
    fam = cand.family_key(layer, "P_A")
    prov = {"P_A": {1}, "P_B": {1}}
    r1 = _cover([_alt(fam, ["P_A"], {1})], ["P_A", "P_B"], prov, {1}, scores_a)
    r2 = _cover([_alt(fam, ["P_B"], {1})], ["P_A", "P_B"], prov, {1}, scores_b)
    return fam, {"r1": r1, "r2": r2}


def test_bundle_representative_uses_the_bundle_rank(layer):
    """One representative over the bundle cells, by the sum of the requirements' rank scores, both directions."""
    views = cv.selection_catalog(layer).views
    fam, covers = _family_covers(layer, {"P_A": 9.0, "P_B": 1.0}, {"P_A": 2.0, "P_B": 5.0})
    tables, marks = bundle_tables(fam, ["r1", "r2"], covers, layer, views, "representative")
    assert tables == ["P_A"] and [m["kind"] for m in marks] == ["other"]  # the left-out table is marked
    assert own_tables(tables, covers["r1"]) == own_tables(tables, covers["r2"]) == ["P_A"]
    fam, covers = _family_covers(layer, {"P_A": 2.0, "P_B": 1.0}, {"P_A": 1.0, "P_B": 9.0})
    assert bundle_tables(fam, ["r1", "r2"], covers, layer, views, "representative")[0] == ["P_B"]
    assert bundle_tables(fam, ["r1", "r2"], covers, layer, views, "keep_all") == (["P_A", "P_B"], [])


def test_replacement_keeps_own_conditions_and_evidence():
    old = {"floor": "auto", "unproven": []}
    assert no_worse({"floor": "auto", "unproven": []}, old)
    assert not no_worse({"floor": "confirm", "unproven": []}, old)
    assert not no_worse({"floor": "auto", "unproven": [{"check": "unit"}]}, old)
    ev = {"evidence": [{"requirement_id": "r1", "table_id": t, "checks": [
        {"name": "indicator", "detail": {"result": res, "available": True}}]} for t, res in (("A", "match"),
                                                                                            ("B", "partial"))]}
    assert indicator_tier(ev, "r1", ["A"]) == 1.0 and indicator_tier(ev, "r1", ["B"]) == 0.5


# ---------------------------------------------------------------- S3 no indicator: targets (r4)


def _broad(target: str, failed=("no_indicator",), types=("target",)) -> dict:
    q = f"{target} 자료를 보여줘"
    an = _analysis(q, {"targets": [{"quote": target, "span": [0, len(target)], "fragment_id": "g0",
                                    "name": target}], "reasons": ["broad"]})
    an["judgement"] = {**an["judgement"], "certain": False, "failed_conditions": list(failed)}
    an["interpretation"]["status"] = "ask"
    an["terms"] = [{"fragment_id": "g0", "name": target, "type": t, "table_ids": [], "obj_id": None,
                    "obj_name": None, "path": "exact", "status": "approved", "score": None} for t in types]
    return an


def _target_selection(layer, an):
    from claview_a.select.graph import select_tables

    return select_tables(an, layer)["selections"][0]


def test_targets_in_one_family(layer):
    an = _broad("을지역")
    assert cand.no_indicator(an, "r1") and cand.target_tables(an, "r1", layer) == ["P_A", "P_B"]
    info: dict = {}
    pool = cand.expand_candidates(an, "r1", {"title": {"merged": []}, "item": {"merged": []}}, layer, info)
    assert info["target_search"] == {"targets": 1, "tables": 2, "truncated": False}
    assert [(p["table_id"], p["entered_by"]) for p in pool] == [("P_A", "target"), ("P_B", "target")]
    sel = _target_selection(layer, an)
    assert sel["signals"]["target_search"]["families"] == 1
    assert sel["status"] == "confirm" and "no_indicator" in sel["reasons"]


def test_targets_in_several_families(layer):
    an = _broad("갑지역")
    assert cand.target_tables(an, "r1", layer) == ["P_A", "P_B", "Q_X"]
    assert _target_selection(layer, an)["signals"]["target_search"]["families"] == 2


def test_target_search_only_for_a_no_indicator_question(layer):
    """r4: a requirement without an indicator next to indicator requirements (no check 9 failure) is not searched by
    its targets; neither is a requirement with an indicator."""
    mixed = _broad("을지역", failed=())
    assert not cand.no_indicator(mixed, "r1")
    info: dict = {}
    cand.expand_candidates(mixed, "r1", {"title": {"merged": []}, "item": {"merged": []}}, layer, info)
    assert "target_search" not in info
    with_ind = _broad("을지역")
    with_ind["interpretation"]["requirements"][0]["indicator"] = _ind("가상물가")
    assert not cand.no_indicator(with_ind, "r1")


def test_total_headers_and_basis_terms_are_no_targets(layer):
    assert cand.search_targets(_broad("을지역", types=("total_header",)), "r1") == []
    assert cand.target_tables(_broad("을지역", types=("basis",)), "r1", layer) == []
    assert cand.search_targets(_broad("을지역", types=("total_header", "target")), "r1") == ["을지역"]


def test_no_indicator_selection_is_never_auto():
    analysis = {"interpretation": {"status": "ok", "requirements": [{"id": "r1", "status": "ok"}]},
                "judgement": {"certain": True}}
    chosen = {"tables": ["T1"], "complete": True, "truncated": False}
    choice = {"chosen": chosen, "contenders": [chosen], "unspecified": [], "added": [], "differences": []}
    recheck = {"floor": "auto", "tables": [], "covered": 1, "cells": 1, "unproven": []}
    kw = dict(unsupported=False, no_evidence=False, choice=choice, recheck=recheck, cover_flags=[])
    assert decide_state(analysis, "r1", **kw, signals={"target_search": {"tables": 1}}) == (
        "confirm", ["no_indicator"])
    assert decide_state(analysis, "r1", **kw, signals={}) == ("auto", [])


def test_target_tables_have_evidence_without_the_indicator_check():
    none = [{"name": "indicator", "detail": {"result": "none", "available": True}}]
    assert cv.has_evidence({"entered_by": "target"}, none)
    assert not cv.has_evidence({"entered_by": "search"}, none)


# ---------------------------------------------------------------- S4 readings


def _sel(layer, question):
    from claview_a.select.graph import select_tables

    an = interpret_question(question, layer)
    return an, select_tables(an, layer)["selections"]


def test_readings_choosing_the_same_tables_are_same(layer):
    an, sels = _sel(layer, "가상수치를 보여줘")
    other = copy.deepcopy(an)
    got = compare_readings(an, sels, layer, candidates=[{"point": "P1", "option": "o1", "analysis": other}])
    assert got["status"] == "same" and len(got["readings"]) == 2


def test_readings_choosing_other_tables_differ(layer):
    an, sels = _sel(layer, "가상수치를 보여줘")
    other, _ = _sel(layer, "가상연수치를 보여줘")
    got = compare_readings(an, sels, layer, candidates=[{"point": "P1", "option": "o1", "analysis": other}])
    assert got["status"] == "differ"


def test_settled_question_has_no_reading_candidates(layer):
    an, sels = _sel(layer, "가상수치를 보여줘")
    settled = {**an, "judgement": {**an["judgement"], "certain": True},
               "interpretation": {**an["interpretation"], "status": "ok"}}
    assert reading_candidates(settled, layer) == []
    assert reading_candidates(an, None) == []
    assert compare_readings(settled, sels, layer)["status"] == "none"


# ---------------------------------------------------------------- S5 question route (r4, rows of decision 1)


def _s(rid, status="auto", tables=("T1",), reasons=(), **kw):
    return {"requirement_id": rid, "status": status, "tables": [{"table_id": t} for t in tables],
            "model_step": kw.pop("model_step", "skipped"), "unspecified": kw.pop("unspecified", []),
            "reasons": list(reasons), "alternatives": kw.pop("alternatives", []), "signals": kw.pop("signals", {})}


def _q(*reqs, status="ok", failed=(), fragments=(), model=None):
    out = {"interpretation": {"status": status, "requirements": list(reqs)},
           "judgement": {"certain": not failed, "failed_conditions": list(failed)},
           "fragments": list(fragments)}
    if model is not None:
        out["judgement"]["model"] = model
    return out


def _req(rid, status="ok", name="갑", reasons=(), span=(0, 4)):
    return {"id": rid, "status": status, "indicator": {"name": name} if name else None, "reasons": list(reasons),
            "span": list(span)}


NOTES_POINTS = {"points": [{"id": "P1", "kind": "name", "form": "", "ask": "?", "requirement_ids": ["r1"],
                            "options": [{"id": "o1", "text": "갑 값", "effect": "name"},
                                        {"id": "o2", "text": "을 값", "effect": "name"}]}]}
SAME = {"status": "same", "readings": [{"source": "interpretation", "tables": ["T1"]},
                                       {"source": "P1:o1", "tables": ["T1"]}]}
DIFFER = {"status": "differ", "readings": [{"source": "interpretation", "tables": ["T1"]},
                                           {"source": "P1:o1", "tables": ["T9"]}]}
TS1 = {"target_search": {"targets": 1, "tables": 2, "truncated": False, "families": 1, "family_tables": ["T1"]}}
TS3 = {"target_search": {"targets": 1, "tables": 4, "truncated": False, "families": 3,
                         "family_tables": ["T1", "T2", "T3"]}}
TS6 = {"target_search": {"targets": 1, "tables": 9, "truncated": False, "families": 6,
                         "family_tables": [f"T{k}" for k in range(6)]}}
MIXED = [{"dimension": "price", "kind": "mixed_basis", "table_ids": ["T1"], "other_table_ids": ["T2"]}]
OTHER = [{"dimension": "agency_values", "kind": "other", "table_ids": ["T1"], "other_table_ids": ["T2"]}]


@pytest.mark.parametrize("analysis, selections, notes, want", [
    # row 0
    (_q(_req("r1"), _req("r2")), [_s("r1", "no_table", (), ["no_cover"]), _s("r2", "no_table", (), ["no_evidence"])],
     {}, ("no_table", ["no_cover", "no_evidence"])),
    (_q(_req("r1", "unsupported")), [_s("r1", "no_table", (), ["unsupported"])], {}, ("no_table", ["unsupported"])),
    (_q(), [], {}, ("ask_interpretation", ["no_requirement"])),
    # row 2
    # user decision 2026-10-20 01:57 (plan 10 section 3): a branch whose selection settled one table goes on
    (_q(_req("r1", reasons=["branch"])), [_s("r1")], {"branch_names": {"r1": ["갑", "을"]}},
     ("proceed", ["branch_resolved"])),
    # row 3 (r5: the old open interpretation asks; the requirement has a table)
    # r5 Q5 rule 4: covered + interpretation undecided -> proceed (confirm)
    (_q(_req("r1", "unresolved", None, ["row:10"]), failed=["row_2_or_7"]), [_s("r1", "confirm")], {},
     ("proceed", ["unresolved", "broad_mixed", "confirm"])),
    # r5 Q5 rule 4: covered + interpretation undecided -> proceed (confirm)
    (_q(_req("r1", "unresolved", None, ["row:6"])), [_s("r1", "confirm")], {},
     ("proceed", ["unresolved", "broad_mixed", "confirm"])),
    # r5 Q5 rule 4: covered + interpretation undecided -> proceed (confirm)
    (_q(_req("r1", reasons=["model_ask"]), failed=["leftover_word"], model={"outcome": "done", "notes": []}),
     [_s("r1", "confirm")], {"leftover": {"말": ["갑"]}},
     ("proceed", ["model_ask", "leftover_with_candidate", "confirm"])),
    # r5 Q5 rule 4: covered + interpretation undecided -> proceed (confirm)
    (_q(_req("r1"), failed=["leftover_word"]), [_s("r1", "confirm")], {"leftover": {"말": []}},
     ("proceed", ["leftover_no_candidate", "confirm"])),
    # r5 Q5 rule 4: covered + interpretation undecided -> proceed (confirm)
    (_q(_req("r1"), failed=["ambiguous_word"]), [_s("r1", "confirm")], {}, ("proceed", ["ambiguous_word", "confirm"])),
    # row 4 (r5: the old interpretation asks with options; the requirement has a table)
    # r5 Q5 rule 4: covered + interpretation undecided -> proceed (confirm)
    (_q(_req("r1"), failed=["inexact_term"]), [_s("r1", "confirm")], NOTES_POINTS,
     ("proceed", ["inexact_term", "confirm"])),
    # r5 Q5 rule 4: covered + undecided, readings giving the same tables -> proceed (confirm)
    (_q(_req("r1"), failed=["leftover_word"]), [_s("r1", "confirm")], {"leftover": {"말": ["갑"]}, "readings": SAME},
     ("proceed", ["leftover_with_candidate", "readings_same", "confirm"])),
    (_q(_req("r1"), failed=["inexact_term"]), [_s("r1", "confirm")], {"readings": DIFFER},
     ("ask_selection", ["inexact_term", "readings_differ"])),
    # r5 Q5 rule 4: covered + interpretation undecided -> proceed (confirm)
    (_q(_req("r1", "unresolved", "갑", ["row:7"])), [_s("r1", "confirm")], {}, ("proceed", ["unresolved", "confirm"])),
    # r5 Q5 rule 4: covered + interpretation undecided -> proceed (confirm)
    (_q(_req("r1"), failed=["row_2_or_7"], fragments=[{"id": "g1", "span": [1, 3], "phrase": "appositive"}]),
     [_s("r1", "confirm")], {}, ("proceed", ["appositive", "confirm"])),
    # r5 Q5 rule 4: covered + interpretation undecided -> proceed (confirm)
    (_q(_req("r1", reasons=["model_ask"]), model={"outcome": "rejected", "notes": ["recheck:x"]}),
     [_s("r1", "confirm")], {}, ("proceed", ["model_ask", "model_rejected", "confirm"])),
    # r5 Q5 rule 4: covered + interpretation undecided -> proceed (confirm)
    (_q(_req("r1"), _req("r2", name=None, span=(5, 8))), [_s("r1"), _s("r2", "confirm")], {},
     ("proceed", ["broad_mixed", "confirm"])),
    # row 5
    (_q(_req("r1", name=None), failed=["no_indicator"]), [_s("r1", "confirm", signals=TS1)], {},
     ("proceed", ["no_indicator"])),
    (_q(_req("r1", name=None), failed=["no_indicator"]), [_s("r1", "confirm", signals=TS3)], {},
     ("ask_selection", ["target_families"])),
    (_q(_req("r1", name=None), failed=["no_indicator"]), [_s("r1", "confirm", signals=TS6)], {},
     ("ask_interpretation", ["no_indicator_open"])),
    # row 6
    (_q(_req("r1")), [_s("r1", "confirm", model_step="pending", alternatives=[["T2"]])], {},
     ("ask_selection", ["family_alternatives"])),
    (_q(_req("r1")), [_s("r1", "confirm", unspecified=MIXED)], {}, ("ask_selection", ["unspecified_mixed_basis"])),
    # rows 7-8
    (_q(_req("r1")), [_s("r1", "confirm", reasons=["unspecified"], unspecified=OTHER)], {},
     ("proceed", ["unspecified"])),
    (_q(_req("r1")), [_s("r1", "confirm", reasons=["partial_cover"])], {}, ("proceed", ["partial_cover"])),
    (_q(_req("r1"), failed=["unverified_structure"]), [_s("r1")], {}, ("proceed", ["unverified_structure"])),
    (_q(_req("r1")), [_s("r1")], {}, ("proceed", [])),
], ids=["all_no_table", "unsupported", "no_requirement", "branch", "row10", "row6", "model_ask", "leftover_none",
        "ambiguous_word", "inexact_points", "readings_same", "readings_differ", "unresolved_other", "appositive",
        "model_rejected", "broad_mixed", "s3_one", "s3_three", "s3_six", "families", "mixed_basis",
        "other_unspecified", "partial_cover", "unverified", "auto"])
def test_route_rows(analysis, selections, notes, want):
    got = question_route(analysis, selections, notes)
    assert (got["kind"], got["reasons"]) == want
    assert check_selection_route(got) == []


def test_route_flags():
    got = question_route(_q(_req("r1"), _req("r2")), [_s("r1", "confirm", reasons=["partial_cover"]),
                                                        _s("r2", "no_table", (), ["no_cover"])])
    assert got["kind"] == "proceed" and got["confirm"] and got["partial"]
    auto = question_route(_q(_req("r1")), [_s("r1")])
    assert not auto["confirm"] and not auto["partial"] and auto["options"] == [] and auto["default"] is None
    assert question_route(_q(_req("r1"), status="partial"), [_s("r1")])["partial"]


def test_v2_states_are_read():
    got = question_route(_q(_req("r1")), [_s("r1")], {}, v2_states={"r1": "no_table"})
    assert got["kind"] == "no_table"


def test_route_of_state_reads_the_recorded_notes_and_v2():
    state = {"analysis": _q(_req("r1"), failed=["inexact_term"]), "selections": [_s("r1", "confirm")],
             "verification": {"selection": {"states": {"r1": "confirm"}}},
             "a_outcome": {"parts": {"select": {"question": {"readings": DIFFER}}}}}
    assert route_of_state(state)["kind"] == "ask_selection"
    state["verification"]["selection"]["states"] = {"r1": "no_table"}
    assert route_of_state(state)["kind"] == "no_table"


def test_options_are_at_most_five_and_keep_every_default():
    sels = [_s(f"r{k}", "confirm", (f"T{k}",), model_step="pending",
               alternatives=[[f"A{k}{j}"] for j in range(3)]) for k in (1, 2, 3)]
    got = question_route(_q(*[_req(f"r{k}") for k in (1, 2, 3)]), sels, {})
    assert len(got["options"]) == MAX_OPTIONS
    assert [o["table_ids"] for o in got["options"] if o["default"]] == [["T1"], ["T2"], ["T3"]]
    assert got["default"] == {"choices": {"r1": ["T1"], "r2": ["T2"], "r3": ["T3"]}}


def test_branch_and_point_options():
    # user decision 2026-10-20 01:57 (plan 10 section 3): settled one table -> proceed (branch_resolved); the names
    # are asked when the table's period cannot be checked; alternatives left -> a table ask over the bundles
    names = {"branch_names": {"r1": ["갑", "을"]}}
    got = question_route(_q(_req("r1", reasons=["branch"])), [_s("r1")], names)
    assert got["kind"] == "proceed" and got["confirm"] and got["reasons"] == ["branch_resolved"]
    no_period = question_route(_q(_req("r1", reasons=["branch"])), [_s("r1", signals={"period_check": True})], names)
    assert [o["name"] for o in no_period["options"]] == ["갑", "을"] and no_period["default"] == {"keep": True}
    left = question_route(_q(_req("r1", reasons=["branch"])),
                          [_s("r1", "confirm", model_step="pending", alternatives=[["T2"]])], names)
    assert left["kind"] == "ask_selection" and [o["table_ids"] for o in left["options"]] == [["T1"], ["T2"]]
    pts = question_route(_q(_req("r1"), failed=["inexact_term"]), [_s("r1", "confirm")], NOTES_POINTS)
    # r5 Q5 rule 4 / Q7: a covered requirement goes on (no interpretation ask, so no point options)
    assert pts["kind"] == "proceed" and pts["options"] == []
    open_ask = question_route(_q(_req("r1"), failed=["inexact_term"]), [_s("r1", "confirm")], {})
    assert open_ask["options"] == []


def test_option_labels_come_from_the_recorded_notes(layer):
    an = _analysis("갑지역 가상물가", {"indicator": _ind("가상물가")})
    sels = [_s("r1", "confirm", ("P_A",), model_step="pending", alternatives=[["Q_X"]])]
    notes = selection_notes(an, sels, layer)
    assert notes["labels"]["P_A"] == {"table_id": "P_A", "table_name": "가상물가(가)", "values": ["가상물가", "갑지역"]}
    got = question_route(an, sels, notes)
    labels = {t["table_id"]: t for o in got["options"] for t in o["tables"]}
    assert labels["Q_X"]["values"] == ["갑지역"]


def test_outer_graph_records_the_route_after_v2(layer):
    from claview_a.agent.graph import run_outer

    for variant in ("straight", "central"):
        out = run_outer("가상수치를 보여줘", variant, layer=layer)
        route = out["selection_route"]
        assert route is not None and check_selection_route(route) == []
        assert route == route_of_state(out)  # the same pure function over the final state
        assert "question" in out["a_outcome"]["parts"]["select"]


def test_respond_fills_the_route_when_the_run_ends_before_selection(layer):
    from claview_a.agent import nodes

    state = {"analysis": _q(_req("r1", "unsupported")), "selections": [], "selection_route": None,
             "verification": {}, "a_outcome": {"stage": "A", "status": None, "parts": {}},
             "control": {"decisions": [{"type": "route", "final": {"kind": "finish",
                                                                   "args": {"reason": "unsupported"}},
                                        "guard_reason": None}]}}
    out = nodes.respond(state, {"configurable": {"layer": layer}})
    assert out["selection_route"]["kind"] == "no_table" and out["selection_route"]["reasons"] == ["unsupported"]
