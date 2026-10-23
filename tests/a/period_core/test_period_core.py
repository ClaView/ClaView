"""claview.a.period_core (the shared period package): policy file, input/output contract, and the stage-3 rules (plan 05).

All inputs are synthetic; no network, no model.
"""

from __future__ import annotations

import copy
import json

import pytest
from claview.a.period_core import (
    CONTRACT_VERSION,
    PolicyError,
    check_assignment,
    check_conditions,
    check_context,
    check_result,
    default_policy,
    load_policy,
    resolve_period_for_table,
)

AS_OF = "2026-10-20"
TABLE = {
    "periods": {
        "Y": {"start": "2000", "end": "2025"},
        "Q": {"start": "200001", "end": "202602"},
        "M": {"start": "200001", "end": "202608"},
    }
}
Q_ONLY = {"periods": {"Q": {"start": "200001", "end": "202602"}}}
Y_AND_Q = {
    "periods": {"Y": {"start": "2000", "end": "2025"}, "Q": {"start": "200001", "end": "202602"}}
}
HALF_TABLE = {"periods": {"H": {"start": "201001", "end": "202601"}}}
OLD_EDITION = {"periods": {"Y": {"start": "2000", "end": "2015"}}}
NEW_EDITION = {"periods": {"Y": {"start": "2010", "end": "2025"}}}


def expr(year=None, alias=None, sub_unit=None, sub_value=None, end_mark=False):
    return {
        "text": "t",
        "year": year,
        "alias": alias,
        "sub_unit": sub_unit,
        "sub_value": sub_value,
        "end_mark": end_mark,
    }


def period(form, start=None, end=None, amount=None, unit=None, relative=False, cid="P1"):
    return {
        "id": cid,
        "kind": "period",
        "quote": "q",
        "value": None,
        "attrs": {
            "form": form,
            "start_expr": start,
            "end_expr": end,
            "open_end": form == "since",
            "amount": amount,
            "unit": unit,
            "relative": relative,
            "invalid": False,
            "extract_status": "explicit",
        },
    }


def freq(value, cid="F1"):
    return {"id": cid, "kind": "frequency", "quote": "q", "value": value, "attrs": {"value": value}}


def applied(result):
    a = result["applied"]
    return a["frequency"], a["start"], a["end"]


# ---------------------------------------------------------------- policy


def test_packaged_policy_holds_the_agreed_numbers():
    policy = load_policy()
    assert policy.default_recent_years == {"Y": 10, "H": 5, "Q": 5, "M": 3}
    assert policy.frequency_order == ("Y", "H", "Q", "M")
    assert [policy.default_window(f) for f in "YHQM"] == [10, 10, 20, 36]
    assert policy.version.startswith("period-policy/")
    assert len(policy.sha256) == 64


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.pop("policy_version"),
        lambda d: d["default_recent_years"].update(M=0),
        lambda d: d["default_recent_years"].pop("H"),
        lambda d: d.update(frequency_order=["M", "Q", "H", "Y"]),
        lambda d: d["periods_per_year"].update(Q=3),
    ],
)
def test_a_broken_policy_file_fails_loudly(tmp_path, change):
    data = json.loads(
        json.dumps(
            {
                "policy_version": "x",
                "frequency_order": ["Y", "H", "Q", "M"],
                "periods_per_year": {"Y": 1, "H": 2, "Q": 4, "M": 12},
                "default_recent_years": {"Y": 10, "H": 5, "Q": 5, "M": 3},
            }
        )
    )
    change(data)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(path)


def test_every_result_names_its_versions():
    result = resolve_period_for_table([freq("Y")], TABLE, AS_OF)
    policy = default_policy()
    assert result["versions"] == {
        "package": "0.2.0",
        "contract": CONTRACT_VERSION,
        "policy": policy.version,
        "policy_sha256": policy.sha256,
    }


# ---------------------------------------------------------------- input contract


