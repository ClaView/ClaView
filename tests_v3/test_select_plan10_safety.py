"""Plan 10 fixes of 10/20 02:28 on made-up data, each in both directions: a table name glued to a value of that
table (interpretation), the indicator check by the catalog name when the written words hold none (evidence), and
the safety rows (an indicator no candidate was checked against; an undecided interpretation left partly out of the
catalog). Table IDs and names are made up."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "spec"))
from spec_support import build_layer, itm, table  # noqa: E402

from claview_v3.agent import question_route as qr  # noqa: E402
from claview_v3.interpret.conditions import _table_then_value  # noqa: E402
from claview_v3.interpret.pipeline import interpret_question  # noqa: E402
from claview_v3.interpret.text import normalize_text  # noqa: E402
from claview_v3.select.evidence import indicator_phrase  # noqa: E402
from claview_v3.select.graph import _indicator_unchecked  # noqa: E402
from claview_v3.select.tables import selection_catalog  # noqa: E402


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    tables = [
        table("T_A", "가상무역수지(유형별)", [itm("A", "1", "가상연구개발 및 가상저작권", obj_name="항목"),
                                         itm("A", "2", "가상운송서비스", obj_name="항목")]),
        table("T_B", "가상고정자본", [itm("A", "1", "가상설비투자", obj_name="항목")]),
    ]
    return build_layer(tmp_path_factory.mktemp("plan10_safety"), tables)


# ---------------------------------------------------------------- interpretation: table name + glued value


def test_table_name_with_its_value_glued_reads_as_name_and_value(layer):
    raw = "가상무역수지가상연구개발 및 가상저작권보여줘"
    pairs = _table_then_value(layer, raw, [])
    assert [(h["quote"], v["quote"]) for h, v in pairs] == [("가상무역수지", "가상연구개발 및 가상저작권")]
    an = interpret_question(raw, layer)
    (req,) = an["interpretation"]["requirements"]
    assert req["indicator"]["name"] == "가상연구개발 및 가상저작권"


def test_a_value_of_another_table_is_not_cut_off_a_table_name(layer):
    assert _table_then_value(layer, "가상고정자본가상운송서비스보여줘", []) == []
    assert _table_then_value(layer, "가상무역수지보여줘", []) == []


# ---------------------------------------------------------------- evidence: the catalog name stands in


def _an(raw: str, name: str | None) -> dict:
    ind = {"quote": raw, "span": [0, len(raw)], "fragment_id": None, "name": name, "path": "exact"} if name else None
    req = {"id": "r1", "quote": raw, "span": [0, len(raw)], "indicator": ind, "targets": [], "condition_ids": [],
           "shared_condition_ids": [], "ambiguous_condition_ids": [], "status": "ok", "reasons": []}
    return {"text": normalize_text(raw), "conditions": [], "fragments": [], "terms": [],
            "interpretation": {"requirements": [req]}}


def test_indicator_check_reads_the_catalog_name_when_the_words_hold_none(layer):
    measure = selection_catalog(layer).measure()
    raw = "갑을병정무술기경보여줘"  # glued words no catalog word starts
    got = indicator_phrase(_an(raw, "가상설비투자"), "r1", measure)
    assert got is not None and got["origin"] == "catalog_name"
    assert indicator_phrase(_an(raw, None), "r1", measure) is None
    written = indicator_phrase(_an("가상설비투자 보여줘", "가상설비투자"), "r1", measure)
    assert written is not None and written["origin"] == "rule"


# ---------------------------------------------------------------- safety 1a: indicator unchecked


def _ev(available: list[bool], exact_on: str | None = None) -> dict:
    out = []
    for k, ok in enumerate(available):
        t = f"T{k}"
        rows = [{"condition_id": None, "polarity": "+", "match_method": "exact"}] if t == exact_on else []
        out.append({"requirement_id": "r1", "table_id": t, "checks": [
            {"name": "matched_items", "detail": {"rows": rows}},
            {"name": "indicator", "detail": {"available": ok, "result": "match" if ok else None}}]})
    return {"evidence": out}


def test_indicator_unchecked_on_every_candidate_without_an_exact_value():
    req = {"indicator": {"name": "갑"}}
    assert _indicator_unchecked(req, _ev([False, False]), "r1", ["T0"])
    assert not _indicator_unchecked(req, _ev([False, True]), "r1", ["T0"])
    assert not _indicator_unchecked(req, _ev([False, False], exact_on="T0"), "r1", ["T0"])
    assert _indicator_unchecked(req, _ev([False, False], exact_on="T1"), "r1", ["T0"])
    assert not _indicator_unchecked({"indicator": None}, _ev([False]), "r1", ["T0"])


def _q(failed=(), certain=True, reqs=(("r1", "ok"),)):
    return {"interpretation": {"status": "ok", "requirements": [
        {"id": r, "status": st, "indicator": {"name": "갑"}, "reasons": [], "span": [0, 1]} for r, st in reqs]},
        "judgement": {"certain": certain, "failed_conditions": list(failed)}, "fragments": []}


def _s(rid, status="confirm", reasons=(), tables=("T1",)):
    return {"requirement_id": rid, "status": status, "tables": [{"table_id": t} for t in tables],
            "model_step": "skipped", "unspecified": [], "reasons": list(reasons), "alternatives": [], "signals": {}}


def test_route_asks_an_unchecked_indicator():
    got = qr.question_route(_q(), [_s("r1", reasons=["indicator_unchecked"])])
    assert got["kind"] == "ask_interpretation" and got["reasons"] == ["indicator_unchecked"]
    assert qr.question_route(_q(), [_s("r1", "auto")])["kind"] == "proceed"


# ---------------------------------------------------------------- safety 1b: switch narrow / wide / off


def test_undecided_with_a_piece_out_of_the_catalog_by_the_switch(monkeypatch):
    an = _q(failed=["inexact_term", "unused_name"], certain=False, reqs=(("r1", "ok"), ("r2", "ok")))
    sels = [_s("r1"), _s("r2", "no_table", ["not_in_catalog"], ())]
    for mode, kind in (("off", "proceed"), ("narrow", "ask_interpretation"), ("wide", "ask_interpretation")):
        monkeypatch.setattr(qr, "UNDECIDED_ASK", mode)
        assert qr.question_route(an, sels)["kind"] == kind, mode
    only_unused = [_s("r1")]
    an1 = _q(failed=["inexact_term", "unused_name"], certain=False)
    for mode, kind in (("narrow", "proceed"), ("wide", "ask_interpretation")):
        monkeypatch.setattr(qr, "UNDECIDED_ASK", mode)
        assert qr.question_route(an1, only_unused)["kind"] == kind, mode
    monkeypatch.setattr(qr, "UNDECIDED_ASK", "wide")
    assert qr.question_route(_q(), only_unused)["kind"] == "proceed"  # a settled interpretation: no ask
