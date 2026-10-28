"""Plan 11 R1b: S1 needs (select/needs.py) and S4 refutation (select/refute.py) on made-up tables, every code in both
directions, and the corrected fact producers (C6, K1, K4, C4). Observation only: nothing here changes a selection.
Table IDs, names and words are made up; basis words are read from the condition dictionary, never written here."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "spec"))
from spec_support import build_layer, itm, table  # noqa: E402

from claview_a_legacy.catalog.facets import facet_of  # noqa: E402
from claview_a_legacy.interpret.condition_rules import BASIS_AXES  # noqa: E402
from claview_a_legacy.select import facts as F  # noqa: E402
from claview_a_legacy.select import refute as R  # noqa: E402
from claview_a_legacy.select.cover import all_cells, requested_cells  # noqa: E402
from claview_a_legacy.select.evidence import check_evidence  # noqa: E402
from claview_a_legacy.select.needs import requirement_needs, settled  # noqa: E402
from claview_a_legacy.select.tables import selection_catalog  # noqa: E402

IND = "BSI코드별"  # read as an indicator axis by the synthetic layer-1 sheet
TGT = "지역코드별"  # ... as a target axis
AXIS = "price"
V1, V2 = list(BASIS_AXES[AXIS])[:2]  # two values of one basis axis
AXIS_B = "seasonal"
W1 = list(BASIS_AXES[AXIS_B])[0]
NAME = "가상물가지수"
VERSION = "test"


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    tables = [
        table("T_IND", NAME, [itm("A", "1", NAME, obj_name=IND)]),
        table("T_NONE", "가상임금표", [itm("A", "1", "가상임금", obj_name=IND)]),
        table("T_PART", "가상물가표", [itm("A", "1", "가상물가", obj_name=IND)]),
        table("T_TY", "가상지역표", [itm("A", "1", NAME, obj_name=IND), itm("R", "1", "가상서울", obj_name=TGT, order=2)]),
        table("T_TN", "가상지역표2", [itm("A", "1", NAME, obj_name=IND), itm("R", "1", "가상부산", obj_name=TGT, order=2)]),
        table("T_B1", f"{NAME}({V1})", [itm("A", "1", NAME, obj_name=IND)]),
        table("T_B2", f"{NAME}({V2})", [itm("A", "1", NAME, obj_name=IND)]),
        table("T_NEG_ONLY", "가상기준표", [itm("A", "1", NAME, obj_name=IND), itm("G", "1", V1, obj_name="가상구분", order=2)]),
        table("T_NEG_BOTH", "가상기준표2", [itm("A", "1", NAME, obj_name=IND), itm("G", "1", V1, obj_name="가상구분", order=2),
                                         itm("G", "2", V2, obj_name="가상구분", order=2)]),
        table("T_NEG_TITLE", f"가상기준표3({V1})", [itm("A", "1", NAME, obj_name=IND)]),
        table("T_SPLIT", "가상갈래표", [itm("A", "1", NAME, obj_name=IND),
                                    itm("G", "1", "가상갑", obj_name="가상구분", order=2),
                                    itm("G", "2", V1, "1", obj_name="가상구분", order=2),
                                    itm("G", "3", "가상을", obj_name="가상구분", order=2),
                                    itm("G", "4", W1, "3", obj_name="가상구분", order=2)]),
        table("T_CHAIN", "가상사슬표", [itm("A", "1", NAME, obj_name=IND),
                                    itm("G", "1", V1, obj_name="가상구분", order=2),
                                    itm("G", "2", W1, "1", obj_name="가상구분", order=2)]),
        table("T_NEGWORD", "가상동향표", [itm("A", "1", "가상물가수입", obj_name=IND)]),
        table("T_OTHERWORD", "가상동향표2", [itm("A", "1", "가상물가동향", obj_name=IND)]),
        table("T_Q", "가상분기표", [itm("A", "1", NAME, obj_name=IND)], periods=(("분기", "2000Q1", "2026Q2"),)),
        table("T_UNIT", "가상단위표", [itm("A", "1", NAME, obj_name=IND)], units=("%",)),
        table("T_ORPHAN", "가상고아표", [itm("A", "1", "가상임금", "zz", obj_name=IND)]),
    ]
    return build_layer(tmp_path_factory.mktemp("select_refute"), tables)


def _analysis(name: str | None = NAME, conditions=(), own=(), targets=(), failed=(), negated=(), path="exact"):
    frags = [{"id": "g1", "quote": name or "", "span": [0, 6], "role": "core", "protected_by": None,
              "phrase": "item"}]
    terms = [{"fragment_id": "g1", "name": name, "type": "indicator", "table_ids": [], "obj_id": None,
              "obj_name": None, "path": path, "status": "approved", "score": None}] if name else []
    tg = []
    for k, t in enumerate(targets, 2):
        frags.append({"id": f"g{k}", "quote": t, "span": [10 * k, 10 * k + 4], "role": "core", "protected_by": None,
                      "phrase": "item"})
        terms.append({"fragment_id": f"g{k}", "name": t, "type": "target", "table_ids": [], "obj_id": "R",
                      "obj_name": TGT, "path": "exact", "status": "approved", "score": None})
        tg.append({"quote": t, "span": [10 * k, 10 * k + 4], "fragment_id": f"g{k}", "name": t})
    req = {"id": "r1", "quote": name or "", "span": [0, 60], "targets": tg, "condition_ids": list(own),
           "shared_condition_ids": [], "ambiguous_condition_ids": [], "status": "ok", "reasons": [],
           "indicator": {"quote": name, "span": [0, 6], "fragment_id": "g1", "name": name, "path": path}
           if name else None, "breakdown": []}
    return {"text": None, "conditions": list(conditions), "fragments": frags, "terms": terms,
            "judgement": {"requirement_draft": [], "failed_sources": list(failed), "negated": list(negated)},
            "interpretation": {"requirements": [req]}}


def _basis(cid: str, axis: str, value: str, polarity: str = "+") -> dict:
    return {"id": cid, "kind": "basis", "quote": value, "value": value,
            "attrs": {"axis": axis, "value": value, "polarity": polarity}}


def _facts(layer, analysis, tids) -> dict:
    doc = check_evidence(analysis, list(tids), layer, None, requirement_ids=["r1"])
    pool = [{"table_id": t, "entered_by": "search"} for t in tids]
    return F.requirement_facts(analysis, "r1", pool, doc, layer, None)["tables"]


def _tables(layer, analysis, tids) -> dict:
    nd = requirement_needs(analysis, "r1")
    views = selection_catalog(layer).views
    return R.refute_tables(nd, _facts(layer, analysis, tids), {t: views[t] for t in tids}, VERSION)


def _cells(layer, analysis, tids):
    nd = requirement_needs(analysis, "r1")
    views = {t: selection_catalog(layer).views[t] for t in tids}
    req = requested_cells(analysis, "r1", views, None)
    cells = all_cells(req)
    return R.refute_cells(nd, _facts(layer, analysis, tids), views, req, cells, list(tids), VERSION), cells


def _codes(certs: list[dict]) -> set[str]:
    return {c["code"] for c in certs}


# ---------------------------------------------------------------- S1 needs: certainty per (requirement, ID)


def test_needs_certainty_by_failed_sources():
    clean = requirement_needs(_analysis(), "r1")
    assert settled(clean) and clean["evidence"]["indicator"]["settled"] and clean["evidence"]["indicator"]["mapped"]
    whole = requirement_needs(_analysis(failed=[{"condition": "leftover_word", "requirement_ids": [],
                                                 "detail": "leftover"}]), "r1")
    assert not settled(whole) and not whole["evidence"]["indicator"]["settled"]
    other = requirement_needs(_analysis(failed=[{"condition": "inexact_term", "requirement_ids": ["r1"],
                                                 "detail": "x", "fragment_ids": ["g9"]}]), "r1")
    assert other["evidence"]["indicator"]["settled"]  # another fragment only
    mine = requirement_needs(_analysis(failed=[{"condition": "inexact_term", "requirement_ids": ["r1"],
                                                "detail": "x", "fragment_ids": ["g1"]}]), "r1")
    assert not mine["evidence"]["indicator"]["settled"]
    lexical = requirement_needs(_analysis(path="lexical"), "r1")
    assert lexical["evidence"]["indicator"]["settled"] and not lexical["evidence"]["indicator"]["mapped"]


def test_identity_key_holds_the_period_meaning():
    p = {"id": "P1", "kind": "period", "quote": "x", "attrs": {"form": "point", "start_expr": {"year": "2010"}}}
    q = copy.deepcopy(p)
    q["attrs"]["start_expr"]["year"] = "2011"
    a = F.requirement_identity(_analysis(conditions=[p], own=["P1"]), "r1")
    b = F.requirement_identity(_analysis(conditions=[q], own=["P1"]), "r1")
    assert a["key"] != b["key"]
    assert a["key"] == F.requirement_identity(_analysis(conditions=[copy.deepcopy(p)], own=["P1"]), "r1")["key"]


# ---------------------------------------------------------------- indicator_absent (K1, C2)


def test_indicator_absent_both_ways(layer):
    refs = _tables(layer, _analysis(), ["T_IND", "T_NONE"])
    assert _codes(refs["T_NONE"]) == {"indicator_absent"} and "T_IND" not in refs


def test_partial_only_indicator_is_not_refuted(layer):
    f = _facts(layer, _analysis(), ["T_PART"])["T_PART"]
    assert f["indicator"]["absent"] is False and f["indicator"]["weak"] is True
    assert "T_PART" not in _tables(layer, _analysis(), ["T_PART"])


def test_indicator_absent_needs_a_settled_mapped_name_and_a_complete_surface(layer):
    q = [{"condition": "leftover_word", "requirement_ids": [], "detail": "leftover"}]
    assert "T_NONE" not in _tables(layer, _analysis(failed=q), ["T_NONE"])  # not settled
    assert "T_NONE" not in _tables(layer, _analysis(path="lexical"), ["T_NONE"])  # name not mapped for sure
    f = _facts(layer, _analysis(), ["T_ORPHAN"])["T_ORPHAN"]
    assert not f["surface"]["indicator"]["complete"] and "hierarchy_anomaly" in f["surface"]["indicator"]["why"]
    assert "T_ORPHAN" not in _tables(layer, _analysis(), ["T_ORPHAN"])  # surface incomplete


# ---------------------------------------------------------------- target_absent (cell)


def test_target_absent_is_a_cell_refutation(layer):
    refs, cells = _cells(layer, _analysis(targets=["가상서울"]), ["T_TY", "T_TN"])
    assert "T_TY" not in refs
    assert {c["code"] for cs in refs["T_TN"].values() for c in cs} == {"target_absent"}
    assert all(c["scope"] == "cell" for cs in refs["T_TN"].values() for c in cs)


def test_target_occurring_in_part_is_not_refuted(layer):
    refs, _ = _cells(layer, _analysis(targets=["가상서울시"]), ["T_TY"])  # "가상서울" is a value: a part occurs
    assert "T_TY" not in refs
    f = _facts(layer, _analysis(targets=["가상서울시"]), ["T_TY"])["T_TY"]
    assert f["targets"] == [{"name": "가상서울시", "present": False, "partial": True}]


def test_unnamed_target_is_not_refuted(layer):
    an = _analysis(targets=["가상서울"])
    an["interpretation"]["requirements"][0]["targets"][0]["name"] = None
    refs, _ = _cells(layer, an, ["T_TN"])
    assert "T_TN" not in refs


# ---------------------------------------------------------------- lower_item_absent (cell)


def test_lower_item_absent_both_ways(layer):
    an = _analysis(conditions=[{"id": "K1", "kind": "breakdown", "quote": "가상서울", "value": "가상서울",
                                "attrs": {"text": "가상서울"}}], own=["K1"])
    nd = requirement_needs(an, "r1")
    nd["evidence"]["lower_items"] = [{"text": "가상서울", "word": "가상서울", "name": "가상서울", "condition_id": "K1",
                                      "fragment_id": "g1", "named": True, "settled": True}]
    views = {t: selection_catalog(layer).views[t] for t in ("T_TY", "T_TN")}
    req = requested_cells(an, "r1", views, None)
    cells = all_cells(req)
    facts = _facts(layer, an, ["T_TY", "T_TN"])
    facts["T_TY"]["lower_items"] = [{"text": "가상서울", "word": "가상서울", "found": True, "under": None,
                                     "partial": True}]
    facts["T_TN"]["lower_items"] = [{"text": "가상서울", "word": "가상서울", "found": False, "under": None,
                                     "partial": False}]
    refs = R.refute_cells(nd, facts, views, req, cells, ["T_TY", "T_TN"], VERSION, {"K1": "가상서울"})
    assert "T_TY" not in refs
    assert {c["code"] for cs in refs["T_TN"].values() for c in cs} == {"lower_item_absent"}
    facts["T_TN"]["lower_items"][0]["partial"] = True  # part of the name occurs: no absence
    assert "T_TN" not in R.refute_cells(nd, facts, views, req, cells, ["T_TN"], VERSION, {"K1": "가상서울"})
    rows = F.lower_item_facts([{"name": "breakdown", "detail": {"rows": [{"text": "x", "word": "가상서울",
                                                                          "found": False}]}}],
                              facet_of(layer, "T_TY"))
    assert rows[0]["partial"] is True


# ---------------------------------------------------------------- condition_conflict


def test_condition_conflict_basis_both_ways(layer):
    an = _analysis(conditions=[_basis("B1", AXIS, V1)], own=["B1"])
    refs = _tables(layer, an, ["T_B1", "T_B2"])
    assert "T_B1" not in refs and _codes(refs["T_B2"]) == {"condition_conflict"}
    unsettled_ = _analysis(conditions=[_basis("B1", AXIS, V1)], own=["B1"],
                           failed=[{"condition": "ambiguous_word", "requirement_ids": [], "detail": "x",
                                    "condition_ids": ["B1"]}])
    assert "T_B2" not in _tables(layer, unsettled_, ["T_B2"])


def test_condition_conflict_split_axis_is_per_cell(layer):
    an = _analysis(conditions=[_basis("B1", AXIS, V1), _basis("B2", AXIS, V2)], own=["B1", "B2"])
    refs, cells = _cells(layer, an, ["T_B1", "T_B2"])
    assert len(cells) == 2
    assert len(refs["T_B1"]) == 1 and len(refs["T_B2"]) == 1  # each table loses the cell of the other value
    assert {c["code"] for t in refs.values() for cs in t.values() for c in cs} == {"condition_conflict"}


def test_condition_conflict_frequency_period_unit(layer):
    freq = _analysis(conditions=[{"id": "F1", "kind": "frequency", "quote": "x", "value": "Q", "attrs": {"value": "Q"}}],
                     own=["F1"])
    refs = _tables(layer, freq, ["T_IND", "T_Q"])
    assert "T_Q" not in refs and _codes(refs["T_IND"]) == {"condition_conflict"}

    def period(year):
        return _analysis(conditions=[{"id": "P1", "kind": "period", "quote": year,
                                      "attrs": {"form": "point", "start_expr": {"year": year}}}], own=["P1"])

    assert _codes(_tables(layer, period("1990"), ["T_IND"])["T_IND"]) == {"condition_conflict"}
    assert "T_IND" not in _tables(layer, period("2010"), ["T_IND"])
    unit = _analysis(conditions=[{"id": "U1", "kind": "unit", "quote": "x", "value": "억원", "attrs": {"value": "억원"}}],
                     own=["U1"])
    assert _codes(_tables(layer, unit, ["T_UNIT"])["T_UNIT"]) == {"condition_conflict"}
    assert "T_IND" not in _tables(layer, unit, ["T_IND"])  # no unit: unknown, never a conflict


# ---------------------------------------------------------------- forbidden_only (C6)


def test_forbidden_only_both_ways(layer):
    an = _analysis(conditions=[_basis("N1", AXIS, V1, "-")], own=["N1"])
    refs = _tables(layer, an, ["T_NEG_ONLY", "T_NEG_BOTH"])
    assert _codes(refs["T_NEG_ONLY"]) == {"forbidden_only"}
    assert "T_NEG_BOTH" not in refs or "forbidden_only" not in _codes(refs["T_NEG_BOTH"])


def test_title_only_negation_is_not_forbidden_only(layer):
    an = _analysis(conditions=[_basis("N1", AXIS, V1, "-")], own=["N1"])
    f = _facts(layer, an, ["T_NEG_TITLE"])["T_NEG_TITLE"]
    row = next(r for r in f["conditions"] if r["condition_id"] == "N1")
    assert row["negation"] == "forbidden_present"
    assert "forbidden_only" not in _codes(_tables(layer, an, ["T_NEG_TITLE"]).get("T_NEG_TITLE", []))


# ---------------------------------------------------------------- negated_name_only


def test_negated_name_only_both_ways(layer):
    an = _analysis(name="가상물가", negated=["가상물가수입"])
    refs = _tables(layer, an, ["T_NEGWORD", "T_OTHERWORD"])
    assert _codes(refs["T_NEGWORD"]) == {"negated_name_only"}
    assert "T_OTHERWORD" not in refs
    assert "T_NEGWORD" not in _tables(layer, _analysis(name="가상물가"), ["T_NEGWORD"])  # nothing negated


# ---------------------------------------------------------------- cells_impossible (K4)


def test_same_axis_basis_branches_are_impossible(layer):
    an = _analysis(conditions=[_basis("B1", AXIS, V1), _basis("B2", AXIS_B, W1)], own=["B1", "B2"])
    split = _facts(layer, an, ["T_SPLIT"])["T_SPLIT"]
    pair = next(p for p in split["cells"]["pairs"] if set(p["parts"]) == {"basis:B1", "basis:B2"})
    assert pair["verdict"] == "impossible" and split["cells"]["combinable"] == "impossible"
    chain = _facts(layer, an, ["T_CHAIN"])["T_CHAIN"]
    assert next(p for p in chain["cells"]["pairs"] if set(p["parts"]) == {"basis:B1", "basis:B2"})["verdict"] \
        == "possible"
    refs, _ = _cells(layer, an, ["T_SPLIT", "T_CHAIN"])
    assert "T_CHAIN" not in refs
    assert {c["code"] for cs in refs["T_SPLIT"].values() for c in cs} == {"cells_impossible"}


def test_split_values_of_one_axis_are_never_paired(layer):
    an = _analysis(conditions=[_basis("B1", AXIS, V1), _basis("B2", AXIS, V2)], own=["B1", "B2"])
    f = _facts(layer, an, ["T_NEG_BOTH"])["T_NEG_BOTH"]
    assert not any(set(p["parts"]) == {"basis:B1", "basis:B2"} for p in f["cells"]["pairs"])


# ---------------------------------------------------------------- certificates (C3)


def test_every_refutation_has_a_certificate(layer):
    an = _analysis(conditions=[_basis("B1", AXIS, V1)], own=["B1"], targets=["가상서울"])
    tids = ["T_IND", "T_NONE", "T_B2", "T_TN", "T_TY"]
    tabs = _tables(layer, an, tids)
    cells, _ = _cells(layer, an, [t for t in tids if t not in tabs])
    certs = [c for cs in tabs.values() for c in cs] + [c for t in cells.values() for cs in t.values() for c in cs]
    assert certs and all(R.is_certificate(c) for c in certs)
    for c in certs:
        assert c["requirement_key"] == F.requirement_identity(an, "r1")["key"] and c["catalog_version"] == VERSION
    broken = dict(certs[0])
    del broken["opposite_fact"]
    assert not R.is_certificate(broken)
    assert not R.is_certificate({**certs[0], "scope": "cell", "cell": None})


def test_switched_off_code_removes_nothing(layer, monkeypatch):
    monkeypatch.setattr(R, "switches", lambda: {"indicator_absent": False})
    assert "T_NONE" not in _tables(layer, _analysis(), ["T_NONE"])


# ---------------------------------------------------------------- failed sources carry the IDs (record only)


def test_failed_sources_carry_ids_and_keep_the_verdict(layer):
    from claview_a_legacy.interpret.pipeline import interpret_question

    an = interpret_question("가상물가지수 가상임금 보여줘 가상없는말", layer)
    rows = an["judgement"].get("failed_sources") or []
    assert rows and all({"condition_ids", "fragment_ids"} <= set(r) for r in rows)
    assert {r["condition"] for r in rows} == set(an["judgement"]["failed_conditions"])
