"""Input and output contract of the shared period resolver.

Inputs:
- conditions: [{id, kind, quote?, value?, attrs}] of the question interpretation. Only `period`,
  `frequency` and `calc` conditions are read; their structure must hold (`check_conditions`),
  otherwise the resolver answers `invalid_input` (an input contract error, never a guess).
- condition_fields: {condition_id: {source, confirmed, applied, period_diagnosis?}} (A's
  `analysis.decomposition.condition_fields`, the scope's conditions only). `applied` from A is
  never read: the resolver fills it.
- inferred: None (the field is absent: an older A) or a list of
  {kind: "frequency", value, from_condition_id, rule} (an empty list is kept as given).
- context: {bundle_id, anchor_source: "bundle_common" | "table_latest", anchor: "YYYY-MM" | None,
  simultaneous?: bool}. Absent = the table's own latest period.
- assignment: [{"start": "YYYY-MM", "end": "YYYY-MM"}] the table's share of the request (edition
  stitching): exactly one continuous segment per table (one query range per table); two or more
  are an input contract error, never filled or cut to the first. Absent = the whole request.
- A period marked `attrs.invalid` (an invalid unit, as the interpretation diagnoses `malformed`)
  is an input contract error whatever its condition slot says. `attrs.extract_status` is the old
  extractor's status and only has to be text: the resolver tries every well-formed period itself.
- table_meta: {"periods": {frequency: {"start", "end"}}} in the table format ("2024", "202403").

Output: see `RESULT_KEYS`; `check_result` returns the problems of one result.
"""

from __future__ import annotations

import re
from typing import Any

# .2: `attrs.invalid` is malformed, `extract_status` no longer decides support, one assigned
# segment per table
CONTRACT_VERSION = "period-contract/2026-10-21.2"

STATUSES = ("ok", "unresolved", "table_unsuitable", "no_data_in_range", "invalid_input")
SOURCES = ("question", "catalog", "default", "user")
HINT_STATES = ("absent", "empty", "given")
ANCHOR_SOURCES = ("bundle_common", "table_latest")
FREQUENCIES = ("Y", "H", "Q", "M")
PERIOD_FORMS = ("range", "since", "recent", "point", "alias")
PERIOD_DIAGNOSES = ("ok", "unsupported", "malformed")
RECENT_UNITS = ("year", "half", "quarter", "month")
READ_KINDS = ("period", "frequency", "calc")

RESULT_KEYS = (
    "status",
    "issue",
    "frequency",
    "applied",
    "source",
    "periods",
    "coverage",
    "anchor",
    "assumptions",
    "slots",
    "condition_fields",
    "relative",
    "show_period",
    "notice",
    "notices",
    "alternatives",
    "versions",
)

_MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
_PERIOD_TEXT = {
    "Y": re.compile(r"\d{4}"),
    "H": re.compile(r"\d{4}0[12]"),
    "Q": re.compile(r"\d{4}0[1-4]"),
    "M": re.compile(r"\d{4}(0[1-9]|1[0-2])"),
}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_month(value: Any) -> bool:
    return isinstance(value, str) and bool(_MONTH.fullmatch(value))


def is_period(value: Any, frequency: str) -> bool:
    return (
        isinstance(value, str)
        and frequency in _PERIOD_TEXT
        and bool(_PERIOD_TEXT[frequency].fullmatch(value))
    )


def _check_expr(expr: Any, path: str) -> list[str]:
    if expr is None:
        return []
    if not isinstance(expr, dict):
        return [f"{path}: not an object"]
    out = []
    for key in ("year", "alias", "sub_unit", "sub_value", "text"):
        if key in expr and expr[key] is not None and not isinstance(expr[key], str):
            out.append(f"{path}.{key}: not text")
    if "end_mark" in expr and not isinstance(expr["end_mark"], bool):
        out.append(f"{path}.end_mark: not a boolean")
    year = expr.get("year")
    if isinstance(year, str) and year and not re.fullmatch(r"\d{4}", year):
        out.append(f"{path}.year: not a 4-digit year")
    return out


