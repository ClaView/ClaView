"""Shared period and frequency resolver of ClaView (B owns it; A and B call the same function).

`resolve_period_for_table` turns the question interpretation's period, frequency and calc
conditions into the frequency and the period one table is queried with. It is a pure function of
its arguments and the packaged policy file (read once and kept): no network, no model, no
environment variables, inputs never changed.

Rules (user decisions of 2026-10-20; team decisions of 10/01, 10/02, 10/06 kept):
- Frequency: a frequency the question states is judged on its own (absent from the table ->
  `table_unsuitable`). Otherwise a frequency read from the period's unit (the interpretation's
  inference hints, or B reading the period conditions again) is an assumption only: the finest unit
  the table supports (`period_unit`, or `period_unit_finest` for mixed units); when the table
  supports none, it is never a reason to drop the table (single frequency, else the longest one with
  the others as alternatives, and a notice). Without any of these: the table's only frequency, else
  the longest one (assumed).
- Period: counted against the anchor (the bundle's common latest period when given, else the
  table's latest period), never against `as_of`. No period in the question -> the policy's recent
  window (assumed). Parts outside the table's coverage are kept as `missing`; nothing overlapping at
  all -> `no_data_in_range`. A well-formed period this resolver cannot read, or one the
  interpretation diagnoses `unsupported` -> `period_unsupported` (never replaced by the default
  window); the old `extract_status` alone decides nothing. A structurally broken condition, one
  marked `invalid` or diagnosed `malformed` -> `invalid_input` / `malformed_condition`.
- Edition stitching: with an `assignment` (one continuous segment) the table only answers its
  share of the unchanged request; more segments -> `invalid_input` / `invalid_assignment`.
"""

from __future__ import annotations

import math
from typing import Any

from ._version import __version__
from .contract import (
    CONTRACT_VERSION,
    check_assignment,
    check_conditions,
    check_context,
    check_table_meta,
)
from .policy import Policy, load_policy

Json = dict[str, Any]

RECENT_UNITS = {"year": "Y", "half": "H", "quarter": "Q", "month": "M"}
YEAR_ALIASES = {"올해": 0, "금년": 0, "올": 0, "작년": -1, "지난해": -1, "재작년": -2}
SUB_ALIASES = {"이번": 0, "지난": -1, "저번": -1}
HALF_WORDS = {"상": 1, "하": 2}
# calc condition values that need earlier periods before a closed stated start
YEAR_OVER_YEAR = {"전년동기비"}
PERIOD_OVER_PERIOD = {"전기비"}
# "가장 크게 변했는지": an extreme (calc) over a change (calc hint) compares each period with the
# one before, so it needs one earlier period too.
EXTREMES = {"최댓값", "최솟값"}

_POLICY: Policy | None = None


def default_policy() -> Policy:
    """The packaged policy, read and validated once per process."""
    global _POLICY
    if _POLICY is None:
        _POLICY = load_policy()
    return _POLICY


class _Stop(Exception):
    def __init__(self, status: str, code: str, detail: str, periods: Json | None = None):
        super().__init__(code)
        self.status, self.code, self.detail, self.periods = status, code, detail, periods


def _unresolved(code: str, detail: str) -> _Stop:
    return _Stop("unresolved", code, detail)


# ---------------------------------------------------------------- period arithmetic


class _Calendar:
    """Period <-> index arithmetic of one policy (periods per year)."""

    def __init__(self, policy: Policy):
        self.per_year = policy.periods_per_year

    def index(self, text: str, freq: str) -> int:
        year = int(text[:4])
        return year if freq == "Y" else year * self.per_year[freq] + int(text[4:]) - 1

    def text(self, index: int, freq: str) -> str:
        if freq == "Y":
            return str(index)
        year, sub = divmod(index, self.per_year[freq])
        return f"{year:04d}{sub + 1:02d}"

    def month_index(self, year: int, month: int, freq: str) -> int:
        """Index (in `freq`) of the period holding this month."""
        if freq == "Y":
            return year
        return year * self.per_year[freq] + (month - 1) // (12 // self.per_year[freq])

    def months(self, index: int, freq: str) -> tuple[int, int]:
        """First and last running month (year * 12 + month - 1) of a period."""
        if freq == "Y":
            return index * 12, index * 12 + 11
        year, sub = divmod(index, self.per_year[freq])
        width = 12 // self.per_year[freq]
        first = year * 12 + sub * width
        return first, first + width - 1

    def from_month(self, month: int, freq: str) -> int:
        year, rest = divmod(month, 12)
        return self.month_index(year, rest + 1, freq)


