"""Catalog loader and term index on a synthetic catalog (plan 02, 4-2). No network, no model, no question file."""

from __future__ import annotations

import hashlib

import pytest
from catalog_synth import write_inputs, write_raw

from claview.a.catalog.index import build_index
from claview.a.catalog.loader import Axis, Catalog, Item, Table, load_catalog
from claview.a.catalog.names import derived_names
from claview.a.catalog.roles import build_axis_roles
from claview.a.catalog.text import name_variants, paren_front


@pytest.fixture
def synth(tmp_path):
    search, meta = write_raw(tmp_path / "raw")
    inputs = write_inputs(tmp_path / "inputs")
    catalog = load_catalog(search, meta)
    roles = build_axis_roles(catalog, **inputs)
    return {"catalog": catalog, "roles": roles, "index": build_index(catalog, roles), "search": search,
            "meta": meta, "inputs": inputs}


# ---------------------------------------------------------------- loader


def test_loader_reads_tables_axes_values_with_english_names(synth):
    cat = synth["catalog"]
    reg = cat.tables["T_REG"]
    assert reg.name == "지역별 대출금(말잔)" and reg.name_front == "지역별 대출금"
    assert reg.name_eng == "Loans by Region" and reg.path == "한국은행 > 합성 > 지역별 대출금(말잔)"
    region = next(ax for ax in reg.axes if ax.obj_id == "REG")
    assert (region.name, region.name_eng) == ("지역코드별", "REGION")
    seoul = next(it for it in region.items if it.item_id == "r1")
    assert (seoul.name, seoul.name_eng, seoul.parent_id, seoul.depth) == ("서울", "Seoul", "r0", 2)
    account = next(ax for ax in reg.axes if ax.obj_id == "ACC")
    loan = next(it for it in account.items if it.item_id == "a1")
    assert loan.unit == "십억원" and loan.unit_eng == "Billion Won"
    assert reg.units == ({"name": "십억원", "name_eng": "Billion Won"},)
    assert [(p.freq, p.start, p.end) for p in reg.periods] == [("M", "2000.01", "2026.06"), ("Y", "2000", "2025")]
    assert [it.name for it in reg.item_rows] == ["지역별 대출금(말잔)"]  # ITEM row kept apart from the axes
    assert all(ax.obj_id != "ITEM" for ax in reg.axes)


def test_loader_records_sha256_of_both_raw_files(synth):
    src = synth["catalog"].sources
    for name, path in (("search_catalog", synth["search"]), ("meta_raw", synth["meta"])):
        assert src[name]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert src["source_date"] == "2026-01-01"


def test_loader_marks_unnamed_axis_and_value(synth):
    cat = synth["catalog"]
    assert {(u["table_id"], u["obj_id"], u["item_id"], u["what"]) for u in cat.unnamed} == {
        ("T_DUP", "NONAME", None, "axis"), ("T_DUP", "NONAME", "DATA", "value")}
    noname = next(ax for ax in cat.tables["T_DUP"].axes if ax.obj_id == "NONAME")
    assert not noname.named and not noname.items[0].named
    assert synth["roles"].value("T_DUP", "NONAME", "DATA").type == "unknown"


def test_loader_depth_and_orphan(tmp_path):
    tables = [("T_X", "깊이 표", "Depth", [("A", "계정항목별", "ACCOUNT", [
        ("1", "가", None, None, None), ("2", "나", None, "1", None), ("3", "다", None, "2", None),
        ("4", "라", None, "missing", None)])])]
    cat = load_catalog(*write_raw(tmp_path, tables))
    items = {it.item_id: it for it in cat.tables["T_X"].axes[0].items}
    assert [items[k].depth for k in "123"] == [1, 2, 3]
    assert items["4"].orphan and items["4"].depth == 1 and not items["3"].orphan


# ---------------------------------------------------------------- normalisation


def test_name_variants_footnote_marks_parentheses():
    assert name_variants("M2(말잔 계절조정계열) 1)") == {
        "name": "m2(말잔계절조정계열)1)", "no_footnote": "m2(말잔계절조정계열)", "no_paren": "m2"}
    assert name_variants("C10 제조업") == {"name": "c10제조업", "no_marks": "제조업"}
    assert name_variants("（1）　예금") == {"name": "(1)예금", "no_marks": "예금"}  # NFKC first


# ---------------------------------------------------------------- index


def test_index_english_names(synth):
    ix = synth["index"]
    seoul = ix.lookup("Seoul")
    assert [(c["name"], c["type"], c["table_ids"], c["obj_id"], c["item_id"], c["variant"], c["path"])
            for c in seoul] == [("Seoul", "target", ["T_REG"], "REG", "r1", "eng", "exact")]
    assert seoul[0]["obj_name"] == "지역코드별"
    assert {c["table_ids"][0] for c in ix.lookup("manufacturing")} == {"T_IND", "T_GEN", "T_CSV"}
    assert [c["table_ids"] for c in ix.lookup("loans by region")] == [["T_REG"]]


