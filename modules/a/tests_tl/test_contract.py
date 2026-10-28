"""Plan 37 step 2: input assembly, output schema and parsing on made-up model outputs (no model, no network)."""

from __future__ import annotations

import copy
import json
import re

import pytest

from conftest import fill_option_evidence  # noqa: E402

from claview_a import contract as K
from claview_a.cards import ASK_AXES, CARD_LEGEND, build_cards, refs, table_numbers
from claview_a.pool import build_pool
from claview_a.transport.canonical import canonical

LOAN = "2024년 1~3월 예금은행 가계대출 금리를 월별로 보여줘"
M2 = "2024년 1~3월 M2 총액을 월별로 보여줘"


@pytest.fixture(scope="module")
def loan(layer):
    cards = build_cards(LOAN, build_pool(LOAN, layer), layer)
    card = next(c for c in cards if "loan_basis" in {s["axis"] for s in c["splits"]})
    split = next(s for s in card["splits"] if s["axis"] == "loan_basis")
    num = table_numbers(card)
    by_value = {v[0]: f"{card['card_id']}.{num[t]}" for t, v in split["values"].items()}
    return cards, card, by_value


def empty() -> dict:
    return {"statistics": [], "splitters": [], "periods": [], "frequencies": [], "calculations": [], "choices": [],
            "asks": [], "missing": [], "alternatives": []}


def answer(by_value: dict) -> dict:
    """A made-up judgement of the loan question: one statistic, asked on the loan basis."""
    out = empty()
    out["statistics"] = [{"id": "s1", "quote": "예금은행 가계대출 금리", "name": "예금은행 가계대출 금리"}]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    out["asks"] = [{"for": "s1", "axis": "loan_basis",
                    "options": [{"value": v, "table": ref} for v, ref in by_value.items()]}]
    return out


# ---------------------------------------------------------------- input


def test_the_payload_holds_the_question_the_legend_once_the_cards_and_the_ask_axes(loan):
    cards, _, _ = loan
    payload = K.build_payload(LOAN, cards)
    assert set(payload) == {"question", "cards", "ask_axes"}
    assert payload["question"] == LOAN and payload["ask_axes"] == ASK_AXES
    assert payload["cards"].startswith(CARD_LEGEND) and payload["cards"].count("카드 읽는 법") == 1
    for card in cards:
        assert f"[{card['card_id']}] {card['name']}" in payload["cards"]
    assert canonical(K.build_payload(LOAN, cards)) == canonical(payload)  # deterministic


def test_a_payload_over_the_limit_is_refused_not_cut(loan, monkeypatch):
    cards, _, _ = loan
    monkeypatch.setattr(K, "PAYLOAD_LIMIT", 100)
    with pytest.raises(K.ContractError) as e:
        K.build_payload(LOAN, cards)
    assert e.value.code == "input_too_large"


def test_the_payload_carries_nothing_of_the_current_analysis(loan):
    cards, _, _ = loan
    text = canonical(K.build_payload(LOAN, cards))
    for word in ("segments", "conditions", "candidate_summary", "scopes", "relations", "table_evidence"):
        assert word not in text


# ---------------------------------------------------------------- schema and request


def _objects(schema):
    if schema.get("type") == "object":
        yield schema
        for sub in schema["properties"].values():
            yield from _objects(sub)
    elif schema.get("type") == "array":
        yield from _objects(schema["items"])


def test_the_schema_is_strict_everywhere():
    schema = K.output_schema()
    for obj in _objects(schema):
        assert obj["additionalProperties"] is False and obj["required"] == list(obj["properties"])
    assert set(schema["properties"]) == set(empty())
    asks = schema["properties"]["asks"]["items"]["properties"]["axis"]["enum"]
    assert asks == list(ASK_AXES)


def test_the_request_has_the_current_paths_shape():
    req = K.Settings("model-x").request({"question": "q", "cards": "", "ask_axes": {}})
    assert req["text"]["format"] == {"type": "json_schema", "name": K.SCHEMA_NAME, "strict": True,
                                     "schema": K.output_schema()}
    assert req["instructions"] == K.prompt_text() and req["store"] is False
    assert req["input"] == canonical({"question": "q", "cards": "", "ask_axes": {}})


