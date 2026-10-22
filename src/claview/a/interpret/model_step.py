"""2.6 model step with the model on (plan 06): decision points, the call, and the recheck of the choices.

The model only chooses (plan 06, 1). For an undecided judgement the code builds the points it could not decide and
the options of each, one function per kind of point (``POINT_BUILDERS``; words in data/model_step_words.json). Every
option the code offers is one the catalog layer supports, and carries its evidence (type, number of tables, paths,
common tables, tables where a name is a value under the head name); an option equal to the draft is not offered
twice (it is the "keep the draft" option, with the draft's evidence). Point kinds (``Point.kind``, the recorded kind)
and their forms (``Point.form``):
- ``name`` (check 1, row 10): a requirement indicator that is a lexical guess, of two types, weak or of an unknown
  type: "keep the draft" with the current guess, up to five other candidates (the fragment's lexical terms and the
  2-gram lookup of its words; type, number of tables holding it, two representative paths "상위 > 값"; no table
  names; never a target value only, nor a name the requirement's breakdown items are not under), or "not in the
  catalog";
- ``appositive`` (check 2): "A, 즉 B": keep (the same indicator A), the same indicator B, or different ones (ask);
  form ``gloss``: "A(B)" read as A broken down by B: keep, A only (the bracket glosses it), B only, or different;
- ``reading``: an item group read as requirements. Options (``_structure_options``): one requirement with breakdown
  ("one", offered only when every other item is a value under the head item in at least one common table), one
  requirement per item ("split", only when every lower item is also held by tables without the head), copies of one
  indicator per basis value, or per combination of values when two basis axes are listed ("copy", only when every
  copy's values are held by a table of the indicator). Forms: ``mixed`` (row 3), ``basis`` (a requirement left
  unresolved by two or more basis values), ``confirm`` (check 8 only, decision 10/18: the draft passed checks 1-7
  but its structure is not one a specification case verified; one point per item group asks whether the draft
  reading matches the question, with the type and table count of every draft name, and the supported options above;
  sent even when there is none), ``grounding`` (ungrounded_split: a requirement whose exact indicator has no table
  holding its own targets and basis values: keep it apart, or merge it back into another requirement of its group as
  breakdown where the catalog reads it so);
- ``scope`` (check 7): "X의 A와 B" (or a target listed with indicators): X on every item, on the first only (with the
  number of tables of the items holding X), or a requirement of its own;
- ``attribution`` (check 4): an ambiguous condition: one requirement (a basis value: with the number of the
  requirement's indicator tables holding it) or common to all;
- ``word`` (checks 5 and 6): form ``leftover``: a request or filler expression (not offered when the word is all its
  requirement holds), part of a name (only with lexical candidates of the word joined with the adjacent name), a
  condition (only with condition-word candidates), or an indicator not in the catalog (its requirement asks); form
  ``unused``: a catalog name no requirement uses is never ignorable: the target or breakdown of a requirement whose
  indicator tables hold it (a target value before "의" + indicators: of those requirements only, or of all of them);
- ``name`` form ``role``: a target of a requirement without an indicator that the catalog also holds on an indicator
  axis: read it as the indicator (when its indicator tables hold the other targets) or keep it a target.
Every point also has "keep the draft" and "cannot decide (ask)". A leftover-word point over the fragment of a name or
appositive point is taken in by that point. More than ``MAX_POINTS`` points: no call, ask.

Every choice is an override of the judgement table (``overrides_of`` -> ``overrides.Overrides``; user decision
10/19): a chosen name or role of a name token, a reading of an item group (one requirement over a head, a split, copies
per combination of basis values), a scope, an appositive or gloss reading, an attached unused name, a condition's
place, a request or filler word. ``recheck`` runs judge_fragments (2.5) again with the overrides, the certainty checks
on the new judgement as they are (absolute, not against the draft; check 8 does not apply once the model answered every
point, its confirm points included), settle_requirements (2.7), the contract shape check and the catalog V-checks
(``model_verify``). Every check passes: the new interpretation is used and its requirements of status ok with an
indicator are ``model_confirmed`` (the interpretation ok when all are); any failure: the draft stays, every requirement
asks and the failures are the record's notes (``recheck:`` + code). A choice that cannot decide (cannot decide,
different ones, not in the catalog, a name part or a condition word) keeps the draft and marks its requirements
``model_ask``. No choice the code offers is outside the table: a requirement of its own for an unused name and a merge
of a requirement as a target of another are not offered. The reply must hold the schema exactly (``read_reply``); an
HTTP 429 leaves the step ``pending`` with the note ``deferred_429`` (a later run sends it). The judgement never becomes
certain. The record is ``judgement.model`` (the contract fields of each option: type, tables, paths; the full evidence
is in the request payload).
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .. import paths
from ..catalog.relations import IND_TYPES, TGT_TYPES, relations_of
from ..catalog.text import index_key
from ..contract.check import check_analysis
from ..contract.shapes import Analysis, Condition, Fragment, Interpretation, Judgement, Term, TextView
from ..reused.model import ModelError
from .certainty import used_tokens
from .conditions import find_conditions
from .grounding import Grounding, indicator_tables
from .lexicon import BYEOL, OPEN
from .model_verify import genitive_followers, verify_after_model
from .nodes import attach_terms, judge_fragments, split_fragments
from .overrides import Overrides, role_tables
from .phrases import key_of, read_question, split_ending
from .reading import Relations, term_of
from .settle import _catalog_name, settle
from .table import SIG_KINDS, judge, owner_view
from .text import normalize_text

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

NODE = "INTERP"  # node name of the request (HcxClient: output cap 512 tokens for an unknown node)
PROMPT_FILE = paths.PACKAGE_DATA_DIR / "prompts" / "interp_choose_v3.txt"
WORDS_FILE = paths.PACKAGE_DATA_DIR / "model_step_words.json"
MAX_POINTS = 6
MAX_NAMES = 5
MAX_PATHS = 2
MAX_COPIES = 6
REASON_MAX = 80  # the prompt (80자 이하), the schema maxLength and the reply check agree
ASK_REASON = "model_ask"
CONFIRMED_REASON = "model_confirmed"
DEFERRED_NOTE = "deferred_429"
NO_INDICATOR = "no_indicator"  # check 9: not sent to the model (user decision 10/19)
RECHECK_NOTE = "recheck:"  # a failure of the recheck (checks 1-7, 8, shape, V-checks), as a record note
MODEL_REASON = "model"
EXACT_PATHS = ("exact", "dictionary")
UNDER = frozenset({"below", "in_series"})  # a value under the head name (or a value of the table it names)
RECORD_INFO = ("type", "tables", "paths")  # option evidence the record keeps (contract ModelOption)
POINT_ORDER = ("name", "role", "appositive", "gloss", "reading", "confirm", "grounding", "scope", "attribution", "word")

_WORDS = json.loads(WORDS_FILE.read_text(encoding="utf-8"))
_LIMITS = _WORDS["word_candidates"]


def _condition_words() -> list[str]:
    """Basis words of find_conditions (data/condition_words.json): the condition-word candidates of a word point."""
    doc = json.loads(paths.CONDITION_WORDS.read_text(encoding="utf-8"))
    out: list[str] = []
    for values in doc.get("basis_axes", {}).values():
        for value, forms in values.items():
            for w in [value] + list(forms):
                if w not in out:
                    out.append(w)
    return out


_CONDITION_WORDS = _condition_words()


def prompt_text() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def prompt_sha256() -> str:
    return hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest()


def _opt_text(key: str, **kw) -> str:
    return _WORDS["options"][key].format(**kw)


def _ask_text(key: str, **kw) -> str:
    return _WORDS["asks"][key].format(**kw)


# ---------------------------------------------------------------- points


@dataclass
class Option:
    effect: str
    text: str
    value: object = None  # what the effect applies (a name, draft IDs, a term); never sent
    info: dict = field(default_factory=dict)  # evidence sent with the option
    id: str = ""


@dataclass
class Point:
    kind: str
    ask: str
    options: list[Option]
    requirement_ids: list[str]  # requirements of the draft interpretation
    fragments: set = field(default_factory=set)
    data: dict = field(default_factory=dict)  # what the apply step needs; never sent
    id: str = ""
    form: str = ""  # gloss, mixed, basis, confirm, grounding, leftover, unused (the kind is the recorded one)
    info: dict = field(default_factory=dict)  # evidence sent with the point

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.form}" if self.form else self.kind


@dataclass
class Context:
    text: TextView
    conditions: list[Condition]
    fragments: list[Fragment]
    terms: list[Term]
    judgement: Judgement
    layer: object
    judged: object  # table.Judged: the reading behind the drafts
    rel: object  # catalog relations, or None without a layer
    interpretation: Interpretation  # 2.7 of the draft (the model-off interpretation)
    rid: dict  # draft ID -> requirement ID of the draft interpretation
    did: dict  # requirement ID -> draft ID
    drafts: dict  # draft ID -> table.Draft
    reading: object = None  # phrases.Reading behind the drafts
    grounding: object = None  # grounding.Grounding of the drafts, or None without a layer


def make_context(text: TextView, conditions: list[Condition], fragments: list[Fragment], terms: list[Term],
                 judgement: Judgement, layer) -> Context:
    reading = read_question(text, conditions, layer)
    j = judge(reading, conditions, layer)
    interp = settle(text, conditions, fragments, terms, judgement, layer)
    rid = {d["id"]: f"r{n}" for n, d in enumerate(judgement.get("requirement_draft") or [], 1)}
    return Context(text=text, conditions=conditions, fragments=fragments, terms=terms, judgement=judgement,
                   layer=layer, judged=j, rel=relations_of(layer) if layer is not None else None,
                   interpretation=interp, rid=rid, did={r: d for d, r in rid.items()},
                   drafts={d.id: d for d in j.drafts}, reading=reading,
                   grounding=Grounding(j, conditions, Relations(layer)) if layer is not None else None)


def _failed(ctx: Context) -> set[str]:
    return set(ctx.judgement.get("failed_conditions") or [])


def _req(ctx: Context, rid: str | None) -> dict | None:
    return next((r for r in ctx.interpretation["requirements"] if r["id"] == rid), None)


def _frag_at(pos: int, fragments: list[Fragment]) -> str | None:
    return next((f["id"] for f in fragments if f["span"][0] <= pos < f["span"][1]), None)


def _frags_in(a: int, b: int, fragments: list[Fragment]) -> set[str]:
    return {f["id"] for f in fragments if f["span"][0] < b and a < f["span"][1]}


def _finish(options: list[Option], keep_text: str | None = None, keep_info: dict | None = None,
            keep_value: object = None) -> list[Option]:
    keep = Option("keep", keep_text or _opt_text("keep"), value=keep_value, info=dict(keep_info or {}))
    return options + [keep, Option("unknown", _opt_text("unknown"))]


def _term_ref(term, fragments: list[Fragment]) -> dict:
    t = term.tok
    return {"quote": t.quote, "span": [t.start, t.end], "fragment_id": _frag_at(t.start, fragments),
            "name": _catalog_name(term)}


def _req_label(r: dict) -> str:
    ind = r.get("indicator")
    if ind:
        return ind.get("name") or ind["quote"]
    names = [t.get("name") or t["quote"] for t in r.get("targets") or []]
    return ", ".join(names) or r["quote"]


def _bigrams(k: str) -> set[str]:
    return {k[i:i + 2] for i in range(len(k) - 1)} if len(k) >= 2 else ({k} if k else set())


def _clean(term) -> bool:
    """A term check 1 passes: exact, of one type, not weak, not of an unknown type."""
    return term is not None and term.exact and not term.mixed and not term.weak and not term.unknown_type


def _has_appositive_word(g) -> bool:
    return any(t.kind == "word" and t.label == "appositive" for it in g.modifier + g.items for t in it.toks)


# ---- catalog evidence


def _type_label(types) -> str:
    order = list(_WORDS["term_types"])
    ranked = sorted(set(types), key=lambda t: (order.index(t) if t in order else len(order), t))
    return "·".join(_WORDS["term_types"].get(t, t) for t in ranked)


def _term_info(term) -> dict:
    return {"type": _type_label(term.types), "tables": len(term.tables)}


def _item_path(ctx: Context, table_id: str, obj_id: str, item_id: str) -> str | None:
    table = ctx.layer.catalog.tables.get(table_id) if ctx.layer is not None else None
    if table is None:
        return None
    axis = next((a for a in table.axes if a.obj_id == obj_id), None)
    if axis is None:
        return None
    items = {it.item_id: it for it in axis.items}
    it = items.get(item_id)
    if it is None or not it.name:
        return None
    parent = items.get(it.parent_id) if it.parent_id else None
    upper = parent.name if parent is not None and parent.name else axis.name
    return f"{upper} > {it.name}" if upper else it.name


def _paths(ctx: Context, name: str) -> list[str]:
    """At most two representative places of a catalog name: "상위 > 값" (the parent value, or the axis name), or
    the mark of a table name (the table name itself is never sent)."""
    if ctx.rel is None:
        return []
    out: list[str] = []
    for table_id, obj_id, item_id in ctx.rel.occurrences(index_key(name)):
        p = _WORDS["table_path"] if obj_id is None else _item_path(ctx, table_id, obj_id, item_id)
        if p and p not in out:
            out.append(p)
        if len(out) >= MAX_PATHS:
            break
    return out


def _catalog_info(ctx: Context, name: str | None, cands: list | None = None) -> dict:
    """Type, number of tables and paths of a catalog name: from the given term candidates of that name, else from
    the catalog's own entries of the name."""
    if not name:
        return {}
    same = [t for t in cands or [] if t.get("name") == name]
    if not same and ctx.layer is not None:
        found = ctx.layer.name_spans().lookup(name)
        same = [c for c in found if c.get("name") == name] or found
    if not same:
        return {}
    info = {"type": _type_label(t["type"] for t in same),
            "tables": len({tid for t in same for tid in t.get("table_ids") or []})}
    found_paths = _paths(ctx, name)
    if found_paths:
        info["paths"] = found_paths
    return info


