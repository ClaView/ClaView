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
   other open axes. No basis is assumed because one sibling covers the period. A negated value (plan 53:
   ``stated`` reads polarity for splitters and the question alike) is excluded, not stated: a representative, an
   option or an ask option of it is ``stated_basis_conflict``, never switched to or replaced by another value; a value
   whose negation scope is not read stops the statistic (``basis_polarity_unresolved``);
   3b. a value the model's own splitters state for the statistic on an axis the card does not split (a single
   table, or siblings that share or lack that value) is compared with the chosen table's title basis
   (``stated_basis_checks``): the same value passes, another value is ``stated_basis_conflict``, a value the table
   does not show is ``stated_basis_unverified`` (a warning, never the opposite value). Nothing is switched or asked;
   3a. every ask option (model or code) is compared the same way on the axes other than the asked one: an option
   of another stated value is ``stated_basis_conflict`` (stage ``ask_option``), so the ask is never passed as
   verified; no option is replaced or added. When the model quoted one frequency that code reads, an option whose
   value gives no table of that frequency (the option table or its frequency/edition candidates) makes the ask
   ``ask_invalid`` (``option_frequency_unsupported``); a value whose only such tables are candidates that do not
   keep the option's cited values by the period rule is held (``target_table_unconfirmed``, stage
   ``ask_frequency``); both facts per candidate are in ``ask_frequency_checks``. With no readable frequency nothing
   is removed by a default (users 2026-10-29 07:51 and 08:24 UTC);
4. periods (``periods.read_quotes``, ``periods.final_tables``) among the candidates of the decided representative
   that pass the check; a quote that cannot be read fails, never a default; each quoted frequency word is kept in
   ``frequency_quotes`` with the statistics it is for, the frequency read and the point in the period ("end" for
   월말, 분기말, 연말), record only (plan 36). With the policy's condition rule ``ask_condition`` (policy ④, plan 56
   order 5) the frequency is decided first (``frequency``: {value, source, reason, valid, offered}): a quoted one
   (``confirmed_quote``); else the one offered frequency that fits the requested periods (``only_valid``,
   ``periods.frequency_fit``), handed to the resolver; none fits: ``frequency_not_fit`` (never another frequency);
   several fit: a frequency question (``condition_asks``, outcome ``ask_condition``), never the period unit's or the
   longest frequency. Each end point quoted (월말, 연말) gets ``end_point_status``: ``table_satisfies`` (the final
   table is an end-of-period stock, 말잔), ``extraction_required`` (only a finer frequency is offered: not done in
   the first stage, never filled with one month's value) or ``unverified``. The period, with that rule (policy ②):
   a period or frequency word the reader finds in the question outside every quote (``periods.unquoted_conditions``)
   is restored when the question has one statistic (``period_quote_missing`` / ``frequency_quote_missing``,
   restored); with several statistics, or when the question's period is quoted for other statistics only, its
   statistic is not known and the statistic stops (cause ``extraction_missing``, never asked); no period at all is
   the user's (cause ``user_omitted``): a period question with the presets' windows that a candidate can cover,
   never a default window. A quote the reader cannot read keeps its failure with its cause (``unreadable_range``
   when nothing was read, else ``unsupported_expression``). Questions of statistics that share a quoted period or
   frequency row and the same options are one question (``statistics``); answers (``condition_answers``) are read as
   quotes by the same reader;
5. the final tables are checked again (``verify_table``, basis, shares) and the route is computed.

Values inside one table (currency, items) stay B's. Nothing here names a table, case or question.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .conditions.words import BASIS_AXES
from .search.basis import polarity as basis_polarity
from .search.basis import values as basis_values
from .search.families import FREQUENCY_WORDS, SIGNATURE_AXES
from .search.tables import nospace

from . import periods as PR
from .cards import ASK_AXES, VALUE_FORMS, card_facts, card_values, question_words, shown_value
from .contract import AVERAGE_BASES
from .contract import CONTRACT_VERSION as K_CONTRACT
from .contract import EARLIER_CONTRACTS
from .contract import OPTION_EVIDENCE_CONTRACTS
from .contract import SEMANTIC_PERIOD_CONTRACTS
from .periods import semantic as SP
from .focus import check_scope, legacy_scope
from .catalog_scope import missing_scope
from .focus_groups import check_groups
from .quotes import quote_in_question

# .16: a stated basis is checked on axes the card does not split too (single tables included)
# .17: and on every ask option, on the axes other than the asked one
# .18: ask options must give the requested frequency; the reason for a default period is recorded
# .19: a candidate giving the frequency must keep the option's cited values (the period rule, shared)
# .20: a negated basis value is excluded, never stated (plan 53, user 2026-10-30 02:07 UTC)
# .22: the frequency decided before the periods; frequency and period questions, the cause of a missing period,
# restored quotes and end-point status (plan 56 order 5)
# .24: three-state stated-basis rows, cited items as a fact source, alternatives and re-judgment targets, the
# excluded-basis warning (plan 56 order 6-2b, bundle 5)
# .25: the period meaning and the aggregation rows of contract .4 answers (plan 56 order 6-3a)
# .26: the edition judgments, relation status, edition corrections and the unverified-relation warning (plan 56
# order 7, ⑦)
# .27: the aggregation evidence by where its quote sits on the card, the basis of the selected series, the average
# basis and the verified path (plan 56 order 6-3c, users 2026-10-31 19:46 and 20:10)
# .28: each ask option's status (supported / unsupported / unconfirmed, why), only executable options asked, an ask
# with no executable option never sent (plan 56 order 8, bundle 7, ⑥-가)
# .29: options of several cards allowed, the basis of each option's own card (plan 56 order 8, ⑨)
# .30: the combinations of the asks and the period asked first when the options depend on it (plan 56 order 8, ⑥)
# .31: a frequency made only by aggregation is unconfirmed, never unsupported; an unread condition is a recovery
# target; the period is asked first only when it is missing (users 2026-10-31 21:54)
# .32: the decision's stage, each warning's kind, statistic, scope and stage, each final table's period hint and
# phrase, the same table asked for different periods apart from rows the model wrote apart (plan 56 order 11)
# .33: a period option says the range choosing it requests, in the decided frequency or each one left (13-4)
# .40: contract .8 periods and frequencies read from the model's structured rows (``periods.semantic``), never the quote
# reader nor words outside the quotes; the failure kind of every ending reason recorded (BOK20 live check 11-02)
CHECK_VERSION = "tl-check-2026-11-02.42"
# the model's failures to structure a period or frequency it quoted or to give a row: one recovery may correct them
SEMANTIC_INTERNAL = ("period_structure_invalid", "frequency_unreadable", "period_meaning_conflict",
                     "frequency_conflict", "period_row_missing", "frequency_row_missing")
# where a statistic ended (record only, owner 11-02 02:47 UTC): never mixed in one count
FAILURE_KINDS = {
    "internal_interpretation": (*SEMANTIC_INTERNAL, "period_unresolved", "period_quote_missing",
                                "period_rows_inconsistent", "frequency_quote_missing"),
    "unsupported_expression": ("period_exclusion_unsupported", "period_partially_read", "frequency_partially_read",
                               "period_base_year_ambiguous", "frequency_unresolved",
                               "period_expression_unsupported", "frequency_multiple_requested"),
    "data_not_covered": ("period_not_covered", "frequency_not_offered", "frequency_not_fit", "table_not_in_pool"),
}
# .39: sendable same-table focus groups checked before the existing one recovery; whitespace quote rule shared
# .38: scope source excerpts and length checked; .37: a period window with nothing to fetch now is marked
# (no_current_data); .36: restored period quoted, answered frequency user_answer; .35: a linked statistic reads its
# own links' rows; .34: same_as linked periods asked once, referable fixed statistics; .15: frequency_quotes
# (quote, for, frequency, end point) recorded; .21: plan 55 negation scope
# .23: the model's basis polarity read from contract .4 answers (plan 56 order 6-2); older answers as before
# answers written to the polarity contract (6-3 makes it the current one): their splitters carry value and polarity,
# read as the model wrote them; any other answer contract keeps the basis wording reading (search.basis)
POLARITY_CONTRACTS = (*EARLIER_CONTRACTS, K_CONTRACT)  # .4 (plan 56 order 6-3b) and .5 (6-3c)
POLARITIES = ("required", "any_of", "excluded", "allowed", "undetermined", "unreadable", "not_basis")
# the one recovery of a correctable output error is linked in order 9; until then none is available
POLARITY_RECOVERY = {"status": "not_available", "slot": "a_self_rejudge"}
# ``*_unverified``: the table or ask is decided with the model's card evidence not verified (users 2026-10-28 05:37
# and 05:42 UTC); never the same state as a verified one, and routed as ``partial``, never ``handoff``
OUTCOMES = ("selected", "selected_unverified", "ask", "ask_unverified", "not_in_pool", "failed")
# a statistic whose table is decided and whose period or frequency only the user can give (policy ②④, plan 56
# order 5): routed as an ask; counted in ``route`` only when there is one
CONDITION_OUTCOME = "ask_condition"
# a statistic kept as a candidate whose requested frequency is to be made from a finer one (policy ④ supplement,
# users 2026-10-31 17:56 and 18:28 UTC): never handed on, never complete before the aggregation is done
AGGREGATION_OUTCOME = "aggregation_needed"
PERIOD_MEANINGS = ("range", "latest_available", "same_as", "user_undecided")
# the finer frequencies a frequency can be made from (sub-period boundaries fit the requested ones)
FINER = {"Q": ("M",), "H": ("Q", "M"), "Y": ("H", "Q", "M")}
METHODS = ("sum", "period_end", "period_average", "weighted", "unknown")
SOURCE_BASES = ("end_balance", "average_balance", "flow", "ratio", "unknown")
# the pairs that can make a method verified (a conservative list: any other pair is basis_mismatch, unconfirmed,
# never a refusal)
METHOD_PAIRS = {("period_end", "end_balance"), ("sum", "flow"), ("period_average", "average_balance"),
                ("period_average", "flow"), ("weighted", "ratio")}
# where an aggregation evidence quote sits (plan 56 order 6-3c): the name part of a value the decision chose for the
# statistic, the title or a bracket of a table chosen for it, a unit only, a fact of the card not chosen (another
# table or value), no quote. definition_or_formula keeps its name; no card field makes it today.
LINKED_LEVELS = ("definition_or_formula", "selected_series_name", "selected_table_title")
EVIDENCE_LEVELS = (*LINKED_LEVELS, "unit_only", "not_linked", "none")
_LEVEL_RANK = {"selected_series_name": 3, "selected_table_title": 2, "unit_only": 1, "not_linked": 0}
# how a period average weighs its source periods (``AVERAGE_BASES`` of the contract): the model judges it with a
# quote, code checks the quote's place
# a fixed match of enum values: the source basis and its value on a basis axis of the card
BASIS_AXIS_VALUE = {"end_balance": ("stock", "말잔"), "average_balance": ("stock", "평잔")}
# an ask option's status (plan 56 order 8, ⑥-가): supported (its own card covers the period, the frequency and keeps
# the cited values), unsupported (it cannot), unconfirmed (a check could not be done: never turned into unsupported,
# never offered as executable); why: period, frequency, items, basis
OPTION_STATUSES = ("supported", "unsupported", "unconfirmed")
OPTION_WHYS = ("period", "frequency", "items", "basis")
# the screen's words for an option chosen by ⑥-나 (never the user's choice, nor the only one in the catalog)
AUTO_DECIDED_MESSAGE = ("제시된 선택지 중 요청 기간·주기를 지원하는 것으로 확인된 기준이 {value} "
                        "하나여서 선택했습니다")
# the stage of a decision (plan 56 order 11, bundle 11): what the next step waits for
A_STAGES = ("selected", "waiting_answer", "rejudge_pending", "blocked_contract", "failed")
# the kind of a warning (the warning states of bundle 11); a code not named here is its own kind
WARNING_KINDS = {"stated_basis_unverified": "basis_unverified", "representative_basis_unknown": "basis_unverified",
                 "edition_relation_unverified": "edition_relation_unverified",
                 "same_table_period_differs": "link_unsupported", "basis_excluded": "basis_excluded",
                 "splitter_without_axis_value": "splitter_without_axis_value",
                 "basis_polarity_undetermined": "basis_polarity_undetermined"}
AGGREGATION_WHYS = ("method_unknown", "basis_mismatch", "no_evidence", "evidence_unit_only", "evidence_not_linked",
                    "basis_not_specific_to_series", "required_series_missing", "averaging_basis_unstated",
                    "day_count_unavailable")
FREQUENCY_REASONS = ("confirmed_quote", "only_valid", "needs_answer", "not_fit")
END_POINT_STATUSES = ("table_satisfies", "extraction_required", "unverified")
PERIOD_CAUSES = ("user_omitted", "unreadable_range", "extraction_missing", "unsupported_expression")
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
                "item": {t for it in card_values(card) if table in it["tables"] for t in (it["name"], shown_value(it))}}

    def resolve_item(self, table: str, text: str) -> dict:
        """The catalog values a cited card text names on ``table`` (user 2026-10-28 06:42 UTC): the shown text
        (name with parent and unit) names one value; a name names every value of that name on the table, and is
        specific only when they are one (``unique``), else ``name_only_several_values``."""
        card = self.card_of[table]
        entries = [it for it in card_values(card) if table in it["tables"]]
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
    return quote_in_question(quote, question)


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


