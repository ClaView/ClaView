"""Arbitration veto and definition merging, based only on literal/catalog evidence."""
from __future__ import annotations

import re

from . import rules
from .axes import axis_name
from .measure import norm
from .q6g_validation import strip_p


def catalog_properties(documents, metadata):
    vocabulary = {axis_name(d.get("obj_nm") or "") for m in metadata.values() for d in m.get("dims", [])}
    vocabulary.discard("")
    result = {}
    for doc in documents:
        title = doc["title"]
        advertised = {axis_name(a) for a, _, _ in rules.find_axes(title)}
        advertised |= {axis_name(t) for t in rules.bracket_tokens(title) if axis_name(t) in vocabulary}
        result[doc["table_id"]] = {"path_indicates_legacy": doc.get("path_indicates_legacy", False),
                                    "advertised_axes": sorted(advertised)}
    return result


def _seasonal(meta):
    return any(rules._axis_of(token) == ("seasonal", "계절조정")
               for token in rules.bracket_tokens(meta.get("title", "")))


def _old_period_requested(view, meta):
    for period in view["cond_period"]:
        if period.get("status") != "explicit":
            continue
        start = rules.stamp_months(period.get("start"))
        end = rules.stamp_months(period.get("end")) or start
        if not start or not end:
            continue
        for coverage in meta.get("prd_by_freq", {}).values():
            lo, hi = rules.stamp_months(coverage.get("start")), rules.stamp_months(coverage.get("end"))
            if lo and hi and start[0] <= hi[1] and lo[0] <= end[1]:
                return True
    return False


def arbitration_veto(rule, model, rows, view, properties):
    """T3 candidate veto: the model table is an unrequested variant of the rule table.

    A seasonally adjusted table that the question did not ask for, or a legacy-path
    table when no old period or legacy series was requested. Never introduces a
    harder conflict than the rule table already has.
    """
    index = {r["table_id"]: r for r in rows}
    if not rule or not model or rule == model or rule not in index or model not in index:
        return None
    from .q6g import hardset
    if hardset(index[rule]) - hardset(index[model]):
        return None  # A soft candidate veto cannot introduce a harder conflict.
    rm, mm = view["meta"].get(rule), view["meta"].get(model)
    rp, mp = properties.get(rule, {}), properties.get(model, {})
    if rm is not None and mm is not None:
        seasonal_request = any(c.get("axis") == "seasonal" and c.get("value") == "계절조정"
                               and c.get("polarity", "pos") == "pos" for c in view["cond_basis"])
        if _seasonal(mm) and not _seasonal(rm) and not seasonal_request:
            return "unrequested_variant"
        legacy_request = bool(re.search(r"옛|과거|구계열|구\s*계열|이전\s*계열", view["query"]))
        if (mp.get("path_indicates_legacy") is True and rp.get("path_indicates_legacy") is False
                and not legacy_request and not _old_period_requested(view, mm)):
            return "unrequested_variant"
    return None


_SUBTRACT = re.compile(r"[가-힣A-Za-z0-9·]+에서\s*[가-힣A-Za-z0-9·]+(?:을|를)\s*뺀(?:\s*(?:것|값|수치))?")
_DEFINITION = re.compile(r"에서.+(?:을|를)\s*뺀|즉|뜻|의미|정의|말하")


def _definition_zones(question):
    zones = []
    for match in re.finditer(r"\(([^()]*)\)", question):
        if not _DEFINITION.search(match.group(1)):
            continue
        parent = re.search(r"[가-힣A-Za-z0-9·]+\s*$", question[:match.start()])
        if parent:
            zones.append((match.start(1), match.end(1), parent.span()))
    for match in _SUBTRACT.finditer(question):
        if any(a <= match.start() and match.end() <= b for a, b, _ in zones):
            continue
        # An unparenthesized subtraction needs an explicit following result name.
        parent = re.match(r"\s+([가-힣A-Za-z0-9·]+)", question[match.end():])
        if parent:
            zones.append((match.start(), match.end(),
                          (match.end() + parent.start(1), match.end() + parent.end(1))))
    return zones


def merge_definition_requirements(question, kept, dropped):
    removed = set()
    for start, end, parent_span in _definition_zones(question):
        parent_text = norm(strip_p(question[slice(*parent_span)]))
        parents = [(i, k) for i, k in enumerate(kept) if i not in removed and k["status"] == "grounded"
                   and any(norm(strip_p(w)) == parent_text for w, _ in k["words"])]
        if not parents:
            continue
        def anchor(row):
            spans = [sp for _, sp in row["words"] if sp[1] <= parent_span[1]]
            return (min(a for a, _ in spans), parent_span[1])
        widths = [sum(not ch.isspace() for ch in question[slice(*anchor(k))]) for _, k in parents]
        widest = [pair for pair, width in zip(parents, widths) if width == max(widths)]
        if len(widest) != 1:
            continue
        parent_index, parent = widest[0]
        parent_span = anchor(parent)
        for i, child in enumerate(kept):
            if i == parent_index or i in removed:
                continue
            terms = [strip_p(w) for w, _ in child["words"]]
            if not terms or not all(t and t in question[start:end] for t in terms):
                continue
            # A repeated component outside the definition/result is independent.
            if any(any(not (start <= m.start() and m.end() <= end)
                       and not (parent_span[0] <= m.start() and m.end() <= parent_span[1])
                       for m in re.finditer(re.escape(t), question)) for t in terms):
                continue
            spans = [(start + question[start:end].find(t), start + question[start:end].find(t) + len(t)) for t in terms]
            parent["extra_sp"] = parent.get("extra_sp", []) + spans
            parent["basis"] |= child["basis"]
            removed.add(i)
            dropped.append({"quote": child["quote"], "reason": "definition_merged", "into": parent["quote"]})
    return [k for i, k in enumerate(kept) if i not in removed]
