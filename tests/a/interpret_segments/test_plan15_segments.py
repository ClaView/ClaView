"""Plan 15 r3 step 2: segments, mentions, uncollapsed catalog candidates and the ref map (3.1-3.3, appendix A 3).

Synthetic catalog (made-up "가상" names and table IDs) for the unit tests; the real catalog only for the loop over the
417 questions (questions only, no answers). No network, no model.
"""

from __future__ import annotations

import copy
import json

import pytest
from catalog_synth import write_inputs, write_raw

from claview.a import paths
from claview.a.catalog.layer import load_layer
from claview.a.catalog.occurrences import ROLE_OF_TYPE, OccurrenceIndex, occurrences_of, role_of
from claview.a.contract.check import check_analysis
from claview.a.interpret.conditions import find_conditions
from claview.a.interpret.mentions import build_mentions, build_ref_map
from claview.a.interpret.pipeline import interpret_question
from claview.a.interpret.segments import build_segments
from claview.a.interpret.text import normalize_text
from claview.a.reused.model import ModelError

NEW_FIELDS = ("segments", "mentions", "ref_map")

TABLES = [
    ("T_A", "가상대출금", "Synthetic Loans", [
        ("REG", "지역코드별", "REGION", [("ra1", "가상서울", None, None, None), ("ra2", "가상부산", None, None, None)]),
        ("ODD", "가상구분별", "ODD", [("oa1", "가상서울", None, None, None), ("oa2", "가상기타", None, None, None)]),
    ]),
    ("T_B", "가상예금(말잔)", "Synthetic Deposits", [
        ("REG", "지역코드별", "REGION", [("rb1", "가상서울", None, None, None)]),
        ("ACC", "BSI코드별", "BSI", [("ab1", "가상예금액", None, None, None), ("ab2", "말잔", None, None, None)]),
    ]),
]


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan15_segments")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


def joined(analysis: dict) -> str:
    return "".join(s["quote"] for s in analysis["segments"])


def by_kind(analysis: dict, kind: str) -> list[dict]:
    return [m for m in analysis["mentions"] if m["kind"] == kind]


def quote_of(analysis: dict, mention: dict) -> str:
    seg = {s["segment_id"]: s for s in analysis["segments"]}
    return "".join(seg[i]["quote"] for i in mention["segment_ids"])


def names_quoted(analysis: dict, quote: str) -> list[dict]:
    return [m for m in by_kind(analysis, "name") if quote_of(analysis, m) == quote]


# ---------------------------------------------------------------- exact restoration (3.1)

QUESTIONS = [
    "가상서울 가상대출금을 지역별로 보여줘",
    "  가상대출금을\n\n가상서울·가상부산으로   나눠서 보여줘!!  ",
    "가상예금（말잔）을　보여줘",  # full-width brackets and an ideographic space (raw kept, read through NFKC)
    "\t가상대출금\r\n보여줘",
    "가상대출금(가상서울)과 [가상부산] {가상기타}?",
    "가상서울을가상부산과가상기타를보여줘",  # particles and a request word written against the names
    "모르는말 가상기타를 뭐라뭐라 해줘?",  # unknown words
    "가상서울, 가상부산 말고 가상기타... 가상대출금 알려 주세요",
    "",
    "   ",
]


@pytest.mark.parametrize("question", QUESTIONS)
def test_segments_restore_the_raw_question_exactly(layer, question):
    a = interpret_question(question, layer)
    assert joined(a) == a["text"]["raw"] == question
    pos = 0
    for n, s in enumerate(a["segments"], start=1):
        assert s["segment_id"] == f"s{n}"
        assert s["span"][0] == pos and s["span"][1] > pos and s["quote"] == question[s["span"][0]:s["span"][1]]
        pos = s["span"][1]
    assert pos == len(question)
    assert check_analysis(a, question) == []


@pytest.mark.parametrize("question", QUESTIONS)
def test_segments_without_a_catalog_layer(question):
    a = interpret_question(question, None)
    assert joined(a) == question and check_analysis(a, question) == []
    assert all(m["candidates"] == [] for m in a["mentions"])


