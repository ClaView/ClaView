"""judge_fragments (detail 2.5, plan 04 2-2 to 2-4): requirement draft, pairs, attribution and the certainty checks.

Certain only when none of the seven checks of plan 04 2-3 fails (a wrong answer marked certain is worse than a right
answer marked undecided):
1. every indicator, context, target and breakdown term is an exact catalog name (decision 6 range; a list member
   completed by the 2.2 list rules is not), of one type, not a weak word (inexact_term);
2. no row 3 (mixed_reading; also a complement "N을 A·B로" whose items are not read under N in every table), no
   row 10, unresolved requirement or appositive gloss (row_2_or_7);
3. no ambiguous condition word (ambiguous_word; no rule raises it yet);
4. no ambiguous attribution of a condition or calc hint, and no indicator named again in another sentence or topic
   (ambiguous_attribution; designation is cycle 1);
5. no word left after names, conditions, family qualifiers, calc hints and the known words (leftover_word);
6. every catalog name of the question is used by a requirement field, a negation, an exclusion, a condition or a
   relation word (unused_name);
7. a modifier over a list ("X의 A와 B") is settled by the catalog, and every target is held by a table of its
   indicator (scope_ambiguous); a family qualifier applies to the indicator it tells copies of apart;
8. (decision 10/18, policy ``data/certainty_policy.json``) the structure signature of the question is one the
   specification cases verified (``structure``; unverified_structure). Only asked when 1-7 pass.
9. (user decision 10/19) a question whose catalog terms are all target, total-header or basis terms is certain only
   when the role rule of the judgement table gave a requirement an indicator (no_indicator: no requirement has an
   indicator, a table context or a breakdown value). A question with no catalog term at all (out of scope, no term)
   keeps its own handling (row 10, R10).
Also: a basis read from a catalog value name with no condition behind it fails check 1, and a designation clause that
holds two conditions of one kind or a kind the item clauses already have fails check 4.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .. import paths
from ..catalog.relations import TGT_TYPES, relations_of
from ..catalog.text import index_key
from ..contract.shapes import CalcHint, Condition, DraftRequirement, Fragment, Judgement, Pair, Term, TextView
from .phrases import read_question
from .reading import ItemRead, leftover_words, term_of
from .structure import signature, signature_key, verified_keys, verified_only
from .table import Draft, Judged, judge

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

AMBIGUOUS_WORDS = frozenset(json.loads(paths.CONDITION_WORDS.read_text(encoding="utf-8"))
                            ["ambiguous_basis_words"]["words"])


def ambiguous_words(j: Judged, conditions: list[Condition], layer, reading=None) -> list[str]:
    """Check 3: a condition word with two meanings ("전망") that no table of its requirement's indicator holds. A word
    inside a catalog name token (not in its bracket part) is part of the name, no condition word of the question
    (review of plan 08, variant FX1n: "가상수출 전망은 빼줘")."""
    rel = relations_of(layer) if layer is not None else None
    in_names = set()
    for t in (reading.toks if reading is not None else []):
        if t.kind == "name":
            cut = t.bracket_at if t.bracket_at >= 0 else t.end
            in_names |= {c["id"] for c in t.inner if c["span"][1] <= cut}
    out = []
    for c in conditions:
        if c["kind"] != "basis" or str(c.get("value")) not in AMBIGUOUS_WORDS or c["id"] in in_names:
            continue
        p = j.place.get(c["id"])
        holders = [d for d in j.drafts if (isinstance(p, list) and d.id in p) or p in ("common", "ambiguous")]
        word = index_key(str(c["value"]))
        for d in holders or j.drafts:
            tables = d.indicator.tables if d.indicator is not None else set()
            held = rel is not None and any(t in rel.tables_of(word) or word in index_key(rel.table_names.get(t, ""))
                                           for t in tables)
            if not held:
                out.append(c["quote"])
                break
    return out


FAILURE_ORDER = ("inexact_term", "row_2_or_7", "mixed_reading", "ambiguous_word", "ambiguous_attribution",
                 "leftover_word", "unused_name", "scope_ambiguous", "ungrounded_split", "reattached", "no_indicator",
                 "unverified_structure")
NO_TERM_TYPES = frozenset({"out_of_scope"})  # term types that are no catalog term for check 9
NOT_INDICATOR_TYPES = TGT_TYPES | {"basis"}  # check 9: the term types of a question that names no indicator


def _indicator_side(d: Draft) -> bool:
    """A draft with an indicator-side term: its indicator, its table context or a breakdown value (indicator-side
    reads: indicator, table name, compound, or a value of an axis without a known role)."""
    return any(t is not None and t.rep == "indicator" for t in [d.indicator, d.context] + list(d.lower)) \
        or bool(d.context_name)


def _dropped_fragments(reading, fragments: list[Fragment]) -> set[str]:
    """Fragments whose names are all excluded or negated ("B는 빼줘", "A가 아니라"): their terms name nothing asked."""
    out = set()
    for f in fragments:
        a, b = f["span"]
        names = [t for t in reading.toks if t.kind == "name" and a <= t.start < b]
        if names and all(t.excluded or t.negated for t in names):
            out.add(f["id"])
    return out


def _requested(t, reading, conditions: list[Condition]) -> bool:
    """An excluded name the question requests (a complete request word after it, before the end of the exclusion
    that reaches it: "A를 보여줄래 B는 빼줘" read as one clause): it surfaces as an unused name (review r2-3)."""
    ends = [c["span"][1] for c in conditions if c["kind"] == "exclude" and c["span"][1] > t.end]
    if not ends:
        return False
    x = min(ends)
    # the request word's form comes from the lexicon: the "없어" of "필요 없어" is no final form (finding 2)
    return any(w.request_form == "final" and t.end <= w.start < x for w in reading.toks)


def _dropped_names(reading, layer) -> set[str]:
    """Catalog names only excluded or negated name tokens hold ("A를 보여줘 B는 빼줘": B), per name (review r2-1)."""
    if layer is None:
        return set()
    out, live = set(), set()
    for t in reading.toks:
        if t.kind != "name":
            continue
        names = {c.get("name") for c in term_of(t, layer).cands if c.get("name")}
        (out if (t.excluded or t.negated) else live).update(names)
    return out - live


def no_indicator(j: Judged, terms: list[Term], dropped: set[str] = frozenset(),
                 dropped_names: set[str] = frozenset()) -> bool:
    """Check 9: the question has catalog terms (those of excluded or negated names aside, by fragment and by name),
    every one of them (exact or a lexical candidate) a target, total header or basis term, and no requirement has an
    indicator-side term (the role rule of the judgement table can give one: a target name read as the indicator)."""
    named = [t for t in terms if t.get("name") and t.get("type") not in NO_TERM_TYPES
             and t.get("fragment_id") not in dropped and t.get("name") not in dropped_names]
    if not named or not j.drafts or any(t.get("type") not in NOT_INDICATOR_TYPES for t in named):
        return False
    return not any(_indicator_side(d) for d in j.drafts)


def _frag_of(item_read: ItemRead) -> list[str]:
    return list(item_read.item.fragment_ids)


def _indicator_fragment(d: Draft, fragments: list[Fragment]) -> str | None:
    if d.indicator is None:
        return None
    a = d.indicator.tok.start
    for f in fragments:
        if f["span"][0] <= a < f["span"][1]:
            return f["id"]
    return None


def attribution_of(j: Judged, conditions: list[Condition]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Condition ID -> draft ID / common / ambiguous (one value per condition), and draft ID -> own condition IDs."""
    place: dict[str, str] = {}
    own: dict[str, list[str]] = {d.id: [] for d in j.drafts}
    for c in conditions:
        p = j.place.get(c["id"])
        if p is None:
            continue
        if isinstance(p, list):
            for did in p:
                own[did].append(c["id"])
            place[c["id"]] = "common" if len(p) == len(j.drafts) and len(p) > 1 else p[0]
        else:
            place[c["id"]] = p
    return place, own