def _parse_month(text: str) -> int:
    return int(text[:4]) * 12 + int(text[5:7]) - 1


def _months_of(expr: Json) -> tuple[int, int]:
    """First and last month of the year a sub unit covers (the whole year without one)."""
    unit, value = expr.get("sub_unit"), expr.get("sub_value")
    if unit is None:
        return 1, 12
    if unit == "H":
        half = HALF_WORDS.get(value) or (int(value) if value in ("1", "2") else None)
        if half is None:
            raise _unresolved("period_unsupported", "half_value")
        return (1, 6) if half == 1 else (7, 12)
    if not (isinstance(value, str) and value.isascii() and value.isdigit()):
        raise _unresolved("period_unsupported", "sub_value")  # "²" is a digit int() refuses
    number = int(value)
    if unit == "Q" and 1 <= number <= 4:
        return 3 * number - 2, 3 * number
    if unit == "M" and 1 <= number <= 12:
        return number, number
    raise _unresolved("period_unsupported", "sub_value")


class _Reader:
    """Turns period conditions into spans of one frequency against one anchor."""

    def __init__(self, cal: _Calendar, freq: str, anchor: int, strict: bool):
        self.cal, self.freq, self.anchor, self.strict = cal, freq, anchor, strict

    def expr_span(self, expr: Json, *, year: int | None = None) -> tuple[int, int]:
        cal, freq = self.cal, self.freq
        alias = expr.get("alias")
        if year is None and expr.get("year"):
            year = int(expr["year"])
        if year is None and alias in YEAR_ALIASES:
            year = int(cal.text(self.anchor, freq)[:4]) + YEAR_ALIASES[alias]
        if year is None and alias in SUB_ALIASES:
            sub_unit = expr.get("sub_unit")
            if sub_unit not in cal.per_year or sub_unit == "Y":
                raise _unresolved("period_unsupported", "relative_sub_unit")
            if sub_unit == freq:
                index = self.anchor + SUB_ALIASES[alias]
                return index, index
            if self.strict:
                # a stated frequency other than the unit the question counts in
                raise _unresolved("conditions_incomplete", "relative_sub_unit_frequency")
            # the whole sub-unit period ("지난 분기" on a monthly table = that quarter's 3 months)
            sub = cal.from_month(cal.months(self.anchor, freq)[1], sub_unit) + SUB_ALIASES[alias]
            first_month, last_month = cal.months(sub, sub_unit)
            if expr.get("end_mark"):  # "지난 분기 말": the sub period's last period only
                first_month = last_month
            return cal.from_month(first_month, freq), cal.from_month(last_month, freq)
        if year is None:
            raise _unresolved("period_unsupported", "period_expression")
        first_month, last_month = _months_of(expr)
        if expr.get("end_mark"):
            first_month = last_month
        return cal.month_index(year, first_month, freq), cal.month_index(year, last_month, freq)

    def recent_count(self, amount: int, unit: str) -> tuple[int, bool]:
        """Periods of `freq` a "최근 N <unit>" covers, and whether it was rounded up."""
        per_year = self.cal.per_year
        unit_freq = RECENT_UNITS.get(unit)
        if not isinstance(amount, int) or amount < 1 or unit_freq is None:
            raise _unresolved("period_unsupported", "recent_amount_or_unit")
        if unit_freq == "Y":
            return amount * per_year[self.freq], False
        if per_year[self.freq] % per_year[unit_freq] == 0:  # the same or a finer frequency
            return amount * per_year[self.freq] // per_year[unit_freq], False
        if self.strict:
            raise _unresolved("conditions_incomplete", "recent_unit_finer_than_stated_frequency")
        return math.ceil(amount * per_year[self.freq] / per_year[unit_freq]), True

    def span(self, condition: Json) -> Json:
        # `invalid` never reaches here (an input contract error); `extract_status` is the old
        # extractor's status: every well-formed period is read, and what cannot be read is
        # period_unsupported below
        attrs = condition["attrs"]
        base = {"id": condition["id"], "rounded": False}
        form = attrs["form"]
        relative = bool(attrs.get("relative"))
        if form == "recent":
            count, rounded = self.recent_count(attrs.get("amount"), attrs.get("unit"))
            return {
                **base,
                "first": self.anchor - (count - 1),
                "last": self.anchor,
                "relative": True,
                "open": True,
                "rounded": rounded,
            }
        first, last = self.expr_span(attrs["start_expr"])
        if form == "since":
            return {**base, "first": first, "last": self.anchor, "relative": relative, "open": True}
        if form == "range":
            end = attrs["end_expr"]
            year = None
            if not end.get("year") and not end.get("alias"):
                year = int(self.cal.text(first, self.freq)[:4])  # "2026년 1월부터 6월까지"
            last = self.expr_span(end, year=year)[1]
            if first > last:
                raise _unresolved("period_unsupported", "start_after_end")
            return {**base, "first": first, "last": last, "relative": relative, "open": False}
        return {**base, "first": first, "last": last, "relative": relative, "open": False}


