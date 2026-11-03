"""Guards for the existing one recovery when its table changes.

The first pool and the user's named target remain authoritative. These facts do not
select a replacement: the second model response still supplies its choice and evidence.
"""

from __future__ import annotations

from copy import deepcopy

from . import check as CH
from . import periods as PR
from .contract import SEMANTIC_PERIOD_CONTRACTS
from .search.tables import nospace


def identities(record: dict, cards: list[dict], views: dict) -> None:
    """Freeze catalog identities mentioned in each statistic's own source scope.

    An exact title, survey path or edition mention is conservative evidence to protect
    during recovery, not a natural-language classifier for the first selection. Names
    of other statistics are not borrowed. A single statistic also owns the full question.
    Mutually incompatible mentions establish no positive target: freeze the original
    choice for unrelated repairs and retain the ambiguity without guessing negation.
    """
    output = record.get("model_output") or {}
    stats = output.get("statistics") or []
    names: dict[tuple[str, str], set[str]] = {}
    for card in cards:
        members = {r["table_id"] for r in card["tables"]}
        names.setdefault(("table", card["name"]), set()).update(members)
        for row in card["tables"]:
            table = row["table_id"]
            names.setdefault(("table", row["title"]), set()).add(table)
            names.setdefault(("table_id", table), set()).add(table)
            for bracket in row.get("brackets") or []:
                names.setdefault(("edition", bracket), set()).add(table)
            view = views.get(table)
            for part in (getattr(view, "path", "") or "").split(">")[:-1]:
                if part.strip():
                    names.setdefault(("survey_or_agency", part.strip()), set()).add(table)
    frozen, ambiguous = {}, {}
    for stat in stats:
        sid = stat["id"]
        sources = [stat["quote"]]
        sources += [q for f in output.get("focuses") or [] if sid in f["for"] for q in f["quotes"]]
        if len(stats) == 1:
            sources.append(record.get("question") or "")
        scopes = [nospace(q) for q in sources]
        matches = [(kind, text, tables) for (kind, text), tables in names.items()
                   if len(nospace(text)) >= (2 if kind in ("survey_or_agency", "table_id") else 4)
                   and any(nospace(text) in q for q in scopes)]
        # A longer exact title is more specific than a nested generic family name.
        matches = [(kind, text, tables) for kind, text, tables in matches
                   if not any(kind == k and nospace(text) != nospace(t) and nospace(text) in nospace(t)
                              for k, t, _ in matches)]
        if matches:
            facts = [{"kind": k, "text": t, "tables": sorted(ids)} for k, t, ids in matches]
            if set.intersection(*(ids for _, _, ids in matches)):
                frozen[sid] = facts
            else:
                original = next((d for d in record.get("decisions") or [] if d["statistic"] == sid), {})
                tables = set(_table_ids(original) or [original.get("representative")]) - {None}
                ambiguous[sid] = {"code": "incompatible_identity_mentions", "mentions": facts,
                                  "fixed_tables": sorted(tables), "positive_target_verified": False}
    record["recovery_identities"] = frozen
    record["recovery_identity_ambiguities"] = ambiguous


def identity_losses(first: dict, second: dict, sid: str, other: str) -> list[str]:
    """A recovery may never replace an explicitly named catalog identity silently."""
    after = next(d for d in second["decisions"] if d["statistic"] == other)
    chosen = set(_table_ids(after) or [after.get("representative")]) - {None}
    ambiguity = (first.get("recovery_identity_ambiguities") or {}).get(sid)
    if ambiguity:
        fixed = set(ambiguity["fixed_tables"])
        return [] if fixed and chosen == fixed else ["explicit_target:ambiguous_table_change"]
    return [f"explicit_target:{g['kind']}:{g['text']}"
            for g in (first.get("recovery_identities") or {}).get(sid, [])
            if not chosen or not chosen <= set(g["tables"])]


def _rows(output: dict, key: str, sid: str) -> list[dict]:
    return [r for r in output.get(key) or [] if sid in r.get("for", [])]


def _table_ids(decision: dict) -> list[str]:
    return [r["table_id"] if isinstance(r, dict) else r for r in decision.get("final_tables") or []]


def _item_scope(table: str, items: list[str], lookup: CH.Cards) -> list[dict] | None:
    """Bind each citable name to one actual object/item, not merely one card spelling.

    Item IDs may be reused across objects. A name or ID with more than one catalog
    owner is not enough evidence for this limited table replacement.
    """
    facts = lookup.facts_by_kind(table)["item"]
    result = []
    for name in items:
        ids = lookup.resolve_item(table, name).get("item_ids") or []
        if name not in facts or len(ids) != 1:
            return None
        owners = [it for it in lookup.views[table].items if it.item_id == ids[0]]
        named = [it for it in lookup.views[table].items if it.name_raw == name]
        if len(owners) != 1 or len(named) != 1 or owners[0].name_raw != name or not owners[0].obj_id:
            return None
        result.append({"name": name, "object_id": owners[0].obj_id, "item_id": owners[0].item_id})
    return result