def _hint_rows(j: Judged, reading) -> list[CalcHint]:
    out: list[CalcHint] = []
    for t in reading.toks:
        if t.kind != "hint":
            continue
        p = j.place.get(j.hint_ids.get(id(t)))
        owners = p if isinstance(p, list) else ([d.id for d in j.drafts] if p == "common" else [])
        row: CalcHint = {"quote": t.quote, "span": [t.start, t.end], "kind": t.label,
                         "requirement_id": owners[0] if len(owners) == 1 else None}
        if owners:
            row["requirement_ids"] = owners
        out.append(row)
    return out


def used_tokens(j: Judged) -> set[int]:
    """Name tokens a final requirement field uses (check 6 by position, R-2): indicator, context, targets,
    breakdown, view and axes; the same indicator merged from a repeated item (row 1); names of a designation
    clause, which point at items of an earlier clause."""
    used: set[int] = set()
    for d in j.drafts:
        for term in [d.indicator, d.context] + list(d.targets) + list(d.lower) + list(d.alternates):
            if term is not None:
                used.add(id(term.tok))
        used.update(id(t) for _a, _k, t in d.view)
        used.update(id(t) for t in d.axes)
        if d.indicator is not None:
            for r in d.items:
                if r.indicator is not None and r.indicator.key == d.indicator.key:
                    used.add(id(r.indicator.tok))
    for g in j.groups:
        if g.designation:
            used.update(id(t) for it in g.items + g.modifier for t in it.toks if t.kind == "name")
    for r in j.reads.values():  # a general noun after a basis value ("명목 금액") is read as part of that value
        used.update(id(t) for t in r.fillers)
    return used