def _merge_evidence(ctx: Context, head, others: list) -> tuple[int, int]:
    """(common tables of the head and every other name, tables among them where every other name is a value under
    the head)."""
    common = set(head.tables)
    for x in others:
        common &= set(x.tables)
    if ctx.rel is None:
        return len(common), 0
    reads = [ctx.rel.readings(x.key, head.key) for x in others]
    under = sum(1 for t in common if all(r.get(t, set()) & UNDER for r in reads))
    return len(common), under


def _in_relation(ctx: Context, rids: list[str]) -> bool:
    """Two or more of the requirements are operands of one relation (a merge would lose it)."""
    s = set(rids)
    return any(len(s & set(rel["requirement_ids"])) >= 2 for rel in ctx.interpretation["relations"])


def _own_kinds(conditions: list[Condition], ids) -> set[str]:
    """The conditions among ``ids`` that tell requirements apart (row 2 signature kinds: period, frequency, basis,
    calc, unit, exclude); a list item's own breakdown condition does not."""
    kinds = {c["id"]: c["kind"] for c in conditions}
    return {i for i in ids if kinds.get(i) in SIG_KINDS}


def _own_ok(ctx: Context, head_rid: str, other_rids: list[str]) -> bool:
    """Row 2: the lower items have no conditions or targets of their own beyond the head's."""
    head = _req(ctx, head_rid)
    if head is None:
        return False
    spans = [t["span"] for t in head["targets"]]
    for rid in other_rids:
        r = _req(ctx, rid)
        if r is None or _own_kinds(ctx.conditions, r["condition_ids"]) - set(head["condition_ids"]) \
                or any(t["span"] not in spans for t in r["targets"]):
            return False
    return True


