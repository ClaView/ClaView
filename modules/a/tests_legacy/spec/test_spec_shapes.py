"""Shape checks of the new outputs (plan 02, 4-1): a complete valid example passes, and each kind of broken data is
caught. Examples are synthetic. These run now.
"""

from __future__ import annotations

import copy

import pytest

from claview_a_legacy.contract import check, shapes
from claview_a_legacy.interpret.conditions import find_frequency_conditions
from claview_a_legacy.interpret.text import normalize_text

Q = "2020년 이후 가상대출 금리를 연도별로 보여줘"


def _span(quote: str) -> list[int]:
    i = Q.index(quote)
    return [i, i + len(quote)]


def full_analysis() -> dict:
    period = {"form": "since", "start_expr": {"text": "2020년", "year": "2020", "alias": None, "sub_unit": None,
                                              "sub_value": None, "end_mark": False},
              "end_expr": None, "open_end": True, "amount": None, "unit": None, "relative": False, "invalid": False,
              "extract_status": "explicit"}
    return {
        "text": normalize_text(Q),
        "conditions": [
            {"id": "P1", "kind": "period", "quote": "2020년 이후", "span": _span("2020년 이후"), "value": None,
             "attrs": period, "status": "candidate"},
            {"id": "F1", "kind": "frequency", "quote": "연도별", "span": _span("연도별"), "value": "Y",
             "attrs": {"value": "Y", "anchor": None}, "status": "candidate"},
        ],
        "fragments": [{"id": "g1", "quote": "가상대출 금리를", "span": _span("가상대출 금리를"), "role": "core",
                       "protected_by": None}],
        "terms": [{"fragment_id": "g1", "name": "가상대출 금리", "type": "series_name", "table_ids": ["T_X"],
                   "obj_id": None, "obj_name": None, "path": "exact", "status": "approved", "score": None}],
        "judgement": {"pairs": [], "requirement_draft": [{"id": "d1", "fragment_ids": ["g1"],
                                                          "indicator_fragment_id": "g1"}],
                      "certain": True, "failed_conditions": []},
        "interpretation": {
            "status": "ok", "common_condition_ids": ["P1", "F1"], "relations": [], "display_condition_ids": [],
            "reasons": [],
            "requirements": [{"id": "r1", "quote": "가상대출 금리를", "span": _span("가상대출 금리를"),
                              "indicator": {"quote": "가상대출 금리", "span": _span("가상대출 금리"), "fragment_id": "g1",
                                            "name": "가상대출 금리", "path": "exact"},
                              "measure_kind": None, "targets": [], "condition_ids": [],
                              "shared_condition_ids": ["P1", "F1"], "ambiguous_condition_ids": [], "status": "ok",
                              "reasons": []}],
        },
    }


def full_evidence() -> dict:
    return {
        "evidence": [{"requirement_id": "r1", "table_id": "T_X", "checks": [
            {"name": "frequency", "state": "computed", "source_status": "match", "verdict": "pass", "evidence_ids": ["e1"],
             "detail": {"requested": ["Y"], "available": ["Y", "M"], "status": "match"}},
            {"name": "period", "state": "computed", "source_status": "covered", "verdict": "pass", "evidence_ids": [],
             "detail": {"request": {"condition_ids": ["P1"], "form": "since", "start": "2020", "end": None},
                        "by_frequency": [{"frequency": "Y", "requested": True, "status": "covered",
                                          "coverage": ["2000", "2025"], "reason": "open_end"}],
                        "coverage_status": "n/a"}},
            {"name": "indicator", "state": "computed", "source_status": "match", "verdict": "pass", "evidence_ids": ["e2"],
             "detail": {"result": "match", "available": True, "by_field": {"title": "match"}, "phrase": "가상대출 금리",
                        "coverage": 1.0, "origin": "rule"}},
            {"name": "matched_items", "state": "computed", "source_status": None, "verdict": "n/a", "evidence_ids": [],
             "detail": {"rows": [{"condition_id": None, "obj_id": "A", "item_id": "2", "name": "가상하위", "term": "가상하위",
                                  "parent_path": [{"item_id": "1", "name": "가상상위"}], "depth": 1,
                                  "match_method": "exact", "polarity": "+"}]}},
            {"name": "region", "state": "unsupported", "source_status": None, "verdict": "n/a", "evidence_ids": [],
             "detail": {"status": "unsupported"}},
        ]}],
        "evidence_rows": {"e1": {"kind": "prd", "stance": "support", "table_id": "T_X", "text": "Y"},
                          "e2": {"kind": "indicator", "stance": "support", "table_id": "T_X", "text": None}},
    }


def test_complete_examples_pass_in_full_mode():
    assert check.check_analysis(full_analysis(), Q) == []
    assert check.check_evidence(full_evidence(), {"r1"}, {"P1", "F1"}, {"T_X"}) == []