def test_the_prompt_names_no_table_or_case():
    text = K.prompt_text()
    assert not re.search(r"DT_\d{3}Y\d{3}|CV-AB|CQ1|DA3", text)
    for key in empty():
        assert f"`{key}`" in text  # every output field is explained


def test_the_prompt_and_the_legend_say_the_choice_is_a_representative_not_the_final_table():
    text = K.prompt_text()
    assert "대표 표" in text and "최종 표가 아니다" in text
    assert "요청 내용·기준·기간·주기" in text and "요청 내용·기준·기간·주기" in CARD_LEGEND
    assert "서로 대체" in text and "서로 대체" in CARD_LEGEND
    assert "축마다 한 행씩" in text and "`other`" in text and "되묻기 축이 아니고" in text


# ---------------------------------------------------------------- parsing


def test_a_good_answer_parses_and_its_card_references_resolve(loan):
    cards, card, by_value = loan
    got = K.parse_output(json.dumps(fill_option_evidence(answer(by_value), cards), ensure_ascii=False), cards)
    assert got["problems"] == []
    back = {f"{card['card_id']}.{n}": t for t, n in table_numbers(card).items()}
    assert got["tables"] == {ref: back[ref] for ref in by_value.values()}
    assert len(set(got["tables"].values())) == len(by_value) >= 2  # one table per asked value


def test_a_choice_by_real_table_id_is_accepted(loan):
    cards, card, by_value = loan
    out = answer(by_value)
    real = refs(cards)[next(iter(by_value.values()))]
    out["asks"] = []
    out["choices"] = [{"for": ["s1"], "table": real, "evidence": [{"kind": "bracket", "text": "신규취급액 기준"}]}]
    got = K.parse_output(json.dumps(fill_option_evidence(out, cards), ensure_ascii=False), cards)
    assert got["tables"] == {real: real} and got["problems"] == []


def test_an_unknown_reference_is_a_problem_not_a_crash(loan):
    cards, _, by_value = loan
    out = answer(by_value)
    out["asks"] = []
    out["choices"] = [{"for": ["s1"], "table": "g99.t1", "evidence": [{"kind": "title", "text": "x"}]}]
    out["alternatives"] = [{"for": ["s1"], "table": "DT_000Y000", "why": "x"}]
    got = K.parse_output(json.dumps(fill_option_evidence(out, cards)), cards)
    assert {p["ref"] for p in got["problems"]} == {"g99.t1", "DT_000Y000"}
    assert got["tables"]["g99.t1"] is None and {p["code"] for p in got["problems"]} == {"table_ref_unknown"}


def chosen(o: dict, by_value: dict, evidence=None) -> dict:
    o["asks"] = []
    o["choices"] = [{"for": ["s1"], "table": next(iter(by_value.values())),
                     "evidence": [{"kind": "bracket", "text": "신규취급액 기준"}] if evidence is None else evidence}]
    return o


def missed(o: dict, words=("대출금리",)) -> dict:
    o["asks"] = []
    o["missing"] = [{"for": ["s1"], "search_words": list(words)}]
    return o


def two_stats(o: dict) -> dict:
    o["statistics"] = o["statistics"] + [{"id": "s2", "quote": "금리", "name": "금리"}]
    return o


@pytest.mark.parametrize("break_it, code", [
    (lambda o: "not json", "not_json"),
    (lambda o: {k: v for k, v in o.items() if k != "missing"}, "schema"),
    (lambda o: {**o, "extra": []}, "schema"),
    (lambda o: {**o, "asks": [{**o["asks"][0], "axis": "frequency"}]}, "schema"),
    (lambda o: {**o, "statistics": [{**o["statistics"][0], "id": 1}]}, "schema"),
    (lambda o: {**o, "statistics": []}, "statistic_id"),
    (lambda o: {**o, "statistics": o["statistics"] * 2}, "statistic_id"),
    (lambda o: {**o, "statistics": [{**o["statistics"][0], "id": "x1"}]}, "statistic_id"),
    (lambda o: {**o, "periods": [{"quote": "2024년", "for": ["s9"]}]}, "statistic_ref"),
    (lambda o: {**o, "asks": [{**o["asks"][0], "for": "s2"}]}, "statistic_ref"),
])
def test_a_broken_answer_names_its_failure(loan, break_it, code):
    cards, _, by_value = loan
    broken = break_it(copy.deepcopy(answer(by_value)))
    text = broken if isinstance(broken, str) else json.dumps(fill_option_evidence(broken, cards), ensure_ascii=False)
    with pytest.raises(K.ContractError) as e:
        K.parse_output(text, cards)
    assert e.value.code == code