# ---------------------------------------------------------------- frequency


def _of_kind(conditions: list[Json], kind: str) -> list[Json]:
    return [c for c in conditions if c.get("kind") == kind]


def _period_units(periods: list[Json], per_year: dict) -> list[str]:
    """Frequencies the units of the stated periods imply.

    "최근 3개월" -> M, "2024년 3분기" -> Q.
    """
    units = []
    for condition in periods:
        attrs = condition["attrs"]
        unit = RECENT_UNITS.get(attrs.get("unit"))
        if attrs.get("form") == "recent" and unit not in (None, "Y"):
            units.append(unit)
        for key in ("start_expr", "end_expr"):
            expr = attrs.get(key)
            if (
                isinstance(expr, dict)
                and expr.get("sub_unit") in per_year
                and expr["sub_unit"] != "Y"
            ):
                units.append(expr["sub_unit"])
    return list(dict.fromkeys(units))


def _decide_frequency(
    conditions: list[Json], available: list[str], inferred: list | None, policy: Policy
) -> Json:
    per_year = policy.periods_per_year
    stated = []
    for condition in _of_kind(conditions, "frequency"):
        stated.append(condition["attrs"].get("value") or condition.get("value"))
    stated = list(dict.fromkeys(stated))
    hint_state = "absent" if inferred is None else ("empty" if not inferred else "given")
    if len(stated) > 1:
        raise _unresolved("conditions_incomplete", "several_frequencies")
    if stated:
        if stated[0] not in available:
            raise _Stop("table_unsuitable", "table_unsuitable", "frequency_not_in_table")
        return {
            "value": stated[0],
            "source": "question",
            "rule": "question_word",
            "confirmed": True,
            "hint_state": hint_state,
            "wanted": stated,
            "strict": True,
        }
    if hint_state == "given":
        units = [
            h.get("value")
            for h in inferred
            if isinstance(h, dict)
            and h.get("kind") == "frequency"
            and isinstance(h.get("value"), str)
        ]
        units = [u for u in dict.fromkeys(units) if u in per_year]
    else:  # absent or empty: the period conditions are read again (an assumption only)
        units = _period_units(_of_kind(conditions, "period"), per_year)
    if units:
        supported = [u for u in units if u in available]
        if supported:
            chosen = max(supported, key=lambda f: per_year[f])
            rule = "period_unit_finest" if len(units) > 1 else "period_unit"
            return {
                "value": chosen,
                "source": "question",
                "rule": rule,
                "confirmed": False,
                "hint_state": hint_state,
                "wanted": units,
                "strict": False,
            }
    fallback = {"hint_state": hint_state, "wanted": units, "strict": False, "unavailable": units}
    if len(available) == 1:
        return {
            **fallback,
            "value": available[0],
            "source": "catalog",
            "rule": "table_single",
            "confirmed": True,
        }
    return {
        **fallback,
        "value": available[0],
        "source": "default",
        "rule": "longest_frequency",
        "confirmed": False,
    }


