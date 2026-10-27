"""V1 code signals and the V2 placeholder (plan 04, 3-1 and 3-2) on hand-made analyses."""

from __future__ import annotations

from claview_a.verify.checks import check_interpretation_state, check_selection_state


def _frag(fid, quote):
    return {"id": fid, "quote": quote, "span": [0, len(quote)], "role": "core", "protected_by": None}


def _term(fid, name, typ="indicator", tables=("T1",)):
    return {"fragment_id": fid, "name": name, "type": typ, "table_ids": list(tables), "obj_id": None,
            "obj_name": None, "path": "exact", "status": "approved", "score": None}


def _draft(did, fids, indicator_fid=None, indicator=None, row=8, breakdown=()):
    return {"id": did, "fragment_ids": list(fids), "indicator_fragment_id": indicator_fid, "indicator": indicator,
            "breakdown": list(breakdown), "row": row}


def _analysis(fragments, terms, drafts, conditions=(), unused=None):
    judgement = {"pairs": [], "requirement_draft": drafts, "certain": True, "failed_conditions": []}
    if unused is not None:
        judgement["unused_names"] = list(unused)
    return {"text": {}, "conditions": list(conditions), "fragments": fragments, "terms": terms,
            "judgement": judgement, "interpretation": None}


def _signals(result):
    return {s["code"]: s["status"] for s in result["signals"]}


def test_skipped_without_analysis():
    assert check_interpretation_state({"analysis": None})["status"] == "skipped"


def test_clear_signals_are_ok_and_count_indicators():
    a = _analysis([_frag("g1", "갑지표"), _frag("g2", "을지표")],
                  [_term("g1", "갑지표", tables=("T1",)), _term("g2", "을지표", tables=("T2",))],
                  [_draft("d1", ["g1"], "g1", "갑지표"), _draft("d2", ["g2"], "g2", "을지표")], unused=[])
    result = check_interpretation_state({"analysis": a})
    assert result["status"] == "ok" and result["source"] == "judgement"
    assert result["requirement_count"] == 2 and result["indicator_count"] == 2
    assert set(_signals(result).values()) == {"clear"}


def test_slash_inside_one_indicator():
    a = _analysis([_frag("g1", "갑지표 / 을지표")], [_term("g1", "갑지표")], [_draft("d1", ["g1"], "g1", "갑지표")],
                  unused=[])
    result = check_interpretation_state({"analysis": a})
    assert _signals(result)["slash_in_indicator"] == "fired"
    assert result["status"] == "mismatch" and result["requirement_id"] == "d1"


def test_slash_in_the_catalog_name_itself_is_not_a_signal():
    a = _analysis([_frag("g1", "갑/을 지표")], [_term("g1", "갑/을 지표")], [_draft("d1", ["g1"], "g1", "갑/을 지표")],
                  unused=[])
    assert _signals(check_interpretation_state({"analysis": a}))["slash_in_indicator"] == "clear"


def test_unused_indicator_name():
    a = _analysis([_frag("g1", "갑지표")], [_term("g1", "갑지표"), _term("g9", "병지표"), _term("g8", "서울", "target")],
                  [_draft("d1", ["g1"], "g1", "갑지표")], unused=["병지표", "서울"])
    result = check_interpretation_state({"analysis": a})
    signal = next(s for s in result["signals"] if s["code"] == "unused_indicator_name")
    assert signal["status"] == "fired" and signal["detail"] == {"names": ["병지표"], "other": ["서울"]}


def test_unused_names_not_in_the_judgement_is_unavailable():
    a = _analysis([_frag("g1", "갑지표")], [_term("g1", "갑지표")], [_draft("d1", ["g1"], "g1", "갑지표")])
    assert _signals(check_interpretation_state({"analysis": a}))["unused_indicator_name"] == "unavailable"


def test_same_table_siblings_in_one_requirement():
    frags = [_frag("g1", "갑지표"), _frag("g2", "을지표")]
    terms = [_term("g1", "갑지표", tables=("T1",)), _term("g2", "을지표", tables=("T1",))]
    merged = _analysis(frags, terms, [_draft("d1", ["g1", "g2"], "g1", "갑지표", row=8)], unused=[])
    assert _signals(check_interpretation_state({"analysis": merged}))["same_table_siblings_merged"] == "fired"
    breakdown = _analysis(frags, terms, [_draft("d1", ["g1", "g2"], "g1", "갑지표", row=2, breakdown=["을지표"])],
                          unused=[])
    assert _signals(check_interpretation_state({"analysis": breakdown}))["same_table_siblings_merged"] == "clear"


