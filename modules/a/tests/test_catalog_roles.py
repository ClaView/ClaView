"""Axis-role table on a synthetic catalog: four layers, compound rule, target dictionary, CSV links, overrides.

Rules: data/a/axis-roles-20261016/지표축_나누기_초안_20261016.md (user draft). No network, no question file.
"""

from __future__ import annotations

import csv

import pytest
from catalog_synth import OVERRIDES, override_row, write_inputs, write_raw

from claview_a.catalog.import_overrides import machine_columns, parse_levels, parse_role
from claview_a.catalog.loader import load_catalog
from claview_a.catalog.roles import OVERRIDE_FIELDS, build_axis_roles, is_combined, load_overrides


@pytest.fixture
def synth(tmp_path):
    catalog = load_catalog(*write_raw(tmp_path / "raw"))
    inputs = write_inputs(tmp_path / "inputs")

    def build(**kw):
        args = dict(inputs)
        args.update(kw)
        return build_axis_roles(catalog, **args)

    return {"catalog": catalog, "inputs": inputs, "build": build, "tmp": tmp_path}


def vtype(roles, table_id, obj_id, item_id):
    v = roles.value(table_id, obj_id, item_id)
    return v.type, v.layer, v.rule


# ---------------------------------------------------------------- target dictionary


def test_target_dictionary_from_layer1_target_axes(synth):
    roles = synth["build"](overrides=False)
    td = roles.target_dict
    assert set(td.keys) == {"비은행금융기관", "상호저축은행", "비금융법인", "가계", "기업", "서울", "부산", "제조업", "건설업",
                            "20대", "30대"}
    assert dict(td.dropped) == {"total": 2, "other(기타…)": 2, "loan/fund(…대출금/…자금)": 1}
    assert td.age_added == {"20대", "30대"}
    assert td.keys["제조업"] == {"업종코드별"}  # industry code prefix removed before keying


# ---------------------------------------------------------------- layers 1 and 2


def test_layer1_axis_name_roles(synth):
    roles = synth["build"](overrides=False)
    assert vtype(roles, "T_REG", "REG", "r1") == ("target", 1, "target_axis")
    assert vtype(roles, "T_REG", "REG", "r0") == ("total_header", 1, "target_axis_total")
    assert vtype(roles, "T_FUND", "BASE", "y1") == ("basis", 1, "basis_axis")
    assert vtype(roles, "T_FUND", "BSI", "s1") == ("indicator", 3, "indicator_default")  # layer-1 indicator axis
    human = roles.axis("T_FUND", "FUND")
    assert (human.layer1, human.role, human.rule) == ("human", "human", "axis_name_pending")
    assert vtype(roles, "T_FUND", "FUND", "f1") == ("unknown", 1, "axis_role_pending")
    assert vtype(roles, "T_REG", "ITEM", "T_REG_ITEM") == ("series_name", 0, "item_row")


def test_layer2_generic_axis_by_target_ratio(synth):
    roles = synth["build"](overrides=False)
    gen = roles.axis("T_GEN", "ACC")  # 제조업·건설업 in the dictionary, 현금통화 not: 2/3
    assert (gen.layer1, gen.role, gen.layer, gen.rule, gen.hit, gen.n_nontotal) == (
        "generic", "target", 2, "target_ratio", 2, 3)
    assert vtype(roles, "T_GEN", "ACC", "g3") == ("target", 2, "target_axis")
    age = roles.axis("T_AGE", "AGE")
    assert (age.role, age.ratio) == ("target", 1.0)
    reg = roles.axis("T_REG", "ACC")
    assert (reg.computed_role, reg.csv_role, reg.role, reg.rule) == ("indicator", "indicator", "indicator", "draft_csv")


def test_layer2_user_csv_takes_precedence_and_can_be_switched_off(synth):
    with_csv = synth["build"](overrides=False).axis("T_CSV", "ACC")
    assert (with_csv.computed_role, with_csv.csv_role, with_csv.role, with_csv.rule) == (
        "target", "indicator", "indicator", "draft_csv")
    assert with_csv.csv_source == "generic.csv:3"
    computed = synth["build"](overrides=False, use_draft_csv=False).axis("T_CSV", "ACC")
    assert (computed.role, computed.rule) == ("target", "target_ratio")