# ---------------------------------------------------------------- period


def _extension(conditions: list[Json], freq: str, calc_hints: list, per_year: dict):
    values = {
        c.get("value") for c in _of_kind(conditions, "calc") if isinstance(c.get("value"), str)
    }
    if values & YEAR_OVER_YEAR:
        return per_year[freq], "year_over_year_base"
    if values & PERIOD_OVER_PERIOD:
        return 1, "period_over_period_base"
    changes = any(h.get("kind") == "change" for h in calc_hints)
    if values & EXTREMES and changes:
        return 1, "largest_change_base"
    return 0, None


def _cut(piece: tuple[int, int], low: int, high: int) -> tuple[tuple | None, list[tuple]]:
    """The part of `piece` inside [low, high] and the parts outside."""
    first, last = piece
    inside = (max(first, low), min(last, high))
    if inside[0] > inside[1]:
        return None, [piece]
    outside = []
    if first < low:
        outside.append((first, min(last, low - 1)))
    if last > high:
        outside.append((max(first, high + 1), last))
    return inside, outside


def _merge(pieces: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for first, last in sorted(pieces):
        if out and first <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], last)
        else:
            out.append([first, last])
    return [(a, b) for a, b in out]


def _resolve_period(
    cal: _Calendar,
    conditions: list[Json],
    bounds: Json,
    freq: str,
    anchor: int,
    as_of: str | None,
    calc_hints: list,
    assignment: list | None,
    strict: bool,
    policy: Policy,
) -> Json:
    start, end = cal.index(bounds["start"], freq), cal.index(bounds["end"], freq)
    periods = _of_kind(conditions, "period")
    reader = _Reader(cal, freq, anchor, strict)
    if periods:
        spans = [reader.span(c) for c in periods]
        source, rule = "question", "question_period"
    else:
        count = policy.default_window(freq)
        spans = [
            {
                "id": None,
                "first": anchor - (count - 1),
                "last": anchor,
                "relative": False,
                "open": True,
                "rounded": False,
            }
        ]
        source, rule = "default", f"recent_years:{freq}={policy.default_recent_years[freq]}"
    soft_any = any(s["relative"] or s["open"] for s in spans)
    requested = _merge([(s["first"], s["last"]) for s in spans])
    calc_base = None
    extension = 0
    if source == "question" and not soft_any:
        # Only a closed, stated period starts earlier for a comparison (CV-AB-11 keeps 2015년 이후).
        extension, extension_rule = _extension(conditions, freq, calc_hints, cal.per_year)
        if extension:
            first = min(s["first"] for s in spans)
            calc_base = (first - extension, first - 1)
        if extension_rule:
            rule += "+" + extension_rule
    if any(s["rounded"] for s in spans):
        rule += "+recent_rounded_up"
    duty_limits = None
    if assignment is not None:
        duty_limits = [
            (
                cal.from_month(_parse_month(a["start"]), freq),
                cal.from_month(_parse_month(a["end"]), freq),
            )
            for a in assignment
        ]
    covered: list[tuple[int, int]] = []
    missing: list[tuple[int, int]] = []
    used_ids: set = set()
    cut_start = cut_end = False
    for s in spans:
        soft = s["relative"] or s["open"] or source == "default"
        high = anchor if soft else end
        pieces = [(s["first"], s["last"])]
        if duty_limits is not None:
            pieces = [
                p for p in (_cut(piece, a, b)[0] for piece in pieces for a, b in duty_limits) if p
            ]
        for piece in pieces:
            inside, outside = _cut(piece, start, high)
            missing += outside
            cut_start |= any(o[0] < start for o in outside)
            cut_end |= any(o[1] > high for o in outside)
            if inside:
                covered.append(inside)
                used_ids.add(s["id"])
    covered, missing = _merge(covered), _merge(missing)

    def spans_text(pieces):
        return [{"start": cal.text(a, freq), "end": cal.text(b, freq)} for a, b in pieces]

    def months_text(pieces):
        return [
            {
                "start": _month_text(cal.months(a, freq)[0]),
                "end": _month_text(cal.months(b, freq)[1]),
            }
            for a, b in pieces
        ]

    if not covered:
        # nothing to query; the request is kept so the caller can report what stays unmet
        raise _Stop(
            "no_data_in_range",
            "no_data_in_range",
            "requested_outside_coverage",
            periods={
                "requested": spans_text(requested),
                "covered": [],
                "missing": spans_text(missing),
                "calc_base": None,
                "assigned": None if assignment is None else [dict(a) for a in assignment],
                "requested_months": months_text(requested),
                "covered_months": [],
            },
        )
    first, last = covered[0][0], covered[-1][1]
    base_out = None
    notices = []
    if calc_base is not None and duty_limits is not None:
        # a table answering a share compares its own first period with the one before it
        calc_base = (covered[0][0] - extension, covered[0][0] - 1)
    if calc_base is not None:
        inside, _outside = _cut(calc_base, start, end)
        if inside:
            first = min(first, inside[0])
            base_out = inside
        if inside != calc_base:
            notices.append({"code": "calc_base_partly_outside_coverage"})
    applied = {"frequency": freq, "start": cal.text(first, freq), "end": cal.text(last, freq)}
    relative = any(s["relative"] for s in spans)
    open_end = any(s["open"] for s in spans)
    lagging = open_end or source == "default" or cut_end
    anchor_text = cal.text(anchor, freq)
    as_of_text = (
        cal.text(cal.month_index(int(as_of[:4]), int(as_of[5:7]), freq), freq) if as_of else None
    )
    notice = None
    lag = lagging and as_of_text is not None and as_of_text != anchor_text
    if cut_start or lag:
        span = f"조회 기간: {applied['start']} ~ {applied['end']}"
        if lag:
            message = (
                f"요청하신 기준(as_of={as_of})으로는 {as_of_text}까지 필요하지만, 기준 시점은 "
                f"{anchor_text}입니다. 조회 가능한 기간으로 자료를 조회합니다.\n{span}"
            )
        else:
            message = (
                "요청하신 기간 중 일부가 표에 수록되어 있지 않아 조회 가능한 기간으로 조회합니다."
                f"\n{span}"
            )
        notice = {
            "status": "needs_confirmation",
            "code": "period_requires_confirmation",
            "requested_as_of_period": as_of_text,
            "resolved_period": dict(applied),
            "message": message,
        }
    if missing:
        notices.append(
            {
                "code": "missing_periods_kept",
                "missing": [
                    {"start": cal.text(a, freq), "end": cal.text(b, freq)} for a, b in missing
                ],
            }
        )

    return {
        "applied": applied,
        "source": source,
        "rule": rule,
        "relative": relative,
        "show_period": source == "question" and (relative or open_end),
        "notice": notice,
        "notices": notices,
        "periods": {
            "requested": spans_text(requested),
            "covered": spans_text(covered),
            "missing": spans_text(missing),
            "calc_base": spans_text([base_out])[0] if base_out else None,
            "assigned": None if assignment is None else [dict(a) for a in assignment],
            "requested_months": months_text(requested),
            "covered_months": months_text(covered),
        },
        "assumption": (
            {"dimension": "period", "value": dict(applied), "rule": rule}
            if source == "default" or any(s["rounded"] for s in spans)
            else None
        ),
        "used_period_ids": used_ids,
        "calc_rule_used": calc_base is not None,
    }


