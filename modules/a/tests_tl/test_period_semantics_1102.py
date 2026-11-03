"""Contract .8 (BOK20 live check 11-02, owner 02:37 and 02:47 UTC): the model structures each period and frequency,
the code computes and checks them. Scripted answers (made up: they say nothing about a model's reading).

- The same request written as .7 quotes and as .8 structured rows ends in the same final tables and periods.
- Rows are required per statistic; a missing row, a failed reading, a conflict are the model's own failures (one
  recovery may correct them); an excluded period stays a first-stage limit; no period is asked, never defaulted; a
  period of a whole year never decides the yearly frequency.
- The quote reader and the words outside the quotes are not read on the .8 path (recorded only)."""

from __future__ import annotations

import copy
import json

import pytest

from claview_a import check as CH
from claview_a import contract as K
from claview_a import graph as G
from claview_a.cards import build_cards, refs
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog

AS_OF = "2026-10-29"
V7, V8 = "tl-contract-2026-10-28.7", "tl-contract-2026-10-28.8"
TAIL = "예금은행 가계대출 금리를 신규취급액 기준으로 보여줘"
QUOTE = "예금은행 가계대출 금리"
TABLE = "DT_121Y006"
_CASES: dict = {}


def cards_for(question, layer, table=TABLE):
    if question not in _CASES:
        cards = build_cards(question, build_pool(question, layer), layer)
        assert table in refs(cards).values(), question
        card = next(c for c in cards if any(t["table_id"] == table for t in c["tables"]))
        _CASES[question] = (cards, card["name"])
    return _CASES[question]


def P(quote, sid="s1", meaning="range", form="absolute", start="none", end="none", count="none", unit="none",
      read="ok", refers_to=()):
    return {"quote": quote, "for": [sid] if isinstance(sid, str) else list(sid), "meaning": meaning,
            "refers_to": list(refers_to), "form": form, "start": start, "end": end, "count": count, "unit": unit,
            "read": read}


def F(quote, value, sid="s1", anchor="none"):
    return {"quote": quote, "for": [sid] if isinstance(sid, str) else list(sid), "value": value, "anchor": anchor}


def output(question, layer, periods, frequencies, contract=V8, stats=(("s1", QUOTE),), table=TABLE):
    cards, title = cards_for(question, layer, table)
    out = {key: [] for key in K.output_schema(contract)["properties"]}
    out.update(
        statistics=[{"id": sid, "quote": q, "name": q} for sid, q in stats],
        choices=[{"for": [sid for sid, _ in stats], "table": table, "evidence": [{"kind": "title", "text": title}]}],
        periods=periods, frequencies=frequencies,
        splitters=[{"for": [sid for sid, _ in stats], "quote": "신규취급액 기준", "axis": "loan_basis",
                    "value": "신규취급액", "polarity": "required"}] if "신규취급액 기준" in question else [],
        focuses=[{"for": [sid], "text": q, "quotes": [q]} for sid, q in stats],
    )
    return out


def checked(question, layer, out, contract=V8):
    cards, _ = cards_for(question, layer)
    parsed = K.parse_output(json.dumps(out, ensure_ascii=False), cards, contract)
    return CH.check(question, cards, parsed, selection_catalog(layer).views, AS_OF)


def final(d):
    return [(t["table_id"], t.get("frequency"), json.dumps(t.get("period"), sort_keys=True))
            for t in d.get("final_tables") or []]


def codes(d):
    return {r["code"] for r in d["reasons"]}


# ---------------------------------------------------------------- the contract
def test_contract_8_is_strict_and_structures_periods_and_frequencies():
    schema = K.output_schema(V8)
    period = schema["properties"]["periods"]["items"]
    frequency = schema["properties"]["frequencies"]["items"]
    assert period["required"] == ["quote", "for", "meaning", "refers_to", "form", "start", "end", "count", "unit",
                                  "read"] and period["additionalProperties"] is False
    assert period["properties"]["meaning"]["enum"] == list(K.SEMANTIC_PERIOD_MEANINGS)
    assert frequency["properties"]["value"]["enum"] == ["Y", "H", "Q", "M", "not_stated", "unreadable"]
    assert V7 in K.EARLIER_CONTRACTS and K.output_schema(V7)["properties"]["periods"]["items"]["required"] == [
        "quote", "for", "meaning", "refers_to"]  # the older answers keep their shape
    assert V8 in K.SEMANTIC_PERIOD_CONTRACTS and K.PROMPT_VERSION == "tl-prompt-2026-11-03.10"


