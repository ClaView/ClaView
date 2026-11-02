"""Semantic row diagnostics and provenance must survive answers and references."""

from claview_a import check as CH


def period(quote, sid, meaning="range", start="2024"):
    return {"quote": quote, "for": [sid], "meaning": meaning, "refers_to": [],
            "form": "absolute" if meaning == "range" else "none", "start": start if meaning == "range" else "none",
            "end": "same" if meaning == "range" else "none", "count": "none", "unit": "none", "read": "ok"}


def output(periods, frequencies):
    return {"periods": periods, "frequencies": frequencies, "splitters": [], "calculations": []}


def test_answered_frequency_keeps_user_answer_provenance():
    out = output([period("M2", "s1", "not_stated")],
                 [{"quote": "M2", "for": ["s1"], "value": "not_stated", "anchor": "none"}])
    d = {"statistic": "s1", "condition_answers": [{"kind": "period", "quote": "2024년"},
                                                  {"kind": "frequency", "quote": "월별"}]}
    CH._semantic_reading(d, "M2", out)
    assert d["frequency_quotes"] == [{"quote": "월별", "for": ["s1"], "frequency": "M",
                                      "point_in_period": None, "source": "user_answer"}]


def test_ambiguous_reference_keeps_real_quotes_not_calendar_values():
    same = period("같은 기간", "s3", "same_as")
    same["refers_to"] = ["s1", "s2"]
    out = output([period("2024년 한 해", "s1"), period("2023년 전체", "s2", start="2023"), same], [])
    d = {"statistic": "s3"}
    CH._semantic_rows(d, out)
    assert d["period_reference"]["quotes"] == {"s1": ["2024년 한 해"], "s2": ["2023년 전체"]}
    assert d["period_reference"]["status"] == "ambiguous"


def test_not_stated_quote_does_not_hide_unquoted_condition_audit():
    question = "2024년 월별 M2"
    out = output([period(question, "s1", "not_stated")],
                 [{"quote": question, "for": ["s1"], "value": "not_stated", "anchor": "none"}])
    d = {"statistic": "s1"}
    CH._semantic_reading(d, question, out)
    assert d["period_audit"]["words_outside_quotes"]["period"]
    assert d["period_audit"]["words_outside_quotes"]["frequency"]


def test_correctable_period_missing_and_unresolved_are_recorded_as_internal():
    for code in ("period_unresolved", "period_quote_missing", "period_rows_inconsistent"):
        assert CH.failure_kinds({"reasons": [{"code": code}]}) == ["internal_interpretation"]
