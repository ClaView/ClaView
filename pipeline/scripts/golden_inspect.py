"""Print what B's model answered in a saved golden run (`golden_replay.py check --out <dir>`).

Usage: python golden_inspect.py runs/golden-v3-hcx CV-BC-04 [--request]

`--request` also shows what the model was given per B call: whether the multi-table block is in
the system prompt, the question, the other tables, and the candidate values of each axis.

Reads `<dir>/<case>.json` and lists, per B call, the selections the model returned (item, dimension
codes, expand, period) and the other trace events (validation retries, waits, HTTP status). It reads
files only: no network, no keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKIP = {"model_request", "model_response", "transport_attempt"}


def model_rows(response: object) -> tuple[list[dict], str]:
    """(selections, note) from a model_response payload (HCX result or scripted answer)."""
    if isinstance(response, dict) and "message" in response:
        try:
            response = json.loads(response["message"]["content"])
        except (KeyError, TypeError, ValueError):
            return [], "content를 해석하지 못함"
    if isinstance(response, dict) and isinstance(response.get("selections"), list):
        return response["selections"], ""
    return [], "selections 없음"


def describe(row: dict) -> str:
    dims = ", ".join(f"{a}={v}" for a, v in sorted((row.get("dimensions") or {}).items()))
    expand = {a: m for a, m in (row.get("expand") or {}).items() if m != "none"}
    period = row.get("period") or {}
    span = f"{period.get('frequency', '?')} {period.get('start', '?')}~{period.get('end', '?')}"
    return f"item={row.get('item_id')} · {dims} · {span}" + (
        f" · expand={expand}" if expand else ""
    )


MULTI_MARK = "selected_tables에 표가 여럿이면"


def request_summary(event: dict) -> list[str]:
    """Lines describing one model_request: system prompt flags, question, tables, candidates."""
    body = event.get("body")
    if isinstance(body, dict) and body.get("messages"):
        system = body["messages"][0].get("content", "")
        try:
            context = json.loads(body["messages"][1].get("content", "{}"))
        except (TypeError, ValueError):
            context = {}
    else:  # scripted model: the event carries system and context directly
        system, context = event.get("system", ""), event.get("context") or {}
    lines = [f"  [입력] 다중 표 규칙 포함: {'예' if MULTI_MARK in system else '아니오'}"]
    lines.append(f"  [입력] 질문: {context.get('query')}")
    if context.get("table_focus"):
        lines.append(f"  [입력] 표별 지표 안내(table_focus): {context['table_focus']}")
    names = [t.get("table_name") for t in context.get("selected_tables") or []]
    lines.append(f"  [입력] 함께 전달된 통계표: {names}")
    table = context.get("table_metadata") or {}
    lines.append(f"  [입력] 항목: {[v.get('name') for v in (table.get('items') or {}).values()]}")
    for axis, dim in (table.get("dimensions") or {}).items():
        values = [v.get("name") for v in (dim.get("values") or {}).values()]
        shown = values[:40]
        more = f" … 외 {len(values) - 40}개" if len(values) > 40 else ""
        lines.append(f"  [입력] {axis}({dim.get('name')}) 후보 {len(values)}개: {shown}{more}")
    if table.get("pruned_axes"):
        lines.append(f"  [입력] 후보를 줄인 축: {table['pruned_axes']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir")
    parser.add_argument("case")
    parser.add_argument("--request", action="store_true", help="모델에 준 입력도 표시")
    args = parser.parse_args(argv)
    path = Path(args.run_dir) / f"{args.case}.json"
    if not path.exists():
        print(f"기록이 없습니다: {path}", file=sys.stderr)
        return 2
    record = json.loads(path.read_text(encoding="utf-8"))
    state = record.get("state") or {}
    print(f"[{args.case}] 상태: {state.get('status')} · 오류: {state.get('error_code')}")
    for problem in record.get("problems") or []:
        print(f"  불일치: {problem}")
    for event in state.get("trace_events") or []:
        name = event.get("event")
        if name == "model_request" and args.request:
            print(f"\n모델 입력 {event.get('task')}")
            print("\n".join(request_summary(event)))
        elif name == "model_response":
            rows, note = model_rows(event.get("response"))
            print(f"\nB 호출 {event.get('task')} → 선택 {len(rows)}개 {note}")
            seen = set()
            for i, row in enumerate(rows, 1):
                key = json.dumps(
                    [row.get("item_id"), row.get("dimensions")], sort_keys=True, ensure_ascii=False
                )
                mark = "  ← 앞 행과 같은 항목·분류(중복)" if key in seen else ""
                seen.add(key)
                print(f"  {i}. {describe(row)}{mark}")
        elif name not in SKIP:
            fields = {k: v for k, v in event.items() if k not in {"event", "body", "response"}}
            print(f"  · {name} {json.dumps(fields, ensure_ascii=False, default=str)[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