def _similar_name(term, layer) -> bool:
    """User decision 10/19 21:00: a name read through a derived or similar catalog name (plan 09 derived names, a
    head alias, an and-name tail, a completed list member, a split name) passes check 1 when it has table or value
    candidates. Except (user decision 10/19 21:45): a name found only as the bracket-free form of names with bracket
    qualifiers ("가상A(순)" read from "가상A": which bracket form, and so the role of its qualifier, is not settled),
    out-of-scope and dictionary entries, a name read inside a word written without spaces (plan 05 G: never exact),
    and an "A 및 B" name whose two sides are catalog names (one name, or a list of two as "A와 B" reads)."""
    if getattr(term.tok, "glued", False):
        return False
    if layer is not None and _two_names_joined(term.tok.spaced_words, layer):
        return False
    return any(c.get("kind") in ("table", "value") and c.get("path") != "dictionary"
               and c.get("variant", "").split(":")[-1] != "no_paren" for c in term.cands)


def _two_names_joined(words: list[str], layer) -> bool:
    """The words hold the list word between two words that are both catalog names of their own."""
    from ..catalog.text import index_key
    from .lexicon import AND_WORD

    names = layer.index.entries  # the catalog's names, derived ones included
    for k in range(1, len(words) - 1):
        if words[k] == AND_WORD:
            if index_key(words[k - 1]) in names and index_key(words[k + 1]) in names:
                return True
    return False