# ---------------------------------------------------------------- same request, .7 quotes vs .8 rows
SAME = [
    ("2024년 월별", [P("2024년", start="2024", end="same")], [F("월별", "M")]),
    ("2023년 1월~2024년 3월 월별", [P("2023년 1월~2024년 3월", start="2023-01", end="2024-03")], [F("월별", "M")]),
    ("2024년 1분기 월별", [P("2024년 1분기", start="2024-Q1", end="same")], [F("월별", "M")]),
    ("2024년 하반기 월별", [P("2024년 하반기", start="2024-H2", end="same")], [F("월별", "M")]),
    ("최근 3년간 분기별", [P("최근 3년간", form="relative", count="3", unit="Y")], [F("분기별", "Q")]),
    ("최근 6개월 월별", [P("최근 6개월", form="relative", count="6", unit="M")], [F("월별", "M")]),
    ("2020년 이후 연간", [P("2020년 이후", start="2020", end="open")], [F("연간", "Y")]),
]


@pytest.mark.parametrize("words, periods, frequencies", SAME, ids=[s[0] for s in SAME])
def test_structured_rows_end_in_the_same_tables_and_periods_as_the_quotes(layer, words, periods, frequencies):
    question = f"{words} {TAIL}"
    old = output(question, layer, [{"quote": p["quote"], "for": p["for"], "meaning": "range", "refers_to": []}
                                   for p in periods],
                 [{"quote": f["quote"], "for": f["for"]} for f in frequencies], V7)
    d7 = checked(question, layer, old, V7)["decisions"][0]
    d8 = checked(question, layer, output(question, layer, periods, frequencies))["decisions"][0]
    assert d7["outcome"] == "selected" and d8["outcome"] == "selected", (codes(d7), codes(d8))
    assert final(d8) == final(d7)
    assert d8["frequency"]["value"] == d7["frequency"]["value"] and d8["frequency"]["source"] == "question"


def test_a_latest_value_reads_as_before(layer):
    question = f"최신 월별 {TAIL}"
    d7 = checked(question, layer, output(question, layer, [{"quote": "최신", "for": ["s1"], "meaning":
                                                           "latest_available", "refers_to": []}],
                                         [{"quote": "월별", "for": ["s1"]}], V7), V7)["decisions"][0]
    d8 = checked(question, layer, output(question, layer, [P("최신", meaning="latest_available", form="none")],
                                         [F("월별", "M")]))["decisions"][0]
    assert d8["outcome"] == d7["outcome"] and final(d8) == final(d7)
    assert d8["period_meaning"] == ["latest_available"]


# ---------------------------------------------------------------- the policies
def test_no_period_is_asked_never_defaulted(layer):
    question = f"월별 {TAIL}"
    d = checked(question, layer, output(question, layer, [P(QUOTE, meaning="not_stated", form="none")],
                                        [F("월별", "M")]))["decisions"][0]
    assert d["outcome"] == CH.CONDITION_OUTCOME and not d["final_tables"]
    assert [a["kind"] for a in d["condition_asks"]] == ["period"]
    assert d["failure_kinds"] == ["user_undecided"]


def test_a_whole_year_does_not_decide_the_yearly_frequency(layer):
    """The period of one whole year with no frequency said: the frequency the catalog cannot decide alone is asked
    (the table offers Y, Q and M), never 'yearly' because the period is a year."""
    question = f"2024년 한 해 {TAIL}"
    d = checked(question, layer, output(question, layer, [P("2024년 한 해", start="2024", end="same")],
                                        [F(QUOTE, "not_stated")]))["decisions"][0]
    assert d["outcome"] == CH.CONDITION_OUTCOME
    assert [a["kind"] for a in d["condition_asks"]] == ["frequency"]
    assert d["frequency"]["value"] is None and d["frequency"]["reason"] == "needs_answer"


def test_a_frequency_the_catalog_decides_alone_is_decided(layer):
    question = "2024년 한 해 기준 현금흐름표에서 제조업 영업활동 현금흐름을 보여줘"
    out = output(question, layer, [P("2024년 한 해", start="2024", end="same")],
                 [F("제조업 영업활동 현금흐름", "not_stated")], stats=(("s1", "제조업 영업활동 현금흐름"),),
                 table="DT_501Y013")
    cards, _ = cards_for(question, layer, "DT_501Y013")
    parsed = K.parse_output(json.dumps(out, ensure_ascii=False), cards, V8)
    d = CH.check(question, cards, parsed, selection_catalog(layer).views, AS_OF)["decisions"][0]
    assert d["frequency"]["value"] == "Y" and d["frequency"]["source"] == "catalog"
    assert d["frequency"]["reason"] == "only_valid"


