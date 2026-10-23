"""Keyless checks. No paid model or KOSIS requests; no user history is saved."""
from pathlib import Path
import json
import socket
import sys
import threading
import urllib.request
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
APP = ROOT / "c_practice/abc_integration"
sys.path.insert(0, str(APP))


def main():
    original_connect = socket.socket.connect

    def local_only(sock, address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            raise AssertionError("검수 중 외부 API 접근을 차단했습니다.")
        return original_connect(sock, address)

    with patch.object(socket.socket, "connect", local_only):
        from dependencies import configure, B_ROOT, C_ROOT
        configure()
        import ui_build
        ui_build.build_all()
        import pipeline as p
        from server import make_server
        runner = p.Pipeline(runs_dir=None)
        try:
            cases = json.loads((B_ROOT / "eval/cases/golden_set_v1.json").read_text(encoding="utf-8"))["cases"]
            counts = {}
            for case in cases:
                result = runner.run(case["query"], through="A", as_of="2026-10-22")
                assert result["calls"] == {"HCX": 0, "KOSIS": 0}
                counts[result["status"]] = counts.get(result["status"], 0) + 1
            assert len(cases) == 22
            print("[통과] 실제 A 코드로 골든셋 22건 실행 (B/조회는 미실행):", counts)
            query04 = next(c["query"] for c in cases if c["case_id"] == "CV-AB-04")
            paused = runner.run(query04, through="A", as_of="2026-10-22")
            choice = {"choices": [
                {"requirement_id": group["requirement_id"], "option_id": next(
                    option["option_id"] for option in group["options"]
                    if "DT_301Y013" in [t["table_id"] for t in option["tables"]])}
                for group in paused["confirmation"]["groups"]], "interpretation_confirmed": True}
            # Only this test replaces B and fetch; the production service is unchanged.
            saved04 = json.loads((C_ROOT / "samples/CV-AB-04_data_result.json").read_text(encoding="utf-8"))
            b_result = {"selections": [s["selection"] for s in saved04["series"]], "relations": []}
            mocked_b = SimpleNamespace(invoke=lambda *_: {"stage_outcome": {
                "stage": "B", "status": "ok", "result": b_result, "issues": []}})
            with patch.object(runner, "model", object()), patch.object(runner, "client", object()), \
                 patch.object(p, "build_b_graph", return_value=mocked_b), \
                 patch.object(p, "fetch_data", return_value={"stage": "fetch", "status": "ok", "result": saved04}):
                resumed = runner.resume(paused, choice)
                assert resumed["status"] == "ok", resumed.get("error_code")
                assert len(resumed["ui_cases"]) == 1
                assert len(resumed["ui_cases"][0]["series"]) == 5
            print("[통과] 모의 연결 시험: 실제 A 후보 선택 → 모의 B/fetch → 실제 C, 5계열 함께 표시")
            for path in sorted((C_ROOT / "samples").glob("*_data_result.json")):
                data = json.loads(path.read_text(encoding="utf-8"))
                out = p.renderer("시간에 따른 추이를 비교해줘", data)
                assert out["status"] in {"ok", "partial"}, path.name
                cards = p.ui_cases(out["result"], data, "team-check", [])
                original = {s["series_id"]: {x["period"]: x["value"] for x in s["points"]} for s in data["series"]}
                for card in cards:
                    for series in card["series"]:
                        assert series["values"] == [original[series["id"]].get(t) for t in card["periods"]]
                print("[통과] 저장 샘플 → C, 원값 보존:", path.name)
            data = json.loads((C_ROOT / "samples/CV-AB-04_data_result.json").read_text(encoding="utf-8"))
            data["_synthetic"] = True
            data["series"][0]["points"][0].update(value="0", raw_value="0", value_status="observed")
            data["series"][0]["points"][1].update(value=None, raw_value="-", value_status="missing")
            out = p.renderer("추이", data)
            card = p.ui_cases(out["result"], data, "synthetic-check", [])[0]
            assert card["series"][0]["values"][:2] == ["0", None]
            assert out["result"]["views"][0]["spec"]["connect_nulls"] is False
            print("[통과] 합성 시험: 정상 0과 결측 구분, 결측 선 연결 금지")
            server = make_server(runner, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                base = f"http://127.0.0.1:{server.server_address[1]}"
                for route in ("/", "/api/info", "/sample"):
                    with opener.open(base + route, timeout=10) as response:
                        assert response.status == 200
                        assert response.read()
                print("[통과] 별도 로컬 서버: 질문 화면·서버 상태·저장 샘플 화면 HTTP 200")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        finally:
            runner.close()
    print("검수 완료. 실제 HCX·KOSIS 호출 검증은 별도 키 입력 후 진행해야 합니다.")


if __name__ == "__main__":
    main()
