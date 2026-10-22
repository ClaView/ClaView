"""R3 bundle decisions: synthetic catalogs, positive and negative directions, no model calls."""

from __future__ import annotations

import copy
import itertools
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "spec"))
from spec_support import build_layer, itm, table  # noqa: E402

from claview.a.interpret.text import normalize_text  # noqa: E402
from claview.a.select import decision as D  # noqa: E402
from claview.a.select import facts as F  # noqa: E402
from claview.a.select.candidates import family_key  # noqa: E402
from claview.a.select.graph import select_tables  # noqa: E402


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    data = json.loads((Path(__file__).parent / "data" / "select_decision_bundle.json").read_text(encoding="utf-8"))
    tables = [table(t["id"], t["title"], [itm("axis", "value", t["name"], obj_name=data["indicator_axis"])],
                    periods=t["periods"], path=data["path"]) for t in data["tables"]]
    return data, build_layer(tmp_path_factory.mktemp("decision_bundle"), tables)


def analysis(data, *, hinted=True):
    question, name = data["question"], data["indicator"]
    reqs = [{"id": r, "quote": question, "span": [0, len(question)],
             "indicator": {"quote": name, "name": name, "span": [0, len(name)],
                           "fragment_id": None, "path": "exact"},
             "measure_kind": None, "targets": [], "condition_ids": [], "shared_condition_ids": [],
             "ambiguous_condition_ids": [], "status": "ok", "reasons": []} for r in data["requirement_ids"]]
    return {"text": normalize_text(question), "conditions": [], "fragments": [], "terms": [],
            "judgement": {"certain": True, "pairs": [], "requirement_draft": [], "failed_conditions": []},
            "interpretation": {"status": "ok", "common_condition_ids": [], "relations": [],
                               "display_condition_ids": [], "requirements": reqs, "reasons": [],
                               "table_group_hint": [data["requirement_ids"]] if hinted else []}}


def setup_bundle(fixture, reverse=False, current=True):
    data, layer = fixture
    an = analysis(data)
    members = list(data["requirement_ids"])
    ids = [t["id"] for t in data["tables"]]
    if reverse:
        members.reverse()
        ids.reverse()
    selections, states, contexts = {}, {}, {}
    selected = dict(zip(data["requirement_ids"], data["mixed_choice"]))
    for rid in members:
        pool = [{"table_id": t, "entered_by": "search", "family": family_key(layer, t), "rank": k}
                for k, t in enumerate(ids, 1)]
        st = {"pool": pool, "pool_cut": {}, "facts": F.requirement_facts(an, rid, pool, None, layer, None)}
        sel = {"requirement_id": rid, "tables": [{"table_id": selected[rid]}] if current else [],
               "status": "confirm" if current else "no_table", "reasons": [], "signals": {}}
        contexts[rid] = {}
        sel["signals"]["decision_preview"] = D.requirement_preview(an, rid, st, sel, layer, context=contexts[rid])
        selections[rid], states[rid] = sel, st
    return members, selections, states, contexts, layer


def assignments(dec):
    return {tuple((r, tuple(sorted(a["assignment"][r]))) for r in dec["requirement_ids"])
            for a in dec["alternatives"]}


@pytest.mark.parametrize("reverse", [False, True])
def test_bundle_alternatives_are_full_assignments(fixture, reverse):
    args = setup_bundle(fixture, reverse)
    members, sels, _, _, _ = args
    before = copy.deepcopy(sels)
    dec = D.bundle_preview(*args)
    sources = [sels[r]["signals"]["decision_preview"]["decision"]["alternatives"] for r in members]
    expected = {tuple((r, tuple(sorted(a["assignment"][r]))) for r, a in zip(members, combo))
                for combo in itertools.product(*sources)}
    assert assignments(dec) == expected and expected
    assert all(set(a["assignment"]) == set(members) for a in dec["alternatives"])
    assert sels == before
    assert dec["member_no_table"] == {} and all(a["member_no_table"] == {} for a in dec["alternatives"])


