"""Source-bound A1 requirements; conservative extraction, never table-count inference."""

from __future__ import annotations

import re

from .a_conditions import FREQUENCY, query_constraints
from .a_semantics import explicit_conditions
from .contracts import StageFailure
from .mappings import UNCERTAIN, mentions

VERSION = "claview-a1-question/1"
SEPARATOR = re.compile(r"[;\n]+|그리고|또한")
DATE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})(?!\d)\s*년?(?:\s*([1-4])\s*분기|\s*(1[0-2]|[1-9])\s*월)?"
)
RELATIVE = re.compile(r"(?:최근|지난)\s*\d+\s*(?:년|개월|분기)|올해|작년|현재|최신")
EXCLUDE = re.compile(
    r"(?P<target>[\w가-힣]+?)(?:을|를|은|는)?\s*(?P<operator>제외|빼고|뺀|말고|아닌|아니라)"
)
SERIES = {
    "원계열": "original",
    "계절조정": "seasonally_adjusted",
    "말잔": "end_balance",
    "평잔": "average_balance",
    "전년동기비": "year_on_year",
    "전년비": "year_on_year",
    "전기비": "previous_period",
}


def prepare_question(original_query, *, effective_query=None, answers=()):
    if (
        not isinstance(original_query, str)
        or not original_query.strip()
        or len(original_query) > 10000
    ):
        raise StageFailure("invalid_query")
    sources = [{"source_id": "original", "kind": "original_question", "text": original_query}]
    for index, answer in enumerate(answers, 1):
        text = answer.get("user_input")
        if not isinstance(text, str) or not text.strip():
            raise StageFailure("invalid_a1_answer_source")
        sources.append({"source_id": f"answer:{index}", "kind": "user_answer", "text": text})
    source_query = original_query + "".join(f"\n사용자 기준 답변: {s['text']}" for s in sources[1:])
    if effective_query is not None and effective_query != source_query:
        raise StageFailure("unverified_a1_search_expression")
    scopes, requirements = [], []

    def add(scope, kind, value, start, end, *, status="explicit", polarity="positive", reason=None):
        text = next(s["text"] for s in sources if s["source_id"] == scope["source_id"])
        requirements.append(
            {
                "requirement_id": f"r{len(requirements) + 1}",
                "kind": kind,
                "value": value,
                "scope_ids": [scope["scope_id"]],
                "status": status,
                "polarity": polarity,
                "reason": reason,
                "target": {
                    "kind": "request_scope",
                    "binding": "unresolved" if kind != "request" else "explicit",
                },
                "source_refs": [
                    {
                        "source_id": scope["source_id"],
                        "start": start,
                        "end": end,
                        "quote": text[start:end],
                    }
                ],
            }
        )

    for source in sources:
        text = source["text"]
        bounds = [0, *[m.end() for m in SEPARATOR.finditer(text)]]
        ends = [*[m.start() for m in SEPARATOR.finditer(text)], len(text)]
        for left, right in zip(bounds, ends):
            fragment = text[left:right]
            start = left + len(fragment) - len(fragment.lstrip())
            end = right - len(fragment) + len(fragment.rstrip())
            if start >= end:
                continue
            fragment = text[start:end]
            scope = {
                "scope_id": f"s{len(scopes) + 1}",
                "source_id": source["source_id"],
                "start": start,
                "end": end,
                "text": fragment,
                "binding": "surface_clause_only",
            }
            scopes.append(scope)
            add(scope, "request", fragment, start, end)
            constraints = query_constraints(fragment)
            frequency_rows = list(FREQUENCY.finditer(fragment))
            for match, row in zip(frequency_rows, constraints["frequency_mentions"]):
                add(
                    scope,
                    "frequency",
                    row["code"],
                    start + match.start(),
                    start + match.end(),
                    status="unresolved"
                    if constraints["frequency_ambiguous"] or UNCERTAIN.search(fragment)
                    else "explicit",
                    polarity="negative" if row["negated"] else "positive",
                    reason="native_or_aggregation_scope_unresolved"
                    if constraints["frequency_ambiguous"]
                    else None,
                )
            dates = list(DATE.finditer(fragment))
            for match in dates:
                year, quarter, month = match.groups()
                remainder = fragment[match.end() :]
                invalid_precision = bool(re.match(r"\s*\d+\s*(?:월|분기)", remainder))
                add(
                    scope,
                    "period_point",
                    {
                        "year": int(year),
                        "quarter": int(quarter) if quarter else None,
                        "month": int(month) if month else None,
                    },
                    start + match.start(),
                    start + match.end(),
                    status="unresolved" if invalid_precision else "explicit",
                    reason="unsupported_period_precision" if invalid_precision else None,
                )
            for first, second in zip(dates, dates[1:]):
                connector = fragment[first.end() : second.start()]
                if re.fullmatch(r"\s*(?:~|〜|–|-|부터)\s*", connector):

                    def point(match):
                        return {
                            "year": int(match[1]),
                            "quarter": int(match[2]) if match[2] else None,
                            "month": int(match[3]) if match[3] else None,
                        }

                    begin, finish = point(first), point(second)
                    mixed_precision = bool(begin["quarter"] is not None) != bool(
                        finish["quarter"] is not None
                    ) or bool(begin["month"] is not None) != bool(finish["month"] is not None)
                    backwards = (begin["year"], begin["quarter"] or begin["month"] or 0) > (
                        finish["year"],
                        finish["quarter"] or finish["month"] or 0,
                    )
                    add(
                        scope,
                        "period_range",
                        {"start": begin, "end": finish},
                        start + first.start(),
                        start + second.end(),
                        status="unresolved" if backwards or mixed_precision else "explicit",
                        reason="mixed_period_precision"
                        if mixed_precision
                        else "reversed_period"
                        if backwards
                        else None,
                    )
            for match in re.finditer(r"(?:19|20)\d{2}\s*년?\s*(?:이후|부터)", fragment):
                add(
                    scope,
                    "open_period",
                    {"start_year": int(match.group()[:4]), "end": None},
                    start + match.start(),
                    start + match.end(),
                    status="unresolved",
                    reason="end_requires_b_period_policy",
                )
            for match in RELATIVE.finditer(fragment):
                add(
                    scope,
                    "relative_period",
                    match.group(),
                    start + match.start(),
                    start + match.end(),
                    status="unresolved",
                    reason="requires_b_period_policy",
                )
            for condition in explicit_conditions(fragment):
                # Keep every occurrence: different local requirements must not be merged.
                for span in condition["source_spans"]:
                    polarity = (
                        "unknown"
                        if condition["polarity"] == "unknown"
                        else "negative"
                        if span["negated"]
                        else "positive"
                    )
                    add(
                        scope,
                        condition["axis"],
                        condition["choice"],
                        start + span["start"],
                        start + span["end"],
                        status="unresolved" if polarity == "unknown" else "explicit",
                        polarity=polarity,
                    )
            for term, value in SERIES.items():
                for span in mentions(fragment, term):
                    add(
                        scope,
                        "series_basis",
                        value,
                        start + span["start"],
                        start + span["end"],
                        status="unresolved" if UNCERTAIN.search(fragment) else "explicit",
                        polarity="negative" if span["negated"] else "positive",
                    )
            for match in EXCLUDE.finditer(fragment):
                uncertain = bool(UNCERTAIN.search(fragment))
                add(
                    scope,
                    "exclusion",
                    match["target"],
                    start + match.start(),
                    start + match.end(),
                    status="unresolved" if uncertain else "explicit",
                    polarity="unknown" if uncertain else "negative",
                    reason="surface_exclusion_only; target_binding_unresolved",
                )
            for match in re.finditer(r"[\w가-힣]+(?:[·,]\s*[\w가-힣]+)+", fragment):
                add(
                    scope,
                    "surface_list",
                    {"components": re.split(r"[·,]\s*", match.group()), "binding": "unresolved"},
                    start + match.start(),
                    start + match.end(),
                    status="unresolved",
                    reason="literal_components; indicator_series_or_table_relation_unresolved",
                )
            for match in re.finditer(r"비교|각각|함께|동시에|또는|아니면|둘\s*중", fragment):
                add(
                    scope,
                    "composition",
                    match.group(),
                    start + match.start(),
                    start + match.end(),
                    status="unresolved"
                    if re.search(r"또는|아니면|둘", match.group())
                    else "explicit",
                    reason="does_not_determine_table_count_or_indicator_binding",
                )
    return {
        "schema_version": VERSION,
        "original_query": original_query,
        "effective_query": source_query,
        "sources": sources,
        "scopes": scopes,
        "requirements": requirements,
        "semantic_completeness": "unverified",
        "table_count": None,
    }


def auxiliary_queries(question, limit=2):
    """Only literal source clauses; no inferred canonical meaning or hard filtering."""
    original = question["original_query"]
    result = []
    effective = question["effective_query"]
    if effective != original and len(question["sources"]) > 1:
        result.append(
            {
                "expression": effective,
                "kind": "user_answer",
                "source_ids": [s["source_id"] for s in question["sources"]],
            }
        )
    original_scopes = [s for s in question["scopes"] if s["source_id"] == "original"]
    if len(original_scopes) > 1:
        for scope in original_scopes:
            if (
                len(scope["text"]) >= 4
                and scope["text"] != original
                and not UNCERTAIN.search(scope["text"])
            ):
                result.append(
                    {
                        "expression": scope["text"],
                        "kind": "literal_clause",
                        "source_ids": ["original"],
                        "scope_ids": [scope["scope_id"]],
                    }
                )
    seen = {original}
    unique = []
    for row in result:
        if row["expression"] not in seen:
            seen.add(row["expression"])
            unique.append(row)
    return unique[:limit]
