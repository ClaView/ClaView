"""Each table reads the period the question attaches to ITS statistic (A -> B per-table period conditions).

The real BService runs over the synthetic selection model and synthetic fetch client (values are synthetic, wiring
and period rules only, no external call). The cases live in `eval/cases/b/period/table_period_context_b.json`;
their expected values are the per-table intent of the question, written before the code was changed.

`python modules/b/tests/b_part/test_b_table_period_context.py [--out FILE]` prints the record of every case:
original question -> per-table received condition -> applied sources and requested range -> read range
(calculation range included) -> the final fetch parameters.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from claview_b.period_condition import phrase_fragments, table_period_query
from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
from claview_b_api.service import BService, store

DATA = Path(__file__).resolve().parents[4] / "eval/cases/b/period/table_period_context_b.json"
CASES = json.loads(DATA.read_text(encoding="utf-8"))["cases"]


def condition(table: dict, index: int) -> dict | None:
    raw = table["condition"]
    if raw is None:
        return None
    sid = f"s{index + 1}"
    return {
        "frequency": raw["frequency"],
        "frequency_source": raw["frequency_source"],
        "period": [{"start": raw["period"][0], "end": raw["period"][1]}],
        "period_source": raw["period_source"],
        "statistics": [sid],
        **({"period_phrase": raw["phrase"]} if raw["phrase"] else {}),
        "answers": [
            {
                "kind": kind,
                "question_id": f"q-{sid}-{kind}",
                "question_version": 1,
                "statistics": [sid],
                "option_id": None,
                "value": raw["frequency"] if kind == "frequency" else raw["phrase"],
                "text": None,
                "source": "user_answer",
            }
            for kind in raw["answers"]
        ],
    }


def request_tables(case: dict) -> list[dict]:
    out = []
    for index, table in enumerate(case["tables"]):
        meta = store().get("301", table["table_id"])
        row = {"org_id": "301", "table_id": table["table_id"], "table_name": meta["table_name"], "focus": table["focus"]}
        cond = condition(table, index)
        out.append({**row, "period_condition": cond} if cond else row)
    return out


def run(case: dict) -> dict:
    """The record of one case: what B got, what it applied and read, and what it would ask KOSIS."""
    client = SyntheticFetchClient()
    service = BService(SyntheticSelectionModel(store()), client, "synthetic")
    tables = request_tables(case)
    outcome, _ = service.resolve(case["query"], tables, case["as_of"])
    record = {"id": case["id"], "query": case["query"], "received": tables, "status": outcome["status"],
              "issues": [i["code"] for i in outcome.get("issues") or []], "tables": {}}
    if outcome["status"] != "ok":
        return record
    result = outcome["result"]
    for row in result.get("period_resolutions") or []:
        record["tables"][row["table_id"]] = {
            "sources": row["applied_sources"],
            "requested": [row["frequency"], row["requested"][0]["start"], row["requested"][0]["end"]],
            "fetched": [row["frequency"], row["fetched"][0]["start"], row["fetched"][0]["end"]],
            "extra": [row["frequency"], row["calculation_extra"][0]["start"], row["calculation_extra"][0]["end"]]
            if row["calculation_extra"]
            else None,
        }
    service.fetch(result)
    for params in client.calls:
        record["tables"].setdefault(params["tblId"], {})["fetch"] = [
            params["prdSe"], params["startPrdDe"], params["endPrdDe"]
        ]
    return record


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_each_table_reads_its_own_period(case):
    record = run(case)
    assert record["status"] == "ok", record["issues"]
    for table_id, expected in case["expect"].items():
        got = record["tables"][table_id]
        for field, value in expected.items():
            assert got.get(field) == value, f"{case['id']} {table_id} {field}: {got.get(field)} != {value}"


def phrases(**by_table):
    return {key: {"period_phrase": phrase} for key, phrase in by_table.items()}


def test_phrase_fragments_split_the_quotes_a_joined():
    assert phrase_fragments("2023년 / 2024년") == ["2023년", "2024년"]
    assert phrase_fragments(None) == [] and phrase_fragments("") == [] and phrase_fragments("년") == []
    assert phrase_fragments(" 최근 3년 ") == ["최근 3년"]


def test_a_table_does_not_read_the_wording_attached_to_another_table():
    query = "M2 통화량 2023년과 예금은행 대출금리 2024년 연간"
    conditions = phrases(m2="2023년", rate="2024년")
    m2, rate = table_period_query(query, "m2", conditions), table_period_query(query, "rate", conditions)
    assert "2023년" in m2 and "2024" not in m2 and "2024" in rate and "2023" not in rate
    assert len(m2) == len(rate) == len(query)  # positions still line up
    assert "연간" in m2 and "연간" in rate  # the frequency wording is nobody's period phrase


def test_a_phrase_shared_by_tables_stays_visible_to_all_of_them():
    query = "M2 통화량과 예금은행 대출금리 2023년부터 2024년까지 연간"
    conditions = phrases(m2="2023년부터 2024년까지", rate="2023년부터 2024년까지")
    assert table_period_query(query, "m2", conditions) == query == table_period_query(query, "rate", conditions)


def test_a_short_phrase_inside_a_longer_one_stays_with_the_longer_one():
    query = "M2 통화량 2023년부터 2024년까지 그리고 예금은행 대출금리 2024년 연간"
    conditions = phrases(m2="2023년부터 2024년까지", rate="2024년")
    m2 = table_period_query(query, "m2", conditions)
    rate = table_period_query(query, "rate", conditions)
    assert "2023년부터 2024년까지" in m2 and m2.count("2024") == 1  # the rate's own "2024년" is hidden
    assert "2023" not in rate and rate.count("2024") == 1  # the longer phrase is hidden


def test_nothing_is_hidden_when_the_tables_own_phrase_is_not_in_the_question():
    query = "M2 통화량 2023년과 예금은행 대출금리 2024년 연간"
    for conditions in (
        {"m2": {"period_phrase": None}, "rate": {"period_phrase": "2024년"}},  # no phrase for this table
        phrases(m2="2023.1~12", rate="2024년"),  # spelled differently (or an answered period)
        {"m2": {}, "rate": {"period_phrase": "2024년"}},
    ):
        assert table_period_query(query, "m2", conditions) == query


def test_several_quotes_of_one_statistic_all_stay_with_it():
    query = "M2 통화량 2022년과 2023년 그리고 예금은행 대출금리 2024년"
    conditions = phrases(m2="2022년 / 2023년", rate="2024년")
    m2 = table_period_query(query, "m2", conditions)
    assert "2022년" in m2 and "2023년" in m2 and "2024" not in m2


def markdown() -> str:
    lines = ["| 사례 | 원질문 | 표 | 받은 조건(기간·출처·문구) | 적용 출처 | 요청 범위 | 조회 범위(계산용 포함) | 최종 조회 파라미터 |",
             "|---|---|---|---|---|---|---|---|"]
    for case in CASES:
        record = run(case)
        for table in record["received"]:
            cond = table.get("period_condition")
            received = (
                f"{cond['period'][0]['start']}~{cond['period'][0]['end']} · {cond['period_source']} · "
                f"{cond['frequency']}/{cond['frequency_source']} · 문구 {cond.get('period_phrase')!r}"
                if cond else "조건 없음"
            )
            got = record["tables"].get(table["table_id"], {})
            sources = got.get("sources") or {}
            lines.append(
                f"| {record['id']} | {record['query']} | {table['table_id']} | {received} | "
                f"기간 {sources.get('period', '-')} / 주기 {sources.get('frequency', '-')} | "
                f"{' '.join(got.get('requested') or ['-'])} | {' '.join(got.get('fetched') or ['-'])} | "
                f"{' '.join(got.get('fetch') or [record['status'] + ' ' + ','.join(record['issues'])])} |"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = markdown()
    if "--out" in sys.argv:
        Path(sys.argv[sys.argv.index("--out") + 1]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
