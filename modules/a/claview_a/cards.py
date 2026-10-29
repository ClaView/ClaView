"""Candidate cards (plan 37, step 1): one card per sibling family of the pool.

Per table: its ID, name, bracket parts, frequencies with their periods, and its **table-level basis** read from the
title's bracket parts only (``title_basis``). Brackets are read by depth (``title_parts``): an outer bracket keeps its
nested words ("지역별 기업경기조사(실적(2013.1~))" -> name "지역별 기업경기조사", bracket "실적(2013.1~)"), and the
same reading gives the family name, the shown brackets and the basis. Basis words found in value names (``item_basis``) are reference only,
with the names they came from: a value name is no table-level fact ("도시가스" holds 시가, "원화수입수수료" holds
원화), so they never make a split, an ask or a choice. Axes the current path reads from titles only
(``evidence.TITLE_ONLY_AXES``) are not read from values at all. This follows the current path's evidence rule (title >
path > values; a value hit is B's row choice: ``select.evidence.fit_basis``).

Per card: the basis axes on which two or more siblings carry different bracket values (siblings with no bracket value
on the axis are "unknown", not another value), frequency/edition candidate groups (``editions``: siblings that
appear to differ in frequency, period or source only, every pair checked with ``dimension_kinds`` group by group, kinds
kept per group; same content is not claimed, whether the value names are the same is recorded apart),
other bracket words, and every value whose name holds a question word, each value with the tables that hold it (no
cap per word, nothing left as a count only: user 2026-10-28 05:05 UTC, measured on 240 and 40 questions, every input
under the 24,000-character limit; a question over it is refused by ``contract.build_payload``, never cut).

Per table also its catalog axes (``axes``; user 2026-10-29 10:40 UTC: a breakdown the catalog has was not shown
when no question word matched its values, Q004/Q058): every axis by its catalog name and value count, the values of an
axis with at most ``AXIS_LIST_MAX`` of them listed whole as the catalog writes them, a larger axis by name and count
only. Axis names and values are never guessed or renamed; choosing values inside a table stays the next step's job.

``card_facts`` gives the texts a model may cite for a table: its name, the family name, its bracket parts and words,
the matched values and the listed axis values that table holds. What is shown but not citable (periods, split lines,
axis names and counts) is display only.

Rendering (users 2026-10-27 20:42 and 20:45 UTC): each table of a card is written once with a card-local number
("t1 <table ID> | ..."); split and matched-value lines use the numbers, "전체" for every table of the card; a value
name is shown once, values with the same tables share a line; the notation is ``CARD_LEGEND``, given once before all
cards. Card data and ``card_facts`` keep the real IDs; ``refs`` maps "<card>.<number>" (g3.t1) back to its table.

Nothing here names a table, case or question.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

from .search.basis import values as basis_values
from .search.words import TOKEN_TAIL, WORD_RE
from .search.families import SIGNATURE_AXES, bracket_words, dimension_kinds
from .search.words import STOP_TERMS, TITLE_ONLY_AXES
from .search.tables import nospace, selection_catalog

CARDS_VERSION = "tl-cards-2026-10-29.11"  # .11: catalog axes per table, small axes listed whole (user 10:40 UTC)
AXIS_LIST_MAX = 12  # an axis with at most this many values is listed whole; a larger one by name and count
ITEM_SOURCES = 2  # value names kept as the source of a value-level basis word
ASK_AXES_PATH = Path(__file__).resolve().parent / "data" / "ask_axes.json"
ASK = json.loads(ASK_AXES_PATH.read_text(encoding="utf-8"))
ASK_AXES: dict[str, str] = ASK["ask_axes"]
CODE_KINDS: tuple[str, ...] = tuple(ASK["code_decided_kinds"])
VALUE_FORMS: dict[str, dict[str, list[str]]] = ASK["value_forms"]["forms"]
_LATIN = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"\d")
_QUARTER = re.compile(r"(\d{4})\s+(\d)/4")
_MONTH = re.compile(r"(\d{4})\.(\d{1,2})")
_YEAR = re.compile(r"(\d{4})")


def question_words(question: str) -> list[str]:
    """Words of two or more letters, particles cut (the selector's rule), function words and number words left out.
    A word with digits is kept only when it also holds a Latin letter (M2, not 2024년)."""
    out: list[str] = []
    for m in WORD_RE.finditer(question or ""):
        word = m.group(0)
        if _DIGIT.search(word) and not _LATIN.search(word):
            continue
        cut = TOKEN_TAIL.sub("", word)
        word = word if len(cut) < 2 <= len(word) else cut
        if len(word) >= 2 and word not in STOP_TERMS and word not in out:
            out.append(word)
    return out


def title_parts(title: str) -> tuple[str, list[str]]:
    """(name, outer bracket contents) of a table title, counting bracket depth, so that a nested bracket keeps its
    outer words: "지역별 기업경기조사(실적(2013.1~))" -> ("지역별 기업경기조사", ["실적(2013.1~)"]). An unclosed
    bracket stays part of the name."""
    name, parts, depth, start = [], [], 0, 0
    text = title or ""
    for i, ch in enumerate(text):
        if ch == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0:
                parts.append(text[start:i])
        elif depth == 0:
            name.append(ch)
    if depth:  # unclosed: the rest is name text
        name.append(text[start - 1:])
    return re.sub(r"\s+", " ", "".join(name)).strip(" ,"), [p.strip() for p in parts]


def flat_title(title: str) -> str:
    """The title with each outer bracket's inner brackets opened into comma-separated words, for the current path's
    one-level bracket readers (``bracket_words``): "X(실적(2013.1~))" -> "X(실적, 2013.1~)"."""
    name, parts = title_parts(title)
    flat = [re.sub(r"\s*,\s*(,\s*)+", ", ", re.sub(r"[()]", ", ", p)).strip(" ,") for p in parts]
    return name + "".join(f"({f})" for f in flat)


def family_name(title: str) -> str:
    return title_parts(title)[0]


# ---------------------------------------------------------------- basis


def title_basis(view) -> dict[str, list[str]]:
    """Basis values of the table read from the bracket parts of its title only (axis -> values)."""
    brackets = " ".join(title_parts(view.title)[1])
    out = {}
    for axis in SIGNATURE_AXES:
        got = basis_values(brackets, axis)
        if got:
            out[axis] = sorted(got)
    return out


def item_basis(view) -> dict[str, dict]:
    """Reference only: basis values found in value names (axis -> {values, sources}); title-only axes left out."""
    out: dict[str, dict] = {}
    for axis in SIGNATURE_AXES:
        if axis in TITLE_ONLY_AXES:
            continue
        found: dict[str, list[str]] = {}
        for it in getattr(view, "items", []):
            for value in basis_values(it.name_raw or "", axis):
                found.setdefault(value, [])
                if len(found[value]) < ITEM_SOURCES and it.name_raw not in found[value]:
                    found[value].append(it.name_raw)
        if found:
            out[axis] = {"values": sorted(found), "sources": {v: found[v] for v in sorted(found)}}
    return out


def _table_row(view) -> dict:
    return {"table_id": view.table_id, "title": view.title, "brackets": title_parts(view.title)[1],
            "bracket_words": sorted(bracket_words(flat_title(view.title), merge_basis=True)),
            "periods": [{"frequency": f, "start": s, "end": e} for f, s, e in getattr(view, "periods", [])],
            "basis": title_basis(view), "item_basis": item_basis(view)}


def _splits(rows: list[dict]) -> list[dict]:
    """Axes on which two or more tables carry different bracket values; tables without one are ``unknown``."""
    out = []
    for axis in SIGNATURE_AXES:
        values = {r["table_id"]: r["basis"][axis] for r in rows if axis in r["basis"]}
        if len({tuple(v) for v in values.values()}) < 2:
            continue
        out.append({"axis": axis, "label": ASK_AXES.get(axis), "ask_axis": axis in ASK_AXES, "values": values,
                    "unknown": [r["table_id"] for r in rows if axis not in r["basis"]]})
    return out


# ---------------------------------------------------------------- editions


def _edition_key(view) -> tuple:
    """Candidate group: the same bracket basis values and bracket words (frequency and period words left out)."""
    basis = tuple(sorted((a, tuple(v)) for a, v in title_basis(view).items()))
    words = tuple(sorted(w for w, k in bracket_words(flat_title(view.title), merge_basis=True).items()
                         if k not in ("frequency", "period")))
    return basis, words


def _item_names(view) -> frozenset:
    return frozenset(nospace(it.name_raw) for it in getattr(view, "items", []) if it.name_raw)


def _editions(views: list) -> list[dict]:
    """Frequency/edition candidate groups (not "same content"): a candidate group is kept only when every pair in it
    differs in code-decided kinds only (``dimension_kinds`` ⊆ frequency, period, source); each group carries its own
    kinds. ``dimension_kinds`` can report a frequency alone when value names differ too, so whether the members hold
    the same values is recorded apart (``items``: "same" when every pair has the same value names, else "differ") and
    never decided here: code picks the final table later from the requested values, period and frequency."""
    groups: dict[tuple, list] = {}
    for v in views:
        groups.setdefault(_edition_key(v), []).append(v)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        kinds: set[str] = set()
        ok = True
        for a, b in itertools.combinations(members, 2):
            got = set(dimension_kinds(a, b))
            if not got <= set(CODE_KINDS):
                ok = False
                break
            kinds |= got
        if ok:
            same = all(_item_names(a) == _item_names(b) for a, b in itertools.combinations(members, 2))
            out.append({"tables": [v.table_id for v in members], "kinds": [k for k in CODE_KINDS if k in kinds],
                        "items": "same" if same else "differ"})
    return out


def _period_key(text: str | None) -> tuple[int, int]:
    """Sortable end of a period: (year, month); a quarter ends in its third month, a year in month 12."""
    text = text or ""
    if m := _QUARTER.search(text):
        return int(m.group(1)), int(m.group(2)) * 3
    if m := _MONTH.search(text):
        return int(m.group(1)), int(m.group(2))
    if m := _YEAR.search(text):
        return int(m.group(1)), 12
    return 0, 0


def newest(rows: list[dict]) -> dict:
    """The edition shown in full: the latest end over its frequencies (then the later one in catalog order)."""
    def key(pair):
        i, row = pair
        return max((_period_key(p["end"]) for p in row["periods"]), default=(0, 0)), i

    return max(enumerate(rows), key=key)[1]


# ---------------------------------------------------------------- matched values


def _rest_rank(name: str, key: str, question_key: str) -> int:
    """Order of a value name among the names of one question word: 0 the name is the word, 1 what the name adds to
    the word is also written in the question (as '수지' in '운송수지' for "가공·운송·여행·건설수지"), 2 it adds words
    the question does not have (a narrower or other value, as '기타운송수입'). Not the length of the name."""
    rest = nospace(name).replace(key, "", 1)
    return 0 if not rest else 1 if rest in question_key else 2


def _context(view, item) -> tuple[str | None, str | None]:
    """(parent name, unit) of a value as the catalog gives them; None where the catalog gives nothing (never
    guessed)."""
    parent_id = getattr(item, "parent_id", None)
    parents = {getattr(i, "item_id", None): i for i in view.items} if parent_id else {}
    parent = parents.get(parent_id)
    unit = (getattr(item, "unit", None) or "").strip() or None
    return (parent.name_raw if parent is not None and parent.name_raw else None), unit


def _matched(views: list, words: list[str], question: str = "") -> list[dict]:
    """Per question word: every value that holds it, each (name, parent, unit) once with all the tables that hold
    it, in ``_rest_rank`` order, then catalog order (a fixed order; plan 37 stage 1, CV-AB-05: '운송수지' was matched
    but hidden behind '운송수입' and others while a cap of three names per word stood). The parent and unit come from
    the catalog (user 2026-10-28 06:37 UTC, CV-AB-02: two '총고정자본형성' values of one table, under '지출항목별
    증감률(실질)' and under '지출구조(…)', looked the same); the citable text stays the name."""
    out = []
    question_key = nospace(question)
    for word in words:
        key = nospace(word)
        holders: dict[tuple, list[str]] = {}
        ids: dict[tuple, dict[str, list[str]]] = {}
        for v in views:
            for it in v.items:
                if it.name_raw and key in nospace(it.name_raw):
                    k = (it.name_raw, *_context(v, it))
                    tables = holders.setdefault(k, [])
                    if v.table_id not in tables:
                        tables.append(v.table_id)
                    ids.setdefault(k, {}).setdefault(v.table_id, []).append(getattr(it, "item_id", None))
        if not holders:
            continue
        keys = sorted(holders, key=lambda k: _rest_rank(k[0], key, question_key))  # stable: catalog order in a rank
        out.append({"word": word, "items": [{"name": k[0], "parent": k[1], "unit": k[2], "tables": holders[k],
                                             "item_ids": ids[k]} for k in keys]})
    return out


def _axes(views: list) -> tuple[dict, list[dict]]:
    """(per table: its catalog axes in catalog order, [{axis, count, listed, values}]; the listed values). A value
    shows its name, with its catalog parent and unit (``shown_value``) only where the name repeats on that axis.
    Each listed value is kept once per (name, parent, unit) with the tables that hold it and their item IDs, like a
    matched value."""
    per_table: dict[str, list[dict]] = {}
    holders: dict[tuple, list[str]] = {}
    ids: dict[tuple, dict[str, list[str]]] = {}
    for v in views:
        rows = []
        for obj, name in getattr(v, "dims", None) or []:
            items = [it for it in v.items if getattr(it, "obj_id", None) == obj and it.name_raw]
            row = {"axis": name, "count": len(items), "listed": len(items) <= AXIS_LIST_MAX, "values": []}
            if row["listed"]:
                names = [it.name_raw for it in items]
                for it in items:
                    parent, unit = _context(v, it)
                    k = (it.name_raw, parent, unit)
                    entry = {"name": it.name_raw, "parent": parent, "unit": unit}
                    row["values"].append(shown_value(entry) if names.count(it.name_raw) > 1 else it.name_raw)
                    tables = holders.setdefault(k, [])
                    if v.table_id not in tables:
                        tables.append(v.table_id)
                    ids.setdefault(k, {}).setdefault(v.table_id, []).append(getattr(it, "item_id", None))
            rows.append(row)
        per_table[v.table_id] = rows
    items = [{"name": k[0], "parent": k[1], "unit": k[2], "tables": holders[k], "item_ids": ids[k]} for k in holders]
    return per_table, items


def shown_value(it: dict) -> str:
    """The value as the card writes it: the name, then the catalog parent and unit when there are any."""
    extra = [x for x in ((f"상위 {it['parent']}" if it.get("parent") else None),
                         (f"단위 {it['unit']}" if it.get("unit") else None)) if x]
    return it["name"] + (f" ‹{' / '.join(extra)}›" if extra else "")


def _other_words(rows: list[dict]) -> list[str]:
    """Bracket words outside basis, frequency, period and source words that differ between siblings."""
    words = [bracket_words(flat_title(r["title"]), merge_basis=True) for r in rows]
    every = set.intersection(*(set(w) for w in words)) if words else set()
    return sorted({w for ws in words for w, k in ws.items() if k == "other" and w not in every})


# ---------------------------------------------------------------- cards


def build_cards(question: str, pool: dict, layer) -> list[dict]:
    views = selection_catalog(layer).views
    words = question_words(question)
    cards = []
    for n, entry in enumerate(pool["families"], start=1):
        vs = [views[t] for t in entry["members"]]
        rows = [_table_row(v) for v in vs]
        axes, axis_items = _axes(vs)
        cards.append({"card_id": f"g{n}", "family": entry["family"], "name": family_name(vs[0].title),
                      "source": entry.get("source", "question"), "tables": rows, "splits": _splits(rows),
                      "editions": _editions(vs), "other_words": _other_words(rows),
                      "matched": _matched(vs, words, question), "axes": axes, "axis_items": axis_items})
    return cards


def card_values(card: dict) -> list[dict]:
    """The values a card shows for its tables: the matched values, then the listed axis values."""
    return [it for m in card["matched"] for it in m["items"]] + list(card.get("axis_items") or [])


def card_facts(card: dict) -> dict[str, set[str]]:
    """Citable texts per table: its name, the family name, its bracket parts and bracket words, and the matched values
    and listed axis values that this table holds (never a sibling's), each by its name and by its text as shown with
    its catalog parent and unit (user 2026-10-28 06:42 UTC: the shown text tells apart two values of one name)."""
    out: dict[str, set[str]] = {}
    for row in card["tables"]:
        facts = {row["title"], card["name"], *row["brackets"], *row["bracket_words"]}
        facts |= {p.strip() for b in row["brackets"] for p in re.split(r"[,·]", b) if p.strip()}
        facts |= {t for it in card_values(card) if row["table_id"] in it["tables"] for t in (it["name"], shown_value(it))}
        out[row["table_id"]] = facts
    return out


CARD_LEGEND = "\n".join([
    "카드 읽는 법",
    "[gN] 이름: 이름이 같은 형제 표 묶음. 표는 카드 안 번호(t1, t2 …)로 적고, 카드 밖에서는 gN.tN으로 가리킨다.",
    "tN 표ID | 괄호 말 | 주기별 기간(M 월, Q 분기, Y 연) | 주기·판본 후보: 주기·기간·판본만 다른 것으로 보이는 표"
    "(같은 내용인지는 확인하지 않았고 항목 이름 같음/다름만 적는다. 이 관계나 항목 이름이 같다는 것만으로 서로 대체되는 "
    "표가 아니다. 대표로 하나를 고르면 최종 표는 코드가 요청 내용·기준·기간·주기를 확인해 고른다).",
    "갈림 축: 값=표 번호 … 표 괄호에서 읽은 기준이 표마다 다르다. ?=괄호에 그 기준이 없는 표(다른 값이라는 뜻이 아니다).",
    "다른 괄호 말: 기준 축이 아닌 괄호 말 중 표마다 다른 것.",
    "맞은 항목(질문 낱말): 표 번호: 항목 이름. 그 번호의 표가 가진 항목이다. 전체=카드의 모든 표. 맞은 항목은 모두 적었다. "
    "항목 이름 뒤 ‹ ›는 카탈로그에 적힌 그 항목의 상위 항목과 단위다(없으면 적지 않았다). item 근거에는 항목 이름을 적고, "
    "한 표에 같은 이름의 항목이 여럿이면 ‹ ›까지 카드에 적힌 그대로 적어 어느 항목인지 구분한다. "
    "괄호 안 질문 낱말 목록은 찾는 데 쓴 말이지 인용 근거가 아니다. item 근거 하나에는 그 표에 적힌 항목 이름 하나를 "
    "그대로 적는다.",
    f"항목 축 표 번호: 카탈로그의 분류 축 이름(값 개수): 값 이름 … / 다음 축. "
    f"값이 {AXIS_LIST_MAX}개 이하인 축은 값을 모두 적고, 더 많은 축은 이름과 개수만 적는다"
    "(그 축의 값은 맞은 항목에 나온 것만 보인다). 축 이름과 값은 카탈로그에 적힌 그대로라 "
    "질문의 말과 다를 수 있다. 적힌 값은 그 번호의 표가 가진 항목이며 item 근거로 쓸 수 있다. "
    "같은 축에 이름이 같은 값이 여럿이면 ‹ ›까지 적었다. 표 안에서 값을 고르는 일은 다음 단계 몫이다.",
])


def _periods_text(row: dict) -> str:
    return ", ".join(f"{p['frequency']} {p['start']}~{p['end']}" for p in row["periods"])


def table_numbers(card: dict) -> dict[str, str]:
    """Card-local table numbers in card order (table ID -> "t1", "t2", ...). Display only: card data and
    ``card_facts`` keep the real table IDs; a reference from outside the card names the card too (``refs``)."""
    return {row["table_id"]: f"t{i}" for i, row in enumerate(card["tables"], start=1)}


def refs(cards: list[dict]) -> dict[str, str]:
    """Every "<card>.<number>" reference of the cards (for example "g3.t1") -> the real table ID."""
    return {f"{c['card_id']}.{n}": t for c in cards for t, n in table_numbers(c).items()}


def _numbers(tables: list[str], num: dict[str, str], every: int) -> str:
    return "전체" if len(tables) == every else ",".join(num[t] for t in tables)


def render_card(card: dict) -> str:
    """Compact card (notation in ``CARD_LEGEND``, given once before all cards): one line per table "tN ID | brackets |
    periods", the newest table of an edition group naming its other members; one line per split axis (tables grouped
    by bracket value, ``?`` for unknown); matched values once per name, grouped by the tables that hold them."""
    lines = [f"[{card['card_id']}] {card['name']}"]
    num = table_numbers(card)
    every = len(card["tables"])
    rows = {r["table_id"]: r for r in card["tables"]}
    same: dict[str, tuple[list[str], str]] = {}
    for g in card["editions"]:
        main = newest([rows[t] for t in g["tables"]])["table_id"]
        same[main] = ([t for t in g["tables"] if t != main], g["items"])
    for row in card["tables"]:
        parts = [f"{num[row['table_id']]} {row['table_id']}", ", ".join(row["brackets"]) or "-", _periods_text(row)]
        if row["table_id"] in same:
            others, items = same[row["table_id"]]
            label = "항목 이름 같음" if items == "same" else "항목 이름 다름"
            parts.append(f"주기·판본 후보: {','.join(num[t] for t in others)} ({label})")
        lines.append(" | ".join(parts))
    for s in card["splits"]:
        by_value: dict[str, list[str]] = {}
        for t, vals in s["values"].items():
            by_value.setdefault("/".join(vals), []).append(t)
        text = " ".join(f"{v}={_numbers(ts, num, every)}" for v, ts in by_value.items())
        if s["unknown"]:
            text += f" ?={_numbers(s['unknown'], num, every)}"
        lines.append(f"갈림 {s['axis']}: {text}")
    if card["other_words"]:
        lines.append(f"다른 괄호 말: {', '.join(card['other_words'])}")
    if card["matched"]:
        groups: dict[tuple, list[str]] = {}
        seen: set[str] = set()
        for m in card["matched"]:
            for it in m["items"]:
                text = shown_value(it)
                if text not in seen:
                    seen.add(text)
                    groups.setdefault(tuple(it["tables"]), []).append(text)
        lines.append(f"맞은 항목({', '.join(m['word'] for m in card['matched'])})")
        lines += [f"{_numbers(list(ts), num, every)}: {', '.join(names)}" for ts, names in groups.items()]
    axes: dict[str, list[str]] = {}
    for row in card["tables"]:
        parts = [f"{a['axis']}({a['count']})" + (f": {', '.join(a['values'])}" if a["listed"] else "")
                 for a in (card.get("axes") or {}).get(row["table_id"], [])]
        if parts:
            axes.setdefault(" / ".join(parts), []).append(row["table_id"])
    # one line per group of tables with the same axes, marked "항목 축" so it never reads as a matched-value line
    lines += [f"항목 축 {_numbers(ts, num, every)}: {text}" for text, ts in axes.items()]
    return "\n".join(lines)


def render_cards(cards: list[dict]) -> str:
    return "\n".join(render_card(c) for c in cards)