# ---------------------------------------------------------------- layer 3


def test_layer3_value_corrections(synth):
    roles = synth["build"](overrides=False)
    expected = {
        "d0": ("total_header", "total_header"),  # has children, starts with 총
        "d1": ("compound", "compound_form"),  # 정기예금-2%미만: "A-B"
        "d2": ("compound", "compound_form"),  # 비금융법인(사용)
        "d8": ("compound", "compound_form"),  # 저축(원천)
        "d7": ("compound", "compound_form"),  # 가계-주택
        "d3": ("indicator", "indicator_default"),  # 가계신용: contains a target word, not compound
        "d4": ("basis", "basis_words"),  # 신규취급액 기준
        "d5": ("basis", "basis_words"),  # 원화
        "d6": ("target", "target_exact"),  # 가계
        "b1": ("target", "target_sibling"),  # 국내은행: two of three siblings are targets
        "b2": ("target", "target_exact"),
        "b0": ("indicator", "indicator_default"),
    }
    got = {k: (roles.value("T_L3", "ACC", k).type, roles.value("T_L3", "ACC", k).rule) for k in expected}
    assert got == expected
    assert {k for k in expected if roles.value("T_L3", "ACC", k).layer == 3} == set(expected)
    assert roles.value("T_L3", "ACC", "d6").dual and not roles.value("T_L3", "ACC", "b2").dual


@pytest.mark.parametrize("name, combined", [
    ("정기예금-2%미만", True), ("일반정부-피용자보수", True), ("비금융법인(사용)", True), ("가계(원천)", True),
    ("국내은행 - 대기업", True), ("가계신용", False), ("가계대출", False), ("Non-MMF부문", False),
    ("C101-4 육류 가공", False), ("비금융법인(사용자)", False), ("원천징수", False),
])
def test_compound_only_by_form(name, combined):
    assert is_combined(name) is combined


def test_sibling_majority_needs_two_siblings(tmp_path):
    tables = [
        ("T_T", "대상 표", "T", [("R", "지역코드별", "REGION", [("1", "서울", None, None, None)])]),
        ("T_M", "지표 표", "M", [("A", "계정항목별", "ACCOUNT", [
            ("p", "묶음", None, None, None), ("c1", "새 지역", None, "p", None),
            ("q", "다른 묶음", None, None, None), ("c2", "새 지역", None, "q", None), ("c3", "서울", None, "q", None),
            ("c4", "부산 외", None, "q", None)])]),
    ]
    inputs = write_inputs(tmp_path / "in", overrides=[])
    roles = build_axis_roles(load_catalog(*write_raw(tmp_path / "raw", tables)), use_draft_csv=False, **inputs)
    assert roles.value("T_M", "A", "c1").rule == "indicator_default"  # alone under its parent
    assert roles.value("T_M", "A", "c4").rule == "indicator_default"  # 1 of 3 siblings is a target
    assert roles.value("T_M", "A", "c3").rule == "target_exact"


# ---------------------------------------------------------------- layer 4


def test_layer4_detected_pressed_axis(synth):
    roles = synth["build"](overrides=False)
    pr = next(p for p in roles.pressed if p.table_id == "T_PR")
    assert (pr.detected, pr.in_draft_csv, pr.applied) == (True, True, True)
    assert set(pr.upper_ids) == {"p1", "p2"} and set(pr.lower_ids) == {"p11", "p12", "p13", "p21", "p22", "p23"}
    assert (pr.upper_role, pr.lower_role, pr.level_source) == ("indicator", "target", "computed")
    assert vtype(roles, "T_PR", "ACC", "p13") == ("target", 4, "pressed_lower")  # 기타 under the target level
    assert vtype(roles, "T_PR", "ACC", "p1") == ("indicator", 3, "indicator_default")  # computed default kept
    assert roles.value("T_PR", "ACC", "p1").pressed_level == "upper"
    assert vtype(roles, "T_PR", "ACC", "p3") == ("indicator", 3, "indicator_default")  # outside the bundle