def prepare(record: dict, cards: list[dict], views: dict) -> None:
    """Offer a bounded table review only for a verified choice's past shortfall.

    A candidate must already be visible in round one, hold all cited items, share the
    agency and known basis/units, and actually cover the whole explicit range at the
    requested frequency. This opens the shared recovery slot; it is not equivalence
    proof or automatic selection. Relative, open, unresolved and answered conditions
    keep their existing behavior.
    """
    output = record.get("model_output") or {}
    contract = (record.get("parse") or {}).get("answer_contract")
    if contract not in SEMANTIC_PERIOD_CONTRACTS:
        return
    lookup = CH.Cards(cards, views)
    refs = {r["table_id"]: f"{c['card_id']}.t{i + 1}"
            for c in cards for i, r in enumerate(c["tables"])}
    for d in record.get("decisions") or []:
        sid, origin = d["statistic"], d.get("representative")
        reasons = d.get("reasons") or []
        if (d.get("outcome") != "failed" or d.get("model_kind") != "choice" or origin not in lookup.row or
                len(reasons) != 1 or reasons[0].get("code") != "period_not_covered" or
                reasons[0].get("detail") != "past_shortfall" or d.get("condition_answers") or
                d.get("partial_periods") or (record.get("recovery_identities") or {}).get(sid) or
                (record.get("recovery_identity_ambiguities") or {}).get(sid) or
                (d.get("focus") or {}).get("status") != "grounded" or
                (d.get("citation") or {}).get("status") != "verified" or
                _rows(output, "aggregations", sid)):
            continue
        periods, frequencies = _rows(output, "periods", sid), _rows(output, "frequencies", sid)
        if (len(periods) != 1 or len(frequencies) != 1 or periods[0].get("meaning") != "range" or
                periods[0].get("form") != "absolute" or periods[0].get("read") != "ok" or
                periods[0].get("start") in (None, "none", "open", "") or
                periods[0].get("end") in (None, "none", "open", "") or
                frequencies[0].get("value") not in ("Y", "H", "Q", "M")):
            continue
        items = list(d.get("item_evidence") or [])
        if not items or not _item_scope(origin, items, lookup):
            continue
        reading = CH.condition_reading(deepcopy(d), record["question"], output, semantic=True,
                                       as_of=record["as_of"], answer_contract=contract)
        if reading["problems"]:
            continue
        conditions = reading["conditions"]
        original = PR.resolve(conditions, views[origin], record["as_of"],
                              context=PR._calendar_context(origin, conditions, record["as_of"]))
        requested = (original.get("periods") or {}).get("requested_months") or []
        frequency = frequencies[0]["value"]
        if len(requested) != 1:
            continue
        allowed, item_scopes = [], {}
        for table in refs:
            if table == origin or table not in views:
                continue
            if not getattr(views[origin], "org_id", None) or views[origin].org_id != views[table].org_id:
                continue
            if lookup.row[origin].get("basis", {}) != lookup.row[table].get("basis", {}):
                continue
            before_units, after_units = PR._units(views[origin]), PR._units(views[table])
            item_scope = _item_scope(table, items, lookup)
            if before_units != after_units or not item_scope:
                continue
            result = PR.resolve(conditions, views[table], record["as_of"],
                                context=PR._calendar_context(table, conditions, record["as_of"]))
            spans = result.get("periods") or {}
            if (result.get("status") != "ok" or result.get("frequency") != frequency or
                    spans.get("requested_months") != requested or spans.get("missing") or
                    PR._need(PR._months(spans.get("covered_months"))) != PR._need(PR._months(requested))):
                continue
            verified = CH.verify_table(d["quote"], table, items, lookup)
            if not verified["ok"]:
                continue
            allowed.append({"table": refs[table], "table_id": table, "items": items,
                            "frequency": frequency, "period": deepcopy(requested)})
            item_scopes[table] = item_scope
        if allowed:
            # Internal checked identities only; keep the recovery model's facts unchanged.
            d["period_reselection_items"] = item_scopes
            d["period_reselection"] = {"code": "period_table_reselection", "origin": origin,
                                       "candidates": allowed, "period": deepcopy(requested),
                                       "frequency": frequency, "items": items,
                                       "rule": "Review only these first-round candidates. Preserve valid conditions; "
                                               "select with fresh card evidence or leave unresolved. Item-name and "
                                               "coverage checks are not proof of equivalent statistical meaning."}


def period_losses(first: dict, second: dict, sid: str, other: str) -> list[str]:
    """Validate the limited re-selection after ordinary checks, before adopting it."""
    before = next(d for d in first["decisions"] if d["statistic"] == sid)
    bound = before.get("period_reselection")
    if not bound:
        return []
    after = next(d for d in second["decisions"] if d["statistic"] == other)
    allowed = {x["table_id"] for x in bound["candidates"]}
    tables = _table_ids(after)
    if (after.get("outcome") != "selected" or after.get("model_kind") != "choice" or len(tables) != 1 or
            after.get("representative") not in allowed or tables[0] != after.get("representative")):
        return ["period_reselection:unverified_choice"]
    assigned = (after.get("period") or {}).get("tables") or []
    if (len(assigned) != 1 or assigned[0].get("period") != bound["period"] or
            (after.get("frequency") or {}).get("value") != bound["frequency"]):
        return ["period_reselection:request_changed"]
    expected = {r["item_id"]: r for r in (before.get("period_reselection_items") or {}).get(tables[0], [])}
    cited = set()
    for evidence in after.get("model_evidence") or []:
        item = evidence.get("item") or {}
        ids = item.get("item_ids") or []
        if (evidence.get("kind") == "item" and evidence.get("table") == tables[0] and
                "item" in (evidence.get("fact_of_table") or []) and len(ids) == 1 and
                ids[0] in expected and item.get("name") == expected[ids[0]]["name"]):
            cited.add(ids[0])
    if (not expected or not set(expected) <= cited or (after.get("citation") or {}).get("status") != "verified"):
        return ["period_reselection:item_evidence_missing"]
    return []