@pytest.mark.parametrize("reverse", [False, True])
def test_member_without_alternatives_takes_empty_assignment(fixture, reverse):
    args = setup_bundle(fixture, reverse)
    members, sels, _, _, _ = args
    full = D.bundle_preview(*args)
    empty, others = members[-1], members[:-1]
    sels[empty]["signals"]["decision_preview"]["decision"]["alternatives"] = []
    dec = D.bundle_preview(*args)
    sources = [sels[r]["signals"]["decision_preview"]["decision"]["alternatives"] for r in others]
    expected = {tuple([*((r, tuple(sorted(a["assignment"][r]))) for r, a in zip(others, combo)), (empty, ())])
                for combo in itertools.product(*sources)}
    # The other members keep every alternative; the empty member is never omitted.
    assert expected and assignments(dec) == expected
    assert all(a["assignment"][empty] == [] and set(a["assignment"]) == set(members) for a in dec["alternatives"])
    cause = dec["member_no_table"][empty]
    assert set(dec["member_no_table"]) == {empty} and isinstance(cause, str) and cause
    assert all(a["member_no_table"] == {empty: cause} and not a["complete"]
               and "member_no_table" in a["gaps_by_requirement"][empty] for a in dec["alternatives"])
    assert not dec["today"]["in_alternatives"]  # today's tables for that member are not an alternative
    sels[empty].update(status="no_table", tables=[])
    none_today = D.bundle_preview(*args)
    assert none_today["today"]["in_alternatives"] and none_today["today"]["first"]
    assert none_today["alternatives"][0]["assignment"][empty] == []
    assert full["member_no_table"] == {}


def test_observation_failure_never_stops_selection(fixture, monkeypatch):
    data, layer = fixture
    an = analysis(data)
    normal = select_tables(an, layer)

    def boom(*args, **kwargs):
        raise RuntimeError("D:\\private\\path")

    monkeypatch.setattr(D, "requirement_preview", boom)
    failed = select_tables(an, layer)
    keep = ("requirement_id", "tables", "status", "reasons")
    assert [{k: s[k] for k in keep} for s in failed["selections"]] == [{k: s[k] for k in keep}
                                                                       for s in normal["selections"]]
    assert all(s["signals"]["decision_preview"] == {"error": "RuntimeError"} for s in failed["selections"])
    assert failed["decisions"] == {}


@pytest.mark.parametrize("reverse", [False, True])
def test_different_families_per_member_are_kept(fixture, reverse):
    args = setup_bundle(fixture, reverse)
    dec = D.bundle_preview(*args)
    layer = args[-1]
    sizes = [len({family_key(layer, t) for ts in a["assignment"].values() for t in ts})
             for a in dec["alternatives"]]
    assert any(n > 1 for n in sizes) and any(n == 1 for n in sizes)
    assert not dec["cut"]["decision_cut"]


@pytest.mark.parametrize("reverse", [False, True])
def test_common_family_is_only_an_order_preference(fixture, reverse):
    args = setup_bundle(fixture, reverse, current=False)
    members, sels, states, _, layer = args
    families = list(dict.fromkeys(family_key(layer, t) for t in fixture[0]["mixed_choice"]))
    assert len(families) == 2
    results = []
    for preferred in families:
        for rid in members:
            alts = sels[rid]["signals"]["decision_preview"]["decision"]["alternatives"]
            states[rid]["cover"] = {"alternatives": [
                {"family": fam, "complete": True, "covered": {1}, "score_sum": 100 if fam == preferred else 1}
                for fam in families]}
            # The real common_family function is used, with either family preferred by score.
            assert alts
        dec = D.bundle_preview(*args)
        assert dec["order_preference"]["common_family"] == preferred
        assert all(family_key(layer, t) == preferred for ts in dec["alternatives"][0]["assignment"].values()
                   for t in ts)
        results.append(dec)
    assert assignments(results[0]) == assignments(results[1])
    assert results[0]["alternatives"][0]["assignment"] != results[1]["alternatives"][0]["assignment"]