def _month_text(month: int) -> str:
    year, rest = divmod(month, 12)
    return f"{year:04d}-{rest + 1:02d}"


# ---------------------------------------------------------------- entry point


def _base(policy: Policy) -> Json:
    return {
        "status": None,
        "issue": None,
        "frequency": None,
        "applied": None,
        "source": None,
        "periods": None,
        "coverage": None,
        "anchor": None,
        "assumptions": [],
        "slots": {"frequency": None, "period": None},
        "condition_fields": {},
        "relative": False,
        "show_period": False,
        "notice": None,
        "notices": [],
        "alternatives": [],
        "versions": {
            "package": __version__,
            "contract": CONTRACT_VERSION,
            "policy": policy.version,
            "policy_sha256": policy.sha256,
        },
    }


def _fields_out(conditions: list[Json], fields_in: dict, applied: dict) -> dict:
    """Condition slots of the scope: A's source/confirmed/diagnosis kept, `applied` filled by B."""
    out = {}
    for condition in conditions:
        cid = condition.get("id")
        if not isinstance(cid, str):
            continue
        given = fields_in.get(cid) if isinstance(fields_in.get(cid), dict) else {}
        entry = {
            "source": given.get("source", "question"),
            "confirmed": given.get("confirmed"),
            "applied": applied.get(cid),
        }
        if "period_diagnosis" in given:
            entry["period_diagnosis"] = given["period_diagnosis"]
        out[cid] = entry
    return out


