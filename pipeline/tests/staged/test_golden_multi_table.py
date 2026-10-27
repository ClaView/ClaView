# ruff: noqa: E501
"""Multi-table / dual-axis golden set (v3): data checks and an offline replay of every case.

A is replaced by the golden tables, B's model by the stored golden answers and KOSIS by SYNTHETIC
values (`golden_replay`). This checks the wiring from B's validation through alignment to the
dual-axis card, not statistics. The `check` command of `golden_replay.py` runs the same cases.
"""

from __future__ import annotations

import json

import golden_replay as g
import pytest
from jsonschema import Draft202012Validator

from claview_b.metadata import selected_unit

CASES = g.load_cases()
SCHEMA = g.GOLDEN.with_suffix(".schema.json")


def test_golden_file_matches_its_schema():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(g.GOLDEN.read_text(encoding="utf-8")))
    assert [c["case_id"] for c in CASES] == [f"CV-BC-0{i}" for i in range(1, 6)]


def test_schema_rejects_a_case_without_alignment_expectation():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    broken = json.loads(g.GOLDEN.read_text(encoding="utf-8"))
    del broken["cases"][0]["expected"]["alignment"]
    assert list(Draft202012Validator(schema).iter_errors(broken))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_tables_and_codes_are_the_registered_ones(case, base):
    """Table names equal the KOSIS table names of the catalog; item/classification codes and units exist there."""
    store = base.store
    expected = case["expected"]
    assert len(expected["A"]["tables"]) >= 2
    for table in expected["A"]["tables"]:
        meta = store.get(table["org_id"], table["table_id"])
        assert table["table_name"] == meta["table_name"]
    assert {s["table_id"] for s in expected["B"]["selections"]} == {
        t["table_id"] for t in expected["A"]["tables"]
    }
    for s in expected["B"]["selections"]:
        meta = store.get(s["org_id"], s["table_id"])
        assert s["item_id"] in meta["items"]
        assert s["period"]["frequency"] in meta["periods"]
        for axis, dim in s["dimensions"].items():
            value = meta["dimensions"][axis]["values"][dim["code"]]
            assert dim["name"] == value["name"]
        codes = {axis: d["code"] for axis, d in s["dimensions"].items()}
        assert selected_unit(meta, s["item_id"], codes) in {s["unit"], None}


@pytest.fixture(scope="module")
def replay(base):
    """One replay pipeline for the module (A -> golden tables, B -> golden answers, KOSIS -> synthetic)."""
    with g.patched_pipeline(CASES) as (cp, recorder):
        synthetic = g.ReplayFetchClient(CASES, g.load_replay_values())
        yield cp, recorder, g.make_runner(cp, CASES, "replay", {}, synthetic=synthetic), synthetic


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_replay_reproduces_the_expected_alignment_and_card(replay, case):
    cp, recorder, runner, _ = replay
    row = g.run_case(cp, recorder, runner, case)
    assert row["problems"] == []
    assert row["final"]["synthetic_replay"] is True
    assert row["card"]["notices"][0] == g.SYNTHETIC_NOTICE


def test_scale_conversion_is_exact_and_raw_values_are_kept(replay):
    cp, recorder, runner, _ = replay
    case = next(c for c in CASES if c["case_id"] == "CV-BC-03")
    row = g.run_case(cp, recorder, runner, case)
    first, second = row["card"]["series"]
    # The 십억원 series was generated as the 백만원 series / 1000, so after conversion both lines coincide.
    assert [float(v) for v in first["values"]] == [float(v) for v in second["values"]]
    block = row["blocks"][-1]
    assert block["unit_conversions"][0]["factor"] == "1000"
    converted = next(
        s
        for s in row["final"]["chart_results"][0]["source"]
        if s["series_id"] == block["unit_conversions"][0]["series_id"]
    )
    assert (converted["source_unit"], converted["unit"]) == ("십억원", "백만원")