@pytest.mark.parametrize("reverse", [False, True])
def test_editions_link_within_each_bundle_member(fixture, reverse):
    args = setup_bundle(fixture, reverse)
    sels = args[1]
    dec = D.bundle_preview(*args)
    data = fixture[0]
    assert not dec["cut"]["decision_cut"]
    for rid in dec["requirement_ids"]:
        links = [e for a in dec["alternatives"] for e in a["edition_links"] if e["requirement_id"] == rid]
        assert links and all(e["members"] == data["edition_pair"] for e in links)
        assert all(e["head"] == data["edition_pair"][0] for e in links)
        assert all(data["frequency_table"] not in e["members"] for e in links)
        assert any(data["frequency_table"] in a["assignment"][rid] for a in dec["alternatives"])
    assert any(d["kind"] == "frequency" for d in dec["dimensions"])
    assert all(d["requirement_ids"] for d in dec["dimensions"])
    every = {a["id"] for a in dec["alternatives"]}
    for rid in dec["requirement_ids"]:
        [freq] = [d for d in dec["dimensions"] if d["requirement_ids"] == [rid] and d["kind"] == "frequency"]
        # Every member alternative is used here (no decision cut), so the first one is the member's first.
        member_first = sels[rid]["signals"]["decision_preview"]["decision"]["alternatives"][0]
        base = data["frequency_table"] in member_first["tables"]
        inside = {a["id"] for a in dec["alternatives"]
                  if (data["frequency_table"] in a["assignment"][rid]) != base}
        # Exactly the bundle alternatives whose member differs from that member's first alternative in frequency.
        assert inside and set(freq["alternative_ids"]) == inside
        assert every - inside and not (every - inside) & set(freq["alternative_ids"])


@pytest.mark.parametrize("reverse", [False, True])
def test_decision_cut_is_explicit_and_keeps_full_assignments(fixture, reverse):
    args = setup_bundle(fixture, reverse)
    full = D.bundle_preview(*args)
    cut = D.bundle_preview(*args, cap=1)
    assert cut["cut"]["decision_cut"] and "decision_cut" in cut["gaps"]
    assert cut["combination_count"] == len(full["alternatives"])
    assert cut["omitted"] == len(full["alternatives"]) - 1
    assert cut["today"]["first"] and set(cut["alternatives"][0]["assignment"]) == set(args[0])
    exact = D.bundle_preview(*args, cap=len(full["alternatives"]))
    assert not exact["cut"]["decision_cut"] and assignments(exact) == assignments(full)


@pytest.mark.parametrize("reverse", [False, True])
def test_current_bundle_choice_is_first_only_when_non_refuted(fixture, reverse):
    args = setup_bundle(fixture, reverse)
    dec = D.bundle_preview(*args)
    assert dec["today"]["in_alternatives"] and dec["today"]["first"]
    assert dec["alternatives"][0]["assignment"] == dec["today"]["assignment"]
    members, sels, states, contexts, layer = args
    rid = members[0]
    sels[rid]["tables"] = [{"table_id": fixture[0]["refuted_table"]}]
    sels[rid]["signals"]["decision_preview"] = D.requirement_preview(
        analysis(fixture[0]), rid, states[rid], sels[rid], layer, context=contexts[rid])
    absent = D.bundle_preview(*args)
    assert not absent["today"]["in_alternatives"] and not absent["today"]["first"]
    assert "indicator_absent" in absent["today"]["explained_by"]
    assert assignments(absent) == assignments(dec)  # Current choice never adds or removes an alternative.


