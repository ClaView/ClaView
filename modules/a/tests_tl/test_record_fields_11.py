"""Plan 56 order 11 (bundle 11, 1차): the decision's stage, the warning rows, each final table's period hint and
phrase, and the same table asked for different periods told apart from rows the model wrote apart. Made-up records."""

from __future__ import annotations

from claview_a import check as CH


def dec(sid, outcome="selected", reasons=(), warnings=(), tables=(), **extra):
    return {"statistic": sid, "outcome": outcome, "reasons": [{"code": c} for c in reasons],
            "warnings": [dict(w) for w in warnings], "final_tables": [dict(t) for t in tables], **extra}


def test_the_stage_follows_the_outcome():
    cases = {("selected", ()): "selected", ("selected_unverified", ()): "selected", ("ask", ()): "waiting_answer",
             ("ask_condition", ()): "waiting_answer", ("aggregation_needed", ()): "blocked_contract",
             ("failed", ("rejudge_pending",)): "rejudge_pending", ("failed", ("x",)): "failed",
             ("not_in_pool", ()): "failed"}
    for (outcome, reasons), stage in cases.items():
        assert CH.stage_of(dec("s1", outcome, reasons)) == stage
    assert set(cases.values()) <= set(CH.A_STAGES)


def test_warnings_get_rows_with_kind_scope_and_stage_and_stay_as_written():
    out = {"periods": [{"quote": "2024년", "for": ["s1"]}]}
    w = {"code": "stated_basis_unverified", "axis": "seasonal", "table": "T"}
    d = dec("s1", warnings=[w, {"code": "basis_excluded", "axis": "seasonal"}],
            tables=[{"table_id": "T", "frequency": "M", "period": [{"start": "2024-01", "end": "2024-12"}]}])
    CH.annotate(d, out)
    assert d["warnings"][0] == w  # unchanged
    assert [(r["kind"], r["scope"], r["statistic"]) for r in d["warning_rows"]] == [
        ("basis_unverified", "table", "s1"), ("basis_excluded", "statistic", "s1")]
    (t,) = d["final_tables"]
    assert t["period_hint"] == {"frequency": "M", "start": "2024-01", "end": "2024-12",
                                "share": [{"start": "2024-01", "end": "2024-12"}]}
    assert t["period_phrase"] == {"text": "2024년", "source": "quoted"}
    d = dec("s1", tables=[{"table_id": "T", "period": []}], condition_answers=[{"kind": "period", "quote": "최근 5년"}])
    CH.annotate(d, out)
    assert d["final_tables"][0]["period_phrase"] == {"text": "최근 5년", "source": "user_answer"}
    d = dec("s1", tables=[{"table_id": "T", "period": []}])
    CH.annotate(d, {"periods": []})
    assert d["final_tables"][0]["period_phrase"]["source"] == "default"


def same_table(quotes1, quotes2):
    D = {"s1": dec("s1", tables=[{"table_id": "T", "period": [{"start": "2024-01", "end": "2024-12"}]}]),
         "s2": dec("s2", tables=[{"table_id": "T", "period": [{"start": "2023-01", "end": "2023-12"}]}])}
    out = {"periods": [{"quote": q, "for": ["s1"]} for q in quotes1] + [{"quote": q, "for": ["s2"]} for q in quotes2]}
    CH.same_table_periods(D, out)
    return D


def test_the_same_table_asked_for_different_periods_is_a_link_not_supported():
    D = same_table(["2024년"], ["2023년"])
    for d in D.values():
        assert not d["reasons"] and [w["code"] for w in d["warnings"]] == ["same_table_period_differs"]
    assert CH.WARNING_KINDS["same_table_period_differs"] == "link_unsupported"


def test_one_period_written_differently_per_row_is_an_output_error():
    D = same_table(["2024년 1월부터"], ["2024년"])
    for d in D.values():
        assert [r["code"] for r in d["reasons"]] == ["period_rows_inconsistent"]
