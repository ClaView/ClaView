"""The development exploration runner (plan 41/44): a fixed list and manifest, gates, a fake run that records
observations only, and refusals (no approval, a changed list, an unknown answer state). No model, no network."""

from __future__ import annotations

import json

import pytest

from tl_eval import explore as E

MODEL = {"name": "terra", "model": "gpt-6.1-terra", "effort": "low", "max_output_tokens": 4096}


def write_list(path, cases):
    path.write_text(json.dumps({"version": "test-list.1", "cases": cases}, ensure_ascii=False), encoding="utf-8")
    return path


def case(cid, question, state="insufficient"):
    return {"case_id": cid, "question": question, "as_of": "2026-10-22", "standard": None,
            "answer_state": {"representative": {"state": state, "why": "test"}}}


@pytest.fixture
def two(tmp_path):
    return write_list(tmp_path / "list.json", [case("T1", "2024년 1~3월 M2 총액을 평잔 기준으로 보여줘"),
                                               case("T2", "예금은행 대출금리를 잔액 기준으로 찾아줘")])


def test_the_manifest_is_one_call_per_question_unapproved_and_passes_its_gates(two):
    m = E.make_manifest("t", MODEL, two)
    assert m["max_calls"] == 2 and m["retries"] == 0 and m["concurrency"] == 1
    assert m["approval"]["live_calls"] is False
    assert all(r["ok"] for r in E.gates(m))


def test_a_fake_run_records_observations_and_no_accuracy(two, tmp_path, layer):
    m = E.make_manifest("t", MODEL, two)
    got = E.run(m, "fake", tmp_path / "run", layer=layer)
    q = got["summary"]["questions"]
    assert sorted(q) == ["terra|1|T1", "terra|1|T2"] and got["calls"] == 2
    assert "wiring check only" in got["summary"]["note"] and "accuracy" not in json.dumps(got["summary"]).replace(
        "no accuracy over the list", "")
    assert {"statistics", "final", "scorer_facts", "conditions"} <= set(q["terra|1|T1"])


def test_live_is_refused_without_approval(two, tmp_path):
    with pytest.raises(PermissionError):
        E.run(E.make_manifest("t", MODEL, two), "live", tmp_path / "run", transport=object())


def test_a_changed_list_fails_the_gates(two):
    m = E.make_manifest("t", MODEL, two)
    write_list(two, [case("T1", "다른 질문")])
    assert not all(r["ok"] for r in E.gates(m))


def test_an_unknown_answer_state_is_refused(tmp_path):
    with pytest.raises(ValueError):
        E.make_manifest("t", MODEL, write_list(tmp_path / "l.json", [case("T1", "질문", state="right")]))