@pytest.mark.parametrize(
    "conditions",
    [
        [None],
        [{"kind": "period", "attrs": {}}],
        [{"id": "P1", "kind": "period", "attrs": {"form": "weird", "extract_status": "explicit"}}],
        [
            {
                "id": "P1",
                "kind": "period",
                "attrs": {
                    "form": "range",
                    "extract_status": "explicit",
                    "start_expr": expr("2020"),
                },
            }
        ],
        [freq("D")],
        [freq("Y"), freq("Y")],
    ],
)
def test_malformed_conditions_are_input_contract_errors(conditions):
    assert check_conditions(conditions)
    result = resolve_period_for_table(conditions, TABLE, AS_OF)
    assert result["status"] == "invalid_input"
    assert result["issue"]["code"] == "malformed_condition"
    assert check_result(result) == []


def test_condition_fields_must_belong_to_a_condition():
    result = resolve_period_for_table([freq("Y")], TABLE, AS_OF, condition_fields={"P9": {}})
    assert result["status"] == "invalid_input"


def test_a_malformed_diagnosis_is_an_input_error_and_unsupported_is_period_unsupported():
    cond = [period("point", expr("2024"))]
    bad = resolve_period_for_table(
        cond, TABLE, AS_OF, condition_fields={"P1": {"period_diagnosis": "malformed"}}
    )
    assert bad["status"] == "invalid_input"
    unsupported = resolve_period_for_table(
        cond, TABLE, AS_OF, condition_fields={"P1": {"period_diagnosis": "unsupported"}}
    )
    assert unsupported["status"] == "unresolved"
    assert unsupported["issue"]["code"] == "period_unsupported"
    assert unsupported["applied"] is None  # never replaced by the default window


@pytest.mark.parametrize(
    "context",
    [
        {"bundle_id": "b1", "anchor_source": "bundle_common"},
        {"bundle_id": "b1", "anchor_source": "table_latest", "anchor": "2026-06"},
        {"bundle_id": "", "anchor_source": "table_latest", "anchor": None},
        {"bundle_id": "b1", "anchor_source": "now", "anchor": None},
    ],
)
def test_bad_contexts_are_rejected(context):
    assert check_context(context)
    assert (
        resolve_period_for_table([freq("M")], TABLE, AS_OF, context=context)["status"]
        == "invalid_input"
    )


# ---------------------------------------------------------------- coverage


def test_partial_overlap_keeps_requested_covered_and_missing_apart():
    result = resolve_period_for_table(
        [freq("Y"), period("range", expr("1995"), expr("2003"))], TABLE, AS_OF
    )
    assert applied(result) == ("Y", "2000", "2003")
    assert result["periods"]["requested"] == [{"start": "1995", "end": "2003"}]
    assert result["periods"]["covered"] == [{"start": "2000", "end": "2003"}]
    assert result["periods"]["missing"] == [{"start": "1995", "end": "1999"}]
    assert check_result(result) == []


def test_no_overlap_is_no_data_in_range_with_the_coverage():
    result = resolve_period_for_table(
        [freq("Y"), period("range", expr("1980"), expr("1990"))], TABLE, AS_OF
    )
    assert result["status"] == "no_data_in_range"
    assert result["coverage"] == {"frequency": "Y", "start": "2000", "end": "2025"}
    assert result["issue"]["code"] != "metadata_refresh_required"
    assert check_result(result) == []


def test_calc_base_is_kept_apart_from_the_request():
    yoy = {
        "id": "K1",
        "kind": "calc",
        "quote": "q",
        "value": "전년동기비",
        "attrs": {"value": "전년동기비"},
    }
    result = resolve_period_for_table([freq("M"), period("point", expr("2024")), yoy], TABLE, AS_OF)
    assert applied(result) == ("M", "202301", "202412")
    assert result["periods"]["requested"] == [{"start": "202401", "end": "202412"}]
    assert result["periods"]["calc_base"] == {"start": "202301", "end": "202312"}
    assert result["condition_fields"]["K1"]["applied"] is True


# ---------------------------------------------------------------- frequency


@pytest.mark.parametrize(
    "hint",
    [
        None,
        [],
        [{"kind": "frequency", "value": "H", "from_condition_id": "P1", "rule": "period_unit"}],
    ],
)
def test_an_inferred_frequency_the_table_lacks_never_drops_the_table(hint):
    cond = [period("point", expr("2024", sub_unit="H", sub_value="하"))]
    result = resolve_period_for_table(cond, TABLE, AS_OF, None, hint)
    assert result["status"] == "ok"
    assert result["frequency"] == "Y"
    assert (
        result["slots"]["frequency"]["hint_state"]
        == {None: "absent", 0: "empty", 1: "given"}[None if hint is None else len(hint)]
    )
    assert any(n["code"] == "inferred_frequency_unavailable" for n in result["notices"])


