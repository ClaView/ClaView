"""Old contract rows that the stretch-1 code already answers: the reused ``stamp_months`` (J3 stamp reading) and the
no-identifier rule for package code (old measure module check, now over all of ``modules/a/claview_a_legacy``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from spec_support import case_id, number_parts, stamp_months

S2 = "test_aflow_stage2_rules.py"
ME = "test_measure_evidence.py"
PKG = Path(__file__).resolve().parents[4] / "modules" / "a" / "claview_a_legacy"
# Identifier patterns the old check refused (case IDs and KOSIS table IDs); package code must not hold them.
FORBIDDEN_IDS = ("CV-AB-", "CX-Q", "DT_")

STAMPS = [("2001", (24012, 24023)), ("2001 2/4", (24015, 24017)), ("2001-Q2", (24015, 24017)),
          ("2001 2/2", (24018, 24023)), ("2001.03", (24014, 24014)), ("200103", (24014, 24014)), ("x", None)]

CASES = number_parts([
    *[{"key": f"{S2}::test_stamp_months[{n}]", "node": "stamp_months (reused)", "stamp": s, "months": m, "param": repr(s)}
      for n, (s, m) in enumerate(STAMPS, 1)],
    *[{"key": f"{S2}::test_period_envelope_uses_months_and_keeps_open_end[-]", "node": "stamp_months (reused)",
       "stamp": s, "months": None} for s in ("2020.13", "2020.00")],
    {"key": f"{ME}::test_measure_module_has_no_case_or_table_identifier_overrides[-]", "node": "package_scan"},
])
STAMP_CASES = [c for c in CASES if c["node"] == "stamp_months (reused)"]


@pytest.mark.parametrize("case", STAMP_CASES, ids=case_id)
def test_case_shape(case):
    months = case["months"]
    assert isinstance(case["stamp"], str)
    assert months is None or (isinstance(months, tuple) and len(months) == 2 and months[0] <= months[1])


@pytest.mark.parametrize("case", STAMP_CASES, ids=case_id)
def test_case_behaviour(case):
    assert stamp_months(case["stamp"]) == case["months"]


def test_package_code_has_no_case_or_table_identifiers():
    # The shared period package is kept byte for byte inside A (claview_a_legacy.period_core); one of its comments names a
    # golden case. It holds no identifier override; the check covers the code A writes.
    files = sorted(p for p in PKG.rglob("*.py") if not p.is_relative_to(PKG / "period_core"))
    assert files
    found = {str(p.relative_to(PKG)): [x for x in FORBIDDEN_IDS if x in p.read_text(encoding="utf-8")] for p in files}
    assert {k: v for k, v in found.items() if v} == {}