def test_layer4_pressed_axis_listed_only_in_user_csv(synth):
    roles = synth["build"](overrides=False)
    pr = next(p for p in roles.pressed if p.table_id == "T_PR2")
    assert (pr.detected, pr.in_draft_csv, pr.applied, pr.csv_source) == (False, True, True, "collapsed.csv:3")
    assert set(pr.upper_ids) == {"q1", "q2"} and set(pr.lower_ids) == {"q11", "q21"}
    assert (pr.upper_role, pr.lower_role) == ("basis", "indicator")
    assert roles.value("T_PR2", "ACC", "q1").type == "basis"
    assert roles.value("T_PR2", "ACC", "q11").pressed_level == "lower"
    no_csv = synth["build"](overrides=False, use_draft_csv=False)
    assert [p.table_id for p in no_csv.pressed] == ["T_PR"]


# ---------------------------------------------------------------- CSV links


def test_csv_rows_linking_to_no_axis_or_several_axes_are_reported_not_judged(synth):
    roles = synth["build"]()
    problems = {(p["kind"], p["table_id"], p["found"]) for p in roles.link_problems}
    assert ("generic_csv", "T_NONE", 0) in problems
    assert ("generic_csv", "T_DUP", 2) in problems
    assert ("override", "T_NONE", 0) in problems
    for obj in ("ACC1", "ACC2"):
        ax = roles.axis("T_DUP", obj)
        assert ax.csv_role is None and ax.rule == "target_ratio"
    linked = [e for e in roles.links["generic_csv"] if e["table_id"] == "T_REG"]
    assert linked[0]["obj_ids"] == ["ACC"] and linked[0]["status"] == "draft"


# ---------------------------------------------------------------- overrides


def _report(roles, table_id, subject):
    return [r for r in roles.override_report if table_id in r["table_ids"] and r["subject"] == subject]


def test_overrides_off_leaves_rule_results(synth):
    roles = synth["build"](overrides=False)
    assert roles.override_report == []
    assert roles.axis("T_CSV", "ACC").role == "indicator"
    assert roles.value("T_L3", "ACC", "d6").type == "target"
    assert all(v.override is None and v.status == "draft" for v in roles.values.values())


def test_overrides_on_apply_draft_recommendations(synth):
    roles = synth["build"]()
    fund = roles.axis("T_FUND", "FUND")  # 1: human axis -> indicator
    assert (fund.role, fund.rule, fund.status) == ("indicator", "override", "draft")
    assert vtype(roles, "T_FUND", "FUND", "f1") == ("indicator", 3, "indicator_default")
    csv_axis = roles.axis("T_CSV", "ACC")  # 2: per-axis CSV said indicator, recommendation says target axis
    assert (csv_axis.role, csv_axis.override["status"]) == ("target", "draft")
    assert roles.value("T_CSV", "ACC", "c2").type == "target"
    gen = _report(roles, "T_GEN", "계정항목별")[0]  # 2: pressed (mixed) axis, but the axis has no pressed structure
    assert gen["outcome"] == "not_applied" and "no pressed structure" in gen["reason"]
    assert roles.axis("T_GEN", "ACC").role == "target"  # nothing of the row is applied
    pr = next(p for p in roles.pressed if p.table_id == "T_PR")  # 3: level roles from the recommendation
    assert (pr.upper_role, pr.lower_role, pr.level_source) == ("target", "indicator", "override")
    assert _report(roles, "T_PR", "계정항목별")[0]["outcome"] == "applied"
    assert vtype(roles, "T_PR", "ACC", "p1") == ("target", 4, "pressed_upper")
    assert vtype(roles, "T_PR", "ACC", "p11") == ("indicator", 4, "pressed_lower")
    three = _report(roles, "T_PR2", "계정항목별")[0]  # three level roles read, but the structure has two levels
    assert three["outcome"] == "not_applied" and "has 2 levels" in three["reason"]
    d6 = roles.value("T_L3", "ACC", "d6")  # 4: dual-role value
    assert (d6.type, d6.rule, d6.status) == ("indicator", "override_value", "draft")
    four = _report(roles, "T_L3", "가계")[0]
    assert four["outcome"] == "applied" and four["listed_tables_not_matched"] == ["T_ZZ"]
    assert roles.value("T_PR", "ACC", "p21").rule == "pressed_lower"  # not dual: value override does not touch it
    assert _report(roles, "T_REG", "계정항목별")[0]["outcome"] == "unchanged"  # 5: component items stay indicator


