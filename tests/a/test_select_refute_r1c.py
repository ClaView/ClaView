"""Plan 11 R1c (observation only): stronger refutation on made-up tables, both directions. A title (bracket) basis
conflict refutes only when the requested value occurs nowhere on the table; a name-only failed source leaves the
conditions of its fragment settled; a table judged against on a condition is never ``proven``; the indicator inside
other names only (``indicator_mention_only``) refutes only on a complete surface; the indicator relation is a
decision dimension. Table IDs, names and words are made up; basis words come from the condition dictionary."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "spec"))
from spec_support import build_layer, itm, table  # noqa: E402

from claview.a.interpret.condition_rules import BASIS_AXES  # noqa: E402
from claview.a.select import decision as D  # noqa: E402
from claview.a.select import facts as F  # noqa: E402
from claview.a.select import refute as R  # noqa: E402
from claview.a.select.cover import requested_cells  # noqa: E402
from claview.a.select.evidence import check_evidence  # noqa: E402
from claview.a.select.needs import requirement_needs  # noqa: E402
from claview.a.select.tables import selection_catalog  # noqa: E402

IND = "BSI코드별"
AXIS = "price"
V1, V2 = list(BASIS_AXES[AXIS])[:2]
NAME = "가상물가지수"
VERSION = "test"


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    tables = [
        table("T_B1", f"{NAME}({V1})", [itm("A", "1", NAME, obj_name=IND)]),
        table("T_B2", f"{NAME}({V2})", [itm("A", "1", NAME, obj_name=IND)]),
        table("T_BOTH", f"{NAME}({V2})", [itm("A", "1", NAME, obj_name=IND),
                                         itm("G", "1", V1, obj_name="가상구분", order=2)]),
        table("T_PLAIN", "가상무기준표", [itm("A", "1", NAME, obj_name=IND)]),
        table("T_INSIDE", "가상동향표", [itm("A", "1", f"{NAME}가상수출입액", obj_name=IND)]),
        table("T_PART", "가상물가표", [itm("A", "1", "가상물가", obj_name=IND)]),
        table("T_INSIDE_ORPHAN", "가상동향표2", [itm("A", "1", f"{NAME}가상수출입액", "zz", obj_name=IND)]),
        table("T_SUBJ", NAME, [itm("A", "1", NAME, obj_name=IND)]),
        table("T_COMP", "가상총괄표", [itm("A", "1", "가상총괄", obj_name=IND),
                                   itm("A", "2", NAME, "1", obj_name=IND)]),
    ]
    return build_layer(tmp_path_factory.mktemp("select_refute_r1c"), tables)


def _analysis(conditions=(), own=(), failed=(), path="exact", name=NAME):
    frags = [{"id": "g1", "quote": name, "span": [0, 12], "role": "core", "protected_by": None, "phrase": "item"}]
    terms = [{"fragment_id": "g1", "name": name, "type": "indicator", "table_ids": [], "obj_id": None,
              "obj_name": None, "path": path, "status": "approved", "score": None}]
    req = {"id": "r1", "quote": name, "span": [0, 60], "targets": [], "condition_ids": list(own),
           "shared_condition_ids": [], "ambiguous_condition_ids": [], "status": "ok", "reasons": [],
           "indicator": {"quote": name, "span": [0, 6], "fragment_id": "g1", "name": name, "path": path},
           "breakdown": []}
    return {"text": None, "conditions": list(conditions), "fragments": frags, "terms": terms,
            "judgement": {"requirement_draft": [], "failed_sources": list(failed), "negated": []},
            "interpretation": {"requirements": [req]}}


def _basis(cid="B1", value=V1, span=(7, 9)):
    return {"id": cid, "kind": "basis", "quote": value, "value": value, "span": list(span),
            "attrs": {"axis": AXIS, "value": value, "polarity": "+"}}


def _facts(layer, an, tids):
    doc = check_evidence(an, list(tids), layer, None, requirement_ids=["r1"])
    pool = [{"table_id": t, "entered_by": "search"} for t in tids]
    return F.requirement_facts(an, "r1", pool, doc, layer, None)["tables"]


def _tables(layer, an, tids):
    views = selection_catalog(layer).views
    return R.refute_tables(requirement_needs(an, "r1"), _facts(layer, an, tids), {t: views[t] for t in tids},
                           VERSION)


def _gaps(layer, an, tid):
    views = {tid: selection_catalog(layer).views[tid]}
    nd = requirement_needs(an, "r1")
    return D.assignment_gaps(nd, requested_cells(an, "r1", views, None), [tid], _facts(layer, an, [tid]), True)


BASIS = dict(conditions=[_basis()], own=["B1"])


# ---------------------------------------------------------------- title basis conflict


def test_title_basis_conflict_refutes_with_a_title_certificate(layer):
    refs = _tables(layer, _analysis(**BASIS), ["T_B1", "T_B2"])
    assert "T_B1" not in refs
    [cert] = refs["T_B2"]
    assert cert["code"] == "condition_conflict" and R.is_certificate(cert)
    assert cert["opposite_fact"]["source"] == "title" and cert["opposite_fact"]["opposite_in_title"]
    row = next(r for r in _facts(layer, _analysis(**BASIS), ["T_B2"])["T_B2"]["conditions"] if r["condition_id"] == "B1")
    assert row["basis"]["title_conflict"] and row["basis"]["requested_in"] == []


def test_both_values_on_the_table_do_not_refute_and_are_not_proven(layer):
    an = _analysis(**BASIS)
    f = _facts(layer, an, ["T_BOTH"])["T_BOTH"]
    row = next(r for r in f["conditions"] if r["condition_id"] == "B1")
    assert row["status"] == "conflict" and row["basis"]["requested_in"] == ["value"]
    assert not row["basis"]["title_conflict"]
    assert "T_BOTH" not in _tables(layer, an, ["T_BOTH"])
    assert "unproven_condition" in _gaps(layer, an, "T_BOTH")


def test_title_without_basis_word_is_unproven_not_refuted(layer):
    an = _analysis(**BASIS)
    row = next(r for r in _facts(layer, an, ["T_PLAIN"])["T_PLAIN"]["conditions"] if r["condition_id"] == "B1")
    assert row["status"] == "unknown" and row["basis"]["title_words"] is False
    assert "T_PLAIN" not in _tables(layer, an, ["T_PLAIN"])
    assert "unproven_condition" in _gaps(layer, an, "T_PLAIN")
    assert "unproven_condition" not in _gaps(layer, an, "T_B1")  # the requested value in the title: proven


def test_conflicting_table_is_never_proven(layer):
    unsure = _analysis(**BASIS, failed=[{"condition": "ambiguous_word", "requirement_ids": [], "detail": "x",
                                         "condition_ids": ["B1"], "fragment_ids": []}])
    assert "T_B2" not in _tables(layer, unsure, ["T_B2"])  # unsettled: no refutation ...
    assert "unproven_condition" in _gaps(layer, unsure, "T_B2")  # ... and never proven


# ---------------------------------------------------------------- certainty: name-only failed sources


def test_name_only_source_keeps_the_conditions_of_its_fragment_settled(layer):
    name_src = {"condition": "inexact_term", "requirement_ids": ["r1"], "detail": "mixed_type:indicator",
                "condition_ids": [], "fragment_ids": ["g1"]}
    nd = requirement_needs(_analysis(**BASIS, failed=[name_src]), "r1")
    assert not nd["evidence"]["indicator"]["settled"]
    assert next(c for c in nd["evidence"]["conditions"] if c["condition_id"] == "B1")["settled"]
    assert "T_B2" in _tables(layer, _analysis(**BASIS, failed=[name_src]), ["T_B2"])
    other = {**name_src, "condition": "scope_ambiguous", "detail": "group_scope", "requirement_ids": []}
    nd = requirement_needs(_analysis(**BASIS, failed=[other]), "r1")
    assert not next(c for c in nd["evidence"]["conditions"] if c["condition_id"] == "B1")["settled"]


# ---------------------------------------------------------------- indicator_mention_only (R4b)


def test_mention_only_refutes_only_on_a_complete_surface(layer, monkeypatch):
    on = {**R.switches(), "indicator_mention_only": True}
    monkeypatch.setattr(R, "switches", lambda: on)
    f = _facts(layer, _analysis(), ["T_INSIDE", "T_PART", "T_INSIDE_ORPHAN"])
    assert f["T_INSIDE"]["indicator"]["mention_only"] is True
    assert f["T_PART"]["indicator"]["mention_only"] is False  # a part of the name, not the name inside another
    refs = _tables(layer, _analysis(), ["T_INSIDE", "T_PART", "T_INSIDE_ORPHAN", "T_SUBJ"])
    assert {c["code"] for c in refs["T_INSIDE"]} == {"indicator_mention_only"}
    assert all(R.is_certificate(c) for c in refs["T_INSIDE"])
    assert "T_PART" not in refs and "T_SUBJ" not in refs
    assert f["T_INSIDE_ORPHAN"]["indicator"]["mention_only"] is True
    assert "T_INSIDE_ORPHAN" not in refs  # surface incomplete: indicator_weak stays
    assert "T_INSIDE" not in _tables(layer, _analysis(path="lexical"), ["T_INSIDE"])  # name not mapped for sure


def test_mention_only_is_off_by_default(layer):
    assert R.on("indicator_mention_only") is False  # off until decided (see decision_policy refutations note)
    assert "T_INSIDE" not in _tables(layer, _analysis(), ["T_INSIDE"])


# ---------------------------------------------------------------- relation dimension (R4c, record only)


def test_relation_dimension_is_recorded(layer):
    f = _facts(layer, _analysis(), ["T_SUBJ", "T_COMP"])
    assert D.indicator_relation(f, ["T_SUBJ"]) == "subject"
    assert D.indicator_relation(f, ["T_COMP"]) == "component"
    assert D.indicator_relation(f, ["T_SUBJ", "T_COMP"]) == "mixed"
    assert D.indicator_relation({}, ["T_SUBJ"]) == "undetermined"
    views = selection_catalog(layer).views
    nd = requirement_needs(_analysis(), "r1")
    alts = [{"id": "a1", "tables": ["T_SUBJ"], "indicator_relation": "subject"},
            {"id": "a2", "tables": ["T_COMP"], "indicator_relation": "component"}]
    fam = {"T_SUBJ": "f", "T_COMP": "f"}
    [rel] = [d for d in D._dimensions(alts, views, fam, nd) if d["kind"] == "relation"]
    assert rel["values"] == ["subject", "component"] and rel["alternative_ids"] == ["a2"]
    same = [{**alts[0]}, {**alts[1], "indicator_relation": "subject"}]
    assert not [d for d in D._dimensions(same, views, fam, nd) if d["kind"] == "relation"]
