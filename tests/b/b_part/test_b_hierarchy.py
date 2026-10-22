"""Hierarchy handling in B: `expand` modes, post-expansion cap, parent/child
relations and minimal label disambiguation.

Synthetic tables cover each rule on its own; the golden-derived test compresses
every golden case's explicit code lists into `expand` rows wherever a group of
selections is exactly the children (or leaves) of one value, and checks that B
rebuilds the identical selection set.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from claview.b.contracts import Config, StageFailure, Trace
from claview.b.evaluation import scripted_answers
from claview.b.graphs import build_b_graph
from claview.b.models import ScriptedModel
from claview.b.stages import (
    B_PROMPT,
    B_PROMPT_PLAIN,
    EXPAND_MODES,
    Services,
    apply_expand_guard,
    asks_for_breakdown,
    asks_to_split_by,
    build_b_schema,
    canonicalize_selections,
    correct_to_named_value,
    display_names,
    expand_selections,
    find_relations,
    lower_expand_on_leaves,
    normalize_model_periods,
    promote_children_to_leaves,
    repair_unused_split,
    select_named_children,
    selection_key,
    split_axes,
    with_default_expand,
)

PERIOD = {"frequency": "Y", "start": "2020", "end": "2024"}
# breakdown wording without a "~별" token: expand is offered, children is not promoted
HAS_CHILDREN_QUERY = "2020년부터 2024년까지 하위 항목 추이"


def _value(name, parent=None, path=None, leaf=True):
    return {
        "name": name,
        "unit": "%",
        "parent_code": parent,
        "path": path,
        "is_leaf": leaf,
    }


def make_tree_table() -> dict:
    """objL1: T(total) > A > (A1, A2) ; T > B (leaf).  objL2 flat: X, Y."""
    t = {"code": "T", "name": "총계"}
    a = {"code": "A", "name": "광공업"}
    values = {
        "T": _value("총계", None, [t], leaf=False),
        "A": _value("광공업", "T", [t, a], leaf=False),
        "A1": _value("제조업", "A", [t, a, {"code": "A1", "name": "제조업"}]),
        "A2": _value("광업", "A", [t, a, {"code": "A2", "name": "광업"}]),
        "B": _value("서비스업", "T", [t, {"code": "B", "name": "서비스업"}]),
    }
    flat = {c: _value(c, None, [{"code": c, "name": c}]) for c in ("X", "Y")}
    return {
        "org_id": "301",
        "table_id": "DT_TREE",
        "table_name": "트리 표",
        "items": {"I1": {"name": "지표", "unit": None, "parent_code": None}},
        "dimensions": {
            "objL1": {"name": "산업", "values": values},
            "objL2": {"name": "구분", "values": {"X": flat["X"], "Y": flat["Y"]}},
        },
        "periods": {"Y": {"start": "2010", "end": "2025"}},
        "notes": [],
        "units": ["%"],
        "snapshot_id": "test",
        "warnings": [],
    }


def row(code, expand=None, other="X", **modes):
    return {
        "item_id": "I1",
        "dimensions": {"objL1": code, "objL2": other},
        "period": PERIOD,
        "expand": {"objL1": expand or "none", "objL2": "none", **modes},
    }


def codes(expanded):
    return [r["dimensions"]["objL1"] for r in expanded["selections"]]


# ---- schema -----------------------------------------------------------------


def test_model_schema_has_expand_modes_per_axis_but_plain_schema_does_not():
    table = make_tree_table()
    full = build_b_schema(table, Config())["properties"]["selections"]["items"]["properties"]
    assert set(full["expand"]["properties"]) == {"objL1", "objL2"}
    assert (
        full["expand"]["properties"]["objL1"]["enum"]
        == list(EXPAND_MODES)
        == ["none", "children", "leaves"]
    )
    plain = build_b_schema(table, Config(), with_expand=False)["properties"]["selections"]["items"][
        "properties"
    ]
    assert "expand" not in plain


def test_row_without_expand_is_rejected():
    table = make_tree_table()
    from claview.b.models import check_output

    raw = {"selections": [{k: v for k, v in row("A").items() if k != "expand"}]}
    with pytest.raises(StageFailure) as exc:
        check_output(raw, build_b_schema(table, Config()))
    assert exc.value.code == "invalid_model_output"


# ---- expansion --------------------------------------------------------------


def test_children_mode_returns_direct_children_only_in_metadata_order():
    table = make_tree_table()
    out = expand_selections({"selections": [row("T", "children")]}, table)
    assert codes(out) == ["A", "B"]  # not A1/A2, not T itself
    assert all("expand" not in r for r in out["selections"])


def test_leaves_mode_returns_all_descendant_leaves():
    table = make_tree_table()
    assert codes(expand_selections({"selections": [row("T", "leaves")]}, table)) == [
        "A1",
        "A2",
        "B",
    ]
    assert codes(expand_selections({"selections": [row("A", "leaves")]}, table)) == [
        "A1",
        "A2",
    ]


def test_self_plus_children_is_two_selections():
    table = make_tree_table()
    out = expand_selections({"selections": [row("A"), row("A", "children")]}, table)
    assert codes(out) == ["A", "A1", "A2"]


def test_expanding_a_value_without_children_is_rejected():
    table = make_tree_table()
    for mode in ("children", "leaves"):
        with pytest.raises(StageFailure) as exc:
            expand_selections({"selections": [row("B", mode)]}, table)
        assert exc.value.code == "expand_target_has_no_children"


def test_expanding_two_axes_in_one_selection_is_rejected():
    table = make_tree_table()
    with pytest.raises(StageFailure) as exc:
        expand_selections({"selections": [row("T", "children", objL2="children")]}, table)
    assert exc.value.code == "expand_multiple_axes"


def test_expanded_rows_pass_canonicalize_and_keep_other_axes():
    table = make_tree_table()
    expanded = expand_selections({"selections": [row("A", "children", other="Y")]}, table)
    rows = canonicalize_selections(expanded, table, Config(), query="2020년부터 2024년까지")
    assert [r["dimensions"]["objL1"]["code"] for r in rows] == ["A1", "A2"]
    assert {r["dimensions"]["objL2"]["code"] for r in rows} == {"Y"}


# ---- relations (G) ----------------------------------------------------------


def _final(table, selections):
    rows = canonicalize_selections(
        expand_selections({"selections": selections}, table),
        table,
        Config(),
        query="2020년부터 2024년까지",
    )
    for i, r in enumerate(rows):
        r["series_id"] = f"S{i + 1:03d}"
    return rows


def test_relations_record_parent_child_pairs():
    table = make_tree_table()
    rows = _final(table, [row("A"), row("A", "children")])
    rel = find_relations(rows, table)
    assert {(r["ancestor_series_id"], r["descendant_series_id"], r["levels"]) for r in rel} == {
        ("S001", "S002", 1),
        ("S001", "S003", 1),
    }
    assert all(r["axis"] == "objL1" for r in rel)


def test_relations_cover_grandchildren_and_ignore_siblings_and_other_axes():
    table = make_tree_table()
    rows = _final(table, [row("T"), row("A1"), row("A2"), row("A1", other="Y")])
    rel = find_relations(rows, table)
    pairs = {(r["ancestor_series_id"], r["descendant_series_id"], r["levels"]) for r in rel}
    # T-A1 and T-A2 are two levels apart; A1-A2 siblings and A1(X)-A1(Y) are not related;
    # T(X) vs A1(Y) differs on two axes, so it is not related either.
    assert pairs == {("S001", "S002", 2), ("S001", "S003", 2)}


def test_no_relations_for_independent_selections():
    table = make_tree_table()
    assert find_relations(_final(table, [row("A1"), row("B")]), table) == []


# ---- labels (F) -------------------------------------------------------------


def test_unique_names_are_not_decorated():
    table = make_tree_table()
    names = display_names(table["dimensions"]["objL1"]["values"])
    assert names == {
        "T": "총계",
        "A": "광공업",
        "A1": "제조업",
        "A2": "광업",
        "B": "서비스업",
    }


def test_duplicate_names_get_parent_names_one_level_at_a_time():
    def path(*pairs):
        return [{"code": c, "name": n} for c, n in pairs]

    values = {
        "C": _value("최종소비지출", None, path(("C", "최종소비지출")), leaf=False),
        "G": _value("총자본형성", None, path(("G", "총자본형성")), leaf=False),
        "C1": _value("민간", "C", path(("C", "최종소비지출"), ("C1", "민간"))),
        "G1": _value("민간", "G", path(("G", "총자본형성"), ("G1", "민간"))),
        "C2": _value("정부", "C", path(("C", "최종소비지출"), ("C2", "정부"))),
    }
    names = display_names(values)
    assert names["C1"] == "최종소비지출 > 민간"
    assert names["G1"] == "총자본형성 > 민간"
    assert names["C2"] == "정부"  # unique -> unchanged


def test_duplicate_names_extend_until_unique():
    def path(*pairs):
        return [{"code": c, "name": n} for c, n in pairs]

    values = {
        "P1": _value("합계", None, path(("P1", "합계")), leaf=False),
        "P2": _value("합계", None, path(("P2", "합계")), leaf=False),
        "M1": _value("민간", "P1", path(("P1", "합계"), ("M1", "민간")), leaf=False),
        "M2": _value("민간", "P2", path(("P2", "합계"), ("M2", "민간")), leaf=False),
        "L1": _value("기타", "M1", path(("P1", "합계"), ("M1", "민간"), ("L1", "기타"))),
        "L2": _value("기타", "M2", path(("P2", "합계"), ("M2", "민간"), ("L2", "기타"))),
    }
    names = display_names(values)
    # "합계 > 민간" is still identical for both, so the next ancestor level is not
    # available; the full path is used and stays distinct only by code.
    assert names["L1"] == names["L2"] == "합계 > 민간 > 기타"
    assert names["M1"] == names["M2"] == "합계 > 민간"


def test_label_uses_disambiguated_name_but_dimension_name_stays_official():
    table = make_tree_table()
    values = table["dimensions"]["objL1"]["values"]
    values["B"]["name"] = "제조업"  # collides with A1
    values["B"]["path"][-1]["name"] = "제조업"
    rows = canonicalize_selections(
        expand_selections({"selections": [row("A1"), row("B")]}, table),
        table,
        Config(),
        query="2020년부터 2024년까지",
    )
    assert rows[0]["label"] == "지표 / 광공업 > 제조업 / X"
    assert rows[1]["label"] == "지표 / 총계 > 제조업 / X"
    assert rows[0]["dimensions"]["objL1"]["name"] == "제조업"
    assert rows[1]["dimensions"]["objL1"]["name"] == "제조업"
    assert rows[0]["label"] != rows[1]["label"]


# ---- pipeline: cap + relations ---------------------------------------------


def _run(table, selections, config, query="2020년부터 2024년까지 산업별로 보여줘", model=None):
    class Store:
        catalog = {"301:DT_TREE": {}}

        def get(self, org, tid):
            return table

    services = Services(
        Store(),
        search=None,
        model=model or ScriptedModel({"B:301:DT_TREE": [{"selections": selections}]}),
        config=config,
        trace=Trace(),
    )
    graph = build_b_graph(services)
    a_result = {"tables": [{"org_id": "301", "table_id": "DT_TREE", "table_name": "트리 표"}]}
    return graph.invoke({"query": query, "a_result": a_result}, {"recursion_limit": 20})[
        "stage_outcome"
    ]


def test_pipeline_expands_and_reports_relations():
    out = _run(make_tree_table(), [row("A"), row("A", "children")], Config())
    assert out["status"] == "ok", out["issues"]
    sels = out["result"]["selections"]
    assert [s["series_id"] for s in sels] == ["S001", "S002", "S003"]
    assert {
        (r["ancestor_series_id"], r["descendant_series_id"]) for r in out["result"]["relations"]
    } == {
        ("S001", "S002"),
        ("S001", "S003"),
    }


def test_pipeline_without_hierarchy_has_empty_relations():
    out = _run(make_tree_table(), [row("A1"), row("B")], Config())
    assert out["status"] == "ok"
    assert out["result"]["relations"] == []


def test_pipeline_over_cap_needs_confirmation_and_returns_no_selections():
    out = _run(make_tree_table(), [row("T", "leaves")], Config(max_expanded_selections=2))
    assert out["status"] == "needs_confirmation"
    assert out["issues"][0]["code"] == "selection_count_requires_confirmation"
    result = out["result"]
    assert result["selection_count"] == 3 and result["limit"] == 2
    assert "selections" not in result and len(result["candidate_labels"]) == 3


def test_pipeline_at_cap_is_ok():
    out = _run(make_tree_table(), [row("T", "leaves")], Config(max_expanded_selections=3))
    assert out["status"] == "ok"


def test_default_cap_is_30():
    assert Config().max_expanded_selections == 30
    with pytest.raises(ValueError):
        Config(max_expanded_selections=0)


# ---- golden-derived replay --------------------------------------------------


def _compress(case, store):
    """Rewrite each table's golden rows into expand rows where a group is exactly
    the children/leaves of one value. Returns (scripted answers, #compressed groups)."""
    answers = scripted_answers(case)
    compressed = 0
    for t in case["expected"]["A"]["tables"]:
        table = store.get(t["org_id"], t["table_id"])
        rows = [
            s
            for s in case["expected"]["B"]["selections"]
            if (s["org_id"], s["table_id"]) == (t["org_id"], t["table_id"])
        ]
        groups = defaultdict(list)  # everything but one axis -> rows
        for r in rows:
            for axis in r["dimensions"]:
                rest = tuple(
                    sorted((a, v["code"]) for a, v in r["dimensions"].items() if a != axis)
                )
                groups[(axis, r["item_id"], rest, r["period"]["start"], r["period"]["end"])].append(
                    r
                )
        used: set[int] = set()
        out_rows = []
        for (axis, item, rest, _s, _e), members in sorted(
            groups.items(), key=lambda kv: -len(kv[1])
        ):
            if len(members) < 2 or any(id(m) in used for m in members):
                continue
            values = table["dimensions"][axis]["values"]
            member_codes = {m["dimensions"][axis]["code"] for m in members}
            for parent, pv in values.items():
                kids = {c for c, v in values.items() if v["parent_code"] == parent}
                leaves = {
                    c
                    for c, v in values.items()
                    if v["is_leaf"] and any(p["code"] == parent for p in v["path"][:-1])
                }
                mode = (
                    "children"
                    if member_codes == kids
                    else "leaves"
                    if member_codes == leaves
                    else None
                )
                if mode:
                    base = members[0]
                    dims = {a: v["code"] for a, v in base["dimensions"].items()}
                    dims[axis] = parent
                    out_rows.append(
                        {
                            "item_id": item,
                            "dimensions": dims,
                            "period": base["period"],
                            "expand": {a: (mode if a == axis else "none") for a in dims},
                        }
                    )
                    used.update(id(m) for m in members)
                    compressed += 1
                    break
        for r in rows:
            if id(r) not in used:
                out_rows.append(
                    {
                        "item_id": r["item_id"],
                        "dimensions": {a: v["code"] for a, v in r["dimensions"].items()},
                        "period": r["period"],
                        "expand": {a: "none" for a in r["dimensions"]},
                    }
                )
        answers[f"B:{t['org_id']}:{t['table_id']}"] = [{"selections": out_rows}]
    return answers, compressed


def test_golden_cases_rebuild_identically_from_compressed_expand_rows(golden_cases, store, config):
    """Pipeline-free (so the 8 relative-period cases, which stop at a period
    confirmation, are covered too): expand + canonicalize must give back exactly
    the golden selection keys."""
    compressed_cases = {}
    for case in golden_cases:
        answers, n = _compress(case, store)
        if not n:
            continue
        compressed_cases[case["case_id"]] = n
        got, want = [], []
        for t in case["expected"]["A"]["tables"]:
            table = store.get(t["org_id"], t["table_id"])
            raw = answers[f"B:{t['org_id']}:{t['table_id']}"][0]
            rows = canonicalize_selections(expand_selections(raw, table), table, config)
            got += [selection_key(r) for r in rows]
        for s in case["expected"]["B"]["selections"]:
            want.append(
                selection_key(
                    {
                        **s,
                        "dimensions": {a: {"code": v["code"]} for a, v in s["dimensions"].items()},
                    }
                )
            )
        assert sorted(got) == sorted(want), case["case_id"]
    # CV-AB-08 (leaves), CV-AB-10/17/20 (children) are the cases the design was drawn from.
    assert {"CV-AB-08", "CV-AB-10"} <= set(compressed_cases), compressed_cases


# ---- prompt -------------------------------------------------------------------


def test_prompt_contains_expand_block_and_default_none_rule():
    from claview.b.stages import B_PROMPT, EXPAND_PROMPT

    assert EXPAND_PROMPT in B_PROMPT
    assert "기본값은 none" in EXPAND_PROMPT and "is_leaf" in EXPAND_PROMPT
    # the pre-expand prompt (what --baseline sends) is B_PROMPT minus that block
    assert "expand" not in B_PROMPT.replace(EXPAND_PROMPT, "")


# ---- guard: expand only when the question asks for a breakdown ------------------


@pytest.mark.parametrize(
    "query",
    [
        "2020~2024년 연간 업권별로 나눠 보여줘",
        "지역별 수출 규모를 비교해줘",
        "세부 항목을 보여줘",
        "구성 내역을 알려줘",
        "모든 항목을 그려줘",
        "성별 실업률 추이",
    ],
)
def test_breakdown_wording_is_recognised(query):
    assert asks_for_breakdown(query)


@pytest.mark.parametrize(
    "query",
    [
        "2024년 공산품과 서비스의 생산자물가를 월별로 비교해줘",
        "2020년 이후 분기별 GDP 성장률 추이",
        "2015~2025년 연간 실질 GDP 성장률 추이를 그래프로 보여줘.",
        "2024년 수출 공산품과 농림수산품의 가격이 전년보다 얼마나 올랐는지 비교해줘",
        "연도별 경상수지 추이",
    ],
)
def test_frequency_wording_or_plain_names_are_not_a_breakdown(query):
    assert not asks_for_breakdown(query)


def test_guard_lowers_expand_without_breakdown_wording_and_reports_it():
    raw = {"selections": [row("A", "children")]}
    guarded, notices = apply_expand_guard(raw, "2020년부터 2024년까지 광공업 추이", "301:DT_TREE")
    assert guarded["selections"][0]["expand"] == {"objL1": "none", "objL2": "none"}
    assert notices == [
        {
            "table": "301:DT_TREE",
            "axis": "objL1",
            "code": "A",
            "requested": "children",
            "applied": "none",
            "reason": "no_breakdown_wording",
        }
    ]
    assert raw["selections"][0]["expand"]["objL1"] == "children"  # input untouched


def test_guard_keeps_expand_when_breakdown_is_asked():
    raw = {"selections": [row("A", "children")]}
    guarded, notices = apply_expand_guard(raw, "광공업을 세부 업종별로 보여줘", "k")
    assert guarded is raw and notices == []


def test_guard_merges_rows_that_collapse_into_the_same_selection():
    raw = {"selections": [row("A"), row("A", "children")]}
    guarded, notices = apply_expand_guard(raw, "2020년부터 2024년까지 광공업 추이", "k")
    assert [r["dimensions"]["objL1"] for r in guarded["selections"]] == ["A"]
    assert len(notices) == 1


def test_guard_does_not_hide_the_models_own_duplicates():
    raw = {"selections": [row("A"), row("A")]}
    guarded, _ = apply_expand_guard(raw, "2020년부터 2024년까지 광공업 추이", "k")
    assert len(guarded["selections"]) == 2  # still a duplicate_selection for canonicalize


def test_pipeline_without_lowering_has_no_expand_notices():
    out = _run(make_tree_table(), [row("A1")], Config())
    assert "expand_notices" not in out["result"]


# ---- one retry after an unusable expand -----------------------------------------


class _Recorder(ScriptedModel):
    def __init__(self, answers):
        super().__init__({"B:301:DT_TREE": answers})
        self.contexts = []

    def complete(self, task, system, context, schema, max_tokens, trace):
        self.contexts.append(context)
        return super().complete(task, system, context, schema, max_tokens, trace)


def make_two_axis_table() -> dict:
    """make_tree_table plus a hierarchy on objL2: X > X1, Y flat (two expandable axes)."""
    table = make_tree_table()
    x = {"code": "X", "name": "X"}
    values = table["dimensions"]["objL2"]["values"]
    values["X"] = _value("X", None, [x], leaf=False)
    values["X1"] = _value("X1", "X", [x, {"code": "X1", "name": "X1"}])
    return table


def test_unusable_expand_is_retried_once_with_feedback():
    bad = {"selections": [row("A", "children", objL2="children")]}  # two axes expanded
    good = {"selections": [row("T", "children")]}
    model = _Recorder([bad, good])
    out = _run(make_two_axis_table(), None, Config(), model=model, query=HAS_CHILDREN_QUERY)
    assert out["status"] == "ok", out["issues"]
    assert [s["dimensions"]["objL1"]["code"] for s in out["result"]["selections"]] == ["A", "B"]
    assert len(model.contexts) == 2 and "retry_feedback" not in model.contexts[0]
    feedback = model.contexts[1]["retry_feedback"]
    assert feedback["error"] == "expand_multiple_axes"
    assert feedback["previous_answer"]["selections"][0]["dimensions"]["objL1"] == "A"
    # only values that actually have children are offered, from every axis
    assert {(v["axis"], v["code"]) for v in feedback["expandable_values"]} == {
        ("objL1", "A"),
        ("objL2", "X"),
    }


def test_expand_on_a_bottom_level_axis_value_needs_no_retry():
    # objL2 "X" is a bottom-level value: its expand is lowered, the objL1 expand is used
    bad = {"selections": [row("A", "children", objL2="children")]}
    model = _Recorder([bad])
    out = _run(make_tree_table(), None, Config(), model=model, query=HAS_CHILDREN_QUERY)
    assert out["status"] == "ok", out["issues"]
    assert len(model.contexts) == 1
    assert [s["dimensions"]["objL1"]["code"] for s in out["result"]["selections"]] == ["A1", "A2"]
    assert [n["reason"] for n in out["result"]["expand_notices"]] == [
        "expand_on_bottom_level_value"
    ]


def test_second_unusable_expand_ends_with_the_error():
    bad = {"selections": [row("A", "children", objL2="children")]}
    model = _Recorder([bad, bad])
    out = _run(make_two_axis_table(), None, Config(), model=model)
    assert out["status"] == "error"
    assert out["issues"][0]["code"] == "expand_multiple_axes"
    assert len(model.contexts) == 2


def test_retries_can_be_switched_off():
    bad = {"selections": [row("A", "children", objL2="children")]}
    model = _Recorder([bad])
    out = _run(make_two_axis_table(), None, Config(max_expand_retries=0), model=model)
    assert out["status"] == "error" and len(model.contexts) == 1
    with pytest.raises(ValueError):
        Config(max_expand_retries=-1)


# ---- CV-AB-08: 업권별 = every bottom-level value of 가계신용 (interpretation 1) ----


@pytest.mark.parametrize("mode", ["leaves", "children"])
def test_cv_ab_08_leaves_of_household_credit_give_the_14_golden_series(
    golden_cases, store, config, mode
):
    """`children` is what HCX answered live (2 series); "업권별" promotes it to `leaves`."""
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-08")
    table = store.get("301", "DT_151Y001")
    root = next(
        c for c, v in table["dimensions"]["objL1"]["values"].items() if c.endswith(".1000000")
    )
    golden = case["expected"]["B"]["selections"][0]
    answer = {
        "selections": [
            {
                "item_id": golden["item_id"],
                "dimensions": {"objL1": root},
                "period": golden["period"],
                "expand": {"objL1": mode},
            }
        ]
    }
    services = Services(
        store,
        search=None,
        model=ScriptedModel({"B:301:DT_151Y001": [answer]}),
        config=config,
        trace=Trace(),
    )
    state = build_b_graph(services).invoke(
        {"query": case["query"], "a_result": case["expected"]["A"]}, {"recursion_limit": 20}
    )
    out = state["stage_outcome"]
    assert out["status"] == "ok", out["issues"]
    got = sorted(selection_key(s) for s in out["result"]["selections"])
    want = sorted(
        selection_key(
            {**s, "dimensions": {a: {"code": v["code"]} for a, v in s["dimensions"].items()}}
        )
        for s in case["expected"]["B"]["selections"]
    )
    assert got == want and len(got) == 14
    labels = [s["label"] for s in out["result"]["selections"]]
    assert len(set(labels)) == 14  # the two "여신전문기관" are told apart
    assert sum("여신전문기관" in label for label in labels) == 2
    notices = out["result"].get("expand_notices", [])
    assert [n["applied"] for n in notices] == (["leaves"] if mode == "children" else [])


# ---- request size: B limit and question-word pruning -----------------------------


def _wide_table(n_distractors=150):
    """One axis with a target value the question names and many look-alike distractors
    that share more character pairs with the question but name nothing in it."""
    values = {}

    def add(code, name):
        values[code] = {
            "name": name,
            "unit": "%",
            "parent_code": None,
            "path": [{"code": code, "name": name}],
            "is_leaf": True,
        }

    add("TARGET", "상품수지")
    for i in range(n_distractors):
        add(f"D{i:03d}", f"이를비스를비{i}")
    table = make_tree_table()
    table["dimensions"] = {"objL1": {"name": "구분", "values": values}}
    return table


def test_question_words_keep_the_named_value_when_pruning():
    from claview.b.stages import build_b_context

    table = _wide_table()
    config = Config(max_b_context_chars=1000, max_axis_candidates=80)
    ctx = build_b_context("상품 서비스 추이를 비교", table, config)
    assert ctx["pruned_axes"] == ["objL1"]
    assert "TARGET" in ctx["dimensions"]["objL1"]["values"]
    assert len(ctx["dimensions"]["objL1"]["values"]) == 80


def test_term_score_ignores_particles_and_year_tokens():
    from claview.b.stages import query_terms, term_score

    assert query_terms("2005~2025년 연간 경상수지와 상품·서비스") == [
        "연간",
        "경상수지와",
        "상품",
        "서비스",
    ]
    terms = query_terms("경상수지와 상품")
    assert term_score(terms, "경상수지") == 4  # "경상수지와" -> leading "경상수지"
    assert term_score(terms, "상품수지") == 2
    assert term_score(terms, "이전소득수지") == 0


def test_b_limit_default_and_shared_limit_still_applies():
    from claview.b.stages import b_limit

    assert Config().max_b_context_chars == 120000 and Config().max_context_chars == 160000
    assert b_limit(Config()) == 120000
    assert b_limit(Config(max_context_chars=50000)) == 50000  # the lower of the two wins
    with pytest.raises(ValueError):
        Config(max_b_context_chars=0)


def test_large_golden_tables_are_pruned_under_the_default_limit(golden_cases, store, config):
    """DT_301Y013 (~131k chars, HTTP 429 in live runs) is shortened and still keeps every
    golden code; DT_402Y014 (~110k chars, answered fine) is sent whole."""
    from claview.b.stages import build_b_context

    for case_id, pruned in (("CV-AB-04", True), ("CV-AB-05", True), ("CV-AB-20", False)):
        case = next(c for c in golden_cases if c["case_id"] == case_id)
        t = case["expected"]["A"]["tables"][0]
        ctx = build_b_context(case["query"], store.get(t["org_id"], t["table_id"]), config)
        assert bool(ctx["pruned_axes"]) is pruned, case_id
        for sel in case["expected"]["B"]["selections"]:
            for axis, v in sel["dimensions"].items():
                assert v["code"] in ctx["dimensions"][axis]["values"], (case_id, v["code"])


def test_prompt_tells_the_model_to_pick_named_children_explicitly():
    from claview.b.stages import EXPAND_PROMPT

    assert "이름으로 하나씩" in EXPAND_PROMPT
    assert "전부 펼치지 않는다" in EXPAND_PROMPT
    assert "하위 코드를 직접 나열하지 않는다" not in EXPAND_PROMPT


# ---- expand is offered only when the question asks for a breakdown -------------------


class _Capture(ScriptedModel):
    def __init__(self, answer):
        super().__init__({"B:301:DT_TREE": [answer]})
        self.calls = []

    def complete(self, task, system, context, schema, max_tokens, trace):
        self.calls.append({"system": system, "schema": schema})
        return super().complete(task, system, context, schema, max_tokens, trace)


def _row_properties(schema):
    return schema["properties"]["selections"]["items"]["properties"]


def test_plain_question_gets_plain_schema_and_prompt_without_expand():
    plain_row = {k: v for k, v in row("T").items() if k != "expand"}
    model = _Capture({"selections": [plain_row]})
    out = _run(
        make_tree_table(), None, Config(), query="2020년부터 2024년까지 총계 추이", model=model
    )
    assert out["status"] == "ok", out["issues"]
    assert "expand" not in _row_properties(model.calls[0]["schema"])
    assert model.calls[0]["system"] == B_PROMPT_PLAIN
    assert "expand" not in B_PROMPT_PLAIN
    assert "expand_notices" not in out["result"]


def test_breakdown_question_gets_expand_schema_and_prompt():
    model = _Capture({"selections": [row("T", "children")]})
    _run(make_tree_table(), None, Config(), model=model, query=HAS_CHILDREN_QUERY)
    assert "expand" in _row_properties(model.calls[0]["schema"])
    assert model.calls[0]["system"] == B_PROMPT


def test_scripted_fixture_with_expand_is_accepted_for_a_plain_question():
    # fixtures always carry expand; replay drops it when the schema does not offer it
    out = _run(make_tree_table(), [row("T")], Config(), query="2020년부터 2024년까지 총계 추이")
    assert out["status"] == "ok", out["issues"]


def test_with_default_expand_fills_only_missing_rows():
    raw = {"selections": [{k: v for k, v in row("A").items() if k != "expand"}, row("T", "leaves")]}
    filled = with_default_expand(raw)
    assert filled["selections"][0]["expand"] == {"objL1": "none", "objL2": "none"}
    assert filled["selections"][1]["expand"]["objL1"] == "leaves"
    assert "expand" not in raw["selections"][0]


# ---- "~별" without a depth means every bottom-level value -------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        ("가계신용을 업권별로 나눠 보여줘", True),
        ("지역별 수출 규모를 비교해줘", True),
        ("월별 추이를 보여줘", False),
        ("분기별 GDP", False),
        ("광공업 세부 항목 추이", False),
    ],
)
def test_split_by_wording(query, expected):
    assert asks_to_split_by(query) is expected


