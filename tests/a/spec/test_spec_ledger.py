"""The specification ledger and the attribute map against the new side (plan 02, 4-1 and 4-3).

- tests/a/spec_ledger.csv: every row has a target and a treatment; changed/excluded rows have a reason; every
  moved/changed row has at least one specification case in each module it names, and every case belongs to such a
  row. A parametrized row's case carries a parameter text found in the ledger's parameter summary (catches a case
  written against the wrong row). Whether the ledger lists exactly the old test rows is checked in
  tests_v3_compare (it needs the old files).
- tests/a/attr_map.csv: every new path names a field of the new shapes.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

import pytest
import test_spec_basis_unit
import test_spec_breakdown_exclude
import test_spec_evidence
import test_spec_fragments
import test_spec_frequency
import test_spec_period
import test_spec_requirements
import test_spec_reused

from claview.a.contract import shapes

ROOT = Path(__file__).resolve().parents[3]
LEDGER = ROOT / "tests" / "a" / "spec_ledger.csv"
ATTR_MAP = ROOT / "tests" / "a" / "attr_map.csv"
FIELDS = ["key", "old_file", "old_function", "case", "params", "target", "treatment", "new_location", "reason"]
TARGETS = {"Q2", "Q3", "Q4", "Q5", "J2", "J3", "J4", "measure", "기타"}
MODULES = {f"tests/a/spec/{m.__name__}.py": m for m in (
    test_spec_frequency, test_spec_period, test_spec_basis_unit, test_spec_breakdown_exclude, test_spec_fragments,
    test_spec_requirements, test_spec_evidence, test_spec_reused)}


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    rows.insert(0, {"__fields__": reader.fieldnames})
    return rows


LEDGER_ROWS = _rows(LEDGER)
LEDGER_FIELDS = LEDGER_ROWS.pop(0)["__fields__"]
BY_KEY = {r["key"]: r for r in LEDGER_ROWS}


def _cases():
    for path, module in MODULES.items():
        for case in module.CASES:
            yield path, case


def test_ledger_header_and_unique_keys():
    assert LEDGER_FIELDS == FIELDS
    dup = [k for k, n in Counter(r["key"] for r in LEDGER_ROWS).items() if n > 1]
    assert dup == []


def test_ledger_rows_are_complete():
    problems = []
    for r in LEDGER_ROWS:
        t = r["treatment"]
        if t not in ("moved", "changed", "excluded", "new_candidate"):
            problems.append(f"{r['key']}: treatment {t!r}")
        if r["target"] not in TARGETS:
            problems.append(f"{r['key']}: target {r['target']!r}")
        if t in ("changed", "excluded", "new_candidate") and not r["reason"].strip():
            problems.append(f"{r['key']}: {t} without a reason")
        locations = [p for p in r["new_location"].split(";") if p]
        if t in ("moved", "changed") and not locations:
            problems.append(f"{r['key']}: {t} without a new location")
        if t in ("excluded", "new_candidate") and locations:
            problems.append(f"{r['key']}: {t} with a new location")
        problems += [f"{r['key']}: no module {p}" for p in locations if p not in MODULES or not (ROOT / p).is_file()]
        if t != "new_candidate" and r["key"] != f"{r['old_file']}::{r['old_function']}[{r['case']}]":
            problems.append(f"{r['key']}: key does not match file/function/case")
    assert problems == []


def test_every_moved_or_changed_row_has_cases_in_its_modules():
    have: dict[str, set[str]] = {}
    for path, case in _cases():
        have.setdefault(case["key"], set()).add(path)
    problems = []
    for r in LEDGER_ROWS:
        if r["treatment"] not in ("moved", "changed"):
            continue
        wanted = {p for p in r["new_location"].split(";") if p}
        if have.get(r["key"], set()) != wanted:
            problems.append(f"{r['key']}: ledger {sorted(wanted)} != cases {sorted(have.get(r['key'], set()))}")
    assert problems == []


def test_every_case_belongs_to_a_moved_or_changed_row():
    problems = []
    for path, case in _cases():
        row = BY_KEY.get(case["key"])
        if row is None or row["treatment"] not in ("moved", "changed"):
            problems.append(f"{path}: {case['key']} is not a moved/changed ledger row")
    assert problems == []


def test_parametrized_cases_point_at_their_row():
    problems = []
    for path, case in _cases():
        row = BY_KEY.get(case["key"])
        if row is None or row["case"] == "-" or case.get("param") is None:
            continue
        if " ".join(case["param"].split()) not in row["params"]:  # the summary collapses whitespace
            problems.append(f"{path}: {case['key']} param {case['param']!r} not in {row['params']!r}")
    assert problems == []


def test_ledger_summary_counts():
    """The counts reported for the bundle; a change here means the ledger changed."""
    old = [r for r in LEDGER_ROWS if r["treatment"] != "new_candidate"]
    counts = Counter(r["treatment"] for r in old)
    assert len(old) == sum(counts.values()) == 842
    assert len({(r["old_file"], r["old_function"]) for r in old}) == 318
    assert counts["excluded"] < counts["moved"] + counts["changed"]


# ---------------------------------------------------------------- attribute map

_PATH = re.compile(r"conditions\[kind=(?P<kind>\w+)\]\.(?P<rest>[\w.]+)")


def _attr_map() -> list[dict]:
    with ATTR_MAP.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize("row", _attr_map(), ids=lambda r: r["old_field"])
def test_attr_map_paths_name_new_fields(row):
    if not row["new_path"]:
        assert row["drop_reason"].strip()
        return
    m = _PATH.fullmatch(row["new_path"])
    assert m, row["new_path"]
    kind, parts = m["kind"], m["rest"].split(".")
    assert kind in shapes.ATTRS_BY_KIND
    assert parts[0] in shapes.Condition.__annotations__
    if parts[0] == "attrs":
        assert len(parts) == 2 and parts[1] in shapes.ATTRS_BY_KIND[kind].__annotations__
    else:
        assert len(parts) == 1
