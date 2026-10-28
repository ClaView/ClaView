"""Checks, corrections and the final tables of one judgement (plan 37, step 3; user 2026-10-27 21:20 UTC, review
fixes 21:36 UTC).

Input: the question, the cards, the parsed model output (``contract.parse_output``), the table views and the fixed
request date. Output: one record that keeps apart the model's own output, the checks, the corrections code made and
the final tables, with one decision per statistic: ``selected``, ``ask``, ``not_in_pool`` or ``failed``. A parsed
answer is never taken as selected; ``alternatives`` are recorded and never used.

Roles (user 2026-10-27 22:20 UTC): the model judges meaning (which statistic, its indicator, institution and target,
which table, what to ask); code checks facts (structure, references, quotes, evidence on that table's card, stated
basis against the model's attribution, ask structure, periods and frequencies, edition shares, errors) and chooses
the final table among frequency, period and edition siblings. A semantically wrong choice that passes every fact check
is a model error, counted in evaluation, not blocked here.

One fact check for every table that may end a statistic (``verify_table``): the model's choice, every option of the
model's ask, a table code switched to (stated basis, unneeded ask), every option of an ask code adds, every period
candidate and every final table. It holds when the table is in the pool and holds every value the model cited as item
evidence (a table code switches to must keep them). Being the representative is no proof of content.

``support`` and the named-table comparison are **diagnostics** (``decision.diagnostics``): the word-level reading of
the statistic's quote (rules recorded word by word) and whether a card name written in the quote is the chosen card.
They are kept for evaluation and never pass or fail a statistic, never remove a candidate or an option, never change a
correction, the final table or the route.

Order, per statistic:
1. quotes: statistic, splitter, period, frequency and calculation quotes must be in the question (whitespace aside);
   a wrong calculation quote is a warning, the others fail the statistic;
2. the model's decision: a choice's evidence must be facts of that table's card of the stated kind (a spacing
   difference is allowed within that table and kind and recorded; two card texts equal but for spaces are ambiguous);
   an ask's options must resolve, sit in one card, be different tables carrying their values (a written value is
   mapped only by the axis's explicit forms in ``ask_axes.json``, and the mapped value is used from then on; their
   support is a diagnostic). A card evidence error (not on the card, another table's, another kind, ambiguous
   spacing; a search-word list cited as an item is one) is recorded under ``citation`` and the statistic goes on with
   the checks it can still do (users 2026-10-28 05:37 and 05:42 UTC): the outcome is then ``selected_unverified`` or
   ``ask_unverified``, routed ``partial``, never the verified handoff; code makes up no evidence and never edits the
   model's output; an invalid item evidence is never a kept value, and a table other than the model's that needs
   kept values gets ``retention_unverified`` when no valid item evidence is left: one verdict for candidates and the
   final recheck, never put in the period and edition competition (review 2026-10-28 05:58 UTC). A sibling check is
   confirmed, a confirmed mismatch, or not confirmed (``retention_unverified``); an axis with a value not confirmed is
   held (``held_axes``, reason ``basis_axis_held``): neither the representative's value taken nor an ask made up.
   The model's choice is checked as it is (user 2026-10-28 07:19 UTC): an axis the question leaves open takes the
   chosen table's value, recorded as ``model_choice`` (never as the user's basis), and code neither asks nor holds
   because other-value siblings exist; whether an ask was needed is scored from the model's output. The target-table
   principle applies only when a table is added or switched to: a model ask option, or a switch to the stated
   value's table, needs the model's own evidence for that table (ask option evidence, contract .3) checked as facts
   on its card (users 2026-10-28 06:43 and 06:47 UTC); the representative's evidence confirms nothing about another
   table, and code makes no correspondence by names, structure or units. A stated value against the chosen table
   with no such evidence is ``stated_basis_conflict``, never switched or passed; an ask option without it is
   ``target_table_unconfirmed``. Same-basis frequency, period and edition choices stay code's.
   Quotes of the question, references, basis conflicts and the other reasons stop the statistic as before.
   ``checks_done`` / ``checks_not_done`` list the stages run and not run;
3. basis per ask axis that splits the card, before periods. The values the question states for the statistic
   (``stated``): the model's splitters for that axis and statistic when their quote holds a value of the axis; else
   the question's values when the question has one statistic, or one value written before or after every statistic's
   quote; else ``basis_attribution_unresolved`` (stated but not attributable, never applied to every statistic). A
   representative of another value is switched to the sibling of the stated value (one meaning only) and checked
   again; an ask on a stated axis becomes the stated option and is checked again; an axis with no stated value is
   asked when every sibling value has a table that passes the fact check (the statistic held where the model cited
   values; a table-level statistic otherwise). A model ask on some axes gets code asks for the
   other open axes. No basis is assumed because one sibling covers the period;
4. periods (``periods.read_quotes``, ``periods.final_tables``) among the candidates of the decided representative
   that pass the check; a quote that cannot be read fails, never a default;
5. the final tables are checked again (``verify_table``, basis, shares) and the route is computed.

Values inside one table (currency, items) stay B's. Nothing here names a table, case or question.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .search.basis import occurrences as basis_occurrences
from .search.basis import values as basis_values
from .search.families import FREQUENCY_WORDS, SIGNATURE_AXES
from .search.tables import nospace

from . import periods as PR
from .cards import VALUE_FORMS, card_facts, question_words, shown_value
from .contract import CONTRACT_VERSION as K_CONTRACT

CHECK_VERSION = "tl-check-2026-10-28.14"
# ``*_unverified``: the table or ask is decided with the model's card evidence not verified (users 2026-10-28 05:37
# and 05:42 UTC); never the same state as a verified one, and routed as ``partial``, never ``handoff``
OUTCOMES = ("selected", "selected_unverified", "ask", "ask_unverified", "not_in_pool", "failed")
ROUTES = ("handoff", "ask", "partial", "fail")
# card evidence errors: recorded apart, the statistic goes on with the checks it can still do. Quotes of the question
# (statistic, splitter, period, frequency), references, basis conflicts and the other reasons still stop it.
CITATION_CODES = ("evidence_not_on_card", "evidence_of_other_table", "evidence_kind_mismatch",
                  "evidence_normalization_ambiguous")
STAGES = ("evidence", "model_table", "basis", "periods", "final_recheck")
SUPPORT_WORDS = json.loads((Path(__file__).resolve().parent / "data" / "support_words.json").read_text(encoding="utf-8"))
_CALC = [re.compile(p["regex"]) for p in json.loads(
    (Path(__file__).resolve().parent / "data" / "condition_words.json").read_text(encoding="utf-8"))["calc_hints"]["patterns"]]
_CW = json.loads((Path(__file__).resolve().parent / "data" / "condition_words.json").read_text(encoding="utf-8"))
_COMPARISON = [re.compile(p["regex"].replace("{than}", _CW["calc_than"])) for p in _CW["calc_patterns"]]
_LEXICON = json.loads((Path(__file__).resolve().parent / "data" / "lexicon.json").read_text(encoding="utf-8"))
_OMITTABLE = frozenset(_LEXICON["filler"]["words"]) | frozenset(_LEXICON["request"]["forms"]["noun"])
_REQUEST_FINAL = tuple(sorted({f for f in _LEXICON["request"]["forms"]["final"] if len(f) >= 2}, key=len, reverse=True))


def _request_word(word: str) -> bool:
    """A request predicate of the current path's lexicon ('보여줘', '비교해줘': the word ends with a final form)."""
    return any(word == f or (word.endswith(f) and len(word) > len(f)) for f in _REQUEST_FINAL)
