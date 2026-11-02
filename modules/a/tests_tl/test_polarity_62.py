"""Plan 56 order 6-2 (bundle 5, users 2026-10-31 15:23 and 16:28 UTC): the model's basis polarity, read as written.

An answer of the polarity contract (``check.POLARITY_CONTRACTS``; 6-3 makes it the current one) carries a value and a
polarity on each splitter row. Code reads the rows, never the question's wording, and checks them against the request
and the card: required (every value), any_of (one is enough), excluded, allowed (no condition, never asked again),
undetermined (asked only through the model's own ask while the card still offers the other values), unreadable. A
correctable output error stops the statistic with ``basis_polarity_invalid`` ({cause, detail} and a recovery, not
available until order 9), never a question to the user. A basis word the code dictionary finds with no row is a
diagnosis only. An answer of an older contract (.3) keeps its reading (``search.basis``).

Parsed outputs go into ``check.check`` directly (the parser of the new contract comes in 6-3): the made-up answer is
parsed under the current contract, then its splitter rows get value and polarity and the parsed answer names the
polarity contract. Real catalog, no model, no network. Tables are found by card name and title basis, never by ID.
"""

from __future__ import annotations

import json

import pytest
from conftest import fill_option_evidence  # noqa: E402

from claview_a import check as CH
from claview_a import periods as PR
from claview_a.cards import build_cards, table_numbers
from claview_a.contract import parse_output
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog

AS_OF = "2026-10-22"
# the fixed inputs here are of contract .3 (plan 56 order 6-3b: contract .4 is the current one)
OLD = "tl-contract-2026-10-28.3"
M2 = "M2 상품별 구성내역"
POLAR = CH.POLARITY_CONTRACTS[0]


def question(words: str) -> str:
    return f"2024년 월별 M2 상품별 구성내역을 {words} 보여줘"


def ref(cards, layer, **basis) -> str:
    card = next(c for c in cards if c["name"] == M2)
    views = selection_catalog(layer).views
    rows = [r for r in card["tables"] if all(r["basis"].get(a) == [v] for a, v in basis.items())]
    rows.sort(key=lambda r: PR._end_key(views[r["table_id"]]), reverse=True)
    return f"{card['card_id']}.{table_numbers(card)[rows[0]['table_id']]}"


def row(quote, axis, value, polarity, sid="s1"):
    return {"quote": quote, "axis": axis, "value": value, "polarity": polarity, "for": [sid]}


