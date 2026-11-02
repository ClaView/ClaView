"""Plan 56 order 6-3b: contract .4 (splitter value and polarity, the period meaning, the aggregation rows).

The current schema is strict and carries the new fields; an answer is read with the contract it names (a stored
record's, or the judge's own), never guessed from its content: an answer of the old shape read as .4 is an output
error (``first_round_output_error``), not read as .3; a .3 answer named as .3 is read as before. The request carries
each ask axis's values and the instructions' version. Synthetic answers only: the code path, not the model's reading.
"""

from __future__ import annotations

import json

import pytest
from conftest import fill_option_evidence  # noqa: E402
from test_contract import _objects
from test_polarity_62 import ref as m2_ref

from claview_a import contract as K
from claview_a.cards import ASK_AXES, build_cards
from claview_a.conditions.words import BASIS_AXES
from claview_a.graph import run
from claview_a.pool import build_pool

AS_OF = "2026-10-22"
OLD = "tl-contract-2026-10-28.3"
Q = "2024년 월별 M2 상품별 구성내역을 말잔 원계열로 보여줘"


def test_the_current_contract_is_4_and_3_is_read_only_as_a_legacy_one():
    # Contract .8 structures the period and frequency meaning; all earlier responses keep their declared shape.
    assert K.CONTRACT_VERSION == "tl-contract-2026-10-28.10" and "tl-contract-2026-10-28.4" in K.EARLIER_CONTRACTS
    assert OLD in K.LEGACY_CONTRACTS
    assert OLD in K.OPTION_EVIDENCE_CONTRACTS and K.CONTRACT_VERSION in K.OPTION_EVIDENCE_CONTRACTS
    assert K.PROMPT_VERSION.startswith("tl-prompt-")


def test_the_schema_of_contract_4_is_strict_and_carries_the_new_fields():
    # the quote-only period rows of contracts .4 to .7 (contract .8's structured rows: test_period_semantics_1102)
    schema = K.output_schema("tl-contract-2026-10-28.7")
    for obj in _objects(schema):
        assert obj["additionalProperties"] is False and obj["required"] == list(obj["properties"])
    props = schema["properties"]
    assert "aggregations" in props and "aggregations" not in K.output_schema(OLD)["properties"]
    split = props["splitters"]["items"]["properties"]
    assert split["polarity"]["enum"] == list(K.POLARITIES) and "value" in split
    period = props["periods"]["items"]["properties"]
    assert period["meaning"]["enum"] == list(K.PERIOD_MEANINGS) and "refers_to" in period
    agg = props["aggregations"]["items"]["properties"]
    assert agg["method"]["enum"] == list(K.METHODS) and agg["source_basis"]["enum"] == list(K.SOURCE_BASES)


def test_the_request_carries_the_axis_values():
    payload = K.build_payload("q", [])
    assert payload["basis_values"] == {axis: list(BASIS_AXES.get(axis, {})) for axis in ASK_AXES}


