"""Builds the three "axis 3" evidence sets under eval/cases/ (2026-10-06):

  golden_set_v1_axis3_h_recent_years.json    -- 반기(H) "최근 N년", N sweep,
                                                 both anchor halves (H1/H2)
  golden_set_v1_axis3_cross_frequency.json   -- the SAME table and the SAME N
                                                 asked at different
                                                 frequencies (H/Q/M/Y), so
                                                 frequency is the only
                                                 variable
  golden_set_v1_axis3_qm_controls.json       -- the existing 분기/월 "최근 N년"
                                                 goldens (CV-AB-07/12/15)
                                                 re-asked with other N and on
                                                 other anchors

Why these exist: the team deferred "does 반기 '최근 N년' align to calendar
years like 분기 does, or slide like 월 does?" (axis 3, 2026-10-02) until
there is more evidence. The only human-labeled evidence is golden_set_v1's
five "최근 N년" cases, and they were recorded at anchors that cannot tell all
candidate rules apart. This generator widens the period axis around those
cases without inventing any new human labels:

  sliding_window  S: start = anchor - (N * periods_per_year - 1)
                     (exactly N years of data ending at the anchor)
  calendar_anchor C: start = first period of calendar year (anchor_year - N)
                     (the rule the code applied to 분기 until 2026-10-06;
                     now a rejected alternative, kept as a named candidate)
  floor_to_year   F: start = first period of the calendar year that S falls in

All three are computed here with the project's pure period arithmetic, never
by calling resolve_relative_period -- the real code is only used afterwards to
(a) assert its output is one of the three and (b) record what it does today
as the case's `expected` period. So every case's expected period is "what the
code does now", NOT a newly confirmed correct answer; `policy_status` says
which cases sit on top of an existing human golden and which follow the
team's decisions (see `status_for`).

Outcome (2026-10-06): the team decided 반기 "최근 N년" is the sliding window
(S) -- "5년치 반기 데이터"는 10개가 상식, 11~12개는 이용자 기대에서 벗어남 -- and
that when the data for the period the user expects (as_of) does not exist yet,
the user is told so (needs_confirmation) rather than the window being shifted
silently. The same day the team applied that principle to 분기 as well ("5년치
분기"는 20개): the Q-only calendar-year exception (golden CV-AB-12, 22 quarters
for "5년") was removed, CV-AB-12's golden period corrected to 202103~202602, and
every frequency now uses the one count-back rule. S is therefore the code's
answer for all 30 cases; C and F stay in `period_candidates` as the documented
alternatives that were considered.

Regenerating keeps each case's recorded kosis_verification when its candidate
periods are unchanged (see carry_over_verification), so results obtained with
a real KOSIS key are not lost.

Run: PYTHONPATH=. python scripts/b_part/build_axis3_period_sets.py
"""

from __future__ import annotations

import json
from pathlib import Path

from build_period_augmented_cases import carry_over_verification, selection, table_ref

from claview.b.contracts import Config
from claview.b.metadata import MetadataStore, period_number
from claview.b.stages import _index_to_period, _year_start, resolve_relative_period

ROOT = Path(__file__).resolve().parents[2]
# Pinned like build_period_augmented_cases.py: the generated cases store this date.
CONFIG = Config(as_of="2026-10-01")
PER_YEAR = {"Y": 1, "H": 2, "Q": 4, "M": 12}
FREQ_WORD = {"H": "반기별로", "Q": "분기별로", "M": "월별로", "Y": "연도별로"}
SCHEMA_VERSION = "claview-ab/0.4-draft-period-augmented"

