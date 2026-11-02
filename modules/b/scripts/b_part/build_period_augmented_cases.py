"""Builds eval/cases/b/period/golden_set_v1_period_augmented.json.

This is a generator script, not a one-off hand-typed file, because every
case needs a real item_id/dimension code pulled from the actual 349-table
catalog (never invented) and the item's real unit resolved through
claview_b.metadata.selected_unit the same way the runtime does. The period
for each case is computed independently here with the project's own pure
period-arithmetic helpers (period_number / _year_start / _index_to_period
-- mechanical date math, not a business-rule judgment call), and then
cross-checked against what claview_b.stages.resolve_relative_period actually
returns today, so any mismatch between "what the math says" and "what the
code does" is caught at generation time instead of silently baked in.

Run: python modules/b/scripts/b_part/build_period_augmented_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path

from claview_b.contracts import Config, StageFailure
from claview_b.metadata import MetadataStore, period_number, selected_unit
from claview_b.stages import _index_to_period, _year_start, resolve_relative_period

ROOT = Path(__file__).resolve().parents[4]
# The generated case file stores this date in every case, so regeneration is pinned to the date
# the checked-in cases were built with (the shared pytest `config` fixture uses it too).
CONFIG = Config(as_of="2026-10-01")
AS_OF_YEAR = int(CONFIG.as_of[:4])


def table_ref(store: MetadataStore, org_id: str, table_id: str) -> dict:
    table = store.get(org_id, table_id)
    return table, {"org_id": org_id, "table_id": table_id, "table_name": table["table_name"]}


def selection(
    table: dict, item_id: str, dims: dict[str, str], period: dict, series_id: str, label: str
) -> dict:
    return {
        "series_id": series_id,
        "label": label,
        "org_id": table["org_id"],
        "table_id": table["table_id"],
        "item_id": item_id,
        "dimensions": {
            axis: {"code": code, "name": table["dimensions"][axis]["values"][code]["name"]}
            for axis, code in dims.items()
        },
        "period": period,
        "unit": selected_unit(table, item_id, dims),
    }


def sliding_window(table: dict, freq: str, n: int, unit_per_year: int) -> dict:
    bounds = table["periods"][freq]
    anchor_idx = period_number(bounds["end"], freq)
    coverage_start_idx = period_number(bounds["start"], freq)
    start_idx = max(anchor_idx - (n * unit_per_year - 1), coverage_start_idx)
    return {"frequency": freq, "start": _index_to_period(start_idx, freq), "end": bounds["end"]}


def year_anchored(table: dict, freq: str, year: int) -> dict:
    bounds = table["periods"][freq]
    start = _year_start(freq, year)
    width = {"Y": 1, "H": 2, "Q": 4, "M": 12}[freq]
    end_idx = (
        period_number(start, freq) + (width - 1) if freq != "Y" else period_number(start, freq)
    )
    end = _index_to_period(end_idx, freq) if freq != "Y" else str(year)
    return {"frequency": freq, "start": start, "end": min(end, bounds["end"])}


def year_since(table: dict, freq: str, year: int) -> dict:
    bounds = table["periods"][freq]
    return {"frequency": freq, "start": _year_start(freq, year), "end": bounds["end"]}


def check_against_code(
    query: str, table: dict, expected_period: dict | None, expect_error: bool
) -> None:
    """Fails the generation run (not a silent case) if reality disagrees
    with what this script is about to bake into the golden file."""
    try:
        result = resolve_relative_period(query, table, CONFIG)
    except StageFailure as exc:
        if not expect_error:
            raise AssertionError(
                f"{query!r}: expected a resolvable period, got StageFailure {exc.code}"
            )
        return
    if expect_error:
        raise AssertionError(f"{query!r}: expected StageFailure, code resolved instead: {result}")
    actual = result["result"]["resolved_period"] if "result" in result else result["period"]
    if actual != expected_period:
        raise AssertionError(
            f"{query!r}: code gives {actual}, this script computed {expected_period}"
        )


def build(store: MetadataStore) -> list[dict]:
    cases = []

    # --- H: 최근 N년 (user-requested) -- status: implemented (2026-10-06:
    # 팀이 반기는 슬라이딩 윈도우로 확정함 -- "5년치 반기 데이터"는 10개가
    # 상식이며, 달력 연도 정렬(calendar_anchor, 12개)은 기대에서 벗어남.
    # calendar_anchor는 기각된 대안으로만 period_candidates에 남겨둠.) -----
    table, tref = table_ref(store, "301", "DT_105Y001")
    bounds = table["periods"]["H"]
    anchor_year = int(bounds["end"][:4])
    sliding = sliding_window(table, "H", 5, 2)
    # The rejected calendar-anchor alternative: start at the calendar-year
    # boundary N years back, end at the table's own actual coverage end --
    # NOT year_anchored's single-calendar-year span.
    calendar = year_since(table, "H", anchor_year - 5)
    query = "최근 5년간 예금규모별 계좌수를 반기별로 보여줘"
    check_against_code(query, table, sliding, expect_error=False)
    dims = {
        "objL1": "13102134743ACC_ITEM.1020000",
        "objL2": "13102134743GUBUN_CD.A",
        "objL3": "13102134743AMT_CD.A10",
    }
    cases.append(
        {
            "case_id": "CV-AUG-01",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {"sliding_window": sliding, "calendar_anchor": calendar},
            "expected": {
                "decision": "select",
                "A": {"tables": [tref]},
                "B": {
                    "selections": [
                        selection(
                            table,
                            "13103134743999",
                            dims,
                            sliding,
                            "CV-AUG-01-S1",
                            "예금규모별 계좌수 및 금액 / 저축성예금 / 계좌수(천좌) / 합계",
                        )
                    ]
                },
            },
        }
    )

    # --- H: 최근 N반기 (direct same-unit count) -- status: implemented ---
    query = "최근 6반기 예금규모별 계좌수를 반기별로 보여줘"
    direct = sliding_window(table, "H", 6, 1)
    check_against_code(query, table, direct, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-02",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {"sliding_window": direct},
            "expected": {
                "decision": "select",
                "A": {"tables": [tref]},
                "B": {
                    "selections": [
                        selection(
                            table,
                            "13103134743999",
                            dims,
                            direct,
                            "CV-AUG-02-S1",
                            "예금규모별 계좌수 및 금액 / 저축성예금 / 계좌수(천좌) / 합계",
                        )
                    ]
                },
            },
        }
    )

    # --- H: YYYY년 이후 -- status: implemented -----------------------------
    query = "2015년 이후 예금규모별 계좌수를 반기별로 보여줘"
    since = year_since(table, "H", 2015)
    check_against_code(query, table, since, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-03",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {"year_since": since},
            "expected": {
                "decision": "select",
                "A": {"tables": [tref]},
                "B": {
                    "selections": [
                        selection(
                            table,
                            "13103134743999",
                            dims,
                            since,
                            "CV-AUG-03-S1",
                            "예금규모별 계좌수 및 금액 / 저축성예금 / 계좌수(천좌) / 합계",
                        )
                    ]
                },
            },
        }
    )

    # --- H: 작년 -- status: implemented (2026-11-02 팀 결정으로 변경: "작년"은 as_of 달력의
    # 닫힌 한 해임. 2026-10-02에는 표의 anchor_year(최신 수록 연도) 기준으로 일괄 결정했으나,
    # 표 최신 연도가 2025인 연간 표에서 작년(2025)을 2024로 읽는 오류라서 되돌림.
    # 이 해 전체가 표에 있으면 그대로, 일부만 있으면 자르고 안내하며, 전혀 없으면 표의
    # 최신 연도로 대체하고 안내함. anchor_year_based는 이전 기준의 참고용으로만 남겨둠.
    # "올해"는 계속 표의 anchor_year 기준임. ---
    query = "작년 예금규모별 계좌수를 반기별로 보여줘"
    as_of_based = year_anchored(table, "H", AS_OF_YEAR - 1)
    anchor_based = year_anchored(table, "H", anchor_year - 1)
    check_against_code(query, table, as_of_based, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-04",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {
                "as_of_year_based": as_of_based,
                "anchor_year_based": anchor_based,
            },
            "expected": {
                "decision": "select",
                "A": {"tables": [tref]},
                "B": {
                    "selections": [
                        selection(
                            table,
                            "13103134743999",
                            dims,
                            as_of_based,
                            "CV-AUG-04-S1",
                            "예금규모별 계좌수 및 금액 / 저축성예금 / 계좌수(천좌) / 합계",
                        )
                    ]
                },
            },
        }
    )

    # --- H: 올해 -- status: implemented (2026-10-02 fix: 올해/작년 모두
    # anchor_year, 즉 표 자체의 최신 수록 연도 기준으로 계산하도록 바꾼 뒤
    # 하드 오류 없이 이 fallback 기간으로 바로 정상 처리됨) --------------
    query = "올해 예금규모별 계좌수를 반기별로 보여줘"
    fallback = year_anchored(table, "H", anchor_year)
    check_against_code(query, table, fallback, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-05",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {"anchor_year_fallback": fallback},
            "expected": {
                "decision": "select",
                "A": {"tables": [tref]},
                "B": {
                    "selections": [
                        selection(
                            table,
                            "13103134743999",
                            dims,
                            fallback,
                            "CV-AUG-05-S1",
                            "예금규모별 계좌수 및 금액 / 저축성예금 / 계좌수(천좌) / 합계",
                        )
                    ]
                },
            },
        }
    )

    # --- Q: 최근 N분기 (direct same-unit count) -- status: implemented -----
    table_q, tref_q = table_ref(store, "301", "DT_102Y001")
    query = "최근 8분기 본원통화 현금통화를 분기별로 보여줘"
    direct_q = sliding_window(table_q, "Q", 8, 1)
    check_against_code(query, table_q, direct_q, expect_error=False)
    dims_q = {"objL1": "13102134736ACC_ITEM.ABA203"}
    cases.append(
        {
            "case_id": "CV-AUG-06",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {"sliding_window": direct_q},
            "expected": {
                "decision": "select",
                "A": {"tables": [tref_q]},
                "B": {
                    "selections": [
                        selection(
                            table_q,
                            "13103134736999",
                            dims_q,
                            direct_q,
                            "CV-AUG-06-S1",
                            "본원통화 구성내역(말잔, 원계열) / 현금통화",
                        )
                    ]
                },
            },
        }
    )

    # --- Q: 작년 / 올해 on a 2025-lagging table -----------------------------
    # dims_qa picked 가계 및 비영리단체(원천) x 피용자보수 (household-side
    # compensation of employees RECEIVED) rather than 비금융법인(사용) x
    # 피용자보수 (non-financial-corporation-side, PAID) used in an earlier
    # draft: the corporation/paid combination is a valid classification code
    # in this table's metadata (is_leaf, no warnings) but KOSIS's real data
    # API returned err=30 "데이터가 존재하지 않습니다" for it on every period
    # tried (2024 and 2025 both) -- a genuinely empty data cell, not a period
    # or wire-code bug (confirmed 2026-10-02 via modules/b/scripts/b_part/verify_periods_kosis.py,
    # run by the user). Household compensation received is one of the most
    # routinely published figures in this kind of institutional-sector-income
    # table, so it is far less likely to hit the same empty-cell problem --
    # but this is still a guess until re-verified against the real API.
    table_qa, tref_qa = table_ref(store, "301", "DT_200Y162")
    bounds_qa = table_qa["periods"]["Q"]
    anchor_year_qa = int(bounds_qa["end"][:4])
    dims_qa = {"objL1": "13102844575AMT_CD.142", "objL2": "13102844575ACC_ITEM.101"}

    query = "작년 제도부문별 소득계정을 분기별로 보여줘"
    as_of_based_q = year_anchored(table_qa, "Q", AS_OF_YEAR - 1)
    anchor_based_q = year_anchored(table_qa, "Q", anchor_year_qa - 1)
    check_against_code(query, table_qa, as_of_based_q, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-07",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {
                "as_of_year_based": as_of_based_q,
                "anchor_year_based": anchor_based_q,
            },
            "expected": {
                "decision": "select",
                "A": {"tables": [tref_qa]},
                "B": {
                    "selections": [
                        selection(
                            table_qa,
                            "13103844575999",
                            dims_qa,
                            as_of_based_q,
                            "CV-AUG-07-S1",
                            "제도부문별 소득계정(명목, 분기) / 가계 및 비영리단체(원천) / 피용자보수",  # noqa: E501
                        )
                    ]
                },
            },
        }
    )

    query = "올해 제도부문별 소득계정을 분기별로 보여줘"
    fallback_q = year_anchored(table_qa, "Q", anchor_year_qa)
    check_against_code(query, table_qa, fallback_q, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-08",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {"anchor_year_fallback": fallback_q},
            "expected": {
                "decision": "select",
                "A": {"tables": [tref_qa]},
                "B": {
                    "selections": [
                        selection(
                            table_qa,
                            "13103844575999",
                            dims_qa,
                            fallback_q,
                            "CV-AUG-08-S1",
                            "제도부문별 소득계정(명목, 분기) / 가계 및 비영리단체(원천) / 피용자보수",  # noqa: E501
                        )
                    ]
                },
            },
        }
    )

    # --- M: YYYY년 이후 -- status: implemented (reuses CV-AB-07's table) ---
    table_m, tref_m = table_ref(store, "301", "DT_121Y006")
    dims_m = {"objL1": "13102134553ACC_ITEM.BECBLA0201"}
    query = "2018년 이후 예금은행 대기업대출 금리를 월별로 보여줘"
    since_m = year_since(table_m, "M", 2018)
    check_against_code(query, table_m, since_m, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-09",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {"year_since": since_m},
            "expected": {
                "decision": "select",
                "A": {"tables": [tref_m]},
                "B": {
                    "selections": [
                        selection(
                            table_m,
                            "13103134553999",
                            dims_m,
                            since_m,
                            "CV-AUG-09-S1",
                            "예금은행 대출금리(신규취급액 기준) / 대기업대출",
                        )
                    ]
                },
            },
        }
    )

    # --- M: 작년 / 올해 on a 2025-lagging table -----------------------------
    table_ma, tref_ma = table_ref(store, "301", "DT_303Y001")
    bounds_ma = table_ma["periods"]["M"]
    anchor_year_ma = int(bounds_ma["end"][:4])
    dims_ma = {"objL1": "13102134484ACC_CD.REGEXASEANUSD", "objL2": "13102134484AMT_RATIO_CD.AMT"}

    query = "작년 지역별 결제통화 수출을 월별로 보여줘"
    as_of_based_m = year_anchored(table_ma, "M", AS_OF_YEAR - 1)
    anchor_based_m = year_anchored(table_ma, "M", anchor_year_ma - 1)
    check_against_code(query, table_ma, as_of_based_m, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-10",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {
                "as_of_year_based": as_of_based_m,
                "anchor_year_based": anchor_based_m,
            },
            "expected": {
                "decision": "select",
                "A": {"tables": [tref_ma]},
                "B": {
                    "selections": [
                        selection(
                            table_ma,
                            "13103134484999",
                            dims_ma,
                            as_of_based_m,
                            "CV-AUG-10-S1",
                            "지역별 결제통화(수출) / 달러 (아세안) / 금액",
                        )
                    ]
                },
            },
        }
    )

    query = "올해 지역별 결제통화 수출을 월별로 보여줘"
    fallback_m = year_anchored(table_ma, "M", anchor_year_ma)
    check_against_code(query, table_ma, fallback_m, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-11",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {"anchor_year_fallback": fallback_m},
            "expected": {
                "decision": "select",
                "A": {"tables": [tref_ma]},
                "B": {
                    "selections": [
                        selection(
                            table_ma,
                            "13103134484999",
                            dims_ma,
                            fallback_m,
                            "CV-AUG-11-S1",
                            "지역별 결제통화(수출) / 달러 (아세안) / 금액",
                        )
                    ]
                },
            },
        }
    )

    # --- Y: 작년 / 올해 (reuses CV-AB-10's table; no Y table in the whole --
    # --- catalog reaches as_of's year, so this is the systemic case) -------
    table_y, tref_y = table_ref(store, "301", "DT_301Y015")
    bounds_y = table_y["periods"]["Y"]
    anchor_year_y = int(bounds_y["end"])
    dims_y = {"objL1": "13102134752ACC_CD.REG1100", "objL2": "13102134752RG_CD.US"}

    query = "작년 지역별 수출 규모를 연도별로 보여줘"
    as_of_based_y = year_anchored(table_y, "Y", AS_OF_YEAR - 1)
    anchor_based_y = year_anchored(table_y, "Y", anchor_year_y - 1)
    check_against_code(query, table_y, as_of_based_y, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-12",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {
                "as_of_year_based": as_of_based_y,
                "anchor_year_based": anchor_based_y,
            },
            "expected": {
                "decision": "select",
                "A": {"tables": [tref_y]},
                "B": {
                    "selections": [
                        selection(
                            table_y,
                            "13103134752999",
                            dims_y,
                            as_of_based_y,
                            "CV-AUG-12-S1",
                            "지역별 경상수지 / 수출 / 미국",
                        )
                    ]
                },
            },
        }
    )

    query = "올해 지역별 수출 규모를 연도별로 보여줘"
    fallback_y = year_anchored(table_y, "Y", anchor_year_y)
    check_against_code(query, table_y, fallback_y, expect_error=False)
    cases.append(
        {
            "case_id": "CV-AUG-13",
            "query": query,
            "as_of": CONFIG.as_of,
            "policy_status": "implemented",
            "period_candidates": {"anchor_year_fallback": fallback_y},
            "expected": {
                "decision": "select",
                "A": {"tables": [tref_y]},
                "B": {
                    "selections": [
                        selection(
                            table_y,
                            "13103134752999",
                            dims_y,
                            fallback_y,
                            "CV-AUG-13-S1",
                            "지역별 경상수지 / 수출 / 미국",
                        )
                    ]
                },
            },
        }
    )

    for case in cases:
        case["kosis_verification"] = {"status": "pending", "notes": ""}
    return cases


def carry_over_verification(out_path: Path, cases: list[dict]) -> int:
    """Regenerating a case file used to reset every case's kosis_verification
    to "pending", losing results that can only be produced on a machine with a
    real KOSIS key. Reuse the previously recorded verification for a case when
    the same case_id exists in the file being overwritten AND its candidate
    periods are unchanged (a changed period means the old check no longer
    describes this case). Returns how many cases kept their record."""
    if not out_path.exists():
        return 0
    old = {
        c["case_id"]: c
        for c in json.loads(out_path.read_text(encoding="utf-8-sig")).get("cases", [])
    }
    kept = 0
    for case in cases:
        previous = old.get(case["case_id"])
        if (
            previous
            and previous.get("period_candidates") == case["period_candidates"]
            and previous.get("kosis_verification", {}).get("status") not in (None, "pending")
        ):
            case["kosis_verification"] = previous["kosis_verification"]
            kept += 1
    return kept


def main() -> None:
    store = MetadataStore(
        ROOT / "data" / "catalog" / "search_catalog.json",
        ROOT / "data" / "raw" / "boK_meta_raw.jsonl",
    )
    cases = build(store)
    payload = {
        "schema_version": "claview-ab/0.4-draft-period-augmented",
        "dataset_id": "claview-golden-set-v1-period-augmented-13",
        "cases": cases,
    }
    out_path = ROOT / "eval" / "cases" / "b" / "period" / "golden_set_v1_period_augmented.json"
    kept = carry_over_verification(out_path, cases)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases to {out_path} ({kept} kept their recorded kosis_verification)")


if __name__ == "__main__":
    main()