def m2_answer(cards, polarity=True, meaning=True, aggregations=True):
    card = next(c for c in cards if c["name"] == "M2 상품별 구성내역")
    out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                           "missing", "alternatives")}
    out["statistics"] = [{"id": "s1", "quote": "M2 상품별 구성내역", "name": "M2"}]
    out["splitters"] = [{"quote": "말잔", "axis": "stock", "for": ["s1"]},
                        {"quote": "원계열", "axis": "seasonal", "for": ["s1"]}]
    if polarity:
        out["splitters"] = [{**s, "value": s["quote"], "polarity": "required"} for s in out["splitters"]]
    out["periods"] = [{"quote": "2024년", "for": ["s1"], **({"meaning": "range", "refers_to": []} if meaning else {})}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    out["choices"] = [{"for": ["s1"], "table": m2_ref(cards, LAYER[0], stock="말잔", seasonal="원계열"),
                       "evidence": [{"kind": "title", "text": card["name"]}]}]
    if aggregations:
        out["aggregations"] = []
    return fill_option_evidence(out, cards)


LAYER: list = []


@pytest.fixture
def cards(layer):
    LAYER[:] = [layer]
    return build_cards(Q, build_pool(Q, layer), layer)


def test_a_contract_4_answer_is_parsed_and_checked_on_the_polarity_path(cards, layer):
    # the answer is written to contract .4 and read by it (plan 56 order 9-2: .6 is the current one)
    got = K.parse_output(json.dumps(m2_answer(cards), ensure_ascii=False), cards, "tl-contract-2026-10-28.4")
    assert got["answer_contract"] == "tl-contract-2026-10-28.4" and got["problems"] == []
    assert got["output"]["splitters"][0]["polarity"] == "required"


@pytest.mark.parametrize("missing", ["polarity", "meaning", "aggregations"])
def test_an_answer_without_a_field_of_contract_4_is_refused_never_read_as_3(cards, missing):
    text = json.dumps(m2_answer(cards, **{missing: False}), ensure_ascii=False)
    with pytest.raises(K.ContractError) as e:
        K.parse_output(text, cards, "tl-contract-2026-10-28.4")
    assert e.value.code == "schema"


def test_an_old_answer_is_read_with_the_contract_it_names(cards):
    text = json.dumps(m2_answer(cards, polarity=False, meaning=False, aggregations=False), ensure_ascii=False)
    assert K.parse_output(text, cards, OLD)["answer_contract"] == OLD


@pytest.mark.parametrize("bad, code", [
    (lambda o: o["periods"][0].update(meaning="same_as", refers_to=["s9"]), "statistic_ref"),
    (lambda o: o["aggregations"].append({"for": ["s9"], "requested_frequency": "Y", "source_frequency": "M",
                                         "method": "sum", "source_basis": "flow", "evidence": [], "weights": []}),
     "statistic_ref"),
    (lambda o: o["aggregations"].append({"for": [], "requested_frequency": "Y", "source_frequency": "M",
                                         "method": "sum", "source_basis": "flow", "evidence": [], "weights": []}),
     "empty_for"),
])
def test_the_new_references_name_statistics(cards, bad, code):
    out = m2_answer(cards)
    bad(out)
    with pytest.raises(K.ContractError) as e:  # rows of contract .4, read by it (plan 56 order 6-3c: .5 is current)
        K.parse_output(json.dumps(out, ensure_ascii=False), cards, "tl-contract-2026-10-28.4")
    assert e.value.code == code


def judge_of(text, contract):
    def judge(payload, request):
        judge.requests.append(request)
        return text

    judge.requests, judge.answer_contract = [], contract
    return judge


def test_through_the_graph_the_judges_contract_is_used_and_recorded(cards, layer):
    j = judge_of(json.dumps(m2_answer(cards), ensure_ascii=False), "tl-contract-2026-10-28.4")  # an answer of contract .4
    state = run(Q, layer, j, K.Settings("fake"), AS_OF)
    assert state["record"]["decisions"][0]["outcome"] == "selected", state["record"]["decisions"][0]["reasons"]
    assert state["run_info"]["versions"]["contract"] == "tl-contract-2026-10-28.4"
    assert state["run_info"]["versions"]["prompt"] == K.PROMPT_VERSION
    (req,) = j.requests
    assert req["text"]["format"]["schema"] == K.output_schema() and "basis_values" in json.loads(req["input"])


def test_through_the_graph_an_old_shape_answer_to_contract_4_is_an_output_error(cards, layer):
    old = json.dumps(m2_answer(cards, polarity=False, meaning=False, aggregations=False), ensure_ascii=False)
    state = run(Q, layer, judge_of(old, "tl-contract-2026-10-28.4"), K.Settings("fake"), AS_OF)
    assert state["error"]["stage"] == "output" and state["error"]["code"] == "schema"
    state = run(Q, layer, judge_of(old, OLD), K.Settings("fake"), AS_OF)  # named as .3: read as before
    assert state.get("error") is None and state["run_info"]["versions"]["contract"] == OLD