def _stated_polarity(sid: str, axis: str, splitters: list[dict]) -> dict:
    """``stated`` for an answer of the polarity contract (plan 56 order 6-2): the model's rows of this statistic and
    axis, as written. ``values`` with ``mode`` all (required: the final table gives every value) or any (any_of:
    one of them is enough); ``excluded`` as before; ``allowed`` makes no condition and is never asked again;
    ``undetermined`` is the user's condition left open. Nothing is read from the question's wording here."""
    rows = [s for s in splitters if s["axis"] == axis and sid in s["for"]]
    required = {s["value"] for s in rows if s.get("polarity") == "required"}
    any_of = {s["value"] for s in rows if s.get("polarity") == "any_of"}
    return {"values": required or any_of, "mode": "all" if required else "any" if any_of else None,
            "excluded": {s["value"] for s in rows if s.get("polarity") == "excluded"},
            "allowed": any(s.get("polarity") == "allowed" for s in rows),
            "undetermined": any(s.get("polarity") == "undetermined" for s in rows),
            "polarity_unresolved": [], "source": "splitter" if rows else None, "unresolved": False, "ignored": [],
            "polarity": True}


def _meets(here, st: dict) -> bool:
    """The table's values on the axis meet what is stated: every required value (mode all), one of the any_of values
    (mode any), or, for an older answer, the table's values among the stated ones (as before)."""
    if st.get("mode") == "all":
        return st["values"] <= set(here)
    if st.get("mode") == "any":
        return bool(set(here) & st["values"])
    return set(here) <= st["values"]


def _carries_all(card: dict, axis: str, values: set) -> bool:
    return any(values <= set(_basis(r).get(axis) or ()) for r in card["tables"])


def polarity_problems(sid: str, splitters: list[dict], d: dict, lk) -> list[dict]:
    """The correctable output errors in the model's polarity rows of one statistic (plan 56 order 6-2), each
    {cause, detail}: a value outside its axis (``value_not_in_axis``), different polarities for one axis and value or
    required and any_of on one axis (``polarity_conflict``), an ask on an allowed axis (``ask_on_allowed_axis``), an
    undetermined axis the model decided while its card still offers the other values (``undetermined_without_ask``),
    an unreadable row (``unreadable_row``). A quote not in the question is caught with the quotes (step 1). Code reads
    no wording here: it checks the rows against the request and the card."""
    rows = [s for s in splitters if sid in s["for"]]
    out, seen, by_axis = [], {}, {}
    for s in rows:
        axis, value, pol = s["axis"], s.get("value"), s.get("polarity")
        by_axis.setdefault(axis, set()).add(pol)
        if pol == "unreadable":
            out.append({"cause": "unreadable_row", "detail": {"axis": axis, "quote": s["quote"]}})
        if axis != "other" and value not in BASIS_AXES.get(axis, {}):
            out.append({"cause": "value_not_in_axis", "detail": {"axis": axis, "value": value}})
        key = (axis, value)
        if key in seen and seen[key] != pol:
            out.append({"cause": "polarity_conflict", "detail": {"axis": axis, "value": value,
                                                                 "polarities": sorted({seen[key], pol})}})
        seen.setdefault(key, pol)
    asked = {a["axis"] for a in d["asks"]}
    rep_card = lk.card_of.get(d["representative"]) if d.get("representative") else None
    for axis, pols in by_axis.items():
        if {"required", "any_of"} <= pols:
            out.append({"cause": "polarity_conflict", "detail": {"axis": axis, "polarities": ["any_of", "required"]}})
        if "allowed" in pols and axis in asked:
            out.append({"cause": "ask_on_allowed_axis", "detail": {"axis": axis}})
        if "undetermined" in pols and axis not in asked and d["model_kind"] in ("choice", "ask_corrected"):
            if rep_card is not None and any(sp["axis"] == axis for sp in rep_card["splits"]):
                out.append({"cause": "undetermined_without_ask", "detail": {"axis": axis}})
    return out


def stated(question: str, stats: dict, sid: str, axis: str, splitters: list[dict], polar: bool = False) -> dict:
    """{values, source, unresolved, ignored}: the values of ``axis`` the question states for statistic ``sid``
    (step-3 re-review, 2026-10-27 21:54 UTC: no common attribution from word positions):
    - the model's splitters on the axis for this statistic, when their quote holds a value of the axis (source
      ``splitter``; a splitter naming every statistic is the explicit common case);
    - none for this statistic, but splitters on the axis for other statistics that account for every value the
      question writes on the axis: the values belong to them, nothing is stated here (source ``other_statistics``);
    - no splitter on the axis at all: the question's value when the question has one statistic and one value
      (``question_one_statistic``);
    - otherwise a value written in the question cannot be attributed: ``unresolved`` (never applied to every
      statistic), which is told apart from no value written at all.
    Polarity (plan 53, user 2026-10-30 02:07 UTC; ``search.basis.polarity``, the same reading for splitters and the
    question): ``values`` are the required values only; a negated value is in ``excluded`` ("계절조정은 하지 않은";
    "X가 아니라 Y": X excluded, Y required) and is checked as a conflict, never turned into the other value; a value
    whose negation scope is not read is in ``polarity_unresolved`` (the statistic stops, never a requirement).
    Splitters whose quotes only permit a value ("계절조정은 제외하지 않아도 돼", plan 55) state nothing: read as no
    splitter. ``polar`` (an answer of the polarity contract): the model's polarity rows only (``_stated_polarity``)."""
    if polar:
        return _stated_polarity(sid, axis, splitters)
    own = [s for s in splitters if s["axis"] == axis and sid in s["for"]]
    good = [s for s in own if basis_values(s["quote"], axis)]
    ignored = [s["quote"] for s in own if s not in good]
    reads = [basis_polarity(s["quote"], axis) for s in good]
    if any(r["required"] or r["excluded"] or r["unresolved"] for r in reads):
        return {"values": set().union(*(r["required"] for r in reads)),
                "excluded": set().union(*(r["excluded"] for r in reads)),
                "polarity_unresolved": [u for r in reads for u in r["unresolved"]], "source": "splitter",
                "unresolved": False, "ignored": ignored}
    read = basis_polarity(question, axis)
    values = read["required"] | read["excluded"] | {u["value"] for u in read["unresolved"]}
    none = {"excluded": set(), "polarity_unresolved": [], "unresolved": False, "ignored": ignored}
    if not values:
        return {"values": set(), "source": None, **none}
    others = [s for s in splitters if s["axis"] == axis and sid not in s["for"] and basis_values(s["quote"], axis)]
    if others and values <= set().union(*(basis_values(s["quote"], axis) for s in others)):
        return {"values": set(), "source": "other_statistics", **none}
    if len(stats) == 1 and len(read["required"]) <= 1 and not others:
        return {"values": read["required"], "excluded": read["excluded"],
                "polarity_unresolved": read["unresolved"], "source": "question_one_statistic", "unresolved": False,
                "ignored": ignored}
    return {"values": values, "source": None, **{**none, "unresolved": True}}


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


STATUS_OF = {"match": "match", "not_excluded": "match", "conflict": "differ", "table_value_unknown": "unknown"}


def _cited_value(items: list[str], axis: str) -> tuple | None:
    """The axis value the model's cited items show on ``table`` when its title shows none (bundle 5): only items
    verified on that very table (the model's own table or the option's), every one that names a value of the axis
    naming the same one; None otherwise. A fact the code read from the model's cited item, kept apart from the title."""
    if not items:
        return None
    found = {tuple(sorted(basis_values(n, axis))) for n in items if basis_values(n, axis)}
    return next(iter(found)) if len(found) == 1 else None


def _alternative(card: dict, table: str, axis: str, st: dict) -> bool:
    """Another table of the card meets the stated condition on the axis (a value it shows, never a guess)."""
    for r in card["tables"]:
        if r["table_id"] == table:
            continue
        here = _basis(r).get(axis)
        if not here:
            continue
        if st["values"] and _meets(here, st) or not st["values"] and st["excluded"] and not set(here) & st["excluded"]:
            return True
    return False


def _state_row(d: dict, row: dict, card: dict, st: dict) -> None:
    """The three-state fields of a stated-basis row and, for a differ or unknown row with an alternative, a
    re-judgment target (record only; order 9 links the recovery)."""
    row["status"] = STATUS_OF[row["result"]]
    row["alternative_present"] = row["status"] != "match" and _alternative(card, row["table"], row["axis"], st)
    if row["alternative_present"]:
        d.setdefault("rejudge_targets", []).append({"code": "stated_basis_" + row["status"], "axis": row["axis"],
                                                    "table": row["table"], "stage": row.get("stage", "chosen_table")})


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
    for t in candidates:
        label, got = _kept(quote, rep, t, item_evidence, lk, retention)
        trace.append(_trace(f"{stage}:period_candidate", got))
        if label in USABLE:
            usable[t] = label
        else:  # retention_unverified included: never put in the period and edition competition
            why[t] = label
    # an axis the card splits with no value on a candidate's title: its basis is unknown (no preservation evidence)
    unknown = tuple(t for t in candidates if any(t in sp["unknown"] for sp in lk.card_of[rep]["splits"]))
    # an option's own card: a later month of another table bounds the unpublished months (a discontinued edition)
    held = PR.latest_month([lk.views[r["table_id"]] for r in lk.card_of[rep]["tables"]]) \
        if stage in ("ask_option", "ask_outside", "ask_period_option") else None
    got = PR.final_tables(rep, candidates, usable, lk.views, reading["conditions"], as_of, unknown=unknown,
                          held_elsewhere=held)
    return {"usable": usable, "not_usable": why, "trace": trace, **got}


def _candidates(rows: dict, rep: str) -> list[str]:
    return [t for t in rows if _basis(rows[t]) == _basis(rows[rep])
            and _other_words_no_time(rows[t]) == _other_words_no_time(rows[rep])]


def _frequency_decision(reading: dict, candidates: list[str], lk: Cards, as_of: str) -> dict:
    """{value, source, reason, valid, offered} of a statistic (policy ④, plan 56 order 5). ``offered``: the
    frequencies the candidates offer; ``valid``: those that fit the requested periods (``periods.frequency_fit``).
    With a quoted period and several fitting ones, ``decide_periods`` narrows ``valid`` to those a candidate covers
    the period in (``fitting`` keeps the fitting ones)."""
    offered = PR.offered_frequencies([lk.views[t] for t in candidates])
    periods = [c for c in reading["conditions"] if c["kind"] == "period"]
    valid = PR.valid_frequencies(periods, offered, as_of)
    quoted = list(dict.fromkeys(c["value"] for c in reading["conditions"] if c["kind"] == "frequency"))
    base = {"valid": valid, "offered": offered}
    if quoted:
        return {"value": quoted[0] if len(quoted) == 1 else None, "source": "question", "reason": "confirmed_quote",
                **base}
    if not valid:
        return {"value": None, "source": None, "reason": "not_fit", **base}
    if len(valid) == 1:
        return {"value": valid[0], "source": "catalog", "reason": "only_valid", **base}
    return {"value": None, "source": None, "reason": "needs_answer", **base}


def _with_frequency(reading: dict, frequency: str) -> dict:
    """The reading with the one valid frequency as a condition (source ``only_valid``), so the resolver uses it and
    never the period unit's or the longest one."""
    c = {"id": "F0", "kind": "frequency", "value": frequency, "quote": None, "source": "only_valid",
         "attrs": {"value": frequency, "anchor": None}}
    return {**reading, "conditions": reading["conditions"] + [c]}


def frequency_ask(statistics: list[str], frequencies: list[str]) -> dict:
    """A frequency question of ``condition_asks``: the valid frequencies as options."""
    return {"kind": "frequency", "statistics": list(statistics), "cause": "several_valid_frequencies",
            "input": PR.PRESETS["frequency"]["input"],
            "options": [{"option_id": f"o{i}", "label": o["label"], "value": o["value"], "quote": o["quote"],
                         "range": None} for i, o in enumerate(PR.frequency_options(frequencies), start=1)]}


def _meaning(row: dict) -> str:
    return row.get("meaning") or "range"


def _period_rows(d: dict, out: dict, polar: bool, ref_periods: dict | None = None) -> list[str]:
    """The period quotes of statistic ``d`` read as ranges. With an answer of contract .4 only rows meaning range are
    read; a row meaning same_as takes the referred statistics' range quotes when they are one and the same set
    (recorded in ``period_reference``); the meanings are kept in ``period_meaning``. Older answers: every quote.
    ``ref_periods`` (a re-judgment): the period rows of fixed statistics the answer refers to. A reference is read one
    step only (a referred statistic's own same_as is no range), so a chain or a cycle never loops."""
    sid = d["statistic"]
    rows = [r for r in out["periods"] if sid in r["for"]]
    if not polar:
        return [r["quote"] for r in rows]
    d["period_meaning"] = sorted({_meaning(r) for r in rows})
    quotes = [r["quote"] for r in rows if _meaning(r) == "range"]
    same = [r for r in rows if _meaning(r) == "same_as"]
    if same:
        refs = sorted({x for r in same for x in r.get("refers_to") or [] if x != sid})
        pool = list(out["periods"]) + [r for x in refs for r in (ref_periods or {}).get(x) or []]
        theirs = {x: tuple(sorted(r["quote"] for r in pool if x in r["for"] and _meaning(r) == "range"))
                  for x in refs}
        sets = {q for q in theirs.values() if q}
        if refs and len(sets) == 1 and all(theirs.values()):
            used = list(next(iter(sets)))
            d["period_reference"] = {"refers_to": refs, "quotes": used, "status": "used"}
            quotes += [q for q in used if q not in quotes]
        else:
            d["period_reference"] = {"refers_to": refs, "quotes": {x: list(q) for x, q in theirs.items()},
                                     "status": "ambiguous" if len(sets) > 1 else "none"}
    return quotes