def test_words_outside_the_quotes_are_recorded_never_read_on_the_new_path(layer):
    question = f"2024년 월별 {TAIL}"
    out = output(question, layer, [P(QUOTE, meaning="not_stated", form="none")], [F(QUOTE, "not_stated")])
    d = checked(question, layer, out)["decisions"][0]
    assert d["outcome"] == CH.CONDITION_OUTCOME  # nothing restored from the words: asked
    assert d["period_audit"]["status"] == "recorded_only" and d["period_audit"]["words_outside_quotes"]
    assert "period_quote_missing" not in d and "frequency_quote_missing" not in d
    old = output(question, layer, [], [], V7)
    d7 = checked(question, layer, old, V7)["decisions"][0]
    assert d7["period_quote_missing"]["restored"] is True  # the older path is kept as it was


# ---------------------------------------------------------------- the model's failures and the limits
FAILURES = [
    ("read_failed", [P("2024년 한 해", read="failed", form="none")], [F("월별", "M")], "period_structure_invalid"),
    ("end_before_start", [P("2024년", start="2024", end="2023")], [F("월별", "M")], "period_structure_invalid"),
    ("bad_value", [P("2024년", start="24년", end="same")], [F("월별", "M")], "period_structure_invalid"),
    ("count_zero", [P("2024년", form="relative", count="0", unit="Y")], [F("월별", "M")], "period_structure_invalid"),
    ("range_and_latest", [P("2024년", start="2024", end="same"), P("최신", meaning="latest_available", form="none")],
     [F("월별", "M")], "period_meaning_conflict"),
    ("not_stated_and_range", [P("2024년", start="2024", end="same"),
                              P(QUOTE, meaning="not_stated", form="none")], [F("월별", "M")],
     "period_meaning_conflict"),
    ("two_frequencies", [P("2024년", start="2024", end="same")], [F("월별", "M"), F("분기별", "Q")],
     "frequency_multiple_requested"),
    ("frequency_unreadable", [P("2024년", start="2024", end="same")], [F("월별", "unreadable")],
     "frequency_unreadable"),
    ("no_period_row", [], [F("월별", "M")], "period_row_missing"),
    ("no_frequency_row", [P("2024년", start="2024", end="same")], [], "frequency_row_missing"),
]


@pytest.mark.parametrize("name, periods, frequencies, code", FAILURES, ids=[f[0] for f in FAILURES])
def test_the_models_own_failure_is_a_correction_target(layer, name, periods, frequencies, code):
    question = f"2024년 한 해 2024년 최신 월별 분기별 {TAIL}"
    record = checked(question, layer, output(question, layer, periods, frequencies))
    d = record["decisions"][0]
    assert d["outcome"] == "failed" and code in codes(d)
    if name == "two_frequencies":
        # Both explicit requests remain intact: recovery cannot pick one and discard the other.
        assert d["period"]["cause"] == "unsupported_expression"
        assert d["failure_kinds"] == ["unsupported_expression"]
        assert G.rejudge_targets(record)["targets"] == []
        return
    assert d["period"]["cause"] == "interpretation_failed" and d["failure_kinds"] == ["internal_interpretation"]
    targets = G.rejudge_targets(record)["targets"]
    assert [(t["statistic"], t["kind"]) for t in targets] == [("s1", "correction")]


def test_an_excluded_period_stays_a_first_stage_limit_as_before(layer):
    question = f"2020~2024년 2022년 제외 월별 {TAIL}"
    out = output(question, layer, [P("2020~2024년", start="2020", end="2024"),
                                   P("2022년 제외", meaning="excluded", form="none")], [F("월별", "M")])
    record = checked(question, layer, out)
    d = record["decisions"][0]
    assert d["outcome"] == "failed" and "period_exclusion_unsupported" in codes(d)
    assert d["period"]["cause"] == "unsupported_expression"
    got = G.rejudge_targets(record)
    assert got["targets"] == [] and got["excluded"][0]["why"] == "unsupported_expression"
    # the same words on the older path: a part left unread, the same first-stage limit (never a default)
    old = output(question, layer, [{"quote": "2020~2024년 2022년 제외", "for": ["s1"], "meaning": "range",
                                    "refers_to": []}], [{"quote": "월별", "for": ["s1"]}], V7)
    d7 = checked(question, layer, old, V7)["decisions"][0]
    assert d7["outcome"] == "failed" and d7["period"]["cause"] == "unsupported_expression"