@pytest.mark.parametrize("hinted", [False, True])
def test_decisions_return_shape_and_actual_bundle_choice(fixture, hinted):
    data, layer = fixture
    out = select_tables(analysis(data, hinted=hinted), layer)
    assert set(out) == {"selections", "selection_groups", "evidence", "facts", "decisions"}
    assert out["decisions"] == D.decisions_from(out["selections"])
    assert len(out["decisions"]) == (1 if hinted else len(data["requirement_ids"]))
    by_rid = {s["requirement_id"]: s for s in out["selections"]}
    for unit, dec in out["decisions"].items():
        assert dec["unit"] == unit and dec["alternatives"]
        for rid in dec["requirement_ids"]:
            assert by_rid[rid]["signals"]["decision_preview"]["bundle"] is dec
        if hinted:
            assert dec["today"]["first"] and dec["today"]["in_alternatives"]
            assert dec["today"]["assignment"] == {
                rid: [t["table_id"] for t in by_rid[rid]["tables"]] for rid in dec["requirement_ids"]}
        else:
            assert dec is by_rid[dec["requirement_ids"][0]]["signals"]["decision_preview"]["decision"]
    empty = select_tables({**analysis(data), "interpretation": None}, layer)
    assert empty["decisions"] == {}


@pytest.mark.parametrize("weak", [False, True])
def test_bundle_gaps_recomputed_from_current_facts_not_history(fixture, weak):
    args = setup_bundle(fixture)
    members, sels, _, contexts, _ = args
    for rid in members:
        sels[rid]["reasons"] = ["pruned_by_score", "d_resolved", "group_family"]
        for fact in contexts[rid]["facts"].values():
            fact.setdefault("indicator", {})["weak"] = weak
    dec = D.bundle_preview(*args)
    assert all(("indicator_weak" in a["gaps"]) == weak for a in dec["alternatives"])
    assert all(not set(a["gaps"]) & {"pruned_by_score", "d_resolved", "group_family"} for a in dec["alternatives"])


@pytest.mark.parametrize("partial", [False, True])
def test_partial_member_assignments_stay_beside_complete_ones(fixture, partial):
    args = setup_bundle(fixture)
    rid = args[0][0]
    source = args[1][rid]["signals"]["decision_preview"]["decision"]
    assert len(source["alternatives"]) > 1
    member = source["alternatives"][-1]
    member["complete"] = not partial
    dec = D.bundle_preview(*args)
    matching = [a for a in dec["alternatives"] if a["member_alternative_ids"][rid] == member["id"]]
    assert matching and all(("partial_cover" in a["gaps"]) == partial for a in matching)
    assert any(a["complete"] for a in dec["alternatives"])


def test_cut_member_assignment_keeps_cover_cut_in_the_bundle(fixture):
    args = setup_bundle(fixture)
    rid = args[0][0]
    source = args[1][rid]["signals"]["decision_preview"]["decision"]
    member = source["alternatives"][-1]
    # As S5 builds a unit listed under cut_units: not complete, gap cover_cut, never partial_cover.
    member["complete"] = False
    member["gaps"] = [g for g in member["gaps"] if g != "partial_cover"] + ["cover_cut"]
    dec = D.bundle_preview(*args)
    matching = [a for a in dec["alternatives"] if a["member_alternative_ids"][rid] == member["id"]]
    assert matching
    for a in matching:
        assert "cover_cut" in a["gaps_by_requirement"][rid] and "partial_cover" not in a["gaps_by_requirement"][rid]
        assert not a["complete"] and a["label"] == "possible"
    others = [a for a in dec["alternatives"] if a["member_alternative_ids"][rid] != member["id"]]
    assert others and all("cover_cut" not in a["gaps_by_requirement"][rid] for a in others)


def test_restoration_cap_can_restore_every_restorable_table(fixture, monkeypatch):
    data, layer = fixture
    an = analysis(data)
    ids = [t["id"] for t in data["tables"]]
    monkeypatch.setattr(F, "RESTORE_CAP", None)
    all_candidates = F.restoration(an, data["requirement_ids"][0], ids, layer)
    assert all_candidates["restorable"] > 1
    assert len(all_candidates["restored"]) == all_candidates["restorable"]
    assert not all_candidates["restoration_cut"]
    monkeypatch.setattr(F, "RESTORE_CAP", 1)
    limited = F.restoration(an, data["requirement_ids"][0], ids, layer)
    assert limited["restoration_cut"] and len(limited["restored"]) == 1
    assert set(limited["restored"]) < set(all_candidates["restored"])