def test_frequency_answer_changes_values_only_through_the_chosen_method(replay):
    cp, recorder, runner, synthetic = replay
    case = next(c for c in CASES if c["case_id"] == "CV-BC-02")
    paused = runner.run(case["query"], as_of=g.AS_OF)
    assert (
        paused["status"] == "needs_confirmation" and paused["confirmation"]["kind"] == "alignment"
    )
    calls = len(synthetic.calls)
    group = paused["confirmation"]["groups"][0]
    by_method = {}
    for option in ("freq_sum", "freq_mean", "freq_last"):
        answer = {
            "choices": [{"requirement_id": group["requirement_id"], "option_id": option}],
            "interpretation_confirmed": False,
        }
        done = runner.resume(paused, answer)
        assert done["status"] in {"ok", "partial"}
        by_method[option] = done["ui_cases"][0]["series"][0]["values"]
    assert len(synthetic.calls) == calls  # answering needs no new fetch
    assert len(by_method["freq_sum"]) == 12  # 2022Q1 ~ 2024Q4
    assert by_method["freq_sum"] != by_method["freq_mean"] != by_method["freq_last"]
    # a quarter's sum is three times its mean (monthly series, complete quarters)
    assert all(
        abs(float(s) - 3 * float(m)) < 1e-3
        for s, m in zip(by_method["freq_sum"], by_method["freq_mean"], strict=True)
    )


def test_a_different_b_selection_is_reported_with_both_sides(replay):
    import copy

    cp, recorder, runner, _ = replay
    case = copy.deepcopy(next(c for c in CASES if c["case_id"] == "CV-BC-05"))
    # The replay model still answers with the stored golden item; the expectation now asks for another one.
    case["expected"]["B"]["selections"][1]["dimensions"]["objL1"] = {
        "code": "13102134588ACC_ITEM.BEABAA2118",
        "name": "정기예금(1년)",
    }
    problems = g.run_case(cp, recorder, runner, case)["problems"]
    assert any(
        p.startswith("B 선택 차이") and "BEABAA2118" in p and "BEABAA211]" in p for p in problems
    )


def test_a_stopped_b_call_is_reported_as_not_comparable(replay):
    from claview_b.contracts import StageFailure

    class Limited:
        calls = 0

        def complete(self, *args, **kwargs):
            self.calls += 1
            raise StageFailure("hcx_http_429")

    cp, recorder, runner, _ = replay
    original = runner.model
    runner.model = Limited()
    try:
        problems = g.run_case(cp, recorder, runner, CASES[0])["problems"]
    finally:
        runner.model = original
    assert len(problems) == 1
    assert "비교하지 못함" in problems[0] and "hcx_http_429" in problems[0]


def test_trailing_period_and_spacing_do_not_matter(replay):
    _, _, runner, _ = replay
    query = CASES[2]["query"].rstrip(".").replace(" ", "  ")
    state = runner.run(query, as_of=g.AS_OF)
    assert state["status"] in {"ok", "partial"} and state["ui_cases"]


def test_a_question_outside_the_golden_set_stops_at_a(replay):
    _, _, runner, _ = replay
    state = runner.run("2024년 월별 소비자물가 보여줘", as_of=g.AS_OF)
    assert state["status"] == "unresolved" and state["error_code"] == "a_no_table"
    assert state["message"] == g.NOT_GOLDEN_MESSAGE


def test_hcx_mode_builds_the_model_with_the_requested_retries(replay):
    cp, _, _, _ = replay
    for retries in (0, 2):
        runner = g.make_runner(
            cp, CASES, "hcx", {"hcx": "dummy", "kosis": "dummy"}, hcx_retries=retries
        )
        assert runner.model.retries == retries
        assert runner.model.min_interval == 35
        runner.model.close()


def test_a_429_is_retried_after_the_reported_reset_time(monkeypatch):
    import httpx

    from claview_b.models import HcxModel

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429, json={"status": {"code": "42901"}}, headers={"x-ratelimit-reset-tokens": "7s"}
            )
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {"finishReason": "stop", "message": {"content": '{"ok": true}'}},
            },
        )

    class Trace:
        def __init__(self):
            self.events = []

        def add(self, event, **fields):
            self.events.append((event, fields))

    slept = []
    monkeypatch.setattr("claview_b.models.time.sleep", slept.append)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    trace = Trace()
    value = HcxModel("dummy", retries=1, min_interval=0, client=client).complete(
        "t", "s", {}, {"type": "object"}, 10, trace
    )
    assert value == {"ok": True}
    assert len(calls) == 2
    assert slept == [7.0]
    statuses = [f["http_status"] for e, f in trace.events if e == "transport_response"]
    assert statuses == [429, 200]


