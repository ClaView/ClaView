"""Selection-time period plans from the shared resolver, never from diagnostic probes.

No interpretation mutation, policy copy, query, or assignment-envelope gap filling.
The resolver's covered_months (not applied, which can include calculation history)
is the only coverage surface. Plans remain separate from B's actual applied values.
"""
from __future__ import annotations

import copy


from .. import period_core
from ..period import package_identity
from ..interpret.condition_fields import fields_of
from ..reused.q_rules import stamp_months
from .evidence import scope


def month(value: str) -> int:
    return int(value[:4]) * 12 + int(value[5:7]) - 1


def month_text(value: int) -> str:
    return f"{value // 12:04d}-{value % 12 + 1:02d}"


def ranges(result: dict, field: str) -> list[tuple[int, int]]:
    return [(month(p["start"]), month(p["end"])) for p in (result.get("periods") or {}).get(field) or []]


def table_meta(view) -> dict:
    # Catalog display stamps and KOSIS stamps describe the same calendar. Convert
    # representation only, using the shared policy's periods-per-year.
    periods = {}
    for f, start, end in view.periods:
        if f in periods:
            raise ValueError("several metadata intervals for one frequency are not representable")
        periods[f] = {"start": metadata_stamp(f, start), "end": metadata_stamp(f, end)}
    return {"periods": periods}


def metadata_months(frequency: str, stamp: str | None) -> tuple[int, int] | None:
    if not stamp:
        return None
    per_year = period_core.default_policy().periods_per_year[frequency]
    if stamp.isdigit() and len(stamp) == 6:
        sub = int(stamp[4:])
        if not 1 <= sub <= per_year:
            return None
        lo = int(stamp[:4]) * 12 + (sub - 1) * (12 // per_year)
        return lo, lo + 12 // per_year - 1
    return stamp_months(stamp)


def metadata_stamp(frequency: str, stamp: str | None) -> str | None:
    bounds = metadata_months(frequency, stamp)
    if bounds is None:
        return stamp  # let the shared contract report malformed metadata
    per_year = period_core.default_policy().periods_per_year[frequency]
    year, m = divmod(bounds[0], 12)
    return f"{year:04d}" if per_year == 1 else f"{year:04d}{m // (12 // per_year) + 1:02d}"


def period_input(analysis: dict, rid: str, as_of: str | None) -> dict:
    identity = package_identity()
    counted, _ = scope(analysis, rid)
    conditions = [copy.deepcopy(c) for c in analysis.get("conditions") or [] if c["id"] in counted]
    record = analysis.get("decomposition") or {}
    # Old rule output has no support diagnosis: derive it without changing that output.
    derived = fields_of(analysis, as_of) if "condition_fields" not in record else record
    fields = {c["id"]: copy.deepcopy(derived["condition_fields"][c["id"]]) for c in conditions
              if c["id"] in derived.get("condition_fields", {})}
    inferred = copy.deepcopy(record.get("inferred", {}).get(rid, [])) if "inferred" in record else None
    return {"conditions": conditions, "condition_fields": fields, "inferred": inferred,
            "as_of": as_of, "resolver_identity": {k: v for k, v in identity.items() if k != "files"}}


def resolve(inp: dict, view, *, context: dict | None = None, assignment: list | None = None) -> dict:
    result = period_core.resolve_period_for_table(
        inp["conditions"], table_meta(view), inp["as_of"], inferred=inp["inferred"],
        condition_fields=inp["condition_fields"], context=context, assignment=assignment)
    problems = period_core.check_result(result)
    if problems:
        raise ValueError("shared period result violates the pinned contract")
    return result


def anchored_request(inp: dict) -> bool:
    periods = [c for c in inp["conditions"] if c["kind"] == "period"]
    return not periods or any((c.get("attrs") or {}).get("relative") or
                              (c.get("attrs") or {}).get("open_end") or
                              (c.get("attrs") or {}).get("form") in ("recent", "since") for c in periods)


def common_anchor(views: list, frequency: str) -> str | None:
    bounds = []
    for view in views:
        found = [row for row in view.periods if row[0] == frequency]
        if len(found) != 1:
            return None
        _, start, end = found[0]
        a, b = metadata_months(frequency, start), metadata_months(frequency, end)
        if a is None or b is None:
            return None
        bounds.append((a[0], b[1]))
    if not bounds or max(a for a, _ in bounds) > min(b for _, b in bounds):
        return None
    return month_text(min(b for _, b in bounds))


def bundle_plan(inp: dict, views: dict, table_ids: list[str], bundle_id: str,
                assignments: dict[str, list] | None = None) -> dict:
    """Resolve every member in the same context; no common time is an execution gap.

    A closed explicit request may join disjoint editions. Their requested intervals
    remain intact; only each table's assigned share restricts its lookup.
    """
    base = {t: resolve(inp, views[t]) for t in table_ids}
    frequencies = {r.get("frequency") for r in base.values()} - {None}
    common = anchored_request(inp) and len(table_ids) > 1
    context = {"bundle_id": bundle_id, "anchor_source": "table_latest", "anchor": None,
               "simultaneous": common}
    gaps = []
    if common:
        anchor = common_anchor([views[t] for t in table_ids], next(iter(frequencies))) \
            if len(frequencies) == 1 and all(r.get("frequency") for r in base.values()) else None
        if anchor is None:
            gaps.append("no_common_anchor")
            context["simultaneous"] = False
        else:
            context.update(anchor_source="bundle_common", anchor=anchor)
    if len(frequencies) > 1:
        gaps.append("frequency_frame_mismatch")
    results = {t: resolve(inp, views[t], context=context, assignment=(assignments or {}).get(t))
               for t in table_ids}
    for result in results.values():
        if result["status"] not in ("ok", "no_data_in_range"):
            gaps.append((result.get("issue") or {}).get("code") or result["status"])
    return {"context": context, "results": results, "gaps": list(dict.fromkeys(gaps))}


def continuous_assignment(spans: list[tuple[int, int]]) -> list[dict]:
    """Coalesce adjacent spans only; never bridge a gap or silently take the first span."""
    merged = []
    for a, b in sorted(set(spans)):
        if a > b:
            raise ValueError("invalid assignment range")
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    result = [{"start": month_text(a), "end": month_text(b)} for a, b in merged]
    if period_core.check_assignment(result):
        raise ValueError("one continuous assignment per table required")
    return result