def test_same_period_reference_applies_to_the_linked_statistic_only(layer):
    question = f"2024년 월별 {TAIL}. 예금은행 가계대출 금리도 같은 기간으로"
    stats = (("s1", QUOTE), ("s2", "예금은행 가계대출 금리도"))
    out = output(question, layer, [P("2024년", start="2024", end="same"),
                                   P("같은 기간으로", sid="s2", meaning="same_as", form="none", refers_to=["s1"])],
                 [F("월별", "M", sid=["s1", "s2"])], stats=stats)
    ds = {d["statistic"]: d for d in checked(question, layer, out)["decisions"]}
    assert ds["s2"]["period_reference"]["status"] == "used"
    assert final(ds["s1"]) == final(ds["s2"]) and ds["s1"]["outcome"] == "selected"


# ---------------------------------------------------------------- the recovery: structured values kept
def first_and_second(layer, first_rows, second_rows, question=None):
    question = question or f"2024년 한 해 최근 3년 월별 분기별 {TAIL}"
    first = checked(question, layer, output(question, layer, *first_rows))
    second = checked(question, layer, output(question, layer, *second_rows))
    target = [{"statistic": "s1", "kind": "correction", "code": "period_structure_invalid"}]
    return first, second, G.merge_rounds(first, second, target)


def test_a_failed_row_is_corrected_and_the_correction_recorded(layer):
    first, second, merged = first_and_second(
        layer, ([P("2024년 한 해", read="failed", form="none")], [F("월별", "M")]),
        ([P("2024년 한 해", start="2024", end="same")], [F("월별", "M")]))
    assert merged["decisions"][0]["outcome"] == "selected" and merged["rejudge"]["rejected"] == []
    (fix,) = merged["rejudge"]["condition_corrections"]
    assert fix["quote"] == "2024년 한 해" and fix["first"][0]["read"] == "failed"
    assert fix["second"][0]["start"] == "2024" and "period_structure_invalid" in fix["why"]
    assert merged["rejudge"]["version"] == "tl-rejudge-2026-11-03.15"


CHANGED = [  # a valid row of the first round changed by the second: the target stays as it was
    ("relative_to_absolute", [P("최근 3년", form="relative", count="3", unit="Y")],
     [P("최근 3년", start="2023", end="2025")]),
    ("count", [P("최근 3년", form="relative", count="3", unit="Y")],
     [P("최근 3년", form="relative", count="2", unit="Y")]),
    ("unit", [P("최근 3년", form="relative", count="3", unit="Y")],
     [P("최근 3년", form="relative", count="3", unit="Q")]),
    ("not_stated_to_range", [P(QUOTE, meaning="not_stated", form="none")],
     [P("2024년 한 해", start="2024", end="same")]),
]


@pytest.mark.parametrize("name, valid, changed", CHANGED, ids=[c[0] for c in CHANGED])
def test_a_valid_period_condition_is_never_changed_by_the_recovery(layer, name, valid, changed):
    bad_frequency = [F("월별", "unreadable")]
    first, second, merged = first_and_second(layer, (valid, bad_frequency), (changed, [F("월별", "M")]))
    (rej,) = merged["rejudge"]["rejected"]
    assert rej["statistic"] == "s1" and rej["why"] == "condition_lost"
    assert merged["decisions"][0] == first["decisions"][0]


def test_a_valid_frequency_and_its_anchor_are_kept(layer):
    bad_period = [P("2024년 한 해", read="failed", form="none")]
    for changed in ([F("월별", "Q")], [F("월별", "M", anchor="end")], [F("월별", "not_stated")]):
        first, second, merged = first_and_second(layer, (bad_period, [F("월별", "M")]),
                                                 ([P("2024년 한 해", start="2024", end="same")], changed))
        assert merged["rejudge"]["rejected"][0]["why"] == "condition_lost", changed


def test_a_correction_cannot_add_another_condition(layer):
    first, second, merged = first_and_second(
        layer, ([P("2024년 한 해", read="failed", form="none")], [F("월별", "M")]),
        ([P("2024년 한 해", start="2024", end="same"), P("최근 3년", form="relative", count="3", unit="Y")],
         [F("월별", "M")]))
    (rej,) = merged["rejudge"]["rejected"]
    assert any(x.startswith("periods:added:") for x in rej["lost"])


