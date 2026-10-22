"""Resume A2 locally; use the existing repository revision and clarification lifecycle."""

import copy

from .a1_query import prepare_question
from .contracts import StageFailure


def clarify(discovery, set_id, answer, context, page_size, ttl_seconds, *, retry=False):
    if discovery.table_policy is None:
        raise StageFailure("a2_selection_disabled", "unresolved")
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
        raise StageFailure("invalid_clarification_answer", "unresolved")
    if answer == "__retry__" and not retry:
        raise StageFailure("invalid_clarification_answer", "unresolved")
    previous = discovery.repository.clarified_result(set_id, answer, context, discovery.snapshots)
    if previous:
        return discovery.decision_outcome(previous, page_size)
    bundle = discovery.repository.get(
        set_id, context, None if retry else discovery.snapshots, active=True
    )
    if bundle["status"] != "awaiting_user":
        raise StageFailure("a_already_confirmed", "unresolved")
    decision = bundle.get("selection_decision", {})
    pending = decision.get("clarification") or {}
    options = {item["value"]: item for item in pending.get("options", [])}
    option = options.get(answer) or next(
        (o for o in options.values() if o["label"] == answer), None
    )
    answers = copy.deepcopy(bundle.get("a_answers", []))
    if not retry:
        answers.append(
            {
                "user_input": option["label"] if option else answer,
                "submitted_answer": answer,
                "axis": pending["axis"] if option else None,
                "choice": option["value"] if option else None,
                "parent_set_id": set_id,
                "source_revision": context["query_revision"],
            }
        )
    question = prepare_question(bundle["original_query"], answers=answers)
    # A known criterion only narrows already verified alternatives. Free text changes search.
    rerun_a1 = (not retry and option is None) or (
        retry and decision.get("reason") == "search_insufficient"
    )
    if rerun_a1:
        rows, diagnostics = discovery.retrieve(
            question["effective_query"], original_query=bundle["original_query"], answers=answers
        )
    else:
        rows = [
            {"doc_id": c["doc_id"], "score": 0, "evidence": c["retrieval_signals"]}
            for c in bundle["candidate_pool"]
        ]
        diagnostics = copy.deepcopy(bundle["diagnostics"])
        diagnostics.pop("a2", None)
        diagnostics.pop("selection_decision", None)
        diagnostics["a1"] = question
    diagnostics["a2_resume"] = {
        "parent_set_id": set_id,
        "a1_rerun": rerun_a1,
        "reason": "search_requirements_changed" if rerun_a1 else "same_candidates_reassessed",
        "original_a1_trace": bundle.get("a1_state") if not rerun_a1 else None,
    }
    candidates, diagnostics = discovery.assess_rows(question["effective_query"], rows, diagnostics)
    if not rerun_a1:
        # Keep the original presentation and retrieval provenance; only A2 judgment changes.
        summaries = {c["doc_id"]: c.get("a2_summary") for c in candidates}
        candidates = copy.deepcopy(bundle["candidate_pool"])
        for candidate in candidates:
            if summaries[candidate["doc_id"]] is not None:
                candidate["a2_summary"] = summaries[candidate["doc_id"]]
    revised = {**context, "query_revision": context["query_revision"] + 1}
    child = discovery.save_candidates(
        bundle["original_query"],
        revised,
        candidates,
        diagnostics,
        ttl_seconds,
        answers=answers,
        clarification=(set_id, answer, context),
    )
    return discovery.decision_outcome(child, page_size)
