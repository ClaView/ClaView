"""Periods and frequencies the model structured (contracts .8 onward): the values it gave move into the resolver's
condition shape; nothing of the quoted words is read again (``read_quotes`` stays for answers of older contracts).

A period row means ``range`` with ``form`` ``absolute`` (``start`` and ``end`` calendar values ``YYYY``, ``YYYY-MM``,
``YYYY-Qn``, ``YYYY-Hn``; ``end`` ``same`` for one point, ``open`` for an open end) or ``relative`` (``count`` and
``unit`` counted back from the catalog reference, or from an explicit ``end`` in .10), or .9 ``calendar_offset``
(a signed offset in one calendar unit, computed here from as_of). An incomplete user range may retain one explicit
endpoint; other meanings
carry no values (``none``). ``read``
``failed``: the model could not give the expression's meaning. A frequency row gives ``value`` (``Y``/``H``/``Q``/``M``,
``not_stated`` or ``unreadable``) and ``anchor`` (``end``/``none``). Every statistic has one period row or more and
one frequency row or more (``not_stated`` when the user said none): a missing row is the model's omission
(``period_row_missing`` / ``frequency_row_missing``), never read as 'not said'. Whether ``not_stated`` is right is the
evaluation's to judge, not the code's.

``read_rows`` returns the shape of ``read_quotes`` ({conditions, readings, problems}). Problems are the model's own
failures to structure a condition it quoted or to give a row (``period_structure_invalid``, ``frequency_unreadable``,
``period_meaning_conflict``, ``frequency_conflict``, ``period_row_missing``, ``frequency_row_missing``: one recovery may
correct them) and unsupported meanings, exclusions or several requested frequencies. A user who gave no period or
left a boundary unspecified is not an internal failure: the caller asks and preserves any explicit endpoint.
"""

from __future__ import annotations

import re
from datetime import date

from .. import contract as K

_YEAR = re.compile(r"(\d{4})")
_POINT = re.compile(r"(\d{4})(?:-(0[1-9]|1[0-2])|-Q([1-4])|-H([12]))?")
UNITS = {"Y": "year", "H": "half", "Q": "quarter", "M": "month"}
_SUB = {"M": "M", "Q": "Q", "H": "H"}


def _expr(value: str) -> dict | None:
    """The resolver's expression of a calendar value, or None when it is not one."""
    m = _POINT.fullmatch(value or "")
    if m is None:
        return None
    year, month, quarter, half = m.groups()
    unit, sub = (("M", str(int(month))) if month else ("Q", quarter) if quarter else ("H", half) if half
                 else (None, None))
    return {"text": value, "year": year, "alias": None, "sub_unit": unit, "sub_value": sub, "end_mark": False}


def _order(expr: dict) -> tuple[int, int]:
    """(first month, last month) of an expression, months counted from year 0."""
    year = int(expr["year"]) * 12
    unit, sub = expr["sub_unit"], expr["sub_value"]
    if unit == "M":
        return year + int(sub) - 1, year + int(sub) - 1
    if unit == "Q":
        return year + 3 * int(sub) - 3, year + 3 * int(sub) - 1
    if unit == "H":
        return (year, year + 5) if sub == "1" else (year + 6, year + 11)
    return year, year + 11