_ENDINGS = sorted(_LEXICON["particles"]["endings"], key=len, reverse=True)  # longest first


# ---------------------------------------------------------------- card lookups


class Cards:
    def __init__(self, cards: list[dict], views: dict):
        self.cards = cards
        self.views = views
        self.card_of = {r["table_id"]: c for c in cards for r in c["tables"]}
        self.row = {r["table_id"]: r for c in cards for r in c["tables"]}
        self.facts = {t: f for c in cards for t, f in card_facts(c).items()}

    def facts_by_kind(self, table: str) -> dict[str, set[str]]:
        """The citable texts of ``table`` on its card, by kind (title / bracket / item)."""
        row, card = self.row[table], self.card_of[table]
        parts = {p.strip() for b in row["brackets"] for p in b.replace("·", ",").split(",") if p.strip()}
        return {"title": {row["title"], card["name"]},
                "bracket": set(row["brackets"]) | set(row["bracket_words"]) | parts,
                "item": {t for m in card["matched"] for it in m["items"] if table in it["tables"]
                         for t in (it["name"], shown_value(it))}}

    def resolve_item(self, table: str, text: str) -> dict:
        """The catalog values a cited card text names on ``table`` (user 2026-10-28 06:42 UTC): the shown text
        (name with parent and unit) names one value; a name names every value of that name on the table, and is
        specific only when they are one (``unique``), else ``name_only_several_values``."""
        card = self.card_of[table]
        entries = [it for m in card["matched"] for it in m["items"] if table in it["tables"]]
        by_shown = [it for it in entries if shown_value(it) == text]
        if by_shown:
            it = by_shown[0]
            return {"name": it["name"], "item_ids": list(it["item_ids"].get(table, [])), "resolution": "shown_text"}
        same = {(it["name"], it.get("parent"), it.get("unit")): it for it in entries if it["name"] == text}
        ids = [i for it in same.values() for i in it["item_ids"].get(table, [])]
        return {"name": text, "item_ids": ids,
                "resolution": "unique" if len(same) == 1 and len(ids) == 1 else "name_only_several_values"}

    def kinds_of(self, table: str, text: str) -> set[str]:
        """Kinds of fact ``text`` is for ``table`` (title / bracket / item), empty when it is none."""
        return {k for k, facts in self.facts_by_kind(table).items() if text in facts}

    def spaced(self, table: str, text: str) -> dict[str, list[str]]:
        """Kind -> the card texts of ``table`` that equal ``text`` once white space is removed (user 2026-10-28
        05:01 UTC: a spacing difference of a quote is allowed, within one table and one kind; nothing else of the
        text is removed or changed). More than one text in a kind is ambiguous."""
        key = nospace(text)
        if not key:
            return {}
        got = {k: sorted(f for f in facts if nospace(f) == key) for k, facts in self.facts_by_kind(table).items()}
        return {k: v for k, v in got.items() if v}

    def owners(self, text: str) -> list[str]:
        return [t for t in self.row if self.spaced(t, text)]

    def in_catalog(self, table: str, kind: str, text: str) -> bool:
        """Whether the catalog (not the card) gives ``table`` this text of this kind, spaces aside: a fact kept apart
        from what the card showed."""
        v, key = self.views[table], nospace(text)
        if kind == "item":
            return any(nospace(it.name_raw) == key for it in v.items)
        if kind == "title":
            return key in (nospace(v.title), nospace(self.card_of[table]["name"]))
        return key in nospace(v.title)


def option_value(axis: str, written: str) -> str | None:
    """The axis value an ask option names: the written value when no form lists it, else the value whose explicit
    forms (``ask_axes.json`` value_forms) hold it, spaces aside. None when two values claim it."""
    forms = VALUE_FORMS.get(axis, {})
    hit = [v for v, fs in forms.items() if nospace(written) in {nospace(f) for f in fs}]
    return written if not hit else hit[0] if len(hit) == 1 else None


def _in_question(quote: str, question: str) -> bool:
    return bool(quote.strip()) and nospace(quote) in nospace(question)


# ---------------------------------------------------------------- statistic support


def _places(view) -> list[tuple[str, str]]:
    return ([("title", nospace(view.title)), ("path", nospace(view.path or ""))]
            + [("item", nospace(it.name_raw or "")) for it in view.items]
            + [("axis", nospace(name)) for _, name in view.dims])


def _frequency_word(word: str) -> bool:
    w = nospace(word)
    return w in FREQUENCY_WORDS or (w.endswith("별") and w[:-1] in FREQUENCY_WORDS)


def _measure_form(view, forms: list[str]) -> str | None:
    texts = [view.title, view.path or "", *(it.name_raw or "" for it in view.items), *view.units()]
    return next((f for f in forms if any(f in (t or "") for t in texts)), None)


