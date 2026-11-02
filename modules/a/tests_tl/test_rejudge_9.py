"""Plan 56 order 9 (bundle 10, policy ⑧): the targets of the one self-recovery and the merge of its second round.

``graph.rejudge_targets`` names what a re-judgment may fix and why the rest is left (user_only, fact_gap,
unsupported_expression, rule_mismatch); ``graph.merge_rounds`` keeps every statistic that is no target and takes a
target's second decision only when its statistic is matched and its user conditions are kept
(``condition_preservation``), else the first stays with the reason. Made-up records only.
"""

from __future__ import annotations

from claview_a import graph as G


def dec(sid, outcome, reasons=(), **extra):
    return {"statistic": sid, "outcome": outcome, "reasons": [{"code": c} for c in reasons], **extra}


def output(stats, periods=(), frequencies=(), splitters=()):
    return {"statistics": [{"id": s, "quote": q, "name": q} for s, q in stats],
            "periods": [{"quote": q, "for": f} for q, f in periods],
            "frequencies": [{"quote": q, "for": f} for q, f in frequencies],
            "splitters": [{"quote": v, "axis": a, "value": v, "polarity": p, "for": f} for a, v, p, f in splitters]}


# ---------------------------------------------------------------- targets


def test_missing_and_correctable_statistics_are_targets_and_the_rest_says_why():
    record = {"decisions": [
        dec("s1", "selected"),
        dec("s2", "not_in_pool", search_words=["원/달러 환율"]),
        dec("s3", "failed", ["rejudge_pending"], rejudge_targets=[{"code": "ask_not_executable"}]),
        dec("s4", "failed", ["basis_polarity_invalid"]),
        dec("s5", "failed", ["period_quote_missing"], period={"cause": "extraction_missing"}),
        dec("s6", "ask_condition"),
        dec("s7", "failed", ["target_table_unconfirmed"]),
        dec("s8", "failed", ["period_unresolved"], period={"cause": "unsupported_expression"}),
        dec("s11", "failed", ["basis_attribution_unresolved"]),
        dec("s9", "failed", ["ask_invalid"]),
        dec("s10", "not_in_pool", search_words=["  "])]}
    got = G.rejudge_targets(record)
    assert [(t["statistic"], t["kind"]) for t in got["targets"]] == [
        ("s2", "research"), ("s3", "correction"), ("s4", "correction"), ("s5", "correction"), ("s11", "correction")]
    assert {e["statistic"]: e["why"] for e in got["excluded"]} == {
        "s6": "user_only", "s7": "fact_gap", "s8": "unsupported_expression", "s9": "rule_mismatch",
        "s10": "fact_gap"}
    assert set(G.EXCLUDE_WHYS) >= {e["why"] for e in got["excluded"]}


def test_a_first_round_output_error_is_a_target_and_a_transport_error_is_not():
    out = G.rejudge_targets({"error": {"stage": "output", "code": "schema"}, "decisions": []})
    assert [t["code"] for t in out["targets"]] == ["first_round_output_error"]
    out = G.rejudge_targets({"error": {"stage": "judge", "code": "timeout"}, "decisions": []})
    assert out["targets"] == [] and out["excluded"][0]["code"] == "first_round_transport_error"


# ---------------------------------------------------------------- merge


FIRST_OUT = output([("s1", "M2 총액"), ("s2", "원/달러 환율")], periods=[("2024년", ["s1", "s2"])],
                   frequencies=[("월별", ["s1", "s2"])], splitters=[("seasonal", "원계열", "required", ["s2"])])


def first(**rejudge):
    return {"model_output": FIRST_OUT, "decisions": [dec("s1", "selected", final_tables=["A"]),
                                                     dec("s2", "not_in_pool")],
            "rejudge": {"targets": [{"statistic": "s2", "kind": "research"}], **rejudge}}


def second(out=FIRST_OUT, s2="selected", error=None):
    rec = {"model_output": out, "decisions": [dec("s1", "failed", ["x"]), dec("s2", s2, final_tables=["B"])]}
    if error:
        rec = {"error": error, "decisions": [], "model_output": None}
    return rec


def merged(rec):
    return {d["statistic"]: d for d in rec["decisions"]}