# Real catalog items/dimension codes. Everything except the two golden
# lookups is a combination already used (and confirmed present against the
# real KOSIS API) by an earlier case, or a simple "total-like" leaf pair on a
# table whose dimensions were read straight from the catalog; every case
# starts with kosis_verification=pending regardless, to be confirmed with
# scripts/b_part/verify_periods_kosis.py --file <set>.
TABLES = {
    "DT_105Y001": {
        "item": "13103134743999",
        "dims": {
            "objL1": "13102134743ACC_ITEM.1020000",
            "objL2": "13102134743GUBUN_CD.A",
            "objL3": "13102134743AMT_CD.A10",
        },
        "topic": "예금규모별 저축성예금 계좌수",
    },
    "DT_304Y101": {
        "item": "13103836578999",
        "dims": {"objL1": "13102836578GUBUN_CD.AA0200", "objL2": "13102836578ACC_ITEM.ISA0210"},
        "topic": "지식서비스 무역통계 수출(IT 개발 및 운영 서비스)",
    },
    "DT_303Y001": {
        "item": "13103134484999",
        "dims": {
            "objL1": "13102134484ACC_CD.REGEXASEANUSD",
            "objL2": "13102134484AMT_RATIO_CD.AMT",
        },
        "topic": "지역별 결제통화 수출 금액(달러 아세안)",
    },
    "DT_141Y008": {
        "item": "13103134513999",
        "dims": {"objL1": "13102134513REP_AGENCY.0710", "objL2": "13102134513RG_CD.X00"},
        "topic": "생명보험회사 전국 수신 말잔",
    },
    "DT_311Y001": {  # CV-AB-12's own table/selection
        "item": "13103134486999",
        "dims": {"objL1": "13102134486ACC_CD.3000000"},
        "topic": "순대외금융자산",
    },
    "DT_121Y006": {  # CV-AB-07's own table/selection (대기업대출)
        "item": "13103134553999",
        "dims": {"objL1": "13102134553ACC_ITEM.BECBLA0201"},
        "topic": "예금은행 대기업대출 금리",
    },
    "DT_513Y001": {
        "golden": "CV-AB-15",
        "topic": "경제심리지수(ESI, 원계열)",
    },  # filled from golden
    "DT_514Y001": {
        "item": "13103134672999",
        "dims": {"objL1": "13102134672ACC_CD.AA"},
        "topic": "대출행태서베이 국내은행 대출태도",
    },
    "DT_512Y014": {
        "item": "13103134488999",
        "dims": {
            "objL1": "13102134488BUSINESS_TYPE_CD.X5000",
            "objL2": "13102134488BSI_CD.BX",
        },
        "topic": "기업경기조사 대기업 기업심리지수 전망",
    },
}

# (case_id suffix, table_id, frequency, N)
SET_A = [  # 반기 only: H2-anchored table (202502) vs H1-anchored table (202601)
    ("01", "DT_105Y001", "H", 1),
    ("02", "DT_105Y001", "H", 2),
    ("03", "DT_105Y001", "H", 3),
    ("04", "DT_105Y001", "H", 10),
    ("05", "DT_304Y101", "H", 1),
    ("06", "DT_304Y101", "H", 3),
    ("07", "DT_304Y101", "H", 5),
    ("08", "DT_304Y101", "H", 10),
]
SET_B = [  # same table + same N, only the frequency changes
    ("01", "DT_303Y001", "H", 5),  # year-end anchors: H 202502 / Q 202504 / M 202512
    ("02", "DT_303Y001", "Q", 5),
    ("03", "DT_303Y001", "M", 5),
    ("04", "DT_303Y001", "H", 2),
    ("05", "DT_303Y001", "Q", 2),
    ("06", "DT_303Y001", "M", 2),
    ("07", "DT_304Y101", "H", 5),  # mid-year anchors: H 202601 / Q 202602 / Y 2025
    ("08", "DT_304Y101", "Q", 5),
    ("09", "DT_304Y101", "Y", 5),
    ("10", "DT_141Y008", "M", 5),  # mid-year anchors: M 202605 / Q 202601 / Y 2025
    ("11", "DT_141Y008", "Q", 5),
    ("12", "DT_141Y008", "Y", 5),
]
SET_C = [  # existing 분기/월 goldens re-asked with other N / other anchors
    ("01", "DT_311Y001", "Q", 1),  # CV-AB-12's table (anchor 202602), golden N=5
    ("02", "DT_311Y001", "Q", 3),
    ("03", "DT_311Y001", "Q", 10),
    ("04", "DT_121Y006", "M", 1),  # CV-AB-07's table (anchor 202607), golden N=3
    ("05", "DT_121Y006", "M", 5),
    ("06", "DT_121Y006", "M", 10),
    ("07", "DT_513Y001", "M", 1),  # CV-AB-15's table (anchor 202608), golden N=2
    ("08", "DT_513Y001", "M", 5),
    ("09", "DT_514Y001", "Q", 5),  # Q3 anchor (202603) -- no golden at this anchor
    ("10", "DT_512Y014", "M", 3),  # M09 anchor (202609) -- no golden at this anchor
]


def candidates(table: dict, freq: str, n: int) -> dict[str, dict]:
    """The three competing readings of "최근 N년", from pure period math."""
    bounds = table["periods"][freq]
    anchor_idx = period_number(bounds["end"], freq)
    cov_start_idx = period_number(bounds["start"], freq)
    anchor_year = int(bounds["end"][:4])
    sliding_idx = anchor_idx - (n * PER_YEAR[freq] - 1)
    sliding_year = int(_index_to_period(sliding_idx, freq)[:4])
    starts = {
        "sliding_window": sliding_idx,
        "calendar_anchor": period_number(_year_start(freq, anchor_year - n), freq),
        "floor_to_year": period_number(_year_start(freq, sliding_year), freq),
    }
    return {
        name: {
            "frequency": freq,
            "start": _index_to_period(max(idx, cov_start_idx), freq),
            "end": bounds["end"],
        }
        for name, idx in starts.items()
    }