_CATALOG_TEXT: dict[int, str] = {}


def _catalog_text(views: dict) -> str:
    """Every place text of the catalog, once per views object (is a word a catalog term at all)."""
    if id(views) not in _CATALOG_TEXT:
        _CATALOG_TEXT[id(views)] = " ".join(h for t in sorted(views) for _, h in _places(views[t]))
    return _CATALOG_TEXT[id(views)]


def _on_table(key: str, places: list[tuple[str, str]]) -> str | None:
    return next((p for p, text in places if key and key in text), None)


def _correspond(word: str, view, places: list[tuple[str, str]]) -> dict | None:
    """A positive correspondence of one request word to the table, by a recorded rule, or None."""
    key = nospace(word)
    if (place := _on_table(key, places)):
        return {"rule": "on_table", "normalized": key, "place": place}
    for old, new in SUPPORT_WORDS["synonyms"].items():
        if old in key:
            norm = key.replace(old, new)
            if (place := _on_table(norm, places)):
                return {"rule": f"synonym:{old}->{new}", "normalized": norm, "place": place}
    cut = next((key[:-len(e)] for e in _ENDINGS if key.endswith(e) and len(key) - len(e) >= 2), None)
    if cut and (place := _on_table(cut, places)):
        return {"rule": "ending_cut", "normalized": cut, "place": place}
    for i in range(2, len(key) - 1):  # an attached compound: both parts on the table
        left, right = key[:i], key[i:]
        if (pl := _on_table(left, places)) and (pr := _on_table(right, places)):
            return {"rule": "compound_split", "normalized": f"{left}+{right}", "place": f"{pl}+{pr}"}
    stem = next((key[:-len(x)] for x in SUPPORT_WORDS["quantity_suffixes"]
                 if key.endswith(x) and len(key) - len(x) >= 2), None)
    if stem and (place := _on_table(stem, places)):
        return {"rule": "quantity_stem", "normalized": stem, "place": place}
    if word in SUPPORT_WORDS["total"]:
        top = next((it.name_raw for it in view.items if it.parent_id is None and it.name_raw), None)
        if top:
            return {"rule": "total_word", "normalized": key, "place": f"top value: {top}"}
    if word in SUPPORT_WORDS["measure"]:
        form = _measure_form(view, SUPPORT_WORDS["measure"][word])
        if form:
            return {"rule": "measure_word", "normalized": form, "place": f"form on table: {form}"}
    return None


def _normalized(word: str) -> str:
    key = nospace(word)
    for old, new in SUPPORT_WORDS["synonyms"].items():
        key = key.replace(old, new)
    return key


def support(quote: str, table: str, views: dict) -> dict:
    """Statistic support of one table (user 2026-10-27 21:57 UTC): positive evidence for the requested indicator,
    not every word. Each word of the statistic's quote is recorded with its role and rule:
    - left aside: comparison and calculation phrases (the current path's calc_patterns, "전년 동월 대비"), basis
      values (the basis check's), frequency words, rate and change words, the current path's request predicates,
      request nouns and filler nouns. Words in brackets are read like any other word;
    - met: on the table (name, brackets, path, value names, axis names) or by a recorded rule (synonym, ending cut,
      attached compound, quantity stem, total word with a top value, measure word with its form on the table);
    - not met: a catalog term absent from the table (``absent_catalog_term``: the table differs) or a word the
      catalog does not have at all (``unregistered``: decides nothing by itself).
    The indicator is the last word left (a total word gives way to the word before it). Verdict:
    ``mismatch`` when a catalog term is absent from the table (the indicator or a scope word written by the
    question), ``insufficient`` when the indicator is not met by any rule or no word is met, else ``supported``.
    Scope words alone (an institution, a borrower) never support the indicator."""
    view = views[table]
    places = _places(view)
    catalog = _catalog_text(views)
    rows = []
    text = quote
    for pattern in _COMPARISON:  # comparison and calculation phrases (the current path's calc_patterns)
        for m in pattern.finditer(text):
            rows.append({"word": m.group(0).strip(), "role": "aside", "rule": "comparison_or_calculation",
                         "normalized": nospace(m.group(0)), "place": None})
        text = pattern.sub(" ", text)
    for word in question_words(text):  # words in brackets are read like any other (no alias by form)
        key = nospace(word)
        if any(basis_values(word, a) for a in SIGNATURE_AXES):
            rows.append({"word": word, "role": "basis", "rule": "basis_value", "normalized": key, "place": None})
            continue
        if _frequency_word(word):
            rows.append({"word": word, "role": "aside", "rule": "frequency_word", "normalized": key, "place": None})
            continue
        if any(p.search(word) for p in _CALC):
            rows.append({"word": word, "role": "aside", "rule": "calculation_word", "normalized": key, "place": None})
            continue
        if (word in _OMITTABLE or _request_word(word)) and word not in SUPPORT_WORDS["total"] and word not in SUPPORT_WORDS["measure"]:
            rows.append({"word": word, "role": "aside", "rule": "request_or_filler", "normalized": key, "place": None})
            continue
        got = _correspond(word, view, places)
        if got:
            rows.append({"word": word, "role": "content", "met": True, **got})
        else:
            norm = _normalized(word)
            if word in SUPPORT_WORDS["total"] or word in SUPPORT_WORDS["measure"]:
                rule = "measure_or_total_unmet"  # context-dependent: never omitted, never a mismatch by itself
            else:
                rule = "absent_catalog_term" if norm in catalog else "unregistered"
            rows.append({"word": word, "role": "content", "met": False, "normalized": norm, "place": None,
                         "rule": rule})
    content = [r for r in rows if r["role"] == "content"]
    heads = [r for r in content if r["word"] not in SUPPORT_WORDS["total"]] or content
    head = heads[-1] if heads else None
    absent = [r["word"] for r in content if r["rule"] == "absent_catalog_term"]
    if absent:
        verdict = "mismatch"
    elif head is None or not head.get("met") or not any(r.get("met") for r in content):
        verdict = "insufficient"
    else:
        verdict = "supported"
    if head is not None:
        head["indicator"] = True
    return {"verdict": verdict, "words": rows, "missing": absent,
            "unverified": [r["word"] for r in content if not r.get("met") and r["rule"] != "absent_catalog_term"],
            "indicator": head["word"] if head else None}