def _target_tables(ctx: Context, term, tables: set) -> int:
    """Tables among ``tables`` holding the name as a value of a classification axis (not the indicator axis)."""
    if ctx.rel is None:
        return 0
    held = {t for t, obj, _item in ctx.rel.occurrences(term.key) if obj is not None and t in tables
            and ctx.rel.axis_roles.get((t, obj), "unknown") not in ("indicator", "unknown")}
    return len(held)


def _holds(ctx: Context, table_id: str, c: Condition) -> bool:
    g = ctx.grounding
    return g is not None and (g.holds_word(table_id, str(c.get("value") or c["quote"])) or g.holds_word(table_id,
                                                                                                        c["quote"]))


# ---- name (check 1, row 10)


def _ranked_names(cands: list[Term]) -> list[str]:
    ranked = sorted(enumerate(cands), key=lambda kv: (-(kv[1].get("score") or 0.0), kv[0]))
    return list(dict.fromkeys(t["name"] for _k, t in ranked))


def _lexical_names(ctx: Context, quote: str) -> list[str]:
    if ctx.rel is None:
        return []
    return [ctx.rel.key_names.get(k, k) for k, _score in ctx.rel.lexical(index_key(quote))]


def _name_supported(ctx: Context, name: str, r: dict) -> bool:
    """A candidate the catalog lets stand as the requirement's indicator: not a target value only, and with the
    requirement's breakdown items read under it in every table holding both (row 2)."""
    if _target_only(ctx, name):
        return False
    if ctx.rel is None:
        return True
    head = index_key(name)
    for b in r.get("breakdown") or []:
        key = index_key(b)
        if not ctx.rel.occurrences(key):
            continue  # an X-byeol axis or a word
        readings = ctx.rel.readings(key, head)
        if not readings or not all(x and x <= UNDER for x in readings.values()):
            return False
    return True


def name_points(ctx: Context) -> list[Point]:
    """A requirement indicator that is a lexical guess, or an exact name of two types, weak or of an unknown type."""
    if not _failed(ctx) & {"inexact_term", "row_2_or_7"}:
        return []
    by_frag: dict[str, list[Term]] = {}
    for t in ctx.terms:
        if t["path"] in EXACT_PATHS or t["type"] == "out_of_scope" or not t["name"]:
            continue
        by_frag.setdefault(t["fragment_id"], []).append(t)
    quotes = {f["id"]: f["quote"] for f in ctx.fragments}
    out = []
    for r in ctx.interpretation["requirements"]:
        ind = r["indicator"]
        if ind is None:
            continue
        d = ctx.drafts.get(ctx.did.get(r["id"]))
        term = d.indicator if d is not None else None
        lexical = ind.get("path") not in EXACT_PATHS
        unclean = term is not None and (term.mixed or term.weak or term.unknown_type)
        if not (lexical or unclean):
            continue
        fid = ind.get("fragment_id")
        cands = by_frag.get(fid, [])
        current = ind.get("name")
        seen = {index_key(current)} if current else set()
        names: list[str] = []
        for n in _ranked_names(cands) + _lexical_names(ctx, ind["quote"]):
            k = index_key(n)
            if k in seen:
                continue
            seen.add(k)
            if _name_supported(ctx, n, r):
                names.append(n)
        if current is None and not names:
            continue  # words with no catalog name and no candidate: the word point of check 5 asks about them
        opts = [Option("name", n, value=n, info=_catalog_info(ctx, n, cands)) for n in names[:MAX_NAMES]]
        opts.append(Option("absent", _opt_text("absent")))
        keep = _opt_text("keep_name", name=current or _WORDS["none_name"])
        keep_info = _catalog_info(ctx, current, [t for t in ctx.terms if t["fragment_id"] == fid])
        out.append(Point("name", _ask_text("name", quote=quotes.get(fid) or ind["quote"]),
                         _finish(opts, keep, keep_info), [r["id"]], fragments={fid} if fid else set(),
                         data={"fragment": fid}))
    return out


def role_points(ctx: Context) -> list[Point]:
    """A requirement with targets and no indicator whose last target the catalog reads both as an indicator and as a
    target ("기관의 X"): read it as the indicator (only when its indicator tables hold the other targets), or keep it a
    target."""
    if ctx.layer is None:
        return []
    out = []
    for r in ctx.interpretation["requirements"]:
        d = ctx.drafts.get(ctx.did.get(r["id"]))
        if d is None or r["indicator"] is not None or not d.targets:
            continue
        term = d.targets[-1]
        if not term.exact:
            continue
        ind, tgt = role_tables(term, ctx.layer)
        if not ind or not tgt:
            continue
        others = [t for t in d.targets if t is not term]
        held = {t for t in ind if all(t in o.tables for o in others)}
        if not held:
            continue  # the other targets would not be held by the indicator's tables (check 7 would reject it)
        name = _catalog_name(term) or term.tok.quote
        opts = [Option("as_indicator", _opt_text("as_indicator", name=name), value=term,
                       info={"tables": len(ind), "with_targets": len(held)})]
        out.append(Point("name", _ask_text("role", name=name),
                         _finish(opts, _opt_text("keep_target"), {"tables": len(tgt)}), [r["id"]],
                         fragments=_frags_in(term.tok.start, term.tok.end, ctx.fragments), data={"term": term},
                         form="role"))
    return out


# ---- appositive (check 2): "A, 즉 B" and the bracket gloss "A(B)"


def appositive_points(ctx: Context) -> list[Point]:
    """"A, 즉 B": the draft reads A; keep (the same indicator A), B, or different ones."""
    if "row_2_or_7" not in _failed(ctx):
        return []
    out = []
    for d in ctx.judged.drafts:
        if d.indicator is None or not d.alternates or d.id not in ctx.rid:
            continue
        a = d.indicator
        opts = [Option("same_b", _opt_text("same_b", b=alt.tok.quote), value=alt,
                       info={**_term_info(alt), "common_tables": len(set(a.tables) & set(alt.tables))})
                for alt in d.alternates]
        opts.append(Option("differ", _opt_text("differ")))
        frags: set[str] = set()
        for t in [a] + list(d.alternates):
            frags |= _frags_in(t.tok.start, t.tok.end, ctx.fragments)
        keep = _opt_text("keep_appositive", a=a.tok.quote)
        out.append(Point("appositive", _ask_text("appositive", a=a.tok.quote,
                                                 b=", ".join(t.tok.quote for t in d.alternates)),
                         _finish(opts, keep, {**_term_info(a), "common_tables": len(set(a.tables) & set(
                             d.alternates[0].tables))}, keep_value="same_a"), [ctx.rid[d.id]], fragments=frags,
                         data={"draft": d.id}))
    return out


def _bracket_between(toks: list, a, b) -> bool:
    """Only an opening bracket stands between tokens ``a`` and ``b`` ("A(B)"; full-width forms too)."""
    between = [t for t in toks if a.end <= t.start and t.end <= b.start]
    return a.send == a.end and len(between) == 1 and between[0].kind == "punct" and between[0].label in OPEN


def gloss_points(ctx: Context) -> list[Point]:
    """"A(B)" with B read as a breakdown of A while the bracket reads as an appositive (check 2)."""
    if "row_2_or_7" not in _failed(ctx):
        return []
    out = []
    toks = ctx.reading.toks if ctx.reading is not None else []
    for d in ctx.judged.drafts:
        if d.indicator is None or d.id not in ctx.rid or not d.group.appositive or _has_appositive_word(d.group):
            continue
        a = d.indicator
        glossed = [t for t in d.lower if t.tok.start >= a.tok.end and _bracket_between(toks, a.tok, t.tok)]
        if not glossed:
            continue
        b = glossed[0]
        common, under = _merge_evidence(ctx, a, [b])
        opts = [Option("gloss_a", _opt_text("gloss_a", a=a.tok.quote), info=_term_info(a))]
        if _clean(b):
            opts.append(Option("gloss_b", _opt_text("gloss_b", b=b.tok.quote), value=b, info=_term_info(b)))
        opts.append(Option("differ", _opt_text("differ")))
        keep = _opt_text("keep_gloss", a=a.tok.quote, b=b.tok.quote)
        out.append(Point("appositive", _ask_text("gloss", a=a.tok.quote, b=b.tok.quote),
                         _finish(opts, keep, {**_term_info(a), "common_tables": common, "under_head_tables": under}),
                         [ctx.rid[d.id]], fragments=_frags_in(a.tok.start, b.tok.end, ctx.fragments),
                         data={"draft": d.id, "gloss": b}, form="gloss"))
    return out