def test_segment_kinds(layer):
    a = interpret_question("가상대출금（가상서울）을　보여줘", layer)
    kinds = [(s["quote"], s["kind"]) for s in a["segments"]]
    assert kinds == [("가상대출금", "name"), ("（", "punct"), ("가상서울", "name"), ("）", "punct"), ("을", "grammar"),
                     ("　", "space"), ("보여줘", "request")]
    d = interpret_question("가상예금（말잔）을 보여줘", layer)  # a table name written with full-width brackets: one name
    assert [(s["quote"], s["kind"]) for s in d["segments"]][:2] == [("가상예금（말잔）", "name"), ("을", "grammar")]
    b = interpret_question("가상서울·가상부산을 나눠서 보여줘", layer)
    kinds = [(s["quote"], s["kind"]) for s in b["segments"]]
    assert ("·", "marker") in kinds and ("을", "grammar") in kinds and ("나눠서", "marker") in kinds
    c = interpret_question("모르는말 가상기타를 뭐라뭐라", layer)
    assert [(s["quote"], s["kind"]) for s in c["segments"]] == [
        ("모르는말", "word"), (" ", "space"), ("가상기타", "name"), ("를", "grammar"), (" ", "space"),
        ("뭐라뭐라", "word")]


def test_segments_are_stable(layer):
    q = "가상서울 가상대출금과 가상서울 가상예금액을 나눠서 보여줘"
    assert interpret_question(q, layer)["segments"] == interpret_question(q, layer)["segments"]
    assert interpret_question(q, layer)["mentions"] == interpret_question(q, layer)["mentions"]


def _questions_417() -> list[str]:
    if not paths.QUESTIONS_417.exists():
        return []
    return [r["question"] for r in json.loads(paths.QUESTIONS_417.read_text(encoding="utf8"))]


@pytest.mark.skipif(not (paths.CATALOG_SEARCH.exists() and paths.CATALOG_META_RAW.exists()
                         and paths.QUESTIONS_417.exists()), reason="catalog raw files or question file absent")
def test_every_question_of_the_417_restores_and_passes_the_contract():
    real = load_layer(include_external_synonyms=False)
    questions = _questions_417()
    assert len(questions) == 417
    bad = []
    for q in questions:
        a = interpret_question(q, real)
        if joined(a) != q or check_analysis(a, q):
            bad.append(q)
    assert bad == []


# ---------------------------------------------------------------- mentions (3.2)


def test_a_repeated_name_gives_two_mentions(layer):
    a = interpret_question("가상서울 가상대출금과 가상서울 가상예금액", layer)
    seoul = names_quoted(a, "가상서울")
    assert len(seoul) == 2
    assert seoul[0]["mention_id"] != seoul[1]["mention_id"] and seoul[0]["segment_ids"] != seoul[1]["segment_ids"]
    assert seoul[0]["candidates"] == seoul[1]["candidates"] != []


def test_a_name_under_a_condition_and_the_condition_are_both_kept(layer):
    """The condition token took the name over (``used_names``): both occurrences are mentions over one segment."""
    a = interpret_question("가상예금 말잔 보여줘", layer)
    (basis,) = [c for c in a["conditions"] if c["kind"] == "basis"]
    (cond,) = [m for m in by_kind(a, "condition") if m["condition_ids"] == [basis["id"]]]
    (name,) = names_quoted(a, basis["quote"])
    assert name["segment_ids"] == cond["segment_ids"]
    assert cond["mention_id"] in name["overlaps"] and name["mention_id"] in cond["overlaps"]
    assert any(c["role"] == "basis" and c["value_id"] is not None for c in name["candidates"])


def test_a_condition_inside_a_name_is_kept(layer):
    a = interpret_question("가상예금(말잔) 보여줘", layer)
    (basis,) = [c for c in a["conditions"] if c["kind"] == "basis"]
    (cond,) = [m for m in by_kind(a, "condition") if m["condition_ids"] == [basis["id"]]]
    (name,) = names_quoted(a, "가상예금(말잔)")
    assert cond["segment_ids"] == name["segment_ids"] and cond["mention_id"] in name["overlaps"]