def verify_table(quote: str, table: str | None, item_evidence: list[str], lk: Cards,
                 retention: dict | None = None) -> dict:
    """The fact check every table that may end a statistic goes through (user 2026-10-27 22:20 UTC: code checks facts,
    the model judges meaning): the table is in the pool and holds every value the model cited as item evidence (a
    table code switches to must keep them). ``diagnosis``: the word-level support of the statistic (``support``),
    recorded only; it never changes ``ok``."""
    if table is None or table not in lk.row:
        return {"ok": False, "code": "table_not_in_pool", "table": table}
    diagnosis = support(quote, table, lk.views)
    if retention and retention["unverified"] and not item_evidence and table != retention["origin"]:
        # a table other than the model's needs the cited values kept, and no cited value is valid: not confirmed,
        # never bypassed (user 2026-10-28 05:37 UTC)
        return {"ok": False, "code": "retention_unverified", "table": table, "diagnosis": diagnosis,
                "cited_values_held": [], "detail": {"unverified_item_evidence": list(retention["unverified"]),
                                                    "origin": retention["origin"]}}
    names = PR.item_names(lk.views[table])
    lost = [n for n in item_evidence if PR.item_key(n) not in names]
    held = [n for n in item_evidence if n not in lost]
    if lost:
        return {"ok": False, "code": "content_unverified", "table": table, "diagnosis": diagnosis,
                "cited_values_held": held, "detail": {"cited_values_missing": lost}}
    return {"ok": True, "code": None, "table": table, "diagnosis": diagnosis, "cited_values_held": held}


# ---------------------------------------------------------------- basis


def _basis(row: dict) -> dict[str, tuple]:
    return {a: tuple(v) for a, v in row["basis"].items()}


def _other_words_no_time(row: dict) -> tuple:
    from .search.families import bracket_words

    from .cards import flat_title

    return tuple(sorted(w for w, k in bracket_words(flat_title(row["title"]), merge_basis=True).items()
                        if k not in ("frequency", "period", "basis")))


def stated(question: str, stats: dict, sid: str, axis: str, splitters: list[dict]) -> dict:
    """{values, source, unresolved, ignored}: the values of ``axis`` the question states for statistic ``sid``
    (step-3 re-review, 2026-10-27 21:54 UTC: no common attribution from word positions):
    - the model's splitters on the axis for this statistic, when their quote holds a value of the axis (source
      ``splitter``; a splitter naming every statistic is the explicit common case);
    - none for this statistic, but splitters on the axis for other statistics that account for every value the
      question writes on the axis: the values belong to them, nothing is stated here (source ``other_statistics``);
    - no splitter on the axis at all: the question's value when the question has one statistic and one value
      (``question_one_statistic``);
    - otherwise a value written in the question cannot be attributed: ``unresolved`` (never applied to every
      statistic), which is told apart from no value written at all."""
    own = [s for s in splitters if s["axis"] == axis and sid in s["for"]]
    good = [s for s in own if basis_values(s["quote"], axis)]
    ignored = [s["quote"] for s in own if s not in good]
    if good:
        return {"values": set().union(*(basis_values(s["quote"], axis) for s in good)), "source": "splitter",
                "unresolved": False, "ignored": ignored}
    values = {o["value"] for o in basis_occurrences(question, axis)}
    if not values:
        return {"values": set(), "source": None, "unresolved": False, "ignored": ignored}
    others = [s for s in splitters if s["axis"] == axis and sid not in s["for"] and basis_values(s["quote"], axis)]
    if others and values <= set().union(*(basis_values(s["quote"], axis) for s in others)):
        return {"values": set(), "source": "other_statistics", "unresolved": False, "ignored": ignored}
    if len(stats) == 1 and len(values) == 1 and not others:
        return {"values": values, "source": "question_one_statistic", "unresolved": False, "ignored": ignored}
    return {"values": values, "source": None, "unresolved": True, "ignored": ignored}


def _siblings_on(card: dict, rows: dict, rep: str, axis: str, value: tuple | None, views: dict) -> list[str]:
    """Tables of the card with ``value`` on ``axis`` (None: any value) and the representative's other basis, newest
    first (the representative of a switch or an ask option; periods pick the final table later)."""
    base = {a: v for a, v in _basis(rows[rep]).items() if a != axis}
    out = []
    for r in card["tables"]:
        b = _basis(r)
        if {a: v for a, v in b.items() if a != axis} != base or axis not in b:
            continue
        if value is None or b[axis] == value:
            out.append(r["table_id"])
    return sorted(out, key=lambda t: PR._end_key(views[t]), reverse=True)


def _bracket_evidence(row: dict, axis: str) -> list[dict]:
    return [{"kind": "bracket", "text": p.strip()} for b in row["brackets"] for p in b.replace("·", ",").split(",")
            if p.strip() and basis_values(p, axis)]


# ---------------------------------------------------------------- periods (shared by choices and ask options)


def _trace(check_name: str, got: dict) -> dict:
    """One verification row: stage, table, result, failure code, the support rows and the cited values held."""
    sup = got.get("diagnosis") or {}
    return {"for": check_name, "table": got.get("table"), "ok": got["ok"], "code": got["code"],
            "diagnosis": {"verdict": sup.get("verdict"), "indicator": sup.get("indicator"), "words": sup.get("words"),
                          "missing_words": sup.get("missing"), "unverified_words": sup.get("unverified")},
            "cited_values_held": got.get("cited_values_held"), **({"detail": got["detail"]} if "detail" in got else {})}


