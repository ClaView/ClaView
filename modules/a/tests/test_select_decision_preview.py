"""Plan 11 R1b: the observation path of table selection (S5 cover enumeration, S6 requirement decisions,
C4 restoration), written to ``signals.decision_preview`` only; the selection itself is unchanged. Made-up tables."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "spec"))
from spec_support import build_layer, itm, table  # noqa: E402

from claview_a.interpret.pipeline import interpret_question  # noqa: E402
from claview_a.select import decision as D  # noqa: E402
from claview_a.select import facts as F  # noqa: E402
from claview_a.select import refute as R  # noqa: E402
from claview_a.select.candidates import family_key  # noqa: E402
from claview_a.select.cover import enumerate_assignments  # noqa: E402
from claview_a.select.graph import select_tables  # noqa: E402
from claview_a.select.tables import selection_catalog  # noqa: E402

IND = "BSI코드별"
TGT = "지역코드별"
NAME = "가상물가지수"


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    tables = [
        table("T_E1", f"{NAME}(2000~2010)", [itm("A", "1", NAME, obj_name=IND)], periods=(("월", "2000.01", "2010.12"),)),
        table("T_E2", f"{NAME}(2011~)", [itm("A", "1", NAME, obj_name=IND)], periods=(("월", "2011.01", "2026.08"),)),
        table("T_F", f"{NAME}(분기)", [itm("A", "1", NAME, obj_name=IND)], periods=(("분기", "2000Q1", "2026Q2"),)),
        table("T_W", "가상임금표", [itm("A", "1", "가상임금", obj_name=IND)]),
    ]
    return build_layer(tmp_path_factory.mktemp("decision_preview"), tables)


# ---------------------------------------------------------------- S5: covers and the assignments beside them (K2)


def test_complete_cover_keeps_unproven_alternatives_beside_it():
    prov = {"A": {1, 2}, "B": {1}, "C": {2}, "E": {1}}
    got = enumerate_assignments(["A", "B", "C", "E"], {1, 2}, prov, {u: 1.0 for u in prov})
    assert [c["units"] for c in got["covers"]] == [["A"], ["B", "C"], ["C", "E"]]
    assert all(c["complete"] for c in got["covers"]) and not got["cut"]
    lone = enumerate_assignments(["A", "B"], {1, 2}, {"A": {1, 2}, "B": {1}}, {"A": 1.0, "B": 2.0})
    assert [c["units"] for c in lone["covers"]] == [["A"]]
    assert lone["partial"] == [{"units": ["B"], "complete": False}]  # kept beside the complete cover


def test_no_complete_cover_gives_partial_covers_and_a_cap():
    got = enumerate_assignments(["A", "B"], {1, 2, 3}, {"A": {1}, "B": {2}}, {"A": 1.0, "B": 1.0})
    assert [c["units"] for c in got["covers"]] == [["A", "B"]] and not got["covers"][0]["complete"]
    prov = {u: {1} for u in "ABCD"}
    capped = enumerate_assignments(list(prov), {1}, prov, {u: 1.0 for u in prov}, cap=2)
    assert capped["cut"] and len(capped["covers"]) == 2


def test_cover_cap_stops_the_search_and_cut_units_are_not_partial():
    prov = {"A": {1}, "B": {2}, "C": {1}, "D": {2}}
    score = {u: 1.0 for u in prov}
    whole = enumerate_assignments(list(prov), {1, 2}, prov, score)
    assert not whole["cut"] and whole["cut_units"] == [] and len(whole["covers"]) == 4
    got = enumerate_assignments(list(prov), {1, 2}, prov, score, cap=1)
    assert got["cut"] and got["tried"] < whole["tried"]  # stopped as soon as the cap was passed
    assert [c["units"] for c in got["covers"]] == [["A", "B"]]
    # C and D may be in covers never explored (C+B, C+D) or dropped (A+D): possible, not partial.
    assert got["cut_units"] == ["C", "D"]
    assert got["partial"] == []


def test_completed_search_keeps_partial_units():
    prov = {"A": {1, 2}, "B": {1}, "C": {2}, "E": {1}, "F": {3}}
    got = enumerate_assignments(list(prov), {1, 2}, prov, {u: 1.0 for u in prov})
    assert not got["cut"] and got["cut_units"] == []
    assert [c["units"] for c in got["covers"]] == [["A"], ["B", "C"], ["C", "E"]]
    assert got["partial"] == []  # F gives no requested cell, so it is not a unit at all
    beside = enumerate_assignments(["A", "B", "C"], {1, 2}, {"A": {1, 2}, "B": {1}, "C": {1}},
                                   {"A": 1.0, "B": 1.0, "C": 1.0})
    assert not beside["cut"] and beside["cut_units"] == []
    assert beside["partial"] == [{"units": ["B"], "complete": False}, {"units": ["C"], "complete": False}]


def test_larger_combination_containing_a_smaller_cover_is_excluded():
    prov = {"A": {1}, "B": {2}, "C": {1, 3}, "D": {2, 3}, "E": {3}}
    got = enumerate_assignments(list(prov), {1, 2, 3}, prov, {u: 1.0 for u in prov})
    covers = [c["units"] for c in got["covers"]]
    assert ["A", "D"] in covers and ["C", "D"] in covers and ["B", "C"] in covers
    # Contains the cover C+D: not minimal, never recorded.
    assert ["A", "C", "D"] not in covers and ["B", "C", "D"] not in covers
    # Contains no smaller cover: kept.
    assert ["A", "B", "E"] in covers
    assert not got["cut"]


# ---------------------------------------------------------------- edition linking


def test_edition_linking_source_period_only(layer):
    views = selection_catalog(layer).views
    tids = ["T_E1", "T_E2", "T_F"]
    fam = {t: family_key(layer, t) for t in tids}
    assert len(set(fam.values())) == 1
    groups = D.editions(tids, {t: views[t] for t in tids}, fam, {t: k for k, t in enumerate(tids)})
    assert ["T_E2", "T_E1"] in groups  # one alternative, the latest edition first
    assert ["T_F"] in groups  # another frequency is another alternative


def test_edition_link_is_one_alternative_in_the_preview(layer):
    out = select_tables(interpret_question(f"{NAME} 보여줘", layer), layer)
    dec = out["selections"][0]["signals"]["decision_preview"]["decision"]
    linked = [a for a in dec["alternatives"] if a["edition_links"]]
    assert linked and linked[0]["edition_links"][0]["members"] == ["T_E2", "T_E1"]
    assert any(a["tables"] == ["T_F"] for a in dec["alternatives"])
    assert {d["kind"] for d in dec["dimensions"]} & {"frequency"}


# ---------------------------------------------------------------- today's selection, order, certificates


def test_today_selection_is_the_first_alternative(layer):
    out = select_tables(interpret_question(f"{NAME} 보여줘", layer), layer)
    for s in out["selections"]:
        dec = s["signals"]["decision_preview"]["decision"]
        assert dec["today"]["in_alternatives"] and dec["today"]["first"]
        assert dec["alternatives"][0]["id"] == dec["today"]["alternative_id"]
        assert D.decisions_from(out["selections"])[s["requirement_id"]] is dec


def test_today_not_among_the_alternatives_is_explained(layer):
    an = interpret_question(f"{NAME} 보여줘", layer)
    out = select_tables(copy.deepcopy(an), layer)
    s = out["selections"][0]
    st = {"pool": [{"table_id": t, "entered_by": "search", "family": family_key(layer, t), "rank": k}
                   for k, t in enumerate(["T_E1", "T_W"], 1)],
          "evidence": out["evidence"], "facts": s["signals"]["facts"], "pool_cut": {}}
    st["facts"] = {"tables": F.requirement_facts(an, "r1", st["pool"], None, layer, None)["tables"]}
    fake = {**s, "status": "confirm", "tables": [{"table_id": "T_W"}]}
    got = D.requirement_preview(an, "r1", st, fake, layer)["decision"]["today"]
    assert not got["in_alternatives"] and got["explained_by"] == ["indicator_absent"]


def test_order_puts_complete_first_and_the_selection_is_unchanged(layer):
    an = interpret_question(f"{NAME} 보여줘", layer)
    out = select_tables(copy.deepcopy(an), layer)
    for s in out["selections"]:
        alts = s["signals"]["decision_preview"]["decision"]["alternatives"]
        assert [a["order"] for a in alts] == sorted(a["order"] for a in alts)
        done = [a["complete"] for a in alts]
        assert done == sorted(done, reverse=True)
        assert {a["label"] for a in alts} <= {"proven", "possible"}
        assert all(a["label"] == "possible" for a in alts if a["gaps"] or not a["complete"])
    stripped = copy.deepcopy(out)
    for s in stripped["selections"]:
        s["signals"].pop("decision_preview")
    assert stripped["selections"][0]["tables"] == out["selections"][0]["tables"]


def test_every_left_out_candidate_has_a_certificate(layer):
    out = select_tables(interpret_question(f"{NAME} 보여줘", layer), layer)
    for s in out["selections"]:
        prev = s["signals"]["decision_preview"]
        tabs, cells = prev["refutations"]["tables"], prev["refutations"]["cells"]
        certs = [c for cs in tabs.values() for c in cs] + [c for t in cells.values() for cs in t.values() for c in cs]
        assert all(R.is_certificate(c) for c in certs)
        kept = {t for a in prev["decision"]["alternatives"] for t in a["tables"]}
        kept |= {m for a in prev["decision"]["alternatives"] for e in a["edition_links"] for m in e["members"]}
        for t in s["signals"]["facts"]["tables"]:
            if t not in kept:
                assert t in tabs or t in cells, f"{t} left out without a certificate"
        assert "T_W" not in kept and _codes(tabs.get("T_W", [])) == {"indicator_absent"}


def _codes(certs):
    return {c["code"] for c in certs}


# ---------------------------------------------------------------- restoration (C4)


def _channels(cut: str) -> dict:
    rows = [{"table_id": t, "score": 1.0, "rank": k + 1, "query_ids": ["q1"]} for k, t in enumerate(["T_W"] * 10)]
    rows.append({"table_id": cut, "score": 0.5, "rank": 11, "query_ids": ["q1"]})
    return {"title": {"ranked_full": rows, "merged": rows[:10]}, "item": {"ranked_full": [], "merged": []}}


def test_restoration_removes_a_pool_cut(layer, monkeypatch):
    an = interpret_question(f"{NAME} 보여줘", layer)
    got = F.pool_cut(an, "r1", _channels("T_F"), [{"table_id": "T_W"}], layer, None)
    assert got["cut"] == 1 and got["restored"] == ["T_F"] and not got["pool_cut"]
    monkeypatch.setattr(F, "RESTORE_CAP", 0)
    capped = F.pool_cut(an, "r1", _channels("T_F"), [{"table_id": "T_W"}], layer, None)
    assert capped["restored"] == [] and capped["pool_cut"]  # the restoration itself was cut


def test_restoration_leaves_out_a_table_without_the_name(layer):
    an = interpret_question(f"{NAME} 보여줘", layer)
    rows = [{"table_id": "T_E1", "score": 1.0, "rank": k + 1, "query_ids": ["q1"]} for k in range(10)]
    rows.append({"table_id": "T_W", "score": 0.5, "rank": 11, "query_ids": ["q1"]})
    ch = {"title": {"ranked_full": rows, "merged": rows[:10]}, "item": {"ranked_full": [], "merged": []}}
    got = F.pool_cut(an, "r1", ch, [{"table_id": "T_E1"}], layer, None)
    assert got["restored"] == [] and got["left_out"] == 1 and not got["pool_cut"]


def test_restored_candidate_reaches_the_preview_only(layer):
    an = interpret_question(f"{NAME} 보여줘", layer)
    st = {"pool": [{"table_id": "T_E1", "entered_by": "search", "family": family_key(layer, "T_E1"), "rank": 1}],
          "pool_cut": {"restored": ["T_F"], "pool_cut": False}}
    st["facts"] = {"tables": F.requirement_facts(an, "r1", st["pool"], None, layer, None)["tables"]}
    prev = D.requirement_preview(an, "r1", st, None, layer)
    assert prev["candidates"]["restored"] == ["T_F"]
    assert any(a["tables"] == ["T_F"] for a in prev["decision"]["alternatives"])
