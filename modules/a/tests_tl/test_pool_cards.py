"""Plan 37 step 1: the question pool and the candidate cards (catalog only: no model, no network).

The synthetic-table tests are the counterexamples of the step-1 review (user 2026-10-27 20:28 UTC) and the three
conditions of 20:31 UTC."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from claview_a import cards as C
from claview_a.pool import EXTRA_CAP, build_pool, pool_tables, queries_of
from claview_a.search.candidates import POOL_CAP, family_key
from claview_a.search.families import SIGNATURE_AXES, family_members
from claview_a.search.tables import selection_catalog

LOAN = "2024년 1~3월 예금은행 가계대출 금리를 신규취급액 기준으로 월별로 보여줘"
M2 = "2024년 1~3월 M2 총액을 평잔·원계열 기준으로 월별로 보여줘"
PPI = "2024년 신선식품 생산자물가와 신선식품을 뺀 물가를 월별 그래프로 비교해줘"


@dataclass
class Item:
    name_raw: str
    obj_id: str = "ITM"
    unit: str | None = None


@dataclass
class View:
    table_id: str
    title: str
    items: list = field(default_factory=list)
    periods: list = field(default_factory=list)
    dims: list = field(default_factory=lambda: [("ITM", "항목")])
    axis_roles: dict = field(default_factory=dict)


def items(*names):
    return [Item(n) for n in names]


# ---------------------------------------------------------------- pool


def test_the_question_is_the_first_query_and_extra_words_follow_once():
    q = queries_of(" 질문 ", ["대외채무", "", "대외채무", "질문"])
    assert [(r["text"], r["source"]) for r in q] == [("질문", "question"), ("대외채무", "extra")]
    assert [r["query_id"] for r in q] == ["q1", "q2"]


def test_the_pool_keeps_whole_families_in_the_order_of_their_best_hit(layer):
    pool = build_pool(LOAN, layer)
    assert 0 < len(pool["hits"]) <= POOL_CAP
    ranks = [f["best_rank"] for f in pool["families"]]
    assert ranks == sorted(ranks)
    hit_ids = [h["table_id"] for h in pool["hits"]]
    assert sorted(t for f in pool["families"] for t in f["hit_tables"]) == sorted(hit_ids)
    for f in pool["families"]:
        assert f["members"] == family_members(layer, f["family"])
        assert all(family_key(layer, t) == f["family"] for t in f["hit_tables"])
    assert set(hit_ids) <= set(pool_tables(pool))


def test_extra_words_only_add_after_the_question_pool(layer):
    base = build_pool(LOAN, layer)
    more = build_pool(LOAN, layer, extra=["현금흐름표"])  # the review's counterexample: it used to push hits out
    n = len(base["hits"])
    assert [h["table_id"] for h in more["hits"][:n]] == [h["table_id"] for h in base["hits"]]
    added = more["hits"][n:]
    assert 0 < len(added) <= EXTRA_CAP and {h["source"] for h in added} == {"extra"}
    assert not {h["table_id"] for h in added} & {h["table_id"] for h in base["hits"]}
    assert [f["family"] for f in more["families"][:len(base["families"])]] == [f["family"] for f in base["families"]]


# ---------------------------------------------------------------- words and axes


def test_question_words_cut_particles_and_drop_number_words():
    words = C.question_words(M2)
    assert "M2" in words and "총액" in words and "평잔" in words
    assert not any(re.search(r"\d", w) and not re.search(r"[A-Za-z]", w) for w in words)
    assert "기준" not in words  # a function word of the selector's stop list
    assert "가계대출" in C.question_words("가계대출을 보여줘")


def test_ask_axes_are_basis_axes():
    assert set(C.ASK_AXES) <= set(SIGNATURE_AXES)
    assert set(C.CODE_KINDS) == {"frequency", "period", "source"}


# ---------------------------------------------------------------- table basis (review major 2, condition 1)


def test_table_basis_comes_from_title_brackets_and_value_words_are_reference_only():
    v = View("T1", "물가지수(기본분류)", items("도시가스", "원화수입수수료", "총지수"))
    assert C.title_basis(v) == {"classification": ["기본분류"]}
    ref = C.item_basis(v)
    assert ref["currency"] == {"values": ["원화"], "sources": {"원화": ["원화수입수수료"]}}
    assert "valuation" not in ref  # a title-only axis is not read from values (도시가스 holds 시가)


def test_a_value_word_never_makes_a_split():
    rows = [C._table_row(View("T1", "물가지수(기본분류)", items("원화수입수수료", "도시가스"))),
            C._table_row(View("T2", "물가지수(특수분류)", items("총지수")))]
    assert [s["axis"] for s in C._splits(rows)] == ["classification"]


def test_a_table_without_a_bracket_value_is_unknown_not_another_value():
    rows = [C._table_row(View("T1", "대출(기본분류)")), C._table_row(View("T2", "대출(특수분류)")),
            C._table_row(View("T3", "대출(업권별)"))]
    (split,) = C._splits(rows)
    assert split["values"] == {"T1": ["기본분류"], "T2": ["특수분류"]} and split["unknown"] == ["T3"]
    one = [C._table_row(View("T1", "대출(용도별)")), C._table_row(View("T2", "대출(업권별)"))]
    assert C._splits(one) == []  # one valued table and one unknown: no split


# ---------------------------------------------------------------- editions (review major 3, condition 2)


def test_tables_that_differ_in_content_are_not_editions():
    a = View("A1", "통화(평잔, 원계열)", items("총액", "가계"), [("M", "2003.10", "2026.07")])
    b = View("A2", "통화(평잔, 원계열)", items("총액", "기업"), [("M", "1986.01", "2004.09")])
    assert C._editions([a, b]) == []


def test_editions_are_checked_group_by_group_and_keep_their_kinds():
    old = [("M", "1986.01", "2004.09")]
    new = [("M", "2003.10", "2026.07")]
    views = [View("S1", "통화(평잔, 원계열)", items("총액"), old), View("S2", "통화(평잔, 원계열)", items("총액"), new),
             View("S3", "통화(평잔, 계절조정계열)", items("총액"), old),
             View("S4", "통화(평잔, 계절조정계열)", items("총액"), new)]
    groups = C._editions(views)
    assert sorted(g["tables"] for g in groups) == [["S1", "S2"], ["S3", "S4"]]
    assert all(set(g["kinds"]) <= set(C.CODE_KINDS) for g in groups)
    rows = [C._table_row(v) for v in views]
    assert [s["axis"] for s in C._splits(rows)] == ["seasonal"]  # the seasonal split stays, editions fold apart


def test_a_frequency_candidate_with_other_values_is_kept_but_not_called_the_same():
    # the re-review's counterexample: dimension_kinds reports a frequency alone although the indicator values differ
    a = View("F1", "대출(말잔)", items("가계"), [("M", "2003.10", "2026.07")], axis_roles={"ITM": "indicator"})
    b = View("F2", "대출(말잔)", items("기업"), [("Q", "2003 4/4", "2026 2/4")], axis_roles={"ITM": "indicator"})
    (group,) = C._editions([a, b])
    assert group["tables"] == ["F1", "F2"] and group["items"] == "differ"
    c = View("F3", "대출(말잔)", items("가계"), [("Q", "2003 4/4", "2026 2/4")], axis_roles={"ITM": "indicator"})
    assert C._editions([a, c])[0]["items"] == "same"
    card = {"card_id": "g1", "name": "대출", "tables": [C._table_row(v) for v in (a, b)], "splits": [],
            "editions": [group], "other_words": [], "matched": []}
    text = C.render_card(card)
    assert "주기·판본 후보: t2 (항목 이름 다름)" in text and "같은 내용" not in text  # t1 ends later: the newest


def test_nested_brackets_keep_their_outer_words():
    assert C.title_parts("지역별 기업경기조사(실적(2013.1~))") == ("지역별 기업경기조사", ["실적(2013.1~)"])
    assert C.title_parts("기업경기실사지수((구)전망(~2012.7))") == ("기업경기실사지수", ["(구)전망(~2012.7)"])
    assert C.title_parts("물가(원계열, 실질)") == ("물가", ["원계열, 실질"])
    assert C.title_parts("표(1)(2)") == ("표", ["1", "2"]) and C.title_parts("표(열림") == ("표(열림", [])
    assert C.flat_title("지역별 기업경기조사(실적(2013.1~))") == "지역별 기업경기조사(실적, 2013.1~)"
    rows = [C._table_row(View("N1", "조사(실적(2013.1~))")), C._table_row(View("N2", "조사(전망(2013.2~))"))]
    assert [r["basis"] for r in rows] == [{"survey_view": ["실적"]}, {"survey_view": ["전망"]}]
    assert [s["axis"] for s in C._splits(rows)] == ["survey_view"]
    assert C.family_name("조사(전망(2013.2~))") == "조사"


def test_real_nested_bracket_siblings_split_on_their_survey_view(layer):
    views = selection_catalog(layer).views
    for a, b in (("DT_512Y011", "DT_512Y012"), ("DT_512Y017", "DT_512Y018"), ("DT_512Y019", "DT_512Y020")):
        rows = [C._table_row(views[a]), C._table_row(views[b])]
        assert [r["basis"].get("survey_view") for r in rows] == [["실적"], ["전망"]], (a, b)
        assert "survey_view" in [s["axis"] for s in C._splits(rows)]
        assert C.family_name(views[a].title) == C.family_name(views[b].title)
        assert "(" not in C.family_name(views[a].title)


def test_the_newest_edition_reads_months_and_quarters():
    late = {"table_id": "N1", "periods": [{"frequency": "M", "start": "2000.01", "end": "2026.12"}]}
    early = {"table_id": "N2", "periods": [{"frequency": "M", "start": "2000.01", "end": "2026.01"}]}
    assert C.newest([late, early])["table_id"] == "N1"  # the review's counterexample (year-only compare)
    q = {"table_id": "N3", "periods": [{"frequency": "Q", "start": "2000 1/4", "end": "2026 2/4"}]}
    m = {"table_id": "N4", "periods": [{"frequency": "M", "start": "2000.01", "end": "2026.07"}]}
    assert C.newest([q, m])["table_id"] == "N4" and C.newest([m, q])["table_id"] == "N4"
    assert C._period_key("2025") == (2025, 12) and C._period_key("2026 2/4") == (2026, 6)


# ---------------------------------------------------------------- matched values (review major 1, minor 5)


def test_matched_values_keep_their_own_tables_and_facts_never_borrow_a_siblings():
    views = [View("T1", "통화(평잔, 원계열)", items("M2(평잔 원계열)")),
             View("T2", "통화(말잔, 원계열)", items("M2(말잔 원계열)"))]
    (m,) = C._matched(views, ["M2"])
    assert {it["name"]: it["tables"] for it in m["items"]} == {"M2(평잔 원계열)": ["T1"], "M2(말잔 원계열)": ["T2"]}
    card = {"name": "통화", "tables": [C._table_row(v) for v in views], "matched": [m]}
    facts = C.card_facts(card)
    assert "M2(평잔 원계열)" in facts["T1"] and "M2(말잔 원계열)" not in facts["T1"]
    assert "M2(말잔 원계열)" in facts["T2"] and "M2(평잔 원계열)" not in facts["T2"]


def test_every_matched_value_is_shown_once_with_all_its_tables():
    """No cap per word (user 2026-10-28 05:05 UTC): every matched name, each once, with every table that holds it."""
    views = [View(f"T{i}", f"표{i}", items(f"가계대출-{i}", "가계대출")) for i in range(6)]
    (m,) = C._matched(views, ["가계대출"])
    assert [it["name"] for it in m["items"]] == ["가계대출"] + [f"가계대출-{i}" for i in range(6)]
    assert m["items"][0]["tables"] == [f"T{i}" for i in range(6)]
    assert all(it["tables"] == [f"T{i}"] for i, it in enumerate(m["items"][1:]))
    assert "more" not in m


# ---------------------------------------------------------------- real cards


def _card_with(cards, axis):
    return next(c for c in cards if axis in {s["axis"] for s in c["splits"]})


def test_the_loan_card_shows_its_split_and_no_leftover_bracket_word(layer):
    cards = C.build_cards(LOAN, build_pool(LOAN, layer), layer)
    card = _card_with(cards, "loan_basis")
    split = next(s for s in card["splits"] if s["axis"] == "loan_basis")
    assert split["ask_axis"] and not split["unknown"] and len(card["tables"]) >= 2
    assert "잔액" not in card["other_words"]  # '잔액 기준' is one basis word (review minor 5)
    facts = C.card_facts(card)
    for row in card["tables"]:
        assert set(row["brackets"]) <= facts[row["table_id"]]


def test_real_card_facts_hold_only_names_of_that_table(layer):
    views = selection_catalog(layer).views
    for q in (LOAN, M2, PPI):
        for card in C.build_cards(q, build_pool(q, layer), layer):
            facts = C.card_facts(card)
            for m in card["matched"]:
                for it in m["items"]:
                    for t in it["tables"]:
                        assert it["name"] in {x.name_raw for x in views[t].items}
                    for row in card["tables"]:
                        own = it["name"] in {x.name_raw for x in views[row["table_id"]].items}
                        assert (it["name"] in facts[row["table_id"]]) == own or it["name"] in row["brackets"]


def test_the_price_card_has_no_split_read_from_value_names(layer):
    cards = C.build_cards(PPI, build_pool(PPI, layer), layer)
    for card in cards:
        for s in card["splits"]:
            assert s["axis"] not in ("currency", "valuation"), (card["name"], s)


def test_m2_editions_are_grouped_per_series_and_every_table_has_its_line(layer):
    cards = C.build_cards(M2, build_pool(M2, layer), layer)
    card = next(c for c in cards if c["editions"] and len(c["splits"]) >= 2)
    assert len(card["editions"]) >= 2  # one group per basis combination, not one family-wide check
    text = C.render_card(card)
    num = C.table_numbers(card)
    lines = {m.group(1): ln for ln in text.splitlines() if (m := re.match(r"(t\d+) DT_", ln))}
    assert set(lines) == set(num.values())  # every table on its own line
    rows = {r["table_id"]: r for r in card["tables"]}
    for g in card["editions"]:
        main = C.newest([rows[t] for t in g["tables"]])["table_id"]
        others = sorted(num[t] for t in g["tables"] if t != main)
        assert sorted(re.search(r"주기·판본 후보: ([t\d,]+) \(", lines[num[main]]).group(1).split(",")) == others
    assert "같은 내용" not in text


def test_card_numbers_map_one_to_one_to_table_ids_and_facts_keep_real_ids(layer):
    for q in (LOAN, M2, PPI):
        cards = C.build_cards(q, build_pool(q, layer), layer)
        ref = C.refs(cards)
        assert len(ref) == sum(len(c["tables"]) for c in cards)
        for card in cards:
            num = C.table_numbers(card)
            every = list(num)
            assert sorted(num.values(), key=lambda n: int(n[1:])) == [f"t{i}" for i in range(1, len(num) + 1)]
            text = C.render_card(card)
            back = {n: t for t, n in num.items()}

            def tables(nums: str) -> list[str]:
                return every if nums == "전체" else [back[n] for n in nums.split(",")]

            for t, n in num.items():
                assert ref[f"{card['card_id']}.{n}"] == t
                assert len(re.findall(rf"^{n} {t} \|", text, re.M)) == 1  # defined once, next to its real ID
            assert set(C.card_facts(card)) == set(num)  # facts are keyed by real table IDs
            # matched values: each name shown once, on the line of exactly its tables
            body = text.split("맞은 항목(", 1)[1].split("\n")[1:] if card["matched"] else []
            shown = {}
            for ln in body:
                m = re.match(r"(전체|t[\d,t]+): (.*)$", ln)
                if m:
                    for name in re.split(r", (?![^‹]*›)", m.group(2)):  # commas inside ‹ › are not separators
                        assert name not in shown
                        shown[name] = tables(m.group(1))
            for m in card["matched"]:
                for it in m["items"]:
                    assert shown[C.shown_value(it)] == it["tables"]  # name, catalog parent and unit (cards .10)
            # split lines: values and unknowns name exactly their tables
            for s in card["splits"]:
                line = re.search(rf"^갈림 {s['axis']}: (.*)$", text, re.M).group(1)
                pairs = dict(x.split("=", 1) for x in line.split(" "))
                assert tables(pairs.pop("?")) == s["unknown"] if s["unknown"] else "?" not in pairs
                got = {t: v for v, nums in pairs.items() for t in tables(nums)}
                assert got == {t: "/".join(v) for t, v in s["values"].items()}


def test_every_card_renders_within_the_input_limit_with_the_legend_once(layer):
    from claview_a.contract import PAYLOAD_LIMIT, build_payload, canonical

    for q in (LOAN, M2, PPI):
        cards = C.build_cards(q, build_pool(q, layer), layer)
        text = C.render_cards(cards)
        assert 0 < len(canonical(build_payload(q, cards))) <= PAYLOAD_LIMIT  # the input limit (05:05 UTC), not 8,000
        assert "카드 읽는 법" not in text  # the notation is not repeated per card


def test_the_package_names_no_table():
    pkg = Path(C.__file__).resolve().parent
    for p in pkg.rglob("*"):  # every package file, the extracted current-path code included (no v3 folder remains)
        if p.is_file() and p.suffix in (".py", ".json") and p.relative_to(pkg).parts[0] != "v3":
            assert not re.search(r"DT_\d{3}Y\d{3}", p.read_text(encoding="utf-8")), p.name