def test_frequency_adapter_output_has_the_full_shape():
    q = "가상수출을 2024년 분기말마다, 그리고 매달 보여줘"
    found = find_frequency_conditions(normalize_text(q))
    assert [(c["id"], c["value"], c["attrs"]["anchor"]) for c in found] == [("F1", "Q", "end"), ("F2", "M", None)]
    assert check.check_conditions(found, q) == []
    assert check.check_text(normalize_text("  가상　대출  "), "  가상　대출  ") == []


def _broken(path, value, *, where="analysis"):
    data = full_analysis() if where == "analysis" else full_evidence()
    node = data
    for p in path[:-1]:
        node = node[p]
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return data


ANALYSIS_BREAKS = [
    (("conditions", 0, "span"), [0, 999], "outside the question"),
    (("conditions", 0, "quote"), "2020년", "!= question"),
    (("conditions", 0, "kind"), "season", "not in"),
    (("conditions", 1, "id"), "P1", "duplicate"),
    (("conditions", 1, "id"), "X9", "should start with"),
    (("conditions", 1, "attrs", "value"), "M", "!= attrs.value"),
    (("conditions", 1, "attrs", "extra"), 1, "unknown field"),
    (("conditions", 1, "attrs", "anchor"), KeyError, "missing field"),
    (("conditions", 0, "attrs", "end_expr"), {"text": "2021년", "year": "2021", "alias": None, "sub_unit": None,
                                              "sub_value": None, "end_mark": False}, "only a range"),
    (("conditions", 0, "attrs", "open_end"), False, "open-ended"),
    (("conditions", 0, "status"), "explicit", "not in"),
    (("fragments", 0, "role"), "head", "not in"),
    (("terms", 0, "fragment_id"), "g9", "unknown ID"),
    (("terms", 0, "type"), "noun", "not in"),
    (("judgement", "pairs"), [{"left": "g1", "right": "g9", "row": 8, "verdict": "split", "relation": None}],
     "unknown ID"),
    # plan 04 decision 1: the judgement table has 10 rows, so rows 1..10 are valid and 11 is not (user, 10/18)
    (("judgement", "pairs"), [{"left": "g1", "right": "g1", "row": 11, "verdict": "split", "relation": None}],
     "not in 1..10"),
    (("judgement", "failed_conditions"), ["inexact_term"], "certain: true while"),
    (("interpretation", "status"), "done", "not in"),
    (("interpretation", "requirements", 0, "condition_ids"), ["P7"], "unknown ID"),
    (("interpretation", "requirements", 0, "shared_condition_ids"), ["P1"], "differs from"),
    (("interpretation", "requirements", 0, "ambiguous_condition_ids"), ["F1"], None),
    (("interpretation", "relations"), [{"kind": "ratio", "requirement_ids": ["r9"], "condition_id": None}],
     "unknown ID"),
    (("interpretation", "requirements", 0, "indicator", "span"), [0, 3], "!= question"),
    (("text", "nospace"), "x", "not norm without whitespace"),
    (("text", "offsets"), [0], "length"),
    (("interpretation", "requirements", 0, "status"), KeyError, "missing field"),
]


@pytest.mark.parametrize("path, value, message", ANALYSIS_BREAKS, ids=lambda x: str(x)[:40])
def test_broken_analysis_is_caught(path, value, message):
    data = _broken(path, value)
    if path[-1] == "ambiguous_condition_ids":
        data["interpretation"]["requirements"][0]["condition_ids"] = ["F1"]
        message = "both own and ambiguous"
    problems = check.check_analysis(data, Q)
    assert problems and any(message in p for p in problems), problems


def test_partial_mode_allows_missing_fields_only():
    assert check.check_conditions([{"id": "F1", "kind": "frequency", "value": "M"}], Q, partial=True) == []
    assert check.check_conditions([{"id": "F1", "kind": "frequency"}], Q) != []
    assert check.check_conditions([{"id": "F1", "kind": "frequency", "colour": "red"}], Q, partial=True) != []
    assert check.check_conditions([{"id": "F1", "kind": "frequency", "quote": "연도", "span": [0, 4]}], Q,
                                  partial=True) != []


EVIDENCE_BREAKS = [
    (("evidence", 0, "checks", 0, "evidence_ids"), ["e9"], "unknown ID"),
    (("evidence", 0, "checks", 0, "detail", "status"), "fine", "not in"),
    (("evidence", 0, "checks", 0, "name"), "colour", "not in"),
    (("evidence", 0, "checks", 4, "verdict"), "pass", "not computed"),
    (("evidence", 0, "checks", 1, "detail", "request", "condition_ids"), ["P9"], "unknown ID"),
    (("evidence", 0, "checks", 3, "detail", "rows", 0, "depth"), 2, "len(parent_path)"),
    (("evidence", 0, "checks", 3, "detail", "rows", 0, "polarity"), "neg", "not in"),
    (("evidence", 0, "requirement_id"), "r9", "unknown ID"),
    (("evidence_rows", "e1", "stance"), "maybe", "not in"),
    (("evidence", 0, "checks", 0, "detail", "extra"), 1, "unknown field"),
]