def test_decision_is_approved_and_wins_over_recommendation(tmp_path):
    catalog = load_catalog(*write_raw(tmp_path / "raw"))
    inputs = write_inputs(tmp_path / "inputs", decisions={("T_CSV", "계정항목별"): "지표 축", ("T_L3 / T_ZZ", "가계"): "대상"})
    roles = build_axis_roles(catalog, **inputs)
    ax = roles.axis("T_CSV", "ACC")
    assert (ax.role, ax.status, ax.override["status"], ax.override["value"]) == ("indicator", "approved", "approved",
                                                                                 "role=indicator")
    d6 = roles.value("T_L3", "ACC", "d6")
    assert (d6.type, d6.status) == ("target", "approved")
    assert roles.value("T_FUND", "FUND", "f1").status == "draft"


def test_override_rows_are_all_reported(synth):
    roles = synth["build"]()
    assert sorted(r["source"] for r in roles.override_report) == sorted(
        f"synthetic.csv:{n}" for n in range(2, len(OVERRIDES) + 2))


@pytest.mark.parametrize("text, role", [
    ("지표", "indicator"), ("대상 축", "target"), ("대상 축(업종; 총부가가치는 지표)", "target"), ("기준", "basis"),
    ("합계 머리", "total_header"), ("기준+지표 결합(단기·장기 = 기준)", "compound"), ("눌린 축(위 지표: a / 아래 대상: b)", None),
    ("지표(권장안 그대로)", "indicator"), ("", None),
])
def test_parse_role(text, role):
    assert parse_role(text) == role


@pytest.mark.parametrize("text, levels", [
    ("위 지표 / 아래 대상", ("indicator", "target")),
    ("위 대상(국가: 대만·러시아) / 아래 대상(결제통화: 달러·엔화)", ("target", "target")),
    ("위 기준+지표 결합(단기채권·장기채권) / 아래 지표(국공채·금융채)", ("compound", "indicator")),
    ("맨 위 합계 머리 / 가운데 대상 / 아래 기준", ("total_header", "target", "basis")),
    ("위 대상(A·B) / 가운데 기준(단기·장기) / 아래 지표(예금)", ("target", "basis", "indicator")),
    ("맨 위 합계 머리 / 위 지표 / 가운데 대상 / 아래 기준", ("total_header", "indicator", "target", "basis")),
    ("위 지표: 가계신용·가계대출 / 아래 대상: 예금은행 등 기관", ("indicator", "target")),
    ("맨 위 합계 머리 / 가운데 지표 / 아래 대상(업종·민간)과 지표가 섞임", None),  # one level mixes roles
    ("같은 단계 섞임: 주택관련대출 = 지표 / 상호저축은행 = 대상", None),  # no level words
    ("아래 지표 / 위 대상", None),  # not top to bottom
    ("가운데 대상 / 아래 기준", None),  # does not start at the top
    ("위 지표", None),
])
def test_parse_levels(text, levels):
    assert parse_levels(text) == levels


@pytest.mark.parametrize("section, text, want", [
    ("1 애매한 축 이름", "지표", ("indicator", "", "", False)),
    ("2 경계 축", "대상 축(값 = 지역 × 결제통화 결합, 둘 다 대상; 뿌리 '수입' = 지표)", ("target", "", "", False)),
    ("2 경계 축", "눌린 축(위 지표: a·b / 아래 대상: c 등 기관)", ("pressed", "indicator|target", "", False)),
    ("2 경계 축", "눌린 축(같은 단계 섞임: a = 지표 / b = 대상)", ("pressed", "", "", True)),
    ("3 눌린 축", "맨 위 합계 머리(x) / 가운데 대상(y) / 아래 기준(z)", ("", "total_header|target|basis", "", False)),
    ("3 눌린 축", "맨 위 합계 머리 / 가운데 지표 / 아래 대상(업종)과 지표가 섞임", ("", "", "", True)),
    ("4 기관/상품 겸용 대상 값", "합계 머리", ("", "", "total_header", False)),
    ("4 기관/상품 겸용 대상 값", "지표(T_A, T_B) / 대상(T_C)", ("", "", "", True)),  # per-table recommendation
    ("5 구성 항목", "지표", ("indicator", "", "", False)),
    ("2 경계 축", "", ("", "", "", True)),
])
def test_machine_columns_from_free_text(section, text, want):
    got = machine_columns(section, text)
    assert (got["role"], got["level_roles"], got["value_role"], bool(got["parse_note"])) == want


