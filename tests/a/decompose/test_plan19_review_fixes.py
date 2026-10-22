"""Plan 19 independent review fixes (review of 2026-10-21, findings F1-F3; F4 is in
``decompose_settle/test_plan19_core_metric.py``).

- F1: a sense that merges spelling variants (spacing, a footnote mark written apart) carries one name with candidate
  rows of several spellings; the final reference check compares that name with the candidates' catalog names by the
  same approved equivalence (``decompose_payload.spelling_key``), the exact row membership unchanged. Bracket
  contents stay apart.
- F2: the settled ``terms`` of a ref hold only the candidate rows of the range it quotes (as ``selection_ref``), never
  the rows another mention of the same sense brought.
- F3: an invalid ``Mention.span`` is a contract violation and never reaches the range rule (no exception); a mention
  without a span keeps the segment-level rule of older runs.

Synthetic catalogs and hand-made rows (made-up "가상" names and table IDs); model answers are hand-written fake
proposals run through the gpt mode. IDs are found by their words, never fixed. No network, no model.
"""

from __future__ import annotations

import copy

import pytest
from catalog_synth import write_inputs, write_raw
from test_plan15_step3_fix import layer  # noqa: F401  (layer is a fixture)
from test_plan19_stage3 import R, proposal, req, run
from test_plan19_structure import PART_Q, part, structure

from claview.a.catalog.layer import load_layer
from claview.a.contract.check import check_analysis, same_catalog_name, structure_status
from claview.a.interpret.decompose_payload import build_senses, spelling_key
from claview.a.interpret.decompose_refs import inner_span, span_pair, subquote_quotes, whole_ref_quotes
from claview.a.interpret.decompose_settle import ALTERNATIVE, PRIMARY, Question, requirement_refs, settle_reading
from claview.a.interpret.decompose_structure import selection_ref

BASE = "가상예탁금"
SPACED, NOTED = "가상 예탁금", "가상예탁금 1)"  # spacing and a footnote mark written apart: one sense
STOCK, YOY = "가상예탁금(평잔)", "가상예탁금(전년동기비)"  # bracket contents: senses of their own
# the names are table names, so that each table's ITEM row (role indicator) is a candidate of the written name
VARIANT_TABLES = [(f"T_V{n}", name, f"Synthetic {n}",
                   [("ACC", "계정항목별", "ACC", [(f"v{n}", "가상요소", None, None, None)])])
                  for n, name in enumerate((BASE, SPACED, NOTED, STOCK, YOY), start=1)]
VARIANT_Q = f"{BASE} 보여줘"
NAME_DIFFERS = "name: differs from candidates"


