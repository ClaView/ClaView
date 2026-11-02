"""Plan 56 order 6-3a: a statistic kept to be aggregated is shown with its aggregation record and never handed on."""

from __future__ import annotations

from claview_a_api import view as V
from claview_common.contracts.a_view import StatisticView


def test_an_aggregation_candidate_is_shown_and_blocked():
    assert V.STATE_OF["aggregation_needed"] == "unverified"
    assert V.BLOCKED_CODE["aggregation_needed"] == "aggregation_needed"
    aggregation = {"status": "aggregation_needed", "requested": "Y", "source": "M", "method": "sum",
                   "method_status": "unconfirmed", "why": "evidence_name_or_unit_only",
                   "fetched_completeness": "not_checked"}
    row = StatisticView(request_id="x:s1", statistic="s1", quote="경상수지", name="경상수지",
                        outcome="aggregation_needed", state="unverified", verified=False, aggregation=aggregation)
    assert row.model_dump()["aggregation"] == aggregation and row.tables == []
