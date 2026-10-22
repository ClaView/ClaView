"""Conservative, evidence-bearing A checks; absence is never semantic conflict."""

from __future__ import annotations

import re

from .contracts import Json, StageFailure

FREQUENCY = re.compile(r"월별|월간|분기별|분기간|분기\s*자료|연도별|연간")
NEGATIVE = re.compile(
    r"^(?:\s|은|는|이|가|을|를|자료|데이터|로|이)*?(?:제외|빼고|말고|아닌|아니라|필요\s*없)"
)


def query_constraints(query: str) -> Json:
    mentions = []
    for match in FREQUENCY.finditer(query):
        word = match.group()
        code = "M" if word.startswith("월") else "Q" if word.startswith("분기") else "Y"
        tail = query[match.end() :]
        negated = bool(NEGATIVE.match(tail))
        mentions.append({"code": code, "source_quote": word, "negated": negated})
    positive = {x["code"] for x in mentions if not x["negated"]}
    negative = {x["code"] for x in mentions if x["negated"]}
    ambiguous = bool(positive & negative) or len(positive) > 1
    # Never treat an aggregation request as a native-frequency restriction.
    ambiguous |= bool(re.search(r"집계|변환|평균|환산", query))
    ambiguous |= bool(re.search(r"제외하지|않|말아|아니지|제외할\s*필요", query))
    return {
        "frequency": next(iter(positive)) if len(positive) == 1 and not ambiguous else None,
        "frequency_mentions": mentions,
        "frequency_ambiguous": ambiguous,
        "years": sorted({int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", query)}),
        "relative_period": bool(re.search(r"최근|이후|현재|올해|작년|최신|지난", query)),
    }


def assess_candidate(query: str, doc_id: str, search) -> Json:
    constraints = query_constraints(query)
    checks = []

    def add(field, state, reason, evidence=None):
        checks.append({"field": field, "state": state, "reason": reason, "evidence": evidence})

    doc = search.documents[doc_id]
    try:
        meta = search.store.get(doc["org_id"], doc["table_id"])
    except StageFailure as exc:
        add("metadata", "unknown", "a_metadata_incomplete", exc.code)
        meta = None
    else:
        add("metadata", "match", "metadata_loaded", search.store.snapshot)
    freq = constraints["frequency"]
    periods = meta.get("periods", {}) if meta else {}
    if freq and periods:
        add(
            "frequency",
            "match" if freq in periods else "conflict",
            "native_frequency" if freq in periods else "explicit_frequency_mismatch",
            {"requested": freq, "available": sorted(periods)},
        )
    else:
        add("frequency", "unknown", "not_explicit_or_ambiguous" if not freq else "metadata_missing")
    years = constraints["years"]
    bounds = [periods[freq]] if freq in periods else list(periods.values())
    if years and bounds:
        covered = any(
            int(b["start"][:4]) <= min(years) and max(years) <= int(b["end"][:4]) for b in bounds
        )
        # A cached end date cannot prove that the current source lacks data.
        add(
            "period",
            "match" if covered else "unknown",
            "explicit_years_in_cache" if covered else "metadata_refresh_required",
            bounds,
        )
    else:
        add("period", "unknown", "period_not_explicit_or_metadata_missing")
    if constraints["relative_period"]:
        add("period_policy", "unknown", "relative_period_requires_b_policy")
    add("meaning", "unknown", "requires_model_and_semantic_evaluation")
    return {
        "doc_id": doc_id,
        "checks": checks,
        "excluded": any(c["state"] == "conflict" for c in checks),
    }


def validate_selected(query: str, doc_ids: list[str], search) -> list[Json]:
    assessments = [assess_candidate(query, key, search) for key in doc_ids]
    for row in assessments:
        for check in row["checks"]:
            if check["reason"] in {
                "a_metadata_incomplete",
                "explicit_frequency_mismatch",
                "metadata_refresh_required",
            }:
                raise StageFailure(check["reason"], "unresolved")
    return assessments
