"""The independent standard through the runner with the fake judge (plan 38, step 7 wiring): made-up questions on
real catalog tables, a standard file in a temporary evaluation folder, no model and no network. The fake judge's
scores say nothing about a model; only the wiring is checked."""

from __future__ import annotations

import json

import pytest

from claview_a.search.tables import selection_catalog
from tl_eval import files as F
from tl_eval import indep as I
from tl_eval import live as L
from tl_eval import report as R

AS_OF = "2026-10-29"
QUESTIONS = [("Q901", "T1", "2024년 소비자심리지수를 월별로 보여 줘", "소비자심리지수", "DT_511Y002"),
             ("Q902", "T1", "2024년 순상품교역조건지수를 월별로 보여 줘", "순상품교역조건지수", "DT_403Y005")]


@pytest.fixture()
def standard(layer, tmp_path, monkeypatch):
    monkeypatch.setattr(F, "EVAL_DIR", tmp_path)
    views = selection_catalog(layer).views
    rows = [{"qid": q, "type": t, "question": text, "expected": "select",
             "targets": [{"id": "r1", "text": quote, "occurrence": 1, "representative": [table], "equivalents": {},
                          "ask": None, "holds": {},
                          "final": {"frequency": "M", "basis": [], "point_in_period": None, "relative": [],
                                    "combinations": [{"id": "c1", "status": "confirmed",
                                                      "parts": [{"table": table, "frequency": "M",
                                                                 "from": "2024-01", "to": "2024-12"}]}]}}]}
            for q, t, text, quote, table in QUESTIONS]
    std = I.build(rows, AS_OF, "indep60", tables=set(views), coverage=R.coverage_of(views))
    path = tmp_path / "standard_indep_test.json"
    path.write_text(json.dumps(std, ensure_ascii=False), encoding="utf-8")
    return path


def test_fake_run_scores_the_indep_standard_end_to_end(standard, layer, tmp_path):
    cases = [f"IND-{q[0]}" for q in QUESTIONS]
    m = L.make_manifest("indep-wiring", "test", cases, as_of=AS_OF, subsets={"type:T1": cases},
                        standard_file=standard)
    assert m["max_calls"] == 2 and m["retries"] == 0 and m["approval"]["live_calls"] is False
    assert m["conditions"]["standard"]["version"] == I.INDEP_STANDARD_VERSION
    assert all(r["ok"] for r in L.gates(m))
    got = L.run(m, "fake", tmp_path / "run", layer=layer)
    assert got["calls"] == 2 and got["stopped"] is None and got["tokens"]["input"] == 0
    rep = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
    assert rep["evaluator"]["indep"] == I.INDEP_SCORE_VERSION and rep["note"].startswith("fake judge")
    assert rep["aggregate"]["indep60"]["questions"] == 2 and set(rep["aggregate"]["by_type"]) == {"T1"}
    assert rep["subsets"]["type:T1"]["aggregate"]["indep60"]["questions"] == 2
    assert rep["standard"]["sha256"] == m["conditions"]["standard"]["sha256"]


def test_manifest_refuses_a_case_outside_the_standard_or_another_as_of(standard):
    with pytest.raises(ValueError):
        L.make_manifest("x", "test", ["IND-Q999"], as_of=AS_OF, standard_file=standard)
    with pytest.raises(ValueError):
        L.make_manifest("x", "test", ["IND-Q901"], as_of="2026-10-22", standard_file=standard)
