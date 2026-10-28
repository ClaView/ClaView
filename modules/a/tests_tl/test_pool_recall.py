"""Plan 37 step 1: pool recall and card size on the development questions, pinned as measured on 2026-10-28.

Not an evaluation: these questions were used to develop the search. The test only shows that the pool and cards
still behave as measured (a change shows up here first). Counts: every answer group has a table in the pool.
"""

from __future__ import annotations

import json
import statistics

import pytest

from claview_a.cards import CARD_LEGEND, build_cards, render_cards
from claview_a.contract import PAYLOAD_LIMIT, build_payload
from claview_a.pool import build_pool
from claview_a.transport.canonical import canonical

from claview_a import paths as _paths  # the repository's case files (expected table groups)
from tl_eval import files as F  # the evaluation folder outside the repository (the 40 questions)


def _rows():
    groups = json.loads((_paths.CASES_DIR / "expected_table_groups_v1.json").read_text(encoding="utf-8"))["cases"]
    yield from ((cid, c["question"], c["groups"]) for cid, c in groups.items())


def _check40():
    c40 = json.loads((F.external("check40_v1.json")).read_text(encoding="utf-8"))
    g16 = json.loads((F.external("golden16_v1.json")).read_text(encoding="utf-8"))
    for c in g16["cases"]:
        if c["case_id"] in c40["golden_cases"]:
            yield c["case_id"], c["question"], c["groups"]
    for c in c40["cases"]:
        groups = list(c.get("groups") or [])
        groups += [[t] for t in (c.get("options") or {}).values()]
        if c.get("acceptable_under_policy"):
            groups.append(list(c["acceptable_under_policy"]))
        yield c["case_id"], c["question"], groups


def _measure(rows, layer):
    found, sizes, missed, payloads = 0, [], [], []
    for cid, q, groups in rows:
        pool = build_pool(q, layer)
        cards = build_cards(q, pool, layer)
        tables = {t for card in cards for row in card["tables"] for t in [row["table_id"]]}
        if all(any(t in tables for t in g) for g in groups):
            found += 1
        else:
            missed.append(cid)
        sizes.append(len(CARD_LEGEND) + 1 + len(render_cards(cards)))  # the legend once, then all cards
        payloads.append(len(canonical(build_payload(q, cards))))  # step 2: refused over PAYLOAD_LIMIT
    return found, sizes, missed, payloads


MISSED = {
    "expected240": ["CV-AB-22", "PC:EQ-13", "PC:EQ-32", "PC:EQ-33", "PC:EQ-41", "PC:EQ-42", "B:CX-Q088", "B:CX-Q091",
                    "B:CX-Q093"],
    "check40": ["CV-AB-22"],
}
# Size (user 2026-10-28 05:05 UTC, cards tl-cards-2026-10-28.8 with every matched value shown): the required limit is
# the real input, the canonical payload (question, cards, ask axes) of at most ``PAYLOAD_LIMIT`` = 24,000 characters,
# which ``build_payload`` enforces by refusing (``input_too_large``), never by cutting. The 8,000-character card size
# (legend and cards) that was the limit while values were capped at three per word is now an observation of how sizes
# change: the questions over it are pinned as measured, so a change shows up here, but it is no limit.
CARD_SIZE_OBSERVED = 8000
# observed with cards .10 (catalog parent and unit shown, user 2026-10-28 06:37 UTC); with .8/.9 it was 240: CV-AB-03, 40: none
OVER_CARD_SIZE = {"expected240": ["A:CX-Q021", "A:CX-Q044", "B:CX-Q054", "B:CX-Q055", "CV-AB-03", "CV-AB-04", "CV-AB-16",
                                  "CV-AB-21"], "check40": ["CV-AB-21"]}


@pytest.mark.parametrize("name, rows", [("expected240", _rows), ("check40", _check40)])
def test_pool_recall_and_input_size_as_measured(layer, name, rows):
    ids = [r[0] for r in rows()]
    found, sizes, missed, payloads = _measure(list(rows()), layer)
    print(name, found, len(sizes), statistics.median(sizes), max(sizes), missed,
          "payload", statistics.median(payloads), max(payloads))
    assert missed == MISSED[name]
    assert max(payloads) <= PAYLOAD_LIMIT  # required
    assert sorted(cid for cid, n in zip(ids, sizes) if n > CARD_SIZE_OBSERVED) == OVER_CARD_SIZE[name]  # observed