def test_children_become_leaves_for_a_split_by_question():
    table = make_tree_table()
    raw = {"selections": [row("T", "children")]}
    promoted, notices = promote_children_to_leaves(raw, "산업별로 보여줘", table, "301:DT_TREE")
    assert promoted["selections"][0]["expand"]["objL1"] == "leaves"
    assert notices == [
        {
            "table": "301:DT_TREE",
            "axis": "objL1",
            "code": "T",
            "requested": "children",
            "applied": "leaves",
            "reason": "split_by_wording_all_levels",
        }
    ]
    assert raw["selections"][0]["expand"]["objL1"] == "children"  # input untouched


@pytest.mark.parametrize(
    "query",
    [
        "산업별로 한 단계 아래만 보여줘",
        "산업을 바로 아래 단계로 나눠 업종별로 보여줘",
        "대분류별로 보여줘",
        "중분류별 추이",
    ],
)
def test_stage_limiting_wording_keeps_children(query):
    raw = {"selections": [row("T", "children")]}
    promoted, notices = promote_children_to_leaves(raw, query, make_tree_table(), "k")
    assert promoted is raw and notices == []


def test_children_stay_when_there_is_no_split_by_wording():
    raw = {"selections": [row("T", "children")]}
    promoted, notices = promote_children_to_leaves(raw, "총계의 세부 항목", make_tree_table(), "k")
    assert promoted is raw and notices == []


