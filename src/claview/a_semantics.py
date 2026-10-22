"""Conservative atomic basis evidence; no B code combination or semantic auto-approval."""

from __future__ import annotations

import re

from .document_corpus import item_paths
from .mappings import UNCERTAIN, mentions, text_key

VERSION = "claview-a-basis/2"
CONDITION_BONUS = 0.5
AXES = {
    "trade": {"label": "수입·수출", "choices": {"import": ["수입"], "export": ["수출"]}},
    "currency": {"label": "통화 기준", "choices": {"krw": ["원화"], "usd": ["달러", "미달러"]}},
    "price_basis": {"label": "명목·실질", "choices": {"nominal": ["명목"], "real": ["실질"]}},
    "loan_basis": {
        "label": "잔액·신규",
        "choices": {"stock": ["잔액"], "new": ["신규", "신규취급액"]},
    },
    "measure": {
        "label": "통계량 유형",
        "choices": {"growth": ["성장률", "증감률"], "deflator": ["디플레이터"]},
    },
}


def explicit_conditions(query):
    result = []
    for axis, definition in AXES.items():
        grouped = {}
        for choice, terms in definition["choices"].items():
            spans = [span for term in terms for span in mentions(query, term)]
            if spans:
                grouped[choice] = spans
        for choice, spans in grouped.items():
            negation = {s["negated"] for s in spans}
            state = (
                "unknown"
                if len(negation) > 1 or UNCERTAIN.search(query)
                else "negative"
                if True in negation
                else "positive"
            )
            result.append(
                {
                    "axis": axis,
                    "label": definition["label"],
                    "choice": choice,
                    "polarity": state,
                    "source_spans": spans,
                }
            )
    return result


def choices_in(text, axis, unit=None):
    value = text_key(text)
    choices = {
        key
        for key, terms in AXES[axis]["choices"].items()
        if any(text_key(term) in value for term in terms)
    }
    if axis == "currency" and unit:
        if re.fullmatch(r"(?:천|만|백만|천만|억|십억|조)?원", unit.strip()):
            choices.add("krw")
        if any(t in unit.casefold() for t in ("달러", "usd", "us dollar", "u.s. dollar")):
            choices.add("usd")
    return choices


class BasisEvidence:
    def __init__(self, store):
        self.store = store
        self.contexts = {}
        for key, cat in store.catalog.items():
            record = store.records.get(key, {})
            paths, errors = item_paths(record)
            contexts = [
                {
                    "source_field": "catalog.title",
                    "source_quote": cat["title"],
                    "unit": None,
                    "context": "table_title",
                }
            ]
            rows = record.get("meta", {}).get("ITM", [])
            for index, row in enumerate(rows):
                if not isinstance(row, dict) or not row.get("ITM_NM"):
                    continue
                path = paths.get(index)
                names = [p["name"] for p in path["path"] if p["name"]] if path else [row["ITM_NM"]]
                contexts.append(
                    {
                        "source_field": f"/meta/ITM/{index}/ITM_NM",
                        "source_quote": row["ITM_NM"],
                        "branch_text": " > ".join(names),
                        "unit": row.get("UNIT_NM"),
                        "branch_evidence": [
                            {"source_field": p["pointer"], "source_quote": p["name"]}
                            for p in path["path"]
                        ]
                        if path
                        else [],
                        "unit_evidence": {
                            "source_field": f"/meta/ITM/{index}/UNIT_NM",
                            "source_quote": row.get("UNIT_NM"),
                        },
                        "context": {
                            "axis": row.get("OBJ_ID"),
                            "code": row.get("ITM_ID"),
                            "path_status": "ambiguous_identity"
                            if any(e["reason"] == "duplicate_axis_code_conflict" for e in errors)
                            else path["path_status"]
                            if path
                            else "unknown",
                        },
                    }
                )
            self.contexts[key] = contexts

    def assess(self, query, key):
        requested = explicit_conditions(query)
        multiple_axes = {
            axis
            for axis in AXES
            if len(
                {
                    c["choice"]
                    for c in requested
                    if c["axis"] == axis and c["polarity"] == "positive"
                }
            )
            > 1
        }
        checks, contexts = [], self.contexts[key]
        title = self.store.catalog[key]["title"]
        for condition in requested:
            axis, choice, polarity = condition["axis"], condition["choice"], condition["polarity"]
            refs = [
                c
                for c in contexts
                if choice in choices_in(c.get("branch_text", c["source_quote"]), axis, c["unit"])
            ]
            title_choices = choices_in(title, axis)
            state, reason = "unknown", "basis_not_explicitly_supported"
            if polarity == "unknown":
                reason = "query_negation_or_alternative_unknown"
            elif polarity == "negative":
                if title_choices == {choice}:
                    state, reason = "conflict", "exclusive_official_title_excluded"
                else:
                    reason = "excluded_basis_not_proven_for_whole_table"
            elif refs:
                state, reason = "match", "literal_basis_available_not_joint_series_validation"
            elif axis in multiple_axes:
                reason = "multiple_bases_requires_separate_series_or_tables"
            elif (
                title_choices
                and choice not in title_choices
                and axis in {"currency", "price_basis", "loan_basis"}
            ):
                state, reason = "conflict", "exclusive_official_title_opposite_basis"
                refs = [contexts[0]]
            checks.append(
                {
                    **condition,
                    "state": state,
                    "reason": reason,
                    "official_evidence": refs[:3],
                    "omitted_evidence": max(0, len(refs) - 3),
                    "hard_conflict": state == "conflict",
                }
            )
        positives = [c for c in requested if c["polarity"] == "positive"]
        witnesses = []
        if positives and not any(c["polarity"] == "unknown" for c in requested):
            for context in contexts:
                if (
                    isinstance(context["context"], dict)
                    and context["context"]["path_status"] != "complete"
                ):
                    continue
                local = context.get("branch_text", context["source_quote"])
                # Table titles are common context; never join values from distinct siblings.
                text = title + " " + local
                if all(
                    choices_in(text, c["axis"], context["unit"]) == {c["choice"]} for c in positives
                ):
                    if not any(
                        c["polarity"] == "negative"
                        and c["choice"] in choices_in(text, c["axis"], context["unit"])
                        for c in requested
                    ):
                        witnesses.append(context)
        conflict = any(c["hard_conflict"] for c in checks)
        joint = "conflict" if conflict else "match" if witnesses else "unknown"
        scope = re.search(r"기관\s*(?:ID|아이디)\s*[:=]?\s*(\d+)", query, re.IGNORECASE)
        scope_state = (
            "out_of_scope"
            if scope and scope.group(1) not in {d["org_id"] for d in self.store.catalog.values()}
            else "unknown"
        )
        return {
            "version": VERSION,
            "condition_bonus": CONDITION_BONUS,
            "source_snapshot": getattr(self.store, "snapshot", None),
            "catalog_snapshot": getattr(self.store, "catalog_snapshot", None),
            "checks": checks,
            "joint_state": joint,
            "joint_evidence": witnesses[:2],
            "joint_scope": "one_official_branch_plus_table_title_not_final_B_series_combination",
            "support_scope": scope_state,
            "support_scope_evidence": scope.group() if scope else None,
            "meaning_verified": False,
            "series_selection": None,
        }