def test_no_golden_question_triggers_a_breakdown():
    """A table name such as "지급수단별 통계" in a question once switched B's `expand` on (CV-BC-03)."""
    from claview_b.stages import asks_for_breakdown

    assert [c["case_id"] for c in CASES if asks_for_breakdown(c["query"])] == []


def test_series_added_by_b_are_a_separate_category_not_a_mismatch(replay):
    import copy

    cp, recorder, runner, _ = replay
    case = copy.deepcopy(next(c for c in CASES if c["case_id"] == "CV-BC-05"))
    case["expected"]["B"]["selections"].pop()  # B still answers with both golden rows
    row = g.run_case(cp, recorder, runner, case)
    assert row["problems"] == [] and row["missing"] == []
    assert len(row["extra"]) == 1
    assert g.verdict_of(row) == "골든 계열 포함·추가 계열 1개"
    assert any(n.startswith("추가 선택 1개") for n in row["notes"])
    assert any(n.startswith("계열 수") and "추가 계열의 영향" in n for n in row["notes"])


def test_a_missing_golden_series_stays_a_mismatch_even_with_added_series(replay):
    import copy

    cp, recorder, runner, _ = replay
    case = copy.deepcopy(next(c for c in CASES if c["case_id"] == "CV-BC-05"))
    sel = case["expected"]["B"]["selections"]
    sel[1]["dimensions"]["objL1"] = {
        "code": "13102134588ACC_ITEM.BEABAA2118",
        "name": "정기예금(1년)",
    }
    row = g.run_case(cp, recorder, runner, case)
    assert row["missing"] and row["extra"] and g.verdict_of(row) == "불일치"
    assert any("계열 수" in p for p in row["problems"]) is False  # same count, so no count problem


def test_card_differences_caused_by_added_series_are_notes_too(replay):
    import copy

    cp, recorder, runner, _ = replay
    case = copy.deepcopy(next(c for c in CASES if c["case_id"] == "CV-BC-01"))
    case["expected"]["B"]["selections"].pop()  # B still returns both golden series: one is "added"
    case["expected"]["C"]["default_view"] = (
        "line"  # a card difference that follows from the series set
    )
    row = g.run_case(cp, recorder, runner, case)
    assert row["problems"] == [] and row["extra"]
    assert any(n.startswith("기본 보기") and "추가 계열의 영향" in n for n in row["notes"])


def test_every_golden_table_names_its_indicator_as_worded_in_the_question():
    for case in CASES:
        for table in case["expected"]["A"]["tables"]:
            assert table["indicator"] in case["query"], (case["case_id"], table["table_id"])


def test_golden_tables_reach_b_with_focus_only_when_asked(base):
    from claview_b.contracts import StageFailure

    class Recorder:
        def __init__(self):
            self.contexts = []

        def complete(self, task, system, context, schema, max_tokens, trace):
            self.contexts.append((task, system, context))
            raise StageFailure("stop_after_recording")

    case = CASES[4]
    for focus in (False, True):
        recorder = Recorder()
        with g.patched_pipeline(CASES, table_focus=focus) as (cp, _):
            runner = cp.Pipeline(
                None, None, model=recorder, client=None, runs_dir=None, session=base
            )
            runner.run(case["query"], as_of=g.AS_OF)
        assert len(recorder.contexts) >= 1
        task, system, context = recorder.contexts[0]
        table = case["expected"]["A"]["tables"][0]
        assert task == f"B:{table['org_id']}:{table['table_id']}"
        if focus:
            assert context["table_focus"] == table["indicator"]
        else:
            assert "table_focus" not in context
        assert all("focus" not in t for t in context["selected_tables"])


def test_series_count_covers_every_card_and_a_split_into_cards_is_reported(replay):
    """With three unit kinds C shows one card per series; the count must not read only card 0."""
    import copy

    cp, recorder, runner, _ = replay
    case = copy.deepcopy(next(c for c in CASES if c["case_id"] == "CV-BC-04"))
    state = {"ui_cases": [{"series": [1]}, {"series": [2]}, {"series": [3]}]}
    # a stand-in for the pipeline result: only the card/series part of run_case is of interest here
    original = runner.run
    try:
        runner.run = lambda *a, **k: {**original(*a, **k), **state}
        row = g.run_case(cp, recorder, runner, case)
    finally:
        runner.run = original
    text = " | ".join(row["problems"] + row["notes"])
    assert "카드 수: 기대 1 / 실제 3" in text and "계열 수: 기대 2 / 실제 3" in text