def test_index_keeps_every_candidate_of_one_name(synth):
    cands = synth["index"].lookup("제조업")
    by_table = {c["table_ids"][0]: c for c in cands}
    assert set(by_table) == {"T_IND", "T_GEN", "T_CSV"}
    assert by_table["T_IND"]["variant"] == "no_marks" and by_table["T_IND"]["path"] == "lexical"
    assert by_table["T_GEN"]["path"] == "exact"
    assert {c["type"] for c in cands} == {"target"}
    # "가계" is a target-axis value, an indicator-axis value and a pressed-axis value: every candidate stays
    gagye = synth["index"].lookup("가계")
    assert {(c["table_ids"][0], c["item_id"]) for c in gagye} == {("T_ORG", "o4"), ("T_L3", "d6"), ("T_PR", "p11"),
                                                                  ("T_PR", "p21")}


def test_index_groups_tables_sharing_a_name(synth):
    ix = synth["index"]
    table = [c for c in ix.lookup("같은 이름 표(월)") if c["kind"] == "table"]
    assert [(c["type"], c["table_ids"]) for c in table] == [("series_name", ["T_SAME1", "T_SAME2"])]
    value = [c for c in ix.lookup("현금통화") if c["item_id"] == "z1"]
    assert [c["table_ids"] for c in value] == [["T_SAME1", "T_SAME2"]]


def test_index_table_name_forms_and_footnote(synth):
    ix = synth["index"]
    full = [c for c in ix.lookup("지역별 대출금(말잔)") if c["kind"] == "table"]
    front = [c for c in ix.lookup("지역별대출금") if c["kind"] == "table"]
    assert full and front and full[0]["type"] == front[0]["type"] == "series_name"
    assert front[0]["variant"] == "table_front" and front[0]["path"] == "exact"
    footnote = [c for c in ix.lookup("현금통화") if c["table_ids"] == ["T_REG"]]
    assert [(c["name"], c["variant"], c["path"]) for c in footnote] == [("현금통화 1)", "no_footnote", "exact")]


def test_index_without_roles_gives_unknown_type(synth):
    ix = build_index(synth["catalog"])
    assert {c["type"] for c in ix.lookup("서울")} == {"unknown"}


# ---------------------------------------------------------------- name-preserving spans


def test_find_spans_longest_match_with_original_positions(synth):
    text = "서울　가계 대출과  상호저축은행,비은행금융기관을 보여줘"
    spans = synth["index"].find_spans(text)
    assert [s["quote"] for s in spans] == ["서울", "가계 대출", "상호저축은행", "비은행금융기관"]
    for s in spans:
        assert text[s["start"]:s["end"]] == s["quote"]
        assert s["candidates"] >= 1
    assert spans[1]["key"] == "가계대출"  # "가계" alone is a name too, the longer one wins


def test_find_spans_prefers_the_longest_overlap_and_skips_short_or_numeric(synth):
    spans = synth["index"].find_spans("예금은행 원화예금 요구불예금 2020 계")
    assert [s["quote"] for s in spans] == ["예금은행 원화예금", "요구불예금"]
    assert synth["index"].find_spans("") == []


def test_find_spans_handles_fullwidth_and_case(synth):
    text = "ＳＥＯＵＬ and busan"
    spans = synth["index"].find_spans(text)
    assert [(s["quote"], s["key"]) for s in spans] == [("ＳＥＯＵＬ", "seoul"), ("busan", "busan")]


# ---------------------------------------------------------------- derived names (plan 09 P1)


def _catalog(rows) -> Catalog:
    """A catalog in memory: rows of (table ID, table name, list path, [value names on one axis])."""
    tables = {}
    for tid, name, path, values in rows:
        items = tuple(Item(tid, "A", f"a{k}", v, None, None, 1, None, None) for k, v in enumerate(values))
        axes = (Axis(tid, "A", "계정항목별", None, 1, items),) if items else ()
        tables[tid] = Table(tid, None, name, None, None, paren_front(name), path, None, axes, (), (), (), True)
    return Catalog(tables=tables, sources={})


def _derived(cat) -> dict[str, set]:
    out: dict[str, set] = {}
    for d in derived_names(cat):
        out.setdefault((d.name, d.variant), set()).add(d.table_id)
    return out


def test_table_core_needs_a_shared_first_word_or_a_path_node():
    cat = _catalog([
        ("T1", "가상은행 가상대출금리(신규 기준)", "가상 > 통계 > 가상금리", []),
        ("T2", "가상은행 가상수신금리(신규 기준)", "가상 > 통계 > 가상금리", []),
        ("T3", "가상단독 가상여신잔고(말잔)", "가상 > 통계 > 가상여신", []),  # one family of 가상단독, no path node
        ("T4", "가상홀로 가상예탁금리", "가상 > 통계 > 가상예탁금리", []),  # one family, but a path node in its name
    ])
    got = _derived(cat)
    assert got[("가상대출금리", "table_core")] == {"T1"} and got[("가상수신금리", "table_core")] == {"T2"}
    assert got[("가상예탁금리", "table_core")] == {"T4"}
    assert not any(name == "가상여신잔고" for name, _ in got)