def test_only_a_stated_frequency_can_drop_the_table():
    assert resolve_period_for_table([freq("H")], TABLE, AS_OF)["status"] == "table_unsuitable"


def test_mixed_units_take_the_finest_the_table_supports_as_an_assumption():
    cond = [
        period(
            "range",
            expr("2024", sub_unit="Q", sub_value="3"),
            expr("2025", sub_unit="M", sub_value="5"),
        )
    ]
    both = resolve_period_for_table(cond, TABLE, AS_OF)
    assert both["frequency"] == "M" and both["slots"]["frequency"]["rule"] == "period_unit_finest"
    q_only = resolve_period_for_table(cond, Y_AND_Q, AS_OF)
    assert (
        q_only["frequency"] == "Q" and q_only["slots"]["frequency"]["rule"] == "period_unit_finest"
    )
    assert q_only["slots"]["frequency"]["confirmed"] is False
    assert {"dimension": "frequency", "value": "Q", "rule": "period_unit_finest"} in q_only[
        "assumptions"
    ]


def test_an_empty_hint_list_and_an_absent_field_both_reread_the_units_but_stay_apart():
    cond = [period("recent", amount=6, unit="month", relative=True)]
    empty = resolve_period_for_table(cond, TABLE, AS_OF, None, [])
    absent = resolve_period_for_table(cond, TABLE, AS_OF)
    assert applied(empty) == applied(absent) == ("M", "202603", "202608")
    assert empty["slots"]["frequency"]["hint_state"] == "empty"
    assert absent["slots"]["frequency"]["hint_state"] == "absent"


def test_recent_months_on_a_coarser_fallback_frequency_round_up_as_an_assumption():
    result = resolve_period_for_table(
        [period("recent", amount=6, unit="month", relative=True)], Q_ONLY, AS_OF
    )
    assert applied(result) == ("Q", "202601", "202602")
    assert any(a["rule"].endswith("recent_rounded_up") for a in result["assumptions"])


def test_half_year_default_window_is_five_years():
    result = resolve_period_for_table([], HALF_TABLE, AS_OF)
    assert applied(result) == ("H", "202102", "202601")
    assert result["source"] == "default"


# ---------------------------------------------------------------- anchors and editions


def test_a_bundle_common_anchor_moves_relative_and_assumed_periods_only():
    context = {
        "bundle_id": "b1",
        "anchor_source": "bundle_common",
        "anchor": "2026-06",
        "simultaneous": True,
    }
    recent = resolve_period_for_table(
        [freq("M"), period("recent", amount=1, unit="year", relative=True)],
        TABLE,
        AS_OF,
        context=context,
    )
    assert applied(recent) == ("M", "202507", "202606")
    assert recent["anchor"] == {
        "period": "202606",
        "source": "bundle_common",
        "bundle_id": "b1",
        "simultaneous": True,
    }
    latest = resolve_period_for_table(
        [freq("M"), period("recent", amount=1, unit="year", relative=True)], TABLE, AS_OF
    )
    assert applied(latest) == ("M", "202509", "202608")
    stated = resolve_period_for_table(
        [freq("M"), period("range", expr("2026"), expr("2026"))], TABLE, AS_OF, context=context
    )
    assert applied(stated) == ("M", "202601", "202608")  # a stated period is not cut at the anchor


def test_an_anchor_after_the_tables_latest_period_is_an_input_error():
    context = {"bundle_id": "b1", "anchor_source": "bundle_common", "anchor": "2026-12"}
    result = resolve_period_for_table([freq("M")], TABLE, AS_OF, context=context)
    assert result["status"] == "invalid_input"
    assert result["issue"]["code"] == "anchor_after_table_latest"