def _referred_by(sid: str, row: dict) -> bool:
    """Whether period ``row`` of other statistics only says their period is that of ``sid`` (same_as)."""
    return _meaning(row) == "same_as" and sid in (row.get("refers_to") or [])


def _semantic_rows(d: dict, out: dict, ref_periods: dict | None = None) -> list[dict]:
    """Contract .8: the period rows that give statistic ``d`` its range: its own rows, and a same_as row's referred
    statistics' range rows when they are one and the same set (recorded in ``period_reference``, as for older
    contracts); the meanings are kept in ``period_meaning``. A reference is read one step only."""
    sid = d["statistic"]
    rows = [r for r in out["periods"] if sid in r["for"]]
    d["period_meaning"] = sorted({_meaning(r) for r in rows})
    own = [r for r in rows if _meaning(r) != "same_as"]
    same = [r for r in rows if _meaning(r) == "same_as"]
    if same:
        refs = sorted({x for r in same for x in r.get("refers_to") or [] if x != sid})
        pool = list(out["periods"]) + [r for x in refs for r in (ref_periods or {}).get(x) or []]
        theirs = {x: tuple(sorted({SP.structured_key(r, "periods") for r in pool if x in r["for"]
                                   and _meaning(r) == "range"})) for x in refs}
        sets = {q for q in theirs.values() if q}
        if refs and len(sets) == 1 and all(theirs.values()):
            used = [r for r in pool if refs[0] in r["for"] and _meaning(r) == "range"]
            d["period_reference"] = {"refers_to": refs, "quotes": [r["quote"] for r in used], "status": "used"}
            own += [dict(r, **{"for": [sid]}) for r in used]
        else:
            d["period_reference"] = {"refers_to": refs,
                                     "quotes": {x: list(dict.fromkeys(r["quote"] for r in pool if x in r["for"]
                                                                      and _meaning(r) == "range")) for x in refs},
                                     "structured": {x: list(q) for x, q in theirs.items()},
                                     "status": "ambiguous" if len(sets) > 1 else "none"}
    return own + same


def _semantic_reading(d: dict, question: str, out: dict, ref_periods: dict | None = None,
                      as_of: str | None = None) -> dict:
    """Contract .8: the conditions of statistic ``d`` from the model's structured rows (``periods.semantic``) and the
    user's condition answers (read as the answers always were). No word outside the quotes is restored: words the old
    reader finds there are recorded in ``period_audit`` only (they change no state)."""
    sid = d["statistic"]
    rows = _semantic_rows(d, out, ref_periods)
    frows = [r for r in out["frequencies"] if sid in r["for"]]
    reading = SP.read_rows([r for r in rows if _meaning(r) != "same_as"] or rows, frows, as_of=as_of)
    d["partial_periods"] = copy.deepcopy(reading.get("partial_periods") or [])
    if not [r for r in rows if _meaning(r) != "same_as"] and rows:
        # a same_as row with no usable reference reads no range (asked as before), never a missing row
        reading["problems"] = [p for p in reading["problems"] if p["code"] != "period_row_missing"]
    answers = d.get("condition_answers") or []
    if answers:  # the user's answers: read as before (a preset or the text the user typed), never the model's words
        got = PR.read_quotes([a["quote"] for a in answers if a["kind"] == "period"],
                             [a["quote"] for a in answers if a["kind"] == "frequency"])
        for c in got["conditions"]:
            reading["conditions"].append({**c, "id": f"{c['id'][0]}{len(reading['conditions']) + 1}"})
        reading["readings"] += got["readings"]
        reading["problems"] += got["problems"]
    stated = {"period": any(_meaning(r) != "not_stated" for r in rows),
              "frequency": any(r.get("value") != "not_stated" for r in frows)}
    if not all(stated.values()):
        missing = PR.unquoted_conditions(question, [r["quote"] for k in ("periods", "frequencies", "splitters",
                                                                         "calculations") for r in out[k]
                                                    if r.get("meaning") != "not_stated"
                                                    and r.get("value") != "not_stated"])
        audit = {k: missing[k] for k in ("period", "frequency") if not stated[k] and missing[k]}
        if audit:
            d["period_audit"] = {"words_outside_quotes": audit, "status": "recorded_only"}
    d["frequency_quotes"] = [{"quote": r["quote"], "for": list(r["for"]),
                              "frequency": r["value"] if r.get("value") in SP.UNITS else None,
                              "point_in_period": "end" if r.get("anchor") == "end" else None,
                              **({"problem": "frequency_unreadable"} if r.get("value") == "unreadable" else {})}
                             for r in frows if r.get("value") != "not_stated"]
    if answers:
        answer_rows = [{"quote": a["quote"], "for": [sid]} for a in answers if a["kind"] == "frequency"]
        d["frequency_quotes"] += [{**p, "source": "user_answer"}
                                  for p in PR.frequency_points(got, answer_rows)]
    return reading


def partial_period_problem(partials: list[dict], conditions: list[dict], as_of: str) -> str | None:
    """A period answer must complete a missing boundary without replacing the supplied boundary."""
    if not partials:
        return None
    spans = [PR.requested_span(c, as_of) for c in conditions if c["kind"] == "period"]
    if not spans:
        return None
    if len(spans) != 1 or spans[0] is None:
        return "partial_period_requires_one_span"
    if any(p.get(k) is not None and p[k] != spans[0][k] for p in partials for k in ("start", "end")):
        return "stated_period_boundary_changed"
    return None


def condition_reading(d: dict, question: str, out: dict, polar: bool = False, ref_periods: dict | None = None,
                      semantic: bool = False, as_of: str | None = None) -> dict:
    """``periods.read_quotes`` of statistic ``d`` with the condition rule ``ask_condition``: the model's quotes, the
    user's condition answers (``condition_answers``, read as quotes) and the words the reader finds outside every
    quote of every field, restored only when the question has one statistic. Writes ``frequency_quotes``, and
    ``period_quote_missing`` / ``frequency_quote_missing`` when words were found outside the quotes. ``semantic``
    (contract .8): the model's structured rows instead (``_semantic_reading``); the quote reader is for older
    answers."""
    if semantic:
        return _semantic_reading(d, question, out, ref_periods, as_of=as_of)
    sid = d["statistic"]
    answers = d.get("condition_answers") or []
    pq = _period_rows(d, out, polar, ref_periods) + [a["quote"] for a in answers if a["kind"] == "period"]
    stated_otherwise = polar and any(m != "range" for m in d.get("period_meaning") or [])
    frows = [{"quote": r["quote"], "for": list(r["for"])} for r in out["frequencies"] if sid in r["for"]] + \
        [{"quote": a["quote"], "for": [sid], "source": "user_answer"} for a in answers if a["kind"] == "frequency"]
    reading = PR.read_quotes(pq, [r["quote"] for r in frows])
    has = {k: any(c["kind"] == k for c in reading["conditions"]) for k in ("period", "frequency")}
    restored = False
    if not reading["problems"] and not all(has.values()):
        # a word the model quoted in any field was read by it as that field: never moved (plan 36)
        missing = PR.unquoted_conditions(question, [r["quote"] for k in ("periods", "frequencies", "splitters",
                                                                         "calculations") for r in out[k]])
        single = len(out["statistics"]) == 1
        # another statistic's row that only says its period is this statistic's (same_as) quotes no period for
        # this one: with nothing else, the period is the user's to give, asked once for the linked statistics. A
        # statistic a same_as row links reads only the rows of its own linked statistics: a period the model gave
        # an unlinked statistic is that statistic's (owner 11-01 13:17)
        links = _same_as_links(out)
        linked = {x for x, root in links.items() if root == links[sid]} if sid in links else None
        others = any(sid not in r["for"] and not _referred_by(sid, r)
                     and (linked is None or bool(set(r["for"]) & linked)) for r in out["periods"])
        if not has["period"] and not stated_otherwise and (missing["period"] or others):
            d["period_quote_missing"] = {"quotes": missing["period"], "quoted_for_other_statistics": others,
                                         "restored": single and bool(missing["period"])}
            if d["period_quote_missing"]["restored"]:
                pq += missing["period"]
                restored = True
        if not has["frequency"] and missing["frequency"]:
            d["frequency_quote_missing"] = {"quotes": missing["frequency"], "restored": single}
            if single:
                frows += [{"quote": q, "for": [sid], "source": "code_restored"} for q in missing["frequency"]]
                restored = True
        if restored:
            reading = PR.read_quotes(pq, [r["quote"] for r in frows])
    points = PR.frequency_points(reading, frows)
    sources = {r["quote"]: r["source"] for r in frows if r.get("source")}
    for row in points:
        if row["quote"] in sources:
            row["source"] = sources[row["quote"]]
    d["frequency_quotes"] = points
    return reading


def failure_kinds(d: dict) -> list[str]:
    """Where the statistic ended, by reason (record only): internal interpretation (the model's structuring, one
    recovery may correct it), unsupported expression, data not covered, the user's to decide (a condition question)
    and the existing checks (focus, table, basis and the others), never mixed in one kind."""
    kinds = []
    for r in d.get("reasons") or []:
        kinds.append(next((k for k, codes in FAILURE_KINDS.items() if r["code"] in codes), "focus_table_or_other"))
    if d.get("condition_asks"):
        kinds.append("user_undecided")
    return sorted(set(kinds))


def _problem_cause(problems: list[dict]) -> str:
    """The cause of a quote the reader cannot read: nothing read is a range the words alone do not give
    (``unreadable_range``); a part left unread is an expression the first stage does not support. Contract .8: the
    model's failure to structure its own rows only is ``interpretation_failed`` (one recovery may correct it); an
    excluded period with it stays an unsupported expression."""
    if problems and all(p["code"] in SEMANTIC_INTERNAL for p in problems):
        return "interpretation_failed"
    return "unreadable_range" if any(p["code"] == "period_unresolved" for p in problems) else "unsupported_expression"


def period_ask(d: dict, quote: str, rep: str, candidates: list[str], reading: dict, freq: dict, lk: Cards,
               as_of: str, cause: str = "user_omitted", stage: str = "period_option") -> dict:
    """A period question of ``condition_asks`` (``cause``: ``user_omitted``, or ``unreadable_range`` when the model
    said the user did not decide the range or a same-range reference has none or several): the presets' windows a
    candidate can cover in a frequency the statistic can take (the quoted one, else each valid one), each with the
    frequencies that cover it; a window no candidate covers is not offered. The user may type another period
    (``input``)."""
    frequencies = [freq["value"]] if freq["value"] else freq["valid"]
    options = []
    for o in PR.period_options(as_of):
        try_reading = PR.read_quotes([o["quote"]], [])
        if partial_period_problem(reading.get("partial_periods") or [], try_reading["conditions"], as_of):
            continue
        covered, spans, empty = [], {}, []
        for f in frequencies:
            got = _select_periods(quote, rep, candidates, d["item_evidence"], _with_frequency(try_reading, f), lk,
                                  as_of, stage, d["retention"])
            if got["status"] == "selected":
                covered.append(f)
                rows = [x for t in got["tables"] for x in t.get("period") or []]
                if rows:  # what choosing it requests in that frequency (policy ②: the real dates are shown)
                    spans[f] = {"start": min(x["start"] for x in rows), "end": max(x["end"] for x in rows)}
                if _nothing_now(got):
                    empty.append(f)
        if covered:
            # the window stays offered (latest_unpublished keeps the table); one with nothing to fetch in the current
            # catalog says so and is never shown as a window with data (owner 11-02)
            options.append({"option_id": f"o{len(options) + 1}", "label": _window_label(o, spans, empty),
                            "value": o["quote"], "quote": o["quote"], "range": o["range"], "frequencies": covered,
                            "ranges": spans, **({"no_current_data": empty} if empty else {})})
    return {"kind": "period", "statistics": [d["statistic"]], "cause": cause,
            "input": PR.PRESETS["period"]["input"], "options": options,
            **({"partial_periods": copy.deepcopy(reading["partial_periods"])}
               if reading.get("partial_periods") else {})}


def _span_text(span: dict, frequency: str) -> str:
    """A requested span (months) in the words of its frequency (2022-01~2026-10, 2022년 1분기~2026년 4분기, ...)."""
    (y0, m0), (y1, m1) = ((int(x[:4]), int(x[5:7])) for x in (span["start"], span["end"]))
    if frequency == "M":
        return f"{span['start']}~{span['end']}"
    if frequency == "Y":
        return f"{y0}년~{y1}년"
    if frequency == "Q":
        return f"{y0}년 {(m0 - 1) // 3 + 1}분기~{y1}년 {(m1 - 1) // 3 + 1}분기"
    half = ("상반기", "하반기")
    return f"{y0}년 {half[(m0 - 1) // 6]}~{y1}년 {half[(m1 - 1) // 6]}"


NO_DATA_WORDS = "현재 카탈로그상 조회 자료 없음(최신 수록 확인 필요)"