def test_relation_with_one_requirement():
    relation = {"id": "L1", "kind": "relation", "quote": "비율", "span": [0, 2], "value": None,
                "attrs": {"relation": "ratio"}, "status": "candidate"}
    a = _analysis([_frag("g1", "갑지표")], [_term("g1", "갑지표")], [_draft("d1", ["g1"], "g1", "갑지표")],
                  conditions=[relation], unused=[])
    assert _signals(check_interpretation_state({"analysis": a}))["relation_single_requirement"] == "fired"


def test_nothing_readable_is_unavailable():
    a = {"text": {}, "conditions": [], "fragments": [], "terms": [], "judgement": None, "interpretation": None}
    result = check_interpretation_state({"analysis": a})
    assert result["status"] == "unavailable" and result["source"] is None


def test_v2_is_skipped_without_selections():
    result = check_selection_state({"analysis": None, "selections": []})
    assert result["status"] == "skipped" and result["states"] == {} and result["reinterpret_signal"] is None


def test_v2_only_lowers_states():
    selections = [{"requirement_id": "r1", "tables": [], "status": "auto"},
                  {"requirement_id": "r2", "tables": [{"table_id": "T1"}], "status": "confirm"}]
    result = check_selection_state({"analysis": None, "selections": selections})
    assert result["states"] == {"r1": "no_table", "r2": "confirm"}
    assert result["lowered"] == [{"requirement_id": "r1", "from": "auto", "to": "no_table"}]
    assert result["status"] == "flagged"


def test_v2_merge_signal():
    def req(rid):
        return {"id": rid, "quote": "갑지표", "span": [0, 3], "targets": [], "condition_ids": [],
                "indicator": {"quote": "갑지표", "span": [0, 3], "fragment_id": None, "name": "갑지표", "path": "exact"},
                "status": "ok", "reasons": []}

    interp = {"status": "ok", "requirements": [req("r1"), req("r2")], "reasons": []}
    analysis = {"conditions": [], "interpretation": interp}
    same = [{"requirement_id": r, "tables": [{"table_id": "T1"}], "status": "auto"} for r in ("r1", "r2")]
    assert check_selection_state({"analysis": analysis, "selections": same})["reinterpret_signal"] == "merge"
    apart = [same[0], {"requirement_id": "r2", "tables": [{"table_id": "T2"}], "status": "auto"}]
    assert check_selection_state({"analysis": analysis, "selections": apart})["reinterpret_signal"] is None


def _merge_case(first: dict, second: dict) -> str | None:
    """Two requirements with the same indicator, each with one own condition, both on table T1."""
    def req(rid, cid):
        return {"id": rid, "quote": "갑지표", "span": [0, 3], "targets": [], "condition_ids": [cid],
                "indicator": {"quote": "갑지표", "span": [0, 3], "fragment_id": None, "name": "갑지표", "path": "exact"},
                "status": "ok", "reasons": []}

    conditions = [{"id": "B1", "status": "candidate", "span": [0, 2], **first},
                  {"id": "B2", "status": "candidate", "span": [5, 7], **second}]
    analysis = {"conditions": conditions, "interpretation": {"status": "ok", "reasons": [],
                                                             "requirements": [req("r1", "B1"), req("r2", "B2")]}}
    selections = [{"requirement_id": r, "tables": [{"table_id": "T1"}], "status": "auto"} for r in ("r1", "r2")]
    return check_selection_state({"analysis": analysis, "selections": selections})["reinterpret_signal"]


def _basis(quote, polarity="+", axis="seasonal"):
    return {"kind": "basis", "quote": quote, "value": "계절조정",
            "attrs": {"axis": axis, "value": "계절조정", "polarity": polarity, "origin": "dictionary"}}


def test_v2_merge_compares_condition_meaning_not_quotes():
    assert _merge_case(_basis("계절조정"), _basis("계절 조정 기준")) == "merge"  # same meaning, other words


def test_v2_no_merge_when_only_the_polarity_differs():
    assert _merge_case(_basis("계절조정"), _basis("계절조정", polarity="-")) is None


def test_v2_no_merge_when_an_attribute_differs():
    assert _merge_case(_basis("계절조정"), _basis("계절조정", axis="price")) is None
    since = {"form": "since", "start_expr": {"text": "2020년", "year": "2020", "alias": None, "sub_unit": None,
                                             "sub_value": None, "end_mark": False},
             "end_expr": None, "open_end": True, "amount": None, "unit": None, "relative": False, "invalid": False,
             "extract_status": "explicit"}
    point = {**since, "form": "point", "open_end": False}
    period = {"kind": "period", "quote": "2020년부터", "value": None}
    assert _merge_case({**period, "attrs": since}, {**period, "quote": "2020년 이후", "attrs": since}) == "merge"
    assert _merge_case({**period, "attrs": since}, {**period, "attrs": point}) is None