def build_judgement(text: TextView, conditions: list[Condition], fragments: list[Fragment], terms: list[Term],
                    layer: CatalogLayer | None, overrides=None) -> Judgement:
    """``overrides``: the model step's decisions (``overrides.Overrides``) fed into the judgement table; the checks
    then run on the new judgement as they are (an appositive the model resolved is no check 2 failure, a word it read
    as a request or filler word no check 5 failure, and check 8 does not apply when every confirm point was answered).
    None is the rule path."""
    reading = read_question(text, conditions, layer)
    j = judge(reading, conditions, layer, overrides)
    place, own = attribution_of(j, conditions)
    hints = _hint_rows(j, reading)
    failed: set[str] = set()
    sources: list[dict] = []  # plan 11 R0 (7-6): where each failure came from (record only)
    rid_of = {d.id: f"r{k}" for k, d in enumerate(j.drafts, 1)}  # 2.7 numbers the drafts in this order

    def src(code: str, detail: str, d=None, conditions_ids=(), fragment_ids=()) -> None:
        # plan 11 R1b (3절): the condition and fragment IDs a failure is about are record fields beside the R0 row
        # (empty: the whole requirement, or the whole question when requirement_ids is empty too); one row per
        # (condition, requirement_ids, detail) as before, the IDs of repeated calls merged into it
        key = {"condition": code, "requirement_ids": [rid_of[d.id]] if d is not None else [], "detail": detail}
        row = next((x for x in sources if all(x[k] == v for k, v in key.items())), None)
        if row is None:
            row = {**key, "condition_ids": [], "fragment_ids": []}
            sources.append(row)
        row["condition_ids"] += [c for c in conditions_ids if c and c not in row["condition_ids"]]
        row["fragment_ids"] += [f for f in fragment_ids if f and f not in row["fragment_ids"]]

    def frags_at(*positions: int) -> list[str]:
        return [f["id"] for f in fragments for p in positions if f["span"][0] <= p < f["span"][1]]

    def frags_of_items(items) -> list[str]:
        return list(dict.fromkeys(fid for it in items for fid in (getattr(it, "fragment_ids", None) or [])))

    drafts_out: list[DraftRequirement] = []
    for d in j.drafts:
        frag_ids = list(dict.fromkeys(f for r in d.items for f in _frag_of(r)))
        row: DraftRequirement = {
            "id": d.id, "fragment_ids": frag_ids, "indicator_fragment_id": _indicator_fragment(d, fragments),
            "indicator": d.indicator.tok.quote if d.indicator else None,
            "context": d.context.tok.quote if d.context else d.context_name,
            "targets": [t.tok.quote for t in d.targets],
            "breakdown": [t.tok.quote for t in d.lower] + [t.quote for t in d.unnamed_lower]
            + [t.quote for t in d.axes],
            "condition_ids": own.get(d.id, []), "status": d.status, "broad": d.broad}
        if d.row is not None:
            row["row"] = d.row
        drafts_out.append(row)
        if d.status == "unresolved":
            failed.add("row_2_or_7")
            src("row_2_or_7", "unresolved", d)
        if d.unnamed_lower or d.inferred:
            failed.add("inexact_term")  # a breakdown word or a table read without an exact catalog name
            src("inexact_term", "unnamed_lower" if d.unnamed_lower else "inferred", d)
        for term in [d.indicator, d.context] + list(d.targets) + list(d.lower):  # breakdown values are terms too
            if term is None:
                continue
            # user decision 10/19 21:00: a derived or similar catalog name (a lexical path with candidates) counts as
            # found; a name with no catalog candidate, a mixed or unknown type, a weak indicator stay inexact
            if not (term.exact or _similar_name(term, layer)) or term.mixed or term.unknown_type \
                    or (term is d.indicator and term.weak):
                failed.add("inexact_term")
                where = "indicator" if term is d.indicator else "context" if term is d.context \
                    else "target" if term in d.targets else "lower"
                found = term.exact or _similar_name(term, layer)
                why = "mixed_type" if term.mixed else "unknown_type" if term.unknown_type \
                    else "no_catalog_name" if not found else "weak_indicator"
                src("inexact_term", f"{why}:{where}", d, fragment_ids=frags_at(term.tok.start))
        if any(a == "catalog_basis" for r in d.items for a, _k, _t in r.view + d.view):
            # a basis read from a catalog value name has no condition behind it: the requirement could not carry it
            # as a condition ID (plan 04, 5-1), so it is not certain (check 1)
            failed.add("inexact_term")
            src("inexact_term", "catalog_basis", d)
        if d.copy_of is not None and any(a == "basis_word" for a, _k, _t in d.view):
            # a copy told apart by a basis word that is no condition ("신규취급액과 잔액": 잔액) carries no
            # condition ID for what it differs by (check 1)
            failed.add("inexact_term")
            src("inexact_term", "copy_basis_word", d)
        if d.axes_unsettled:  # plan 05 B, decision 2: listed X-byeol axes with no marker (split or together?)
            failed.add("scope_ambiguous")
            src("scope_ambiguous", "axes_unsettled", d)
    if not j.drafts:
        failed.add("row_2_or_7")
        src("row_2_or_7", "no_draft")
    pairs: list[Pair] = []
    for left, right, rown, verdict in j.pairs:
        lf, rf = _frag_of(left), _frag_of(right)
        if not lf or not rf:
            continue
        group_rel = next((g.relation for g in j.groups for it in g.items if it is right.item), None)
        pairs.append({"left": lf[0], "right": rf[-1], "row": rown, "verdict": verdict, "relation": group_rel})
        if rown == 3:
            failed.add("mixed_reading")
            src("mixed_reading", "pair_row_3", fragment_ids=[lf[0], rf[-1]])
        if rown == 10:
            failed.add("row_2_or_7")
            src("row_2_or_7", "pair_row_10", fragment_ids=[lf[0], rf[-1]])
    for res in j.results:
        res_frags = frags_of_items([r.item for p in res.pairs for r in p[:2]]
                                   + [r.item for d in res.drafts for r in d.items])
        if res.mixed:
            failed.add("mixed_reading")
            src("mixed_reading", "group_mixed", fragment_ids=res_frags)
        if res.scope:
            failed.add("scope_ambiguous")
            src("scope_ambiguous", "group_scope", fragment_ids=res_frags)
    if any(g.appositive and g.start not in j.resolved for g in j.groups):
        failed.add("row_2_or_7")
        src("row_2_or_7", "appositive",
            fragment_ids=frags_of_items([it for g in j.groups if g.appositive and g.start not in j.resolved
                                         for it in g.items + g.modifier]))
    failed.update(j.override_conflicts)
    for code in j.override_conflicts:
        src(code, "override_conflict")
    if any(p == "ambiguous" for p in place.values()) or j.designation_conflict or \
            (len(j.drafts) >= 2 and any(not h.get("requirement_ids") for h in hints)):
        failed.add("ambiguous_attribution")
        src("ambiguous_attribution", "place_ambiguous" if any(p == "ambiguous" for p in place.values())
            else "designation_conflict" if j.designation_conflict else "hint_unowned",
            conditions_ids=[cid for cid, p in place.items() if p == "ambiguous"])
    amb_words = ambiguous_words(j, conditions, layer, reading)
    if amb_words:
        failed.add("ambiguous_word")
        src("ambiguous_word", "condition_word",
            conditions_ids=[c["id"] for c in conditions if c["kind"] == "basis" and c["quote"] in amb_words])
    leftover = leftover_words(reading)
    if overrides is not None:
        leftover = [w for w in leftover if w not in overrides.ignored]
    if leftover:
        failed.add("leftover_word")
        src("leftover_word", "leftover")
    used = used_tokens(j)
    unused_toks = [t for t in reading.toks if t.kind == "name" and not t.negated
                   and (not t.excluded or _requested(t, reading, conditions)) and id(t) not in used]
    unused = [t.quote for t in unused_toks]
    if unused:
        failed.add("unused_name")
        src("unused_name", "unused", fragment_ids=frags_at(*(t.start for t in unused_toks)))
    # split grounding (user decision 10/18): reattached is never certain; ungrounded_split no longer blocks certainty
    # (user decision 10/19 21:00): its loose requirements carry the reason "ungrounded" and get no table
    if j.split_check == "reattached":
        failed.add(j.split_check)
        src(j.split_check, "split_check")
    # check 9 (user decision 10/19): targets, total headers or basis terms only
    if no_indicator(j, terms, _dropped_fragments(reading, fragments), _dropped_names(reading, layer)):
        failed.add("no_indicator")
        src("no_indicator", "no_indicator_term")
    live = {d.id for d in j.drafts}
    hint_groups = []
    for res in j.results:
        for grp in res.hints:
            ids = [d.id for d in grp if d.id and d.id in live]
            if len(ids) >= 2:
                hint_groups.append(ids)
    sig = signature_key(signature(reading, j, conditions))
    confirmed = overrides is not None and overrides.structure_confirmed
    if not failed and verified_only() and sig not in verified_keys() and not confirmed:
        failed.add("unverified_structure")  # check 8: a structure no specification case has verified
        src("unverified_structure", "structure")
    failed_list = [f for f in FAILURE_ORDER if f in failed]
    out: Judgement = {"pairs": pairs, "requirement_draft": drafts_out, "certain": not failed_list,
                      "failed_conditions": failed_list, "attribution": place, "table_group_hint": hint_groups,
                      "calc_hints": hints, "negated": [t.quote for t in reading.negated], "leftover": leftover,
                      "unused_names": unused, "structure": sig,
                      "failed_sources": [x for x in sources if x["condition"] in failed_list]}
    return out
