"""Synonym and out-of-scope dictionaries (format, loader, switches) and the override import. Synthetic files only.

The external synonym file used here is a made-up stand-in written by the test; the real external draft is not read.
"""

from __future__ import annotations

import csv

import pytest
from catalog_synth import write_inputs, write_jsonl, write_raw

from claview_a import paths
from claview_a.catalog.import_overrides import DualTables, convert, full_table_ids
from claview_a.catalog.index import build_index
from claview_a.catalog.loader import load_catalog
from claview_a.catalog.out_of_scope import load_out_of_scope
from claview_a.catalog.roles import OVERRIDE_FIELDS, build_axis_roles, load_overrides
from claview_a.catalog.synonyms import load_synonyms


def _ext(official, words, ambiguous=False, kind="alias"):
    return {"official": official, "official_norm": official, "synonyms": words, "kind": kind, "negatives": [],
            "source": "external:test", "source_ref": "x:1", "status": "draft", "ambiguous": ambiguous, "note": ""}


@pytest.fixture
def files(tmp_path):
    approved = write_jsonl(tmp_path / "approved.jsonl", [
        {"official": "현금통화", "synonyms": ["현금"], "kind": "alias", "source": "team", "status": "approved"},
        {"official": "요구불예금", "synonyms": ["보통예금"], "kind": "alias", "source": "team", "status": "draft"},
    ])
    external = write_jsonl(tmp_path / "external.jsonl", [
        _ext("M2(말잔)", ["통화량"], ambiguous=True),
        _ext("M2(평잔)", ["통화량"], ambiguous=True),
        _ext("제조업", ["공장"], ambiguous=True),
        _ext("건설업", ["공장"], ambiguous=True),
        _ext("건설업", ["건설"]),
        _ext("서울", ["서울시"], kind="layman"),
        _ext("부산", ["가"]),
    ])
    return {"approved": approved, "external": external, "missing": tmp_path / "absent.jsonl", "tmp": tmp_path}


# ---------------------------------------------------------------- synonyms


def test_package_approved_synonyms_start_empty():
    assert paths.SYNONYMS_APPROVED.read_bytes() == b""
    syn = load_synonyms(paths.SYNONYMS_APPROVED, paths.ROOT / "runs" / "__absent__.jsonl")
    assert syn.entries == [] and syn.counts["approved_lines"] == 0 and not syn.external_present


def test_external_draft_read_when_present_and_switch_on(files):
    syn = load_synonyms(files["approved"], files["external"])
    assert syn.external_present and syn.counts["external_lines"] == 7
    ext = [e for e in syn.usable() if e.origin == "external"]
    assert {e.source for e in ext} == {"external:test"} and {e.status for e in ext} == {"draft"}
    assert [e.term for e in syn.lookup("서울 시")] == ["서울시"]
    assert syn.lookup("서울시")[0].kinds == ("layman",)


def test_external_draft_skipped_when_absent_or_switched_off(files):
    absent = load_synonyms(files["approved"], files["missing"])
    off = load_synonyms(files["approved"], files["external"], include_external=False)
    for syn in (absent, off):
        assert [e.term for e in syn.usable()] == ["현금"]
        assert syn.counts["external_lines"] == 0
    assert not absent.external_present and off.external_present


def test_only_approved_lines_of_the_approved_file_are_used(files):
    syn = load_synonyms(files["approved"], files["missing"])
    assert syn.counts["approved_lines"] == 2 and syn.counts["approved_lines_not_approved"] == 1
    assert [e.term for e in syn.usable()] == ["현금"] and syn.lookup("보통예금") == []


def test_ambiguity_is_judged_again_after_merging_parenthesis_free_names(files):
    syn = load_synonyms(files["approved"], files["external"])
    money = syn.lookup("통화량")
    assert len(money) == 1 and money[0].officials == ("M2(말잔)", "M2(평잔)") and money[0].official_key == "m2"
    assert money[0].ambiguous is False and money[0].ambiguous_source is True
    factory = syn.lookup("공장")
    assert len(factory) == 2 and all(e.ambiguous for e in factory)
    assert syn.lookup("가")[0].ambiguous  # single character
    assert syn.counts["ambiguous_resolved_by_merge"] == 1 and syn.counts["ambiguous_new"] == 1


def test_bad_synonym_line_is_rejected(tmp_path):
    bad = write_jsonl(tmp_path / "bad.jsonl", [{"official": "x", "synonyms": "not a list", "source": "s",
                                                "status": "approved"}])
    with pytest.raises(ValueError):
        load_synonyms(bad, tmp_path / "none.jsonl")