@pytest.fixture(scope="module")
def variants_layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan19_review_fixes")
    search, meta = write_raw(folder / "raw", VARIANT_TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


# ---------------------------------------------------------------- F1: the merged sense passes the final check


def _merged(p) -> dict:
    """The indicator ref of the question's name, with the sense that merges the spelling variants of ``BASE``."""
    sid = p.s(BASE)
    (z,) = {r["sense_id"] for r in p.payload["candidate_summary"] if r["sense_id"] and r["role"] == "indicator"
            and sid in r["segment_ids"] and spelling_key(r["name"]) == spelling_key(BASE)}
    return proposal(p.reading([req("r1", R(sid, z=z))], p.bindings()))


def _indicator_ref(a: dict) -> dict:
    (ref,) = [x for x in a["decomposition"]["selection_refs"]["r1"] if x["place"] == "indicator"]
    return ref


def test_a_merged_spelling_sense_passes_verify_settle_and_the_final_check(variants_layer):
    a = run(VARIANT_Q, variants_layer, _merged)  # ``run`` asserts check_analysis == []
    assert a["decomposition"]["applied_source"] == "model"
    ref = _indicator_ref(a)
    spellings = {c["value_name"] for c in ref["candidates"]}
    assert len(spellings) >= 2 and ref["name"] in spellings  # several spellings under one name (precondition)
    assert {spelling_key(n) for n in spellings} == {spelling_key(BASE)}
    assert not {STOCK, YOY} & spellings  # bracket contents are never merged into the sense
    assert structure_status(a) == {"state": "valid", "problems": []}


@pytest.mark.parametrize("name, same", [
    (SPACED, True), (NOTED, True), ("가상 예탁금 1)", True),
    (STOCK, False), (YOY, False), ("가상예탁금(1)", False), ("가상예탁", False),
])
def test_the_final_check_reads_the_name_by_the_approved_equivalence_only(variants_layer, name, same):
    a = run(VARIANT_Q, variants_layer, _merged)
    renamed = copy.deepcopy(a)
    _indicator_ref(renamed)["name"] = name  # the indicator is no structure ref: only this check reads it
    got = check_analysis(renamed)
    assert (got == []) is same, got
    assert any(NAME_DIFFERS in x for x in got) is not same
    forged = copy.deepcopy(a)  # the exact row membership is unchanged: a row renamed is no raw candidate
    row = next(c for c in _indicator_ref(forged)["candidates"] if c["value_name"] != name)
    row["value_name"] = name
    assert any("not a raw mention candidate" in x for x in check_analysis(forged))


@pytest.mark.parametrize("a, b, same", [
    ("가상항목 (갑)", "가상항목(갑)", True), ("가상항목(갑) 1)", "가상항목(갑)", True),
    ("가상항목( 갑 )", "가상항목(갑)", True),
    ("가상항목(1)", "가상항목(2)", False), ("가상항목(2010)", "가상항목(2015)", False),
    ("가상항목(평잔)", "가상항목(전년동기비)", False), ("가상항목(갑)", "가상항목", False),
    (None, None, True), (None, "가상항목", False), ("가상항목", None, False),
])
def test_same_catalog_name_is_the_sense_tables_spelling_key(a, b, same):
    assert same_catalog_name(a, b) is same
    if a is not None and b is not None:
        assert same == (spelling_key(a) == spelling_key(b))  # one definition, no wider rule


# ---------------------------------------------------------------- F2: terms from the quoted range only

RAW2 = "가상지표 가상지표"


def _row(table: str) -> dict:
    return {"table_id": table, "obj_id": "ITEM", "value_id": f"{table}_v", "value_name": "가상지표",
            "role": "indicator", "path": "exact", "type": "item_row"}


def _shared_sense_question() -> tuple[Question, str]:
    """Two mentions of one name, each with its own table (T_A, T_B), one sense over both (``build_senses``)."""
    segments = [{"segment_id": "s1", "span": [0, 4], "quote": "가상지표", "kind": "name"},
                {"segment_id": "s2", "span": [4, 5], "quote": " ", "kind": "space"},
                {"segment_id": "s3", "span": [5, 9], "quote": "가상지표", "kind": "name"}]
    mentions = [{"mention_id": mid, "kind": "name", "segment_ids": [sid], "span": span, "condition_ids": [],
                 "marker": None, "candidates": [_row(table)], "overlaps": []}
                for mid, sid, span, table in (("m1", "s1", [0, 4], "T_A"), ("m2", "s3", [5, 9], "T_B"))]
    local = {**build_senses(mentions), "condition_segments": {}, "mention_spans": {"m1": [0, 4], "m2": [5, 9]}}
    (z,) = local["senses"]
    assert sorted(r["table_id"] for r in local["senses"][z]["rows"]) == ["T_A", "T_B"]  # precondition: one sense
    return Question({"raw": RAW2}, [], segments, mentions, local), z


def _reading(indicator: dict) -> dict:
    return {"requirements": [{"id": "r1", "indicator": indicator, "targets": [], "basis_refs": [], "context": None,
                              "breakdowns": []}],
            "condition_bindings": [], "relations": [], "polarity_links": [], "request_refs": [], "unused_refs": []}


@pytest.mark.parametrize("chosen", [True, False])
@pytest.mark.parametrize("reading_id", [PRIMARY, ALTERNATIVE])
def test_terms_and_selection_refs_hold_only_the_quoted_mentions_rows(chosen, reading_id):
    q, z = _shared_sense_question()
    for sid, table in (("s1", "T_A"), ("s3", "T_B")):
        ref = R(sid, z=z if chosen else None)
        settled = settle_reading(q, _reading(ref), [], reading_id)
        terms = [t for t in settled["terms"] if t["type"] != "out_of_scope"]
        assert terms and {t for term in terms for t in term["table_ids"]} == {table}
        assert all(t["path"] == "exact" for t in terms)
        refs = [selection_ref(q, place, x) for place, x in requirement_refs(_reading(ref)["requirements"][0])]
        # a chosen sense: its rows of the quoted range only; no chosen sense: no rows (selection_ref as before)
        assert [c["table_id"] for x in refs for c in x["candidates"]] == ([table] if chosen else [])


# ---------------------------------------------------------------- F3: an invalid mention span is a violation

BAD_SPANS = [[], [0], "x", "reversed", [0, 0], [True, 2], None]


def _target_mention(a: dict) -> int:
    (t,) = structure(a)["requirements"]["r1"]["targets"]
    (k,) = [k for k, m in enumerate(a["mentions"]) if m["kind"] == "name" and m.get("span") == t["span"]]
    return k


@pytest.mark.parametrize("bad", BAD_SPANS)
def test_an_invalid_mention_span_is_reported_and_never_read_as_a_range(layer, bad):
    a = run(PART_Q, layer, part)
    k = _target_mention(a)
    broken = copy.deepcopy(a)
    span = broken["mentions"][k]["span"]
    broken["mentions"][k]["span"] = [span[1], span[0]] if bad == "reversed" else bad
    got = check_analysis(broken)  # no exception
    assert any(x.startswith(f"mentions[{k}].span") for x in got), got
    status = structure_status(broken)  # no exception
    assert status["state"] == "invalid" and status["problems"], status
    assert any("not a raw mention candidate" in x for x in status["problems"])  # its rows are quoted by no ref


def test_a_mention_without_a_span_keeps_the_older_segment_rule(layer):
    a = run(PART_Q, layer, part)
    old = copy.deepcopy(a)
    del old["mentions"][_target_mention(a)]["span"]
    assert check_analysis(old) == [] and structure_status(old) == {"state": "valid", "problems": []}


@pytest.mark.parametrize("bad", [[], [0], "x", [3, 1], [2, 2], [-1, 2], [True, 2], ("a", "b")])
def test_the_range_rule_never_indexes_a_malformed_span(bad):
    seg_spans = {"s1": [0, 4]}
    assert span_pair(bad) is None
    assert inner_span(["s1"], bad, seg_spans) is False
    assert whole_ref_quotes(["s1"], bad, ["s1"], seg_spans) is False
    assert subquote_quotes(["s1"], bad, "s1", (0, 2)) is False
    # a valid span and no span read as before
    assert span_pair([0, 2]) == (0, 2) and inner_span(["s1"], [0, 2], seg_spans) is True
    assert whole_ref_quotes(["s1"], None, ["s1"], seg_spans) is True
    assert subquote_quotes(["s1"], None, "s1", (0, 2)) is None
    assert subquote_quotes(["s1"], [0, 2], "s1", (0, 2)) is True