def _nothing_now(got: dict) -> bool:
    """Every table of a selected window holds the window only as latest_unpublished: nothing to fetch now."""
    tables = got.get("tables") or []
    if not tables:
        return False
    for t in tables:
        want = {m for r in t.get("period") or [] for m in range(*(lambda a, b: (a, b + 1))(*_months_of(r)))}
        later = {m for r in t.get("latest_unpublished") or [] for m in range(*(lambda a, b: (a, b + 1))(*_months_of(r)))}
        if not want or not want <= later:
            return False
    return True


def _window_label(option: dict, spans: dict, empty: list | None = None) -> str:
    """A period option's label with the range choosing it requests: in the decided frequency, or in each frequency
    the statistic can still take (13-4 review: the label is the range that is used, never the as_of months alone).
    A frequency with nothing to fetch now says so (``empty``)."""
    empty = empty or []
    if not spans:
        return option["label"] + (f" · {NO_DATA_WORDS}" if empty else "")
    if len(spans) == 1:
        ((f, span),) = spans.items()
        return f"{option['quote']} ({_span_text(span, f)})" + (f" · {NO_DATA_WORDS}" if f in empty else "")
    words = PR.PRESETS["frequency"]["labels"]
    if empty and set(empty) >= set(spans):
        return (f"{option['quote']} (" + " / ".join(f"{words[f]} {_span_text(spans[f], f)}" for f in spans) + ")"
                + f" · {NO_DATA_WORDS}")
    return f"{option['quote']} (" + " / ".join(
        f"{words[f]} {_span_text(spans[f], f)}" + (" · 현재 조회 자료 없음" if f in empty else "") for f in spans) + ")"


def _months_of(row: dict) -> tuple[int, int]:
    return PR.P.month(row["start"]), PR.P.month(row["end"])


def _needed(method: str | None, a: int, b: int, source: str) -> tuple[int, int]:
    """The source months one target period needs by its method: the end observation only for period_end (the last
    source period of the target), every source period otherwise (sum, period_average, weighted, unknown)."""
    if method == "period_end":
        step = {"M": 1, "Q": 3, "H": 6}[source]
        return b - step + 1, b
    return a, b


def _selected_names(d: dict, tables: list[str], lk: Cards) -> dict[str, set[str]]:
    """Table -> the names of the values the decision chose for the statistic (``item_evidence``) on it."""
    out = {}
    for t in tables:
        held = PR.item_names(lk.views[t])
        out[t] = {name for name in (lk.resolve_item(t, text)["name"] for text in d["item_evidence"])
                  if PR.item_key(name) in held}
    return out


def _shown_series(t: str, lk: Cards) -> set[str]:
    return {it["name"] for it in card_values(lk.card_of[t]) if t in it["tables"]}


def _quote_level(text: str, tables: list[str], chosen: dict[str, set[str]], lk: Cards) -> str | None:
    """Where one quote sits (``EVIDENCE_LEVELS``); None: not on the card at all (an output error)."""
    got = []
    for t in tables:
        kinds = lk.kinds_of(t, text)
        if "item" in kinds and lk.resolve_item(t, text)["name"] in chosen[t]:
            got.append("selected_series_name")
        if kinds & {"title", "bracket"}:
            got.append("selected_table_title")
        if text in lk.views[t].units():
            got.append("unit_only")
    others = [r["table_id"] for r in lk.card_of[tables[0]]["tables"]]
    if any(lk.kinds_of(t, text) or text in lk.views[t].units() for t in others):
        got.append("not_linked")
    return max(got, key=_LEVEL_RANK.get) if got else None


def _evidence_level(evidence: list[dict], tables: list[str], chosen: dict, lk: Cards) -> tuple[str, list[str]]:
    """(the best level of the quotes, the quotes not on the card)."""
    levels = [(e["text"], _quote_level(e["text"], tables, chosen, lk)) for e in evidence]
    off = [text for text, level in levels if level is None]
    found = [level for _, level in levels if level]
    if not found or off:
        return "none", off
    return max(found, key=_LEVEL_RANK.get), off


def _series_basis(basis: str, level: str, tables: list[str], chosen: dict, lk: Cards) -> str | None:
    """Whether the source basis the model gave holds for the series chosen (user 2026-10-31 20:10): from the card's
    structured facts only (no wording rule). With a basis axis (``BASIS_AXIS_VALUE``): the value of a chosen series
    on the axis must be it (``basis_mismatch``); a chosen series without one in a table whose values mix two or more
    is not told apart by a title (``basis_not_specific_to_series``); a table title of one other value is a mismatch.
    Without an axis (flow, ratio): a title quote holds only for a table showing one series or with every shown series
    chosen; otherwise a chosen series name is needed. None: it holds."""
    if basis in BASIS_AXIS_VALUE:
        axis, value = BASIS_AXIS_VALUE[basis]
        for t in tables:
            own = {v for n in chosen[t] for v in basis_values(n, axis)}
            if own:
                if value not in own:
                    return "basis_mismatch"
                continue
            if len((lk.row[t].get("item_basis") or {}).get(axis, {}).get("values") or []) > 1:
                return "basis_not_specific_to_series"
            title = _basis(lk.row[t]).get(axis) or ()
            if title and value not in title:
                return "basis_mismatch"
        return None
    if level == "selected_series_name":
        return None
    for t in tables:
        shown = _shown_series(t, lk)
        if len(shown) > 1 and not shown <= chosen[t]:
            return "basis_not_specific_to_series"
    return None