def test_no_common_anchor_keeps_each_tables_latest_period():
    context = {
        "bundle_id": "b1",
        "anchor_source": "table_latest",
        "anchor": None,
        "simultaneous": False,
    }
    result = resolve_period_for_table([freq("Y")], TABLE, AS_OF, context=context)
    assert result["anchor"]["period"] == "2025" and result["anchor"]["simultaneous"] is False


def test_edition_stitching_keeps_the_request_and_queries_each_share():
    request = [freq("Y"), period("range", expr("2005"), expr("2024"))]
    old = resolve_period_for_table(
        request, OLD_EDITION, AS_OF, assignment=[{"start": "2005-01", "end": "2009-12"}]
    )
    new = resolve_period_for_table(
        request, NEW_EDITION, AS_OF, assignment=[{"start": "2010-01", "end": "2024-12"}]
    )
    assert applied(old) == ("Y", "2005", "2009") and applied(new) == ("Y", "2010", "2024")
    assert (
        old["periods"]["requested"]
        == new["periods"]["requested"]
        == [{"start": "2005", "end": "2024"}]
    )
    assert old["periods"]["missing"] == new["periods"]["missing"] == []
    # without assignments no common period is needed either: the old edition answers what it holds
    alone = resolve_period_for_table(request, OLD_EDITION, AS_OF)
    assert applied(alone) == ("Y", "2005", "2015")
    assert alone["periods"]["missing"] == [{"start": "2016", "end": "2024"}]


# ---------------------------------------------------------------- condition slots


def test_b_fills_applied_and_keeps_as_source_and_confirmed():
    fields = {
        "P1": {"source": "user", "confirmed": True, "applied": True, "period_diagnosis": "ok"},
        "P2": {"source": "question", "confirmed": False, "applied": True},
    }
    conditions = [
        freq("Y"),
        period("point", expr("1990")),
        period("recent", amount=3, unit="year", relative=True, cid="P2"),
    ]
    result = resolve_period_for_table(conditions, TABLE, AS_OF, condition_fields=fields)
    assert result["condition_fields"]["P1"] == {
        "source": "user",
        "confirmed": True,
        "applied": False,
        "period_diagnosis": "ok",
    }
    assert result["condition_fields"]["P2"]["applied"] is True
    assert result["condition_fields"]["F1"]["applied"] is True


# ---------------------------------------------------------------- review fixes (stage 3)


YOY = {
    "id": "K1",
    "kind": "calc",
    "quote": "q",
    "value": "전년동기비",
    "attrs": {"value": "전년동기비"},
}


def test_a_share_takes_its_comparison_base_from_its_own_first_period():
    request = [freq("M"), period("range", expr("2020"), expr("2024")), YOY]
    share = resolve_period_for_table(
        request, TABLE, AS_OF, assignment=[{"start": "2024-01", "end": "2024-12"}]
    )
    assert applied(share) == ("M", "202301", "202412")
    assert share["periods"]["calc_base"] == {"start": "202301", "end": "202312"}
    assert share["periods"]["requested"] == [{"start": "202001", "end": "202412"}]


TWO_SHARES = [{"start": "2024-01", "end": "2024-03"}, {"start": "2024-10", "end": "2024-12"}]


@pytest.mark.parametrize(
    "assignment",
    [TWO_SHARES, [{"start": "2024-01", "end": "2024-06"}, {"start": "2024-07", "end": "2024-12"}]],
)
def test_more_than_one_assigned_segment_is_rejected_before_any_query(assignment):
    """User decision 10/21 (option A): one continuous segment per table; never filled from the
    first start to the last end, never cut to the first segment (adjacent ones too)."""
    given = copy.deepcopy(assignment)
    request = [freq("M"), period("range", expr("2024"), expr("2024"))]
    result = resolve_period_for_table(request, TABLE, AS_OF, assignment=assignment)
    assert result["status"] == "invalid_input"
    assert result["issue"]["code"] == "invalid_assignment"
    assert result["issue"]["problems"] == [
        "assignment: one continuous segment per table expected, got 2"
    ]
    assert result["applied"] is None and result["periods"] is None
    assert assignment == given  # the input is not changed
    assert check_assignment(assignment) == result["issue"]["problems"]
    assert check_result(result) == []


