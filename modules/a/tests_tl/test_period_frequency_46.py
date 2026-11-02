"""Period quotes and the requested frequency of ask options (user 2026-10-29 07:51 UTC).

- "YYYY년 기준" is the quoted year; only explicit base-year marks ("2020=100", "기준연도 2020년", "2015년 기준년") are
  taken out; only a lone-기준 year directly before another period ("2015년 기준 2024년") cannot be told and is
  ``period_base_year_ambiguous`` (user 08:24 UTC: not every second period); why a default period applies is
  recorded.
- "최근 8개 분기" reads as "최근 8분기"; unsupported forms stay unread.
- When the model quoted one readable frequency, an ask option counts only if its value gives that frequency (the
  option table, or a same-basis frequency/edition candidate of it that keeps the option's cited values by the
  period rule: user 08:24 UTC); nothing is added, switched or aggregated.

Pairs through the straight graph on the real catalog (fake model, no network). Tables are found by card name, title
basis and catalog frequencies, never by ID."""

from __future__ import annotations

import json

from conftest import fill_option_evidence  # noqa: E402

from claview_a import check as CH
from claview_a import periods as PR
from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings
from claview_a.graph import run
from claview_a.pool import build_pool

AS_OF = "2026-10-22"
GDP = "국내총생산에 대한 지출"
M2H = "M2 경제주체별 보유현황"


def freqs(row) -> set[str]:
    return {p["frequency"] for p in row["periods"]}


def ref(card, has=None, lacks=None, **basis):
    """The card reference of a table of ``card`` with these title basis values (and a catalog frequency it has or
    lacks); the last such table in ID order when several."""
    rows = [r for r in card["tables"] if all(r["basis"].get(a) == [v] for a, v in basis.items())
            and (has is None or has in freqs(r)) and (lacks is None or lacks not in freqs(r))]
    row = sorted(rows, key=lambda r: r["table_id"])[-1]
    return f"{card['card_id']}.{table_numbers(card)[row['table_id']]}", row["table_id"]


def output(quote, splitters=(), period="2024년", frequency=None, choice=None, asks=()) -> dict:
    out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                           "missing", "alternatives")}
    out["statistics"] = [{"id": "s1", "quote": quote, "name": quote}]
    out["splitters"] = [{"quote": q, "axis": a, "for": ["s1"]} for q, a in splitters]
    out["periods"] = [{"quote": period, "for": ["s1"]}] if period else []
    out["frequencies"] = [{"quote": frequency, "for": ["s1"]}] if frequency else []
    if choice:
        out["choices"] = [{"for": ["s1"], "table": choice, "evidence": [{"kind": "title", "text": quote}]}]
    out["asks"] = [{"for": "s1", "axis": axis,
                    "options": [{"value": o[0], "table": o[1], **({"evidence": o[2]} if len(o) > 2 else {})}
                                for o in options]}
                   for axis, options in asks]
    return out


