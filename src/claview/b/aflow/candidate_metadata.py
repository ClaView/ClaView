"""Scope-local evidence from full classification metadata (axis totals, question terms).

Titles, generated measure text, truncated cards and evaluation labels are not
inputs. Unknown or incomplete metadata cannot establish absence.
"""
from __future__ import annotations

import re
import unicodedata

from .axes import axis_name
from .measure import norm
from .q6g_validation import FUNC, PERIOD_WORDS, strip_p

_WORD = re.compile(r"[가-힣A-Za-z0-9]+")
_TOTAL = {"전체", "전국", "총계", "합계", "총액", "계", "종합", "전산업"}
_SIZE = ((re.compile(r"큰\s*기업"), "대기업"), (re.compile(r"작은\s*기업"), "중소기업"))


def _complete(meta):
    if not (meta and meta.get("items") and meta.get("dims")
            and "ITM" not in meta.get("missing_fields", [])
            and all(any(it.get("obj_id") == d.get("obj_id") and it.get("name_raw")
                        for it in meta["items"]) for d in meta["dims"])):
        return False
    index = {(it.get("obj_id"), it.get("item_id")): it for it in meta["items"]}
    if len(index) != len(meta["items"]) or any(not it.get("item_id") for it in meta["items"]):
        return False
    for item in meta["items"]:
        seen, current = set(), item
        while current.get("up_item_id"):
            key = (current.get("obj_id"), current["up_item_id"])
            if key in seen or key not in index:
                return False
            seen.add(key)
            current = index[key]
    return True


def _label(text):
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\[[^]]*\]|\d+\)\s*$", "", text)
    return text.strip()


def question_terms(view, *, size_aliases=False, content_only=False):
    """Literal atoms in verified requirement spans, with original offsets."""
    question = view.get("question", "")
    spans = [sp for r in view.get("requirements", []) for sp in r.get("quote_spans", [])]
    for field in ("cond_breakdown", "cond_basis"):
        spans += [c["span"] for c in view.get(field, []) if c.get("span")
                  and c.get("polarity", "pos") == "pos"
                  and (field == "cond_breakdown" or c.get("axis") == "classification")]
    excluded = [c["span"] for field in ("cond_period", "cond_frequency", "cond_unit", "cond_exclude", "cond_basis", "cond_breakdown")
                for c in view.get(field, []) if c.get("span")
                and (field in ("cond_period", "cond_frequency", "cond_unit", "cond_exclude")
                     or c.get("polarity", "pos") == "neg")]
    if content_only:
        excluded += [c["span"] for field in ("cond_basis", "cond_breakdown")
                     for c in view.get(field, []) if c.get("span")
                     and (c.get("kind") == "axis"
                          or (field == "cond_basis" and c.get("axis") != "classification")
                          or c.get("value", c.get("text", "")).endswith("별"))]
    visible = "".join(ch if any(a <= i < b for a, b in spans)
                      and not any(a <= i < b for a, b in excluded) else " "
                      for i, ch in enumerate(question))
    terms = []
    for m in _WORD.finditer(visible):
        word = m.group()
        clean = strip_p(word) if len(word) > 2 else word
        if len(clean) < 2 or clean in FUNC or word in FUNC or PERIOD_WORDS.fullmatch(word):
            continue
        if content_only and clean.endswith("별"):
            continue
        terms.append({"text": clean, "span": [m.start(), m.start() + len(clean)],
                      "variants": sorted({norm(word), norm(clean)})})
    if size_aliases:
        for pattern, replacement in _SIZE:
            for m in pattern.finditer(visible):
                terms.append({"text": m.group(), "span": list(m.span()), "variants": [norm(replacement)]})
    return terms


def _dimensions(meta):
    return [(d, [it for it in meta["items"] if it.get("obj_id") == d.get("obj_id")]) for d in meta["dims"]]


def _aggregate_possible(items):
    if any(norm(_label(it["name_raw"])) in _TOTAL for it in items):
        return True
    # One complete rooted hierarchy may aggregate even without a total label.
    # Several root leaves (e.g. individual countries) do not establish a total.
    by_id = {it.get("item_id"): it for it in items if it.get("item_id")}
    if len(by_id) != len(items):
        return True  # Unknown hierarchy: absence of a total is not established.
    if any(it.get("up_item_id") and it["up_item_id"] not in by_id for it in items):
        return True
    roots = [it["item_id"] for it in items if not it.get("up_item_id")]
    if len(roots) != 1:
        return False
    root = roots[0]
    for it in items:
        key, seen = it["item_id"], set()
        while key != root:
            if key in seen or key not in by_id:
                return True  # Broken/ambiguous metadata is unknown.
            seen.add(key)
            key = by_id[key].get("up_item_id")
    return True


def _member_requested(terms, items):
    # Prefixes are lexical upper terms; arbitrary infixes would match unrelated
    # place names (e.g. a housing category inside a concatenated region name).
    values = [norm(_label(it["name_raw"])) for it in items]
    atoms = [norm(m.group()) for it in items for m in _WORD.finditer(_label(it["name_raw"]))]
    return any(value.startswith(term) for t in terms for term in t["variants"]
               if len(term) >= 2 for value in values + atoms)


def axis_total_veto(view, rule_meta, model_meta):
    if not _complete(rule_meta) or not _complete(model_meta):
        return None
    rule_dims = _dimensions(rule_meta)
    terms = question_terms(view, size_aliases=True)
    for dim, items in _dimensions(model_meta):
        values = {norm(_label(it["name_raw"])) for it in items}
        axis = axis_name(dim.get("obj_nm") or "")
        if not axis or len(values) < 2:
            continue
        if any(axis == axis_name(rd.get("obj_nm") or "")
               or values == {norm(_label(it["name_raw"])) for it in ri} for rd, ri in rule_dims):
            continue
        explicit_axis = any(c.get("polarity", "pos") == "pos"
                            and (c.get("kind") == "axis" or c.get("axis") == "classification")
                            and axis_name(c.get("value", c.get("text", ""))) == axis
                            for field in ("cond_basis", "cond_breakdown") for c in view.get(field, []))
        if explicit_axis or _aggregate_possible(items) or _member_requested(terms, items):
            continue
        return {"axis": dim, "item_count": len(items), "total": "absent", "requested": False}
    return None


