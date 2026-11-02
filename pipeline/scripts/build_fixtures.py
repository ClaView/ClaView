"""Build the contract fixtures in common/fixtures/ from the golden 22 questions (offline).

    PYTHONPATH=common:modules/a:modules/b:modules/c:pipeline python -B -X utf8 pipeline/scripts/build_fixtures.py

Per case:
    a_request/   {query, as_of}                         input of POST /a/legacy/select
    a_result/    decision, tables, b_requests, pending  output of A (rules, no model); state left out
    b_request/   {query, tables, as_of}                 input of POST /b/resolve, tables = golden expected A tables
    b_result/    B envelope                             output of B with the SYNTHETIC model
    data_result/ fetch envelope                         output of fetch with the SYNTHETIC client (values "1")
    view/        C envelope                             output of C on that data_result
Plus data_result_saved/: real KOSIS data_result payloads saved by C (modules/c/samples), for C's own work.

b_request does not depend on A's answer, so B can be developed and measured without A.
Synthetic outputs check shapes and wiring only, never accuracy.

Use --a-http-only to wrap the existing synthetic AView fixtures for the new HTTP boundary,
without rebuilding any legacy A, B or C fixture.
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


def write(kind: str, name: str, value) -> None:
    path = OUT / kind / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def write_http_fixtures() -> None:
    from claview_common.contracts import A_HTTP_VERSION, AHTTPResult, AHTTPSelectRequest, Meta

    for path in sorted((OUT / "a_view").glob("*.json")):
        view = json.loads(path.read_text(encoding="utf-8"))
        request = AHTTPSelectRequest(contract_version=A_HTTP_VERSION,
                                     query=view["session"]["question"], as_of=view["session"]["as_of"])
        result = AHTTPResult(view=view, execution_mode="synthetic", meta=Meta(latency_ms=0, mode="synthetic"))
        write("a_http_request", path.stem, request.model_dump())
        write("a_http_result", path.stem, result.model_dump())


def main() -> int:
    if "--a-http-only" in sys.argv:
        write_http_fixtures()
        return 0

    from claview_a_api import service as a_service
    from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
    from claview_b_api.service import BService, store
    from claview_c_api.service import present

    if OUT.exists():
        for kind in ("a_request", "a_result", "b_request", "b_result", "data_result", "data_result_saved", "view"):
            shutil.rmtree(OUT / kind, ignore_errors=True)
    b = BService(SyntheticSelectionModel(store()), SyntheticFetchClient(), "synthetic")
    cases = json.loads(GOLDEN.read_text(encoding="utf-8-sig"))["cases"]
    for case in cases:
        cid, query = case["case_id"], case["query"]
        write("a_request", cid, {"query": query, "as_of": AS_OF})
        a = a_service.select(query, AS_OF)
        write("a_result", cid, {k: a[k] for k in ("decision", "tables", "b_requests", "pending_question")})
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
