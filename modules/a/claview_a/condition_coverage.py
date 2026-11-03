"""Bounded source-anchor coverage, not a judgement of meaning or fulfillment.

Money units and numeric index bases may be omitted from every model field. Detect
those literal omissions independently of the model's extracted list. Polarity and
statistic scope remain the model's responsibility; an unassigned mention requests
the existing recovery, never applies a requirement to every statistic.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .quotes import quote_in_question

VERSION = "literal-condition-coverage/2026-11-03.1"
MISSING = "explicit_condition_missing"
UNREADABLE = "explicit_condition_unreadable"
CODES = (MISSING, UNREADABLE)

_CURRENCIES = {"원": "KRW", "원화": "KRW", "KRW": "KRW", "달러": "USD", "미달러": "USD",
               "미국달러": "USD", "USD": "USD", "엔": "JPY", "JPY": "JPY", "유로": "EUR",
               "EUR": "EUR", "위안": "CNY", "CNY": "CNY"}
_MONEY = re.compile(
    r"(?<![가-힣A-Za-z])(?P<scale>천만|백만|조|억|만|천|백)?\s*"
    r"(?P<currency>미국\s*달러|미달러|달러|원화|원|엔|유로|위안|KRW|USD|JPY|EUR|CNY)"
    r"(?=$|[^가-힣A-Za-z]|단위|기준|으로|로|은|는|을|를|만|와|과)", re.IGNORECASE)
_INDEX = re.compile(r"(?<![\d.])(?P<year>\d{4})\s*=\s*(?P<value>\d+(?:\.\d+)?)(?!\d|[.,]\d)")


def anchors(text: str) -> list[dict]:
    """Return literal shapes only. A year without an equals sign is not an index base."""
    found = []
    for match in _MONEY.finditer(text):
        currency = re.sub(r"\s+", "", match["currency"])
        key = (_CURRENCIES.get(currency.upper()) or _CURRENCIES[currency], match["scale"] or "")
        found.append({"kind": "money_unit", "key": list(key), "quote": match.group().strip(),
                      "start": match.start(), "end": match.end()})
    for match in _INDEX.finditer(text):
        found.append({"kind": "index_base", "key": [match["year"], str(Decimal(match["value"]).normalize())],
                      "quote": match.group(), "start": match.start(), "end": match.end()})
    return sorted(found, key=lambda row: row["start"])


def _has(text: str, anchor: dict) -> bool:
    for other in anchors(text):
        if other["kind"] != anchor["kind"]:
            continue
        if other["key"] == anchor["key"]:
            return True
        # A bare currency requires no scale. A stated scale must match in full;
        # merely retaining the currency cannot cover a scaled source unit.
        if anchor["kind"] == "money_unit" and not anchor["key"][1] and other["key"][0] == anchor["key"][0]:
            return True
    return False


def unresolved_row(row: dict, anchor: dict) -> bool:
    """Only the diagnosed unknown polarity row may change during recovery."""
    return row.get("polarity") in ("unreadable", "undetermined") and \
        _has(row.get("quote", ""), anchor) and _has(row.get("value", ""), anchor)


def catalog_facts(decision: dict, views: dict) -> list[dict]:
    """Snapshot structured facts on the actual final tables, without reading meaning.

    The snapshot travels with a decision, so checking a partial recovery and its
    merged result uses exactly the same table/item linkage. Never inherit units
    from a parent or borrow evidence from a sibling table.
    """
    facts = []
    for selected in decision.get("final_tables") or []:
        table = selected["table_id"]
        view = views.get(table)
        if view is None or not view.has_meta:
            continue
        table_units = sorted(set(view.table_units))
        overrides = sorted({u for _, u in view.itm_rows if u} | {it.unit for it in view.items if it.unit})
        linked, problems = [], []
        item_rows = [r for r in decision.get("model_evidence") or []
                     if r.get("kind") == "item" and r.get("table") == table]
        for row in item_rows:
            ids = (row.get("item") or {}).get("item_ids") or []
            matches = [it for it in view.items if len(ids) == 1 and it.item_id == ids[0]]
            if "item" not in row.get("fact_of_table", []) or len(matches) != 1:
                problems.append("item_link_unconfirmed")
                continue
            item = matches[0]
            if any(not hasattr(it, "parent_id") for it in view.items):
                problems.append("item_hierarchy_unconfirmed")
                continue
            if any(it.obj_id == item.obj_id and it.parent_id == item.item_id for it in view.items):
                # A parent may be a valid aggregate in its own right. Its unit
                # still proves nothing about a request for its children; leave
                # that semantic distinction to the model, not a name heuristic.
                problems.append("parent_item_does_not_prove_children")
                continue
            if not item.unit:
                problems.append("item_unit_missing")
                continue
            linked.append({"item_id": item.item_id, "obj_id": item.obj_id, "name": item.name_raw,
                           "unit": item.unit, "quote": row["text"], "leaf_in_catalog": True})
        if decision.get("invalid_item_evidence"):
            problems.append("invalid_item_evidence")
        facts.append({"table": table, "table_units": table_units, "item_unit_overrides": overrides,
                      "cited_items": linked, "cited_item_count": len(item_rows),
                      "item_link_problems": sorted(set(problems)),
                      "source": "catalog.meta.UNIT_and_ITM.UNIT_NM"})
    return facts


def _unit_guarantees(unit: str, anchor: dict) -> bool:
    same_kind = [a for a in anchors(unit) if a["kind"] == anchor["kind"]]
    return bool(same_kind) and all(_has(a["quote"], anchor) for a in same_kind)


def _catalog_support(anchor: dict, decision: dict, records: list[dict]) -> list[dict]:
    # A catalog fact does not resolve unreadable scope, turn an exclusion into a
    # requirement, or erase an already detected polarity contradiction.
    if any(r.get("polarity") in ("excluded", "not_basis") for r in records) or \
            any(r["code"] == "basis_polarity_invalid" for r in decision.get("reasons") or []):
        return []
    selected = {t["table_id"] for t in decision.get("final_tables") or []}
    support = []
    for fact in decision.get("condition_catalog_facts") or []:
        if fact["table"] not in selected:
            continue
        common = {"source": "catalog_guarantee", "statistic": decision["statistic"],
                  "table": fact["table"], "metadata_source": fact["source"]}
        units, overrides = fact["table_units"], fact["item_unit_overrides"]
        if units and all(_unit_guarantees(u, anchor) for u in units + overrides):
            support.append({**common, "scope": "table", "units": units,
                            "checked_item_overrides": overrides})
            continue
        linked = fact["cited_items"]
        if linked and len(linked) == fact["cited_item_count"] and not fact["item_link_problems"] and \
                all(_unit_guarantees(row["unit"], anchor) for row in linked):
            support.append({**common, "scope": "cited_items", "items": linked})
    # An edition split or another final table must not disappear from the proof.
    return support if selected and {r["table"] for r in support} == selected else []


def _within_quote(question: str, quote: str, anchor: dict) -> bool:
    # The statistic's own source may locate a mention, but is never evidence that
    # the model accounted for it. Repeated excerpts remain ambiguous candidates.
    source = re.sub(r"\s+", "", question)
    part = re.sub(r"\s+", "", quote)
    start = len(re.sub(r"\s+", "", question[:anchor["start"]]))
    end = len(re.sub(r"\s+", "", question[:anchor["end"]]))
    return bool(part) and any(m.start() <= start and end <= m.start() + len(part)
                              for m in re.finditer(f"(?={re.escape(part)})", source))


def _account(question: str, anchor: dict, decision: dict, output: dict) -> tuple[list[dict], bool]:
    sid = decision["statistic"]
    records = []
    unreadable = False
    for row in output.get("splitters") or []:
        if sid not in row["for"] or not quote_in_question(row["quote"], question):
            continue
        if not _has(row["quote"], anchor) or not _has(row.get("value", ""), anchor):
            continue
        if row.get("polarity") in ("unreadable", "undetermined"):
            unreadable = True
            continue
        if row.get("polarity") in ("required", "any_of", "excluded", "allowed", "not_basis"):
            records.append({"source": "splitter", "statistic": sid, "axis": row["axis"],
                            "value": row["value"], "polarity": row["polarity"], "quote": row["quote"]})
    focus = decision.get("focus") or {}
    if focus.get("status") == "grounded" and _has(focus.get("text", ""), anchor):
        quotes = [q for q in focus.get("quotes") or [] if quote_in_question(q, question) and _has(q, anchor)]
        if quotes:
            records.append({"source": "focus", "statistic": sid, "quotes": quotes})
    # A fact on an unselected card (or another statistic's card) provides no coverage.
    selected = {r["table_id"] for r in decision.get("final_tables") or []}
    for row in decision.get("model_evidence") or []:
        if row.get("table") not in selected or row.get("kind") not in row.get("fact_of_table", []):
            continue
        if _has(row.get("text", ""), anchor):
            records.append({"source": "selected_card_evidence", "statistic": sid,
                            "table": row["table"], "kind": row["kind"], "text": row["text"]})
    return records, unreadable


def assess(question: str, output: dict, decisions: list[dict], fixed: dict | None = None) -> list[dict]:
    """Account for source mentions using current and immutable recovery context.

    No semantic scope is inferred outside a statistic's own quoted source. If none
    accounts for a shared/unassigned mention, all current statistics are review
    targets, not recipients of an automatically required condition.
    """
    current_ids = {d["statistic"] for d in decisions}
    contexts = [(d, output) for d in decisions]
    contexts += [(d, fixed["output"]) for d in (fixed or {}).get("decisions", [])]
    rows = []
    for anchor in anchors(question):
        local = [(d, out) for d, out in contexts if _within_quote(question, d.get("quote", ""), anchor)]
        candidates = local or contexts
        evidence, unreadable_ids, catalog_evidence, unsupported_ids = [], [], [], []
        for d, out in candidates:
            records, unreadable = _account(question, anchor, d, out)
            evidence.extend(records)
            if unreadable:
                unreadable_ids.append(d["statistic"])
            support = [] if unreadable else _catalog_support(anchor, d, records)
            catalog_evidence.extend(support)
            if not support:
                unsupported_ids.append(d["statistic"])
        # An explicitly unresolved semantic row cannot be hidden by an incidental
        # citation of the same literal. It goes through the same bounded recovery.
        catalog_guaranteed = bool(catalog_evidence) and not unsupported_ids
        status = "unreadable" if unreadable_ids else "accounted" if evidence or catalog_guaranteed else "missing"
        targets = unreadable_ids if unreadable_ids else [d["statistic"] for d, _ in candidates]
        rows.append({**anchor, "status": status, "scope": "quoted_statistic" if local else "unassigned",
                     "review_statistics": sorted(set(targets) & current_ids), "evidence": evidence,
                     "support_basis": "model_accounted" if evidence else
                         "catalog_guaranteed" if catalog_guaranteed else "unresolved",
                     "catalog_evidence": catalog_evidence, "catalog_unconfirmed_statistics": unsupported_ids,
                     "semantic_fulfillment": "not_checked"})
    return rows


def apply(question: str, output: dict, decisions: list[dict], fixed: dict | None = None,
          mutable: set[str] | None = None) -> list[dict]:
    rows = assess(question, output, decisions, fixed)
    by_id = {d["statistic"]: d for d in decisions}
    for row in rows:
        if row["status"] == "accounted":
            continue
        for sid in row["review_statistics"]:
            # A genuine pool miss keeps the search path. The searched answer is
            # checked again; missing literal coverage must not suppress re-search.
            if by_id[sid].get("model_kind") == "missing" or mutable is not None and sid not in mutable:
                continue
            if any(r["code"] in CODES and (r.get("detail") or {}).get("anchor", {}).get("start") == row["start"]
                   for r in by_id[sid]["reasons"]):
                continue
            by_id[sid]["reasons"].append({"code": UNREADABLE if row["status"] == "unreadable" else MISSING,
                "detail": {"anchor": dict(row), "recovery": {"status": "pending"},
                           "repair": "attribute_in_other_splitter_keep_valid_conditions"}})
    return rows
