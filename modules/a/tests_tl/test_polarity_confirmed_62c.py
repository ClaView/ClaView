"""Plan 56 order 6-2c: the confirmed polarity answers of the counterexample sentences (user 2026-10-31 20:25 UTC).

Data: ``data/polarity_confirmed_62c.json``, copied from the confirmed file 극성정답_확정.json, sha256
5a171a4168f1b09a2999e3e48df565d158d7daee9bdfd657b1e7fdab228fe1cf (108 sentences with status confirmed; 70, an
incomplete input, and 103, a single answer held, are left out). The answers are polarity only: these tests never say a
question has no other condition (a frequency, a breakdown, a display form).

Each sentence's rows are written as the model of the polarity contract writes them (value, polarity, a quote of the
sentence for statistic s1) and read by ``check.stated``: required, excluded and allowed are kept as written, nothing
flips, an allowed value makes no condition, any_of passes with one value, and several required values pass only a
table giving them all. Tests of the old reader that fixed some of these sentences as unread stay as they are.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from claview_a import check as CH
from claview_a.conditions.words import BASIS_AXES

DATA = json.loads((Path(__file__).parent / "data" / "polarity_confirmed_62c.json").read_text(encoding="utf-8"))
SOURCE_SHA256 = "5a171a4168f1b09a2999e3e48df565d158d7daee9bdfd657b1e7fdab228fe1cf"
ROWS = DATA["rows"]


def splitters(case):
    return [{"quote": v["quote"], "axis": v["axis"], "value": v["value"], "polarity": v["polarity"], "for": ["s1"]}
            for v in case["values"]]


def test_the_data_is_the_confirmed_file():
    assert DATA["source_sha256"] == SOURCE_SHA256
    assert len(ROWS) == 108 and not {70, 103} & {r["no"] for r in ROWS}


@pytest.mark.parametrize("case", ROWS, ids=lambda c: f"{c['no']}")
def test_a_confirmed_sentence_written_with_its_polarity_is_kept(case):
    rows = splitters(case)
    for r in rows:  # a quote of the sentence, for the statistic, a value of its axis
        assert r["quote"] in case["text"] and r["value"] in BASIS_AXES[r["axis"]]
    for axis in {r["axis"] for r in rows}:
        mine = [(r["value"], r["polarity"]) for r in rows if r["axis"] == axis]
        st = CH.stated("", {}, "s1", axis, rows, polar=True)
        required = {v for v, p in mine if p == "required"}
        any_of = {v for v, p in mine if p == "any_of"}
        assert st["excluded"] == {v for v, p in mine if p == "excluded"}
        assert st["values"] == (required or any_of)
        assert not st["excluded"] & st["values"]  # nothing flips
        assert st["mode"] == ("all" if required else "any" if any_of else None)
        assert st["allowed"] is any(p == "allowed" for _, p in mine)
        if st["allowed"] and not st["values"] and not st["excluded"]:
            assert not st["undetermined"]  # no condition and nothing left to ask
        # the table check: every required value, one any_of value; an excluded value never passes
        if required:
            assert CH._meets(sorted(required), st)
            if len(required) > 1 and case["no"] != 84:  # two values of one axis: a table giving them all (order 6)
                for v in required:
                    assert not CH._meets([x for x in required if x != v], st)
        if any_of:
            for v in any_of:
                assert CH._meets([v], st)


def test_both_series_are_required_and_neither_is_excluded_from_the_whole_request():
    """No. 84 (user 20:25): the series choice requires both and excludes neither. Its display conditions (the
    result's table in one series, its graph in the other) are no polarity and are not checked here, nor whether one
    catalog table must give both."""
    case = next(c for c in ROWS if c["no"] == 84)
    st = CH.stated("", {}, "s1", "seasonal", splitters(case), polar=True)
    assert st["mode"] == "all" and st["values"] == {"원계열", "계절조정"} and not st["excluded"]


def test_the_copied_data_matches_an_explicitly_configured_source():
    source = os.environ.get("CLAVIEW_A_POLARITY_SOURCE")
    if not source:
        pytest.skip("set CLAVIEW_A_POLARITY_SOURCE for the optional source-file comparison")
    src = Path(source)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == SOURCE_SHA256