def check(layer, q, rows, contract=POLAR, choose=None, asks=None):
    """``rows``: polarity rows; ``choose``: the chosen table's basis, or ``asks``: [(axis, [(value, basis)])]."""
    cards = build_cards(q, build_pool(q, layer), layer)
    out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                           "missing", "alternatives")}
    out["statistics"] = [{"id": "s1", "quote": "M2 상품별 구성내역", "name": "M2"}]
    out["periods"] = [{"quote": "2024년", "for": ["s1"]}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    out["splitters"] = [{"quote": r["quote"], "axis": r["axis"], "for": r["for"]} for r in rows]
    if choose is not None:
        out["choices"] = [{"for": ["s1"], "table": ref(cards, layer, **choose),
                           "evidence": [{"kind": "title", "text": M2}]}]
    for axis, options in asks or []:
        out["asks"].append({"for": "s1", "axis": axis, "options": [{"value": v, "table": ref(cards, layer, **b)}
                                                                   for v, b in options]})
    parsed = parse_output(json.dumps(fill_option_evidence(out, cards), ensure_ascii=False), cards, OLD)
    if contract != parsed["answer_contract"]:  # the polarity rows as the new contract carries them
        parsed["output"]["splitters"] = [dict(r) for r in rows]
        parsed["answer_contract"] = contract
    record = CH.check(q, cards, parsed, selection_catalog(layer).views, AS_OF)
    return record, record["decisions"][0]


def codes(d):
    return [r["code"] for r in d["reasons"]]


def invalid(d):
    (r,) = [r for r in d["reasons"] if r["code"] == "basis_polarity_invalid"]
    assert r["detail"]["recovery"] == {"status": "not_available", "slot": "a_self_rejudge"}
    return r["detail"]["cause"]


STOCK = row("말잔", "stock", "말잔", "required")
BOTH_SEASONAL = (row("원계열과 계절조정 모두", "seasonal", "원계열", "required"),
                 row("원계열과 계절조정 모두", "seasonal", "계절조정", "required"))


# ---------------------------------------------------------------- the polarity is used as written


def test_a_required_value_the_table_gives_passes(layer):
    _, d = check(layer, question("말잔 원계열로"), [STOCK, row("원계열", "seasonal", "원계열", "required")],
                 choose={"stock": "말잔", "seasonal": "원계열"})
    assert d["outcome"] == "selected" and codes(d) == []
    assert d["basis"]["seasonal"]["value"] == ["원계열"] and d["basis"]["seasonal"]["source"] == "splitter"


def test_both_required_values_no_table_gives_is_unsupported_never_reduced(layer):
    _, d = check(layer, question("말잔 원계열과 계절조정 모두"), [STOCK, *BOTH_SEASONAL],
                 choose={"stock": "말잔", "seasonal": "원계열"})
    assert d["outcome"] == "failed" and codes(d) == ["basis_combination_unsupported"]
    assert d["reasons"][0]["detail"]["required"] == ["계절조정", "원계열"] and d["final_tables"] == []


def test_both_required_values_are_met_only_by_a_table_giving_both():
    assert CH._meets(("원계열", "계절조정"), {"values": {"원계열", "계절조정"}, "mode": "all"})
    assert not CH._meets(("원계열",), {"values": {"원계열", "계절조정"}, "mode": "all"})  # one is not success
    assert CH._meets(("원계열",), {"values": {"원계열", "계절조정"}, "mode": "any"})
    assert not CH._meets(("평잔",), {"values": {"원계열", "계절조정"}, "mode": "any"})


@pytest.mark.parametrize("chosen", ["원계열", "계절조정"])
def test_one_of_two_values_is_enough_and_never_both_or_none(layer, chosen):
    rows = [STOCK, row("원계열이나 계절조정", "seasonal", "원계열", "any_of"),
            row("원계열이나 계절조정", "seasonal", "계절조정", "any_of")]
    _, d = check(layer, question("말잔 원계열이나 계절조정으로"), rows, choose={"stock": "말잔", "seasonal": chosen})
    assert d["outcome"] == "selected" and codes(d) == [] and d["basis"]["seasonal"]["value"] == [chosen]
    st = CH.stated("", {}, "s1", "seasonal", rows, polar=True)
    assert (st["mode"], st["values"], st["excluded"]) == ("any", {"원계열", "계절조정"}, set())


@pytest.mark.parametrize("chosen, ok", [("원계열", True), ("계절조정", False)])
def test_an_excluded_value_conflicts_and_is_never_switched(layer, chosen, ok):
    rows = [STOCK, row("계절조정은 빼고", "seasonal", "계절조정", "excluded")]
    _, d = check(layer, question("말잔으로 계절조정은 빼고"), rows, choose={"stock": "말잔", "seasonal": chosen})
    assert (d["outcome"] == "selected") is ok
    assert codes(d) == ([] if ok else ["stated_basis_conflict"])


@pytest.mark.parametrize("chosen", ["원계열", "계절조정"])
def test_an_allowed_value_makes_no_condition_and_is_not_asked(layer, chosen):
    rows = [STOCK, row("계절조정은 없어도 괜찮아", "seasonal", "계절조정", "allowed")]
    _, d = check(layer, question("말잔으로 계절조정은 없어도 괜찮아"), rows,
                 choose={"stock": "말잔", "seasonal": chosen})
    assert d["outcome"] == "selected" and codes(d) == [] and d["asks"] == []
    assert d["basis"]["seasonal"]["value"] == [chosen] and d["basis"]["seasonal"]["source"] != "splitter"


def test_an_undetermined_axis_is_asked_through_the_models_own_ask(layer):
    rows = [STOCK, row("계열은 아직 모르겠어", "seasonal", "원계열", "undetermined")]
    _, d = check(layer, question("말잔으로 계열은 아직 모르겠어"), rows,
                 asks=[("seasonal", [("원계열", {"stock": "말잔", "seasonal": "원계열"}),
                                     ("계절조정", {"stock": "말잔", "seasonal": "계절조정"})])])
    assert d["outcome"] == "ask" and [a["axis"] for a in d["asks"]] == ["seasonal"] and codes(d) == []


# ---------------------------------------------------------------- correctable output errors: never asked


@pytest.mark.parametrize("rows, words, cause", [
    ([STOCK, row("원계열만", "seasonal", "원계열", "required")], "말잔 원계열로", "quote_not_in_question"),
    ([STOCK, row("원계열", "seasonal", "실질", "required")], "말잔 원계열로", "value_not_in_axis"),
    ([STOCK, row("원계열", "seasonal", "원계열", "required"), row("원계열", "seasonal", "원계열", "excluded")],
     "말잔 원계열로", "polarity_conflict"),
    ([STOCK, row("원계열", "seasonal", "원계열", "required"), row("계절조정", "seasonal", "계절조정", "any_of")],
     "말잔 원계열 계절조정으로", "polarity_conflict"),
    ([STOCK, row("계열은 아직 모르겠어", "seasonal", "원계열", "undetermined")], "말잔으로 계열은 아직 모르겠어",
     "undetermined_without_ask"),
    ([STOCK, row("원계열이라던가", "seasonal", "원계열", "unreadable")], "말잔 원계열이라던가", "unreadable_row"),
])
def test_an_output_error_stops_the_statistic_with_its_cause_and_no_recovery_yet(layer, rows, words, cause):
    _, d = check(layer, question(words), rows, choose={"stock": "말잔", "seasonal": "원계열"})
    assert d["outcome"] == "failed" and codes(d) == ["basis_polarity_invalid"] and invalid(d) == cause
    assert d["asks"] == [] and d["final_tables"] == []


def test_an_ask_on_an_allowed_axis_is_an_output_error(layer):
    rows = [STOCK, row("계절조정은 없어도 괜찮아", "seasonal", "계절조정", "allowed")]
    _, d = check(layer, question("말잔으로 계절조정은 없어도 괜찮아"), rows,
                 asks=[("seasonal", [("원계열", {"stock": "말잔", "seasonal": "원계열"}),
                                     ("계절조정", {"stock": "말잔", "seasonal": "계절조정"})])])
    assert d["outcome"] == "failed" and invalid(d) == "ask_on_allowed_axis" and d["final_tables"] == []


# ---------------------------------------------------------------- diagnosis only, and the older contract


def test_a_basis_word_without_a_row_is_a_diagnosis_only(layer):
    q = question("말잔 원계열로")
    _, with_row = check(layer, q, [STOCK, row("원계열", "seasonal", "원계열", "required")],
                        choose={"stock": "말잔", "seasonal": "원계열"})
    _, d = check(layer, q, [STOCK], choose={"stock": "말잔", "seasonal": "원계열"})
    assert d["outcome"] == with_row["outcome"] == "selected" and codes(d) == []
    assert [x["axis"] for x in d["diagnostics"] if x.get("stage") == "basis_value_without_row"] == ["seasonal"]


def test_an_older_contract_keeps_the_current_reading(layer):
    """The same rows under contract .3: value and polarity are not read (an unreadable row stops nothing)."""
    rows = [STOCK, row("원계열이라던가", "seasonal", "원계열", "unreadable")]
    _, d = check(layer, question("말잔 원계열이라던가"), rows, contract=OLD,
                 choose={"stock": "말잔", "seasonal": "원계열"})
    assert "basis_polarity_invalid" not in codes(d)


# ---------------------------------------------------------------- the counterexample sets read as the model writes them


def _counterexamples():
    """The plan 55 and 55b reading cases whose meaning the tests fix (required / excluded values, nothing left
    unread), and the 15:23 counterexamples with their agreed polarity."""
    import inspect

    import test_basis_negation_55 as A
    import test_basis_negation_55b as B

    from claview_a.conditions.words import BASIS_AXES

    axis_of = {v: a for a, vals in BASIS_AXES.items() for v in vals}
    out = []
    for mod in (A, B):
        for _, f in inspect.getmembers(mod, inspect.isfunction):
            for m in getattr(f, "pytestmark", []):
                names = [x.strip() for x in m.args[0].split(",")] if m.name == "parametrize" else []
                if "text" not in names or not {"required", "excluded"} & set(names):
                    continue
                for values in m.args[1]:
                    p = dict(zip(names, values))
                    if p.get("unresolved"):
                        continue
                    rows = [(axis_of[v], v, "required") for v in p.get("required", [])] + \
                           [(axis_of[v], v, "excluded") for v in p.get("excluded", [])]
                    if rows:
                        out.append((p["text"], rows))
    return out + [
        ("계절조정은 없어도 괜찮아", [("seasonal", "계절조정", "allowed")]),
        ("계절조정은 넣어도 되고 빼도 돼", [("seasonal", "계절조정", "allowed")]),
        ("계절조정은 제외하지 않아도 돼", [("seasonal", "계절조정", "allowed")]),
        ("원계열뿐 아니라 계절조정도", [("seasonal", "원계열", "required"), ("seasonal", "계절조정", "required")]),
        ("원화나 달러화로", [("currency", "원화", "any_of"), ("currency", "달러", "any_of")]),
        ("계절조정 없이는 안 돼", [("seasonal", "계절조정", "required")]),
    ]


@pytest.mark.parametrize("text, rows", _counterexamples(), ids=lambda x: x if isinstance(x, str) else "")
def test_a_counterexample_written_with_its_polarity_is_kept_as_written(text, rows):
    splitters = [row(text, axis, value, pol) for axis, value, pol in rows]
    for axis in {a for a, _, _ in rows}:
        mine = [(v, p) for a, v, p in rows if a == axis]
        st = CH.stated("", {}, "s1", axis, splitters, polar=True)
        assert st["excluded"] == {v for v, p in mine if p == "excluded"}
        wanted = {v for v, p in mine if p in ("required", "any_of")}
        assert st["values"] == wanted
        assert st["mode"] == ("all" if any(p == "required" for _, p in mine) else "any" if wanted else None)
        assert st["allowed"] is any(p == "allowed" for _, p in mine)
