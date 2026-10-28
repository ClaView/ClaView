"""Selection-time period plans from the shared resolver, never from diagnostic probes.

No interpretation mutation, policy copy, query, or assignment-envelope gap filling.
The resolver's covered_months (not applied, which can include calculation history)
is the only coverage surface. Plans remain separate from B's actual applied values.
"""

from __future__ import annotations
from . import core as claview_period
from ..conditions.q_rules import stamp_months


def month(value: str) -> int:
    return int(value[:4]) * 12 + int(value[5:7]) - 1


def month_text(value: int) -> str:
    return f"{value // 12:04d}-{value % 12 + 1:02d}"


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
    per_year = claview_period.default_policy().periods_per_year[frequency]
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
    per_year = claview_period.default_policy().periods_per_year[frequency]
    year, m = divmod(bounds[0], 12)
    return f"{year:04d}" if per_year == 1 else f"{year:04d}{m // (12 // per_year) + 1:02d}"


def resolve(inp: dict, view, *, context: dict | None = None, assignment: list | None = None) -> dict:
    result = claview_period.resolve_period_for_table(
        inp["conditions"], table_meta(view), inp["as_of"], inferred=inp["inferred"],
        condition_fields=inp["condition_fields"], context=context, assignment=assignment)
    problems = claview_period.check_result(result)
    if problems:
        raise ValueError("shared period result violates the pinned contract")
    return result


def anchored_request(inp: dict) -> bool:
    periods = [c for c in inp["conditions"] if c["kind"] == "period"]
    return not periods or any((c.get("attrs") or {}).get("relative") or
                              (c.get("attrs") or {}).get("open_end") or
                              (c.get("attrs") or {}).get("form") in ("recent", "since") for c in periods)


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
    if claview_period.check_assignment(result):
        raise ValueError("one continuous assignment per table required")
    return result