# ---------------------------------------------------------------- machine-column overrides on small catalogs

SEED = ("T_SEED", "기관별 잔액", "By Institution", [("ORG", "기관코드별", "INSTITUTION", [
    ("s1", "가계", "Households", None, None), ("s2", "기업", "Corporations", None, None),
    ("s3", "정부", "Government", None, None)])])
MIX = ("T_MIX", "대출 잔액(기관별)", "Loans", [("ACC", "계정항목별", "ACCOUNT", [
    ("m1", "대출A", None, None, None), ("m11", "가계", None, "m1", None), ("m12", "기업", None, "m1", None),
    ("m2", "대출B", None, None, None), ("m21", "가계", None, "m2", None), ("m22", "기업", None, "m2", None)])])
THREE = ("T_3L", "발행 잔액(분류별)", "Issues", [("ACC", "계정항목별", "ACCOUNT", [
    ("h1", "통화별", None, None, None), ("h2", "만기별", None, None, None),
    ("r1", "거주자", None, "h1", None), ("r2", "비거주자", None, "h1", None),
    ("r3", "거주자", None, "h2", None), ("r4", "비거주자", None, "h2", None),
    ("c1", "원화", None, "r1", None), ("c2", "외화", None, "r1", None), ("c3", "원화", None, "r2", None),
    ("c4", "외화", None, "r2", None), ("c5", "단기", None, "r3", None), ("c6", "장기", None, "r3", None),
    ("c7", "단기", None, "r4", None), ("c8", "장기", None, "r4", None)])])


def _dual_table(tid):
    return (tid, f"예금 잔액 {tid}", "Deposits", [("ACC", "계정항목별", "ACCOUNT", [
        ("d0", "예금", None, None, None), ("d1", "가계", None, "d0", None), ("d2", "요구불", None, "d0", None),
        ("d3", "저축성", None, "d0", None)])])


def _small(tmp_path, tables, overrides, **kw):
    catalog = load_catalog(*write_raw(tmp_path / "raw", [SEED, *tables]))
    inputs = write_inputs(tmp_path / "in", overrides=overrides)
    return catalog, (lambda **more: build_axis_roles(catalog, **{**inputs, **kw, **more}))


def test_pressed_role_makes_a_mixed_axis_an_indicator_axis_with_level_roles(tmp_path):
    rec = "눌린 축(위 지표: 대출A·대출B / 아래 대상: 가계·기업)"
    _cat, build = _small(tmp_path, [MIX], [("2 경계 축", "T_MIX", "계정항목별", rec, "")])
    off = build(overrides=False)
    assert off.axis("T_MIX", "ACC").role == "target"  # 4 of 6 values in the target dictionary
    roles = build()
    ax = roles.axis("T_MIX", "ACC")
    assert (ax.role, ax.rule, ax.status) == ("indicator", "override_pressed", "draft")
    assert vtype(roles, "T_MIX", "ACC", "m1") == ("indicator", 4, "pressed_upper")
    assert vtype(roles, "T_MIX", "ACC", "m21") == ("target", 4, "pressed_lower")
    (rep,) = roles.override_report
    assert rep["outcome"] == "applied" and rep["reason"].startswith("axis target -> indicator (pressed)")


def test_pressed_role_whose_levels_do_not_fit_is_not_applied_at_all(tmp_path):
    rec = "눌린 축(맨 위 합계 머리 / 가운데 지표 / 아래 대상)"
    _cat, build = _small(tmp_path, [MIX], [("2 경계 축", "T_MIX", "계정항목별", rec, "")])
    roles = build()
    assert roles.axis("T_MIX", "ACC").role == "target" and roles.value("T_MIX", "ACC", "m1").type == "target"
    (rep,) = roles.override_report
    assert rep["outcome"] == "not_applied" and "has 2 levels" in rep["reason"]
    assert "axis role left target" in rep["reason"]