def _check_period_attrs(attrs: dict, path: str) -> list[str]:
    out = []
    if attrs.get("form") not in PERIOD_FORMS:
        out.append(f"{path}.form: unknown")
    if not isinstance(attrs.get("extract_status"), str):
        out.append(f"{path}.extract_status: missing")
    for key in ("relative", "invalid", "open_end"):
        if key in attrs and not isinstance(attrs[key], bool):
            out.append(f"{path}.{key}: not a boolean")
    if attrs.get("invalid") is True:
        out.append(f"{path}.invalid: marked invalid by the interpretation (malformed)")
    amount = attrs.get("amount")
    if amount is not None and (not isinstance(amount, int) or isinstance(amount, bool)):
        out.append(f"{path}.amount: not an integer")
    unit = attrs.get("unit")
    if unit is not None and unit not in RECENT_UNITS:
        out.append(f"{path}.unit: unknown")
    out += _check_expr(attrs.get("start_expr"), f"{path}.start_expr")
    out += _check_expr(attrs.get("end_expr"), f"{path}.end_expr")
    form = attrs.get("form")
    if form in ("range", "since", "point", "alias") and not isinstance(
        attrs.get("start_expr"), dict
    ):
        out.append(f"{path}.start_expr: required for {form}")
    if form == "range" and not isinstance(attrs.get("end_expr"), dict):
        out.append(f"{path}.end_expr: required for range")
    return out


def check_conditions(conditions: Any, condition_fields: Any = None) -> list[str]:
    """Structure problems of the conditions the resolver reads (empty when they hold).

    A condition of another kind is not inspected beyond being an object with an id and a kind.
    `condition_fields` entries must belong to a given condition, and a `malformed` diagnosis is a
    problem of its condition.
    """
    if not isinstance(conditions, list):
        return ["conditions: not a list"]
    out: list[str] = []
    ids = []
    for i, condition in enumerate(conditions):
        path = f"conditions[{i}]"
        if not isinstance(condition, dict):
            out.append(f"{path}: not an object")
            continue
        if not _text(condition.get("id")) or not _text(condition.get("kind")):
            out.append(f"{path}: id or kind missing")
            continue
        ids.append(condition["id"])
        kind = condition["kind"]
        if kind not in READ_KINDS:
            continue
        attrs = condition.get("attrs")
        if not isinstance(attrs, dict):
            out.append(f"{path}.attrs: not an object")
            continue
        if kind == "period":
            out += _check_period_attrs(attrs, f"{path}.attrs")
        elif kind == "frequency":
            value = attrs.get("value") or condition.get("value")
            if value not in FREQUENCIES:
                out.append(f"{path}: frequency value unknown")
    if len(ids) != len(set(ids)):
        out.append("conditions: duplicate id")
    if condition_fields is None:
        return out
    if not isinstance(condition_fields, dict):
        return out + ["condition_fields: not an object"]
    for cid, fields in condition_fields.items():
        if cid not in ids:
            out.append(f"condition_fields[{cid}]: no such condition")
        elif not isinstance(fields, dict):
            out.append(f"condition_fields[{cid}]: not an object")
        elif fields.get("period_diagnosis") == "malformed":
            out.append(f"condition_fields[{cid}]: diagnosed malformed by the interpretation")
        elif fields.get("period_diagnosis") not in (None, *PERIOD_DIAGNOSES):
            out.append(f"condition_fields[{cid}].period_diagnosis: unknown")
    return out


def check_context(context: Any) -> list[str]:
    if context is None:
        return []
    if not isinstance(context, dict):
        return ["context: not an object"]
    out = []
    if not _text(context.get("bundle_id")):
        out.append("context.bundle_id: missing")
    source = context.get("anchor_source")
    if source not in ANCHOR_SOURCES:
        out.append("context.anchor_source: unknown")
    anchor = context.get("anchor")
    if source == "bundle_common" and not is_month(anchor):
        out.append("context.anchor: a bundle_common anchor must be YYYY-MM")
    if source == "table_latest" and anchor is not None:
        out.append("context.anchor: must be None for table_latest")
    if "simultaneous" in context and not isinstance(context["simultaneous"], bool):
        out.append("context.simultaneous: not a boolean")
    return out