def test_children_that_are_already_the_bottom_level_are_not_reported():
    raw = {"selections": [row("A", "children")]}  # A1, A2 are leaves
    promoted, notices = promote_children_to_leaves(raw, "산업별로 보여줘", make_tree_table(), "k")
    assert promoted["selections"][0]["expand"]["objL1"] == "children" and notices == []


def test_leaves_and_none_are_untouched_by_promotion():
    raw = {"selections": [row("T", "leaves"), row("B")]}
    promoted, notices = promote_children_to_leaves(raw, "산업별로 보여줘", make_tree_table(), "k")
    assert [r["expand"]["objL1"] for r in promoted["selections"]] == ["leaves", "none"]
    assert notices == []


def test_pipeline_promotes_children_to_all_bottom_values_and_reports_it():
    out = _run(make_tree_table(), [row("T", "children")], Config())  # default query: 산업별로
    assert out["status"] == "ok", out["issues"]
    assert [s["dimensions"]["objL1"]["code"] for s in out["result"]["selections"]] == [
        "A1",
        "A2",
        "B",
    ]
    assert [n["applied"] for n in out["result"]["expand_notices"]] == ["leaves"]


def test_pipeline_keeps_children_when_the_question_limits_the_depth():
    out = _run(
        make_tree_table(),
        [row("T", "children")],
        Config(),
        query="2020년부터 2024년까지 산업별로 한 단계 아래만 보여줘",
    )
    assert [s["dimensions"]["objL1"]["code"] for s in out["result"]["selections"]] == ["A", "B"]
    assert "expand_notices" not in out["result"]


