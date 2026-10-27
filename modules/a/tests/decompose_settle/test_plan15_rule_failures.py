"""Plan 15 r3 step 4, user decision 2026-10-20 22:10: rule failure sources are never copied because the structures
are the same; each is revalidated against the adopted reading, kept with its scope mapped to the reading's IDs when
its ground is still there, left out when the reading validly resolved it, and kept for the whole question
(``scope_unmapped``) when its scope cannot be mapped. A reading that is not certain always names a failed source.

Synthetic catalog (made-up "가상" names and table IDs); model answers are hand-written fake proposal sources. No
network, no model.
"""

from __future__ import annotations

import copy
import json

import pytest
from catalog_synth import write_inputs, write_raw

from claview_a.catalog.layer import load_layer
from claview_a.contract.check import check_analysis
from claview_a.interpret.decompose_run import DecompositionConfig, decompose
from claview_a.interpret.pipeline import interpret_question
from claview_a.select.needs import unsettled

TABLES = [
    ("T_A", "가상대출금", "Synthetic Loans", [
        ("REG", "지역코드별", "REGION", [("r0", "가상전국", None, None, None), ("ra1", "가상서울", None, "r0", None),
                                    ("ra2", "가상부산", None, "r0", None)]),
        ("ACC", "계정항목별", "ACC", [("a1", "가상가계대출", None, None, None)]),
        ("BASE", "기준년도구분별", "BASE YEAR", [("y1", "가상기준값", None, None, None)]),
    ]),
    ("T_B", "가상예금(말잔)", "Synthetic Deposits", [
        ("REG", "지역코드별", "REGION", [("rb1", "가상서울", None, None, None)]),
        ("ACC", "계정항목별", "BSI", [("ab1", "가상예금액", None, None, None)]),
    ]),
]


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan15_rule_failures")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


class P:
    def __init__(self, payload: dict):
        self.payload = payload

    def s(self, text: str, nth: int = 0) -> str:
        return [x["id"] for x in self.payload["segments"] if x["text"] == text][nth]

    def z(self, name: str, role: str) -> str:
        (z,) = {r["sense_id"] for r in self.payload["candidate_summary"] if r["name"] == name and r["role"] == role}
        return z


def R(*sids, z=None, sq=None) -> dict:
    return {"segment_ids": list(sids), "subquote": sq, "sense_id": z}


def req(rid="r1", indicator=None, targets=(), basis=()) -> dict:
    return {"id": rid, "indicator": indicator, "targets": list(targets), "basis_refs": list(basis), "context": None,
            "breakdowns": []}


def reading(reqs, bindings=(), request=(), unused=()) -> dict:
    return {"requirements": list(reqs), "condition_bindings": list(bindings), "relations": [], "polarity_links": [],
            "request_refs": list(request), "unused_refs": list(unused)}


def proposal(primary) -> dict:
    return {"primary": primary, "alternative": None, "ambiguities": []}


def _source(build):
    def source(payload, schema):
        return build(P(payload))
    return source


def run(question: str, layer, build) -> dict:
    a = interpret_question(question, layer, decomposition={"mode": "gpt", "proposal_source": _source(build)})
    assert check_analysis(a) == []
    assert json.loads(json.dumps(a, ensure_ascii=False)) == a
    assert a["decomposition"]["applied_source"] == "model"
    return a


def rule_rows(a: dict) -> list[dict]:
    return [x for x in a["judgement"]["failed_sources"] if x["detail"].startswith("rule:")]


def test_same_structure_with_its_ground_still_there_keeps_the_rule_failure_with_its_scope(layer):
    question = "가상기준값 보여줘"
    rules = interpret_question(question, layer)
    assert rules["judgement"]["certain"] is False
    assert {x["condition"] for x in rules["judgement"]["failed_sources"]} >= {"row_2_or_7", "inexact_term"}

    def build(p):  # the basis name as the indicator without a sense: kept open by its role (the rules read it so)
        return proposal(reading([req(indicator=R(p.s("가상기준값")))], request=[R(p.s("보여줘"))]))

    a = run(question, layer, build)
    assert {it["code"] for it in a["decomposition"]["verify"]["issues"]} == {"role_ambiguous"}
    assert a["decomposition"]["certainty"]["same_structure"] is True
    rows = {(x["condition"], x["detail"]): x for x in rule_rows(a)}
    assert rows[("row_2_or_7", "rule:unresolved")]["requirement_ids"] == ["r1"]
    assert rows[("inexact_term", "rule:catalog_basis")]["requirement_ids"] == ["r1"]
    assert rows[("no_indicator", "rule:no_indicator_term")]["requirement_ids"] == []  # question level as in the rules
    assert a["judgement"]["certain"] is False and unsettled(a, "r1")["all"] is True