def resolve_period_for_table(
    conditions: list[Json],
    table_meta: Json,
    as_of: str | None,
    calc_hints: list | None = None,
    inferred: list | None = None,
    *,
    condition_fields: dict | None = None,
    context: Json | None = None,
    assignment: list | None = None,
    policy: Policy | None = None,
) -> Json:
    """The frequency and period one table is queried with.

    Returns a result of the contract (`claview_period.contract.RESULT_KEYS`) with status ok,
    unresolved, table_unsuitable, no_data_in_range or invalid_input. Never raises for its inputs.
    `inferred=None` means the hint field is absent; `[]` is kept as an empty hint list.
    """
    policy = policy or default_policy()
    result = _base(policy)
    fields_in = condition_fields if isinstance(condition_fields, dict) else {}
    assignment_problems = check_assignment(assignment)
    problems = (
        check_conditions(conditions, condition_fields)
        + check_context(context)
        + check_table_meta(table_meta)
    )
    if as_of is not None and not (
        isinstance(as_of, str)
        and len(as_of) >= 7
        and as_of[:4].isdigit()
        and as_of[4] == "-"
        and as_of[5:7].isdigit()
    ):
        problems.append("as_of: not YYYY-MM-DD")
    if inferred is not None and not isinstance(inferred, list):
        problems.append("inferred: not a list")
    if calc_hints is not None and not isinstance(calc_hints, list):
        problems.append("calc_hints: not a list")
    if problems or assignment_problems:
        # the assignment's own code only when it is the one problem source
        code = "malformed_condition" if problems else "invalid_assignment"
        problems = problems + assignment_problems
        return {
            **result,
            "status": "invalid_input",
            "issue": {"code": code, "detail": problems[0], "problems": problems},
        }
    conditions = [c for c in conditions if c.get("kind") in ("period", "frequency", "calc")] + [
        c for c in conditions if c.get("kind") not in ("period", "frequency", "calc")
    ]
    hints = [h for h in calc_hints or [] if isinstance(h, dict)]
    cal = _Calendar(policy)
    periods_meta = table_meta["periods"]
    available = [f for f in policy.frequency_order if f in periods_meta]
    result["condition_fields"] = _fields_out(conditions, fields_in, {})
    if not available:
        return {
            **result,
            "status": "table_unsuitable",
            "issue": {"code": "table_unsuitable", "detail": "table_without_periods"},
        }
    for condition in _of_kind(conditions, "period"):
        if (fields_in.get(condition["id"]) or {}).get("period_diagnosis") == "unsupported":
            return {
                **result,
                "status": "unresolved",
                "issue": {
                    "code": "period_unsupported",
                    "detail": "diagnosed_unsupported",
                    "condition_id": condition["id"],
                },
            }
    frequency = None
    try:
        frequency = _decide_frequency(conditions, available, inferred, policy)
        freq = frequency["value"]
        bounds = periods_meta[freq]
        anchor = _anchor(cal, context, bounds, freq)
        period = _resolve_period(
            cal,
            conditions,
            bounds,
            freq,
            anchor,
            as_of,
            hints,
            assignment,
            frequency["strict"],
            policy,
        )
    except _Stop as stop:
        result.update(status=stop.status, issue={"code": stop.code, "detail": stop.detail})
        if stop.periods is not None:
            result["periods"] = stop.periods
        if frequency is not None:
            result["frequency"] = frequency["value"]
            bounds = periods_meta[frequency["value"]]
            result["coverage"] = {
                "frequency": frequency["value"],
                "start": bounds["start"],
                "end": bounds["end"],
            }
        return result
    assumptions = []
    if not frequency["confirmed"] and frequency["source"] != "catalog":
        assumptions.append({"dimension": "frequency", "value": freq, "rule": frequency["rule"]})
    if period["assumption"]:
        assumptions.append(period["assumption"])
    notices = list(period["notices"])
    if frequency.get("unavailable"):
        notices.append(
            {
                "code": "inferred_frequency_unavailable",
                "wanted": frequency["unavailable"],
                "used": freq,
                "hint_state": frequency["hint_state"],
            }
        )
    alternatives = []
    if frequency["source"] == "default":
        for other in [f for f in available if f != freq]:
            try:
                other_bounds = periods_meta[other]
                alt = _resolve_period(
                    cal,
                    conditions,
                    other_bounds,
                    other,
                    _anchor(cal, context, other_bounds, other),
                    as_of,
                    hints,
                    assignment,
                    False,
                    policy,
                )
            except _Stop:
                continue  # that frequency cannot show this period: not offered
            alternatives.append({"frequency": other, "applied": alt["applied"]})
    applied_flags = {}
    for condition in conditions:
        kind, cid = condition.get("kind"), condition.get("id")
        if kind == "period":
            applied_flags[cid] = cid in period["used_period_ids"]
        elif kind == "frequency":
            applied_flags[cid] = frequency["rule"] == "question_word"
        elif kind == "calc" and period["calc_rule_used"]:
            value = condition.get("value")
            applied_flags[cid] = isinstance(value, str) and value in (
                YEAR_OVER_YEAR | PERIOD_OVER_PERIOD | EXTREMES
            )
    period_ids = [c["id"] for c in _of_kind(conditions, "period")]
    result.update(
        status="ok",
        frequency=freq,
        applied=period["applied"],
        source=period["source"],
        periods=period["periods"],
        coverage={"frequency": freq, "start": bounds["start"], "end": bounds["end"]},
        anchor={
            "period": cal.text(anchor, freq),
            "source": (context or {}).get("anchor_source", "table_latest"),
            "bundle_id": (context or {}).get("bundle_id"),
            "simultaneous": (context or {}).get("simultaneous"),
        },
        assumptions=assumptions,
        slots={
            "frequency": {
                "value": freq,
                "source": frequency["source"],
                "confirmed": frequency["confirmed"],
                "applied": freq,
                "rule": frequency["rule"],
                "alternatives": [a["frequency"] for a in alternatives],
                "hint_state": frequency["hint_state"],
            },
            "period": {
                "value": {"condition_ids": period_ids} if period_ids else None,
                "source": period["source"],
                "confirmed": period["source"] != "default",
                "applied": dict(period["applied"]),
                "rule": period["rule"],
            },
        },
        condition_fields=_fields_out(conditions, fields_in, applied_flags),
        relative=period["relative"],
        show_period=period["show_period"],
        notice=period["notice"],
        notices=notices,
        alternatives=alternatives,
    )
    return result


def _anchor(cal: _Calendar, context: Json | None, bounds: Json, freq: str) -> int:
    """The index the relative, open and assumed periods count back from."""
    end = cal.index(bounds["end"], freq)
    if not context or context.get("anchor_source") != "bundle_common":
        return end
    anchor = cal.from_month(_parse_month(context["anchor"]), freq)
    if anchor > end:
        raise _Stop(
            "invalid_input",
            "anchor_after_table_latest",
            "bundle anchor after the table's latest period",
        )
    return anchor
