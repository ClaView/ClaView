"""The local server on the new pipeline: the HTTP handler driven without a real socket."""

from __future__ import annotations

import io
import json
import socketserver
import time

import pytest
import server as server_module
from server import make_server


@pytest.fixture(autouse=True)
def _bind_without_name_lookup(monkeypatch):
    """The test process blocks name lookups; HTTPServer.server_bind would resolve the host."""

    def bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]

    monkeypatch.setattr(server_module.LocalHTTPServer, "server_bind", bind)


class _Conn:
    def __init__(self, raw: bytes):
        self.raw, self.out = raw, io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        return io.BytesIO(self.raw) if "r" in mode else self.out

    def sendall(self, data):
        self.out.write(data)

    def close(self):
        pass


def call(server, method, path, body=None, token=None):
    headers = {"Host": f"127.0.0.1:{server.server_port}"}
    payload = b""
    if body is not None:
        payload = json.dumps(body).encode()
        headers.update({"Content-Type": "application/json", "Content-Length": str(len(payload))})
    if token:
        headers["X-Claview-Token"] = token
    head = (
        f"{method} {path} HTTP/1.1\r\n"
        + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        + "\r\n"
    )
    conn = _Conn(head.encode() + payload)
    handler_cls = server.RequestHandlerClass
    handler_cls(conn, ("127.0.0.1", 0), server)
    raw = conn.out.getvalue()
    status = int(raw.split(b" ", 2)[1])
    return status, json.loads(raw.split(b"\r\n\r\n", 1)[1] or b"null")


def wait(server, token, job_id):
    for _ in range(600):
        status, job = call(server, "GET", f"/api/jobs/{job_id}", token=token)
        if job["done"]:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_query_confirm_resume_over_http(make_pipeline):
    server = make_server(make_pipeline(), 0)
    try:
        status, info = call(server, "GET", "/api/info")
        assert status == 200 and info["hcx_ready"] and info["kosis_ready"]
        assert info["confirmation_resume"] is True and info["b_condition_resume"] is False
        token = info["token"]
        _, started = call(server, "POST", "/api/query", {"query": "총고정자본형성 2023년"}, token)
        job = wait(server, token, started["job_id"])
        assert job["result"]["status"] == "needs_confirmation"
        group = job["result"]["confirmation"]["groups"][0]
        bad = {
            "choices": [{"requirement_id": group["requirement_id"], "option_id": "q:forged"}],
            "interpretation_confirmed": False,
        }
        assert (
            call(
                server,
                "POST",
                "/api/resume",
                {"job_id": started["job_id"], "confirmation": bad},
                token,
            )[0]
            == 400
        )
        good = {
            "choices": [
                {
                    "requirement_id": group["requirement_id"],
                    "option_id": group["options"][1]["option_id"],
                }
            ],
            "interpretation_confirmed": False,
        }
        status, resumed = call(
            server,
            "POST",
            "/api/resume",
            {"job_id": started["job_id"], "confirmation": good},
            token,
        )
        assert status == 202
        done = wait(server, token, resumed["job_id"])
        assert done["result"]["status"] == "ok" and done["result"]["ui_cases"]
        # The same checkpoint cannot be resumed twice (no duplicate calls).
        assert (
            call(
                server,
                "POST",
                "/api/resume",
                {"job_id": started["job_id"], "confirmation": good},
                token,
            )[0]
            == 409
        )
        assert (
            call(server, "POST", "/api/conditions", {"job_id": "x", "conditions": {}}, token)[0]
            == 404
        )
    finally:
        server.server_close()


def test_requests_without_token_or_with_key_text_are_refused(make_pipeline):
    server = make_server(make_pipeline(), 0)
    try:
        assert call(server, "POST", "/api/query", {"query": "GDP 2023년"}, None)[0] == 403
        token = call(server, "GET", "/api/info")[1]["token"]
        assert call(server, "POST", "/api/query", {"query": "GDP apiKey=abc"}, token)[0] == 400
        assert call(server, "POST", "/api/query", {"query": "GDP", "extra": 1}, token)[0] == 400
    finally:
        server.server_close()