def is_final_anchor(freq: str, anchor: str) -> bool:
    """True when the table's latest period is the last one of its calendar
    year (H2 / Q4 / M12 / any annual). At a final anchor S == F: both are
    exactly N full calendar years, and only C reaches back one year further."""
    return freq == "Y" or int(anchor[4:]) == PER_YEAR[freq]


def status_for(freq: str, final_anchor: bool) -> str:
    """Always "implemented" since 2026-10-06: every frequency follows the one
    sliding-window rule (N years = N * periods-per-year periods counted back
    from the table's own latest period). It is pinned by existing human
    goldens for 연 (CV-AB-10), 월 (CV-AB-07/13/15) and 분기 (CV-AB-12, expected
    period corrected to 202103 the same day), and by the team's decisions of
    2026-10-06 for 반기 and 분기. The parameters are kept so a future gap can
    be tagged "assumption_untested" again without changing call sites."""
    return "implemented"


def build_set(store: MetadataStore, prefix: str, specs: list, golden: dict) -> list[dict]:
    cases = []
    for suffix, table_id, freq, n in specs:
        table, tref = table_ref(store, "301", table_id)
        spec = TABLES[table_id]
        if "golden" in spec:
            g = golden[spec["golden"]]["expected"]["B"]["selections"][0]
            item_id = g["item_id"]
            dims = {axis: d["code"] for axis, d in g["dimensions"].items()}
        else:
            item_id, dims = spec["item"], spec["dims"]
        query = f"최근 {n}년간 {spec['topic']} 추이를 {FREQ_WORD[freq]} 보여줘"
        cands = candidates(table, freq, n)

        result = resolve_relative_period(query, table, CONFIG)
        actual = result["result"]["resolved_period"] if "result" in result else result["period"]
        matching = [name for name, p in cands.items() if p == actual]
        if not matching:
            raise AssertionError(f"{query!r}: code gives {actual}, matches none of {cands}")

        final = is_final_anchor(freq, table["periods"][freq]["end"])
        case_id = f"{prefix}-{suffix}"
        label = " / ".join(
            [table["table_name"]]
            + [table["dimensions"][a]["values"][c]["name"] for a, c in dims.items()]
        )
        cases.append(
            {
                "case_id": case_id,
                "query": query,
                "as_of": CONFIG.as_of,
                "policy_status": status_for(freq, final),
                "period_candidates": cands,
                "expected": {
                    "decision": "select",
                    "A": {"tables": [tref]},
                    "B": {
                        "selections": [
                            selection(table, item_id, dims, actual, f"{case_id}-S1", label)
                        ]
                    },
                },
                "kosis_verification": {"status": "pending", "notes": ""},
            }
        )
    return cases


def main() -> None:
    store = MetadataStore(
        ROOT / "data" / "catalog" / "search_catalog.json",
        ROOT / "data" / "raw" / "boK_meta_raw.jsonl",
    )
    golden = {
        c["case_id"]: c
        for c in json.loads(
            (ROOT / "eval" / "cases" / "golden" / "golden_set_v1.json").read_text(encoding="utf-8-sig")
        )["cases"]
    }
    outputs = [
        (
            "AX3-A",
            SET_A,
            "golden_set_v1_axis3_h_recent_years",
            "claview-golden-set-v1-axis3-h-recent-years-8",
        ),
        (
            "AX3-B",
            SET_B,
            "golden_set_v1_axis3_cross_frequency",
            "claview-golden-set-v1-axis3-cross-frequency-12",
        ),
        (
            "AX3-C",
            SET_C,
            "golden_set_v1_axis3_qm_controls",
            "claview-golden-set-v1-axis3-qm-controls-10",
        ),
    ]
    for prefix, specs, stem, dataset_id in outputs:
        cases = build_set(store, prefix, specs, golden)
        payload = {"schema_version": SCHEMA_VERSION, "dataset_id": dataset_id, "cases": cases}
        out = ROOT / "eval" / "cases" / "b" / "period" / f"{stem}.json"
        kept = carry_over_verification(out, cases)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {len(cases)} cases to {out} ({kept} kept their recorded kosis_verification)")


if __name__ == "__main__":
    main()