# ---- reading (row 3, unresolved basis copies, check 8, ungrounded split)


def _basis_of(ctx: Context, draft_id: str) -> list[Condition]:
    """Basis conditions with distinct values the draft carries: its own, or every common one when it is the only
    draft (2.7 gives a lone requirement every condition as its own)."""
    rows = {d["id"]: d for d in ctx.judgement.get("requirement_draft") or []}
    ids = list(rows.get(draft_id, {}).get("condition_ids") or [])
    if len(rows) == 1:
        att = ctx.judgement.get("attribution") or {}
        ids += [cid for cid, p in att.items() if p == "common" and cid not in ids]
    out, seen = [], set()
    for c in ctx.conditions:
        if c["id"] in ids and c["kind"] == "basis" and c["attrs"].get("polarity", "+") == "+" \
                and c.get("value") not in seen:
            seen.add(c.get("value"))
            out.append(c)
    return out


def _one_option(ctx: Context, inds: list, text: str | None = None) -> Option | None:
    """One requirement over the first item with the others as its breakdown: only when every other item is a value
    under the head in at least one common table, no relation reads them apart and the others have no conditions of
    their own (row 2)."""
    if ctx.rel is None or len(inds) < 2 or any(d.indicator is None or d.id not in ctx.rid for d in inds):
        return None
    rids = [ctx.rid[d.id] for d in inds]
    if _in_relation(ctx, rids) or not _own_ok(ctx, rids[0], rids[1:]):
        return None
    head = inds[0].indicator
    others = [d.indicator for d in inds[1:]]
    if any(x.key == head.key for x in others):
        return None
    common, under = _merge_evidence(ctx, head, others)
    if under < 1:
        return None
    text = text or _opt_text("one", head=head.tok.quote, rest=", ".join(x.tok.quote for x in others))
    return Option("one", text, value=[d.id for d in inds], info={"common_tables": common, "under_head_tables": under})


def _split_option(ctx: Context, drafts: list) -> Option | None:
    """One requirement per item of a row 2 draft: only when every lower item is an exact name also held by tables
    without the head (a requirement of its own); never for a share ("X 중 B의 비중")."""
    if len(drafts) != 1 or ctx.rel is None:
        return None
    d = drafts[0]
    if d.indicator is None or not d.lower or d.share or not all(_clean(t) for t in d.lower):
        return None
    head = d.indicator
    names = [{"name": head.tok.quote, "tables": len(head.tables)}]
    for t in d.lower:
        own = set(t.tables) - set(head.tables)
        if not own:
            return None
        names.append({"name": t.tok.quote, "tables": len(t.tables), "without_head": len(own)})
    text = _opt_text("split", names=", ".join([head.tok.quote] + [t.tok.quote for t in d.lower]))
    return Option("split", text, value=d.id, info={"names": names})


def _copy_option(ctx: Context, drafts: list) -> Option | None:
    """Copies of one indicator: one per basis value (one axis) or per combination of values (two or more axes), only
    when a table of the indicator holds the values of every copy."""
    join = _WORDS["basis_join"]
    for d in drafts:
        if d.indicator is None or d.copy_of is not None or d.id not in ctx.rid:
            continue
        basis = _basis_of(ctx, d.id)
        if len(basis) < 2:
            continue
        axes: dict[str, list[Condition]] = {}
        for c in basis:
            axes.setdefault(str(c["attrs"].get("axis") or c.get("value")), []).append(c)
        combos = list(itertools.product(*axes.values()))
        if not 2 <= len(combos) <= MAX_COPIES:
            continue
        rows = []
        for combo in combos:
            n = sum(1 for t in d.indicator.tables if all(_holds(ctx, t, c) for c in combo))
            if n == 0:
                break
            rows.append({"basis": join.join(c["quote"] for c in combo), "tables": n})
        else:
            q = d.indicator.tok.quote
            names = ", ".join(f"{q}({row['basis']})" for row in rows)
            return Option("copy", _opt_text("copy", names=names),
                          value=(d.id, [[c["id"] for c in combo] for combo in combos], [c["id"] for c in basis]),
                          info={"copies": rows})
    return None


def _structure_options(ctx: Context, drafts: list) -> list[Option]:
    inds = [d for d in drafts if d.indicator is not None and d.copy_of is None]
    return [o for o in (_one_option(ctx, inds), _split_option(ctx, drafts), _copy_option(ctx, drafts)) if o]


def _group_span(ctx: Context, drafts: list) -> tuple[int, int, str]:
    a = min(r.item.start for d in drafts for r in d.items)
    b = max(r.item.end for d in drafts for r in d.items)
    quote = ctx.text["raw"][a:b].strip() or ", ".join(d.indicator.tok.quote for d in drafts if d.indicator)
    return a, b, quote


def _group_drafts(ctx: Context):
    for g, res in zip(ctx.judged.groups, ctx.judged.results):
        drafts = [d for d in res.drafts if d.id in ctx.rid]
        if g.designation or not drafts:
            continue
        yield g, res, drafts


def reading_points(ctx: Context) -> list[Point]:
    """Row 3 groups, and a requirement left unresolved by two or more basis values: the supported readings."""
    failed = _failed(ctx)
    out = []
    for g, res, drafts in _group_drafts(ctx):
        mixed = "mixed_reading" in failed and res.mixed
        unresolved_copy = "row_2_or_7" in failed and any(
            d.status == "unresolved" and d.indicator is not None and len(_basis_of(ctx, d.id)) >= 2 for d in drafts)
        if not (mixed or unresolved_copy):
            continue
        opts = _structure_options(ctx, drafts)
        if not opts:
            continue
        a, b, quote = _group_span(ctx, drafts)
        out.append(Point("reading", _ask_text("reading", quote=quote),
                         _finish(opts, None, _drafted_evidence(ctx, drafts)),
                         [ctx.rid[d.id] for d in drafts], fragments=_frags_in(a, b, ctx.fragments),
                         data={"group": g.start}, form="mixed" if mixed else "basis"))
    return out


def _drafted_evidence(ctx: Context, drafts: list) -> dict:
    """The evidence of the drafted reading, in the fields of the alternatives: for a head with lower items (or two
    or more indicators drafted apart) the common tables and the tables where the others are under the first."""
    inds = [d for d in drafts if d.indicator is not None and d.copy_of is None]
    if ctx.rel is None:
        return {}
    if len(drafts) == 1 and inds and inds[0].lower:
        common, under = _merge_evidence(ctx, inds[0].indicator, list(inds[0].lower))
    elif len(inds) >= 2:
        common, under = _merge_evidence(ctx, inds[0].indicator, [d.indicator for d in inds[1:]])
    else:
        return {}
    return {"common_tables": common, "under_head_tables": under}


def _draft_names(d) -> list[dict]:
    out, seen = [], set()
    for term in [d.indicator, d.context] + list(d.targets) + list(d.lower):
        if term is None:
            continue
        name = _catalog_name(term) or term.tok.quote
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name, **_term_info(term)})
    return out