def test_a_same_period_reference_is_kept(layer):
    question = f"2024년 월별 {TAIL}. 예금은행 가계대출 금리도 같은 기간으로"
    stats = (("s1", QUOTE), ("s2", "예금은행 가계대출 금리도"))
    rows = [P("2024년", start="2024", end="same"),
            P("같은 기간으로", sid="s2", meaning="same_as", form="none", refers_to=["s1"])]
    first = checked(question, layer, output(question, layer, rows, [F("월별", "M", sid="s1"),
                                                                     F("월별", "unreadable", sid="s2")], stats=stats))
    moved = copy.deepcopy(rows)
    moved[1] = P("같은 기간으로", sid="s2", start="2024", end="same")  # the reference dropped for a range
    second = checked(question, layer, output(question, layer, moved, [F("월별", "M", sid=["s1", "s2"])],
                                             stats=stats))
    merged = G.merge_rounds(first, second, [{"statistic": "s2", "kind": "correction", "code": "frequency_unreadable"}])
    assert merged["rejudge"]["rejected"][0]["why"] == "condition_lost"


def test_an_older_answer_keeps_the_quote_comparison(layer):
    question = f"2024년 월별 {TAIL}"
    old = output(question, layer, [{"quote": "2024년", "for": ["s1"], "meaning": "range", "refers_to": []}],
                 [{"quote": "월별", "for": ["s1"]}], V7)
    first = checked(question, layer, old, V7)
    second_out = copy.deepcopy(old)
    second_out["periods"] = []
    second = checked(question, layer, second_out, V7)
    merged = G.merge_rounds(first, second, [{"statistic": "s1", "kind": "correction", "code": "focus_too_long"}])
    assert merged["rejudge"]["rejected"][0]["lost"] == ["periods:2024년"]


# ---------------------------------------------------------------- one recovery in all
class Judge:
    answer_contract = V8

    def __init__(self, *texts):
        self.texts, self.payloads = texts, []

    def __call__(self, payload, request):
        self.payloads.append(payload)
        return self.texts[min(len(self.payloads), len(self.texts)) - 1]


def _run(layer, question, *outs):
    judge = Judge(*[o if isinstance(o, str) else json.dumps(o, ensure_ascii=False) for o in outs])
    state = G.run(question, layer, judge, K.Settings("fake"), AS_OF, research=True)
    return state, judge


def test_a_period_failure_and_a_focus_failure_share_the_one_recovery(layer):
    question = f"2024년 한 해 월별 {TAIL}"
    bad = output(question, layer, [P("2024년 한 해", read="failed", form="none")], [F("월별", "M")])
    bad["focuses"][0]["text"] = "가" * 101
    good = output(question, layer, [P("2024년 한 해", start="2024", end="same")], [F("월별", "M")])
    state, judge = _run(layer, question, bad, good)
    assert len(judge.payloads) == 2
    target = judge.payloads[1]["targets"][0]
    assert target["conditions"]["periods"][0]["read"] == "failed"
    assert target["condition_problems"][0]["code"] == "period_structure_invalid"
    assert state["record"]["decisions"][0]["outcome"] == "selected"
    assert state["record"]["rejudge"]["condition_corrections"][0]["quote"] == "2024년 한 해"


def test_a_failed_recovery_ends_with_no_third_judgment(layer):
    question = f"2024년 한 해 월별 {TAIL}"
    bad = output(question, layer, [P("2024년 한 해", read="failed", form="none")], [F("월별", "M")])
    state, judge = _run(layer, question, bad, bad, bad)
    assert len(judge.payloads) == 2
    d = state["record"]["decisions"][0]
    assert d["outcome"] == "failed" and "period_structure_invalid" in codes(d)


def test_a_first_answer_that_breaks_the_contract_takes_the_same_one_recovery(layer):
    question = f"2024년 월별 {TAIL}"
    broken = output(question, layer, [P("2024년", start="2024", end="same")], [F("월별", "M")])
    broken["periods"][0].pop("read")  # not the contract's shape: the whole answer is unreadable
    good = output(question, layer, [P("2024년", start="2024", end="same")], [F("월별", "M")])
    state, judge = _run(layer, question, broken, good)
    assert len(judge.payloads) == 2 and state["record"]["decisions"][0]["outcome"] == "selected"