def test_table_core_blocks_values_xbyeol_words_fillers_and_joiner_first_words():
    cat = _catalog([
        ("T1", "가상은행 가상예금(말잔)", "가상 > 통계", ["가상예금"]),  # the rest is a value name
        ("T2", "가상은행 지역별", "가상 > 통계", []),  # an X-byeol rest
        ("T3", "가상은행 통계", "가상 > 통계", []),  # a filler rest
        ("T4", "가상은행 가상총수신(말잔)", "가상 > 통계", []),
        ("T5", "가상생산과 가상지출(명목)", "가상 > 통계", []),  # the first word ends in a joiner
        ("T6", "가상생산과 가상소득(명목)", "가상 > 통계", []),
    ])
    names = {name for name, _ in _derived(cat)}
    assert "가상총수신" in names
    assert not names & {"가상예금", "지역별", "통계", "가상지출", "가상소득"}


def test_path_node_tail_word_and_abbreviation():
    cat = _catalog([
        ("T1", "가상기관 가상연체율(잔액)", "가상 > 통계 > 가상연체율", []),
        ("T2", "가상조합 가상연체율(잔액)", "가상 > 통계 > 가상연체율", []),
        ("T3", "가상지점 가상예탁(잔액)", "가상 > 통계 > 가상예탁(구)", []),  # the node is not in the name
        ("T4", "가상수입물가지수(가상분류)", "가상 > 물가", []),
        ("T5", "가상환율지수", "가상 > 물가", ["가상환율"]),  # the name without its tail is a value
        ("T6", "가상소득표", "가상 > 소득", ["가상총소득(VGN)"]),
    ])
    got = _derived(cat)
    assert got[("가상연체율", "path_node")] == {"T1", "T2"}
    assert not any(variant == "path_node" and "T3" in tids for (_n, variant), tids in got.items())
    assert got[("가상수입물가", "no_tail")] == {"T4"}
    assert ("가상환율", "no_tail") not in got
    assert got[("VGN", "abbr")] == {"T6"}


def test_and_part_is_not_made_and_the_value_abbreviation_is():
    cat = _catalog([
        ("T1", "가상활동별 가상GDP 및 VGNI(명목)", "가상 > 계정", ["가상GDP", "가상총소득(VGNI)"]),
        ("T2", "가상활동별 가상투자(명목)", "가상 > 계정", []),
    ])
    got = _derived(cat)
    assert not any(variant == "and_part" for _n, variant in got)  # and_part of r2: not made (user decision pending)
    assert got[("VGNI", "abbr")] == {"T1"}  # user decision 10/19: the value's abbreviation is a derived name
    assert ("가상GDP 및 VGNI", "table_core") in got  # table_core of the shared first word 가상활동별
    vgni = [d for d in derived_names(cat) if d.name == "VGNI"]
    assert [(d.table_id, d.obj_id, d.item_id) for d in vgni] == [("T1", "A", "a1")]  # the value kept


def test_index_adds_derived_names_as_lexical_and_marks_free_keys_as_they_were():
    cat = _catalog([
        ("T1", "가상은행 가상대출금리(신규 기준)", "가상 > 통계", []),
        ("T2", "가상조합 가상대출금리(잔액 기준)", "가상 > 통계", []),
        ("T3", "가상은행 가상수신(말잔)", "가상 > 통계", []),
        ("T4", "가상자산/가상자본 지표(가상분류)", "가상 > 경영", ["가상A·가상B"]),
        ("T5", "가상조합 가상수신", "가상 > 통계", []),
    ])
    ix = build_index(cat)
    rate = ix.lookup("가상대출금리")
    assert [(c["kind"], c["type"], c["table_ids"], c["path"], c["variant"]) for c in rate] == [
        ("table", "series_name", ["T1", "T2"], "lexical", "table_core")]  # one candidate for both tables
    front = [c for c in ix.lookup("가상자산 가상자본 지표") if c["kind"] == "table"]
    assert [(c["variant"], c["path"]) for c in front] == [("table_front", "exact")]  # the mark-free key stays exact
    value = ix.lookup("가상A 가상B")
    assert [(c["kind"], c["item_id"], c["variant"]) for c in value] == [("value", "a0", "name")]
    assert {"가상대출금리", "가상자산가상자본지표", "가상a가상b"} <= ix.derived_keys
    assert [c["table_ids"] for c in ix.lookup("가상수신")] == [["T3", "T5"]]
    # a derived name never adds a candidate to a key of the catalog's own names
    assert [(c["table_ids"], c["variant"]) for c in ix.lookup("가상조합 가상수신")] == [(["T5"], "table_name")]
    assert "가상조합가상수신" not in ix.derived_keys
