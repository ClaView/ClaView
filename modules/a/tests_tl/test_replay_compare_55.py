"""Plan 55 (user 2026-10-31 12:36 UTC): what a stored-answer replay compares and when a case is comparable.

A replay's "no change" covers the targets of checks and corrections and the frequency quotes, not only codes; the
comparison before plan 55 (``FIELDS_V1``) is kept beside it. In order mode a case is comparable only when every
whole request equals the archived one, not only its input. Records here are small hand-written ones (no run, no
network).
"""

from __future__ import annotations

import copy

import pytest
from tl_eval import replay as RP

from claview_a.graph import JudgeError
from claview_a.judges import text_sha


def record() -> dict:
    return {
        "route": "handoff", "error": None, "card_refs": ["C1.1"], "research": None,
        "final": {"s1": {"outcome": "selected", "representative": "T1",
                         "tables": [{"table_id": "T1", "frequency": "M", "period": ["2024.01", "2024.12"]}],
                         "asks": [],
                         "frequency_quotes": [{"quote": "월별", "for": ["s1"], "frequency": "M",
                                               "point_in_period": None}]}},
        "checks": {"s1": {"reasons": [],
                          "warnings": [{"code": "stated_basis_unverified", "axis": "seasonal", "stated": ["원계열"],
                                        "table": "T1"},
                                       {"code": "splitter_without_axis_value", "axis": "stock", "quotes": ["말잔"]}],
                          "citation": []}},
        "corrections": {"s1": [{"code": "basis_switched", "axis": "seasonal", "from": "T1", "to": "T2"}]},
        "unresolved": [{"id": "s2", "quote": "환율", "periods": ["2022년부터"], "frequencies": ["월평균"],
                        "calculations": []}],
    }


def changed(path, value):
    rec = record()
    *keys, last = path
    node = rec
    for k in keys:
        node = node[k]
    node[last] = value
    return rec


def test_the_same_record_has_no_change():
    assert RP.diff(record(), record()) == {} and RP.diff(record(), record(), RP.FIELDS_V1) == {}


@pytest.mark.parametrize("path, value, field", [
    (("checks", "s1", "warnings", 0, "table"), "T9", "checks"),
    (("checks", "s1", "warnings", 0, "stated"), ["계절조정"], "checks"),
    (("checks", "s1", "warnings", 1, "quotes"), ["평잔"], "checks"),
    (("corrections", "s1", 0, "to"), "T3", "corrections"),
    (("corrections", "s1", 0, "from"), "T0", "corrections"),
    (("final", "s1", "frequency_quotes", 0, "for"), ["s1", "s2"], "final"),
    (("final", "s1", "frequency_quotes", 0, "point_in_period"), "end", "final"),
    (("unresolved", 0, "periods"), ["2023년부터"], "unresolved"),
])
def test_a_changed_target_is_a_change_the_codes_alone_miss(path, value, field):
    rec = changed(path, value)
    assert list(RP.diff(record(), rec)) == [field]
    assert RP.diff(record(), rec, RP.FIELDS_V1) == {}


def test_a_changed_code_is_a_change_in_both():
    rec = changed(("checks", "s1", "warnings", 0, "code"), "stated_basis_conflict")
    assert list(RP.diff(record(), rec)) == ["checks"] and list(RP.diff(record(), rec, RP.FIELDS_V1)) == ["checks"]


def test_the_order_of_warnings_is_not_a_change():
    rec = record()
    rec["checks"]["s1"]["warnings"].reverse()
    assert RP.diff(record(), rec) == {}


def test_the_compared_fields_name_every_field():
    assert set(RP.COMPARED) == set(RP.FIELDS) and set(RP.FIELDS_V1) < set(RP.FIELDS)


REQUEST = {"model": "m", "instructions": "A", "input": "question and cards", "format": {}, "max_output_tokens": 10}


def archived(request) -> dict:
    return {"request_sha256": text_sha(request), "request": copy.deepcopy(request), "outcome": "failed",
            "error": "timeout"}


def call(mode, request):
    log = []
    judge = RP._Judge([archived(REQUEST)], "first", mode, log)
    with pytest.raises(JudgeError):
        judge({}, request)
    return log


def test_order_mode_tells_a_new_instruction_from_the_same_input():
    log = call("order", {**REQUEST, "instructions": "B"})
    assert (log[0]["input_equal"], log[0]["request_equal"]) == (True, False)
    assert RP.equality(log) == {"inputs_equal": True, "requests_equal": False, "comparable": False}


def test_order_mode_with_the_same_request_is_comparable():
    log = call("order", copy.deepcopy(REQUEST))
    assert RP.equality(log) == {"inputs_equal": True, "requests_equal": True, "comparable": True}


def test_order_mode_with_a_new_input_is_not_comparable():
    log = call("order", {**REQUEST, "input": "other cards"})
    assert RP.equality(log) == {"inputs_equal": False, "requests_equal": False, "comparable": False}


def test_exact_mode_answers_only_the_same_request():
    log = []
    judge = RP._Judge([archived(REQUEST)], "first", "exact", log)
    with pytest.raises(RP.ReplayGap):
        judge({}, {**REQUEST, "instructions": "B"})
    assert log[0]["hit"] is False and RP.equality(log)["comparable"] is False


@pytest.mark.parametrize("path", ["runs/new200/x", "D:/x/indep60/run", "blind30", "x/holdout/y"])
def test_a_protected_set_is_refused(path):
    with pytest.raises(PermissionError):
        RP.refuse_protected(path)