def test_same_structure_with_its_ground_resolved_leaves_the_rule_failure_out(layer):
    question = "가상대출금에 대해 알려줘"
    rules = interpret_question(question, layer)
    assert rules["judgement"]["leftover"] == ["대해"]
    assert [x["condition"] for x in rules["judgement"]["failed_sources"]] == ["leftover_word"]

    def build(p):  # the leftover word validly read as a request word (the code confirms it)
        return proposal(reading([req(indicator=R(p.s("가상대출금"), z=p.z("가상대출금", "indicator")))],
                                request=[R(p.s("대해")), R(p.s("알려줘"))]))

    a = run(question, layer, build)
    d = a["decomposition"]
    assert d["verify"]["issues"] == [] and d["certainty"]["same_structure"] is True
    assert not [x for x in a["judgement"]["failed_sources"] if x["condition"] == "leftover_word"]
    # 7.2 still keeps it not certain, but (decision B) the only remaining reason, the rules being uncertain, is
    # recorded in decomposition.certainty and not as an active row
    assert a["judgement"]["certain"] is False
    assert a["judgement"]["failed_sources"] == []
    assert a["judgement"]["failed_conditions"] == []
    assert d["certainty"]["reason"] == "rules_uncertain_only"
    assert unsettled(a, "r1")["all"] is False


def test_a_leftover_word_still_open_keeps_the_rule_failure(layer):
    question = "가상대출금 모르는말 보여줘"

    def build(p):
        return proposal(reading([req(indicator=R(p.s("가상대출금"), z=p.z("가상대출금", "indicator")))],
                                request=[R(p.s("보여줘"))], unused=[{"ref": R(p.s("모르는말")), "reason": "unresolved"}]))

    a = run(question, layer, build)
    (row,) = [x for x in rule_rows(a) if x["condition"] == "leftover_word"]
    assert row["requirement_ids"] == [] and row["detail"] == "rule:leftover"


def test_a_ground_still_there_whose_scope_cannot_be_mapped_is_kept_for_the_whole_question(layer):
    question = "가상대출금 가상서울 가상예금액"
    rules = interpret_question(question, layer)
    (row,) = rules["judgement"]["failed_sources"]
    assert row["condition"] == "unused_name" and row["fragment_ids"]

    def build(p):
        return proposal(reading([req(indicator=R(p.s("가상대출금"), z=p.z("가상대출금", "indicator")),
                                     targets=[R(p.s("가상서울"), z=p.z("가상서울", "target"))])],
                                unused=[{"ref": R(p.s("가상예금액"), z=p.z("가상예금액", "indicator")),
                                         "reason": "unresolved"}]))

    cfg = DecompositionConfig(mode="gpt", proposal_source=_source(build))
    mapped = decompose(cfg, rules, layer)
    (kept,) = [x for x in rule_rows(mapped) if x["condition"] == "unused_name"]
    frag_ids = {f["id"] for f in mapped["fragments"]}
    assert kept["detail"] == "rule:unused" and kept["fragment_ids"] and set(kept["fragment_ids"]) <= frag_ids
    # the rule fragment narrowed to the unused name's own words: no fragment of the reading lies there
    narrowed = copy.deepcopy(rules)
    raw = narrowed["text"]["raw"]
    a = raw.index("가상예금액")
    for f in narrowed["fragments"]:
        if f["id"] in row["fragment_ids"]:
            f["span"], f["quote"] = [a, a + len("가상예금액")], "가상예금액"
    out = decompose(cfg, narrowed, layer)
    (kept,) = [x for x in rule_rows(out) if x["condition"] == "unused_name"]
    assert kept == {"condition": "unused_name", "requirement_ids": [], "detail": "rule:scope_unmapped:unused",
                    "condition_ids": [], "fragment_ids": []}
    assert out["judgement"]["certain"] is False and unsettled(out, "r1")["all"] is True


@pytest.mark.parametrize("question", ["가상기준값 보여줘", "가상대출금에 대해 알려줘", "가상대출금 모르는말 보여줘"])
def test_an_uncertain_reading_always_names_a_failed_source(layer, question):
    def build(p):
        names = [s["id"] for s in p.payload["segments"] if s["kind"] in ("name", "word")]
        first = names[0]
        z = next((r["sense_id"] for r in p.payload["candidate_summary"] if first in r["segment_ids"]
                  and r["role"] == "indicator"), None)
        return proposal(reading([req(indicator=R(first, z=z) if z else None)],
                                request=[R(s["id"]) for s in p.payload["segments"] if s["kind"] == "request"],
                                unused=[{"ref": R(s), "reason": "unresolved"} for s in names if s != first or not z]))

    a = run(question, layer, build)
    assert a["judgement"]["certain"] is False and a["judgement"]["failed_sources"]