def go(question, name, build, layer):
    cards = build_cards(question, build_pool(question, layer), layer)
    card = next(c for c in cards if c["name"] == name)
    out = fill_option_evidence(build(card), cards)
    state = run(question, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    return state, state["record"]["decisions"][0]


def codes(d):
    return [r["code"] for r in d["reasons"]]


def no_switch_or_add(d):
    assert not any(c["code"] in ("basis_switched", "unneeded_ask_to_choice") for c in d["corrections"])


def problems(d):
    return [p for r in d["reasons"] if r["code"] == "ask_invalid" for p in r["detail"]["problems"]]


# ---------------------------------------------------------------- 1. "YYYY년 기준"


def test_a_year_with_a_lone_standard_word_is_the_quoted_year():
    got = PR.read_quotes(["2024년 기준"], [])
    assert got["problems"] == [] and got["readings"][0]["base_year_removed"] == []
    assert got["readings"][0]["plain_standard"] == ["2024년 기준"]
    [c] = got["conditions"]
    assert c["attrs"]["form"] == "point" and c["attrs"]["start_expr"]["year"] == "2024"
    again = PR.read_quotes(["2024년 기준으로"], [])
    assert [x["attrs"]["start_expr"]["year"] for x in again["conditions"]] == ["2024"] and again["problems"] == []
    span = PR.read_quotes(["2020~2024년 기준"], [])  # one range, not two periods: nothing ambiguous
    assert span["problems"] == [] and [x["attrs"]["form"] for x in span["conditions"]] == ["range"]


def test_explicit_base_year_marks_are_taken_out_and_give_no_period():
    for quote in ("2020=100", "(2020=100)", "기준연도 2020년", "2015년 기준년", "2015년 기준연도"):
        got = PR.read_quotes([quote], [])
        assert got["conditions"] == [] and got["problems"] == [], quote
        assert got["readings"][0]["base_year_removed"], quote


def test_an_explicit_base_year_and_an_observation_year_in_one_quote():
    for quote, removed in (("2015년 기준년 2024년", ["2015년 기준년"]), ("2020=100 기준 2024년", ["2020=100"])):
        got = PR.read_quotes([quote], [])
        assert got["problems"] == [] and got["readings"][0]["base_year_removed"] == removed
        assert [c["attrs"]["start_expr"]["year"] for c in got["conditions"]] == ["2024"]


def years(got):
    return [(c["attrs"]["form"], (c["attrs"]["start_expr"] or {}).get("year")) for c in got["conditions"]]


def test_a_lone_standard_year_directly_before_another_period_is_ambiguous_never_removed():
    for quote in ("2015년 기준 2024년", "2015년 기준으로 2024년", "2015년 기준의 2024년"):
        got = PR.read_quotes([quote], [])
        [p] = got["problems"]
        assert p["code"] == "period_base_year_ambiguous" and p["expression"] == quote, quote
        assert got["readings"][0]["base_year_removed"] == []
    # two separate quotes are each kept as the model quoted them: never ambiguous for standing next to each other
    # (user 08:42 UTC)
    got = PR.read_quotes(["2015년 기준", "2024년"], [])
    assert got["problems"] == [] and sorted(years(got)) == [("point", "2015"), ("point", "2024")]


def test_two_observation_points_with_standard_words_stay_two_points():
    for quotes in (["2023년 기준과 2024년 기준"], ["2023년과 2024년 기준"], ["2024년 기준", "2023년"],
                   ["2023년 기준", "2024년 기준"]):
        got = PR.read_quotes(quotes, [])
        assert got["problems"] == [] and sorted(years(got)) == [("point", "2023"), ("point", "2024")], quotes
    got = PR.read_quotes(["2020년과 2024년"], [])  # two separate years: two points, never one stretched range
    assert got["problems"] == [] and years(got) == [("point", "2020"), ("point", "2024")]


def test_the_same_year_quoted_twice_is_not_ambiguous():
    got = PR.read_quotes(["2024년 기준", "2024년"], [])
    assert got["problems"] == [] and years(got) == [("point", "2024"), ("point", "2024")]


def test_the_ambiguous_quote_stops_the_statistic_without_a_default(layer):
    q = "2015년 기준 2024년 실질 원계열 국내총생산에 대한 지출을 연간으로 보여줘"
    state, d = go(q, GDP, lambda card: output(GDP, [("실질", "price"), ("원계열", "seasonal")], "2015년 기준 2024년",
                                              "연간", ref(card, price="실질", seasonal="원계열")[0]), layer)
    assert codes(d) == ["period_base_year_ambiguous"] and d["final_tables"] == []
    assert "default_applied" not in d["period"]


def test_two_points_and_a_repeated_year_through_the_graph(layer):
    q = "2023년 기준과 2024년 기준의 실질 원계열 국내총생산에 대한 지출을 연간으로 비교해줘"
    _, d = go(q, GDP, lambda card: output(GDP, [("실질", "price"), ("원계열", "seasonal")],
                                          "2023년 기준과 2024년 기준", "연간",
                                          ref(card, price="실질", seasonal="원계열")[0]), layer)
    assert d["outcome"] == "selected" and codes(d) == []
    # two points read; the months are exactly those two years (adjacent years are written as one span)
    assert [r["period"] for r in d["final_tables"]] == [[{"start": "2023-01", "end": "2024-12"}]]
    q = "2020년 기준과 2024년 기준의 실질 원계열 국내총생산에 대한 지출을 연간으로 비교해줘"
    _, d = go(q, GDP, lambda card: output(GDP, [("실질", "price"), ("원계열", "seasonal")],
                                          "2020년 기준과 2024년 기준", "연간",
                                          ref(card, price="실질", seasonal="원계열")[0]), layer)
    assert d["outcome"] == "selected" and codes(d) == []  # never stretched to 2020~2024
    assert [r["period"] for r in d["final_tables"]] == [[{"start": "2020-01", "end": "2020-12"},
                                                         {"start": "2024-01", "end": "2024-12"}]]
    q = "2024년 기준 실질 원계열 국내총생산에 대한 지출을 2024년 연간으로 보여줘"

    def twice(card):
        out = output(GDP, [("실질", "price"), ("원계열", "seasonal")], "2024년 기준", "연간",
                     ref(card, price="실질", seasonal="원계열")[0])
        out["periods"].append({"quote": "2024년", "for": ["s1"]})
        return out

    _, d = go(q, GDP, twice, layer)
    assert d["outcome"] == "selected" and codes(d) == []
    assert [r["period"] for r in d["final_tables"]] == [[{"start": "2024-01", "end": "2024-12"}]]


def test_each_statistic_keeps_its_own_period_and_calculations_are_not_periods(layer):
    q = "2023년 기준 실질 원계열 국내총생산에 대한 지출과 2024년 명목 원계열 국내총생산에 대한 지출을 연간으로 보여줘"

    def build(card):
        out = output(GDP, (), None, None)
        out["statistics"] = [{"id": "s1", "quote": "실질 원계열 국내총생산에 대한 지출", "name": "실질"},
                             {"id": "s2", "quote": "명목 원계열 국내총생산에 대한 지출", "name": "명목"}]
        out["splitters"] = [{"quote": "실질", "axis": "price", "for": ["s1"]},
                            {"quote": "명목", "axis": "price", "for": ["s2"]},
                            {"quote": "원계열", "axis": "seasonal", "for": ["s1", "s2"]}]
        out["periods"] = [{"quote": "2023년 기준", "for": ["s1"]}, {"quote": "2024년", "for": ["s2"]}]
        out["frequencies"] = [{"quote": "연간", "for": ["s1", "s2"]}]
        out["choices"] = [{"for": ["s1"], "table": ref(card, price="실질", seasonal="원계열")[0],
                           "evidence": [{"kind": "title", "text": GDP}]},
                          {"for": ["s2"], "table": ref(card, price="명목", seasonal="원계열")[0],
                           "evidence": [{"kind": "title", "text": GDP}]}]
        return out

    cards = build_cards(q, build_pool(q, layer), layer)
    out = fill_option_evidence(build(next(c for c in cards if c["name"] == GDP)), cards)
    state = run(q, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    d = {x["statistic"]: x for x in state["record"]["decisions"]}
    assert [r["period"] for r in d["s1"]["final_tables"]] == [[{"start": "2023-01", "end": "2023-12"}]]
    assert [r["period"] for r in d["s2"]["final_tables"]] == [[{"start": "2024-01", "end": "2024-12"}]]
    # a comparison base in calculations is not read as an observation period
    q = "2024년 기준 실질 원계열 국내총생산에 대한 지출의 2023년 대비 증가율을 연간으로 보여줘"

    def calc(card):
        out = output(GDP, [("실질", "price"), ("원계열", "seasonal")], "2024년 기준", "연간",
                     ref(card, price="실질", seasonal="원계열")[0])
        out["calculations"] = [{"quote": "2023년 대비 증가율", "for": ["s1"]}]
        return out

    _, d = go(q, GDP, calc, layer)
    assert [r["period"] for r in d["final_tables"]] == [[{"start": "2024-01", "end": "2024-12"}]]


def test_the_quoted_year_is_used_through_the_graph(layer):
    q = "2024년 기준 실질 원계열 국내총생산에 대한 지출을 연간으로 보여줘"
    state, d = go(q, GDP, lambda card: output(GDP, [("실질", "price"), ("원계열", "seasonal")], "2024년 기준",
                                              "연간", ref(card, price="실질", seasonal="원계열")[0]), layer)
    assert d["outcome"] == "selected" and d["period"]["default_applied"] is False
    assert d["period"]["default_reason"] is None
    assert [r["frequency"] for r in d["final_tables"]] == ["Y"]
    assert all("2024" in json.dumps(r["period"]) for r in d["final_tables"])


def test_why_a_default_period_applies_is_recorded(layer):
    # plan 56 order 5 (policy ②, user 2026-10-31 17:02). Before: default_applied True with default_reason "only
    # base-year marks were quoted" / "no period quoted". Now neither question has a period to go on: the period is
    # asked (cause user_omitted), default_applied False, default_reason None, no default period decided or handed on.
    q = "실질 원계열 국내총생산에 대한 지출을 2020=100 기준으로 연간으로 보여줘"
    _, d = go(q, GDP, lambda card: output(GDP, [("실질", "price"), ("원계열", "seasonal")], "2020=100", "연간",
                                          ref(card, price="실질", seasonal="원계열")[0]), layer)
    assert d["outcome"] == "ask_condition" and d["period"]["default_applied"] is False
    assert d["period"]["default_reason"] is None and d["period"]["cause"] == "user_omitted"
    q = "실질 원계열 국내총생산에 대한 지출을 연간으로 보여줘"
    _, d = go(q, GDP, lambda card: output(GDP, [("실질", "price"), ("원계열", "seasonal")], None, "연간",
                                          ref(card, price="실질", seasonal="원계열")[0]), layer)
    assert d["outcome"] == "ask_condition" and d["period"]["default_applied"] is False
    assert d["period"]["default_reason"] is None and d["period"]["cause"] == "user_omitted"


# ---------------------------------------------------------------- 2. "최근 N개 분기"


def recent(quote):
    got = PR.read_quotes([quote], [])
    assert got["problems"] == [], quote
    [c] = got["conditions"]
    return {k: c["attrs"][k] for k in ("form", "amount", "unit", "relative")}


def test_recent_quarters_with_the_counter_read_as_recent_quarters():
    same = recent("최근 8분기")
    assert same == {"form": "recent", "amount": 8, "unit": "quarter", "relative": True}
    for quote in ("최근 8개 분기", "최근 8개분기", "최근8개분기", "최근 8 개 분기", "최근 8개 분기 동안",
                  "지난 8개 분기간"):
        assert recent(quote) == same, quote
    assert recent("최근 2개 반기") == recent("최근 2반기") == {"form": "recent", "amount": 2, "unit": "half",
                                                                  "relative": True}


def test_unsupported_recent_forms_stay_unread():
    for quote in ("최근 여덟 분기", "최근 몇 개 분기", "최근 8개의 분기"):
        assert [p["code"] for p in PR.read_quotes([quote], [])["problems"]] == ["period_unresolved"], quote


def test_both_recent_quarter_forms_give_the_same_tables_and_the_stated_count(layer):
    got = []
    for quote in ("최근 8개 분기", "최근 8분기"):
        q = f"{quote} 동안 실질 원계열 국내총생산에 대한 지출을 분기별로 보여줘"
        _, d = go(q, GDP, lambda card, quote=quote: output(
            GDP, [("실질", "price"), ("원계열", "seasonal")], f"{quote} 동안", "분기별",
            ref(card, price="실질", seasonal="원계열")[0]), layer)
        assert d["outcome"] == "selected"
        got.append([(r["table_id"], r["frequency"], json.dumps(r["period"], sort_keys=True))
                    for r in d["final_tables"]])
    assert got[0] == got[1]
    _, d = go("최근 8개 분기 동안 실질 원계열 국내총생산에 대한 지출을 분기별로 보여줘", GDP, lambda card: output(
        GDP, [("실질", "price"), ("원계열", "seasonal")], "최근 8개 분기 동안", "분기별",
        ref(card, price="실질", seasonal="원계열")[0]), layer)
    [row] = d["final_tables"]
    assert row["frequency"] == "Q"
    [span] = row["period"]
    (y0, m0), (y1, m1) = (map(int, span[k].split("-")) for k in ("start", "end"))
    assert (y1 - y0) * 12 + m1 - m0 + 1 == 8 * 3 and m1 % 3 == 0  # eight whole quarters, as stated


# ---------------------------------------------------------------- 3. requested frequency of ask options


def seasonal_ask(card, frequency=None, **basis):
    return ("seasonal", [(v, ref(card, seasonal=v, **basis)[0]) for v in ("원계열", "계절조정")])


def test_annual_request_an_option_value_without_annual_tables_makes_the_ask_invalid(layer):
    # plan 56 order 8 (bundle 7, ⑥ and ④ supplement, users 2026-10-31 21:54): the 계절조정 option has no annual
    # table but a finer one, so its annual values need an aggregation whose method is not confirmed: unconfirmed,
    # never unsupported, and the other option is not chosen for it. Held. Before order 8: ask_invalid,
    # option_frequency_unsupported; order 8-1: auto_decided.
    q = "2024년 연간 실질 국내총생산에 대한 지출을 보여줘"
    state, d = go(q, GDP, lambda card: output(GDP, [("실질", "price")], "2024년", "연간",
                                              asks=[seasonal_ask(card, price="실질")]), layer)
    assert codes(d) == ["target_table_unconfirmed"] and d["asks"] == []
    assert not [c for c in d["corrections"] if c["code"] == "auto_decided"]
    assert [(o["value"], o["status"], o["why"]) for o in d["reasons"][0]["detail"]["options"]] == [
        ("원계열", "supported", []), ("계절조정", "unconfirmed", ["frequency"])]
    rows = {r["value"]: r for r in d["ask_frequency_checks"]}
    assert rows["원계열"]["supported"] is True and rows["계절조정"]["supported"] is False
    assert rows["계절조정"]["requested"] == "Y" and rows["계절조정"]["supported_by"] == []
    assert d["asks"] == [] and d["final_tables"] == []
    no_switch_or_add(d)


def test_annual_request_the_one_value_that_gives_it_is_chosen_without_an_ask(layer):
    q = "2024년 연간 실질 국내총생산에 대한 지출을 보여줘"
    _, d = go(q, GDP, lambda card: output(GDP, [("실질", "price")], "2024년", "연간",
                                          ref(card, price="실질", seasonal="원계열")[0]), layer)
    assert d["outcome"] == "selected" and [r["frequency"] for r in d["final_tables"]] == ["Y"]
    assert d["basis"]["seasonal"]["source"] == "model_choice" and d["ask_frequency_checks"] == []


def test_quarterly_request_both_values_give_it_and_the_ask_stands(layer):
    q = "2024년 분기별 실질 국내총생산에 대한 지출을 보여줘"
    state, d = go(q, GDP, lambda card: output(GDP, [("실질", "price")], "2024년", "분기별",
                                              asks=[seasonal_ask(card, price="실질")]), layer)
    assert d["outcome"] == "ask" and codes(d) == []
    assert {r["value"]: r["supported"] for r in d["ask_frequency_checks"]} == {"원계열": True, "계절조정": True}
    assert [o["value"] for a in d["asks"] for o in a["options"]] == ["원계열", "계절조정"]


def test_unstated_or_unreadable_frequency_removes_no_alternative(layer):
    cases = (("2024년 실질 국내총생산에 대한 지출을 보여줘", None),
             ("2024년 실질 국내총생산에 대한 지출을 어쩌다 한 번씩 보여줘", "어쩌다"),
             ("2024년 실질 국내총생산에 대한 지출을 연간 및 분기별로 보여줘", "연간 및 분기별"))
    for q, frequency in cases:
        _, d = go(q, GDP, lambda card, frequency=frequency: output(
            GDP, [("실질", "price")], "2024년", frequency, asks=[seasonal_ask(card, price="실질")]), layer)
        assert d["ask_frequency_checks"] == [], q
        if frequency is None:
            assert d["outcome"] == "ask", q
            assert [o["value"] for a in d["asks"] for o in a["options"]] == ["원계열", "계절조정"], q
        else:  # plan 56 order 8 (bundle 7, ⑥, registered): an unreadable frequency leaves both options
            # unconfirmed: held, none removed. Before: ask
            assert codes(d) == ["target_table_unconfirmed"], q
            assert [(o["value"], o["status"]) for o in d["reasons"][0]["detail"]["options"]] == [
                ("원계열", "unconfirmed"), ("계절조정", "unconfirmed")], q


def test_stated_seasonal_basis_without_annual_tables_is_unmet_never_switched(layer):
    q = "2024년 연간 계절조정 실질 국내총생산에 대한 지출을 보여줘"
    state, d = go(q, GDP, lambda card: output(GDP, [("계절조정", "seasonal"), ("실질", "price")], "2024년", "연간",
                                              ref(card, price="실질", seasonal="계절조정")[0]), layer)
    _, chosen = ref(next(c for c in state["cards"] if c["name"] == GDP), price="실질", seasonal="계절조정")
    assert codes(d) == ["frequency_not_offered"] and d["final_tables"] == []
    assert d["representative"] == chosen and d["basis"]["seasonal"]["value"] == ["계절조정"]
    no_switch_or_add(d)


def m2_holders(item, own_item=None, extra=()):
    """An annual 평잔 M2-holder question asking the seasonal axis: the 원계열 option has annual tables itself, the
    계절조정 option is the monthly-only table whose same-basis candidate has annual data. ``item``: the item the
    계절조정 option cites (None: title only)."""
    q = f"2024년 연간 평잔 기준 M2 경제주체별 보유현황 중 {own_item or '가계 및 비영리단체'} 보유분을 보여줘"

    def build(card):
        title = [{"kind": "title", "text": M2H}]
        options = [("원계열", ref(card, stock="평잔", seasonal="원계열", has="Y")[0], title),
                   ("계절조정", ref(card, stock="평잔", seasonal="계절조정", lacks="Y")[0],
                    title + ([{"kind": "item", "text": item}] if item else []) + list(extra))]
        return output(M2H, [("평잔", "stock")], "2024년", "연간", asks=[("seasonal", options)])

    return q, build


def seasonal_row(d):
    return next(r for r in d["ask_frequency_checks"] if r["value"] == "계절조정")


def test_a_same_basis_candidate_that_keeps_the_cited_item_gives_the_frequency(layer):
    q, build = m2_holders("가계 및 비영리단체 2)")
    state, d = go(q, M2H, build, layer)
    card = next(c for c in state["cards"] if c["name"] == M2H)
    _, monthly = ref(card, stock="평잔", seasonal="계절조정", lacks="Y")
    _, sibling = ref(card, stock="평잔", seasonal="계절조정", has="Y")
    assert d["outcome"] == "ask" and codes(d) == []
    row = seasonal_row(d)
    assert row["table"] == monthly and "Y" not in row["frequencies"] and row["status"] == "supported"
    assert row["supported_by"] == [sibling]
    [sib] = [c for c in row["candidates"] if c["table"] == sibling]
    assert sib["offers_frequency"] is True and sib["items_kept"] == "cited_values_held"
    # the option keeps its own table: nothing is replaced by the candidate
    assert [o["table_id"] for a in d["asks"] for o in a["options"] if o["value"] == "계절조정"] == [monthly]


def test_a_candidate_with_the_frequency_but_not_the_cited_item_is_held_not_absent(layer):
    q, build = m2_holders("기업 3)", own_item="기업")
    state, d = go(q, M2H, build, layer)
    card = next(c for c in state["cards"] if c["name"] == M2H)
    _, sibling = ref(card, stock="평잔", seasonal="계절조정", has="Y")
    row = seasonal_row(d)
    assert row["status"] == "items_kept_unconfirmed" and row["offering"] == [sibling] and row["supported_by"] == []
    [sib] = [c for c in row["candidates"] if c["table"] == sibling]
    assert sib["offers_frequency"] is True and sib["items_kept"] == "content_unverified" and not sib["confirmed"]
    assert codes(d) == ["target_table_unconfirmed"] and d["reasons"][0]["detail"]["stage"] == "ask_frequency"
    assert "option_frequency_unsupported:계절조정" not in json.dumps(d["reasons"], ensure_ascii=False)
    assert d["asks"] == [] and state["action"] != "handoff"


def test_an_invalid_cited_item_is_never_read_as_no_item(layer):
    q, build = m2_holders(None, extra=[{"kind": "item", "text": "없는 항목 이름"}])
    _, d = go(q, M2H, build, layer)
    row = seasonal_row(d)
    [sib] = [c for c in row["candidates"] if c["offers_frequency"]]
    assert sib["items_kept"] == "retention_unverified" and row["status"] == "items_kept_unconfirmed"
    assert codes(d) == ["target_table_unconfirmed"]


def test_title_only_evidence_counts_when_the_candidate_has_the_same_value_names(layer):
    q = "2024년 분기별 M1 상품별 구성내역의 M1 총액을 말잔 기준으로 보여줘"
    name = "M1 상품별 구성내역"

    def build(card):
        options = [("원계열", ref(card, stock="말잔", seasonal="원계열", has="Q")[0]),
                   ("계절조정", ref(card, stock="말잔", seasonal="계절조정", lacks="Q")[0])]
        return output(name, [("말잔", "stock")], "2024년", "분기별", asks=[("seasonal", options)])

    state, d = go(q, name, build, layer)
    # plan 56 order 8 (bundle 7, ⑥, registered policy change): the 계절조정 option has the frequency but not
    # 2024 (its period check fails), so 원계열 is chosen by ⑥-나 (auto_decided). Before: ask. The frequency
    # check rows below are as before.
    assert d["outcome"] == "selected" and codes(d) == [] and d["basis"]["seasonal"]["source"] == "auto_decided"
    rows = {r["value"]: r for r in d["ask_frequency_checks"]}
    assert rows["원계열"]["status"] == "supported" and rows["원계열"]["table"] in rows["원계열"]["supported_by"]
    assert rows["계절조정"]["status"] == "supported"
    [sib] = [c for c in rows["계절조정"]["candidates"] if c["table"] != rows["계절조정"]["table"]]
    assert sib["items_kept"] == "same_value_names" and rows["계절조정"]["supported_by"] == [sib["table"]]


def test_of_several_candidates_only_the_one_keeping_the_cited_item_counts(layer):
    """A card with two same-basis candidates that give annual data, one keeping the cited item and one not: the
    real catalog has no such pair, so the second is a real table whose title basis is set to the option's here
    (its own items are used)."""
    import copy

    from claview_a.search.tables import selection_catalog

    q, _ = m2_holders("M2(평잔 계절조정계열) 1)")
    card = copy.deepcopy(next(c for c in build_cards(q, build_pool(q, layer), layer) if c["name"] == M2H))
    rows = {r["table_id"]: r for r in card["tables"]}
    _, monthly = ref(card, stock="평잔", seasonal="계절조정", lacks="Y")
    _, keeper = ref(card, stock="평잔", seasonal="계절조정", has="Y")
    _, other = ref(card, stock="말잔", seasonal="계절조정", has="Y")  # holds no "M2(평잔 계절조정계열)" value
    rows[other].update(basis=dict(rows[keeper]["basis"]), title=rows[keeper]["title"])
    lk = CH.Cards([card], selection_catalog(layer).views)
    evidence = {"items": ["M2(평잔 계절조정계열) 1)"], "invalid_items": []}
    got = CH._frequency_support(M2H, monthly, "Y", evidence, lk)
    by = {c["table"]: c for c in got["candidates"]}
    assert by[keeper]["items_kept"] == "cited_values_held" and by[other]["items_kept"] == "content_unverified"
    assert got["status"] == "supported" and got["supported_by"] == [keeper]
    assert sorted(got["offering"]) == sorted([keeper, other])


def test_another_open_axis_is_still_asked_when_one_axis_has_a_single_annual_value(layer):
    q = "2024년 연간 국내총생산에 대한 지출을 보여줘"

    def price_ask(card):
        return ("price", [(v, ref(card, price=v, seasonal="원계열")[0]) for v in ("명목", "실질")])

    _, d = go(q, GDP, lambda card: output(GDP, (), "2024년", "연간", asks=[price_ask(card)]), layer)
    assert d["outcome"] == "ask" and codes(d) == [] and "seasonal" in d["unasked_axes"]
    assert {r["value"]: r["supported"] for r in d["ask_frequency_checks"]} == {"명목": True, "실질": True}
    # the same question with the seasonal axis also asked: that ask's 계절조정 option gives no annual table
    # (plan 56 order 8, ⑥ and ④ supplement, users 21:54: its annual values need an unconfirmed aggregation, so it is
    # unconfirmed and the statistic is held, never chosen. Before order 8: ask_invalid; order 8-1: auto_decided)
    _, d = go(q, GDP, lambda card: output(GDP, (), "2024년", "연간",
                                          asks=[price_ask(card), seasonal_ask(card, price="실질")]), layer)
    assert codes(d) == ["target_table_unconfirmed"] and "seasonal" not in d["basis"]