def _select_periods(quote: str, rep: str, candidates: list[str], item_evidence: list[str], reading: dict,
                    lk: "Cards", as_of: str, stage: str, retention: dict | None = None) -> dict:
    """Usable candidates (the common check, ``verify_table``) and the final tables among them; every check is
    traced. ``reading``: ``periods.read_quotes`` of the statistic."""
    usable, why, trace = {}, {}, []
    rep_names = PR.item_names(lk.views[rep])
    for t in candidates:
        got = verify_table(quote, t, item_evidence, lk, retention)
        trace.append(_trace(f"{stage}:period_candidate", got))
        if not got["ok"]:  # retention_unverified included: never put in the period and edition competition
            why[t] = got["code"]
        elif t == rep:
            usable[t] = "representative_checked"
        elif item_evidence:
            usable[t] = "cited_values_held"
        elif PR.item_names(lk.views[t]) == rep_names:
            usable[t] = "same_value_names"
        else:
            why[t] = "value_names_differ"
    got = PR.final_tables(rep, candidates, usable, lk.views, reading["conditions"], as_of)
    return {"usable": usable, "not_usable": why, "trace": trace, **got}


def _candidates(rows: dict, rep: str) -> list[str]:
    return [t for t in rows if _basis(rows[t]) == _basis(rows[rep])
            and _other_words_no_time(rows[t]) == _other_words_no_time(rows[rep])]


def axis_items(items: list[str], axes) -> tuple[list[str], list[str]]:
    """(kept, bound): cited value names split for a check across ``axes``. A name that carries a value of one of the
    axes (as 'M2(평잔계절조정계열)' carries 평잔 and 계절조정) names the representative's value, not the statistic, so
    it is not required in a table of another value of that axis (user 2026-10-28 06:27 UTC). Nothing is removed
    from the name and no two names are made equal; the bound names are recorded."""
    bound = [n for n in items if any(basis_values(n, a) for a in axes)]
    return [n for n in items if n not in bound], bound


def _ask_feasibility(d: dict, quote: str, reading: dict, lk: "Cards", as_of: str) -> list[dict]:
    """Record only (user 2026-10-27 21:54 UTC): for each option of each ask, whether the tables of that option's value
    can cover the requested period and frequency. The option fixes its own axis; the decided basis stays; every other
    open axis is left free, and the result is given per combination of their values (conditional) with the axes
    still open. Status: covered / not_covered / conditional / unreadable. Nothing here removes an option, decides an
    axis or turns an ask into a choice."""
    out = []
    open_axes = [a["axis"] for a in d["asks"]]
    for ask in d["asks"]:
        axis = ask["axis"]
        others = [x for x in open_axes if x != axis]
        for opt in ask["options"]:
            table = opt["table_id"]
            row = {"axis": axis, "value": opt["value"], "option_table": table, "remaining_axes": others,
                   "readings": reading["readings"]}
            if reading["problems"]:
                out.append({**row, "status": "unreadable", "problems": reading["problems"], "groups": []})
                continue
            card = lk.card_of[table]
            rows = {r["table_id"]: r for r in card["tables"]}
            base = _basis(rows[table])
            fixed = {a: v for a, v in base.items() if a not in others}
            groups: dict[tuple, list[str]] = {}
            for t, r in rows.items():
                b = _basis(r)
                if any(b.get(a) != v for a, v in fixed.items()) or \
                        _other_words_no_time(r) != _other_words_no_time(rows[table]):
                    continue
                groups.setdefault(tuple(b.get(o) for o in others), []).append(t)
            results = []
            for key, members in groups.items():
                rep = sorted(members, key=lambda t: PR._end_key(lk.views[t]), reverse=True)[0]
                kept = axis_items(d["item_evidence"], open_axes)[0]  # record only: names bound to open axes skipped
                got = _select_periods(quote, rep, members, kept, reading, lk, as_of, "ask_option", d["retention"])
                results.append({"other_axes": {o: ("/".join(k) if k else None) for o, k in zip(others, key)},
                                "candidates": members, "status": got["status"], "reason": got["reason"],
                                "tables": got["tables"], "anchor": got["anchor"], "usable": got["usable"],
                                "not_usable": got["not_usable"], "trace": got["trace"]})
            done = [r["status"] == "selected" for r in results]
            status = "covered" if done and all(done) else "conditional" if any(done) else "not_covered"
            out.append({**row, "status": status, "groups": results})
    return out


# ---------------------------------------------------------------- main