def test_every_condition_keeps_its_id(layer):
    a = interpret_question("가상대출금을 가상서울·가상부산으로 나눠서 지역별로 보여줘", layer)
    got = sorted(i for m in by_kind(a, "condition") for i in m["condition_ids"])
    assert got == sorted(c["id"] for c in a["conditions"]) != []


def test_markers_are_recorded_without_scope(layer):
    a = interpret_question("가상대출금과 가상서울·가상부산을 나눠서 보여주고 가상기타 말고 가상부산", layer)
    markers = {(quote_of(a, m), m["marker"]) for m in by_kind(a, "marker")}
    assert {("과", "list"), ("·", "list"), ("나눠서", "divide"), ("말고", "negation")} <= markers
    assert all(m["condition_ids"] == [] and m["candidates"] == [] for m in by_kind(a, "marker"))


def test_a_divide_condition_carries_its_marker(layer):
    a = interpret_question("가상대출금을 가상서울·가상부산으로 나눠서 보여줘", layer)
    (fn,) = [c for c in a["conditions"] if c["kind"] == "function"]
    (m,) = [m for m in by_kind(a, "condition") if m["condition_ids"] == [fn["id"]]]
    assert m["marker"] == "divide"


def test_request_words_with_spaces_are_one_request_mention(layer):
    a = interpret_question("가상대출금 알려 주세요", layer)
    (req,) = by_kind(a, "request")
    assert quote_of(a, req) == "알려 주세요"


def test_mention_ids_follow_the_text(layer):
    a = interpret_question("가상서울 가상대출금을 지역별로 보여줘", layer)
    ids = [m["mention_id"] for m in a["mentions"]]
    assert ids == [f"m{n}" for n in range(1, len(ids) + 1)]
    order = {s["segment_id"]: n for n, s in enumerate(a["segments"])}
    starts = [order[m["segment_ids"][0]] for m in a["mentions"]]
    assert starts == sorted(starts)


def test_build_functions_alone(layer):
    text = normalize_text("가상서울 가상대출금")
    conds = find_conditions(text, layer)
    segs = build_segments(text, conds, layer)
    ments = build_mentions(text, conds, segs, layer)
    assert "".join(s["quote"] for s in segs) == text["raw"]
    assert [quote_of({"segments": segs}, m) for m in ments if m["kind"] == "name"] == ["가상서울", "가상대출금"]


# ---------------------------------------------------------------- candidates (3.3)


def _cands(a: dict, quote: str) -> list[dict]:
    (m,) = names_quoted(a, quote)
    return m["candidates"]


def test_candidates_keep_tables_axes_and_value_ids(layer):
    """One row per table, axis and value: never collapsed (``terms`` groups them and drops the item IDs)."""
    a = interpret_question("가상서울 가상대출금", layer)
    rows = {(c["table_id"], c["obj_id"], c["value_id"]) for c in _cands(a, "가상서울")}
    want = {(it.table_id, it.obj_id, it.item_id) for t in layer.catalog.tables.values() for ax in t.axes
            for it in ax.items if it.name == "가상서울"}
    assert rows == want and len({t for t, _, _ in rows}) == 2  # two tables
    assert len({o for t, o, _ in rows if t == "T_A"}) == 2  # two axes of one table
    assert all(c["value_name"] == "가상서울" and c["path"] == "exact" for c in _cands(a, "가상서울"))


def test_a_table_name_has_its_table_and_its_item_row(layer):
    a = interpret_question("가상대출금 보여줘", layer)
    cands = _cands(a, "가상대출금")
    table = layer.catalog.tables["T_A"]
    assert {"table_id": "T_A", "obj_id": None, "value_id": None, "value_name": table.name, "role": "table_name",
            "path": "exact", "type": "series_name"} in cands
    (item,) = table.item_rows
    assert {"table_id": "T_A", "obj_id": "ITEM", "value_id": item.item_id, "value_name": item.name,
            "role": "indicator", "path": "exact", "type": "item_row"} in cands


