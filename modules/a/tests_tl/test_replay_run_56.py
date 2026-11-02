"""Plan 56 order 2 (bundle 1): the stored-answer replay as a whole, on a synthetic stored run of two questions.

The run is written here with this code (a fake model answer, the request each question builds, its sha256 and a
provider-shaped raw response), then replayed with ``tl_eval.replay.replay_run``: the same code answers every call from
the archive with no change; an archive whose instructions differ by one character answers none of them (every case
a ``miss`` gap, no exception); nothing is ever sent (the provider transport is never built); the replay is never
written over an existing folder. No network, no model.
"""

from __future__ import annotations

import copy
import json

import pytest
from conftest import fill_option_evidence  # noqa: E402
from test_explicit_conditions_53 import M2, STOCK, output, ref
from tl_eval import replay as RP
from tl_eval import score as S

from claview_a.cards import build_cards
from claview_a.contract import Settings
from claview_a.graph import research_summary, run
from claview_a.judges import text_sha
from claview_a.pool import build_pool

AS_OF = "2026-10-22"
SETTINGS = Settings(model="synthetic-model", effort="low", max_output_tokens=4096)
QUESTIONS = [
    ("SYN-01", "월말 잔액 기준의 광의통화를 상품별로 원계열로 보여줘", "원계열"),
    ("SYN-02", "월말 잔액 기준의 광의통화를 상품별로 계절조정 계열로 보여줘", "계절조정"),
]


def raw(text: str) -> str:
    return json.dumps({"status": "completed", "usage": None,
                       "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]},
                      ensure_ascii=False)


def write_run(run_dir, layer, mutate=None):
    """A stored run in the explore.run layout: manifest.json, calls.jsonl, records.json."""
    rows, records = [], {}
    for case_id, question, seasonal in QUESTIONS:
        cards = build_cards(question, build_pool(question, layer), layer)
        r, _, card = ref(cards, M2, layer, seasonal=seasonal, stock="말잔")
        text = json.dumps(fill_option_evidence(output([("s1", "광의통화")], [STOCK], [("s1", r, card["name"])]),
                                               cards), ensure_ascii=False)
        key = f"terra|1|{case_id}"

        def judge(payload, request, key=key, text=text):
            stored = mutate(copy.deepcopy(request)) if mutate else copy.deepcopy(request)
            rows.append({"case_id": key, "call": len(rows) + 1, "outcome": "answered", "request": stored,
                         "request_sha256": text_sha(stored), "raw_response": raw(text)})
            return text

        state = run(question, layer, judge, SETTINGS, AS_OF)
        rec = S.adapt(state)
        rec.update(research_summary(state))
        rec.update(run_id="synthetic-run", model_name="synthetic", round=1)
        records[key] = rec
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": "synthetic-run", "research": {"enabled": False, "cases": []},
        "model": {"model": SETTINGS.model, "effort": SETTINGS.effort,
                  "max_output_tokens": SETTINGS.max_output_tokens}}), encoding="utf-8")
    (run_dir / "calls.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                         encoding="utf-8")
    (run_dir / "records.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return rows


@pytest.fixture
def no_transport(monkeypatch):
    from claview_a.transport import provider

    def refuse(*args, **kwargs):
        raise AssertionError("a replay built a provider transport")

    monkeypatch.setattr(provider.OpenAITransport, "__init__", refuse)


def test_the_same_code_answers_every_call_with_no_change(tmp_path, layer, no_transport):
    rows = write_run(tmp_path / "run", layer)
    got = RP.replay_run(tmp_path / "run", tmp_path / "out", layer=layer)
    t = got["totals"]
    assert (t["cases"], t["calls"], t["hits"]) == (2, len(rows), len(rows)) and len(rows) == 2
    assert t["gaps"] == {"miss": 0, "new_call_not_in_archive": 0}
    assert t["changed"] == [] and t["changed_v1"] == [] and t["requests_differ"] == []
    assert sorted(t["same"]) == ["terra|1|SYN-01", "terra|1|SYN-02"]
    assert set(got["compared_fields"]) == set(RP.FIELDS)


def test_one_changed_instruction_character_misses_every_call_without_an_error(tmp_path, layer, no_transport):
    def changed(request):
        request["instructions"] = request["instructions"][:-1] + "#"
        return request

    write_run(tmp_path / "run", layer, mutate=changed)
    got = RP.replay_run(tmp_path / "run", tmp_path / "out", layer=layer)
    t = got["totals"]
    assert t["hits"] == 0 and t["gaps"] == {"miss": 2, "new_call_not_in_archive": 0}
    assert all(c["gap"] == "miss" and c["diff"] is None for c in got["cases"].values())


def test_a_replay_is_never_written_over_an_existing_folder(tmp_path, layer):
    write_run(tmp_path / "run", layer)
    (tmp_path / "out").mkdir()
    with pytest.raises(FileExistsError):
        RP.replay_run(tmp_path / "run", tmp_path / "out", layer=layer)


def test_the_stored_files_are_read_only(tmp_path, layer):
    write_run(tmp_path / "run", layer)
    before = {p.name: p.read_bytes() for p in (tmp_path / "run").iterdir()}
    got = RP.replay_run(tmp_path / "run", tmp_path / "out", layer=layer)
    assert {p.name: p.read_bytes() for p in (tmp_path / "run").iterdir()} == before
    assert set(got["stored_sha256"]) == {"manifest.json", "calls.jsonl", "records.json"}


def test_a_rejudgment_the_archive_does_not_hold_is_a_gap_and_no_call():
    log = []
    judge = RP._Judge([], "rejudge", "exact", log)
    with pytest.raises(RP.ReplayGap) as e:
        judge({}, {"model": "m", "instructions": "A", "input": "x"})
    assert e.value.gap == "new_call_not_in_archive" and log[0]["hit"] is False