def check_assignment(assignment: Any) -> list[str]:
    if assignment is None:
        return []
    if not isinstance(assignment, list) or not assignment:
        return ["assignment: not a non-empty list"]
    out = []
    if len(assignment) > 1:
        out.append(f"assignment: one continuous segment per table expected, got {len(assignment)}")
    for i, segment in enumerate(assignment):
        if (
            not isinstance(segment, dict)
            or not is_month(segment.get("start"))
            or not is_month(segment.get("end"))
        ):
            out.append(f"assignment[{i}]: needs start and end as YYYY-MM")
        elif segment["start"] > segment["end"]:
            out.append(f"assignment[{i}]: start after end")
    return out


def check_table_meta(table_meta: Any) -> list[str]:
    if not isinstance(table_meta, dict) or not isinstance(table_meta.get("periods"), dict):
        return ["table_meta.periods: missing"]
    out = []
    for freq, bounds in table_meta["periods"].items():
        if freq not in FREQUENCIES:
            out.append(f"table_meta.periods.{freq}: unknown frequency")
        elif (
            not isinstance(bounds, dict)
            or not is_period(bounds.get("start"), freq)
            or not is_period(bounds.get("end"), freq)
        ):
            out.append(f"table_meta.periods.{freq}: start/end not in the {freq} format")
        elif bounds["start"] > bounds["end"]:
            out.append(f"table_meta.periods.{freq}: start after end")
    return out


def _check_span_list(value: Any, freq: str | None, path: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{path}: not a list"]
    for span in value:
        if (
            not isinstance(span, dict)
            or not freq
            or not is_period(span.get("start"), freq)
            or not (is_period(span.get("end"), freq))
        ):
            return [f"{path}: spans must be {{start, end}} in the {freq} format"]
    return []


def check_result(result: Any) -> list[str]:
    """Problems of one resolver result (empty when it holds the contract). Never raises."""
    if not isinstance(result, dict) or set(result) != set(RESULT_KEYS):
        return ["result: keys differ from the contract"]
    out = []
    status = result["status"] if isinstance(result["status"], str) else None
    if status not in STATUSES:
        return ["result.status: unknown"]
    if status == "ok":
        freq = result["frequency"]
        applied = result["applied"]
        if freq not in FREQUENCIES:
            out.append("result.frequency: unknown")
        elif (
            not isinstance(applied, dict)
            or applied.get("frequency") != freq
            or not is_period(applied.get("start"), freq)
            or not is_period(applied.get("end"), freq)
            or applied["start"] > applied["end"]
        ):
            out.append("result.applied: not a period of the chosen frequency")
        if result["source"] not in SOURCES:
            out.append("result.source: unknown")
        if result["issue"] is not None:
            out.append("result.issue: must be None when ok")
        periods = result["periods"]
        if not isinstance(periods, dict):
            out.append("result.periods: not an object")
        else:
            for key in ("requested", "covered", "missing"):
                out += _check_span_list(
                    periods.get(key), freq if freq in FREQUENCIES else None, f"result.periods.{key}"
                )
    else:
        issue = result["issue"]
        if not isinstance(issue, dict) or not _text(issue.get("code")):
            out.append("result.issue: code missing")
        if result["applied"] is not None:
            out.append("result.applied: must be None unless ok")
    if not isinstance(result["assumptions"], list):
        out.append("result.assumptions: not a list")
    if not isinstance(result["notices"], list):
        out.append("result.notices: not a list")
    if not isinstance(result["condition_fields"], dict):
        out.append("result.condition_fields: not an object")
    else:
        for cid, fields in result["condition_fields"].items():
            if not isinstance(fields, dict) or fields.get("applied") not in (True, False, None):
                out.append(f"result.condition_fields[{cid}].applied: must be true, false or None")
    versions = result["versions"]
    if not isinstance(versions, dict) or versions.get("contract") != CONTRACT_VERSION:
        out.append("result.versions.contract: missing or another version")
    return out