# ---- the model asked for a breakdown but never expanded (CV-AB-10) ---------------------


def test_split_axes_matches_the_word_against_the_official_axis_name():
    table = make_tree_table()  # objL1 "산업", objL2 "구분"
    assert split_axes("산업별로 보여줘", table) == ["objL1"]
    assert split_axes("수출을산업별로 보여줘", table) == ["objL1"]  # glued to the word before
    assert split_axes("구분별로 보여줘", table) == ["objL2"]
    assert split_axes("업권별로 보여줘", table) == []
    assert split_axes("월별 산업 추이", table) == []  # a time unit is not a split word


def test_unused_split_expands_the_single_parent_value_of_the_named_axis():
    raw = {"selections": [row("T")]}
    repaired, notices = repair_unused_split(raw, "산업별로 보여줘", make_tree_table(), "k")
    assert repaired["selections"][0]["expand"] == {"objL1": "leaves", "objL2": "none"}
    assert notices == [
        {
            "table": "k",
            "axis": "objL1",
            "code": "T",
            "requested": "none",
            "applied": "leaves",
            "reason": "split_axis_not_expanded",
        }
    ]
    assert raw["selections"][0]["expand"]["objL1"] == "none"  # input untouched


def test_unused_split_uses_children_when_the_question_limits_the_depth():
    raw = {"selections": [row("T")]}
    repaired, _ = repair_unused_split(raw, "산업별로 한 단계 아래만", make_tree_table(), "k")
    assert repaired["selections"][0]["expand"]["objL1"] == "children"