def test_three_level_override_places_each_level(tmp_path):
    rec = "맨 위 합계 머리(통화별·만기별) / 가운데 대상(거주자·비거주자) / 아래 기준(원화·외화, 단기·장기)"
    _cat, build = _small(tmp_path, [THREE], [("3 눌린 축", "T_3L", "계정항목별", rec, "")])
    roles = build()
    pr = next(p for p in roles.pressed if p.table_id == "T_3L")
    assert pr.level_roles == ("total_header", "target", "basis") and pr.level_source == "override"
    assert [set(g) for g in pr.level_ids] == [{"h1", "h2"}, {"r1", "r2", "r3", "r4"},
                                              {f"c{k}" for k in range(1, 9)}]
    assert vtype(roles, "T_3L", "ACC", "h2") == ("total_header", 4, "pressed_upper")
    assert vtype(roles, "T_3L", "ACC", "r3") == ("target", 4, "pressed_middle")
    c5 = roles.value("T_3L", "ACC", "c5")
    assert (c5.type, c5.pressed_level) == ("basis", "lower")
    (rep,) = roles.override_report
    assert rep["outcome"] == "applied"
    two_path = write_inputs(tmp_path / "two", overrides=[
        ("3 눌린 축", "T_3L", "계정항목별", "위 지표 / 아래 대상", "")])["overrides_path"]
    two = build(overrides_path=two_path)
    pr2 = next(p for p in two.pressed if p.table_id == "T_3L")  # two levels: the upper/lower reading
    assert (pr2.level_ids, pr2.upper_role, pr2.lower_role) == ((), "indicator", "target")
    assert two.value("T_3L", "ACC", "r1").pressed_level == "upper"


def test_value_override_touches_only_the_listed_tables(tmp_path):
    tables = [_dual_table("T_V1"), _dual_table("T_V2")]
    _cat, build = _small(tmp_path, tables, [("4 기관/상품 겸용 대상 값", "T_V1", "가계", "지표", "")])
    off = build(overrides=False)
    assert off.value("T_V1", "ACC", "d1").dual and off.value("T_V2", "ACC", "d1").dual
    roles = build()
    assert roles.value("T_V1", "ACC", "d1").type == "indicator"
    v2 = roles.value("T_V2", "ACC", "d1")  # a dual value of the same name in a table not listed stays as it was
    assert (v2.type, v2.rule, v2.override) == ("target", "target_exact", None)
    (rep,) = roles.override_report
    assert (rep["outcome"], rep["values"], rep["changed"]) == ("applied", 1, 1)


def test_value_override_with_a_cut_table_list_is_not_applied(tmp_path):
    tables = [_dual_table("T_V1"), _dual_table("T_V2")]
    _cat, build = _small(tmp_path, tables, [("4 기관/상품 겸용 대상 값", "T_V1 …", "가계", "지표", "")])
    roles = build()
    assert roles.value("T_V1", "ACC", "d1").type == "target" and roles.value("T_V2", "ACC", "d1").type == "target"
    (rep,) = roles.override_report
    assert rep["outcome"] == "not_applied" and "cut" in rep["reason"]


@pytest.mark.parametrize("field, value", [("role", "colour"), ("level_roles", "indicator|colour"),
                                          ("level_roles", "indicator"), ("value_role", "pressed"),
                                          ("decision_role", "maybe")])
def test_bad_machine_tokens_are_refused(tmp_path, field, value):
    row = override_row(2, "2 경계 축", "T_CSV", "계정항목별", "대상 축")
    row[field] = value
    path = tmp_path / "ov.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(OVERRIDE_FIELDS))
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(ValueError):
        load_overrides(path)


# ---------------------------------------------------------------- value exceptions inside the parentheses (review 2)


@pytest.mark.parametrize("text, role, exceptions, note", [
    ("대상 축(값 = 지역 × 결제통화 결합, 둘 다 대상; 뿌리 '수입' = 지표)", "target", "수입=indicator", ""),
    ("대상 축(업종; 끝의 총부가가치·순생산물세·국내총생산은 지표/합계 머리)", "target",
     "총부가가치=indicator;순생산물세=indicator;국내총생산=indicator", "첫 역할"),
    ("대상 축(국내은행·비은행금융기관과 그 아래 기관·차주; '국내은행-대기업'은 대상+대상 결합)", "target",
     "국내은행-대기업=compound", ""),
    ("지표 축(값 = 부문-지출항목 결합, 3층 결합으로 나눠 읽음)", "indicator", "", ""),  # about all values
    ("지표 축(정부 기능 분류 = 구성 항목)", "indicator", "", ""),  # no role word: a description
    ("대상 축(업종; 총부가가치가 지표처럼 쓰임)", "", "", "값 예외를 읽지 못함"),  # role-like, not "값 = 역할"
    ("대상 축(뿌리 '수입' = 지표 또는 대상)", "", "", "값 예외를 읽지 못함"),  # the role cannot be read
])
def test_machine_columns_read_value_exceptions(text, role, exceptions, note):
    got = machine_columns("2 경계 축", text)
    assert (got["role"], got["value_exceptions"]) == (role, exceptions)
    assert (note in got["parse_note"]) if note else got["parse_note"] == ""


