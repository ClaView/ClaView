"""Stated years that only partly lie in the table's coverage (B reads the part inside and says what is missing).

Rule (2026-11-03): a question that states years is read where the table holds them. The part inside the coverage is
fetched, the part outside is `missing` (kind `requested_period_partly_unavailable`, a visible notice), and the stated
years stay the `requested` range. Nothing inside the coverage still stops (`metadata_refresh_required`): the stated
years are not replaced by another period, as a relative wording is. Part of the period before the first year of the
table, or a calculation range before it, is simply not read.

Unit cases use a small table; the end-to-end cases (`eval/cases/b/period/explicit_partial_coverage_b.json`, expected
values fixed by the rule before the code was changed) run the real BService over the synthetic model and fetch client
(synthetic values, no external call).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.b_part.test_b_unit import make_table

from claview_b.contracts import StageFailure
from claview_b.stages import explicit_period_detail
from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
from claview_b_api.service import BService, store

DATA = Path(__file__).resolve().parents[4] / "eval/cases/b/period/explicit_partial_coverage_b.json"
CASES = json.loads(DATA.read_text(encoding="utf-8"))["cases"]
TABLE = make_table(periods={"Y": {"start": "2010", "end": "2024"}})


def detail(query: str, table=TABLE):
    return explicit_period_detail(query, table, "Y")


def test_a_request_inside_the_coverage_is_unchanged():
    d = detail("2012년부터 2020년까지 연도별")
    assert d["period"] == {"frequency": "Y", "start": "2012", "end": "2020"}
    assert d["missing"] == [] and d["kind"] is None and d["message"] is None and d["extra"] is None


def test_the_part_after_the_end_of_the_table_is_missing():
    d = detail("2020년부터 2026년까지 연도별")
    assert d["period"] == {"frequency": "Y", "start": "2020", "end": "2024"}
    assert d["requested"] == (2020, 2026) and d["fetched"] == (2020, 2024) and d["missing"] == [(2025, 2026)]
    assert d["kind"] == "requested_period_partly_unavailable" and not d["substituted"]
    assert "2025~2026" in d["message"] and "조회 기간: 2020 ~ 2024" in d["message"]


def test_the_part_before_the_start_of_the_table_is_missing():
    d = detail("2005년부터 2012년까지 연도별")
    assert d["period"] == {"frequency": "Y", "start": "2010", "end": "2012"}
    assert d["missing"] == [(2005, 2009)] and d["kind"] == "requested_period_partly_unavailable"


def test_both_ends_can_be_missing_at_once():
    d = detail("2005년부터 2030년까지 연도별")
    assert d["period"] == {"frequency": "Y", "start": "2010", "end": "2024"}
    assert d["missing"] == [(2005, 2009), (2025, 2030)]


@pytest.mark.parametrize("query", ["2030년 연도별", "2031년부터 2035년까지 연도별", "2000년부터 2005년까지 연도별"])
def test_a_request_with_nothing_inside_the_coverage_still_stops(query):
    with pytest.raises(StageFailure) as stopped:
        detail(query)
    assert stopped.value.code == "metadata_refresh_required" and stopped.value.status == "unresolved"


def test_a_range_that_runs_backwards_is_still_an_error():
    with pytest.raises(StageFailure) as stopped:
        detail("2024년부터 2020년까지 연도별")
    assert stopped.value.code == "invalid_period"


def test_the_calculation_range_is_read_only_where_the_table_has_it():
    d = detail("2020년 연도별 전년 대비")
    assert d["fetched"] == (2019, 2020) and d["extra"] == (2019, 2019) and d["missing"] == []
    first = detail("2010년 연도별 전년 대비")  # the base year is before the table: nothing to read, no stop
    assert first["fetched"] == (2010, 2010) and first["extra"] is None and first["missing"] == []
    partly = detail("2024년부터 2026년까지 연도별 전년 대비")
    assert partly["fetched"] == (2023, 2024) and partly["extra"] == (2023, 2023) and partly["missing"] == [(2025, 2026)]


def tables_for(case: dict) -> list[dict]:
    out = []
    for index, table in enumerate(case["tables"]):
        meta = store().get("301", table["table_id"])
        row = {"org_id": "301", "table_id": table["table_id"], "table_name": meta["table_name"], "focus": table["focus"]}
        raw = table["condition"]
        if raw is not None:
            row["period_condition"] = {
                "frequency": raw["frequency"],
                "frequency_source": raw["frequency_source"],
                "period": [{"start": raw["period"][0], "end": raw["period"][1]}],
                "period_source": raw["period_source"],
                "statistics": [f"s{index + 1}"],
                "period_phrase": raw["phrase"],
                "answers": [],
            }
        out.append(row)
    return out


def run(case: dict) -> dict:
    client = SyntheticFetchClient()
    service = BService(SyntheticSelectionModel(store()), client, "synthetic")
    outcome, _ = service.resolve(case["query"], tables_for(case), case["as_of"])
    record = {"status": outcome["status"], "issues": [i["code"] for i in outcome.get("issues") or []], "tables": {}}
    if outcome["status"] != "ok":
        return record
    result = outcome["result"]
    for row in result.get("period_resolutions") or []:
        first, extra = row["requested"][0], row["calculation_extra"]
        record["tables"][row["table_id"]] = {
            "sources": row["applied_sources"],
            "requested": [row["frequency"], first["start"], first["end"]],
            "fetched": [row["frequency"], row["fetched"][0]["start"], row["fetched"][0]["end"]],
            "extra": [row["frequency"], extra[0]["start"], extra[0]["end"]] if extra else None,
            "missing": [[row["frequency"], m["start"], m["end"]] for m in row["missing"]],
            "notices": [n["code"] for n in row["notices"]],
        }
    service.fetch(result)
    for params in client.calls:
        record["tables"].setdefault(params["tblId"], {})["fetch"] = [params["prdSe"], params["startPrdDe"], params["endPrdDe"]]
    return record


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_the_service_reads_the_part_inside_the_coverage(case):
    record = run(case)
    assert record["status"] == "ok", record["issues"]
    for table_id, expected in case["expect"].items():
        for field, value in expected.items():
            assert record["tables"][table_id].get(field) == value, f"{case['id']} {field}"