def test_an_axis_name_is_a_breakdown_candidate(layer):
    a = interpret_question("가상대출금을 가상구분별로 지역별로 보여줘", layer)
    exact = _cands(a, "가상구분별")
    assert exact == [{"table_id": "T_A", "obj_id": "ODD", "value_id": None, "value_name": "가상구분별",
                      "role": "breakdown", "path": "exact", "type": "axis_name"}]
    part = _cands(a, "지역별")
    assert {(c["table_id"], c["obj_id"], c["value_id"], c["role"], c["path"]) for c in part} == {
        ("T_A", "REG", None, "breakdown", "lexical"), ("T_B", "REG", None, "breakdown", "lexical")}


def test_an_unmapped_role_stays_unclassified(layer):
    a = interpret_question("가상서울", layer)
    odd = [c for c in _cands(a, "가상서울") if c["obj_id"] == "ODD"]
    assert odd and all(c["role"] == "unclassified" and c["type"] == "unknown" for c in odd)
    assert all(c["role"] != "indicator" for c in odd)


@pytest.mark.parametrize("cand, role", [
    ({"kind": "table", "type": "series_name"}, "table_name"), ({"kind": "value", "type": "indicator"}, "indicator"),
    ({"kind": "value", "type": "compound"}, "indicator"), ({"kind": "value", "type": "target"}, "target"),
    ({"kind": "value", "type": "total_header"}, "target"), ({"kind": "value", "type": "basis"}, "basis"),
    ({"kind": "value", "type": "unknown"}, "unclassified"), ({"kind": "value", "type": None}, "unclassified"),
    ({"kind": "value", "type": "something_new"}, "unclassified"),
])
def test_role_mapping(cand, role):
    assert role_of(cand) == role
    assert "unknown" not in ROLE_OF_TYPE


def test_expand_never_collapses_tables_and_skips_out_of_scope():
    rows = OccurrenceIndex.expand([
        {"kind": "value", "type": "target", "table_ids": ["T1", "T2"], "obj_id": "O", "item_id": "i", "name": "n",
         "path": "exact", "variant": "name"},
        {"kind": "out_of_scope", "type": "out_of_scope", "table_ids": [], "obj_id": None, "item_id": None,
         "name": "x", "path": "exact", "variant": "out_of_scope"},
        {"kind": "value", "type": "indicator", "table_ids": ["T1"], "obj_id": "O", "item_id": "j", "name": "m",
         "path": "lexical", "variant": "no_marks"},
    ])
    assert [(r["table_id"], r["value_id"], r["role"], r["path"]) for r in rows] == [
        ("T1", "i", "target", "exact"), ("T2", "i", "target", "exact"), ("T1", "j", "indicator", "exact")]


def test_the_occurrence_layer_leaves_the_index_unchanged(layer):
    before = {k: layer.name_spans().lookup(k) for k in ("가상서울", "가상대출금", "말잔")}
    keys = set(layer.index.entries)
    occurrences_of(layer)
    assert {k: layer.name_spans().lookup(k) for k in before} == before and set(layer.index.entries) == keys


# ---------------------------------------------------------------- ref map (appendix A 3)


def test_ref_map_ties_fragments_segments_and_mentions(layer):
    a = interpret_question("가상서울 가상대출금을 지역별로, 가상예금 말잔 보여줘", layer)
    segs = {s["segment_id"]: s for s in a["segments"]}
    rm = a["ref_map"]
    assert set(rm["fragment_segments"]) == {f["id"] for f in a["fragments"]}
    for f in a["fragments"]:
        fa, fb = f["span"]
        want = [s["segment_id"] for s in a["segments"] if s["span"][0] < fb and fa < s["span"][1]]
        assert rm["fragment_segments"][f["id"]] == want != []
        covered = "".join(segs[i]["quote"] for i in want)
        assert f["quote"] in covered
    for sid, fids in rm["segment_fragments"].items():
        assert all(sid in rm["fragment_segments"][fid] for fid in fids)
    assert set(rm["segment_mentions"]) == set(segs)
    for m in a["mentions"]:
        assert all(m["mention_id"] in rm["segment_mentions"][s] for s in m["segment_ids"])
        frags = {fid for s in m["segment_ids"] for fid in rm["segment_fragments"][s]}
        assert set(rm["mention_fragments"][m["mention_id"]]) == frags
    assert build_ref_map(a["segments"], a["mentions"], a["fragments"]) == rm


