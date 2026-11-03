"""Per-table periods through the REAL group-by-group B calls of the screen path (SYNTHETIC model and fetch).

`ScreenPipeline._run_b` sends one `b.resolve` per requirement group (one table each), so a table never sees the other
tables of the question. These cases run that path: scripted A judges (two or three statistics, each with its own
period quote), the real BService, the synthetic fetch client. The cases and their expected values
(`eval/cases/b/period/table_period_pipeline_calls_b.json`) are the per-table intent of the question and were fixed
before the code was changed.

A's actual handoff is used without bypasses. Only B instances with prepared period conditions and the capability
to scope other groups' period wording may receive different ranges; legacy receivers remain protected.

`python pipeline/tests/staged/test_screen_table_period_calls_1103.py` prints the record of every case:
question -> calls (tables, received period, wording handed over) -> B request range -> read range -> fetch parameters.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_screen_54 import RecordingB

from claview_a_api import screen as a_screen
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_pipeline.screen import ScreenPipeline
from claview_pipeline.synthetic import SyntheticFetchClient, SyntheticSelectionModel

DATA = Path(__file__).resolve().parents[3] / "eval/cases/b/period/table_period_pipeline_calls_b.json"
CASES = json.loads(DATA.read_text(encoding="utf-8"))["cases"]


class ContextB(RecordingB):
    """B's real service over the synthetic model; it takes `period_context` and records every call it received."""

    accepts_period_condition = True
    accepts_period_context = True

    def resolve(self, query, tables, as_of=None, **extra):
        type(self).calls.append({"query": query, "tables": tables, **extra})
        return self.inner.resolve(query, tables, as_of, **{k: v for k, v in extra.items() if k == "period_context"})


class WithoutContextB(ContextB):
    """The same B before the change: no `period_context` (the pipeline must not send it)."""

    accepts_period_context = False

    def resolve(self, query, tables, as_of=None, **extra):
        assert "period_context" not in extra
        type(self).calls.append({"query": query, "tables": tables, **extra})
        return self.inner.resolve(query, tables, as_of)


def make(base, query, script, b=ContextB, client=None):
    judge = ScriptJudge(by_question({query: script}))
    b.calls = []
    return ScreenPipeline(
        model=SyntheticSelectionModel(base.store),
        client=client or SyntheticFetchClient(),
        runs_dir=None,
        session=base,
        judges_factory=lambda: a_screen.Judges(judge, judge, "script"),
        b_factory=b,
    )


def record(base, case, b=ContextB):
    """What happened to one question: the calls B got, what it applied and read, what KOSIS would be asked."""
    client = SyntheticFetchClient()
    state = make(base, case["query"], case["script"], b, client).run(case["query"], as_of=case["as_of"])
    rows = {}
    for outcome in state["stage_outcomes"]["B"]:
        for row in (outcome.get("result") or {}).get("period_resolutions") or []:
            rows[row["table_id"]] = {
                "requested": [row["frequency"], row["requested"][0]["start"], row["requested"][0]["end"]],
                "fetched": [row["frequency"], row["fetched"][0]["start"], row["fetched"][0]["end"]],
                "extra": [row["frequency"], row["calculation_extra"][0]["start"], row["calculation_extra"][0]["end"]]
                if row["calculation_extra"]
                else None,
                "sources": row["applied_sources"],
            }
    for params in client.calls:
        rows.setdefault(params["tblId"], {})["fetch"] = [params["prdSe"], params["startPrdDe"], params["endPrdDe"]]
    calls = [
        {
            "tables": [
                {
                    "table_id": t["table_id"],
                    "period": (t.get("period_condition") or {}).get("period"),
                    "phrase": (t.get("period_condition") or {}).get("period_phrase"),
                }
                for t in call["tables"]
            ],
            "period_context": call.get("period_context"),
        }
        for call in b.calls
    ]
    return {"id": case.get("id"), "query": case["query"], "status": state["status"], "calls": calls, "tables": rows,
            "state": state}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_each_group_call_reads_its_own_period(base, case):
    got = record(base, case)
    assert got["status"] == ("partial" if case.get("partial") else "ok"), got["state"].get("message")
    assert len(got["calls"]) == len(case["expect"])  # one B call per table: the real screen-path structure
    assert all(len(c["tables"]) == 1 for c in got["calls"])
    for table_id, expected in case["expect"].items():
        for field, value in expected.items():
            assert got["tables"][table_id].get(field) == value, f"{case['id']} {table_id} {field}"