def check(question: str, cards: list[dict], parsed: dict, views: dict, as_of: str) -> dict:
    out = parsed["output"]
    lk = Cards(cards, views)
    tables = parsed["tables"]
    stats = {s["id"]: s for s in out["statistics"]}
    splitters = out["splitters"]
    D = {sid: {"statistic": sid, "quote": s["quote"], "name": s["name"], "model_kind": None, "representative": None,
               "model_evidence": [], "item_evidence": [], "code_evidence": [], "basis": {}, "open_axes": [],
               "outcome": None, "final_tables": [], "asks": [], "corrections": [], "reasons": [], "warnings": [],
               "verification": [], "diagnostics": [], "period": None,
               "citation": {"status": "verified", "problems": []}, "invalid_item_evidence": [],
               "retention": {"origin": None, "unverified": []}, "model_representative": None, "held_axes": [],
               "axis_bound_items": {}, "item_refs": {}, "option_evidence": {}, "unasked_axes": [],
               "checks_done": [], "checks_not_done": []}
         for sid, s in stats.items()}
    checks: list[dict] = []

    def fail(sid: str, code: str, detail=None):
        D[sid]["reasons"].append({"code": code, "detail": detail})

    def cite(sid: str, code: str, detail=None):
        """A citation error: recorded, the statistic goes on (user 2026-10-28 05:37 UTC)."""
        D[sid]["citation"] = {"status": "unverified", "problems": D[sid]["citation"]["problems"] +
                              [{"code": code, "detail": detail}]}

    def check_evidence(sid: str, table: str, evidence: list[dict]) -> dict:
        """The model's evidence for ``table`` against its card (choices and, from contract .3, ask options): rows,
        the valid item names with the catalog values they name, and the invalid item texts; card evidence errors are
        citation errors (recorded, the statistic goes on)."""
        rows, items, refs_, invalid = [], [], {}, []
        for ev in evidence:
            kinds = lk.kinds_of(table, ev["text"])
            row = {"kind": ev["kind"], "text": ev["text"], "fact_of_table": sorted(kinds), "table": table}
            rows.append(row)
            card_text = ev["text"] if ev["kind"] in kinds else None
            spaced = lk.spaced(table, ev["text"]) if card_text is None else {}
            same_kind = spaced.get(ev["kind"], [])
            if card_text is None and len(same_kind) == 1:  # the same card text but for spaces, kept with the rule
                card_text = same_kind[0]
                row.update(fact_of_table=[ev["kind"]], normalized={"rule": "whitespace", "card_text": card_text})
            if card_text is not None:
                if ev["kind"] == "item":
                    ref = lk.resolve_item(table, card_text)
                    row["item"] = ref
                    items.append(ref["name"])
                    refs_[ref["name"]] = ref
                continue
            if ev["kind"] == "item":
                invalid.append(ev["text"])
            if len(same_kind) > 1:
                cite(sid, "evidence_normalization_ambiguous", {**row, "card_texts": same_kind})
                continue
            observed = {"chosen_table_in_catalog": lk.in_catalog(table, ev["kind"], ev["text"])}
            if spaced:
                cite(sid, "evidence_kind_mismatch", {**row, "fact_of_table": sorted(spaced), **observed})
            elif lk.owners(ev["text"]):
                # an observation, not a catalog fact: the chosen table's card does not show the text and other
                # tables' cards do; ``chosen_table_in_catalog`` says whether the chosen table holds it all the same
                cite(sid, "evidence_of_other_table", {**row, "owners": lk.owners(ev["text"]),
                                                      "observed": "not_on_chosen_table_card_shown_for_owners",
                                                      **observed})
            else:
                cite(sid, "evidence_not_on_card", {**row, **observed})
        return {"rows": rows, "items": items, "refs": refs_, "invalid_items": invalid,
                "valid": any(r["fact_of_table"] for r in rows)}

    def axis_verdict(sid: str, rep: str, table: str, axis: str) -> tuple[dict, list[dict]]:
        """The content check across ``axis`` (users 2026-10-28 06:27, 06:37, 06:42 and 06:43 UTC). Cited names
        without a value of the axis must be on ``table`` as before. A cited name bound to the axis is not asked of
        ``table``, and leaving it out confirms nothing: ``table`` is confirmed only by the model's own evidence for
        it (an ask option's evidence on its card, contract .3) checked as facts; with none, it is not confirmed
        (``target_table_unconfirmed``). Code makes no correspondence of its own (no name, structure or unit rule);
        no confirmed explicit correspondence is available to this path."""
        if table == rep:
            return verify_table(stats[sid]["quote"], table, D[sid]["item_evidence"], lk, D[sid]["retention"]), []
        kept, bound = axis_items(D[sid]["item_evidence"], [axis])
        model = D[sid]["option_evidence"].get(table)
        link = {"cited_on_representative": bound, "table": table, "axis": axis,
                "model_evidence": [r["text"] for r in model["rows"]] if model else []}
        if not model or not model["valid"]:
            # the representative's evidence (a title as much as a value) confirms nothing about another table
            # (user 2026-10-28 06:47 UTC): no model evidence for this table, no confirmation
            return {"ok": False, "code": "target_table_unconfirmed", "table": table,
                    "detail": {**link, "why": "no model evidence for this table"}}, [link]
        got = verify_table(stats[sid]["quote"], table, kept + model["items"], lk, D[sid]["retention"])
        return got, [{**link, "basis": "model_option_evidence"}]

    def verified(sid: str, table: str | None, why: str, items: list[str] | None = None) -> bool:
        got = verify_table(stats[sid]["quote"], table, D[sid]["item_evidence"] if items is None else items, lk,
                           D[sid]["retention"])
        D[sid]["verification"].append(_trace(why, got))
        if not got["ok"]:
            fail(sid, got["code"], {"for": why, "table": table, **({"detail": got["detail"]} if "detail" in got else {})})
        if got.get("diagnosis"):
            D[sid]["diagnostics"].append({"stage": why, "table": table, "support": got["diagnosis"]["verdict"],
                                          "indicator": got["diagnosis"]["indicator"],
                                          "missing_words": got["diagnosis"]["missing"],
                                          "unverified_words": got["diagnosis"]["unverified"]})
        return got["ok"]

    # 1. quotes
    for sid, s in stats.items():
        ok = _in_question(s["quote"], question)
        checks.append({"check": "statistic_quote", "statistic": sid, "ok": ok})
        if not ok:
            fail(sid, "quote_not_in_question", s["quote"])
    for key in ("splitters", "periods", "frequencies", "calculations"):
        for row in out[key]:
            ok = _in_question(row["quote"], question)
            checks.append({"check": f"{key}_quote", "statistics": row["for"], "quote": row["quote"], "ok": ok})
            if not ok:
                for sid in row["for"]:
                    if key == "calculations":
                        D[sid]["warnings"].append({"code": "calculation_quote_not_in_question", "detail": row["quote"]})
                    else:
                        fail(sid, "quote_not_in_question", row["quote"])
    family_names = [c["name"] for c in cards if len(nospace(c["name"])) >= 4]

    # 2a. choices
    for c in out["choices"]:
        table = tables.get(c["table"])
        for sid in c["for"]:
            d = D[sid]
            d["model_kind"] = "choice"
            d["representative"] = table
            if table is None or table not in lk.row:
                fail(sid, "table_not_in_pool", c["table"])
                continue
            d["retention"]["origin"] = d["model_representative"] = table
            got = check_evidence(sid, table, c["evidence"])
            d["model_evidence"] += got["rows"]
            d["item_evidence"] += got["items"]
            d["item_refs"].update(got["refs"])
            d["invalid_item_evidence"] += got["invalid_items"]  # never kept values; a switch then needs a valid one
            d["retention"]["unverified"] = list(d["invalid_item_evidence"])
            d["checks_done"].append("evidence")
            if not d["reasons"]:
                verified(sid, table, "model_choice")
                d["checks_done"].append("model_table")
            named = [n for n in family_names if nospace(n) in nospace(stats[sid]["quote"])]
            if named and lk.card_of[table]["name"] not in named and not any(
                    nospace(lk.card_of[table]["name"]) in nospace(n) for n in named):
                d["diagnostics"].append({"stage": "model_choice", "table": table, "named_table_mismatch":
                                         {"named": named, "chosen_card": lk.card_of[table]["name"]}})
    for m in out["missing"]:
        for sid in m["for"]:
            D[sid]["model_kind"] = "missing"
            D[sid]["search_words"] = list(m["search_words"])

    # 2b. model asks
    by_stat: dict[str, list[dict]] = {}
    for a in out["asks"]:
        D[a["for"]]["model_kind"] = "ask"
        by_stat.setdefault(a["for"], []).append(a)
    for sid, asks in by_stat.items():
        d = D[sid]
        for a in asks:
            written = [o["value"] for o in a["options"]]
            values = [option_value(a["axis"], w) for w in written]
            opts = [(v if v is not None else w, tables.get(o["table"]))
                    for v, w, o in zip(values, written, a["options"])]
            problems = [f"option_value_ambiguous:{w}" for v, w in zip(values, written) if v is None]
            if any(t is None or t not in lk.row for _, t in opts):
                problems.append("option_not_in_pool")
            else:
                if len({lk.card_of[t]["card_id"] for _, t in opts}) != 1:
                    problems.append("options_in_several_cards")
                if len({t for _, t in opts}) != len(opts):
                    problems.append("same_table_twice")
                for value, t in opts:
                    if value not in lk.row[t]["basis"].get(a["axis"], []):
                        problems.append(f"option_value_not_on_table:{value}")
                    got = verify_table(stats[sid]["quote"], t, [], lk)
                    d["verification"].append(_trace(f"model_ask_option:{a['axis']}:{value}", got))
                    d["diagnostics"].append({"stage": f"model_ask_option:{a['axis']}:{value}", "table": t,
                                             "support": got["diagnosis"]["verdict"],
                                             "indicator": got["diagnosis"]["indicator"],
                                             "missing_words": got["diagnosis"]["missing"]})
            if problems:
                fail(sid, "ask_invalid", {"axis": a["axis"], "problems": problems})
                continue
            absent = parsed.get("answer_contract") != K_CONTRACT and any(not o["evidence"] for o in a["options"])
            for o, (_, t) in zip(a["options"], opts):  # the model's evidence per option table (contract .3)
                d["option_evidence"][t] = {**check_evidence(sid, t, o["evidence"]),
                                           "field_absent_in_answer_contract": absent}
            unconfirmed = [t for _, t in opts if not d["option_evidence"][t]["valid"]]
            if unconfirmed:  # an option table with no valid model evidence: the ask is held, never confirmed
                fail(sid, "target_table_unconfirmed",
                     {"axis": a["axis"], "tables": unconfirmed, "stage": "model_ask",
                      "why": "evidence field absent in the answer's contract" if absent
                      else "no valid model evidence for these option tables"})
                continue
            st = stated(question, stats, sid, a["axis"], splitters)
            if st["unresolved"]:
                fail(sid, "basis_attribution_unresolved", {"axis": a["axis"], "values": sorted(st["values"])})
                continue
            if st["values"]:
                hit = [t for v, t in opts if v in st["values"]]
                if len(hit) == 1:
                    d["corrections"].append({"code": "unneeded_ask_to_choice", "axis": a["axis"],
                                             "stated": sorted(st["values"]), "source": st["source"], "table": hit[0]})
                    d["representative"] = hit[0]
                    d["basis"][a["axis"]] = {"value": sorted(st["values"]), "source": st["source"]}
                    d["code_evidence"] += _bracket_evidence(lk.row[hit[0]], a["axis"])
                else:
                    fail(sid, "ask_on_stated_axis", {"axis": a["axis"], "stated": sorted(st["values"])})
                continue
            d["asks"].append({"axis": a["axis"], "source": "model",
                              "options": [{"value": v, "table_id": t, **({"written": w, "rule": "value_forms"}
                                                                          if w != v else {})}
                                          for (v, t), w in zip(opts, written)]})
        if d["reasons"]:
            continue
        if d["corrections"] and not d["asks"]:
            d["model_kind"] = "ask_corrected"
            verified(sid, d["representative"], "corrected_ask")
        elif d["asks"]:
            d["representative"] = d["asks"][0]["options"][0]["table_id"]  # for the other axes only

    # 3. basis on the representative's card
    for sid, d in D.items():
        rep = d["representative"]
        if rep is None or d["reasons"] or d["model_kind"] not in ("choice", "ask_corrected", "ask"):
            continue
        card = lk.card_of[rep]
        rows = {r["table_id"]: r for r in card["tables"]}
        asked = {a["axis"] for a in d["asks"]}
        d["checks_done"].append("basis")
        for split in card["splits"]:
            axis = split["axis"]
            if not split["ask_axis"] or axis in asked or axis in d["basis"]:
                continue
            if axis not in rows[rep]["basis"]:
                d["warnings"].append({"code": "representative_basis_unknown", "axis": axis})
                continue
            st = stated(question, stats, sid, axis, splitters)
            if st["ignored"]:
                d["warnings"].append({"code": "splitter_without_axis_value", "axis": axis, "quotes": st["ignored"]})
            if st["unresolved"]:
                fail(sid, "basis_attribution_unresolved", {"axis": axis, "values": sorted(st["values"])})
                break
            here = tuple(rows[rep]["basis"][axis])
            if st["values"]:
                if set(here) <= st["values"]:
                    d["basis"][axis] = {"value": list(here), "source": st["source"]}
                    continue
                if d["model_kind"] == "ask":
                    d["basis"][axis] = {"value": sorted(st["values"]), "source": st["source"],
                                        "note": "the ask's option tables are representatives only"}
                    continue
                target = tuple(sorted(st["values"]))
                switch = _siblings_on(card, rows, rep, axis, target, views)
                evidenced = [t for t in switch if (d["option_evidence"].get(t) or {}).get("valid")]
                if evidenced and len({_other_words_no_time(rows[t]) for t in switch}) == 1:
                    # a switch only to a table the model gave its own evidence for (users 2026-10-28 06:47, 07:19)
                    new = evidenced[0]
                    got, links = axis_verdict(sid, rep, new, axis)
                    d["corrections"].append({"code": "basis_switched", "axis": axis, "from": rep, "to": new,
                                             "stated": sorted(st["values"]), "source": st["source"]})
                    d["representative"] = rep = new
                    d["basis"][axis] = {"value": list(target), "source": st["source"]}
                    d["code_evidence"] += _bracket_evidence(rows[new], axis)
                    d["verification"].append(_trace(f"basis_switched:{axis}", got))
                    if not got["ok"]:
                        fail(sid, got["code"], {"for": f"basis_switched:{axis}", "table": new,
                                                **({"detail": got["detail"]} if "detail" in got else {})})
                        break
                    if links:  # the model's evidence for the new table is kept from here; the old names are not
                        d["axis_bound_items"][axis] = links
                        model = d["option_evidence"].get(new) or {"items": []}
                        d["item_evidence"] = axis_items(d["item_evidence"], [axis])[0] + model["items"]
                elif switch:
                    # the question states one value and the model chose another's table: never switched without
                    # the model's evidence for the stated value's table, never passed either (user 07:19 UTC)
                    fail(sid, "stated_basis_conflict", {"axis": axis, "stated": sorted(st["values"]),
                                                        "representative_value": list(here),
                                                        "stated_value_tables": switch,
                                                        "why": "no model evidence for the stated value's table"})
                    break
                else:
                    fail(sid, "stated_basis_not_on_card", {"axis": axis, "stated": sorted(st["values"]),
                                                           "candidates": switch})
                    break
                continue
            # an axis the question leaves open (user 2026-10-28 07:19 UTC): the model's own decision stands. A
            # choice keeps its table's value, recorded as the model's choice and never as the user's basis; an
            # ask leaves the axes it did not ask open. Code neither asks nor holds because siblings exist; whether
            # an ask was needed is scored against the standard, from the model's output.
            if d["model_kind"] == "ask":
                d["unasked_axes"].append(axis)
            else:
                d["basis"][axis] = {"value": list(here), "source": "model_choice",
                                    "note": "not stated by the question; the model chose this value's table"}

    # 4. periods and final tables (ask options: feasibility record only)
    for sid, d in D.items():
        if d["reasons"] or d["model_kind"] in (None, "missing"):
            continue
        reading = PR.read_quotes([r["quote"] for r in out["periods"] if sid in r["for"]],
                                 [r["quote"] for r in out["frequencies"] if sid in r["for"]])
        if d["asks"]:
            d["ask_feasibility"] = _ask_feasibility(d, stats[sid]["quote"], reading, lk, as_of)
            continue
        if d["model_kind"] == "ask":
            continue
        rep = d["representative"]
        rows = {r["table_id"]: r for r in lk.card_of[rep]["tables"]}
        d["period"] = {"readings": reading["readings"]}
        if reading["problems"]:
            for p in reading["problems"]:
                fail(sid, p["code"], p)
            continue
        candidates = _candidates(rows, rep)
        d["checks_done"].append("periods")
        got = _select_periods(stats[sid]["quote"], rep, candidates, d["item_evidence"], reading, lk, as_of, "choice",
                              d["retention"])
        d["verification"] += got.pop("trace")
        d["period"].update({"candidates": candidates, "excluded": [t for t in rows if t not in candidates],
                            "default_applied": not any(c["kind"] == "period" for c in reading["conditions"]), **got})
        if got["status"] != "selected":
            fail(sid, got["reason"], got.get("detail"))
            continue
        # 5. recheck every final table
        d["checks_done"].append("final_recheck")
        problems = []
        for row in got["tables"]:
            t = row["table_id"]
            again = verify_table(stats[sid]["quote"], t, d["item_evidence"], lk, d["retention"])
            d["verification"].append({**_trace("final_table", again), "period": row["period"],
                                      "frequency": row["frequency"]})
            if not again["ok"]:
                problems.append(f"{t}: {again['code']}")
            if _basis(lk.row[t]) != _basis(lk.row[rep]):
                problems.append(f"{t}: basis differs")
            if t not in got["usable"]:
                problems.append(f"{t}: content not verified")
        if problems:
            fail(sid, "final_recheck_failed", problems)
            continue
        d["final_tables"] = got["tables"]
        if [r["table_id"] for r in got["tables"]] != [rep]:
            d["corrections"].append({"code": "final_tables_by_period", "from": rep,
                                     "to": [r["table_id"] for r in got["tables"]]})

    for d in D.values():
        if d["model_kind"] == "choice" or d["model_kind"] == "ask_corrected":
            d["checks_not_done"] = [x for x in STAGES if x not in d["checks_done"]]
        if d["reasons"]:
            d["outcome"] = "failed"
        elif d["model_kind"] == "missing":
            d["outcome"] = "not_in_pool"
        elif d["asks"]:
            d["outcome"] = "ask" if d["citation"]["status"] == "verified" else "ask_unverified"
        elif d["final_tables"]:
            d["outcome"] = "selected" if d["citation"]["status"] == "verified" else "selected_unverified"
        else:
            d["outcome"] = "failed"
            d["reasons"].append({"code": "undecided", "detail": None})
    record = {"version": CHECK_VERSION, "question": question, "as_of": as_of,
              "model_output": copy.deepcopy(out), "parse": {"tables": dict(tables), "problems": parsed["problems"],
                        "answer_contract": parsed.get("answer_contract")},
              "checks": checks, "decisions": [D[s["id"]] for s in out["statistics"]],
              "alternatives": copy.deepcopy(out["alternatives"])}
    record["route"] = route(record)
    return record


def route(record: dict) -> dict:
    """The one next action from the outcomes (pure): every statistic selected (verified) -> handoff; verified asks
    and verified selections only -> ask; any statistic decided with its card evidence unverified, or decided next to
    failures or missing ones -> partial (never the verified handoff, users 2026-10-28 05:37 and 05:42 UTC); else
    fail."""
    outcomes = [d["outcome"] for d in record["decisions"]]
    counts = {o: outcomes.count(o) for o in OUTCOMES}
    done = counts["selected"] + counts["selected_unverified"] + counts["ask"] + counts["ask_unverified"]
    if outcomes and counts["selected"] == len(outcomes):
        action = "handoff"
    elif counts["ask"] and counts["ask"] + counts["selected"] == len(outcomes):
        action = "ask"
    elif done:
        action = "partial"
    else:
        action = "fail"
    return {"action": action, "complete": action == "handoff", "counts": counts}