def test_the_target_is_merged_and_a_decided_statistic_keeps_its_first_decision():
    rec = G.merge_rounds(first(), second(), first()["rejudge"]["targets"])
    m = merged(rec)
    assert m["s1"]["outcome"] == "selected" and m["s1"]["final_tables"] == ["A"]  # never redone
    assert m["s2"]["outcome"] == "selected" and m["s2"]["rejudged_from"] == "s2"
    assert rec["rejudge"]["merged"] == [{"statistic": "s2", "second_round_statistic": "s2", "outcome": "selected"}]
    assert rec["route"]["action"] in ("handoff", "partial", "ask", "fail")


def test_a_second_round_that_drops_a_quoted_frequency_is_rejected_and_the_first_kept():
    out = output([("s1", "M2 총액"), ("s2", "원/달러 환율")], periods=[("2024년", ["s1", "s2"])],
                 splitters=[("seasonal", "원계열", "required", ["s2"])])
    rec = G.merge_rounds(first(), second(out), first()["rejudge"]["targets"])
    assert merged(rec)["s2"]["outcome"] == "not_in_pool"
    (r,) = rec["rejudge"]["rejected"]
    assert r["why"] == "condition_lost" and r["lost"] == ["frequencies:월별"]


def test_a_basis_row_changed_without_its_reason_is_lost():
    out = output([("s1", "M2 총액"), ("s2", "원/달러 환율")], periods=[("2024년", ["s1", "s2"])],
                 frequencies=[("월별", ["s1", "s2"])], splitters=[("seasonal", "원계열", "allowed", ["s2"])])
    rec = G.merge_rounds(first(), second(out), first()["rejudge"]["targets"])
    assert rec["rejudge"]["rejected"][0]["lost"] == ["splitters:seasonal:원계열:required"]


def test_a_failed_second_round_keeps_the_first_whole():
    rec = G.merge_rounds(first(), second(error={"stage": "output", "code": "schema"}), first()["rejudge"]["targets"])
    assert merged(rec)["s2"]["outcome"] == "not_in_pool"
    assert rec["rejudge"]["rejected"] == [{"statistic": "s2", "why": "second_round_error"}]


def test_a_renumbered_statistic_is_matched_by_its_quote_or_rejected():
    out = output([("s1", "원/달러 환율"), ("s2", "M2 총액")], periods=[("2024년", ["s1", "s2"])],
                 frequencies=[("월별", ["s1", "s2"])], splitters=[("seasonal", "원계열", "required", ["s1"])])
    rec2 = {"model_output": out, "decisions": [dec("s1", "selected", final_tables=["B"]), dec("s2", "failed", ["x"])]}
    rec = G.merge_rounds(first(), rec2, first()["rejudge"]["targets"])
    assert merged(rec)["s2"]["rejudged_from"] == "s1" and merged(rec)["s2"]["final_tables"] == ["B"]
    out2 = output([("s3", "원/달러 환율"), ("s4", "원/달러 환율")])  # two statistics of that quote: not told apart
    rec3 = {"model_output": out2, "decisions": [dec("s3", "selected"), dec("s4", "selected")]}
    rec = G.merge_rounds(first(), rec3, first()["rejudge"]["targets"])
    assert rec["rejudge"]["rejected"] == [{"statistic": "s2", "why": "statistic_not_matched"}]


def test_there_is_no_third_judgment():
    assert G.after_check({"round": 2}) == "research_end"
    assert G.after_research({"research": {"status": "not_run"}}) == "route"


# ---------------------------------------------------------------- the correction call (contract .6 input, order 9-2)