def test_the_other_groups_wording_is_handed_over_and_nothing_else(base):
    case = next(c for c in CASES if c["id"] == "PC-01")
    got = record(base, case)
    first, second = got["calls"]
    assert first["tables"][0]["phrase"] == "2023년" and second["tables"][0]["phrase"] == "2024년"
    assert first["period_context"] == [{"ref": "DT_121Y006", "period_phrase": "2024년"}]
    assert second["period_context"] == [{"ref": "DT_161Y014", "period_phrase": "2023년"}]


def test_a_b_without_context_cannot_receive_different_ranges(base):
    case = next(c for c in CASES if c["id"] == "PC-01")
    state = make(base, case["query"], case["script"], WithoutContextB).run(case["query"], as_of=case["as_of"])
    assert state["status"] == "unsupported" and WithoutContextB.calls == []
    assert {r["code"] for r in state["unsupported_links"]} == {"period_differs_between_requests"}
    assert state["calls"]["KOSIS"] == 0


def test_a_b_without_period_conditions_cannot_receive_different_ranges(base):
    class ContextOnlyB(ContextB):
        accepts_period_condition = False

    case = next(c for c in CASES if c["id"] == "PC-01")
    state = make(base, case["query"], case["script"], ContextOnlyB).run(case["query"], as_of=case["as_of"])
    assert state["status"] == "unsupported" and ContextOnlyB.calls == []
    assert {r["code"] for r in state["unsupported_links"]} == {"period_differs_between_requests"}


def test_failed_preparation_never_falls_back_to_unscoped_different_ranges(base, monkeypatch):
    from claview_a_api import view

    prepare = view.prepare_b_request

    def missing_one(view_state, request):
        if request["statistics"] == ["s1"]:
            return {"request": None, "withheld": "period_condition_invalid"}
        return prepare(view_state, request)

    monkeypatch.setattr(view, "prepare_b_request", missing_one)
    case = next(c for c in CASES if c["id"] == "PC-01")
    state = make(base, case["query"], case["script"]).run(case["query"], as_of=case["as_of"])
    assert state["status"] == "unsupported" and ContextB.calls == []
    assert {r["code"] for r in state["unsupported_links"]} == {"period_differs_between_requests"}


def test_a_single_request_has_nothing_to_hand_over(base):
    case = {"query": "M2 통화량 2023년 연간", "as_of": "2026-10-29",
            "script": {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
                       "periods": [{"quote": "2023년", "for": ["s1"]}],
                       "frequencies": [{"quote": "연간", "for": ["s1"]}],
                       "choices": [{"for": ["s1"], "table": "DT_161Y014"}]}}
    got = record(base, case)
    assert got["status"] == "ok" and got["calls"][0]["period_context"] is None
    assert got["tables"]["DT_161Y014"]["fetch"] == ["Y", "2023", "2023"]


if __name__ == "__main__":  # record of every case: question -> calls -> request range -> read range -> fetch parameters
    from claview_pipeline import FlowSession

    session = FlowSession.load()
    for item in CASES:
        got = record(session, item)
        print(f"\n[{got['id']}] {got['query']}  -> {got['status']}")
        for number, call in enumerate(got["calls"], 1):
            shown = ", ".join(f"{t['table_id']} 받은 기간={t['period']} 문구={t['phrase']!r}" for t in call["tables"])
            print(f"  호출 {number}: {shown} | 함께 전달한 다른 문구={call['period_context']}")
        for table_id, row in got["tables"].items():
            print(f"  {table_id}: 요청={row.get('requested')} 읽은 범위={row.get('fetched')} 계산용={row.get('extra')} "
                  f"조회={row.get('fetch')}")


def test_legacy_b_keeps_different_period_guard_after_answered_group_is_filtered(base):
    from test_screen_54 import period_answer, period_supplier

    case = next(c for c in CASES if c["id"] == "PC-01")
    client = SyntheticFetchClient()
    runner = make(base, case["query"], case["script"], RecordingB, client)
    # This existing test-only supplier exercises the real answer/resume/filter path.
    # Both years remain in the original question after the answered s1 group is withheld.
    runner.period_questions = lambda view_state: [
        question for question in period_supplier(view_state) if question["statistics"] == ["s1"]
    ]
    waiting = runner.run(case["query"], as_of=case["as_of"])
    assert waiting["status"] == "needs_confirmation" and RecordingB.calls == []
    done = runner.resume(waiting, period_answer(waiting, "2023년"))
    assert done["status"] == "unsupported"
    assert RecordingB.calls == [] and client.calls == []
    assert done["calls"]["HCX"] == done["calls"]["KOSIS"] == done["calls"]["A_judge"] == 0
    blocked = {(tuple(row["statistics"]), row["code"]) for row in done["unsupported_links"]}
    assert blocked == {
        (("s1",), "period_answer_contract_pending"),
        (("s2",), "period_differs_between_requests"),
    }