# the structure errors of the step-2 review (user 2026-10-27 21:12 UTC): rejected by the parser, each with its code
@pytest.mark.parametrize("break_it, code", [
    (lambda o, b: {**o, "statistics": [{**o["statistics"][0], "quote": "  "}]}, "empty_string"),
    (lambda o, b: {**o, "statistics": [{**o["statistics"][0], "name": ""}]}, "empty_string"),
    (lambda o, b: {**o, "asks": [{**o["asks"][0], "options": [{**o["asks"][0]["options"][0], "value": " "},
                                                              o["asks"][0]["options"][1]]}]}, "empty_string"),
    (lambda o, b: {**o, "periods": [{"quote": "2024년 1~3월", "for": []}]}, "empty_for"),
    (lambda o, b: {**o, "periods": [{"quote": "2024년 1~3월", "for": ["s1", "s1"]}]}, "duplicate_for"),
    (lambda o, b: chosen(o, b, evidence=[]), "empty_evidence"),
    (lambda o, b: missed(o, words=()), "empty_search_words"),
    (lambda o, b: {**o, "asks": [{**o["asks"][0], "options": o["asks"][0]["options"][:1]}]}, "ask_options_too_few"),
    (lambda o, b: {**o, "asks": [{**o["asks"][0], "options": [o["asks"][0]["options"][0]] * 2}]},
     "ask_option_value_duplicate"),
    (lambda o, b: {**o, "asks": o["asks"] * 2}, "ask_axis_repeated"),
    (lambda o, b: {**chosen(copy.deepcopy(o), b), "asks": o["asks"]}, "statistic_multi_kind"),
    (lambda o, b: {**missed(copy.deepcopy(o)), "asks": o["asks"]}, "statistic_multi_kind"),
    (lambda o, b: two_stats(o), "statistic_unplaced"),
    (lambda o, b: (lambda c: {**c, "choices": c["choices"] * 2})(chosen(o, b)), "statistic_repeated"),
    (lambda o, b: (lambda m: {**m, "missing": m["missing"] * 2})(missed(o)), "statistic_repeated"),
])
def test_a_structure_error_is_rejected_with_its_code(loan, break_it, code):
    cards, _, by_value = loan
    broken = break_it(copy.deepcopy(answer(by_value)), by_value)
    text = broken if isinstance(broken, str) else json.dumps(fill_option_evidence(broken, cards), ensure_ascii=False)
    with pytest.raises(K.ContractError) as e:
        K.parse_output(text, cards)
    assert e.value.code == code


def test_one_statistic_may_be_asked_on_several_axes(layer):
    cards = build_cards(M2, build_pool(M2, layer), layer)
    card = next(c for c in cards if {"stock", "seasonal"} <= {s["axis"] for s in c["splits"]})
    num = table_numbers(card)
    out = empty()
    out["statistics"] = [{"id": "s1", "quote": "M2 총액", "name": "M2 총액"}]
    for axis in ("stock", "seasonal"):
        split = next(s for s in card["splits"] if s["axis"] == axis)
        first: dict[str, str] = {}
        for t, vals in split["values"].items():
            first.setdefault(vals[0], f"{card['card_id']}.{num[t]}")
        out["asks"].append({"for": "s1", "axis": axis,
                            "options": [{"value": v, "table": ref} for v, ref in first.items()]})
    got = K.parse_output(json.dumps(fill_option_evidence(out, cards), ensure_ascii=False), cards)
    assert got["problems"] == [] and len(got["output"]["asks"]) == 2
    assert all(t is not None for t in got["tables"].values())


def test_the_good_answers_still_parse(loan):
    cards, _, by_value = loan
    for out in (answer(by_value), chosen(answer(by_value), by_value), missed(answer(by_value))):
        assert K.parse_output(json.dumps(fill_option_evidence(out, cards), ensure_ascii=False), cards)["problems"] == []