def test_unused_split_leaves_other_cases_alone():
    table = make_tree_table()
    query = "산업별로 보여줘"
    untouched = {
        "model already expanded": {"selections": [row("T", "children")]},
        "bottom-level value": {"selections": [row("B")]},
        "two values listed on the axis": {"selections": [row("A"), row("B")]},
        "no rows": {"selections": []},
    }
    for label, raw in untouched.items():
        repaired, notices = repair_unused_split(raw, query, table, "k")
        assert repaired is raw and notices == [], label
    # no axis name carries the word / no split word at all
    for q in ("업권별로 보여줘", "총계 추이를 보여줘"):
        repaired, notices = repair_unused_split({"selections": [row("T")]}, q, table, "k")
        assert notices == [], q


def test_unused_split_needs_exactly_one_matching_axis():
    table = make_tree_table()
    table["dimensions"]["objL2"]["name"] = "산업구분"  # both axis names now contain "산업"
    raw = {"selections": [row("T")]}
    repaired, notices = repair_unused_split(raw, "산업별로 보여줘", table, "k")
    assert repaired is raw and notices == []


def test_pipeline_repairs_the_total_only_answer():
    out = _run(make_tree_table(), [row("T")], Config())  # default query: 산업별로
    assert out["status"] == "ok", out["issues"]
    assert [s["dimensions"]["objL1"]["code"] for s in out["result"]["selections"]] == [
        "A1",
        "A2",
        "B",
    ]
    assert [n["reason"] for n in out["result"]["expand_notices"]] == ["split_axis_not_expanded"]