def test_one_assigned_segment_is_queried_as_its_share():
    result = resolve_period_for_table(
        [freq("M"), period("range", expr("2024"), expr("2024"))],
        TABLE,
        AS_OF,
        assignment=[{"start": "2024-04", "end": "2024-09"}],
    )
    assert applied(result) == ("M", "202404", "202409")
    assert result["periods"]["requested"] == [{"start": "202401", "end": "202412"}]
    assert result["periods"]["assigned"] == [{"start": "2024-04", "end": "2024-09"}]


def test_a_bad_assignment_next_to_a_bad_condition_is_reported_as_malformed():
    bad = period("point", expr("2024"))
    bad["attrs"]["form"] = "weird"
    result = resolve_period_for_table([bad], TABLE, AS_OF, assignment=TWO_SHARES)
    assert result["issue"]["code"] == "malformed_condition"
    assert result["issue"]["problems"][-1].startswith("assignment:")


def test_several_stated_periods_with_one_share_stay_allowed():
    """The one-segment rule is per table's assignment: the user's own periods are not limited."""
    conditions = [freq("Y"), period("point", expr("2018")), period("point", expr("2022"), cid="P2")]
    result = resolve_period_for_table(
        conditions, TABLE, AS_OF, assignment=[{"start": "2015-01", "end": "2024-12"}]
    )
    assert applied(result) == ("Y", "2018", "2022")


# --- invalid, old extract status, unsupported and malformed (user follow-up 10/21)


@pytest.mark.parametrize(
    "fields",
    [
        None,
        {"P1": {"source": "question", "confirmed": True, "period_diagnosis": "ok"}},
        {"P1": {"source": "question", "confirmed": True, "period_diagnosis": "unsupported"}},
        {"P1": {"source": "question", "confirmed": True}},
    ],
)
def test_a_period_marked_invalid_is_malformed_whatever_its_slot_says(fields):
    cond = [period("point", expr("2024", sub_unit="Q", sub_value="7"))]
    cond[0]["attrs"]["invalid"] = True
    result = resolve_period_for_table(cond, TABLE, AS_OF, condition_fields=fields)
    assert result["status"] == "invalid_input"
    assert result["issue"]["code"] == "malformed_condition"
    assert (
        "conditions[0].attrs.invalid: marked invalid by the interpretation (malformed)"
        in (result["issue"]["problems"])
    )
    assert result["applied"] is None and result["assumptions"] == []


def _old_status(condition):
    condition = copy.deepcopy(condition)
    condition["attrs"]["extract_status"] = "unresolved"
    return condition


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (period("recent", amount=3, unit="month", relative=True), ("M", "202606", "202608")),
        (
            period("point", expr(alias="지난", sub_unit="Q"), relative=True),
            ("M", "202604", "202606"),
        ),
        (
            period("point", expr(alias="지난", sub_unit="M"), relative=True),
            ("M", "202607", "202607"),
        ),
    ],
)
def test_the_old_extract_status_alone_never_makes_a_readable_period_unsupported(
    condition, expected
):
    month_table = {"periods": {"M": {"start": "200001", "end": "202608"}}}
    result = resolve_period_for_table([_old_status(condition)], month_table, AS_OF)
    assert result["status"] == "ok"
    assert applied(result) == expected


@pytest.mark.parametrize("sub_value", ["²", "１３", "x"])
def test_a_sub_value_int_cannot_read_is_unsupported_not_an_exception(sub_value):
    cond = _old_status(period("point", expr("2024", sub_unit="M", sub_value=sub_value)))
    result = resolve_period_for_table([cond], TABLE, AS_OF)
    assert (result["status"], result["issue"]["code"]) == ("unresolved", "period_unsupported")


def test_unreadable_unsupported_and_malformed_periods_stay_apart():
    unreadable = _old_status(period("point", expr(alias="언젠가")))
    real = resolve_period_for_table([freq("Y"), unreadable], TABLE, AS_OF)
    assert (real["status"], real["issue"]["code"], real["issue"]["detail"]) == (
        "unresolved",
        "period_unsupported",
        "period_expression",
    )
    readable = period("point", expr("2024"))
    stated = resolve_period_for_table(
        [freq("Y"), readable],
        TABLE,
        AS_OF,
        condition_fields={"P1": {"period_diagnosis": "unsupported"}},
    )
    assert (stated["status"], stated["issue"]["code"], stated["issue"]["detail"]) == (
        "unresolved",
        "period_unsupported",
        "diagnosed_unsupported",
    )
    malformed = resolve_period_for_table(
        [freq("Y"), readable],
        TABLE,
        AS_OF,
        condition_fields={"P1": {"period_diagnosis": "malformed"}},
    )
    assert (malformed["status"], malformed["issue"]["code"]) == (
        "invalid_input",
        "malformed_condition",
    )
    for result in (real, stated, malformed):
        assert result["applied"] is None and result["assumptions"] == []  # no default window