def confirm_points(ctx: Context) -> list[Point]:
    """Check 8 only: does the draft reading of each item group match the question (with the supported options)."""
    if _failed(ctx) != {"unverified_structure"}:
        return []
    out = []
    for g, _res, drafts in _group_drafts(ctx):
        a, b, quote = _group_span(ctx, drafts)
        info = {"draft_names": [{"req": ctx.rid[d.id], "names": _draft_names(d)} for d in drafts]}
        out.append(Point("reading", _ask_text("confirm", quote=quote),
                         _finish(_structure_options(ctx, drafts), None, _drafted_evidence(ctx, drafts)),
                         [ctx.rid[d.id] for d in drafts], fragments=_frags_in(a, b, ctx.fragments), form="confirm",
                         info=info, data={"group": g.start}))
    return out


def grounding_points(ctx: Context) -> list[Point]:
    """Ungrounded split: a requirement whose exact indicator has no table holding its own targets and basis values:
    keep it apart, or merge it back into a grounded requirement of its group where the catalog reads it so."""
    if "ungrounded_split" not in _failed(ctx) or ctx.grounding is None:
        return []
    j = ctx.judged
    gr = ctx.grounding
    tables = {d.id: gr.tables(d) for d in j.drafts}
    out = []
    for u in j.drafts:
        if u.id not in ctx.rid or tables[u.id] or not _clean(u.indicator):
            continue
        ru = ctx.rid[u.id]
        own = indicator_tables(u.indicator)
        ev: dict = {"req": ru, "tables": len(own)}
        if u.targets:
            ev["with_targets"] = sum(1 for t in own if all(t in tg.tables for tg in u.targets))
        basis = gr.own_basis(u)
        if basis:
            ev["with_basis"] = sum(1 for t in own if all(_holds(ctx, t, c) for c in basis))
        opts = []
        for h in j.drafts:
            if h is u or h.group is not u.group or not tables[h.id] or h.indicator is None or h.id not in ctx.rid:
                continue
            rh = ctx.rid[h.id]
            label = _req_label(_req(ctx, rh))
            one = _one_option(ctx, [h, u], _opt_text("merge_breakdown", req=rh, label=label))
            if one is not None:
                opts.append(one)
        a = min(r.item.start for r in u.items)
        b = max(r.item.end for r in u.items)
        out.append(Point("reading", _ask_text("grounding", req=ru, label=_req_label(_req(ctx, ru))),
                         _finish(opts, _opt_text("keep_split")), [ru], fragments=_frags_in(a, b, ctx.fragments),
                         form="grounding", info={"grounding": ev}, data={"group": u.group.start}))
    return out


# ---- scope (check 7)


def scope_points(ctx: Context) -> list[Point]:
    if "scope_ambiguous" not in _failed(ctx):
        return []
    j = ctx.judged
    out = []
    for g, res in zip(j.groups, j.results):
        if not res.scope or g.designation:
            continue
        drafts = [d for d in res.drafts if d.id in ctx.rid]
        inds = [d for d in drafts if d.indicator is not None]
        mods = [j.reads[id(it)] for it in g.modifier if id(it) in j.reads]
        xs, sibling = [], None
        if mods and all(m.kind == "target" for m in mods) and len(inds) >= 2:
            xs = [t for m in mods for t in m.targets]
        else:
            broad = [d for d in drafts if d.broad and d.indicator is None and d.targets]
            if len(broad) == 1 and inds:
                xs, sibling = list(broad[0].targets), broad[0].id
        if not xs:
            continue
        x = ", ".join(t.tok.quote for t in xs)
        held = set.intersection(*[set(t.tables) for t in xs])
        together = [len(held & set(d.indicator.tables)) for d in inds]  # tables of each item holding X
        opts = [Option("all", _opt_text("all", x=x), info={"common_tables": min(together)}),
                Option("first", _opt_text("first", x=x, first=inds[0].indicator.tok.quote),
                       info={"common_tables": together[0]})]
        if sibling is None:  # a listed target is already a requirement of its own (the draft)
            opts.append(Option("separate", _opt_text("separate", x=x)))
        frags: set[str] = set()
        for t in xs:
            frags |= _frags_in(t.tok.start, t.tok.end, ctx.fragments)
        out.append(Point("scope", _ask_text("scope", x=x, items=", ".join(d.indicator.tok.quote for d in inds)),
                         _finish(opts), [ctx.rid[d.id] for d in drafts], fragments=frags,
                         data={"x": [_term_ref(t, ctx.fragments) for t in xs], "inds": [ctx.rid[d.id] for d in inds],
                               "sibling": ctx.rid.get(sibling) if sibling else None, "group": g.start}))
    return out


# ---- attribution (check 4)


def attribution_points(ctx: Context) -> list[Point]:
    if "ambiguous_attribution" not in _failed(ctx):
        return []
    reqs = ctx.interpretation["requirements"]
    if len(reqs) < 2:
        return []
    att = ctx.judgement.get("attribution") or {}
    out = []
    for c in ctx.conditions:
        if att.get(c["id"]) != "ambiguous":
            continue
        opts = []
        for r in reqs:
            if r["id"] not in ctx.did:
                continue
            d = ctx.drafts.get(ctx.did[r["id"]])
            info = {}
            if c["kind"] == "basis" and d is not None and d.indicator is not None:
                # tables of the requirement's indicator holding the basis value
                info["with_condition"] = sum(1 for t in d.indicator.tables if _holds(ctx, t, c))
            opts.append(Option("owner", _opt_text("owner", req=r["id"], label=_req_label(r)), value=ctx.did[r["id"]],
                               info=info))
        opts.append(Option("common", _opt_text("common")))
        out.append(Point("attribution", _ask_text("attribution", quote=c["quote"]), _finish(opts),
                         [r["id"] for r in reqs], fragments=_frags_in(c["span"][0], c["span"][1], ctx.fragments),
                         data={"condition": c["id"]}))
    return out


# ---- word (checks 5 and 6)


def _name_part_candidates(ctx: Context, stem: str, pos: int, end: int) -> list[tuple[str, dict]]:
    """Lexical candidates of the word joined with the name right before or after it (nearest name tokens), that
    hold a 2-gram of the word and are not a name of the question."""
    if ctx.rel is None or ctx.reading is None or len(stem) < 2 or pos < 0:
        return []
    names = [t for t in ctx.reading.toks if t.kind == "name"]
    before = max((t for t in names if t.end <= pos), key=lambda t: t.end, default=None)
    after = min((t for t in names if t.start >= end), key=lambda t: t.start, default=None)
    in_question = {index_key(t.quote) for t in names}
    grams = _bigrams(stem)
    scored: dict[str, float] = {}
    joins = ([index_key(before.quote) + stem] if before is not None else []) \
        + ([stem + index_key(after.quote)] if after is not None else [])
    for joined in joins:
        for k, score in ctx.rel.lexical(joined):
            if k in in_question or not (_bigrams(k) & grams):
                continue
            scored[k] = max(score, scored.get(k, 0.0))
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[:_LIMITS["names"]]
    out = []
    for k, _score in ranked:
        name = ctx.rel.key_names.get(k, k)
        out.append((name, _catalog_info(ctx, name)))
    return out


def _condition_candidates(ctx: Context, stem: str) -> list[str]:
    """Condition words (basis words and the catalog's family qualifiers) the word holds or shares most 2-grams with."""
    if len(stem) < 2:
        return []
    words = list(_CONDITION_WORDS) + (sorted(ctx.rel.qualifiers) if ctx.rel is not None else [])
    grams = _bigrams(stem)
    scored: dict[str, float] = {}
    for w in words:
        k = index_key(w)
        if len(k) < 2 or k in scored:
            continue
        gk = _bigrams(k)
        overlap = len(grams & gk) / max(len(grams), len(gk))
        score = 1.0 if k in stem else overlap
        if score >= _LIMITS["condition_min_overlap"]:
            scored[k] = score
    by_key = {index_key(w): w for w in reversed(words)}
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:_LIMITS["conditions"]]
    return [by_key[k] for k, _s in ranked]


def _holder(reqs: list[dict], pos: int) -> dict | None:
    return next((r for r in reqs if pos >= 0 and r["span"][0] <= pos < r["span"][1]), None)