@pytest.mark.parametrize("path, value, message", EVIDENCE_BREAKS, ids=lambda x: str(x)[:40])
def test_broken_evidence_is_caught(path, value, message):
    problems = check.check_evidence(_broken(path, value, where="evidence"), {"r1"}, {"P1", "F1"}, {"T_X"})
    assert problems and any(message in p for p in problems), problems


@pytest.mark.parametrize("field", ["text", "conditions", "fragments", "terms"])
@pytest.mark.parametrize("partial", [False, True])
def test_none_for_a_non_nullable_analysis_field_is_caught(field, partial):
    data = full_analysis()
    data[field] = None
    problems = check.check_analysis(data, Q, partial=partial)
    assert any(f"analysis.{field}: None is not allowed" in p for p in problems), problems


@pytest.mark.parametrize("field", ["judgement", "interpretation"])
def test_none_for_a_nullable_analysis_field_passes(field):
    data = full_analysis()
    data[field] = None
    assert check.check_analysis(data, Q) == []


def test_wrong_type_text_is_caught():
    data = full_analysis()
    data["text"] = Q
    assert any("expected TextView" in p for p in check.check_analysis(data, Q))


@pytest.mark.parametrize("path, message", [
    (("evidence", 0, "checks"), "evidence[0].checks: None is not allowed"),
    (("evidence", 0, "requirement_id"), "evidence[0].requirement_id: None is not allowed"),
    (("evidence", 0, "table_id"), "evidence[0].table_id: None is not allowed"),
    (("evidence", 0, "checks", 0, "detail"), "expected dict"),
    (("evidence", 0, "checks", 0, "evidence_ids"), "expected a list"),
    (("evidence_rows", "e1", "kind"), "expected str"),
    (("evidence",), "evidence_doc.evidence: None is not allowed"),
    (("evidence_rows",), "evidence_doc.evidence_rows: None is not allowed"),
], ids=lambda x: str(x)[:40])
@pytest.mark.parametrize("partial", [False, True])
def test_none_in_evidence_is_caught(path, message, partial):
    problems = check.check_evidence(_broken(path, None, where="evidence"), {"r1"}, {"P1", "F1"}, {"T_X"},
                                    partial=partial)
    assert any(message in p for p in problems), problems


def test_none_inside_pieces_is_caught():
    data = full_analysis()
    data["conditions"][0]["attrs"] = None
    data["fragments"][0]["quote"] = None
    data["interpretation"]["requirements"][0]["reasons"] = None
    problems = check.check_analysis(data, Q)
    assert any("conditions[0].attrs: expected dict" in p for p in problems), problems
    assert any("fragments[0].quote: expected str" in p for p in problems), problems
    assert any("requirements[0].reasons: expected a list" in p for p in problems), problems


def test_duplicate_requirement_table_entry_is_caught():
    doc = full_evidence()
    doc["evidence"].append(copy.deepcopy(doc["evidence"][0]))
    assert any("duplicate requirement x table" in p for p in check.check_evidence(doc))


def _indicator(doc, **detail):
    doc["evidence"][0]["checks"][2]["detail"].update(detail)
    return doc


def test_not_extracted_and_no_match_are_different_values():
    ok_not_extracted = _indicator(full_evidence(), available=False, result=None)
    ok_not_extracted["evidence"][0]["checks"][2]["verdict"] = "n/a"
    ok_not_extracted["evidence_rows"]["e2"]["stance"] = "unknown"
    assert check.check_evidence(ok_not_extracted) == []
    ok_no_match = _indicator(full_evidence(), result="none")
    ok_no_match["evidence"][0]["checks"][2]["verdict"] = "fail"
    ok_no_match["evidence_rows"]["e2"]["stance"] = "against"
    assert check.check_evidence(ok_no_match) == []
    # mixing them up is caught
    bad = _indicator(copy.deepcopy(ok_not_extracted), result="none")
    assert any("must be None" in p for p in check.check_evidence(bad))
    bad = copy.deepcopy(ok_not_extracted)
    bad["evidence_rows"]["e2"]["stance"] = "against"
    assert any("stance must be unknown" in p for p in check.check_evidence(bad))
    bad = _indicator(copy.deepcopy(ok_no_match), available=True, result=None)
    assert any("has a result" in p for p in check.check_evidence(bad))
    bad = copy.deepcopy(ok_no_match)
    bad["evidence_rows"]["e2"]["stance"] = "support"
    assert any("stance must be against" in p for p in check.check_evidence(bad))


def test_every_check_name_has_a_detail_shape_and_every_kind_attrs():
    assert set(shapes.DETAIL_BY_NAME) == set(shapes.CheckName.__args__)
    assert set(shapes.ATTRS_BY_KIND) == set(shapes.ConditionKind.__args__) == set(shapes.ID_PREFIX)
    assert {"relation", "calc", "function", "display"} <= set(shapes.ConditionKind.__args__)  # EDA 5 gaps
