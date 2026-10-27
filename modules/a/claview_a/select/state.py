"""3.6 choose_table, 3.8 decide_state and collect_selections (plan 04, 4-1 and 4-6).

- ``choose_table``: dimensions the question did not set follow the policy of data/selection_policy.json
  (representative: one table per dimension kind, cover.representatives; keep_all: every equal family table, cap 3,
  cover.unspecified). Every complete alternative is a contender, whatever its evidence tier (the tier only orders them
  and shows in the difference rows); with no complete one, the partial ones covering as many cells. One -> it. Two
  or more -> difference rows built from axis values (not title words, detail 3.5), the first by order, ``model_step``
  "pending" and confirm (the model picks in cycle 2, after approval). Unspecified dimensions of the chosen family
  are filled in (cover.unspecified).
- ``decide_state``: no_table > confirm > auto (plan 04, 4-6 table). auto is a white list (``AUTO_CONDITIONS``):
  every condition must be shown to hold; each one that is not adds its reason code and the state is confirm.
  Score gaps are recorded, never used for the state.
- ``collect_selections``: requirements that share a table are one query group.
"""

from __future__ import annotations

from .candidates import family_key
from .cover import DEFAULT_POLICY, POLICIES, family_members, order_alternatives, representatives, unspecified
from .differences import set_dimension_filter
from .tables import selection_catalog

DIFF_VALUES = 8  # axis values shown per difference row


def _axis_values(view) -> tuple[set[str], set[str]]:
    return {name for _, name in view.dims if name}, {it.name_raw for it in view.items if it.name_raw}


def differences(contenders: list[dict], views: dict) -> list[dict]:
    """One row per contender: the axis names and values its tables have and the other contenders' tables lack."""
    sets = []
    for a in contenders:
        axes, values = set(), set()
        for t in a["tables"]:
            ax, va = _axis_values(views[t])
            axes |= ax
            values |= va
        sets.append((axes, values))
    rows = []
    for k, a in enumerate(contenders):
        other_axes = set().union(*(s[0] for j, s in enumerate(sets) if j != k)) if len(sets) > 1 else set()
        other_values = set().union(*(s[1] for j, s in enumerate(sets) if j != k)) if len(sets) > 1 else set()
        rows.append({"family": a["family"], "table_ids": list(a["tables"]),
                     "only_axes": sorted(sets[k][0] - other_axes),
                     "only_values": sorted(sets[k][1] - other_values)[:DIFF_VALUES]})
    return rows


def choose_table(cover: dict, layer, policy: str | None = None) -> dict:
    """{chosen, contenders, tables, unspecified, added, conventions, differences, model_step} from the cover result
    of one requirement. ``policy`` (default: data/selection_policy.json): representative or keep_all."""
    policy = policy or DEFAULT_POLICY
    if policy not in POLICIES:
        raise ValueError(f"unknown unspecified policy {policy!r}")
    alts = order_alternatives(cover["alternatives"])
    if not alts:
        return {"chosen": None, "contenders": [], "tables": [], "unspecified": [], "added": [], "conventions": [],
                "differences": [], "model_step": "skipped"}
    first = alts[0]
    if first["complete"]:
        same = [a for a in alts if a["complete"]]
    else:
        same = [a for a in alts if len(a["covered"]) == len(first["covered"])]
    # plan 10 D (가): only the contenders meeting every dimension the question set stay; one left -> confirm
    kept, filtered, d_resolved = set_dimension_filter(same, cover)
    if d_resolved:  # not settled by the table names: every contender stays (the default never moves)
        same = kept
    else:
        filtered = []
    first = chosen = same[0]
    tables, marks, conventions = list(chosen["tables"]), [], []
    pruned = _filter_record(filtered, cover)  # plan 11 R0: what the cuts here left out (record only)
    if policy == "representative":
        members = {c: [t for t in family_members(layer, family_key(layer, c)) if t in cover["views"]]
                   for c in chosen["tables"]}
        tables, marks, conventions = representatives(chosen["tables"], members, cover["provided"],
                                                     set(cover["usable"]), cover["views"], cover.get("words") or {},
                                                     cover.get("scores") or {}, cover.get("order") or {},
                                                     cover.get("cells") or set(), set(cover.get("siblings") or []),
                                                     set(cover.get("named") or []), pruned)
    elif chosen["family"] != "mixed":
        members = [t for t in family_members(layer, chosen["family"]) if t in cover["views"]]
        tables, marks = unspecified(chosen["tables"], members, cover["provided"], set(cover["usable"]),
                                    cover["views"], cover.get("words"))
    views = selection_catalog(layer).views
    rows = differences(same, views) if len(same) > 1 else []
    added = [t for t in tables if t not in chosen["tables"]]
    return {"chosen": chosen, "contenders": same, "tables": tables, "unspecified": marks, "added": added,
            "conventions": conventions, "differences": rows, "model_step": "pending" if len(same) > 1 else "skipped",
            "filtered": filtered, "d_resolved": d_resolved, "pruned": pruned}