def _leftover_point(ctx: Context, w: str) -> Point:
    reqs = ctx.interpretation["requirements"]
    toks = ctx.reading.toks if ctx.reading is not None else []
    pos = next((t.start for t in toks if t.kind == "word" and t.quote == w), -1)
    frags = _frags_in(pos, pos + len(w), ctx.fragments) if pos >= 0 else set()
    holder = _holder(reqs, pos)
    rids = [holder["id"]] if holder is not None else [r["id"] for r in reqs]
    # the word is all its requirement holds: no catalog name (the indicator, if any, is the item's own words), no target
    content = holder is not None and not holder["targets"] and (holder["indicator"] is None
                                                                or holder["indicator"].get("name") is None)
    stem = next((t.stem for t in toks if t.kind == "word" and t.quote == w), split_ending(key_of(w))[0])
    opts = []
    if not content:
        opts.append(Option("ignore", _opt_text("ignore")))
    for name, info in _name_part_candidates(ctx, stem, pos, pos + len(w)):
        opts.append(Option("name_part", _opt_text("name_part", name=name), value=name, info=info))
    for word in _condition_candidates(ctx, stem):
        opts.append(Option("condition", _opt_text("condition", word=word), value=word))
    if content:
        opts.append(Option("absent_indicator", _opt_text("absent_indicator")))
    return Point("word", _ask_text("word_leftover", word=w), _finish(opts), rids, fragments=frags,
                 data={"word": w}, form="leftover")


def _genitive_owners(ctx: Context, tok) -> list[str] | None:
    """Requirements whose indicators are listed right after "의" that ends a coordinated list starting at ``tok``
    ("A와 B의 X와 Y": X and Y); None when the name does not stand in such a list (token layer, ``model_verify``)."""
    toks = ctx.reading.toks
    k = next((n for n, t in enumerate(toks) if t is tok), None)
    starts = {r["indicator"]["span"][0]: r["id"] for r in ctx.interpretation["requirements"] if r["indicator"]}
    return (genitive_followers(toks, k, starts) or None) if k is not None else None


def _unused_point(ctx: Context, w: str) -> Point | None:
    if ctx.reading is None:
        return None
    used = used_tokens(ctx.judged)
    names = [t for t in ctx.reading.toks if t.kind == "name" and t.quote == w and not (t.negated or t.excluded)]
    tok = next((t for t in names if id(t) not in used), names[0] if names else None)
    if tok is None:
        return None
    term = term_of(tok, ctx.layer)
    reqs = ctx.interpretation["requirements"]
    # a target value standing before "의" + indicators ("A와 B의 X와 Y"): the target of those requirements only, never a
    # requirement of its own
    target_only = bool(term.types) and term.types <= TGT_TYPES
    owners = _genitive_owners(ctx, tok) if target_only else None
    opts = []
    for r in reqs:
        d = ctx.drafts.get(ctx.did.get(r["id"]))
        if d is None or d.indicator is None or ctx.rel is None or (owners and r["id"] not in owners):
            continue
        head = d.indicator
        common = set(term.tables) & set(head.tables)
        if not common or term.key == head.key:
            continue
        label = _req_label(r)
        rds = ctx.rel.readings(term.key, head.key)
        if _clean(term) and rds and all(x and x <= UNDER for x in rds.values()):
            under = sum(1 for t in common if rds.get(t, set()) & UNDER)
            opts.append(Option("attach_breakdown", _opt_text("attach_breakdown", req=r["id"], label=label),
                               value=r["id"], info={"common_tables": len(common), "under_head_tables": under}))
        elif term.types & TGT_TYPES or _target_tables(ctx, term, common):
            opts.append(Option("attach_target", _opt_text("attach_target", req=r["id"], label=label), value=r["id"],
                               info={"tables": len(common)}))
    attach = [o for o in opts if o.effect == "attach_target"]
    if owners and len(attach) >= 2:
        opts.append(Option("attach_target_all", _opt_text("attach_target_all", reqs=", ".join(o.value for o in attach)),
                           value=[o.value for o in attach], info={"tables": min(o.info["tables"] for o in attach)}))
    # a requirement of its own for the name is not offered: the judgement table has no item to stand it on (an
    # override of 2.5 cannot make one), so the choice could not go through the same checks
    holder = _holder(reqs, tok.start)
    rids = [holder["id"]] if holder is not None else [r["id"] for r in reqs]
    return Point("word", _ask_text("word_unused", word=w), _finish(opts), rids,
                 fragments=_frags_in(tok.start, tok.end, ctx.fragments), data={"word": w, "term": term},
                 form="unused")


def word_points(ctx: Context) -> list[Point]:
    failed = _failed(ctx)
    out, seen = [], set()
    if "leftover_word" in failed:
        for w in ctx.judgement.get("leftover") or []:
            if w not in seen:
                seen.add(w)
                out.append(_leftover_point(ctx, w))
    if "unused_name" in failed:
        for w in ctx.judgement.get("unused_names") or []:
            if w in seen:
                continue
            seen.add(w)
            p = _unused_point(ctx, w)
            if p is not None:
                out.append(p)
    return out


POINT_BUILDERS = {"name": name_points, "role": role_points, "appositive": appositive_points, "gloss": gloss_points,
                  "reading": reading_points, "confirm": confirm_points, "grounding": grounding_points,
                  "scope": scope_points, "attribution": attribution_points, "word": word_points}


def build_points(ctx: Context) -> list[Point]:
    """Points of every kind in ``POINT_ORDER``, numbered P1.. with options numbered o1.. over the request."""
    points: list[Point] = []
    for kind in POINT_ORDER:
        for p in POINT_BUILDERS[kind](ctx):
            if p.form == "leftover" and any(q.kind in ("name", "appositive") and p.fragments & q.fragments
                                            for q in points):
                continue  # the word is part of the fragment a name or appositive point decides
            points.append(p)
    n = 0
    for k, p in enumerate(points, 1):
        p.id = f"P{k}"
        for o in p.options:
            n += 1
            o.id = f"o{n}"
    return points


# ---------------------------------------------------------------- request


def _kind_label(c: Condition) -> str:
    if c["kind"] == "basis" and str(c.get("value") or "").endswith(BYEOL):
        return _WORDS["byeol_label"]
    return _WORDS["condition_kinds"].get(c["kind"], c["kind"])


def _option_row(o: Option) -> dict:
    row = {"id": o.id, "text": o.text}
    row.update(o.info)
    return row


def _draft_note(ctx: Context, rid: str) -> str:
    """How the code formed a draft requirement (judgement-table row; words in data/model_step_words.json)."""
    notes = _WORDS["draft_notes"]
    d = ctx.drafts.get(ctx.did.get(rid))
    rows = {x["id"]: x for x in ctx.judgement.get("requirement_draft") or []}
    row = rows.get(ctx.did.get(rid), {}).get("row")
    if d is not None and d.share:
        text = notes["share"]
    elif d is not None and d.broad:
        text = notes["broad"]
    elif d is not None and d.copy_of is not None:
        text = notes["6"]
    else:
        text = notes.get(str(row), notes["single"]) if row is not None else notes["single"]
    repeated = d is not None and row != 1 and (d.copy_of is not None or row == 6) and len(d.sources) >= 2
    return f"{text}({notes['repeat']})" if repeated else text


def payload_of(ctx: Context, points: list[Point]) -> dict:
    interp = ctx.interpretation
    draft = []
    for r in interp["requirements"]:
        ind = r["indicator"]
        draft.append({"id": r["id"], "indicator": (ind.get("name") or ind["quote"]) if ind else None,
                      "targets": [t.get("name") or t["quote"] for t in r["targets"]],
                      "breakdown": list(r.get("breakdown") or []), "conditions": list(r["condition_ids"]),
                      "note": _draft_note(ctx, r["id"])})
    out = {"question": ctx.text["raw"],
           "conditions": [{"id": c["id"], "kind": _kind_label(c), "quote": c["quote"]} for c in ctx.conditions],
           "fragments": [{"id": f["id"], "quote": f["quote"]} for f in ctx.fragments],
           "draft": draft}
    if interp["common_condition_ids"]:
        out["common"] = list(interp["common_condition_ids"])
    out["points"] = [{"id": p.id, "ask": p.ask, **p.info, "options": [_option_row(o) for o in p.options]}
                     for p in points]
    return out


