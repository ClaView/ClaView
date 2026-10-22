"""Conservative J5 metadata evidence: prefix-condition support and total-absent scope ordering.

Evidence and flag names (cx_item_prefix, cx_scope_total) are kept as recorded identifiers.
"""
from __future__ import annotations

import json
import re
from datetime import date

from .axes import axis_name
from .candidate_metadata import _member_requested, axis_total_veto, question_terms, _label
from .catalog import nospace
from .measure import norm
from .rules import stamp_months


_NAME_COMPONENT = re.compile(r"[가-힣]+(?:\s+[가-힣]+)*")
# General spatial scope words, never aliases for a particular region or table.
_SPATIAL_SCOPE = frozenset({"지역", "지방", "권역", "수도권", "시도", "시군구"})
_PERIOD_ORDER = {"covered": 0, "unknown": 1, "partial": 2, "outside": 3}


def item_prefix_support(state, meta, already_covered):
    """Support explicit listed members from a complete hyphen-first component.

    A component is only condition evidence. It is never an exact item binding
    and does not claim that the suffix is the requested measure. Negative,
    ambiguous, unanchored, axis-only and already-covered conditions abstain.
    """
    question = state["question"]
    support = {}
    for condition in state["cond_breakdown"]:
        cid = condition["condition_id"]
        if (cid in already_covered or condition.get("status") != "explicit"
                or condition.get("kind") != "list" or condition.get("polarity", "pos") != "pos"):
            continue
        span, term = condition.get("span"), condition.get("text", "")
        if not span or not (0 <= span[0] < span[1] <= len(question)) or len(nospace(term)) < 2:
            continue
        if nospace(question[span[0]:span[1]]) != nospace(term):
            continue
        hits = []
        for item in meta.get("items", []):
            prefix, separator, suffix = (item.get("name_raw") or "").partition("-")
            # A conservative Korean name component excludes code/range joins,
            # English compounds and hyphens inside parentheses or formulae.
            if (separator and suffix.strip() and _NAME_COMPONENT.fullmatch(prefix.strip())
                    and nospace(prefix) == nospace(term)):
                hits.append({"obj_id": item["obj_id"], "item_id": item["item_id"],
                             "name_raw": item["name_raw"], "component": prefix.strip()})
        if hits:
            support[cid] = {"term": term, "span": span, "method": "hyphen_first_component",
                            "item_count": len(hits), "examples": hits[:3]}
    return support


def item_prefix_evidence(table_id, support, meta):
    return {"table_id": table_id, "kind": "item_prefix", "stance": "support",
            "text": json.dumps({"conditions": support, "exact_item_binding": False}, ensure_ascii=False),
            "source_field": "condition.quote × ITM.ITM_NM first hyphen component",
            "source_snapshot": meta["source_snapshot"]}


def _reference_date(as_of, winner_meta, loser_meta):
    if as_of:
        values, source = [as_of], "as_of"
    else:
        values = [m.get("source_snapshot", "").split("#")[0] for m in (winner_meta, loser_meta)]
        source = "earlier_metadata_snapshot"
    try:
        reference = min(date.fromisoformat(value) for value in values)
    except (TypeError, ValueError):
        return None
    return {"date": reference.isoformat(), "month": reference.year * 12 + reference.month - 1, "source": source}