def _filter_record(filtered: list[dict], cover: dict) -> list[dict]:
    """Plan 11 R0 (7-12): a contender the set-dimension filter dropped because its table names do not say the word
    (``title_unproven``: not refuted) or because a table refutes it (``refuted``: conflict). Record only."""
    words = cover.get("words") or {}
    out = []
    for f in filtered:
        refuted = any((words.get(t) or {}).get(f["condition_id"]) == "conflict" for t in f["table_ids"])
        out.append({"step": "set_dimension", "table_ids": list(f["table_ids"]), "family": f["family"],
                    "condition_id": f["condition_id"], "reason": "refuted" if refuted else "title_unproven"})
    return out


def interpretation_undecided(analysis: dict, rid: str) -> bool:
    """The interpretation did not settle this requirement: requirement unresolved, interpretation ask/partial, or
    the 2.5 judgement not certain."""
    interp = analysis.get("interpretation") or {}
    req = next((r for r in interp.get("requirements") or [] if r.get("id") == rid), {})
    judgement = analysis.get("judgement") or {}
    return (req.get("status") == "unresolved" or interp.get("status") in ("ask", "partial")
            or judgement.get("certain") is False)


def _unproven(recheck: dict, checks: tuple[str, ...], inside: bool) -> bool:
    return any((u["check"] in checks) == inside for u in recheck.get("unproven") or [])


# Plan 04, 4-6 as a white list: auto only when every line holds; a line that does not hold adds its reason code.
# (reason, holds(analysis, rid, choice, recheck, flags, signals))
AUTO_CONDITIONS = (
    # the cover is complete: every requested cell (targets, listed values, period segments, frequency, basis
    # values) is given by a chosen table, and the chosen family covered it by itself
    ("partial_cover", lambda a, r, ch, rc, fl, sg: rc["floor"] == "auto" and ch["chosen"]["complete"]),
    # every own and shared condition (ambiguous ones are not the requirement's) passes on every chosen table:
    # frequency, basis and "X별" words (two or more on different axes), unit, exclusion
    ("unproven_condition", lambda a, r, ch, rc, fl, sg: not _unproven(rc, ("period",), False)),
    # the period check of every chosen table is covered (partial only inside a complete join; never unknown or
    # snapshot lag), and the requested period was resolved and not run into the snapshot lag
    ("period_unknown", lambda a, r, ch, rc, fl, sg: not _unproven(rc, ("period",), True)
     and "period_unknown" not in fl and "period_lag" not in fl),
    # no unspecified dimension among the family tables (basis-axis values and qualifiers compared)
    ("unspecified", lambda a, r, ch, rc, fl, sg: not ch["unspecified"]),
    ("unspecified_more", lambda a, r, ch, rc, fl, sg: not any(m.get("more") for m in ch["unspecified"])),
    # one family alternative
    ("family_alternatives", lambda a, r, ch, rc, fl, sg: len(ch["contenders"]) <= 1),
    # plan 10 D: a single alternative left by a dimension the question set is gone on with confirm, never auto
    ("d_resolved", lambda a, r, ch, rc, fl, sg: not ch.get("d_resolved")),
    # nothing was cut: the exact search and the term tables
    ("search_truncated", lambda a, r, ch, rc, fl, sg: not ch["chosen"].get("truncated")),
    ("term_tables_truncated", lambda a, r, ch, rc, fl, sg: not sg.get("term_tables_truncated")),
    # the interpretation settled the requirement
    ("interpretation_undecided", lambda a, r, ch, rc, fl, sg: not interpretation_undecided(a, r)),
    # plan 07 S3: a requirement without an indicator, searched by its targets, is never auto (decision 3: confirm)
    ("no_indicator", lambda a, r, ch, rc, fl, sg: not sg.get("target_search")),
)


def decide_state(analysis: dict, rid: str, *, unsupported: bool, no_evidence: bool, choice: dict | None,
                 recheck: dict | None, cover_flags: list[str], signals: dict | None = None) -> tuple[str, list[str]]:
    """(state, reasons) by plan 04, 4-6: no_table > confirm > auto; auto only when every AUTO_CONDITIONS line
    holds."""
    if unsupported:
        return "no_table", ["unsupported"]
    if no_evidence:
        return "no_table", ["no_evidence"]
    if not choice or not choice.get("chosen") or not recheck or recheck["floor"] == "no_table":
        return "no_table", ["no_cover"]
    reasons = [code for code, holds in AUTO_CONDITIONS
               if not holds(analysis, rid, choice, recheck, list(cover_flags), signals or {})]
    return ("confirm" if reasons else "auto"), reasons


def collect_selections(selections: list[dict]) -> list[dict]:
    """Query groups: requirements whose table groups share a table are queried together."""
    groups: list[dict] = []
    for s in selections:
        if s["status"] == "no_table":
            continue
        tables = [t["table_id"] for t in s["tables"]]
        joined = [g for g in groups if set(g["table_ids"]) & set(tables)]
        merged = {"requirement_ids": [], "table_ids": []}
        for g in joined:
            groups.remove(g)
            merged["requirement_ids"] += g["requirement_ids"]
            merged["table_ids"] += [t for t in g["table_ids"] if t not in merged["table_ids"]]
        merged["requirement_ids"].append(s["requirement_id"])
        merged["table_ids"] += [t for t in tables if t not in merged["table_ids"]]
        groups.append(merged)
    order = {s["requirement_id"]: k for k, s in enumerate(selections)}
    for g in groups:
        g["requirement_ids"].sort(key=order.get)
    return sorted(groups, key=lambda g: order[g["requirement_ids"][0]])