EXP = ("T_EXP", "지역별 수입(결제통화별)", "Imports", [("REG", "지역코드별", "REGION", [
    ("x0", "수입", None, None, None), ("x1", "서울", None, "x0", None), ("x2", "부산", None, "x0", None)])])


def test_value_exception_types_the_named_value_after_the_axis_role(tmp_path):
    _cat, build = _small(tmp_path, [EXP], [("2 경계 축", "T_EXP", "지역코드별", "대상 축(뿌리 '수입' = 지표)", "")])
    off = build(overrides=False)
    assert off.value("T_EXP", "REG", "x0").type == "target"  # the whole axis is a target axis by its name
    roles = build()
    assert roles.axis("T_EXP", "REG").role == "target"
    x0 = roles.value("T_EXP", "REG", "x0")
    assert (x0.type, x0.rule, x0.status, x0.override["value"]) == (
        "indicator", "override_value_exception", "draft", "role=target; value_exceptions=수입=indicator")
    assert vtype(roles, "T_EXP", "REG", "x1") == ("target", 1, "target_axis")
    (rep,) = roles.override_report
    assert (rep["outcome"], rep["exception_values"], rep["exception_changed"]) == ("applied", 1, 1)


def test_value_exception_missing_from_the_axis_applies_nothing(tmp_path):
    rec = "지표 축(뿌리 '수출' = 대상)"  # the axis has no value 수출
    _cat, build = _small(tmp_path, [EXP], [("2 경계 축", "T_EXP", "지역코드별", rec, "")])
    roles = build()
    assert roles.axis("T_EXP", "REG").role == "target" and roles.value("T_EXP", "REG", "x0").type == "target"
    (rep,) = roles.override_report
    assert rep["outcome"] == "not_applied" and "value exception not in the axis: 수출" in rep["reason"]


def test_unreadable_value_exception_leaves_the_whole_row_unapplied(tmp_path):
    catalog = load_catalog(*write_raw(tmp_path / "raw"))
    rec = "대상 축(제조업 등; '요구불예금'이 지표로 쓰임)"  # names a role, but not as "값 = 역할"
    inputs = write_inputs(tmp_path / "in", overrides=[("2 경계 축", "T_CSV", "계정항목별", rec, "")])
    (row,) = load_overrides(inputs["overrides_path"])
    assert row.role == "" and row.parse_note.startswith("값 예외를 읽지 못함")
    roles = build_axis_roles(catalog, **inputs)
    assert roles.axis("T_CSV", "ACC").role == "indicator"  # the per-axis CSV judgment stays; target is not applied
    (rep,) = roles.override_report
    assert rep["outcome"] == "not_applied" and "값 예외를 읽지 못함" in rep["reason"]


def test_decision_value_exceptions_replace_the_recommendation(tmp_path):
    catalog = load_catalog(*write_raw(tmp_path / "raw", [SEED, EXP]))
    inputs = write_inputs(tmp_path / "in", overrides=[("2 경계 축", "T_EXP", "지역코드별", "대상 축(뿌리 '수입' = 지표)", "")],
                          decisions={("T_EXP", "지역코드별"): "대상 축(뿌리 '수입' = 기준)"})
    (row,) = load_overrides(inputs["overrides_path"])
    assert row.decision_value_exceptions == (("수입", "basis"),) and row.effective_status == "approved"
    roles = build_axis_roles(catalog, **inputs)
    x0 = roles.value("T_EXP", "REG", "x0")
    assert (x0.type, x0.status) == ("basis", "approved")