def test_model_step_path_carries_the_new_fields(layer):
    """The model step builds its own analysis; the segments, mentions and ref map are those of the rule path."""
    calls = []

    class Fails:
        name = "fake"

        def complete(self, node, system, payload, schema):
            calls.append(node)
            raise ModelError("other", "fake failure")

    for q in ("가상서울 가상대출금을 지역별로 보여줘", "가상서울 가상부산", "가상기타 말고 가상부산"):
        got = interpret_question(q, layer, model=Fails())
        off = interpret_question(q, layer)
        assert all(got[f] == off[f] for f in NEW_FIELDS)
        assert check_analysis(got, q) == []
    assert calls  # at least one question went through the model step


# ---------------------------------------------------------------- contract checks (9)


def test_an_old_analysis_without_the_new_fields_passes(layer):
    a = interpret_question("가상서울 가상대출금을 지역별로 보여줘", layer)
    old = {k: v for k, v in a.items() if k not in NEW_FIELDS}
    assert check_analysis(old, a["text"]["raw"]) == []
    assert check_analysis(a, a["text"]["raw"]) == []


def _broken(a: dict, how: str) -> dict:
    b = copy.deepcopy(a)
    segs = b["segments"]
    if how == "drop":
        del segs[1]
    elif how == "quote":
        segs[0]["quote"] += "x"
    elif how == "swap":
        segs[0], segs[1] = segs[1], segs[0]
    elif how == "overlap":
        segs[1]["span"] = [segs[0]["span"][0], segs[1]["span"][1]]
        segs[1]["quote"] = b["text"]["raw"][segs[1]["span"][0]:segs[1]["span"][1]]
    elif how == "short":
        segs.pop()
    elif how == "duplicate_id":
        segs[1]["segment_id"] = segs[0]["segment_id"]
    elif how == "mention_unknown_segment":
        b["mentions"][0]["segment_ids"] = ["s999"]
    elif how == "mention_gap":
        b["mentions"][0]["segment_ids"] = [segs[0]["segment_id"], segs[2]["segment_id"]]
    elif how == "mention_unknown_condition":
        b["mentions"][0]["condition_ids"] = ["Z9"]
    elif how == "mention_bad_role":
        b["mentions"][0]["candidates"][0]["role"] = "guess"
    elif how == "mention_bad_overlap":
        b["mentions"][0]["overlaps"] = ["m999"]
    elif how == "ref_unknown_fragment":
        b["ref_map"]["fragment_segments"]["g999"] = [segs[0]["segment_id"]]
    elif how == "ref_unknown_segment":
        b["ref_map"]["segment_mentions"]["s999"] = []
    elif how == "ref_unknown_mention":
        b["ref_map"]["segment_mentions"][segs[0]["segment_id"]] = ["m999"]
    elif how == "no_segments":
        del b["segments"]
    return b


@pytest.mark.parametrize("how", ["drop", "quote", "swap", "overlap", "short", "duplicate_id",
                                 "mention_unknown_segment", "mention_gap", "mention_unknown_condition",
                                 "mention_bad_role", "mention_bad_overlap", "ref_unknown_fragment",
                                 "ref_unknown_segment", "ref_unknown_mention", "no_segments"])
def test_broken_segment_fields_fail(layer, how):
    a = interpret_question("가상서울 가상대출금을 지역별로 보여줘", layer)
    assert a["mentions"][0]["candidates"]
    assert check_analysis(_broken(a, how), a["text"]["raw"]) != []