def period_comparison(winner, loser, state, meta, as_of=None):
    """Allow promotion only without worsening same-frequency period evidence.

    Not-requested coverage ends are a non-regression constraint, not a claim
    that publication has stopped. Their ends are capped at a common reference.
    Requested unknown/unknown remains a tie; unlike frequencies never compare.
    """
    fit = state.get("fit_period", {})
    comparisons = []
    for req in state["requirements"]:
        sid = req["scope_id"]
        wfit, lfit = fit.get(winner, {}).get(sid, {}), fit.get(loser, {}).get(sid, {})
        requested = lfit.get("requested", {}).get("frequencies", [])
        if wfit.get("requested", {}).get("frequencies", []) != requested:
            return {"allowed": False, "reason": "different_frequency_requests", "comparisons": comparisons}
        def periods(tid, own):
            if own:
                return own.get("period_by_frequency", {})
            return {freq: {"status": "unknown", "coverage": [cov.get("start"), cov.get("end")],
                           "reason": "fit_unavailable"}
                    for freq, cov in meta[tid].get("prd_by_freq", {}).items()}
        wp, lp = periods(winner, wfit), periods(loser, lfit)
        if not wp and not lp:
            comparisons.append({"scope_id": sid, "status": "unknown_equal", "reason": "both_period_metadata_unavailable"})
            continue
        frequencies = set(lp) & set(requested) if requested else set(lp)
        if not frequencies or not frequencies <= set(wp):
            return {"allowed": False, "reason": "frequency_not_preserved", "comparisons": comparisons}
        for freq in sorted(frequencies):
            w, l = wp[freq], lp[freq]
            row = {"scope_id": sid, "frequency": freq, "winner_status": w.get("status", "unknown"),
                   "loser_status": l.get("status", "unknown")}
            comparisons.append(row)
            if _PERIOD_ORDER.get(row["winner_status"], 1) > _PERIOD_ORDER.get(row["loser_status"], 1):
                return {"allowed": False, "reason": "worse_period_status", "comparisons": comparisons}
            if w.get("reason") == l.get("reason") == "not_requested":
                wc, lc = w.get("coverage", []), l.get("coverage", [])
                we = stamp_months(wc[1]) if len(wc) == 2 else None
                le = stamp_months(lc[1]) if len(lc) == 2 else None
                reference = _reference_date(as_of, meta[winner], meta[loser])
                if we and le and we[1] == le[1]:
                    row.update(winner_end=wc[1], loser_end=lc[1], end_comparison="equal_without_reference")
                    continue
                if not we or not le or not reference:
                    return {"allowed": False, "reason": "coverage_end_uncomparable", "comparisons": comparisons}
                row.update(reference=reference, winner_end=wc[1], loser_end=lc[1],
                           winner_effective_end=min(we[1], reference["month"]),
                           loser_effective_end=min(le[1], reference["month"]))
                if row["winner_effective_end"] < row["loser_effective_end"]:
                    return {"allowed": False, "reason": "shorter_unrequested_coverage_end", "comparisons": comparisons}
    return {"allowed": True, "reason": "period_not_worse", "comparisons": comparisons}


def _compound_member_uncertain(terms, axis, winner_meta, loser_meta):
    """Infix overlap only blocks a preference; it never establishes a binding.

    This protects an abbreviated part of a concatenated member without a place
    dictionary. A member explicitly present on the preferred table is retained.
    """
    losing_values = [norm(_label(item["name_raw"])) for item in loser_meta.get("items", [])
                     if item.get("obj_id") == axis.get("obj_id")]
    winning_values = {norm(_label(item["name_raw"])) for item in winner_meta.get("items", [])}
    return any(len(term) >= 2 and term not in winning_values and any(term in value for value in losing_values)
               for atom in terms for term in atom["variants"])


def _spatial(terms):
    """A general spatial scope word, also in its axis form ('지역별')."""
    return any(({*term["variants"]} | {axis_name(v) for v in term["variants"]}) & _SPATIAL_SCOPE for term in terms)


def _unattributed_terms(view, requirements):
    """Question words that no requirement quote covers.

    A model quote can omit a word the question states ('지역 가상지수' quoted as
    '가상지수'). The omission is not evidence that the word was not requested,
    so such a word keeps the ordering on hold. A word inside another
    requirement's quote (peer_quote_spans) belongs to that requirement and is
    not applied here. Period/frequency/unit/exclusion spans and negative
    conditions are removed as in question_terms.
    """
    question = view["question"]
    quoted = [sp for req in requirements for sp in req["quote_spans"] + req.get("peer_quote_spans", [])]
    free, start = [], None
    for i in range(len(question) + 1):
        inside = i == len(question) or any(a <= i < b for a, b in quoted)
        if not inside and start is None:
            start = i
        elif inside and start is not None:
            free.append([start, i])
            start = None
    if not free:
        return []
    return question_terms(dict(view, requirements=[{"quote_spans": free}], cond_basis=[
        c for c in view["cond_basis"] if c.get("polarity", "pos") == "neg"], cond_breakdown=[
        c for c in view["cond_breakdown"] if c.get("polarity", "pos") == "neg"]))


def _axis_mentioned(terms, axis, items):
    """The axis name or one of its members appears (exactly, as a prefix, or inside a joined member)."""
    name = axis_name(axis.get("obj_nm") or "")
    values = [norm(_label(item["name_raw"])) for item in items]
    return (_member_requested(terms, items)
            or any(name and axis_name(v) == name for term in terms for v in term["variants"])
            or any(len(v) >= 2 and v in value for term in terms for v in term["variants"] for value in values))