def test_cv_ab_10_total_without_expand_gives_the_8_golden_series(golden_cases, store, config):
    """Live HCX answered `수출 x 총계` with every expand none, 3 of 3 times."""
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-10")
    golden = case["expected"]["B"]["selections"]
    first = golden[0]
    table = store.get("301", "DT_301Y015")
    total = next(c for c, v in table["dimensions"]["objL2"]["values"].items() if c.endswith(".SUM"))
    answer = {
        "selections": [
            {
                "item_id": first["item_id"],
                "dimensions": {"objL1": first["dimensions"]["objL1"]["code"], "objL2": total},
                "period": first["period"],
                "expand": {"objL1": "none", "objL2": "none"},
            }
        ]
    }
    services = Services(
        store,
        search=None,
        model=ScriptedModel({"B:301:DT_301Y015": [answer]}),
        config=config,
        trace=Trace(),
    )
    state = build_b_graph(services).invoke(
        {"query": case["query"], "a_result": case["expected"]["A"]}, {"recursion_limit": 20}
    )
    out = state["stage_outcome"]
    assert out["status"] == "ok", out["issues"]
    got = sorted(selection_key(s) for s in out["result"]["selections"])
    want = sorted(
        selection_key(
            {**s, "dimensions": {a: {"code": v["code"]} for a, v in s["dimensions"].items()}}
        )
        for s in golden
    )
    assert got == want and len(got) == 8
    assert [n["reason"] for n in out["result"]["expand_notices"]] == ["split_axis_not_expanded"]


def test_golden_answers_are_never_changed_by_the_repair(golden_cases, store):
    """The golden selections are explicit codes: feeding them back as a model answer
    must not trigger the repair in any case (no false positives on the golden set)."""
    for case in golden_cases:
        for t in case["expected"]["A"]["tables"]:
            table = store.get(t["org_id"], t["table_id"])
            rows = [
                {
                    "item_id": s["item_id"],
                    "dimensions": {a: v["code"] for a, v in s["dimensions"].items()},
                    "period": s["period"],
                    "expand": {a: "none" for a in s["dimensions"]},
                }
                for s in case["expected"]["B"]["selections"]
                if s["table_id"] == t["table_id"]
            ]
            if not rows:
                continue
            _, notices = repair_unused_split({"selections": rows}, case["query"], table, "k")
            assert notices == [], case["case_id"]


# ---- "[참고]" lines are memo items, not parts of the parent (CV-AB-09) -----------------


def _table_with_reference_item():
    table = make_tree_table()
    t = {"code": "T", "name": "총계"}
    table["dimensions"]["objL1"]["values"]["R"] = _value(
        "[참고] 구 지표", "T", [t, {"code": "R", "name": "[참고] 구 지표"}]
    )
    return table


def test_reference_lines_are_not_part_of_children_or_leaves():
    table = _table_with_reference_item()
    for mode in ("children", "leaves"):
        out = expand_selections({"selections": [row("T", mode)]}, table)
        assert "R" not in codes(out), mode
    assert codes(expand_selections({"selections": [row("T", "children")]}, table)) == ["A", "B"]


def test_a_reference_line_can_still_be_selected_by_code():
    table = _table_with_reference_item()
    out = canonicalize_selections(
        expand_selections({"selections": [row("R")]}, table), table, Config()
    )
    assert out[0]["dimensions"]["objL1"]["code"] == "R"


def test_cv_ab_09_children_of_m2_give_the_14_golden_series(golden_cases, store, config):
    """HCX answers `children` of M2; the old-M2 reference line must not come along."""
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-09")
    table = store.get("301", "DT_161Y008")
    root = next(c for c in table["dimensions"]["objL1"]["values"] if c.endswith(".BBGA00"))
    out = _golden_run(case, store, config, root, "children")
    assert out["status"] == "ok", out["issues"]
    assert _keys(out) == _golden_keys(case) and len(_keys(out)) == 14


# ---- the question names some children: select those, not all of them (CV-AB-13) ----------


def test_named_children_replace_the_expand():
    raw = {"selections": [row("T", "children")]}
    result, notices = select_named_children(
        raw, "광공업과 서비스업 산업별로 보여줘", make_tree_table(), "k"
    )
    assert codes(result) == ["A", "B"]
    assert all(set(r["expand"].values()) == {"none"} for r in result["selections"])
    assert notices == [
        {
            "table": "k",
            "axis": "objL1",
            "code": "T",
            "requested": "children",
            "applied": "named_children",
            "reason": "query_names_children",
            "selected": ["A", "B"],
        }
    ]
    assert raw["selections"][0]["expand"]["objL1"] == "children"  # input untouched


def test_named_children_also_replace_leaves_and_keep_other_axes():
    raw = {"selections": [row("T", "leaves", other="Y")]}
    result, _ = select_named_children(raw, "광공업 서비스업 비교", make_tree_table(), "k")
    assert [(r["dimensions"]["objL1"], r["dimensions"]["objL2"]) for r in result["selections"]] == [
        ("A", "Y"),
        ("B", "Y"),
    ]


def test_one_name_or_deeper_names_do_not_count():
    table = make_tree_table()
    for query in ("서비스업만 산업별로", "제조업과 광업 산업별로", "산업별로 보여줘"):
        raw = {"selections": [row("T", "children")]}
        result, notices = select_named_children(raw, query, table, "k")
        assert result["selections"] == raw["selections"] and notices == [], query


def test_named_children_do_not_duplicate_rows_the_model_already_wrote():
    raw = {"selections": [row("A"), row("T", "children")]}
    result, _ = select_named_children(raw, "광공업 서비스업 산업별로", make_tree_table(), "k")
    assert codes(result) == ["A", "B"]


def test_named_children_keep_the_models_own_duplicates():
    raw = {"selections": [row("A"), row("A")]}
    result, notices = select_named_children(raw, "광공업 서비스업 산업별로", make_tree_table(), "k")
    assert codes(result) == ["A", "A"] and notices == []


def test_cv_ab_13_named_funds_give_the_3_golden_series(golden_cases, store, config):
    """HCX answers `children` of 총대출금 (then 27 series after promotion); 3 are named."""
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-13")
    table = store.get("301", "DT_104Y016")
    root = next(c for c in table["dimensions"]["objL1"]["values"] if c.endswith(".BDCA1"))
    for mode in ("children", "leaves"):
        out = _golden_run(case, store, config, root, mode)
        assert out["status"] == "ok", out["issues"]
        assert _keys(out) == _golden_keys(case) and len(_keys(out)) == 3, mode
        assert [n["reason"] for n in out["result"]["expand_notices"]] == ["query_names_children"]


def _golden_keys(case):
    return sorted(
        selection_key(
            {**s, "dimensions": {a: {"code": v["code"]} for a, v in s["dimensions"].items()}}
        )
        for s in case["expected"]["B"]["selections"]
    )


def _keys(out):
    return sorted(selection_key(s) for s in out["result"]["selections"])


def _golden_run(case, store, config, root, mode):
    first = case["expected"]["B"]["selections"][0]
    answer = {
        "selections": [
            {
                "item_id": first["item_id"],
                "dimensions": {"objL1": root},
                "period": first["period"],
                "expand": {"objL1": mode},
            }
        ]
    }
    table_id = first["table_id"]
    services = Services(
        store,
        search=None,
        model=ScriptedModel({f"B:301:{table_id}": [answer]}),
        config=config,
        trace=Trace(),
    )
    state = build_b_graph(services).invoke(
        {"query": case["query"], "a_result": case["expected"]["A"]}, {"recursion_limit": 20}
    )
    return state["stage_outcome"]


# ---- 2026-10-12 live run (22 cases): bottom-level expand and period text ------------------


def test_expand_on_a_bottom_level_value_becomes_none_and_is_reported():
    raw = {"selections": [row("B", "children"), row("A1", "leaves")]}
    lowered, notices = lower_expand_on_leaves(raw, make_tree_table(), "301:DT_TREE")
    assert [r["expand"]["objL1"] for r in lowered["selections"]] == ["none", "none"]
    assert [(n["code"], n["requested"], n["applied"], n["reason"]) for n in notices] == [
        ("B", "children", "none", "expand_on_bottom_level_value"),
        ("A1", "leaves", "none", "expand_on_bottom_level_value"),
    ]
    assert raw["selections"][0]["expand"]["objL1"] == "children"  # input untouched