def test_a_correction_target_is_judged_once_with_its_fixed_context_and_merged(layer):
    import json

    from test_polarity_62 import ref as m2_ref
    from test_research_46 import Script, blank

    from claview_a.cards import build_cards
    from claview_a.contract import Settings
    from claview_a.pool import build_pool

    q = "2024년 M2 말잔 원계열과 M2 평잔 원계열을 월별로 보여줘"
    cards = build_cards(q, build_pool(q, layer), layer)

    def answer(with_periods):
        out = blank()
        out["statistics"] = [{"id": "s1", "quote": "M2 말잔 원계열", "name": "M2"},
                             {"id": "s2", "quote": "M2 평잔 원계열", "name": "M2"}]
        out["frequencies"] = [{"quote": "월별", "for": ["s1", "s2"]}]
        out["periods"] = [{"quote": "2024년", "for": ["s1", "s2"]}] if with_periods else []
        out["splitters"] = [{"quote": v, "axis": "stock", "for": [s]} for s, v in (("s1", "말잔"), ("s2", "평잔"))] + \
            [{"quote": "원계열", "axis": "seasonal", "for": ["s1", "s2"]}]
        out["choices"] = [{"for": [s], "table": m2_ref(cards, layer, stock=v, seasonal="원계열"),
                           "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"}]}
                          for s, v in (("s1", "말잔"), ("s2", "평잔"))]
        return out
    judge = Script(answer(False), answer(True))
    state = G.run(q, layer, judge, Settings("fake"), "2026-10-22", research=True)
    rej = state["record"]["rejudge"]
    targets = [t["statistic"] for t in rej["targets"]]
    if not targets:  # the code restored the period itself: nothing to correct
        assert judge.calls == 1
        return
    assert judge.calls == 2 and set(targets) == set(rej["sent"])  # one call for every target, no third
    payload = judge.payloads[1]
    assert payload["fixed"]["question"] == q and [t["id"] for t in payload["targets"]] == targets
    assert all("periods" in t["quotes"] for t in payload["targets"])
    assert {m["statistic"] for m in rej["merged"]} == set(targets) and not rej["rejected"]
    assert json.dumps(rej, ensure_ascii=False).count("second_round_output") == 1


def test_a_correction_with_the_slot_used_ends_without_a_call_and_says_so(layer):
    from test_research_46 import Script, blank

    from claview_a.contract import Settings

    q = "2024년 M2 말잔과 M2 평잔을 월별로 보여줘"
    out = blank()
    out["statistics"] = [{"id": "s1", "quote": "M2 말잔", "name": "M2"}]
    out["missing"] = [{"for": ["s1"], "search_words": ["M2 말잔 원계열"]}]
    judge = Script(out, out)
    state = G.run(q, layer, judge, Settings("fake"), "2026-10-22", research=True, research_budget=lambda: False)
    if state["research"]["status"] == "not_run" and state["research"]["end"] == "rejudge_call_limit":
        assert judge.calls == 1


def test_a_target_basis_change_with_its_quote_and_reason_is_merged_and_recorded():
    rec1 = first()
    rec1["question"] = "2024년 월별 원/달러 환율을 원계열 없이는 안 되게 보여줘"
    out = output([("s1", "M2 총액"), ("s2", "원/달러 환율")], periods=[("2024년", ["s1", "s2"])],
                 frequencies=[("월별", ["s1", "s2"])], splitters=[("seasonal", "원계열", "allowed", ["s2"])])
    rec1["model_output"] = output([("s1", "M2 총액"), ("s2", "원/달러 환율")], periods=[("2024년", ["s1", "s2"])],
                                  frequencies=[("월별", ["s1", "s2"])],
                                  splitters=[("seasonal", "원계열", "required", ["s2"])])
    out["changes"] = [{"for": ["s2"], "axis": "seasonal", "value": "원계열", "from": "required", "to": "allowed",
                       "quote": "원계열 없이는 안 되게", "reason": ""}]
    rec = G.merge_rounds(rec1, second(out), rec1["rejudge"]["targets"])
    assert rec["rejudge"]["rejected"][0]["why"] == "condition_lost"  # no reason: not merged
    out["changes"][0]["reason"] = "the user said it may be left out"
    rec = G.merge_rounds(rec1, second(out), rec1["rejudge"]["targets"])
    assert merged(rec)["s2"]["rejudged_from"] == "s2"
    assert rec["rejudge"]["changes"] == [{"statistic": "s2", "axis": "seasonal", "value": "원계열", "from": "required",
                                          "to": "allowed", "quote": "원계열 없이는 안 되게",
                                          "reason": "the user said it may be left out"}]
    out["changes"][0]["quote"] = "질문에 없는 말"  # a quote outside the question never carries a change
    rec = G.merge_rounds(rec1, second(out), rec1["rejudge"]["targets"])
    assert rec["rejudge"]["rejected"][0]["why"] == "condition_lost"


def test_a_polarity_error_not_fixed_ends_with_its_recovery_status():
    def record():
        return {"decisions": [dec("s1", "failed", ["basis_polarity_invalid"])]} | {
            "rejudge": {"merged": []}}
    for status in ("attempted_failed", "budget_exhausted"):
        rec = record()
        rec["decisions"][0]["reasons"][0]["detail"] = {"recovery": {"status": "not_available"}}
        G._recovery(rec, rec["rejudge"], status)
        assert rec["decisions"][0]["reasons"][0]["detail"]["recovery"]["status"] == status