def scope_total_order(rows, state, meta, as_of=None):
    """Reorder the leading semantic tie only when an extra axis lacks a total.

    All non-search components and the title stem must agree, and both tables
    must match the measure. Explicit classification/list/exclusion requests
    abstain: their wording may differ from the metadata axis label. This does
    not infer a national intent or penalize tables merely for being regional.
    A spatial word, or the extra axis name or member, that the question states
    outside every requirement quote also abstains: the attribution is uncertain.
    """
    if not rows or "blocked" in rows[0]["flags"] or "measure_match" not in rows[0]["flags"]:
        return {}
    if state.get("cond_breakdown") or state.get("cond_exclude") or any(
            c.get("axis") == "classification" for c in state.get("cond_basis", [])):
        return {}
    view = {key: state.get(key, []) for key in (
        "question", "requirements", "cond_basis", "cond_breakdown", "cond_exclude",
        "cond_period", "cond_frequency", "cond_unit")}
    # Rule fallback requirements have a literal span, not Q6G quote_spans.
    requirements = []
    for req in view["requirements"]:
        spans = req.get("quote_spans")
        if "quote_spans" not in req and req.get("source") != "q6g" and req.get("span"):
            spans = [req["span"]]
        if not spans or any(not (0 <= a < b <= len(view["question"])) for a, b in spans):
            return {}
        requirements.append(dict(req, quote_spans=spans))
    if not requirements:
        return {}
    view["requirements"] = requirements
    terms = question_terms(view)
    outside = _unattributed_terms(view, requirements)
    if _spatial(terms) or _spatial(outside):
        return {}

    def signature(row):
        return (nospace((meta[row["table_id"]].get("title") or "").split("(")[0]),
                {k: v for k, v in row["components"].items() if k != "search"})

    signature0 = signature(rows[0])
    if not signature0[0]:
        return {}
    slots = [i for i, r in enumerate(rows) if signature(r) == signature0
             and "measure_match" in r["flags"] and "blocked" not in r["flags"]]
    group = [rows[i] for i in slots]
    if len(group) < 2:
        return {}
    edges, reasons = {}, {}
    periods = {(w["table_id"], l["table_id"]): period_comparison(w["table_id"], l["table_id"], state, meta, as_of)
               for w in group for l in group if w is not l}
    # A rejected promotion must remain rejected even when another preference
    # makes a topological sort move an unrelated table earlier as a side effect.
    for i, earlier in enumerate(group):
        for later in group[i + 1:]:
            pair = later["table_id"], earlier["table_id"]
            if not periods[pair]["allowed"]:
                edges.setdefault(pair[0], set()).add(pair[1])
    for loser in group:
        tid = loser["table_id"]
        for winner in group:
            other = winner["table_id"]
            if tid == other:
                continue
            reason = axis_total_veto(view, meta[other], meta[tid])
            period = periods[other, tid]
            if (reason and period["allowed"] and not axis_total_veto(view, meta[tid], meta[other])
                    and not _compound_member_uncertain(terms, reason["axis"], meta[other], meta[tid])
                    and not _axis_mentioned(outside, reason["axis"],
                                            [item for item in meta[tid].get("items", [])
                                             if item.get("obj_id") == reason["axis"].get("obj_id")])):
                edges.setdefault(tid, set()).add(other)
                reasons.setdefault(tid, []).append({"preferred_table": other, **reason, "period_check": period})
    # Stable topological order. A cycle is ambiguous and leaves the tie intact.
    remaining, ordered = list(group), []
    while remaining:
        ids = {r["table_id"] for r in remaining}
        first = next((r for r in remaining if not (edges.get(r["table_id"], set()) & ids)), None)
        if first is None:
            return {}
        ordered.append(first)
        remaining.remove(first)
    if ordered == group:
        return {}
    evidence = {}
    for slot, row in zip(slots, ordered):
        rows[slot] = row
        row["policy_state"], row["policy_name"] = "applied", "cx_scope_total"
        if row["table_id"] in reasons:
            tid = row["table_id"]
            row["flags"].append("cx_scope_total_absent")
            evidence[f"cx_scope_total:{tid}"] = {
                "table_id": tid, "kind": "scope_total", "stance": "against",
                "text": json.dumps({"reason": "unrequested_extra_axis_without_total", "comparisons": reasons[tid]}, ensure_ascii=False),
                "source_field": "ITM.OBJ_NM × ITM.ITM_NM × ITM.UP_ITM_ID",
                "source_snapshot": meta[tid]["source_snapshot"],
            }
    return evidence