def test_expand_on_a_value_with_children_is_kept():
    raw = {"selections": [row("A", "children")]}
    lowered, notices = lower_expand_on_leaves(raw, make_tree_table(), "k")
    assert lowered["selections"][0]["expand"]["objL1"] == "children" and notices == []


def test_leaf_lowering_merges_rows_it_makes_identical_but_not_the_models_duplicates():
    merged, _ = lower_expand_on_leaves(
        {"selections": [row("B"), row("B", "children")]}, make_tree_table(), "k"
    )
    assert [r["dimensions"]["objL1"] for r in merged["selections"]] == ["B"]
    own, _ = lower_expand_on_leaves({"selections": [row("B"), row("B")]}, make_tree_table(), "k")
    assert len(own["selections"]) == 2


def test_pipeline_bottom_level_expand_gives_the_named_series_without_retry():
    model = ScriptedModel(
        {"B:301:DT_TREE": [{"selections": [row("B", "children"), row("A1", "children")]}]}
    )
    out = _run(
        make_tree_table(),
        [],
        Config(),
        query="2020년부터 2024년까지 서비스업·제조업으로 나눠",
        model=model,
    )
    assert out["status"] == "ok", out["issues"]
    assert [s["dimensions"]["objL1"]["code"] for s in out["result"]["selections"]] == ["B", "A1"]
    reasons = [n["reason"] for n in out["result"]["expand_notices"]]
    assert reasons == ["expand_on_bottom_level_value"] * 2


@pytest.mark.parametrize(
    ("frequency", "text", "expected"),
    [
        ("M", "2024-01", "202401"),
        ("M", "2024.1", "202401"),
        ("M", "2024/12", "202412"),
        ("M", "202401", "202401"),
        ("Q", "2024-Q2", "202402"),
        ("H", "2024 H1", "202401"),
        ("Y", "2024", "2024"),
        ("Y", "2024-01", "2024-01"),
        ("M", "최근", "최근"),
    ],
)
def test_model_period_text_is_normalised_to_table_format(frequency, text, expected):
    raw = {"selections": [{"period": {"frequency": frequency, "start": text, "end": text}, "x": 1}]}
    fixed = normalize_model_periods(raw)["selections"][0]
    assert fixed["period"]["start"] == expected and fixed["period"]["end"] == expected
    assert fixed["x"] == 1 and raw["selections"][0]["period"]["start"] == text


def _replay(case, store, config, selections):
    first = case["expected"]["B"]["selections"][0]
    services = Services(
        store,
        search=None,
        model=ScriptedModel({f"B:301:{first['table_id']}": [{"selections": selections}]}),
        config=config,
        trace=Trace(),
    )
    state = build_b_graph(services).invoke(
        {"query": case["query"], "a_result": case["expected"]["A"]}, {"recursion_limit": 20}
    )
    return state["stage_outcome"]


def test_cv_ab_07_children_on_two_bottom_level_values_give_the_2_golden_series(
    golden_cases, store, config
):
    """HCX picked both values and added `children` to each; twice, so a retry cannot help."""
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-07")
    selections = [
        {
            "item_id": s["item_id"],
            "dimensions": {a: v["code"] for a, v in s["dimensions"].items()},
            "period": s["period"],
            "expand": {a: "children" for a in s["dimensions"]},
        }
        for s in case["expected"]["B"]["selections"]
    ]
    out = _replay(case, store, config, selections)
    assert out["status"] == "ok", out["issues"]
    assert _keys(out) == _golden_keys(case)


def test_cv_ab_19_period_text_with_hyphen_gives_the_2_golden_series(golden_cases, store, config):
    """HCX wrote 2024-01 / 2024-12, which failed with invalid_period."""
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-19")
    selections = [
        {
            "item_id": s["item_id"],
            "dimensions": {a: v["code"] for a, v in s["dimensions"].items()},
            "period": {"frequency": "M", "start": "2024-01", "end": "2024-12"},
        }
        for s in case["expected"]["B"]["selections"]
    ]
    out = _replay(case, store, config, selections)
    assert out["status"] == "ok", out["issues"]
    assert _keys(out) == _golden_keys(case)


# ---- reverse check: the longer value the question names in full ------------------


def make_named_table() -> dict:
    """Flat objL1 with 대외금융자산 / 순대외금융자산 / 대외금융부채 (names overlap)."""
    table = make_tree_table()
    names = {"P": "대외금융자산", "N": "순대외금융자산", "D": "대외금융부채"}
    table["dimensions"]["objL1"]["values"] = {
        c: _value(n, None, [{"code": c, "name": n}]) for c, n in names.items()
    }
    return table


def _named(query, *codes, expand="none"):
    raw = {"selections": [row(c, expand) for c in codes]}
    return correct_to_named_value(raw, query, make_named_table(), "T")


def test_chosen_name_inside_a_longer_named_value_is_replaced():
    fixed, notices = _named("순대외금융자산 규모 추이", "P")
    assert codes(fixed) == ["N"]
    assert notices == [
        {
            "table": "T",
            "axis": "objL1",
            "code": "P",
            "requested": "none",
            "applied": "named_value",
            "reason": "query_names_longer_value",
            "selected": ["N"],
        }
    ]


@pytest.mark.parametrize(
    "query, chosen, expand",
    [
        ("순대외금융자산 규모 추이", "N", "none"),  # already the longer value
        ("대외금융자산 규모 추이", "P", "none"),  # the question names the shorter one
        ("대외금융자산과 순대외금융자산 비교", "P", "none"),  # shorter one named on its own too
        ("순대외금융자산 규모 추이", "P", "children"),  # expand rows are left alone
        ("외환보유액 추이", "P", "none"),  # nothing named
    ],
)
def test_reverse_check_leaves_other_selections_alone(query, chosen, expand):
    fixed, notices = _named(query, chosen, expand=expand)
    assert codes(fixed) == [chosen] and notices == []


def test_reverse_check_needs_exactly_one_longer_named_value():
    table = make_named_table()
    values = table["dimensions"]["objL1"]["values"]
    values["M"] = _value("신순대외금융자산", None, [{"code": "M", "name": "신순대외금융자산"}])
    raw = {"selections": [row("P")]}
    fixed, notices = correct_to_named_value(raw, "신순대외금융자산 순대외금융자산", table, "T")
    assert codes(fixed) == ["P"] and notices == []


def test_rows_that_become_identical_after_the_correction_are_merged():
    fixed, notices = _named("순대외금융자산 규모 추이", "P", "N")
    assert codes(fixed) == ["N"] and len(notices) == 1


def test_cv_ab_12_wrong_value_is_corrected_to_the_golden_series(golden_cases, store, config):
    """HCX chose 대외금융자산 for "순대외금융자산"."""
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-12")
    golden = case["expected"]["B"]["selections"][0]
    table = store.get(golden["org_id"], golden["table_id"])
    axis, value = next(iter(golden["dimensions"].items()))
    wrong = next(
        c for c, v in table["dimensions"][axis]["values"].items() if v["name"] == "대외금융자산"
    )
    selections = [
        {
            "item_id": golden["item_id"],
            "dimensions": {axis: wrong},
            "period": golden["period"],
        }
    ]
    out = _replay(case, store, config, selections)
    assert out["status"] == "ok", out["issues"]
    assert _keys(out) == _golden_keys(case)
    assert out["result"]["expand_notices"][0]["reason"] == "query_names_longer_value"