def decorate_candidates(query, candidates, basis, *, rank=False):
    for candidate in candidates:
        analysis = basis.assess(query, candidate["doc_id"])
        candidate["basis_analysis"] = analysis
        for check in analysis["checks"]:
            evidence = {
                "query_quote": " / ".join(s["quote"] for s in check["source_spans"]),
                "source_field": check["axis"],
                "state": check["state"],
                "reason": check["reason"],
                "value": check["official_evidence"],
                "displayable": bool(check["official_evidence"]),
            }
            candidate["evidence"].append(evidence)
            if check["hard_conflict"]:
                candidate["conflicts"].append(evidence)
            elif check["state"] == "unknown":
                candidate["unknowns"].append(evidence)
        signal = CONDITION_BONUS if rank and analysis["joint_state"] == "match" else 0
        candidate["rank_signals"]["joint_basis_bonus"] = signal
        candidate["rank_signals"]["score"] += signal
        reasons = []
        if candidate["rank_signals"]["title"]:
            reasons.append("공식 표명에 질문과 겹치는 표현이 있습니다.")
        if candidate["summary"]["related_fields"]:
            reasons.append("관련 항목·분류명에 질문과 겹치는 표현이 있습니다.")
        if any(e.get("channel") == "metadata" for e in candidate["retrieval_signals"]):
            reasons.append("전체 메타 검색에서 원문 근거를 찾았습니다.")
        candidate["selection_explanation"] = {
            "reasons": reasons,
            "unresolved_conditions": [
                c["label"] for c in analysis["checks"] if c["state"] == "unknown"
            ],
            "unresolved_evidence": [
                {k: e[k] for k in ("query_quote", "source_field", "reason")}
                for e in candidate["unknowns"]
            ],
            "joint_conditions": "한 공식 분류 경로에서 조건 언급을 찾았습니다."
            if analysis["joint_state"] == "match"
            else "여러 조건을 같은 계열이 함께 충족하는지는 미확인입니다.",
            "meaning": "원문 언급 근거이며 최종 의미·조회 조건은 미확인입니다.",
            "scope": analysis["support_scope"],
        }
    top = sorted(
        (c for c in candidates if not c["conflicts"]),
        key=lambda c: (-c["rank_signals"]["score"], c["doc_id"]),
    )[:5]
    for candidate in candidates:
        candidate["selection_explanation"]["differences"] = [
            {
                "doc_id": other["doc_id"],
                "official_table_name": other["table_name"],
                "conditions": [
                    {"label": c["label"], "state": c["state"]}
                    for c in other["basis_analysis"]["checks"]
                ],
            }
            for other in top
            if other["doc_id"] != candidate["doc_id"]
        ][:2]