# ---------------------------------------------------------------- out of scope


def test_package_out_of_scope_starts_empty():
    assert paths.OUT_OF_SCOPE.read_bytes() == b""
    assert load_out_of_scope().all_entries == []


def test_out_of_scope_format_and_approved_only(tmp_path):
    path = write_jsonl(tmp_path / "oos.jsonl", [
        {"term": "주택 가격", "reason": "not a BOK table", "source": "team", "status": "approved"},
        {"term": "실업률", "reason": "not a BOK table", "source": "team", "status": "draft"},
    ])
    oos = load_out_of_scope(path)
    assert len(oos.all_entries) == 2 and [e.term for e in oos.entries] == ["주택 가격"]
    assert oos.lookup("주택가격")[0].reason == "not a BOK table" and oos.lookup("실업률") == []
    with pytest.raises(ValueError):
        load_out_of_scope(write_jsonl(tmp_path / "bad.jsonl", [{"term": "x", "status": "approved"}]))


# ---------------------------------------------------------------- index with dictionaries


def test_index_adds_synonyms_and_out_of_scope_as_dictionary_entries(files):
    tmp = files["tmp"]
    catalog = load_catalog(*write_raw(tmp / "raw"))
    roles = build_axis_roles(catalog, **write_inputs(tmp / "inputs"))
    oos = load_out_of_scope(write_jsonl(tmp / "oos.jsonl", [
        {"term": "주택 가격", "reason": "r", "source": "team", "status": "approved"}]))
    ix = build_index(catalog, roles, load_synonyms(files["approved"], files["external"]), oos)
    seoul = ix.lookup("서울시")
    assert [(c["name"], c["path"], c["status"], c["source"], c["table_ids"]) for c in seoul] == [
        ("서울", "dictionary", "draft", "external:test", ["T_REG"])]
    assert {c["status"] for c in ix.lookup("현금")} == {"approved"}
    assert [c["type"] for c in ix.lookup("주택가격")] == ["out_of_scope"]
    assert "서울시" not in ix.span_keys and "주택가격" not in ix.span_keys  # spans use catalog names only
    off = build_index(catalog, roles, load_synonyms(files["approved"], files["external"], include_external=False))
    assert off.lookup("서울시") == [] and off.lookup("현금")


# ---------------------------------------------------------------- overrides file


def test_package_override_file_shape():
    rows = load_overrides()  # also refuses bad machine tokens
    assert rows and {r.status for r in rows} == {"draft"}
    with paths.AXIS_ROLE_OVERRIDES.open(encoding="utf-8-sig", newline="") as stream:
        assert csv.DictReader(stream).fieldnames == list(OVERRIDE_FIELDS)
    for r in rows:
        assert r.section in "12345" and r.subject and r.recommended
        assert r.effective_status == ("approved" if r.decided else "draft")
        assert not r.truncated, r.source  # every cut table list was rebuilt by the importer
        assert (r.role or r.level_roles or r.value_role) or r.parse_note, r.source  # unread rows say why
        if r.section == "4":
            assert r.value_role or r.parse_note
        assert not (r.value_role and r.section != "4")


SHEET_FIELDS = ["구분", "표 ID", "표 이름", "축 이름", "값", "지금 판정", "근거", "recommended", "confidence", "evidence",
                "note", "decision"]


