"""저장 실행 기록은 덮어쓰지 않고 현재 C 버전으로 표시 자료만 다시 만든다."""
import copy
import json
from datetime import datetime, timezone

from dependencies import HERE
from pipeline import renderer, ui_cases


def presentation_for_record(record):
    data = copy.deepcopy(record.get("data_result", {}))
    path = HERE / "runs/c_analysis_verified" / (record["run_id"] + ".json")
    refresh = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if refresh and refresh["source_run_id"] == record["run_id"] and refresh["query"] == record["query"]:
        data = refresh["data_result"]
    tables = [{"table_id": s["selection"]["table_id"], "table_name": s["selection"]["label"].split(" / ")[0]} for s in data.get("series", [])]
    c_out = renderer(record["query"], data)
    if c_out["status"] not in {"ok", "partial"}:
        raise ValueError(f"C 재구성 실패: {record['run_id']}: {c_out.get('issues')}")
    cards = ui_cases(c_out["result"], data, record["run_id"], tables)
    return {"source_run_id": record["run_id"], "processed_at": datetime.now(timezone.utc).isoformat(),
            "data_result": data, "chart_result": c_out["result"], "ui_cases": cards,
            "refresh": {"fetched_at": refresh["fetched_at"], "calls": refresh["calls"]} if refresh else None}
