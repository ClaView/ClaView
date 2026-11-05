"""Build the contract fixtures in common/fixtures/ from the golden 22 questions (offline).

    PYTHONPATH=common:modules/a:modules/b:modules/c:pipeline python -B -X utf8 pipeline/scripts/build_fixtures.py

Per case:
    a_request/   {query, as_of}                         question corpus (not an HTTP request)
    b_request/   {query, tables, as_of}                 input of POST /b/resolve, tables = golden expected A tables
    b_result/    B envelope                             output of B with the SYNTHETIC model
    data_result/ fetch envelope                         output of fetch with the SYNTHETIC client (values "1")
    view/        C envelope                             output of C on that data_result
Plus data_result_saved/: real KOSIS data_result payloads saved by C (modules/c/samples), for C's own work.

b_request does not depend on A's answer, so B can be developed and measured without A.
Synthetic outputs check shapes and wiring only, never accuracy.

Use --a-http-only to wrap the existing synthetic AView fixtures for the new HTTP boundary,
without rebuilding B or C fixtures.
Use --period-contract-only for the synthetic period request/result examples and updated
AView/HTTP wrappers only. It imports common models, never module services or providers.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "common" / "fixtures"
GOLDEN = ROOT / "eval" / "cases" / "golden" / "golden_set_v1.json"
AS_OF = "2026-10-22"


def write(kind: str, name: str, value, *, indent: int = 1) -> None:
    path = OUT / kind / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8", newline="\n")


def write_http_fixtures() -> None:
    from claview_common.contracts import A_HTTP_VERSION, AHTTPResult, AHTTPSelectRequest, Meta

    for path in sorted((OUT / "a_view").glob("*.json")):
        view = json.loads(path.read_text(encoding="utf-8"))
        request = AHTTPSelectRequest(contract_version=A_HTTP_VERSION,
                                     query=view["session"]["question"], as_of=view["session"]["as_of"])
        result = AHTTPResult(view=view, execution_mode="synthetic", meta=Meta(latency_ms=0, mode="synthetic"))
        write("a_http_request", path.stem, request.model_dump())
        write("a_http_result", path.stem, result.model_dump())


def write_period_contract_fixtures() -> None:
    """Illustrative invented tables and answers, validated by the shared contracts."""
    from claview_common.contracts import BRequest, PeriodCondition, PeriodResolution
    from claview_common.contracts.a_view import A_VIEW_VERSION, AView

    for name in ("focus_legacy_quote", "focus_model_scope"):
        view = json.loads((OUT / "a_view" / f"{name}.json").read_text(encoding="utf-8"))
        view["contract_version"] = A_VIEW_VERSION
        for request in view["handoff"]["ready"]:
            request["b_request"] = None
            request["b_request_issue"] = None
        write("a_view", name, AView.model_validate(view).model_dump(), indent=2)
    write_http_fixtures()

    def answer(kind, statistics, *, question_id=None, value=None, text=None):
        return {"kind": kind, "question_id": question_id or f"synthetic:{kind}:s1",
                "question_version": 1, "statistics": statistics, "option_id": None,
                "value": value, "text": text, "source": "user_answer"}

    period_answer = answer("period", ["s1"], value="2024년", text="2024년")
    frequency_answer = answer("frequency", ["s1"], value="M")
    cases = [
        {"name": "period_answer_only", "query": "합성 예시 지표를 월별로 보여줘",
         "table_id": "DEMO_PERIOD_ONLY", "frequency": "M", "period_source": "user_answer",
         "frequency_source": "question", "answers": [period_answer]},
        {"name": "frequency_answer_only", "query": "2024년 합성 예시 지표를 보여줘",
         "table_id": "DEMO_FREQUENCY_ONLY", "frequency": "Q", "period_source": "quoted",
         "frequency_source": "user_answer", "answers": [answer("frequency", ["s1"], value="Q")]},
        {"name": "both_answers", "query": "합성 예시 지표의 전년 동월 대비 변화를 보여줘",
         "table_id": "DEMO_BOTH_ANSWERS", "frequency": "M", "period_source": "user_answer",
         "frequency_source": "user_answer", "answers": [period_answer, frequency_answer]},
        {"name": "quoted_reference", "query": "최근 12개월 합성 예시 지표를 월별로 보여줘",
         "table_id": "DEMO_QUOTED_REFERENCE", "frequency": "M", "period_source": "quoted",
         "frequency_source": "question", "answers": []},
        {"name": "same_table_two_statistics", "query": "합성 계열 가와 계열 나를 월별로 보여줘",
         "table_id": "DEMO_SHARED_TABLE", "frequency": "M", "period_source": "user_answer",
         "frequency_source": "question", "statistics": ["s1", "s2"], "answers": [
             answer("period", ["s1"], question_id="synthetic:period:s1", value="2024년", text="2024년"),
             answer("period", ["s2"], question_id="synthetic:period:s2", value="2024년", text="2024년"),
         ]},
    ]
    for case in cases:
        frequency = case["frequency"]
        statistics = case.get("statistics", ["s1"])
        period = [{"start": "2024-01", "end": "2024-12"}]
        reference_only = case["name"] == "quoted_reference"
        condition = PeriodCondition.model_validate({
            "frequency": frequency, "frequency_source": case["frequency_source"],
            "period": period, "period_source": case["period_source"], "statistics": statistics,
            "period_phrase": "최근 12개월" if reference_only else "2024년",
            "answers": case["answers"],
        })
        request = BRequest.model_validate({
            "query": case["query"], "as_of": "2025-01-31", "tables": [{
                "org_id": "DEMO", "table_id": case["table_id"], "table_name": "합성 계약 예시표",
                "focus": "합성 계열 가 · 합성 계열 나" if len(statistics) > 1 else "합성 예시 지표",
                "period_condition": condition.model_dump(),
            }],
        })
        write("b_period_request", case["name"], request.model_dump())

        def span(start, end):
            return {"frequency": frequency, "start": start, "end": end}

        requested = span("202401", "202404" if frequency == "Q" else "202412")
        fetched = span("202401", "202403" if frequency == "Q" else "202409")
        missing = [span("202404", "202404")] if frequency == "Q" else [span("202410", "202412")]
        unavailable_code = ("confirmed_period_partly_unavailable" if case["period_source"] == "user_answer"
                            else "requested_period_partly_unavailable")
        notices = [{"code": unavailable_code, "visible": True,
                    "message": "합성 예시: 요청 기간을 보존하고 미수록 기간은 조회하지 않았습니다."}]
        if reference_only:
            # B's invented newer metadata changes a reference, not an authoritative user answer.
            requested = fetched = span("202402", "202501")
            missing = []
            notices = [{"code": "reference_period_differs", "visible": False,
                        "message": "합성 예시: B 수록 기준으로 계산한 기간이 A 참고 기간과 다릅니다."}]
        fetched_ranges = [fetched]
        extra = []
        if case["name"] == "both_answers":
            baseline = span("202301", "202309")
            fetched_ranges.insert(0, baseline)
            extra = [{**baseline, "purpose": "yoy_baseline"}]
            notices.append({"code": "calculation_range_extended", "visible": False,
                            "message": "합성 예시: 표시 기간은 유지하고 전년 동월 비교 구간을 추가 조회했습니다."})
        resolution = PeriodResolution.model_validate({
            "table_id": case["table_id"], "statistics": statistics, "frequency": frequency,
            "frequency_alternatives": [], "requested": [requested], "fetched": fetched_ranges,
            "missing": missing, "calculation_extra": extra,
            "applied_sources": {
                "period": "a_user_answer" if case["period_source"] == "user_answer" else "b_computed",
                "frequency": "a_user_answer" if case["frequency_source"] == "user_answer" else "b_computed",
            },
            "substituted": False,
            "reference": {"period": period, "frequency": frequency,
                          "differs": {"period": reference_only, "frequency": False}},
            "notices": notices,
        })
        write("b_period_resolution", case["name"], resolution.model_dump())


def main() -> int:
    if "--period-contract-only" in sys.argv:
        write_period_contract_fixtures()
        return 0
    if "--a-http-only" in sys.argv:
        write_http_fixtures()
        return 0

    from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
    from claview_b_api.service import BService, store
    from claview_c_api.service import present

    if OUT.exists():
        for kind in ("a_request", "b_request", "b_result", "data_result", "data_result_saved", "view"):
            shutil.rmtree(OUT / kind, ignore_errors=True)
    b = BService(SyntheticSelectionModel(store()), SyntheticFetchClient(), "synthetic")
    cases = json.loads(GOLDEN.read_text(encoding="utf-8-sig"))["cases"]
    for case in cases:
        cid, query = case["case_id"], case["query"]
        write("a_request", cid, {"query": query, "as_of": AS_OF})
        tables = case["expected"]["A"]["tables"]
        if not tables:
            continue
        b_request = {"query": query, "tables": tables, "as_of": AS_OF}
        write("b_request", cid, b_request)
        b_out, _ = b.resolve(query, tables, AS_OF)
        write("b_result", cid, b_out)
        if b_out["status"] != "ok":
            continue
        data, _ = b.fetch(b_out["result"])
        write("data_result", cid, data)
        if data["status"] == "ok":
            write("view", cid, present(query, data["result"]))
    for sample in sorted((ROOT / "modules" / "c" / "samples").glob("*.json")):
        (OUT / "data_result_saved").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sample, OUT / "data_result_saved" / sample.name)
    write_http_fixtures()
    counts = {k.name: len(list(k.glob("*.json"))) for k in sorted(OUT.iterdir()) if k.is_dir()}
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