def _days(a: int, b: int, source: str) -> list[int]:
    """The calendar days of each source period from month ``a`` to month ``b`` (leap years counted)."""
    import calendar

    step = {"M": 1, "Q": 3, "H": 6}[source]
    out = []
    for start in range(a, b + 1, step):
        out.append(sum(calendar.monthrange(m // 12, m % 12 + 1)[1] for m in range(start, min(start + step, b + 1))))
    return out


def method_check(d: dict, row: dict | None, ids: list[str], lk: Cards) -> dict:
    """The aggregation method of the model's ``row`` checked on tables ``ids`` (plan 56 order 6-3c): where each quote
    sits, the method and basis pair, the basis of the chosen series, the weight series and the average basis. ``why``
    None: verified; else why it is unconfirmed (``AGGREGATION_WHYS``). Shared by a kept candidate and an ask option."""
    method, basis = (row or {}).get("method") or "unknown", (row or {}).get("source_basis") or "unknown"
    evidence = list((row or {}).get("evidence") or [])
    chosen = _selected_names(d, ids, lk)
    level, off_card = _evidence_level(evidence, ids, chosen, lk)
    average_basis = (row or {}).get("average_basis")
    average_basis = average_basis if average_basis in AVERAGE_BASES else "unknown"  # absent before contract .5
    average_evidence = list((row or {}).get("average_evidence") or [])
    average_level, off_average = _evidence_level(average_evidence, ids, chosen, lk)
    names = {PR.item_key(n) for t in ids for n in PR.item_names(lk.views[t])}
    missing_series = [w for w in (row or {}).get("weights") or [] if PR.item_key(w) not in names] \
        if method == "weighted" else []
    if method == "weighted" and not (row or {}).get("weights"):
        missing_series = ["(none named)"]
    why = ("method_unknown" if "unknown" in (method, basis) else
           "basis_mismatch" if (method, basis) not in METHOD_PAIRS else
           "no_evidence" if level == "none" else
           "evidence_unit_only" if level == "unit_only" else
           "evidence_not_linked" if level not in LINKED_LEVELS else
           _series_basis(basis, level, ids, chosen, lk) or
           ("required_series_missing" if missing_series else None))
    if why is None and method == "period_average":
        why = ("averaging_basis_unstated" if average_basis == "unknown" or average_level not in LINKED_LEVELS else
               "day_count_unavailable" if average_basis in ("business_day", "observation_day") else None)
    return {"why": why, "evidence": evidence, "level": level, "off_card": off_card + off_average,
            "average_basis": average_basis, "average_evidence": average_evidence, "average_level": average_level,
            "missing_series": missing_series}


def aggregation_candidate(d: dict, quote: str, rep: str, candidates: list[str], reading: dict, freq: dict,
                          lk: Cards, as_of: str, rows: list[dict]) -> dict | None:
    """A quoted frequency no candidate gives, made from a finer one the candidates give (policy ④ supplement, users
    2026-10-31 17:56 and 18:28 UTC; contract .4 answers only): the candidate is kept and ``aggregation`` records three
    layers apart (bundle 4): the method's evidence level and status, the availability by the metadata per target
    period by the method's needs, and the completeness after a fetch (not checked here). The model proposes the
    method, the source basis, the average basis and their quotes; code checks only where each quote sits on the card
    (``EVIDENCE_LEVELS``), that it is linked to the tables and series chosen, the method and basis pair, the basis of
    the chosen series and the data the method needs (plan 56 order 6-3c). A verified method is still no aggregation
    done: nothing is computed here and the statistic is never complete. None when the frequency cannot be made
    structurally, or no candidate covers the period in the finer one (the reason stays as it is)."""
    f = freq["value"]
    sources = [g for g in freq["offered"] if g in FINER.get(f, ())]
    if not sources:
        return None
    row = next((r for r in rows if r.get("requested_frequency") == f), None)
    g = row["source_frequency"] if row and row.get("source_frequency") in sources else sources[0]
    base = {**reading, "conditions": [c for c in reading["conditions"] if c["kind"] != "frequency"]}
    got = _select_periods(quote, rep, candidates, d["item_evidence"], _with_frequency(base, g), lk, as_of,
                          "aggregation_source", d["retention"])
    if got["status"] != "selected":
        return None
    tables = got["tables"]
    covered = {m for t in tables for c in got["results"][t["table_id"]].get("covered") or []
               for m in range(*(lambda a, b: (a, b + 1))(*_months_of(c)))}
    requested = [_months_of(p) for t in tables for p in t["period"] or []]
    lo, hi = min(a for a, _ in requested), max(b for _, b in requested)
    method = (row or {}).get("method") or "unknown"
    basis = (row or {}).get("source_basis") or "unknown"
    step = {"Q": 3, "H": 6, "Y": 12}[f]
    periods = []
    for start in range(lo - lo % step, hi + 1, step):
        a, b = max(start, lo), min(start + step - 1, hi)
        na, nb = _needed(method, start, start + step - 1, g)
        periods.append({"start": PR.P.month_text(a), "end": PR.P.month_text(b),
                        "needed": {"start": PR.P.month_text(na), "end": PR.P.month_text(nb)},
                        "status": "available" if all(m in covered for m in range(na, nb + 1)) else "short"})
    ids = [t["table_id"] for t in tables]
    got = method_check(d, row, ids, lk)
    evidence, level, average_basis = got["evidence"], got["level"], got["average_basis"]
    average_evidence, average_level, missing_series, why = (got["average_evidence"], got["average_level"],
                                                            got["missing_series"], got["why"])
    if got["off_card"]:  # a model output error: never a missing frequency
        d.setdefault("output_errors", []).append({"code": "aggregation_evidence_not_on_card",
                                                  "texts": got["off_card"], "recovery": dict(POLARITY_RECOVERY)})
    calculation = None
    if why is None:  # the kind of computation only: nothing is computed by A
        calculation = {"kind": {"sum": "sum", "period_end": "period_end", "weighted": "weighted_mean"}.get(method),
                       "computed": False}
        if method == "period_average":
            calculation["kind"] = "calendar_day_weighted_mean" if average_basis == "calendar_day" else "simple_mean"
            if average_basis == "calendar_day":  # sum(average x days of the source period) / days of the target
                calculation["periods"] = [{"start": p["start"], "end": p["end"],
                                           "days": _days(PR.P.month(p["needed"]["start"]),
                                                         PR.P.month(p["needed"]["end"]), g)} for p in periods]
                for c in calculation["periods"]:
                    c["total_days"] = sum(c["days"])
        if method == "weighted":
            calculation["weights"] = list((row or {}).get("weights") or [])
    return {"status": "aggregation_needed", "requested": f, "source": g, "method": method, "source_basis": basis,
            "method_status": "unconfirmed" if why else "verified", "why": why, "evidence_level": level,
            "evidence": evidence, "average_basis": average_basis, "average_evidence": average_evidence,
            "average_evidence_level": average_level, "calculation": calculation,
            "weights": list((row or {}).get("weights") or []),
            "meta_availability": {"periods": periods, "gaps_unchecked": True,
                                  "required_series_missing": missing_series},
            "fetched_completeness": "not_checked", "source_tables": tables,
            "source_range": {"start": PR.P.month_text(lo), "end": PR.P.month_text(hi)},
            "model_row": row is not None}


def same_table_periods(D: dict, out: dict) -> None:
    """Statistics decided on the same final tables with different periods (plan 56 order 11): when their period
    quotes are different parts of the question the user asked them apart (``same_table_period_differs``, a warning:
    the link is not supported, never merged); when one statistic's quote is part of another's, the model wrote one
    period differently per row (``period_rows_inconsistent``, an output error the one recovery may correct)."""
    groups: dict[tuple, list[dict]] = {}
    for d in D.values():
        if not d["reasons"] and d["final_tables"]:
            groups.setdefault(tuple(sorted(r["table_id"] for r in d["final_tables"])), []).append(d)
    for members in groups.values():
        spans = {json.dumps([r.get("period") for r in d["final_tables"]], sort_keys=True) for d in members}
        if len(members) < 2 or len(spans) < 2:
            continue
        quotes = [{r["quote"] for r in out["periods"] if d["statistic"] in r["for"]} for d in members]
        nested = any(a != b and any(x in y or y in x for x in a for y in b) for a in quotes for b in quotes)
        for d in members:
            if nested:
                d["reasons"].append({"code": "period_rows_inconsistent",
                                     "detail": {"statistics": [m["statistic"] for m in members],
                                                "quotes": [sorted(q) for q in quotes]}})
            else:
                d["warnings"].append({"code": "same_table_period_differs",
                                      "tables": [r["table_id"] for r in d["final_tables"]],
                                      "statistics": [m["statistic"] for m in members]})


def stage_of(d: dict) -> str:
    """The decision's stage (``A_STAGES``)."""
    if d["outcome"] in ("selected", "selected_unverified"):
        return "selected"
    if d["outcome"] in ("ask", "ask_unverified", CONDITION_OUTCOME):
        return "waiting_answer"
    if d["outcome"] == AGGREGATION_OUTCOME:
        return "blocked_contract"  # to be aggregated: no aggregation is linked yet
    if any(r["code"] == "rejudge_pending" for r in d["reasons"]):
        return "rejudge_pending"
    return "failed"


def annotate(d: dict, out: dict) -> None:
    """Each warning's kind, statistic, scope and stage; each final table's period hint and phrase (plan 56 order 11:
    kept in the record and AView only, never in a request to B)."""
    d["a_stage"] = stage_of(d)
    # the warnings in the rows of bundle 11 (the warnings themselves stay as written)
    d["warning_rows"] = [{"kind": WARNING_KINDS.get(w["code"], w["code"]), "code": w["code"],
                          "statistic": d["statistic"], "table": w.get("table") or w.get("tables"),
                          "stage": w.get("stage") or "check",
                          "scope": "table" if w.get("table") or w.get("tables") else "statistic", "detail": dict(w)}
                         for w in d["warnings"]]
    quotes = [r["quote"] for r in out["periods"] if d["statistic"] in r["for"]]
    # a period word of the question the check restored for this statistic (period_quote_missing, restored: one
    # statistic, the words found outside every quote) is the question's: quoted with its words; the restoring stays
    # recorded in period_quote_missing (owner 11-01 13:39). Nothing else becomes quoted
    restored = d.get("period_quote_missing") or {}
    if restored.get("restored"):
        quotes += [q for q in restored.get("quotes") or [] if q not in quotes]
    answered = [a["quote"] for a in d.get("condition_answers") or [] if a["kind"] == "period"]
    for r in d["final_tables"]:
        rows = r.get("period") or []
        r["period_hint"] = {"frequency": r.get("frequency"), "start": rows[0]["start"] if rows else None,
                            "end": rows[-1]["end"] if rows else None, "share": [dict(x) for x in rows]}
        r["period_phrase"] = {"text": " / ".join(answered or quotes) or None,
                              "source": "user_answer" if answered else "quoted" if quotes else "default"}


def _same_as_links(out: dict) -> dict[str, str]:
    """statistic -> the root of the statistics a same_as period row links (each with every one it refers to)."""
    root: dict[str, str] = {}

    def find(x: str) -> str:
        while root.setdefault(x, x) != x:
            x = root[x]
        return x

    for r in out["periods"]:
        if _meaning(r) == "same_as":
            for x in r["for"]:
                for y in r.get("refers_to") or []:
                    a, b = find(x), find(y)
                    if a != b:
                        root[max(a, b)] = min(a, b)
    return {x: find(x) for x in list(root)}


def group_condition_asks(D: dict, out: dict) -> None:
    """Statistics that share a quoted period or frequency row and get the same question (kind, cause, options) are
    asked once: each of their asks names all of them. Statistics a same_as row links whose periods are both still to
    be asked get one period question when its options are the same (one answer gives them one period, owner 11-01
    12:26); with other options each keeps its own. A statistic sharing no row keeps its own question."""
    def rows_of(sid: str) -> tuple:
        # a contract .8 row saying the user stated nothing is no shared condition: read as no row (as before)
        return (tuple(i for i, r in enumerate(out["periods"]) if sid in r["for"] and r.get("meaning") != "not_stated"),
                tuple(i for i, r in enumerate(out["frequencies"]) if sid in r["for"]
                      and r.get("value") != "not_stated"))

    order = list(D)
    links = _same_as_links(out)
    linked = {}
    for sid in D:
        linked.setdefault(links.get(sid, sid), []).append(sid)
    groups: dict[tuple, list[dict]] = {}
    for sid, d in D.items():
        for a in d.get("condition_asks") or []:
            key = rows_of(sid)
            options = json.dumps([(o["value"], o.get("range")) for o in a["options"]], ensure_ascii=False)
            if a["kind"] == "period" and len(linked[links.get(sid, sid)]) > 1:
                sig = ("period", "same_as", links.get(sid, sid), options)
            else:
                sig = (a["kind"], a["cause"], key, options) if any(key) else (a["kind"], sid)
            groups.setdefault(sig, []).append(a)
    for asks in groups.values():
        stats = sorted({s for a in asks for s in a["statistics"]}, key=order.index)
        for a in asks:
            a["statistics"] = stats


def _end_point_status(d: dict, lk: Cards, offered: list[str]) -> list[dict]:
    """One row per quoted end point (월말, 분기말, 연말): table_satisfies when every final table is a stock at the end
    of the period (말잔), extraction_required when no final table and only a finer frequency is offered (the first
    stage does not extract the end month), else unverified (no evidence either way). Record only."""
    rows = []
    order = PR.frequency_order()
    for q in d.get("frequency_quotes") or []:
        if q.get("point_in_period") != "end" or not q.get("frequency"):
            continue
        tables = [r["table_id"] for r in d.get("final_tables") or []]
        if tables and all("말잔" in (_basis(lk.row[t]).get("stock") or ()) for t in tables):
            status = "table_satisfies"
        elif not tables and q["frequency"] not in offered and any(
                order.index(f) > order.index(q["frequency"]) for f in offered):
            status = "extraction_required"
        else:
            status = "unverified"
        rows.append({"quote": q["quote"], "frequency": q["frequency"], "status": status})
    return rows


def decide_periods(d: dict, quote: str, rep: str, reading: dict, lk: Cards, as_of: str, stage: str,
                   context: dict | None = None) -> bool:
    """Check steps 4 and 5 on a decided table ``rep`` of the statistic record ``d``: the period and edition
    candidates (``_candidates``), the final tables among them (``_select_periods``) and the recheck of every final
    table. The check and applied answers (``answers``) both call this, so a table decided either way passes the same
    checks in the same order. ``reading``: ``periods.read_quotes`` of the statistic; ``stage`` names the trace rows.
    Writes ``period``, ``checks_done``, ``verification``, ``reasons``, ``final_tables`` and ``corrections`` of ``d``.
    True when the final tables are set, False after a failure reason was added or a condition question was made.
    ``context`` ({question, out}): with the condition rule ``ask_condition`` the reading is ``condition_reading``
    (the caller's ``reading`` is not used) and a missing period or frequency is asked, never defaulted."""
    rows = {r["table_id"]: r for r in lk.card_of[rep]["tables"]}
    asking = PR.condition_rule() == "ask_condition" and context is not None
    polar = bool(asking and context.get("polar"))
    if asking:
        reading = condition_reading(d, context["question"], context["out"], polar, context.get("ref_periods"),
                                    bool(context.get("semantic")), as_of=as_of)
        d["condition_asks"] = []
    d["period"] = {"readings": reading["readings"]}
    if why := partial_period_problem(reading.get("partial_periods") or [], reading["conditions"], as_of):
        reading["problems"].append({"code": "period_answer_conflict", "why": why,
                                    "quote": " / ".join(p["quote"] for p in reading["partial_periods"])})
    if reading["problems"]:
        for p in reading["problems"]:
            d["reasons"].append({"code": p["code"], "detail": p})
        if asking:
            d["period"]["cause"] = _problem_cause(reading["problems"])
        return False
    if asking:
        for key, code in (("period_quote_missing", "period"), ("frequency_quote_missing", "frequency")):
            if (d.get(key) or {}).get("restored") is False:  # whose period it is is not known: never asked
                d["period"]["cause"] = "extraction_missing"
                d["reasons"].append({"code": key, "detail": {**d[key], "cause": "extraction_missing"}})
        if d["reasons"]:
            return False
    candidates = _candidates(rows, rep)
    d["checks_done"].append("periods")
    if asking:  # policy ②④ (plan 56 order 5): the frequency first; a missing period or frequency is asked
        freq = d["frequency"] = _frequency_decision(reading, candidates, lk, as_of)
        if freq["reason"] == "confirmed_quote" and freq["value"] and any(
                q.get("source") == "user_answer" and q.get("frequency") == freq["value"]
                for q in d.get("frequency_quotes") or []):
            freq["source"] = "user_answer"  # the frequency the user answered for this statistic (owner 11-01 13:39)
        omitted = not any(c["kind"] == "period" for c in reading["conditions"])
        meanings = d.get("period_meaning") or []
        undecided = polar and omitted and ("user_undecided" in meanings or
                                           (d.get("period_reference") or {}).get("status") in ("none", "ambiguous"))
        latest = polar and omitted and not undecided and "latest_available" in meanings
        if polar and freq["reason"] == "confirmed_quote" and freq["value"] not in freq["offered"]:
            rows_ = [r for r in context["out"].get("aggregations") or [] if d["statistic"] in r["for"]]
            agg = aggregation_candidate(d, quote, rep, candidates, reading, freq, lk, as_of, rows_)
            if agg is not None:
                d["frequency"]["aggregation"] = agg
                d["period"].update({"candidates": candidates, "excluded": [t for t in rows if t not in candidates],
                                    "status": "aggregation_needed", "default_applied": False,
                                    "default_reason": None})
                return False
        if latest and freq["value"]:  # the latest period the candidates hold in the decided frequency
            q = PR.latest_quote([lk.views[t] for t in candidates], freq["value"])
            if q is not None:
                reading = _with_frequency(PR.read_quotes([q], []), freq["value"])
                d["period"]["latest"] = {"frequency": freq["value"], "quote": q}
                omitted = False
        if freq["reason"] == "not_fit":
            d["period"].update({"candidates": candidates, "excluded": [t for t in rows if t not in candidates],
                                "default_applied": False, "default_reason": None})
            d["reasons"].append({"code": "frequency_not_fit", "detail": {"offered": freq["offered"]}})
            return False
        if omitted and latest:  # the frequency is still to be asked; the period is the latest one, never asked
            pass
        elif omitted:
            cause = "unreadable_range" if undecided else "user_omitted"
            d["period"]["cause"] = cause
            d["condition_asks"].append(period_ask(d, quote, rep, candidates, reading, freq, lk, as_of, cause))
        elif freq["reason"] == "needs_answer":
            # a frequency option must be able to give the quoted period (an option that cannot satisfy the request
            # is never offered): the fitting frequencies a candidate covers the period in
            tried = {f: _select_periods(quote, rep, candidates, d["item_evidence"], _with_frequency(reading, f), lk,
                                        as_of, "frequency_option", d["retention"]) for f in freq["valid"]}
            covering = [f for f, got in tried.items() if got["status"] == "selected"]
            freq.update(fitting=list(freq["valid"]), valid=covering)
            if not covering:  # no fitting frequency gives the period: nothing to ask, the period's reason stands
                freq["reason"] = "not_fit"
                d["period"].update({"candidates": candidates, "excluded": [t for t in rows if t not in candidates],
                                    "default_applied": False, "default_reason": None})
                codes = {got["reason"] for got in tried.values()}
                d["reasons"].append({"code": codes.pop() if len(codes) == 1 else "period_not_covered",
                                     "detail": {"by_frequency": {f: {"reason": got["reason"], "detail": got["detail"]}
                                                                 for f, got in tried.items()}}})
                return False
            if len(covering) == 1:
                freq.update(value=covering[0], source="catalog", reason="only_valid")
        if freq["reason"] == "needs_answer":
            d["condition_asks"].append(frequency_ask([d["statistic"]], freq["valid"]))
        if d["condition_asks"]:
            d["period"].update({"candidates": candidates, "excluded": [t for t in rows if t not in candidates],
                                "status": "needs_answer", "default_applied": False, "default_reason": None})
            return False
        if freq["reason"] == "only_valid" and any(f != freq["value"] for f in freq["offered"]):
            reading = _with_frequency(reading, freq["value"])
    got = _select_periods(quote, rep, candidates, d["item_evidence"], reading, lk, as_of, stage, d["retention"])
    d["verification"] += got.pop("trace")
    quoted = any(c["kind"] == "period" for c in reading["conditions"])
    d["period"].update({"candidates": candidates, "excluded": [t for t in rows if t not in candidates],
                        "default_applied": not quoted,
                        "default_reason": None if quoted else
                        ("only base-year marks were quoted" if any(r["kind"] == "period" for r in reading["readings"])
                         else "no period quoted"), **got})
    if got["status"] != "selected":
        d["reasons"].append({"code": got["reason"], "detail": got.get("detail")})
        if "frequency" in d:
            d["end_point_status"] = _end_point_status(d, lk, d["frequency"]["offered"])
        return False
    # 5. recheck every final table
    d["checks_done"].append("final_recheck")
    problems = []
    for row in got["tables"]:
        t = row["table_id"]
        again = verify_table(quote, t, d["item_evidence"], lk, d["retention"])
        d["verification"].append({**_trace("final_table", again), "period": row["period"],
                                  "frequency": row["frequency"]})
        if not again["ok"]:
            problems.append(f"{t}: {again['code']}")
        if _basis(lk.row[t]) != _basis(lk.row[rep]):
            problems.append(f"{t}: basis differs")
        if t not in got["usable"]:
            problems.append(f"{t}: content not verified")
    if problems:
        d["reasons"].append({"code": "final_recheck_failed", "detail": problems})
        return False
    d["final_tables"] = got["tables"]
    if [r["table_id"] for r in got["tables"]] != [rep]:
        # policy ⑦ (plan 56 order 7): which edition replaced or was stitched to the representative, and on what;
        # final_tables_by_period stays the last correction, as before
        others = [r for r in got["tables"] if r["table_id"] != rep]
        d["corrections"].append({"code": "edition_stitched" if len(got["tables"]) > 1 else "edition_replaced",
                                 "from": rep, "to": [r["table_id"] for r in got["tables"]],
                                 "reason": "representative_does_not_cover",
                                 "relation_status": {r["table_id"]: r.get("relation_status") for r in others}})
        d["corrections"].append({"code": "final_tables_by_period", "from": rep,
                                 "to": [r["table_id"] for r in got["tables"]]})
        unverified = [r for r in others if r.get("relation_status") == "unverified"]
        if unverified:
            d["warnings"].append({"code": "edition_relation_unverified", "tables": [r["table_id"] for r in unverified],
                                  "spans": {r["table_id"]: r["period"] for r in unverified}})
    if "frequency" in d:
        d["end_point_status"] = _end_point_status(d, lk, d["frequency"]["offered"])
    return True


USABLE = ("representative_checked", "cited_values_held", "same_value_names")


def _kept(quote: str, rep: str, t: str, item_evidence: list[str], lk: "Cards", retention: dict | None) -> tuple:
    """Whether candidate ``t`` keeps what the model cited for ``rep`` (the rule of the period and edition choice,
    shared): the fact check (``verify_table``, cited values and their retention), then the representative itself,
    the cited values held, or, with no cited value, the same value names. (label, check): a label in ``USABLE``, or
    why not (``content_unverified``, ``retention_unverified``, ``value_names_differ``, ...)."""
    got = verify_table(quote, t, item_evidence, lk, retention)
    if not got["ok"]:
        return got["code"], got
    if t == rep:
        return "representative_checked", got
    if item_evidence:
        return "cited_values_held", got
    if PR.item_names(lk.views[t]) == PR.item_names(lk.views[rep]):
        return "same_value_names", got
    return "value_names_differ", got


def _requested_frequency(out: dict, sid: str) -> str | None:
    """The one frequency the model quoted for ``sid`` and code reads (None: none quoted, unreadable or several). Rows
    of contract .8 give their value."""
    rows = [r for r in out["frequencies"] if sid in r["for"]]
    if any("value" in r for r in rows):
        values = {r["value"] for r in rows}
        return next(iter(values)) if len(values) == 1 and next(iter(values)) in SP.UNITS else None
    quotes = [r["quote"] for r in rows]
    if not quotes:
        return None
    got = PR.read_quotes([], quotes)
    values = {c["value"] for c in got["conditions"] if c["kind"] == "frequency"}
    return next(iter(values)) if not got["problems"] and len(values) == 1 else None


def _stock_ask_end_anchor(out: dict, sid: str, axis: str, opts: list[tuple], question: str) -> dict | None:
    """The model's valid end-point meaning can settle a stock ask it contradicted.

    This is not a text rule or a default for stock tables. Only an explicit,
    single structured frequency row and an already validated closing/average
    pair qualify. Any stock polarity row remains authoritative, including a
    permitted or unresolved value; an end point must not replace explicit
    average-balance semantics. The ordinary final checks still apply.
    """
    if axis != "stock" or len(opts) != 2 or {value for value, _ in opts} != {"말잔", "평잔"}:
        return None
    if any(row["axis"] == "stock" and sid in row["for"] for row in out["splitters"]):
        return None
    rows = [row for row in out["frequencies"] if sid in row["for"]]
    if len(rows) != 1:
        return None
    row = rows[0]
    if row.get("value") not in SP.UNITS or row.get("anchor") != "end" or \
            not _in_question(row["quote"], question):
        return None
    return row


def _frequency_support(quote: str, table: str, frequency: str, evidence: dict, lk: "Cards") -> dict:
    """Whether an option's value gives the requested frequency (users 2026-10-29 07:51 and 08:24 UTC). The option
    table, or a frequency or edition candidate of it (same basis, same other bracket words), offers the frequency in
    the catalog; a candidate counts only if it keeps what the model cited for the option (``_kept`` with the option's
    valid item evidence and its retention: invalid cited items are never read as none). Two facts per candidate:
    ``offers_frequency`` and ``items_kept``. Status: ``supported`` (a confirmed table), ``items_kept_unconfirmed``
    (offered, keeping not confirmed: not "no frequency", not "another meaning") or ``not_offered``. No new
    correspondence rule; nothing is added, removed or switched."""
    rows = {r["table_id"]: r for r in lk.card_of[table]["tables"]}
    retention = {"origin": table, "unverified": list(evidence.get("invalid_items") or [])}
    checked = []
    for t in _candidates(rows, table):
        row = {"table": t, "offers_frequency": frequency in {p["frequency"] for p in rows[t].get("periods") or []}}
        if row["offers_frequency"]:
            label, got = _kept(quote, table, t, list(evidence.get("items") or []), lk, retention)
            row.update(items_kept=label, confirmed=label in USABLE, cited_values_held=got.get("cited_values_held"))
        checked.append(row)
    confirmed = [r["table"] for r in checked if r.get("confirmed")]
    offering = [r["table"] for r in checked if r["offers_frequency"]]
    status = "supported" if confirmed else "items_kept_unconfirmed" if offering else "not_offered"
    return {"table": table, "requested": frequency, "status": status, "supported": bool(confirmed),
            "supported_by": confirmed, "offering": offering, "candidates": checked,
            "frequencies": sorted({p["frequency"] for p in rows[table].get("periods") or []})}


def axis_items(items: list[str], axes) -> tuple[list[str], list[str]]:
    """(kept, bound): cited value names split for a check across ``axes``. A name that carries a value of one of the
    axes (as 'M2(평잔계절조정계열)' carries 평잔 and 계절조정) names the representative's value, not the statistic, so
    it is not required in a table of another value of that axis (user 2026-10-28 06:27 UTC). Nothing is removed
    from the name and no two names are made equal; the bound names are recorded."""
    bound = [n for n in items if any(basis_values(n, a) for a in axes)]
    return [n for n in items if n not in bound], bound


def option_status(d: dict, axis: str, value: str, lk: "Cards | None" = None,
                  rows: list[dict] | None = None) -> tuple[str, list[str], list[str]]:
    """(status, why, depends_on) of one ask option from its own card (plan 56 order 8, ⑥-가): the requested frequency
    (``ask_frequency_checks``), the period over the values of the other open axes (``ask_feasibility``). A check that
    could not be done leaves it unconfirmed, never unsupported; a latest month not yet published is no shortfall
    (policy ①, kept by the period choice)."""
    status, why, depends = "supported", [], []
    freq = next((r for r in d.get("ask_frequency_checks") or [] if (r["axis"], r["value"]) == (axis, value)), None)
    finer = set(FINER.get((freq or {}).get("requested"), ()))
    made = freq and lk is not None and finer & set(PR.offered_frequencies(
        [lk.views[c["table"]] for c in freq.get("candidates") or [] if c["table"] in lk.views]))
    if freq and freq["status"] == "not_offered" and made:
        # the frequency could be aggregated from a finer one (policy ④ supplement): the option is kept, its method
        # checked as a kept candidate's (``method_check``); verified, it stays supported with the aggregation still
        # to do, else it is unconfirmed, never "unsupported" (users 2026-10-31 21:54: an unconfirmed aggregation never
        # makes another option chosen)
        row = next((r for r in rows or [] if r.get("requested_frequency") == freq["requested"]), None)
        tables = [c["table"] for c in freq.get("candidates") or [] if c["table"] in lk.views]
        if row and not method_check(d, row, tables, lk)["why"]:
            return "supported", [], []
        return "unconfirmed", ["frequency"], []
    if freq and freq["status"] == "not_offered":
        status, why = "unsupported", ["frequency"]
    elif freq and freq["status"] == "items_kept_unconfirmed":
        status, why = "unconfirmed", ["items"]
    feas = next((f for f in d.get("ask_feasibility") or [] if (f["axis"], f["value"]) == (axis, value)), None)
    if feas and status != "unsupported":
        reasons = {g["reason"] for g in feas["groups"]}
        if feas["status"] == "not_covered" and reasons and reasons <= {"period_not_covered", "frequency_not_offered"}:
            # a confirmed shortfall of every group: unsupported
            status, why = "unsupported", ["frequency" if reasons == {"frequency_not_offered"} else "period"]
        elif feas["status"] == "not_covered":  # a check not done (an unread period, content unverified, ...)
            status, why = "unconfirmed", sorted({*why, "period"})
        elif feas["status"] == "unreadable":
            status, why = "unconfirmed", sorted({*why, "period"})
        elif feas["status"] == "conditional":
            depends = list(feas["remaining_axes"])
    return status, why, depends


def _fitting_outside(d: dict, quote: str, reading: dict, lk: "Cards", as_of: str) -> list[str]:
    """Tables of the options' cards outside the options that cover the request (the recovery's candidates, never
    chosen by code)."""
    offered = {o["table_id"] for a in d["asks"] for o in a["options"]}
    out = []
    for card_id in dict.fromkeys(lk.card_of[t]["card_id"] for t in offered):
        card = next(lk.card_of[t] for t in offered if lk.card_of[t]["card_id"] == card_id)
        groups: dict[tuple, list[str]] = {}
        for r in card["tables"]:
            if r["table_id"] not in offered:
                groups.setdefault((tuple(sorted(_basis(r).items())), _other_words_no_time(r)), []).append(r["table_id"])
        for members in groups.values():
            rep = sorted(members, key=lambda t: PR._end_key(lk.views[t]), reverse=True)[0]
            got = _select_periods(quote, rep, members, [], reading, lk, as_of, "ask_outside", d["retention"])
            if got["status"] == "selected":
                out += [t["table_id"] for t in got["tables"]]
    return out


def _no_pending_condition(reading: dict, table: str, lk: "Cards") -> bool:
    """No period or frequency left to the user that could change which option works (⑥-나 condition 4)."""
    kinds = {c["kind"] for c in reading["conditions"]}
    return "period" in kinds and not reading["problems"] and (
        "frequency" in kinds or len(PR.offered_frequencies([lk.views[table]])) == 1)


def settle_asks(d: dict, quote: str, reading: dict, lk: "Cards", as_of: str,
                rows: list[dict] | None = None) -> tuple | None:
    """Each ask option's status (``option_status``) and the ask that follows (plan 56 order 8, ⑥-가·나): only
    supported options are executable and offered; unconfirmed ones are shown as needing a check and never selectable; unsupported
    ones are not offered. Two or more executable options: the ask goes on. One supported option is chosen without
    another call only when all five conditions hold (it is the model's own option with valid evidence; every other
    option is confirmed unsupported; no unconfirmed one is left; no unanswered question changes which works; it passed
    the stated basis, item, frequency and period checks), recorded as ``auto_decided`` (support_only), never as the
    user's choice; else the statistic is held. An unconfirmed option with fewer than two supported: held, never read
    as unsupported. Every option unsupported: with a table of the card outside the options covering the request, a
    re-judgment is pending (its candidates in ``rejudge_targets``; code never chooses it), else the ask is invalid.
    (reason, detail) when the statistic stops, else None."""
    # a period the user quoted (read, or quoted and unreadable): the statuses are of the user's request
    quoted = any(c["kind"] == "period" for c in reading["conditions"]) or bool(reading["problems"])
    for a in d["asks"]:
        for o in a["options"]:
            o["status"], o["why"], o["depends_on"] = option_status(d, a["axis"], o["value"], lk, rows)
            if not quoted and o["status"] != "supported":
                o["depends_on"] = [*o["depends_on"], "period"]  # judged on no period of the user's
            o["selectable"] = o["status"] == "supported" or not quoted
    if not quoted:  # the statuses are of no period the user gave: recorded, the ask goes on as before
        return None
    decided = []
    for a in d["asks"]:
        statuses = [o["status"] for o in a["options"]]
        supported = statuses.count("supported")
        if supported >= 2:
            continue
        rows = [{"value": o["value"], "table": o["table_id"], "status": o["status"], "why": o["why"]}
                for o in a["options"]]
        stage = "ask_frequency" if any("items" in o["why"] for o in a["options"]) else "ask_options"
        if "unconfirmed" in statuses:
            d["asks"] = []
            if reading["problems"]:  # the code could not read a quoted condition: a correction target (⑧)
                d.setdefault("rejudge_targets", []).append({"code": "condition_unreadable", "axis": a["axis"],
                                                            "problems": [x.get("code") for x in reading["problems"]]})
            return "target_table_unconfirmed", {"axis": a["axis"], "stage": stage, "options": rows,
                                                "why": "an option is not confirmed: held, never read as unsupported"}
        if supported == 1:
            o = next(x for x in a["options"] if x["status"] == "supported")
            fixed = all(not x["depends_on"] for x in a["options"])
            if (d["option_evidence"].get(o["table_id"]) or {}).get("valid") and fixed and \
                    _no_pending_condition(reading, o["table_id"], lk):
                decided.append((a, o, rows))
                continue
            d["asks"] = []
            return "target_table_unconfirmed", {"axis": a["axis"], "stage": "ask_options", "options": rows,
                                                "why": "one option supported; a question not answered may change it"}
        outside = _fitting_outside(d, quote, reading, lk, as_of)
        if outside:
            d["asks"] = []
            d.setdefault("rejudge_targets", []).append({"code": "ask_not_executable", "axis": a["axis"],
                                                        "options": rows, "candidates": outside})
            return "rejudge_pending", {"axis": a["axis"], "why": "no executable option; tables of the card outside "
                                                                 "the options cover the request", "options": rows}
        d["asks"] = []
        return "ask_invalid", {"axis": a["axis"], "problems": ["no_executable_option"], "options": rows}
    if decided:
        tables = {o["table_id"] for _, o, _ in decided}
        remaining = [a for a in d["asks"] if all(a is not x for x, _, _ in decided)]
        if len(tables) > 1:
            d["asks"] = []
            return "target_table_unconfirmed", {"stage": "ask_options",
                                                "why": "the options chosen on several axes are different tables"}
        for a, o, rows in decided:
            d["corrections"].append({"code": "auto_decided", "reason": "support_only", "axis": a["axis"],
                                     "value": o["value"], "table": o["table_id"], "options": rows,
                                     "message": AUTO_DECIDED_MESSAGE.format(value=o["value"])})
            d["basis"][a["axis"]] = {"value": [o["value"]], "source": "auto_decided"}
        d["asks"] = remaining
        if not remaining:
            d["representative"] = next(iter(tables))
            d["model_kind"] = "ask_corrected"
    return None


def ask_combinations(d: dict) -> list[dict]:
    """The combinations of the values of every ask of ``d`` before it is asked (plan 56 order 8, ⑥): executable when
    each option of the combination is supported and its table group for the other values gives the request,
    unsupported when a group has a confirmed shortfall or no table on the card, needs_confirmation otherwise. Only
    executable combinations may be chosen."""
    from itertools import product

    if len(d["asks"]) < 2:
        return []
    feas = {(f["axis"], f["value"]): f for f in d.get("ask_feasibility") or []}
    out = []
    for combo in product(*[[(a["axis"], o) for o in a["options"]] for a in d["asks"]]):
        values = {axis: o["value"] for axis, o in combo}
        status = "executable"
        for axis, o in combo:
            f = feas.get((axis, o["value"]))
            want = {x: values[x] for x in (f or {}).get("remaining_axes") or []}
            group = next((g for g in (f or {}).get("groups") or []
                          if all(g["other_axes"].get(x) == v for x, v in want.items())), None)
            if group is None or (group["status"] != "selected" and group["reason"] in
                                 ("period_not_covered", "frequency_not_offered")) or o["status"] == "unsupported":
                status = "unsupported"
                break
            if group["status"] != "selected" or o["status"] != "supported":
                status = "needs_confirmation"
        out.append({"values": values, "status": status})
    return out


def ask_period_first(d: dict, quote: str, reading: dict, lk: "Cards", as_of: str,
                     meanings: tuple = ()) -> None:
    """No period quoted (condition rule ask_condition): the statistic's period is asked (⑥). Before the basis asks
    when an option's status depends on it (each ask ``after`` period), on the same screen otherwise; the option
    statuses are judged again on the answer (``answers``). A question with no window an option covers is not made."""
    if reading["conditions"] and any(c["kind"] == "period" for c in reading["conditions"]) or reading["problems"] \
            or PR.condition_rule() != "ask_condition" or {"latest_available", "same_as"} & set(meanings):
        return  # a period the user gave, a reading problem, or a meaning already set (the latest, a reference)
    tables = list(dict.fromkeys(o["table_id"] for a in d["asks"] for o in a["options"]))
    freq = _frequency_decision(reading, tables, lk, as_of)
    ask = period_ask(d, quote, tables[0], tables, reading, freq, lk, as_of, stage="ask_period_option")
    if not ask["options"]:  # no window an option covers: the basis question goes first; the period after it
        return
    d["condition_asks"] = [ask]
    dependent = any(o["status"] != "supported" or o["depends_on"] for a in d["asks"] for o in a["options"])
    for a in d["asks"]:
        a["after"] = ["period"] if dependent else []


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
    polar = parsed.get("answer_contract") in POLARITY_CONTRACTS
    semantic = parsed.get("answer_contract") in SEMANTIC_PERIOD_CONTRACTS
    D = {sid: {"statistic": sid, "quote": s["quote"], "name": s["name"], "model_kind": None, "representative": None,
               "model_evidence": [], "item_evidence": [], "code_evidence": [], "basis": {}, "open_axes": [],
               "outcome": None, "final_tables": [], "asks": [], "corrections": [], "reasons": [], "warnings": [],
               "verification": [], "diagnostics": [], "period": None, "frequency_quotes": [],
               "citation": {"status": "verified", "problems": []}, "invalid_item_evidence": [],
               "retention": {"origin": None, "unverified": []}, "model_representative": None, "held_axes": [],
               "axis_bound_items": {}, "item_refs": {}, "option_evidence": {}, "unasked_axes": [],
               "stated_basis_checks": [], "ask_frequency_checks": [], "checks_done": [], "checks_not_done": []}
         for sid, s in stats.items()}
    checks: list[dict] = []

    # A scope is model-authored text with original-question evidence, not a code-inferred interpretation. Older
    # answers keep the old quote-based focus, explicitly labelled; they are never silently upgraded to contract .7.
    focus_rows = {row["for"][0]: row for row in out.get("focuses", [])}
    for sid, d in D.items():
        if sid in focus_rows:
            d["focus"], problems = check_scope(question, focus_rows[sid])
            d["reasons"].extend(problems)
            checks.append({"check": "focus_sources", "statistic": sid, "ok": not problems})
        else:
            d["focus"] = legacy_scope(d["quote"])

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
            if D[sid]["focus"]["source"] == "legacy_quote":
                D[sid]["focus"]["status"] = "invalid"
            fail(sid, "quote_not_in_question", s["quote"])
    for key in ("splitters", "periods", "frequencies", "calculations"):
        for row in out[key]:
            ok = _in_question(row["quote"], question)
            checks.append({"check": f"{key}_quote", "statistics": row["for"], "quote": row["quote"], "ok": ok})
            if not ok:
                for sid in row["for"]:
                    if key == "calculations":
                        D[sid]["warnings"].append({"code": "calculation_quote_not_in_question", "detail": row["quote"]})
                    elif key == "splitters" and polar:  # a polarity row's quote (contract .4): a correctable error
                        fail(sid, "basis_polarity_invalid", {"cause": "quote_not_in_question",
                                                             "detail": {"axis": row["axis"], "quote": row["quote"]},
                                                             "recovery": dict(POLARITY_RECOVERY)})
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
            absent = parsed.get("answer_contract") not in OPTION_EVIDENCE_CONTRACTS + POLARITY_CONTRACTS and \
                any(not o["evidence"] for o in a["options"])
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
            wanted = _requested_frequency(out, sid)
            if wanted:  # each option's value must give the requested frequency (user 2026-10-29 07:51, 08:24 UTC)
                rows = [{"axis": a["axis"], "value": v,
                         **_frequency_support(stats[sid]["quote"], t, wanted, d["option_evidence"][t], lk)}
                        for v, t in opts]
                d["ask_frequency_checks"] += rows  # each option's status is decided in step 4 (plan 56 order 8)
            st = stated(question, stats, sid, a["axis"], splitters, polar)
            end_anchor = _stock_ask_end_anchor(out, sid, a["axis"], opts, question) \
                if semantic and not d["reasons"] else None
            if end_anchor is not None:
                st = {**st, "values": {"말잔"}, "source": "frequency_anchor"}
            if st["polarity_unresolved"]:
                fail(sid, "basis_polarity_unresolved", {"axis": a["axis"], "stage": "model_ask",
                                                        "values": st["polarity_unresolved"]})
                continue
            if st["unresolved"]:
                fail(sid, "basis_attribution_unresolved", {"axis": a["axis"], "values": sorted(st["values"])})
                continue
            gone = [v for v, _ in opts if v in st["excluded"]]
            if not st["values"] and gone:
                # an option of a value the question excludes: never turned into the remaining option (plan 53)
                fail(sid, "stated_basis_conflict", {"axis": a["axis"], "stage": "ask_option_excluded",
                                                    "excluded": sorted(st["excluded"]), "options": gone,
                                                    "source": st["source"],
                                                    "why": "an ask option of a value the question excludes"})
                continue
            card_a = lk.card_of[opts[0][1]]
            if st.get("mode") == "all" and len(st["values"]) > 1 and not _carries_all(card_a, a["axis"], st["values"]):
                fail(sid, "basis_combination_unsupported", {"axis": a["axis"], "required": sorted(st["values"]),
                                                            "stage": "model_ask",
                                                            "why": "no table of the card gives every required value"})
                continue
            if st["values"]:
                hit = [t for v, t in opts if v in st["values"]]
                if len(hit) == 1:
                    d["corrections"].append({"code": "unneeded_ask_to_choice", "axis": a["axis"],
                                             "stated": sorted(st["values"]), "source": st["source"], "table": hit[0],
                                             **({"frequency": copy.deepcopy(end_anchor)} if end_anchor else {})})
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

    # 2b. the model's polarity rows (contract .4, plan 56 order 6-2): a correctable output error stops the statistic
    # (basis_polarity_invalid: its cause apart from the recovery), never a question to the user; a basis value the
    # code's words find in the question with no row is a diagnosis only; an undetermined axis with no other value on
    # the card is a warning
    if polar:
        for sid, d in D.items():
            if d["reasons"]:
                continue
            problems = polarity_problems(sid, splitters, d, lk)
            if problems:
                fail(sid, "basis_polarity_invalid", {**problems[0], "problems": problems,
                                                     "recovery": dict(POLARITY_RECOVERY)})
                continue
            for axis in sorted(BASIS_AXES):
                if axis in ASK_AXES and basis_values(question, axis) and \
                        not any(s["axis"] == axis and sid in s["for"] for s in splitters):
                    d["diagnostics"].append({"stage": "basis_value_without_row", "axis": axis,
                                             "values": sorted(basis_values(question, axis))})
            rep_card = lk.card_of.get(d["representative"]) if d["representative"] else None
            undetermined = {s["axis"] for s in splitters if sid in s["for"] and s.get("polarity") == "undetermined"}
            for axis in sorted(undetermined):
                if axis not in {a["axis"] for a in d["asks"]} and not (
                        rep_card and any(sp["axis"] == axis for sp in rep_card["splits"])):
                    d["warnings"].append({"code": "basis_polarity_undetermined", "axis": axis})

    # 3. basis on the representative's card (an ask: on each option's card)
    for sid, d in D.items():
        rep = d["representative"]
        if rep is None or d["reasons"] or d["model_kind"] not in ("choice", "ask_corrected", "ask"):
            continue
        asked = {a["axis"] for a in d["asks"]}
        d["checks_done"].append("basis")
        if d["model_kind"] == "ask":  # each option's own card (options of several cards, plan 56 order 8, ⑨)
            first = {}
            for a in d["asks"]:
                for o in a["options"]:
                    first.setdefault(lk.card_of[o["table_id"]]["card_id"], o["table_id"])
            reps = list(first.values())
        else:
            reps = [rep]
        for rep in reps:
            if d["reasons"]:
                break
            card = lk.card_of[rep]
            rows = {r["table_id"]: r for r in card["tables"]}
            for split in card["splits"]:
                axis = split["axis"]
                if not split["ask_axis"] or axis in asked or axis in d["basis"]:
                    continue
                if axis not in rows[rep]["basis"]:
                    d["warnings"].append({"code": "representative_basis_unknown", "axis": axis})
                    continue
                st = stated(question, stats, sid, axis, splitters, polar)
                if st["ignored"]:
                    d["warnings"].append({"code": "splitter_without_axis_value", "axis": axis, "quotes": st["ignored"]})
                if st["polarity_unresolved"]:
                    fail(sid, "basis_polarity_unresolved", {"axis": axis, "values": st["polarity_unresolved"]})
                    break
                if st["unresolved"]:
                    fail(sid, "basis_attribution_unresolved", {"axis": axis, "values": sorted(st["values"])})
                    break
                here = tuple(rows[rep]["basis"][axis])
                if not st["values"] and st["excluded"]:
                    # only an excluded value is stated (plan 53): a table of it conflicts; any other value is the
                    # model's choice, never the user's basis, and code switches to no "other" value
                    if set(here) & st["excluded"]:
                        fail(sid, "stated_basis_conflict", {"axis": axis, "excluded": sorted(st["excluded"]),
                                                            "representative_value": list(here), "source": st["source"],
                                                            "why": "the representative carries a value the question "
                                                                   "excludes"})
                        break
                    if d["model_kind"] == "ask":
                        d["unasked_axes"].append(axis)
                    else:
                        d["basis"][axis] = {"value": list(here), "source": "model_choice",
                                            "excluded": sorted(st["excluded"]), "excluded_source": st["source"],
                                            "note": "the question excludes a value; the model chose this value's table"}
                    continue
                if st["values"]:
                    if _meets(here, st):
                        d["basis"][axis] = {"value": list(here), "source": st["source"]}
                        continue
                    if st.get("mode") == "all" and len(st["values"]) > 1 and not _carries_all(card, axis, st["values"]):
                        fail(sid, "basis_combination_unsupported", {"axis": axis, "required": sorted(st["values"]),
                                                                    "representative_value": list(here),
                                                                    "why": "no table of the card gives every required "
                                                                           "value"})
                        break
                    if d["model_kind"] == "ask":
                        d["basis"][axis] = {"value": sorted(st["values"]), "source": st["source"],
                                            "note": "the ask's option tables are representatives only"}
                        continue
                    target = tuple(sorted(st["values"]))
                    if st.get("mode") == "any":  # one of the values is enough: any table of one of them
                        switch = [t for v in sorted(st["values"])
                                  for t in _siblings_on(card, rows, rep, axis, (v,), views)]
                    else:
                        switch = _siblings_on(card, rows, rep, axis, target, views)
                    evidenced = [t for t in switch if (d["option_evidence"].get(t) or {}).get("valid")]
                    if evidenced and len({_other_words_no_time(rows[t]) for t in switch}) == 1:
                        # a switch only to a table the model gave its own evidence for (users 2026-10-28 06:47, 07:19)
                        new = evidenced[0]
                        got, links = axis_verdict(sid, rep, new, axis)
                        d["corrections"].append({"code": "basis_switched", "axis": axis, "from": rep, "to": new,
                                                 "stated": sorted(st["values"]), "source": st["source"]})
                        d["representative"] = rep = new
                        d["basis"][axis] = {"value": list(rows[new]["basis"][axis]) if st.get("mode") == "any"
                                            else list(target), "source": st["source"]}
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
                                                            **({"mode": st["mode"]} if st.get("mode") else {}),
                                                            "why": "required_value_missing" if st.get("mode") == "all"
                                                            and len(st["values"]) > 1 else
                                                            "no model evidence for the stated value's table"})
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
            # 3a. every ask option against the basis the statistic states on the other axes (user 2026-10-29
            # 06:20 UTC): the same attribution as steps 3 and 3b (an axis the card splits by ``stated``, an unsplit
            # axis by the model's own splitters only). An option table of another stated value is a conflict: the statistic stops,
            # never a verified ask; a value the option table does not show is unverified (a warning); no option is
            # replaced or added
            if not d["reasons"] and d["asks"]:
                split_axes = {split["axis"] for split in card["splits"]}
                asked_axes = {a["axis"] for a in d["asks"]}
                conflict = None
                for a in d["asks"]:
                    for o in a["options"]:
                        orow = lk.row[o["table_id"]]
                        for axis in sorted(ASK_AXES):
                            if axis in asked_axes or conflict:
                                continue
                            st = stated(question, stats, sid, axis, splitters, polar)
                            if st["unresolved"] or (axis not in split_axes and st["source"] != "splitter"):
                                continue
                            if st["polarity_unresolved"]:
                                conflict = "polarity"
                                fail(sid, "basis_polarity_unresolved", {"axis": axis, "stage": "ask_option",
                                                                        "values": st["polarity_unresolved"]})
                                continue
                            if not st["values"] and not st["excluded"]:
                                continue
                            here = orow["basis"].get(axis)
                            source = "title" if here else None
                            if here is None:
                                items = (d["option_evidence"].get(o["table_id"]) or {}).get("items") or []
                                here = _cited_value(items, axis)
                                source = "cited_item" if here else None
                            row = {"statistic": sid, "axis": axis, "stage": "ask_option", "ask_axis": a["axis"],
                                   "option": o["value"],
                                   "quotes": [x["quote"] for x in splitters if x["axis"] == axis
                                              and sid in x["for"] and basis_values(x["quote"], axis)],
                                   "stated": sorted(st["values"]),
                                   **({"excluded": sorted(st["excluded"])} if st["excluded"] else {}),
                                   "source": st["source"], "table": o["table_id"],
                                   "table_value": list(here) if here else None,
                                   "evidence": _bracket_evidence(orow, axis), "card_splits_axis": axis in split_axes,
                                   "fact_source": source}
                            if here is None:
                                row["result"] = "table_value_unknown"
                                d["warnings"].append({"code": "stated_basis_unverified", "axis": axis,
                                                      "stated": row["stated"], "table": o["table_id"],
                                                      "stage": "ask_option"})
                            elif st["values"] and _meets(here, st):
                                row["result"] = "match"
                            elif not st["values"] and not set(here) & st["excluded"]:
                                row["result"] = "not_excluded"
                            else:
                                row["result"] = conflict = "conflict"
                                fail(sid, "stated_basis_conflict", {"axis": axis, "stated": row["stated"],
                                                                    **({"excluded": row["excluded"]}
                                                                       if "excluded" in row else {}),
                                                                    "stage": "ask_option", "ask_axis": a["axis"],
                                                                    "option": o["value"], "table": o["table_id"],
                                                                    "option_value": list(here),
                                                                    "why": "an ask option of another stated value"
                                                                    if st["values"] else
                                                                    "an ask option of a value the question excludes"})
                            _state_row(d, row, card, st)
                            d["stated_basis_checks"].append(row)
            # 3b. a stated basis on an axis the card does not split (user 2026-10-29 04:59 UTC): a single table or a
            # card without that split is checked too. Only the model's own splitters for this statistic (their quotes
            # passed step 1) are compared with the chosen table's title basis; a value the table does not show is
            # unverified, never taken as the other value; nothing is switched or asked here, and value names inside
            # the table are not read as a table basis
            if d["reasons"] or d["model_kind"] == "ask":
                continue
            split_axes = {split["axis"] for split in card["splits"]}
            for axis in sorted({s["axis"] for s in splitters if sid in s["for"]}):
                if axis not in ASK_AXES or axis in split_axes or axis in d["basis"]:
                    continue
                st = stated(question, stats, sid, axis, splitters, polar)
                if st["source"] != "splitter":
                    continue
                if st["polarity_unresolved"]:
                    fail(sid, "basis_polarity_unresolved", {"axis": axis, "values": st["polarity_unresolved"]})
                    break
                here = rows[rep]["basis"].get(axis)
                source = "title" if here else None
                if here is None and rep == d["model_representative"]:  # cited items were checked on the model's table
                    here = _cited_value(d["item_evidence"], axis)
                    source = "cited_item" if here else None
                row = {"statistic": sid, "axis": axis,
                       "quotes": [s["quote"] for s in splitters
                                  if s["axis"] == axis and sid in s["for"] and basis_values(s["quote"], axis)],
                       "stated": sorted(st["values"]),
                       **({"excluded": sorted(st["excluded"])} if st["excluded"] else {}),
                       "table": rep, "table_value": list(here) if here else None,
                       "evidence": _bracket_evidence(rows[rep], axis), "card_splits_axis": False,
                       "fact_source": source}
                if here is None:
                    row["result"] = "table_value_unknown"
                    d["warnings"].append({"code": "stated_basis_unverified", "axis": axis, "stated": row["stated"],
                                          "table": rep})
                elif st["values"] and _meets(here, st):
                    row["result"] = "match"
                    d["basis"][axis] = {"value": list(here), "source": st["source"],
                                        **({"fact_source": source} if source == "cited_item" else {})}
                elif not st["values"] and not set(here) & st["excluded"]:
                    row["result"] = "not_excluded"
                    d["basis"][axis] = {"value": list(here), "source": "model_choice",
                                        "excluded": sorted(st["excluded"]), "excluded_source": st["source"],
                                        "note": "the question excludes a value; the model chose this value's table"}
                elif source == "cited_item":  # a cited item of another value: unknown, never a conflict on items alone
                    row["result"] = "table_value_unknown"
                    d["warnings"].append({"code": "stated_basis_unverified", "axis": axis, "stated": row["stated"],
                                          "table": rep})
                elif not st["values"]:
                    row["result"] = "conflict"
                    fail(sid, "stated_basis_conflict", {"axis": axis, "stated": [], "excluded": sorted(st["excluded"]),
                                                        "representative_value": list(here), "stated_value_tables": [],
                                                        "why": "the representative carries a value the question "
                                                               "excludes"})
                else:
                    row["result"] = "conflict"
                    fail(sid, "stated_basis_conflict", {"axis": axis, "stated": row["stated"],
                                                        "representative_value": list(here), "stated_value_tables": [],
                                                        "why": "the card has no table of the stated value on "
                                                               "this axis"})
                _state_row(d, row, card, st)
                d["stated_basis_checks"].append(row)
                if row["result"] == "conflict":
                    break

    # 4. periods and final tables (ask options: feasibility record only)
    for sid, d in D.items():
        if d["reasons"] or d["model_kind"] in (None, "missing"):
            continue
        frequency_rows = [r for r in out["frequencies"] if sid in r["for"]]
        if semantic:  # contract .8: the model's structured rows (frequency points written by the reading)
            reading = _semantic_reading(d, question, out, parsed.get("reference_periods"), as_of=as_of)
        else:
            reading = PR.read_quotes([r["quote"] for r in out["periods"] if sid in r["for"]],
                                     [r["quote"] for r in frequency_rows])
            if d["asks"] and polar:  # an answer with period meanings: only ranges are read (latest, references apart)
                reading = PR.read_quotes(_period_rows(d, out, polar), [r["quote"] for r in frequency_rows])
            # the end point of each period (월말: the month's end) is kept with its statistics; it is not the
            # resolver's anchor and chooses no basis (plan 36)
            d["frequency_quotes"] = PR.frequency_points(reading, frequency_rows)
        if d["asks"]:
            d["ask_feasibility"] = _ask_feasibility(d, stats[sid]["quote"], reading, lk, as_of)
            got = settle_asks(d, stats[sid]["quote"], reading, lk, as_of,
                              [r for r in out.get("aggregations") or [] if sid in r["for"]] if polar else None)
            if got:
                fail(sid, *got)
                continue
            if d["asks"]:
                combos = ask_combinations(d)
                for a in d["asks"]:
                    a["combinations"] = combos
                ask_period_first(d, stats[sid]["quote"], reading, lk, as_of, tuple(d.get("period_meaning") or ()))
                continue
            if not verified(sid, d["representative"], "auto_decided"):  # every ask decided by ⑥-나
                continue
        if d["model_kind"] == "ask":
            continue
        decide_periods(d, stats[sid]["quote"], d["representative"], reading, lk, as_of, "choice",
                       {"question": question, "out": out, "polar": polar, "semantic": semantic,
                        "ref_periods": parsed.get("reference_periods")})
    group_condition_asks(D, out)
    same_table_periods(D, out)

    catalog_matches = [row for card in cards for row in card.get("catalog_matches", [])]
    for d in D.values():
        source = question if parsed.get("scope_statistic_count", len(stats)) == 1 else d["quote"]
        missing = missing_scope(d, catalog_matches, source)
        if missing:
            d["reasons"].append({"code": "focus_catalog_condition_missing", "detail": {"selectors": missing}})
            d["focus"]["status"] = "invalid"

    for d in D.values():  # an excluded value kept with the basis is shown, never dropped (bundle 5)
        for axis, b in sorted(d["basis"].items()):
            if b.get("excluded"):
                d["warnings"].append({"code": "basis_excluded", "axis": axis, "excluded": list(b["excluded"])})
    for d in D.values():
        if d["model_kind"] == "choice" or d["model_kind"] == "ask_corrected":
            d["checks_not_done"] = [x for x in STAGES if x not in d["checks_done"]]
        if d["reasons"]:
            d["outcome"] = "failed"
        elif d["model_kind"] == "missing":
            d["outcome"] = "not_in_pool"
        elif d.get("condition_asks"):  # before the basis asks that wait for it (plan 56 order 8, ⑥)
            d["outcome"] = CONDITION_OUTCOME if d["citation"]["status"] == "verified" else "ask_unverified"
        elif d["asks"]:
            d["outcome"] = "ask" if d["citation"]["status"] == "verified" else "ask_unverified"
        elif (d.get("frequency") or {}).get("aggregation"):
            d["outcome"] = AGGREGATION_OUTCOME
        elif d["final_tables"]:
            d["outcome"] = "selected" if d["citation"]["status"] == "verified" else "selected_unverified"
        else:
            d["outcome"] = "failed"
            d["reasons"].append({"code": "undecided", "detail": None})
    # A valid per-statistic scope can still exceed B's shared field when the final table is the same. Detect it
    # before the one recovery chooses its targets, without merging different conditions or truncating text.
    check_groups(list(D.values()))
    for d in D.values():
        annotate(d, out)
        d["failure_kinds"] = failure_kinds(d)
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
    asking = counts["ask"] + outcomes.count(CONDITION_OUTCOME)
    for extra in (CONDITION_OUTCOME, AGGREGATION_OUTCOME):
        if extra in outcomes:
            counts[extra] = outcomes.count(extra)
    done = counts["selected"] + counts["selected_unverified"] + asking + counts["ask_unverified"] + \
        outcomes.count(AGGREGATION_OUTCOME)
    if outcomes and counts["selected"] == len(outcomes):
        action = "handoff"
    elif asking and asking + counts["selected"] == len(outcomes):
        action = "ask"
    elif done:
        action = "partial"
    else:
        action = "fail"
    return {"action": action, "complete": action == "handoff", "counts": counts}
