"""HCX-007의 호출 한도(TPM/QPM)와 429 응답 정보를 확인하는 스크립트.

배경: B 요청이 약 131,000자(CV-AB-04, DT_301Y013)일 때 HTTP 429가 났음.
verify_expand_hcx.py는 Trace를 저장하지 않아서 429의 서비스 코드와
x-ratelimit-* 헤더가 남아 있지 않음. 이 스크립트는 그 정보를 다시 모으기 위한 것임.

무엇을 하는가 (B 단계의 모델 호출만 함. KOSIS 호출 없음):

  1) 핑: 아주 작은 요청 1회를 보내 응답 헤더의 한도를 읽음.
     x-ratelimit-limit-tokens 값이 이 키가 적용받는 분당 토큰 한도(TPM)임.
       - 60000 이면 테스트/플레이그라운드 앱 기준 한도로 보임
       - 300000 이면 서비스 앱 기준 한도로 보임
     (공식 문서의 2025-07-17 기준 표이며 바뀔 수 있음)
  2) --case 를 주면, 해당 골든 질문의 B 요청을 가지치기 없이 1회 보냄(재시도 없음).
     429가 나면 http_status, service_code, 헤더의 남은/한도 토큰 수를 출력함.

호출 수는 최대 2회임. 키는 환경변수 CLOVASTUDIO_API_KEY에서만 읽고, 출력과 파일에
기록하지 않음. 저장 파일에는 transport 이벤트(상태 코드, 서비스 코드, 한도 헤더)와
요청 글자 수만 남기며 요청 본문은 저장하지 않음.

준비 (Windows PowerShell, 프로젝트 루트에서):

    $sec = Read-Host "HCX API key" -AsSecureString
    $env:CLOVASTUDIO_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))

실행:

    # 한도만 확인 (작은 요청 1회)
    python scripts/b/b_part/check_hcx_ratelimit.py --allow-external
    # 한도 확인 + 04번 요청(가지치기 없음) 1회
    python scripts/b/b_part/check_hcx_ratelimit.py --allow-external --case CV-AB-04

결과: 화면 출력과 eval/results/hcx_ratelimit_check.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))  # 패키지 설치 없이 실행

from claview.b.contracts import Config, Trace  # noqa: E402
from claview.b.evaluation import write_json  # noqa: E402
from claview.b.metadata import MetadataStore  # noqa: E402
from claview.b.models import HcxModel  # noqa: E402
from claview.b.stages import BSteps, Services, object_schema  # noqa: E402

CATALOG = ROOT / "data" / "catalog" / "search_catalog.json"
METADATA = ROOT / "data" / "raw" / "boK_meta_raw.jsonl"
GOLDEN = ROOT / "eval" / "cases" / "golden" / "golden_set_v1.json"
OUTPUT = ROOT / "eval" / "results" / "hcx_ratelimit_check.json"

TRANSPORT_EVENTS = {"transport_attempt", "transport_response", "transport_error", "transport_wait"}


def transport_events(trace: Trace) -> list[dict]:
    """키·본문이 없는 전송 관련 이벤트만 추린다."""
    return [e for e in trace.events if e.get("event") in TRANSPORT_EVENTS]


def request_sizes(trace: Trace) -> dict | None:
    for e in trace.events:
        if e.get("event") == "model_request":
            body = json.dumps(e["body"], ensure_ascii=False)
            return {
                "chars": len(body),
                "utf8_bytes": len(body.encode("utf-8")),
                "max_completion_tokens": e["body"].get("maxCompletionTokens"),
            }
    return None


def ping(model) -> dict:
    """아주 작은 요청 1회. 응답 헤더의 한도 값을 읽는 것이 목적임."""
    trace = Trace()
    schema = object_schema({"ok": {"type": "boolean"}})
    outcome = "ok"
    try:
        model.complete("ping", "JSON으로만 답한다.", {"q": "ok라고 답하라"}, schema, 64, trace)
    except Exception as exc:  # 한도 확인이 목적이라 실패해도 헤더 정보는 출력한다
        outcome = getattr(exc, "code", type(exc).__name__)
    return {
        "step": "ping",
        "outcome": outcome,
        "request": request_sizes(trace),
        "events": transport_events(trace),
    }


def big_request(model, case_id: str, as_of: str) -> dict:
    """골든 질문의 B 요청을 가지치기 없이 1회 보낸다 (한도를 크게 잡아 가지치기를 끈다)."""
    cases = {c["case_id"]: c for c in json.loads(GOLDEN.read_text(encoding="utf-8-sig"))["cases"]}
    if case_id not in cases:
        sys.exit("Unknown case id: " + case_id)
    case = cases[case_id]
    store = MetadataStore(CATALOG, METADATA)
    config = Config(as_of=as_of, max_context_chars=1_000_000, max_b_context_chars=1_000_000)
    trace = Trace()
    steps = BSteps(Services(store, search=None, model=model, config=config, trace=trace))
    state = {"query": case["query"], "a_result": case["expected"]["A"]}
    state.update(steps.load(state))
    state.update(steps.context(state))
    state.update(steps.select(state))  # 실패(429 등)는 stage_outcome으로 돌아온다
    outcome = state.get("stage_outcome", {})
    issues = outcome.get("issues") or [{}]
    return {
        "step": "case",
        "case_id": case_id,
        "outcome": outcome.get("status", "model_answered"),
        "error": issues[0].get("code"),
        "request": request_sizes(trace),
        "events": transport_events(trace),
    }


def show(result: dict) -> None:
    print(f"\n[{result['step']}] {result.get('case_id', '')} outcome={result['outcome']}", end="")
    print(f" error={result['error']}" if result.get("error") else "")
    if result.get("request"):
        print("  request:", result["request"])
    for e in result["events"]:
        if e["event"] == "transport_response":
            print(
                f"  response: http={e.get('http_status')} "
                f"service_code={e.get('service_code')} rate_limits={e.get('rate_limits')}"
            )
        elif e["event"] == "transport_error":
            print("  transport_error:", e.get("exception_type"))


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--allow-external", action="store_true", help="실제 HCX 호출에 필요함")
    p.add_argument("--case", help="가지치기 없이 보낼 골든 질문 id (예: CV-AB-04)")
    p.add_argument("--as-of", default="2026-10-01")
    p.add_argument("--output", type=Path, default=OUTPUT)
    args = p.parse_args()
    if not args.allow_external:
        p.error("실제 HCX 호출에는 --allow-external 이 필요함")
    key = os.environ.get("CLOVASTUDIO_API_KEY")
    if not key:
        p.error("CLOVASTUDIO_API_KEY 환경변수가 없음. 이 파일 상단의 준비 절차를 따를 것")

    model = HcxModel(key, retries=0)  # 재시도하지 않아야 첫 응답의 헤더가 그대로 보임
    results = []
    try:
        results.append(ping(model))
        show(results[-1])
        if args.case:
            results.append(big_request(model, args.case, args.as_of))
            show(results[-1])
    finally:
        model.close()
    write_json(args.output, {"results": results})
    print("\nWrote", args.output)


if __name__ == "__main__":
    main()