def _calendar_offset(row: dict, as_of: str | None) -> tuple[dict | None, str | None]:
    """Resolve a signed calendar offset; no date or year is guessed by the model.

    Without as_of this only validates the row shape (used by condition preservation).
    A runtime reading always supplies as_of before the returned condition is resolved.
    """
    count, unit = row.get("count"), row.get("unit")
    if not isinstance(count, str) or re.fullmatch(r"-?(0|[1-9][0-9]*)", count) is None:
        return None, "offset_invalid"
    if unit not in UNITS:
        return None, "unit_invalid"
    if any(row.get(k) not in (None, "none") for k in ("start", "end")):
        return None, "relative_with_calendar_values"
    offset = int(count)
    if abs(offset) > 9999:
        return None, "offset_out_of_range"
    base = {"start_expr": None, "end_expr": None, "open_end": offset == 0, "amount": None, "unit": None,
            "relative": True, "invalid": False, "extract_status": "structured"}
    if as_of is None:
        return {"form": "calendar_offset", **base, "calendar_offset": offset, "calendar_unit": unit}, None
    try:
        day = date.fromisoformat(as_of)
    except (TypeError, ValueError):
        return None, "as_of_invalid"
    width = {"Y": 12, "H": 6, "Q": 3, "M": 1}[unit]
    anchor = day.year * 12 + day.month - 1
    first = (anchor // width + offset) * width
    last = first + width - 1
    if offset == 0:
        last = min(last, anchor)
    if first < 12 or last >= 10000 * 12:
        return None, "offset_out_of_range"
    def text(month):
        return f"{month // 12:04d}-{month % 12 + 1:02d}"
    return {"form": "range", **base, "start_expr": _expr(text(first)), "end_expr": _expr(text(last))}, None


def _period(row: dict, as_of: str | None = None,
            answer_contract: str = K.CONTRACT_VERSION) -> tuple[dict | None, str | None]:
    """(the resolver's attrs, None) of a range row, or (None, why) when its values do not make one."""
    form = row.get("form")
    if form == "calendar_offset":
        return _calendar_offset(row, as_of)
    if form == "relative":
        count, unit = row.get("count"), row.get("unit")
        if not (isinstance(count, str) and count.isascii() and count.isdigit() and int(count) > 0):
            return None, "count_invalid"
        if unit not in UNITS:
            return None, "unit_invalid"
        if row.get("start") not in (None, "none"):
            return None, "relative_with_calendar_values"
        end = None
        if row.get("end") not in (None, "none"):
            if answer_contract not in K.BOUNDED_RECENT_CONTRACTS:
                return None, "relative_with_calendar_values"
            end = _expr(row.get("end"))
            if end is None:
                return None, "relative_end_invalid"
        return {"form": "recent", "start_expr": None, "end_expr": end, "open_end": False, "amount": int(count),
                "unit": UNITS[unit], "relative": True, "invalid": False, "extract_status": "structured"}, None
    if form != "absolute":
        return None, "form_invalid"
    if any(row.get(k) not in (None, "none") for k in ("count", "unit")):
        return None, "absolute_with_count"
    start = _expr(row.get("start"))
    if start is None:
        return None, "start_invalid"
    end_value = row.get("end")
    base = {"start_expr": start, "end_expr": None, "open_end": False, "amount": None, "unit": None,
            "relative": False, "invalid": False, "extract_status": "structured"}
    if end_value == "same":
        return {"form": "point", **base}, None
    if end_value == "open":
        return {"form": "since", **base, "open_end": True}, None
    end = _expr(end_value)
    if end is None:
        return None, "end_invalid"
    if _order(end)[1] < _order(start)[0]:
        return None, "end_before_start"
    if end == {**start, "text": end["text"]} and end["text"] == start["text"]:
        return {"form": "point", **base}, None
    return {"form": "range", **base, "end_expr": end}, None


def _unused(row: dict, keys=("form", "start", "end", "count", "unit")) -> bool:
    return all(row.get(key) in (None, "none") for key in keys)


def _row_error(row: dict) -> str | None:
    """Validate fields even when the meaning does not produce a resolvable range."""
    meaning = row.get("meaning")
    if meaning not in {"range", "latest_available", "same_as", "user_undecided", "excluded", "not_stated",
                       "unsupported"}:
        return "meaning_invalid"
    if meaning == "same_as":
        if not row.get("refers_to"):
            return "reference_missing"
    elif row.get("refers_to"):
        return "reference_on_other_meaning"
    if row.get("read") not in {"ok", "failed"}:
        return "read_invalid"
    if row.get("read") == "failed":
        return "read_failed" if _unused(row) else "read_failed_with_values"
    if meaning == "range":
        return None
    if meaning == "user_undecided" and row.get("form") == "absolute":
        if not _unused(row, ("count", "unit")):
            return "partial_with_count"
        bounds = [row.get("start"), row.get("end")]
        if sum(value not in (None, "none") for value in bounds) != 1:
            return "partial_boundary_invalid"
        if any(value not in (None, "none") and _expr(value) is None for value in bounds):
            return "partial_boundary_invalid"
        return None
    if not _unused(row):
        return "inactive_values"
    return None


def _partial(row: dict) -> dict:
    bounds = {}
    for key, index in (("start", 0), ("end", 1)):
        expr = _expr(row.get(key))
        month = _order(expr)[index] if expr else None
        bounds[key] = f"{month // 12:04d}-{month % 12 + 1:02d}" if month is not None else None
    return {"quote": row["quote"], **bounds}


def read_rows(period_rows: list[dict], frequency_rows: list[dict], as_of: str | None = None,
              answer_contract: str = K.CONTRACT_VERSION) -> dict:
    """{conditions, readings, problems} of the structured rows of one statistic (``read_quotes``'s shape). Only rows
    meaning ``range`` give period conditions; the caller handles the other meanings (latest, same period, undecided)
    as before. Every row keeps its quote and the structured values it was read from."""
    conditions: list[dict] = []
    readings: list[dict] = []
    problems: list[dict] = []
    partial_periods: list[dict] = []
    if not period_rows:
        problems.append({"code": "period_row_missing", "quote": None})
    if not frequency_rows:
        problems.append({"code": "frequency_row_missing", "quote": None})
    meanings = {r.get("meaning") for r in period_rows}
    if ("range" in meanings and "latest_available" in meanings) or ("not_stated" in meanings and len(meanings) > 1):
        problems.append({"code": "period_meaning_conflict", "quote": " / ".join(r["quote"] for r in period_rows),
                         "meanings": sorted(m for m in meanings if m)})
    for row in period_rows:
        values = {k: row.get(k) for k in ("meaning", "form", "start", "end", "count", "unit", "read")}
        reading = {"kind": "period", "quote": row["quote"], "base_year_removed": [], "read_as": row["quote"],
                   "conditions": [], "uncovered": [], "plain_standard": [], "rewrites": [], "structured": values}
        readings.append(reading)
        why = _row_error(row)
        if why is not None:
            problems.append({"code": "period_structure_invalid", "quote": row["quote"], "why": why,
                             "structured": values})
            continue
        if row.get("meaning") == "excluded":
            problems.append({"code": "period_exclusion_unsupported", "quote": row["quote"], "structured": values,
                             "a_support": "unsupported", "b_contract_support": "not_checked"})
            continue
        if row.get("meaning") == "unsupported":
            problems.append({"code": "period_expression_unsupported", "quote": row["quote"], "structured": values})
            continue
        if row.get("meaning") == "user_undecided" and row.get("form") == "absolute":
            partial_periods.append(_partial(row))
        if row.get("meaning") != "range":
            continue
        attrs, why = _period(row, as_of, answer_contract)
        if attrs is None:
            problems.append({"code": "period_structure_invalid", "quote": row["quote"], "why": why,
                             "structured": values})
            continue
        cid = f"P{len(conditions) + 1}"
        conditions.append({"id": cid, "kind": "period", "quote": row["quote"], "span": None, "value": None,
                           "attrs": attrs, "status": "candidate"})
        reading["conditions"].append(cid)
    values = {r.get("value") for r in frequency_rows if r.get("value") in UNITS}
    if len(values) > 1 or (values and any(r.get("value") == "not_stated" for r in frequency_rows)):
        code = "frequency_multiple_requested" if len(values) > 1 else "frequency_conflict"
        problems.append({"code": code, "quote": " / ".join(r["quote"] for r in frequency_rows),
                         "values": sorted({str(r.get("value")) for r in frequency_rows})})
    for row in frequency_rows:
        reading = {"kind": "frequency", "quote": row["quote"], "base_year_removed": [], "read_as": row["quote"],
                   "conditions": [], "uncovered": [],
                   "structured": {"value": row.get("value"), "anchor": row.get("anchor")}}
        readings.append(reading)
        if row.get("anchor") not in ("none", "end") or (row.get("value") == "not_stated"
                                                         and row.get("anchor") != "none"):
            problems.append({"code": "frequency_unreadable", "quote": row["quote"], "why": "anchor_invalid",
                             "structured": reading["structured"]})
            continue
        if row.get("value") == "not_stated":
            continue
        if row.get("value") not in UNITS:
            problems.append({"code": "frequency_unreadable", "quote": row["quote"],
                             "structured": reading["structured"]})
            continue
        if any(p["code"] in ("frequency_conflict", "frequency_multiple_requested") for p in problems):
            continue
        cid = f"F{len(conditions) + 1}"
        anchor = "end" if row.get("anchor") == "end" else None
        conditions.append({"id": cid, "kind": "frequency", "quote": row["quote"], "span": None,
                           "value": row["value"], "attrs": {"value": row["value"], "anchor": anchor},
                           "status": "candidate"})
        reading["conditions"].append(cid)
    return {"conditions": conditions, "readings": readings, "problems": problems, "partial_periods": partial_periods}


def structured_key(row: dict, kind: str) -> tuple:
    """The values a row states (what a re-judgment must keep when the row was valid)."""
    if kind == "frequencies":
        return ("frequency", row.get("value"), row.get("anchor"))
    return ("period", row.get("meaning"), row.get("form"), row.get("start"), row.get("end"), row.get("count"),
            row.get("unit"), tuple(sorted(row.get("refers_to") or [])))


def failed_rows(period_rows: list[dict], frequency_rows: list[dict],
                answer_contract: str = K.CONTRACT_VERSION) -> set[str]:
    """The quotes of failed rows. Recovery must preserve their scope when correcting them."""
    got = read_rows(period_rows, frequency_rows, answer_contract=answer_contract)
    failed = {p["quote"] for p in got["problems"]
              if p["code"] in ("period_structure_invalid", "frequency_unreadable")}
    return failed