def _sheet(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        w = csv.DictWriter(stream, fieldnames=SHEET_FIELDS)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return path


def _set_column(path, n, field, value):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[n][field] = value
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        w = csv.DictWriter(stream, fieldnames=list(OVERRIDE_FIELDS))
        w.writeheader()
        w.writerows(rows)


def test_import_overrides_fills_machine_columns_and_keeps_existing_decisions(tmp_path):
    sheet = _sheet(tmp_path / "sheet.csv", [
        {"구분": "2 경계 축", "표 ID": "T_A", "축 이름": "계정항목별", "recommended": "대상 축", "confidence": "high"},
        {"구분": "4 겸용 값", "표 ID": "T_A / T_B", "값": "가계", "recommended": "지표", "confidence": "high"},
        {"구분": "3 눌린 축", "표 ID": "T_C", "축 이름": "계정항목별", "recommended": "맨 위 합계 머리 / 가운데 대상 / 아래 기준"},
        {"구분": "2 경계 축", "표 ID": "T_D", "축 이름": "계정항목별", "recommended": "눌린 축(같은 단계 섞임: a = 지표 / b = 대상)"},
    ])
    out = tmp_path / "overrides.csv"
    assert convert(sheet, out) == 4
    rows = load_overrides(out)
    assert [(r.subject, r.role, r.level_roles, r.value_role, r.status, r.decided, r.source) for r in rows] == [
        ("계정항목별", "target", (), "", "draft", False, "sheet.csv:2"),
        ("가계", "", (), "indicator", "draft", False, "sheet.csv:3"),
        ("계정항목별", "", ("total_header", "target", "basis"), "", "draft", False, "sheet.csv:4"),
        ("계정항목별", "pressed", (), "", "draft", False, "sheet.csv:5")]
    assert rows[3].parse_note and not rows[0].parse_note
    _set_column(out, 1, "decision_value_role", "target")
    assert load_overrides(out)[1].effective_status == "approved"
    convert(sheet, out)
    again = load_overrides(out)
    assert again[1].decision_value_role == "target" and again[1].effective_value_role == "target"
    assert not again[0].decided


def test_import_overrides_reads_a_free_text_decision(tmp_path):
    sheet = _sheet(tmp_path / "sheet.csv", [
        {"구분": "3 눌린 축", "표 ID": "T_C", "축 이름": "계정항목별", "recommended": "위 지표 / 아래 대상",
         "decision": "위 대상 / 아래 지표"}])
    out = tmp_path / "overrides.csv"
    convert(sheet, out)
    (row,) = load_overrides(out)
    assert (row.level_roles, row.decision_level_roles, row.effective_levels, row.effective_status) == (
        ("indicator", "target"), ("target", "indicator"), ("target", "indicator"), "approved")


def test_cut_table_list_is_rebuilt_from_the_sheet_or_the_catalog(tmp_path):
    cut = {"구분": "4 겸용 값", "값": "가계", "recommended": "지표"}
    from_sheet = dict(cut, **{"표 ID": "T_A / T_B …", "evidence": "표 3개: [지표] 설명 외 2표 (표: T_C, T_A, T_B)"})
    assert full_table_ids(from_sheet, "가계", DualTables(object())) == (
        "T_A / T_B / T_C", "표 ID: 잘린 목록을 확인표 근거 목록으로 다시 만듦(3표)")

    def dual_tables(tid):
        return (tid, f"예금 {tid}", "Deposits", [("ACC", "계정항목별", "ACCOUNT", [
            ("d0", "예금", None, None, None), ("d1", "가계", None, "d0", None), ("d2", "요구불", None, "d0", None),
            ("d3", "저축성", None, "d0", None)])])

    seed = ("T_SEED", "기관별", "By Institution", [("ORG", "기관코드별", "ORG", [("s1", "가계", None, None, None)])])
    catalog = load_catalog(*write_raw(tmp_path / "raw", [seed, dual_tables("T_V1"), dual_tables("T_V2")]))
    from_catalog = dict(cut, **{"표 ID": "T_V1 …", "evidence": "표 2개: [지표] 설명 외 1표 (표: T_V1, …)"})
    assert full_table_ids(from_catalog, "가계", DualTables(catalog)) == (
        "T_V1 / T_V2", "표 ID: 잘린 목록을 카탈로그 겸용 값으로 다시 만듦(2표)")
    wrong_count = dict(from_catalog, evidence="표 3개: [지표] 설명 (표: T_V1, …)")
    ids, note = full_table_ids(wrong_count, "가계", DualTables(catalog))
    assert ids == "T_V1 …" and "다시 만들지 못함" in note
    no_count = dict(from_catalog, evidence="(표: T_V1, …)")
    assert full_table_ids(no_count, "가계", DualTables(catalog))[0] == "T_V1 …"  # the catalog needs a stated count


def test_rebuilt_table_list_keeps_its_decision_on_regeneration(tmp_path):
    ids = ", ".join(f"T_{k}" for k in range(1, 8))
    sheet = _sheet(tmp_path / "sheet.csv", [
        {"구분": "4 겸용 값", "표 ID": "T_1 / T_2 / T_3 / T_4 / T_5 …", "값": "가계", "recommended": "지표",
         "evidence": f"표 7개: [지표] 설명 (표: {ids})"}])
    out = tmp_path / "overrides.csv"
    convert(sheet, out)
    (row,) = load_overrides(out)
    assert row.table_ids == tuple(f"T_{k}" for k in range(1, 8)) and not row.truncated
    _set_column(out, 0, "decision_value_role", "target")
    convert(sheet, out)
    assert load_overrides(out)[0].decision_value_role == "target"


def test_import_overrides_writes_value_exceptions_and_their_decision(tmp_path):
    sheet = _sheet(tmp_path / "sheet.csv", [
        {"구분": "2 경계 축", "표 ID": "T_A", "축 이름": "계정코드별",
         "recommended": "대상 축(값 = 지역 × 결제통화 결합, 둘 다 대상; 뿌리 '수입' = 지표)",
         "decision": "대상 축(뿌리 '수입' = 기준)"},
        {"구분": "2 경계 축", "표 ID": "T_B", "축 이름": "계정코드별", "recommended": "대상 축(업종; 수입이 지표로 보임)"}])
    out = tmp_path / "overrides.csv"
    convert(sheet, out)
    first, second = load_overrides(out)
    assert (first.role, first.value_exceptions, first.decision_value_exceptions) == (
        "target", (("수입", "indicator"),), (("수입", "basis"),))
    assert first.effective_value_exceptions == (("수입", "basis"),) and first.effective_status == "approved"
    assert second.role == "" and second.value_exceptions == () and "값 예외를 읽지 못함" in second.parse_note


def _decided_out(tmp_path, ids_text):
    tmp_path.mkdir(parents=True, exist_ok=True)
    sheet = _sheet(tmp_path / "sheet1.csv", [
        {"구분": "4 겸용 값", "표 ID": ids_text, "값": "가계", "recommended": "지표"}])
    out = tmp_path / "overrides.csv"
    convert(sheet, out)
    _set_column(out, 0, "decision_value_role", "target")
    return out


def _orphans(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_decision_key_is_the_whole_table_set(tmp_path):
    out = _decided_out(tmp_path, "T_A / T_B")
    sheet = _sheet(tmp_path / "sheet2.csv", [{"구분": "4 겸용 값", "표 ID": "T_B / T_A", "값": "가계", "recommended": "지표"}])
    orphan = tmp_path / "orphans.csv"
    convert(sheet, out, orphan_path=orphan)
    assert load_overrides(out)[0].decision_value_role == "target"  # same set in another order keeps the decision
    assert not orphan.exists()


def test_decision_whose_table_set_changed_is_kept_aside_and_warned(tmp_path, capsys):
    out = _decided_out(tmp_path, "T_A / T_B")
    capsys.readouterr()
    sheet = _sheet(tmp_path / "sheet2.csv", [{"구분": "4 겸용 값", "표 ID": "T_A / T_C", "값": "가계", "recommended": "지표"}])
    orphan = tmp_path / "orphans.csv"
    convert(sheet, out, orphan_path=orphan)
    assert not load_overrides(out)[0].decided  # not carried to a row with another table set
    (row,) = _orphans(orphan)
    assert (row["표 ID"], row["decision_value_role"]) == ("T_A / T_B", "target")
    assert row["reason"].startswith("표 집합이 바뀜") and "T_A / T_C" in row["reason"]
    assert "warning: decision not carried (표 집합이 바뀜" in capsys.readouterr().err
    convert(sheet, out, orphan_path=orphan)  # regenerating again neither loses nor repeats the kept decision
    assert len(_orphans(orphan)) == 1


def test_decision_with_no_row_and_replaced_decision_are_kept_aside(tmp_path, capsys):
    out = _decided_out(tmp_path, "T_A")
    sheet = _sheet(tmp_path / "sheet2.csv", [{"구분": "2 경계 축", "표 ID": "T_Z", "축 이름": "계정항목별",
                                              "recommended": "대상 축"}])
    orphan = tmp_path / "orphans.csv"
    convert(sheet, out, orphan_path=orphan)
    assert [r["reason"] for r in _orphans(orphan)] == ["새 파일에 짝이 없음"]
    out2 = _decided_out(tmp_path / "b", "T_A")
    sheet2 = _sheet(tmp_path / "sheet3.csv", [{"구분": "4 겸용 값", "표 ID": "T_A", "값": "가계", "recommended": "지표",
                                               "decision": "합계 머리"}])
    orphan2 = tmp_path / "orphans2.csv"
    convert(sheet2, out2, orphan_path=orphan2)
    assert load_overrides(out2)[0].decision_value_role == "total_header"  # the sheet's own decision wins
    (row,) = _orphans(orphan2)
    assert (row["decision_value_role"], row["reason"]) == ("target", "확인표의 decision으로 바뀜")
    assert "warning:" in capsys.readouterr().err