@pytest.mark.parametrize(
    ("table", "sub_unit", "expected"),
    [
        ({"periods": {"M": {"start": "200001", "end": "202608"}}}, "Q", ("M", "202604", "202606")),
        ({"periods": {"M": {"start": "200001", "end": "202608"}}}, "H", ("M", "202601", "202606")),
        ({"periods": {"Q": {"start": "200001", "end": "202602"}}}, "H", ("Q", "202503", "202504")),
    ],
)
def test_a_relative_sub_unit_on_a_finer_table_keeps_the_whole_sub_period(table, sub_unit, expected):
    cond = [period("point", expr(alias="지난", sub_unit=sub_unit), relative=True)]
    result = resolve_period_for_table(cond, table, AS_OF)
    assert applied(result) == expected


def test_the_end_of_a_relative_sub_period_is_its_last_period():
    month_table = {"periods": {"M": {"start": "200001", "end": "202608"}}}
    cond = [period("point", expr(alias="지난", sub_unit="Q", end_mark=True), relative=True)]
    assert applied(resolve_period_for_table(cond, month_table, AS_OF)) == ("M", "202606", "202606")


def test_no_data_in_range_keeps_the_request():
    result = resolve_period_for_table(
        [freq("Y"), period("range", expr("1980"), expr("1990"))], TABLE, AS_OF
    )
    assert result["periods"]["requested_months"] == [{"start": "1980-01", "end": "1990-12"}]
    assert result["periods"]["covered_months"] == []


JUNK = [None, True, 7, "abcd", "", [], {}, [1], {"x": 1}]


def _leaves(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, (*path, key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _leaves(item, (*path, i))
    yield path


def _replace(value, path, new):
    if not path:
        return new
    out = copy.deepcopy(value)
    node = out
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = new
    return out


def test_no_wrong_value_anywhere_in_the_inputs_raises():
    args = {
        "conditions": [
            freq("M"),
            period("range", expr("2020", sub_unit="Q", sub_value="1"), expr("2024")),
            period("point", expr(alias="지난", sub_unit="Q"), relative=True, cid="P2"),
            YOY,
        ],
        "calc_hints": [{"kind": "change", "quote": "q"}],
        "inferred": [{"kind": "frequency", "value": "M", "from_condition_id": "P1", "rule": "x"}],
        "condition_fields": {"P1": {"source": "question", "confirmed": True}},
        "context": {"bundle_id": "b1", "anchor_source": "bundle_common", "anchor": "2026-06"},
        "assignment": [{"start": "2020-01", "end": "2024-12"}],
    }
    count = 0
    for name, value in args.items():
        for path in _leaves(value):
            for junk in JUNK:
                call = {**args, name: _replace(value, path, junk)}
                result = resolve_period_for_table(
                    call["conditions"],
                    TABLE,
                    AS_OF,
                    call["calc_hints"],
                    call["inferred"],
                    condition_fields=call["condition_fields"],
                    context=call["context"],
                    assignment=call["assignment"],
                )
                assert check_result(result) == [], (name, path, junk)
                count += 1
    assert count > 300


def test_inputs_are_never_changed():
    conditions = [freq("M"), period("since", expr("2015"))]
    fields = {"P1": {"source": "question", "confirmed": True, "applied": None}}
    context = {"bundle_id": "b1", "anchor_source": "bundle_common", "anchor": "2026-06"}
    before = copy.deepcopy((conditions, fields, context, TABLE))
    resolve_period_for_table(conditions, TABLE, AS_OF, condition_fields=fields, context=context)
    assert (conditions, fields, context, TABLE) == before
