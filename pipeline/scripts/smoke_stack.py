"""Check the deployed synthetic stack over HTTP, including nginx, answers and persisted results."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from uuid import uuid4


def request(base, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Claview-Token"] = token
    req = urllib.request.Request(base + path, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def wait(base, token, job_id):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = request(base, f"/api/jobs/{job_id}", token=token)
        if job["done"]:
            return job["result"]
        time.sleep(0.2)
    raise AssertionError("HTTP pipeline job did not finish")


def answers(card):
    assert card["kind"] == "a_view", card["kind"]
    out = []
    for group in card["groups"]:
        row = {"question_id": group["requirement_id"], "question_version": group["question_version"]}
        if group["kind"] == "period":
            row["text"] = "2023년"
        else:
            options = group["options"]
            assert options, "A question has no selectable answer"
            choice = next((o for o in options if "연간" in o["label"]), options[0])
            row["option_id"] = choice["option_id"]
        out.append(row)
    return {"session_id": card["session_id"], "answers": out}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:10300")
    args = parser.parse_args()
    info = request(args.url, "/api/info")
    assert info["claview_mode"] == "synthetic", "This check must never invoke live providers"
    token = info["token"]
    with urllib.request.urlopen(args.url + "/", timeout=30) as response:
        assert response.status == 200 and b"ClaView" in response.read(), "Frontend HTML was not served"
    try:
        request(args.url, "/api/query", {"query": "M2"})
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
    else:
        raise AssertionError("Missing CSRF token was accepted")
    report = []
    # Fixed wiring inputs whose first synthetic candidate has verified metadata.
    # The fake judge does not interpret natural-language periods; supply those as user answers.
    # Use distinct synthetic inputs for each run; retained question budgets must not be reset.
    tag = uuid4().hex[:12].translate(str.maketrans("0123456789", "ghijklmnop"))
    for subject in ("예금은행 대출금리", "가계신용"):
        query = f"{subject} (합성 연결 확인 {tag})"
        start = request(args.url, "/api/query", {"query": query}, token)
        job_id = start["job_id"]
        result = wait(args.url, token, job_id)
        count = 0
        while result["status"] == "needs_confirmation" and count < 4:
            payload = answers(result["confirmation"])
            start = request(args.url, "/api/resume", {"job_id": job_id, "confirmation": payload}, token)
            result = wait(args.url, token, start["job_id"])
            try:
                request(args.url, "/api/resume", {"job_id": job_id, "confirmation": payload}, token)
            except urllib.error.HTTPError as exc:
                assert exc.code == 409
            else:
                raise AssertionError("Duplicate answer was accepted")
            job_id = start["job_id"]
            count += 1
        assert result["status"] in {"ok", "partial"}, (result["status"], result.get("error_code"))
        assert result["ui_cases"] and any(c.get("series") for c in result["ui_cases"])
        assert all(s["status"] in {"completed", "warning"} for s in result["stages"])
        if count:
            assert result["calls"]["A_judge"] == 0, "Applying answers called the A model"
        restored = request(args.url, "/api/records/load", {"run_id": result["run_id"]}, token)
        assert restored["loaded"]
        saved = wait(args.url, token, restored["job_id"])
        assert saved["ui_cases"] == result["ui_cases"] and saved["calls"] == result["calls"]
        report.append({"query": query, "status": result["status"], "answer_rounds": count,
                       "run_id": result["run_id"], "cards": len(result["ui_cases"])})
    assert any(row["answer_rounds"] for row in report), "Confirmation path was not exercised"
    print(json.dumps({"mode": "synthetic", "external_calls": 0, "cases": report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
