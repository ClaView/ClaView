"""로컬 전용 질문 실행 서버. 키는 환경변수 또는 터미널 숨김 입력, 파일에는 저장하지 않는다."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4

from dependencies import HERE, UI_ROOT
from pipeline import Pipeline, validate_query
from c_analysis import VERSION as C_ANALYSIS_VERSION


class LocalHTTPServer(ThreadingHTTPServer):
    """Windows에서도 같은 주소에 서버 두 개가 동시에 뜨지 않도록 한다."""
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(runner, port=8775):
    token, busy, lock = secrets.token_urlsafe(32), threading.Lock(), threading.Lock()
    jobs, checkpoints = {}, {}  # 전체 A 상태는 서버 안에만 보관한다.

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_bytes(self, code, raw, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "img-src data: blob:; font-src data:; connect-src 'self'; "
                             "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def send_json(self, code, value):
            self.send_bytes(code, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def local(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            return self.headers.get("Host") == expected and self.headers.get("Origin") in {None, f"http://{expected}"} \
                and self.headers.get("Sec-Fetch-Site") != "cross-site"

        def do_GET(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                return self.send_json(403, {"error": "local_only"})
            path = urlsplit(self.path).path
            pages = {"/": HERE / "dist/index.html", "/index.html": HERE / "dist/index.html",
                     "/review22": HERE / "dist/review22.html",
                     "/review-new": HERE / "dist/review_new_questions.html",
                     "/chart-review": HERE / "dist/chart_selection_review.html",
                     "/dictionary-review": HERE / "dist/dictionary_comparison.html",
                     "/sample": HERE / "dist/sample.html"}
            if path in pages:
                if not pages[path].is_file():
                    return self.send_json(404, {"error": "검수 결과가 아직 준비되지 않았습니다."})
                return self.send_bytes(200, pages[path].read_bytes(), "text/html; charset=utf-8")
            if not self.local():
                return self.send_json(403, {"error": "local_only"})
            if path == "/api/info":
                return self.send_json(200, {"token": token, "mode": "actual_pipeline",
                                           "confirmation_resume": True,
                                           "b_condition_resume": False,
                                           "flow": "A->B->fetch->C (claview.pipeline)",
                                           "c_analysis_version": C_ANALYSIS_VERSION,
                                           "server_pid": os.getpid(),
                                           "busy": busy.locked(),
                                           "a_mode": "rules_only", "hcx_ready": runner.model is not None,
                                           "kosis_ready": runner.client is not None})
            if re.fullmatch(r"/api/jobs/[a-f0-9]{32}", path):
                if self.headers.get("X-Claview-Token") != token:
                    return self.send_json(403, {"error": "request_not_allowed"})
                with lock:
                    job = jobs.get(path.rsplit("/", 1)[-1])
                    response = json.loads(json.dumps(job)) if job else None
                return self.send_json(200 if response else 404, response or {"error": "not_found"})
            return self.send_json(404, {"error": "not_found"})

        def do_POST(self):
            if not self.local() or self.headers.get("X-Claview-Token") != token:
                return self.send_json(403, {"error": "request_not_allowed"})
            path = urlsplit(self.path).path
            if path not in {"/api/query", "/api/resume"}:
                return self.send_json(404, {"error": "not_found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16000 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError()
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError()
                if path == "/api/query":
                    if set(payload) != {"query"}:
                        raise ValueError()
                    query = validate_query(payload["query"])
                    if any(k in query for k in runner.secrets):
                        raise ValueError()
                elif set(payload) != {"job_id", "confirmation"} or not isinstance(payload["job_id"], str):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                return self.send_json(400, {"error": "요청 형식이 올바르지 않습니다. 인증키는 질문에 넣지 마세요."})
            source_job, paused = None, None
            with lock:
                if path == "/api/resume":
                    source_job = payload["job_id"]
                    original = jobs.get(source_job)
                    if original is None:
                        return self.send_json(410, {"error": "서버가 재시작되었거나 보관 한도가 지나 이 확인 기록이 없습니다. 같은 질문을 다시 입력해 주세요."})
                    if not original["done"] or original.get("resumed_job_id"):
                        return self.send_json(409, {"error": "이미 이어 실행했거나 아직 처리 중인 기록입니다. 중복 조회하지 않습니다."})
                    paused = checkpoints.get(source_job)
                    if paused is None:
                        return self.send_json(400, {"error": "후보 확인을 기다리는 실행이 아닙니다."})
                    try:
                        runner.validate_resume(paused, payload["confirmation"])
                    except ValueError as exc:
                        return self.send_json(400, {"error": str(exc) or "선택 내용을 다시 확인해 주세요."})
                    except (TypeError, KeyError):
                        return self.send_json(400, {"error": "선택 형식이 올바르지 않습니다."})
                if not busy.acquire(blocking=False):
                    return self.send_json(409, {"error": "앞선 질문을 처리 중입니다. 중복 호출하지 않고 기다립니다."})
                job_id = uuid4().hex
                # 완료된 응답만 정리한다. 실행 중 작업을 버리지 않는다.
                if len(jobs) >= 30:
                    oldest = next((k for k in jobs if k != source_job and jobs[k]["done"]), None)
                    if oldest:
                        jobs.pop(oldest)
                        checkpoints.pop(oldest, None)
                jobs[job_id] = {"job_id": job_id, "done": False, "events": [], "result": None}
                if source_job:
                    jobs[source_job]["resumed_job_id"] = job_id

            def emit(event):
                with lock:
                    jobs[job_id]["events"].append(event)
                    if event["type"] == "result":
                        jobs[job_id]["result"] = event["result"]

            def work():
                try:
                    state = (runner.resume(paused, payload["confirmation"], emit=emit) if source_job
                             else runner.run(query, emit=emit))
                    if state.get("confirmation"):
                        with lock:
                            checkpoints[job_id] = state
                except Exception:
                    with lock:
                        jobs[job_id]["error"] = "실행을 완료하지 못했습니다. 원인을 확인한 뒤 다시 시도하세요."
                finally:
                    with lock:
                        jobs[job_id]["done"] = True
                    busy.release()

            threading.Thread(target=work, daemon=True).start()
            return self.send_json(202, {"job_id": job_id})

    return LocalHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8775)
    parser.add_argument("--prompt-keys", action="store_true")
    parser.add_argument("--supervised-stop-file", type=str)
    args = parser.parse_args()
    hcx, kosis = os.environ.get("CLOVASTUDIO_API_KEY"), os.environ.get("KOSIS_API_KEY")
    if args.prompt_keys:
        hcx = getpass.getpass("HCX key (hidden, memory only): ").strip() or hcx
        kosis = getpass.getpass("KOSIS key (hidden, memory only): ").strip() or kosis
    print("Loading shared A/B metadata...", flush=True)
    runner = Pipeline(hcx, kosis)
    del hcx, kosis
    # Only whether a key is set; never the value.
    print(f"HCX key: {'set' if runner.model else 'NOT set (B stage will stop)'} | "
          f"KOSIS key: {'set' if runner.client else 'NOT set (fetch stage will stop)'}", flush=True)
    server = make_server(runner, args.port)
    stop_watch = None
    watch_done = threading.Event()
    if args.supervised_stop_file:
        from pathlib import Path
        stop_file = Path(args.supervised_stop_file).resolve()
        if stop_file.parent != (HERE / ".runtime").resolve() or not re.fullmatch(r"child-stop-[a-f0-9]{32}\.json", stop_file.name):
            server.server_close(); runner.close()
            raise ValueError("관리자 중지 파일 경로가 올바르지 않습니다.")

        def watch_stop():
            while not watch_done.wait(.25):
                if stop_file.is_file():
                    server.shutdown()
                    return

        stop_watch = threading.Thread(target=watch_stop, daemon=True)
        stop_watch.start()
    print(f"ClaView question -> A -> B -> KOSIS -> C: http://127.0.0.1:{server.server_port}/", flush=True)
    print("No external calls until a question is submitted. Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watch_done.set()
        server.server_close()
        runner.close()
        if stop_watch:
            stop_watch.join(timeout=2)
        print("Server stopped; connections closed.", flush=True)


if __name__ == "__main__":
    main()