def test_reverse_check_never_changes_a_golden_selection(golden_cases, store):
    """Feeding every golden answer back in as the model output must change nothing."""
    for case in golden_cases:
        by_table = defaultdict(list)
        for s in case["expected"]["B"]["selections"]:
            by_table[s["table_id"]].append(s)
        for table_id, rows in by_table.items():
            table = store.get(rows[0]["org_id"], table_id)
            raw = {
                "selections": [
                    {
                        "item_id": s["item_id"],
                        "dimensions": {a: v["code"] for a, v in s["dimensions"].items()},
                        "period": s["period"],
                        "expand": {a: "none" for a in s["dimensions"]},
                    }
                    for s in rows
                ]
            }
            fixed, notices = correct_to_named_value(raw, case["query"], table, table_id)
            assert notices == [] and fixed == raw, case["case_id"]


# ---- reverse check rules A (named parent) and B ("X를 뺀" -> X이외) --------------------


def make_group_table() -> dict:
    """총지수 > 원재료 > (원재료국내출하, 원재료수입); 총지수 > 신선식품구분/식료품구분 groups."""
    table = make_tree_table()
    top = {"code": "T", "name": "총지수"}
    raw = {"code": "R", "name": "원재료"}
    fresh = {"code": "FG", "name": "신선식품구분"}
    food = {"code": "DG", "name": "식료품구분"}

    def leaf(code, name, *above):
        return _value(name, above[-1]["code"], [top, *above, {"code": code, "name": name}])

    table["dimensions"]["objL1"]["values"] = {
        "T": _value("총지수", None, [top], leaf=False),
        "R": _value("원재료", "T", [top, raw], leaf=False),
        "R1": leaf("R1", "원재료국내출하", raw),
        "R2": leaf("R2", "원재료수입", raw),
        "FG": _value("신선식품구분", "T", [top, fresh], leaf=False),
        "F1": leaf("F1", "신선식품", fresh),
        "F2": leaf("F2", "신선식품이외", fresh),
        "DG": _value("식료품구분", "T", [top, food], leaf=False),
        "D1": leaf("D1", "식료품", food),
        "D2": leaf("D2", "식료품이외", food),
    }
    return table


def _grouped(query, *codes, expand="none"):
    raw = {"selections": [row(c, expand) for c in codes]}
    return correct_to_named_value(raw, query, make_group_table(), "T")


def test_sub_type_of_a_named_value_is_replaced_by_the_named_value():
    fixed, notices = _grouped("원재료 물가 추이", "R1")
    assert codes(fixed) == ["R"]
    assert [(n["code"], n["selected"], n["reason"]) for n in notices] == [
        ("R1", ["R"], "query_names_parent_value")
    ]


@pytest.mark.parametrize(
    "query, chosen, expand",
    [
        ("원재료국내출하 물가 추이", "R1", "none"),  # the sub-type is named in full
        ("원재료 물가 추이", "R", "none"),  # already the named value
        ("원재료 물가 추이", "R1", "children"),  # expand rows are left alone
        ("원재료수입 물가 추이", "R1", "none"),  # a different sub-type is named, not the parent
        ("중간재 물가 추이", "R1", "none"),  # the parent is not named
    ],
)
def test_named_parent_rule_leaves_other_selections_alone(query, chosen, expand):
    fixed, notices = _grouped(query, chosen, expand=expand)
    assert codes(fixed) == [chosen] and notices == []


def test_named_parent_rule_needs_a_3_character_name_and_a_name_prefix():
    table = make_group_table()
    values = table["dimensions"]["objL1"]["values"]
    # 2-character ancestor name: too weak a signal
    values["R"]["name"] = "원료"
    values["R"]["path"][-1]["name"] = "원료"
    values["R1"]["path"][-2]["name"] = "원료"
    fixed, notices = correct_to_named_value({"selections": [row("R1")]}, "원료 추이", table, "T")
    assert codes(fixed) == ["R1"] and notices == []
    # the sub-type's name does not start with the ancestor's name
    values["R"]["name"] = "원재료"
    values["R"]["path"][-1]["name"] = "원재료"
    values["R1"]["path"][-2]["name"] = "원재료"
    values["R1"]["name"] = "국내출하"
    fixed, notices = correct_to_named_value({"selections": [row("R1")]}, "원재료 추이", table, "T")
    assert codes(fixed) == ["R1"] and notices == []


def test_excluded_value_picks_the_matching_except_value():
    fixed, notices = _grouped("신선식품과 신선식품을 뺀 물가 비교", "F1", "D2")
    assert codes(fixed) == ["F1", "F2"]
    assert [(n["code"], n["selected"], n["reason"]) for n in notices] == [
        ("D2", ["F2"], "query_excludes_named_value")
    ]


@pytest.mark.parametrize(
    "query, chosen",
    [
        ("신선식품을 뺀 물가", "F2"),  # already right
        ("식료품을 뺀 물가", "D2"),  # the model's value is the excluded one's `이외`
        ("신선식품 물가", "D2"),  # no exclusion wording
        ("식료품이외 물가와 신선식품 제외 물가", "D2"),  # the chosen name is in the question
        ("신선식품을 뺀 물가", "F1"),  # not an `이외` value
    ],
)
def test_excluded_value_rule_leaves_other_selections_alone(query, chosen):
    fixed, notices = _grouped(query, chosen)
    assert codes(fixed) == [chosen] and notices == []


def _wrong_pick_case(golden_cases, store, config, case_id, wrong_names):
    """Replay the answer HCX gave in the live check: each golden value swapped for its wrong one."""
    case = next(c for c in golden_cases if c["case_id"] == case_id)
    first = case["expected"]["B"]["selections"][0]
    table = store.get(first["org_id"], first["table_id"])
    values = table["dimensions"]["objL1"]["values"]
    by_name = {v["name"]: c for c, v in values.items()}
    selections = [
        {
            "item_id": s["item_id"],
            "dimensions": {"objL1": by_name[wrong_names.get(s["dimensions"]["objL1"]["name"])]}
            if s["dimensions"]["objL1"]["name"] in wrong_names
            else {"objL1": s["dimensions"]["objL1"]["code"]},
            "period": s["period"],
        }
        for s in case["expected"]["B"]["selections"]
    ]
    return case, _replay(case, store, config, selections)


def test_cv_ab_21_sub_types_are_replaced_by_the_named_values(golden_cases, store, config):
    """HCX picked 원재료국내출하 / 중간재국내출하 / 최종재국내출하 for 원재료·중간재·최종재."""
    case, out = _wrong_pick_case(
        golden_cases,
        store,
        config,
        "CV-AB-21",
        {"원재료": "원재료국내출하", "중간재": "중간재국내출하", "최종재": "최종재국내출하"},
    )
    assert out["status"] == "ok", out["issues"]
    assert _keys(out) == _golden_keys(case)
    reasons = {n["reason"] for n in out["result"]["expand_notices"]}
    assert reasons == {"query_names_parent_value"}


def test_cv_ab_17_wrong_except_value_is_replaced(golden_cases, store, config):
    """HCX picked 식료품이외 for "신선식품을 뺀"."""
    case, out = _wrong_pick_case(
        golden_cases, store, config, "CV-AB-17", {"신선식품이외": "식료품이외"}
    )
    assert out["status"] == "ok", out["issues"]
    assert _keys(out) == _golden_keys(case)
    assert [n["reason"] for n in out["result"]["expand_notices"]] == ["query_excludes_named_value"]