@pytest.mark.parametrize("field, value, role", [
    ("value_exceptions", "수입", "target"), ("value_exceptions", "수입=colour", "target"),
    ("value_exceptions", "=indicator", "target"), ("value_exceptions", "수입=indicator", "pressed"),
    ("decision_value_exceptions", "수입=indicator", ""),
])
def test_bad_value_exception_tokens_are_refused(tmp_path, field, value, role):
    row = override_row(2, "2 경계 축", "T_CSV", "계정항목별", "대상 축")
    row[field], row["role"] = value, role
    if role == "pressed":
        row["level_roles"] = "indicator|target"
    path = tmp_path / "ov.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(OVERRIDE_FIELDS))
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(ValueError):
        load_overrides(path)


# ---------------------------------------------------------------- three levels under a root total (review 2)

# 채무 (root) -> 정부·은행 (sectors) and 단기·장기 (maturity totals) -> 단기·장기 per sector -> 증권·대출 (products)
_MATURITIES = ("m01", "m02", "m11", "m12", "m21", "m22")
ROOTED = ("T_ROOT", "대외 채무(부문별, 만기별)", "Debt", [("ACC", "계정항목별", "ACCOUNT", [
    ("t0", "채무", None, None, None),
    ("s1", "정부", None, "t0", None), ("s2", "은행", None, "t0", None),
    ("m01", "단기", None, "t0", None), ("m02", "장기", None, "t0", None),
    ("m11", "단기", None, "s1", None), ("m12", "장기", None, "s1", None),
    ("m21", "단기", None, "s2", None), ("m22", "장기", None, "s2", None),
    *[(f"{m}{k}", name, None, m, None) for m in _MATURITIES for k, name in (("a", "증권"), ("b", "대출"))]])])


def test_root_above_the_sectors_gets_no_level(tmp_path):
    rec = "위 대상(정부·은행) / 가운데 기준(단기·장기) / 아래 지표(증권·대출)"
    _cat, build = _small(tmp_path, [ROOTED], [("3 눌린 축", "T_ROOT", "계정항목별", rec, "")])
    off = build(overrides=False)
    assert off.axis("T_ROOT", "ACC").role == "indicator"
    root_before = off.value("T_ROOT", "ACC", "t0").type
    roles = build()
    pr = next(p for p in roles.pressed if p.table_id == "T_ROOT")
    assert [set(g) for g in pr.level_ids] == [
        {"s1", "s2"}, set(_MATURITIES), {f"{m}{k}" for m in _MATURITIES for k in "ab"}]
    assert pr.outside_ids == ("t0",)
    t0 = roles.value("T_ROOT", "ACC", "t0")
    assert (t0.type, t0.pressed_level, t0.layer) == (root_before, None, 3) and root_before == "indicator"
    assert vtype(roles, "T_ROOT", "ACC", "s1") == ("target", 4, "pressed_upper")
    assert roles.value("T_ROOT", "ACC", "m01").pressed_level == "middle"
    assert roles.value("T_ROOT", "ACC", "m11a").pressed_level == "lower"
    (rep,) = roles.override_report
    assert rep["outcome"] == "applied" and "1 root values above the top level keep their type" in rep["reason"]


def test_root_gets_a_level_only_when_the_override_names_one(tmp_path):
    rec = "맨 위 합계 머리(채무) / 위 대상(정부·은행) / 가운데 기준(단기·장기) / 아래 지표(증권·대출)"
    _cat, build = _small(tmp_path, [ROOTED], [("3 눌린 축", "T_ROOT", "계정항목별", rec, "")])
    roles = build()
    pr = next(p for p in roles.pressed if p.table_id == "T_ROOT")
    assert [set(g) for g in pr.level_ids][:2] == [{"t0"}, {"s1", "s2"}] and pr.outside_ids == ()
    assert vtype(roles, "T_ROOT", "ACC", "t0") == ("total_header", 4, "pressed_upper")
    assert vtype(roles, "T_ROOT", "ACC", "s2") == ("target", 4, "pressed_middle1")
    two = build(overrides_path=write_inputs(tmp_path / "two", overrides=[
        ("3 눌린 축", "T_ROOT", "계정항목별", "위 대상 / 아래 지표", "")])["overrides_path"])
    pr2 = next(p for p in two.pressed if p.table_id == "T_ROOT")  # two levels: the upper/lower reading, unchanged
    assert pr2.level_ids == () and pr2.outside_ids == () and "t0" in pr2.upper_ids