def schema_of(points: list[Point]) -> dict:
    """One answer per point; the point ID and the choice are enumerations of that point's IDs (one item schema per
    point, joined with anyOf when there are several)."""
    items = [{"type": "object", "additionalProperties": False, "required": ["point", "choice", "reason"],
              "properties": {"point": {"type": "string", "enum": [p.id]},
                             "choice": {"type": "string", "enum": [o.id for o in p.options]},
                             "reason": {"type": "string", "maxLength": REASON_MAX}}} for p in points]
    return {"type": "object", "additionalProperties": False, "required": ["answers"],
            "properties": {"answers": {"type": "array", "minItems": len(points), "maxItems": len(points),
                                       "items": items[0] if len(items) == 1 else {"anyOf": items}}}}


ANSWER_FIELDS = frozenset({"point", "choice", "reason"})


def read_reply(text: str, points: list[Point]) -> tuple[dict, dict, str | None]:
    """(choices, reasons, problem): the reply holds the schema exactly (one ``answers`` list; each answer exactly
    point, choice and reason, all strings, reason at most ``REASON_MAX`` characters) and answers every point once with
    one of its own options; anything else is a problem and the draft stays."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}, {}, "reply_not_json"
    if not isinstance(data, dict) or set(data) != {"answers"} or not isinstance(data["answers"], list):
        return {}, {}, "reply_shape"
    by_id = {p.id: p for p in points}
    choices: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for a in data["answers"]:
        if not isinstance(a, dict) or set(a) != ANSWER_FIELDS or not all(isinstance(a[k], str) for k in ANSWER_FIELDS):
            return {}, {}, "reply_shape"
        if len(a["reason"]) > REASON_MAX:
            return {}, {}, "reply_reason_too_long"
        pid, oid = a["point"], a["choice"]
        if pid not in by_id:
            return {}, {}, "reply_unknown_point"
        if pid in choices:
            return {}, {}, "reply_duplicate_point"
        if oid not in {o.id for o in by_id[pid].options}:
            return {}, {}, "reply_unknown_choice"
        choices[pid] = oid
        reasons[pid] = a["reason"]
    if any(p.id not in choices for p in points):
        return {}, {}, "reply_missing_point"
    return choices, reasons, None


# ---------------------------------------------------------------- choices as overrides of the judgement table


ASK_EFFECTS = frozenset({"unknown", "differ", "absent", "absent_indicator", "name_part", "condition"})


def _mark(r: dict, reason: str) -> None:
    if reason not in r["reasons"]:
        r["reasons"].append(reason)


def _target_only(ctx: Context, name: str) -> bool:
    """A catalog name every candidate of which is a target value (no indicator reading)."""
    if ctx.layer is None:
        return False
    types = {c.get("type") for c in ctx.layer.name_spans().lookup(name)}
    return bool(types) and types <= TGT_TYPES


def _ind_start(ctx: Context, draft_id: str | None) -> int | None:
    d = ctx.drafts.get(draft_id) if draft_id else None
    return d.indicator.tok.start if d is not None and d.indicator is not None else None


def _owner_key(ctx: Context, draft_id: str | None) -> tuple | None:
    """The chosen requirement itself, not the first one sharing its indicator token (copies of one indicator):
    (indicator token start, its place among the drafts on that token, its view values)."""
    d = ctx.drafts.get(draft_id) if draft_id else None
    if d is None or d.indicator is None:
        return None
    start = d.indicator.tok.start
    same = [x for x in ctx.judged.drafts if x.indicator is not None and x.indicator.tok.start == start]
    return (start, same.index(d), owner_view(d))


def overrides_of(ctx: Context, points: list[Point], choices: dict[str, str]) -> tuple[Overrides, list[Point]]:
    """(the overrides of 2.5 the choices make, the points whose choice asks). Every option the code offers is an
    override of the judgement table or an ask (``ASK_EFFECTS``)."""
    chosen = {p.id: next(o for o in p.options if o.id == choices[p.id]) for p in points}
    ov = Overrides()
    asks: list[Point] = []
    for p in points:
        o = chosen[p.id]
        if o.effect in ASK_EFFECTS:
            asks.append(p)
            continue
        group = p.data.get("group")
        if p.kind == "name" and p.form == "role":
            if o.effect == "as_indicator":
                t = o.value.tok
                ov.roles[(t.start, t.end)] = "indicator"
        elif p.kind == "name":
            d = ctx.drafts.get(ctx.did.get(p.requirement_ids[0]))
            r = _req(ctx, p.requirement_ids[0])
            # "keep the draft" on a name point chooses the current candidate (the draft's guess)
            name = o.value if o.effect == "name" else (r["indicator"] or {}).get("name") if r else None
            if o.effect in ("name", "keep") and name and d is not None and d.indicator is not None:
                t = d.indicator.tok
                ov.names[(t.start, t.end)] = name
        elif p.kind == "appositive" and p.form == "gloss":
            start = _ind_start(ctx, p.data["draft"])
            how = {"gloss_a": "a", "gloss_b": "b", "keep": "keep"}.get(o.effect)
            if start is not None and how:
                ov.drafts[start] = {"gloss": how, "b": p.data["gloss"].tok.start}
        elif p.kind == "appositive":
            start = _ind_start(ctx, p.data["draft"])
            how = "b" if o.effect == "same_b" else "a" if o.effect == "keep" else None
            if start is not None and how:
                ov.drafts[start] = {"appositive": how}
                if how == "b":  # the chosen candidate itself (several candidates: "A, 즉 B, 즉 C")
                    ov.drafts[start]["alt"] = o.value.tok.start
        elif p.kind == "reading":
            if o.effect == "one":
                spec = ov.groups.setdefault(group, {})
                spec.update({"reading": "one", "head": _ind_start(ctx, o.value[0]),
                             "members": [_ind_start(ctx, x) for x in o.value]})
            elif o.effect in ("split", "copy"):
                ov.groups.setdefault(group, {})["reading"] = o.effect
        elif p.kind == "scope":
            if o.effect in ("all", "first", "separate"):
                ov.groups.setdefault(group, {}).update({"scope": o.effect, "x": [x["span"] for x in p.data["x"]]})
        elif p.kind == "attribution":
            cid = p.data["condition"]
            if o.effect == "owner":
                ov.places[cid] = _owner_key(ctx, o.value)
            elif o.effect == "common":
                ov.places[cid] = "common"
        elif p.kind == "word":
            if o.effect == "ignore":
                ov.ignored.add(p.data["word"])
            elif o.effect in ("attach_target", "attach_breakdown", "attach_target_all"):
                t = p.data["term"].tok
                role = "breakdown" if o.effect == "attach_breakdown" else "target"
                for rid in (o.value if o.effect == "attach_target_all" else [o.value]):
                    start = _ind_start(ctx, ctx.did.get(rid))
                    if start is not None:
                        ov.attach.append(((t.start, t.end), start, role))
    # check 8 asks whether a specification case verified the structure; the model answered every point (its confirm
    # points included) with a reading, so the structure is the model-confirmed one (status rule: checks 1-7 and the
    # V-checks decide)
    ov.structure_confirmed = not asks
    return ov, asks


@dataclass
class Rechecked:
    judgement: Judgement  # 2.5 again with the overrides (certain left as the checks give it)
    interpretation: Interpretation  # 2.7 again with the overrides
    failures: list  # checks 1-7 (8 unless every confirm point was answered), the shape check, the V-checks


def recheck(ctx: Context, ov: Overrides) -> Rechecked:
    """The choices through the same judgement table and checks as the rule path: judge_fragments with the
    overrides, the certainty checks as they are (absolute, not against the draft), settle_requirements, the
    contract shape check and the catalog V-checks (``model_verify``)."""
    text, conds, frags, terms = ctx.text, ctx.conditions, ctx.fragments, ctx.terms
    j = judge_fragments(text, conds, frags, terms, ctx.layer, overrides=ov)
    failures = list(j["failed_conditions"])
    asked = {**j, "certain": False}  # 2.7 status: the model never makes a judgement certain
    interp = settle(text, conds, frags, terms, asked, ctx.layer, ov)
    analysis = {"text": text, "conditions": conds, "fragments": frags, "terms": terms, "judgement": asked,
                "interpretation": interp}
    failures += [f"shape:{p}" for p in check_analysis(analysis, text["raw"])]
    failures += verify_after_model(analysis, ctx.layer, sorted(ov.ignored))
    return Rechecked(judgement=asked, interpretation=interp, failures=failures)


# ---------------------------------------------------------------- the step


def _analysis(ctx: Context, judgement: Judgement, interp: Interpretation) -> Analysis:
    return {"text": ctx.text, "conditions": ctx.conditions, "fragments": ctx.fragments, "terms": ctx.terms,
            "judgement": judgement, "interpretation": interp}


def _point_row(p: Point) -> dict:
    return {"id": p.id, "kind": p.kind, "ask": p.ask,
            "options": [{"id": o.id, "text": o.text, "effect": o.effect,
                         **{k: v for k, v in o.info.items() if k in RECORD_INFO}} for o in p.options],
            "requirement_ids": list(p.requirement_ids)}


def _record(judgement: Judgement, outcome: str, points: list[Point], choices: dict, reasons: dict,
            notes: list[str]) -> Judgement:
    out = dict(judgement)
    out["certain"] = False  # the model never makes a judgement certain
    out["model_step"] = outcome
    rec = {"points": [_point_row(p) for p in points], "choices": dict(choices), "reasons": dict(reasons),
           "prompt_sha256": prompt_sha256(), "outcome": outcome}
    if notes:
        rec["notes"] = list(notes)
    out["model"] = rec
    return out


@dataclass
class Prepared:
    ctx: Context | None
    points: list[Point]
    payload: dict | None
    schema: dict | None
    skip: str | None  # certain, no_indicator, no_point, too_many_points:N


def prepare(text: TextView, conditions: list[Condition], fragments: list[Fragment], terms: list[Term],
            judgement: Judgement, layer) -> Prepared:
    """Points, payload and schema of one question (no call). Certain judgements are not sent, nor are judgements
    without an indicator-side term (check 9, user decision 10/19: the question asks)."""
    if judgement["certain"]:
        return Prepared(None, [], None, None, "certain")
    ctx = make_context(text, conditions, fragments, terms, judgement, layer)
    if NO_INDICATOR in _failed(ctx):
        return Prepared(ctx, [], None, None, NO_INDICATOR)
    points = build_points(ctx)
    if not points:
        return Prepared(ctx, points, None, None, "no_point")
    if len(points) > MAX_POINTS:
        return Prepared(ctx, points, None, None, f"too_many_points:{len(points)}")
    return Prepared(ctx, points, payload_of(ctx, points), schema_of(points), None)


def prepare_question(question: str, layer: CatalogLayer | None = None) -> Prepared:
    """``prepare`` after 2.1-2.5 of one question (the dry run of the model run uses it)."""
    text = normalize_text(question)
    conditions = find_conditions(text, layer)
    fragments = split_fragments(text, conditions, layer)
    terms = attach_terms(fragments, layer, text, conditions)
    judgement = judge_fragments(text, conditions, fragments, terms, layer)
    return prepare(text, conditions, fragments, terms, judgement, layer)


def run(text: TextView, conditions: list[Condition], fragments: list[Fragment], terms: list[Term],
        judgement: Judgement, layer, model) -> Analysis:
    """2.6 with a model client (``complete(node, system, payload, schema) -> ModelReply``) and 2.7 after it."""
    prep = prepare(text, conditions, fragments, terms, judgement, layer)
    if prep.ctx is None:  # certain: passes as with the model off
        interp = settle(text, conditions, fragments, terms, judgement, layer)
        return {"text": text, "conditions": conditions, "fragments": fragments, "terms": terms,
                "judgement": judgement, "interpretation": interp}
    ctx = prep.ctx
    if prep.payload is None:
        interp = copy.deepcopy(ctx.interpretation)
        notes = [prep.skip or "no_point"]
        if prep.skip and prep.skip.startswith("too_many_points"):
            for r in interp["requirements"]:  # too many points to send: ask (plan 06, 2)
                _mark(r, ASK_REASON)
        elif prep.skip == NO_INDICATOR:
            # check 9: no indicator-side term, nothing for the model to choose among; every requirement asks
            for r in interp["requirements"]:
                _mark(r, ASK_REASON)
            interp["status"] = "ask"
            notes = [f"ask:{NO_INDICATOR}"]
        return _analysis(ctx, _record(judgement, "skipped", prep.points, {}, {}, notes), interp)
    try:
        reply = model.complete(NODE, prompt_text(), prep.payload, prep.schema)
    except ModelError as err:
        if err.http_status == 429:
            # deferred, not failed (plan 06 approval: 429 is filled by a later run): the step is still pending; the
            # record (outcome skipped, the contract's closest value) carries the note
            rec = _record(judgement, "skipped", prep.points, {}, {}, [DEFERRED_NOTE])
            rec["model_step"] = "pending"
            return _analysis(ctx, rec, ctx.interpretation)
        return _analysis(ctx, _record(judgement, "failed", prep.points, {}, {}, [f"error:{err.error_class}"]),
                         ctx.interpretation)
    choices, reasons, problem = read_reply(reply.text, prep.points)
    if problem is not None:
        return _analysis(ctx, _record(judgement, "failed", prep.points, {}, {}, [problem]), ctx.interpretation)
    ov, asks = overrides_of(ctx, prep.points, choices)
    if asks:
        # cannot decide, different ones, not in the catalog, a name part or condition word: the draft stays and the
        # requirements of those points ask (no other choice is applied: the result could not be confirmed anyway)
        interp = copy.deepcopy(ctx.interpretation)
        asked = {i for p in asks for i in p.requirement_ids}
        for r in interp["requirements"]:
            if r["id"] in asked or not asked:
                _mark(r, ASK_REASON)
        return _analysis(ctx, _record(judgement, "done", prep.points, choices, reasons,
                                      [f"ask:{p.id}" for p in asks]), interp)
    chk = recheck(ctx, ov)
    if chk.failures:
        interp = copy.deepcopy(ctx.interpretation)
        for r in interp["requirements"]:
            _mark(r, ASK_REASON)
        return _analysis(ctx, _record(judgement, "rejected", prep.points, choices, reasons,
                                      [f"{RECHECK_NOTE}{c}" for c in chk.failures]), interp)
    _confirm(chk.interpretation)
    return _analysis(ctx, _record(chk.judgement, "done", prep.points, choices, reasons, []), chk.interpretation)


def _confirm(interp: Interpretation) -> None:
    """Status rule (user decision 10/19): the choices passed the judgement table, checks 1-7 (check 8 too unless every
    confirm point was answered) and the V-checks, and none asks: every requirement of status ok is model-confirmed,
    and the interpretation is ok when every requirement is (no several-official-names branch open). The judgement
    stays not certain."""
    reqs = interp["requirements"]
    for r in reqs:
        # a broad requirement (targets only) has no indicator to choose a table by: it is never confirmed
        if r["status"] == "ok" and r["indicator"] is not None:
            _mark(r, CONFIRMED_REASON)
    if reqs and interp["status"] == "ask" and all(CONFIRMED_REASON in r["reasons"] for r in reqs) \
            and not any("branch" in r["reasons"] for r in reqs):
        interp["status"] = "ok"
