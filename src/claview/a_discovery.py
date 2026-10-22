"""Model-free retrieval, official evidence, presentation, and user confirmation."""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from .a1_graph import build_a1_graph
from .a1_query import prepare_question
from .a2_evidence import A2Evidence
from .a2_graph import build_a2_graph
from .a2_selection import TableSetPolicy, failure_decision
from .a_conditions import assess_candidate, query_constraints
from .a_retrieval import RetrievalOrchestrator, finalize_pool
from .a_semantics import BasisEvidence, decorate_candidates
from .candidate_store import CandidateStore, validate_context
from .contracts import AWAITING_USER, Config, StageFailure, Trace, fingerprint, outcome
from .search import SearchIndex, grams
from .search_documents import digest

RANK_VERSION = "official-lexical/2"
RANK_WEIGHTS = {
    "title": 2.0,
    "related_field": 3.0,
    "path": 1.0,
    "conditions": 0.1,
    "rrf": 1.0,
    "term_coverage": 2.0,
}


def field_grams(text):
    # Do not invent bigrams across field, path, punctuation, or word boundaries.
    return set().union(*(grams(token) for token in re.findall(r"[\w가-힣]+", text)))


def query_terms(query):
    ignored = {
        "연간",
        "연도별",
        "월별",
        "월간",
        "분기별",
        "분기",
        "최근",
        "이후",
        "현재",
        "추이",
        "흐름",
        "규모",
        "기준",
        "비교",
        "비교해줘",
        "보여줘",
        "만들어줘",
        "그려줘",
        "그래프",
        "그래프로",
        "막대그래프",
        "막대그래프로",
        "꺾은선",
        "표로",
        "정리해줘",
        "나눠",
        "어떻게",
        "얼마나",
        "달랐는지",
        "올랐는지",
    }
    terms = []
    for token in re.findall(r"[a-zA-Z가-힣]+", query):
        if token in ignored:
            continue
        stripped = re.sub(r"(?:으로|에서|부터|까지|의|과|와|을|를|은|는|이|가)$", "", token)
        term = stripped if len(stripped) >= 2 else token
        if len(term) >= 2 and term not in ignored:
            terms.append(term.casefold())
    return sorted(set(terms))


def lexical_score(query, text, idf):
    q, t = field_grams(query), field_grams(text)
    overlap = sum(idf.get(g, 1.0) for g in sorted(q & t))
    return overlap / math.sqrt(max(1, len(t)) * max(1, len(q)))


def evidence_for(query, search, row):
    """Connect official fields to literal query fragments, not inferred meaning."""
    key = row["doc_id"]
    doc = search.store.catalog[key]
    assessment = assess_candidate(query, key, search)
    constraints = query_constraints(query)
    evidence = []
    for check in assessment["checks"]:
        field = check["field"]
        quote = (
            " / ".join(m["source_quote"] for m in constraints["frequency_mentions"])
            if field == "frequency"
            else " / ".join(str(y) for y in constraints["years"])
            if field == "period"
            else query
        )
        evidence.append(
            {
                "query_quote": quote,
                "source_field": "PRD" if field in {"frequency", "period"} else field,
                "state": check["state"],
                "reason": "semantic_fit_unverified" if field == "meaning" else check["reason"],
                "value": check["evidence"],
                "displayable": field in {"frequency", "period"} and check["evidence"] is not None,
            }
        )
    fields = [("catalog.title", doc["title"], None), ("catalog.path", doc["path"], None)]
    try:
        meta = search.store.get(doc["org_id"], doc["table_id"])
    except StageFailure:
        meta = None
    coverage_records = search.store.records.get(key, {}).get("meta", {}).get("PRD", [])
    varying_coverage = len({r.get("PRD_SE") for r in coverage_records}) != len(coverage_records)
    if varying_coverage:
        for entry in evidence:
            if entry["reason"] in {"explicit_years_in_cache", "metadata_refresh_required"}:
                entry.update(
                    state="unknown", reason="coverage_varies_by_record", value=coverage_records
                )
    if meta:
        fields.extend(
            (f"ITM.ITEM.{code}", v["name"], v["unit"]) for code, v in meta["items"].items()
        )
        for axis, dimension in meta["dimensions"].items():
            fields.extend(
                (f"ITM.{axis}.{code}", " > ".join(p["name"] for p in v["path"]), v["unit"])
                for code, v in dimension["values"].items()
            )
    ranked_fields = []
    for source, label, unit in fields:
        score = lexical_score(query, label, search.idf)
        if score <= 0:
            continue
        ranked_fields.append({"source_field": source, "label": label, "unit": unit, "score": score})
    ranked_fields.sort(key=lambda v: (-v["score"], v["source_field"]))
    related = [f for f in ranked_fields if f["source_field"].startswith("ITM.")]
    terms = query_terms(query)
    official_text = "\n".join(label.casefold() for _, label, _ in fields)
    matched_terms = [term for term in terms if term in official_text]
    term_weights = {
        term: sum(search.idf.get(g, 1.0) for g in sorted(grams(term))) for term in terms
    }
    term_coverage = sum(term_weights[t] for t in matched_terms) / max(1, sum(term_weights.values()))
    for term in matched_terms:
        source, label, unit = next(f for f in fields if term in f[1].casefold())
        evidence.append(
            {
                "query_quote": re.search(re.escape(term), query, re.I).group(),
                "source_field": source,
                "state": "match",
                "reason": "literal_term_presence_only",
                "value": {"label": label, "unit": unit},
                "displayable": True,
            }
        )
    for f in [r for r in ranked_fields if r["source_field"].startswith("catalog.")] + related[:5]:
        evidence.append(
            {
                "query_quote": query,
                "source_field": f["source_field"],
                "state": "match",
                "reason": "lexical_overlap_only",
                "value": {
                    "label": f["label"],
                    "unit": f["unit"],
                    "matched_grams": sorted(field_grams(query) & field_grams(f["label"])),
                },
                "displayable": True,
            }
        )
    signals = {
        "title": lexical_score(query, doc["title"], search.idf),
        "related_field": related[0]["score"] if related else 0,
        "path": lexical_score(query, doc["path"], search.idf),
        "conditions": sum(e["state"] == "match" and e["source_field"] == "PRD" for e in evidence),
        "rrf": row.get("score", 0) * 61 / (2 if search.corpus else 1),
        "term_coverage": term_coverage,
    }
    signals["score"] = round(sum(RANK_WEIGHTS[k] * v for k, v in signals.items()), 10)
    if "original_score" in row:
        signals["original_display_score"] = round(
            signals["score"]
            - RANK_WEIGHTS["rrf"] * signals["rrf"]
            + RANK_WEIGHTS["rrf"] * row["original_score"] * 61 / (2 if search.corpus else 1),
            10,
        )
    return {
        "doc_id": key,
        "org_id": doc["org_id"],
        "table_id": doc["table_id"],
        "table_name": doc["title"],
        "paths": [{"depth": doc["depth"], "node_ids": doc["path_ids"], "names": doc["path_names"]}],
        "retrieval_signals": row.get("evidence", []),
        "evidence": evidence,
        "conflicts": [e for e in evidence if e["state"] == "conflict"],
        "unknowns": [e for e in evidence if e["state"] == "unknown"],
        "summary": {
            "related_fields": [{k: v for k, v in f.items() if k != "score"} for f in related[:5]],
            "omitted_related_fields": max(0, len(related) - 5),
            "cached_periods": meta["periods"] if meta and not varying_coverage else {},
            "coverage_records": coverage_records,
            "coverage_scope": "cached_table_metadata_not_series_observations",
            "unit_scope": "per_related_field_not_table_wide",
            "table_unit_catalog": meta["units"] if meta else [],
            "statistical_basis": {
                "source_field": "catalog.path",
                "official_text": doc["path"],
                "scope": "classification_context_not_inferred_definition",
            },
            "meaning_verified": False,
            "literal_query_terms": terms,
            "matched_query_terms": matched_terms,
        },
        "rank_signals": signals,
    }


def assess_candidates(query, rows, search):
    merged = {}
    rejected = []
    for row in rows:
        key = row.get("doc_id")
        if not isinstance(key, str) or key not in search.store.catalog:
            rejected.append({"doc_id": key, "reason": "non_catalog_candidate"})
            continue
        if key in merged:
            merged[key]["evidence"].extend(row.get("evidence", []))
        else:
            merged[key] = {**row, "evidence": list(row.get("evidence", []))}
    return [evidence_for(query, search, row) for row in merged.values()], rejected


def display_order(candidates):
    return [
        c["doc_id"]
        for c in sorted(
            (c for c in candidates if not c["conflicts"]),
            key=lambda c: (
                c["rank_signals"].get("display_group", 0),
                -c["rank_signals"]["score"],
                c["doc_id"],
            ),
        )
    ]


def page_view(bundle, page=1, page_size=5):
    if type(page) is not int or type(page_size) is not int or page < 1 or not 1 <= page_size <= 100:
        raise StageFailure("invalid_page")
    order = bundle["display_order"]
    start = (page - 1) * page_size
    by_id = {c["doc_id"]: c for c in bundle["candidate_pool"]}
    view = {
        "candidate_set_id": bundle["candidate_set_id"],
        "context": bundle["context"],
        "options": [
            {
                "number": i + 1,
                **{
                    k: v
                    for k, v in by_id[key].items()
                    if k not in {"rank_signals", "retrieval_signals"}
                },
            }
            for i, key in enumerate(order[start : start + page_size], start)
        ],
        "page": page,
        "page_size": page_size,
        "total": len(order),
        "next_page": page + 1 if start + page_size < len(order) else None,
        "expires_at": bundle["expires_at"],
    }
    if "agent_state" in bundle:
        agent = bundle["agent_state"]
        view.update(
            kind="clarification" if agent["clarification"] else "table_selection",
            clarification=agent["clarification"],
            model_usage=agent["model_usage"],
            unresolved_conditions=agent["unresolved_conditions"],
            original_query=bundle["original_query"],
            clarified_context=agent["answers"],
            termination_reason=agent["termination_reason"],
        )
    if "selection_decision" in bundle:
        decision = bundle["selection_decision"]
        view["selection_decision"] = decision
        if bundle.get("selection_mode") == "auto" and not bundle.get("agent_state", {}).get(
            "clarification"
        ):
            view.update(
                kind="clarification" if decision.get("clarification") else "table_selection",
                clarification=decision.get("clarification"),
                original_query=bundle["original_query"],
                clarified_context=bundle.get("a_answers", []),
            )
    return view


class TableDiscovery:
    def __init__(
        self,
        search: SearchIndex,
        repository: CandidateStore,
        config=None,
        channels=None,
        *,
        retrieval_policy="baseline",
        metadata_corpus=None,
        agent=None,
        a1_mode="rules",
        a1_dictionary=None,
        a2_mode="evidence",
        selection_mode="shadow",
        interpreter=None,
    ):
        self.search = search
        self.repository = repository
        self.config = config or Config()
        self.agent = agent if agent and agent.config.enabled else None
        if a1_mode not in {"off", "rules"} or (a1_mode == "off" and a1_dictionary):
            raise StageFailure("invalid_a1_configuration")
        self.a1_mode = a1_mode
        self.a1_dictionary = a1_dictionary or search.mapping_dictionary
        if interpreter is not None and (a1_mode != "rules" or self.agent is not None):
            raise StageFailure("invalid_interpreter_configuration")
        self.a1_graph = (
            build_a1_graph(self._retrieve_candidates, self.a1_dictionary, interpreter)
            if a1_mode == "rules"
            else None
        )
        if (
            self.config.structure_query
            or self.config.hierarchy
            or self.config.search_scope != "global"
            or self.config.candidate_policy != "union"
            or not self.config.filter_frequency
        ):
            raise StageFailure("unsupported_discovery_option")
        # Optional channel adapters expose stable version and retrieve(query) -> rows.
        self.channels = channels or {}
        self.retriever = RetrievalOrchestrator(search, retrieval_policy, metadata_corpus)
        self.basis = BasisEvidence(search.store) if self.retriever.semantic_checks else None
        if a2_mode not in {"off", "evidence"}:
            raise StageFailure("invalid_a2_configuration")
        self.a2 = (
            A2Evidence(search, basis=self.basis, corpus=metadata_corpus)
            if a2_mode == "evidence"
            else None
        )
        if selection_mode not in {"off", "shadow", "auto"} or (
            selection_mode == "auto" and (not self.a2 or self.agent)
        ):
            raise StageFailure("invalid_a_selection_configuration")
        self.selection_mode = selection_mode
        self.table_policy = TableSetPolicy(self.a2) if self.a2 and selection_mode != "off" else None
        self.a2_graph = build_a2_graph(self.a2, self.table_policy) if self.a2 else None
        self.snapshots = {
            "catalog": search.store.catalog_snapshot,
            "metadata": search.store.snapshot,
            "index": digest(
                {
                    "description": search.describe(),
                    "retrieval": self.retriever.describe(),
                    "vectors": getattr(search.vectors, "snapshot", None),
                    "fields": {
                        k: [(t, w) for t, _, w in rows] for k, rows in search.fields.items()
                    },
                    "channels": {k: c.version for k, c in self.channels.items()},
                }
            ),
            "configuration": digest(
                {
                    "config": asdict(self.config),
                    "rank_version": RANK_VERSION,
                    "weights": RANK_WEIGHTS,
                    "a1": {"mode": a1_mode, "dictionary": self.a1_dictionary.describe()},
                    "interpreter": interpreter.describe() if interpreter else {"mode": "off"},
                    "a2": self.a2.describe() if self.a2 else {"mode": "off"},
                    "selection_mode": selection_mode,
                    "implementation": {
                        name: fingerprint(Path(__file__).with_name(name))
                        for name in (
                            "a_discovery.py",
                            "a_conditions.py",
                            "search.py",
                            "search_documents.py",
                            "mappings.py",
                            "document_corpus.py",
                            "a_retrieval.py",
                            "a_semantics.py",
                            "a1_query.py",
                            "a1_graph.py",
                            "a1_interpreter.py",
                            "a1_mappings.py",
                            "a2_evidence.py",
                            "a2_graph.py",
                            "a2_selection.py",
                            "a2_interaction.py",
                        )
                    },
                }
            ),
        }
        if self.agent:
            self.snapshots["agent"] = self.agent.snapshot()

    def retrieve(self, query, *, original_query=None, answers=()):
        if self.a1_graph is None:
            rows, diagnostics = self._retrieve_candidates(query)
            diagnostics["question_source"] = {
                "original_query": original_query if original_query is not None else query,
                "effective_query": query,
                "answers": list(answers),
            }
            return rows, diagnostics
        result = self.a1_graph.invoke(
            {
                "original_query": original_query if original_query is not None else query,
                "effective_query": query,
                "answers": list(answers),
            }
        )
        return result["retrieval_rows"], result["diagnostics"]

    def _retrieve_candidates(self, query, expressions=(), *, mapping_resolution=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 10000:
            raise StageFailure("invalid_query")
        config = self.config
        rows, diagnostics = self.search.retrieve_pool_with_diagnostics(
            query,
            expressions,
            config.channel_k,
            config.candidate_pool_limit,
            config.expansion_limit,
            mapping_resolution=mapping_resolution,
        )
        original_ids = {
            row["doc_id"] for hits in diagnostics["runs"][0]["channels"].values() for row in hits
        }
        original = [row for row in rows if row["doc_id"] in original_ids]
        additions = []
        for run in diagnostics["runs"][1:]:
            for name, hits in run["channels"].items():
                for rank, row in enumerate(hits, 1):
                    additions.append(
                        {
                            "doc_id": row["doc_id"],
                            "evidence": [
                                {
                                    **row,
                                    "channel": name,
                                    "rank": rank,
                                    "expression": run["kind"],
                                    "search_query": run["expression"],
                                    "mapping": run.get("mapping", {}),
                                }
                            ],
                        }
                    )
        for name, hits in self.retriever.channels(query, config.channel_k).items():
            for rank, row in enumerate(hits, 1):
                additions.append(
                    {
                        "doc_id": row["doc_id"],
                        "evidence": [
                            {
                                **row,
                                "channel": name,
                                "rank": rank,
                                "expression": "original",
                                "search_query": query,
                            }
                        ],
                    }
                )
        diagnostics["optional_channels"] = {}
        for name, adapter in sorted(self.channels.items()):
            try:
                hits = adapter.retrieve(query)
                if not isinstance(hits, list) or any(not isinstance(r, dict) for r in hits):
                    raise StageFailure("invalid_channel_output")
                for rank, row in enumerate(hits, 1):
                    additions.append(
                        {
                            "doc_id": row.get("doc_id"),
                            "score": 0,
                            "evidence": [
                                {
                                    "channel": name,
                                    "fusion_channel": "optional:" + name,
                                    "rank": rank,
                                    "expression": "original",
                                }
                            ],
                        }
                    )
                diagnostics["optional_channels"][name] = {"ids": [r.get("doc_id") for r in hits]}
            except Exception as exc:
                diagnostics["optional_channels"][name] = {
                    "failure": exc.code
                    if isinstance(exc, StageFailure)
                    else "optional_channel_failed"
                }
        rows, final = finalize_pool(
            original,
            additions,
            self.search.store.catalog,
            pool_limit=config.candidate_pool_limit,
            addition_limit=config.expansion_limit,
            fusion=self.retriever.describe()["fusion"],
        )
        diagnostics.update(
            candidate_count=len(rows), final_pool=final, retrieval_policy=self.retriever.describe()
        )
        diagnostics["rejected"] = final["rejected"]
        if not rows:
            raise StageFailure("retrieval_empty", "unresolved")
        return rows, diagnostics

    def save_candidates(
        self,
        query,
        context,
        candidates,
        diagnostics,
        ttl_seconds=86400,
        *,
        agent_state=None,
        agent_run=None,
        answers=None,
        clarification=None,
    ):
        context = validate_context(context)
        if type(ttl_seconds) not in (int, float) or not 0 < ttl_seconds <= 604800:
            raise StageFailure("invalid_ttl")
        order = display_order(candidates)
        if not order:
            raise StageFailure("no_compatible_candidates", "unresolved")
        now = self.repository.clock()
        trace = Trace()
        trace.add(
            "a_discovery",
            query=query,
            context=context,
            diagnostics=diagnostics,
            display_order=order,
        )
        bundle = {
            "schema_version": "claview-candidates/1",
            "candidate_set_id": uuid.uuid4().hex,
            "context": context,
            "original_query": query,
            "query_hash": digest(query),
            "snapshots": self.snapshots,
            "candidate_pool": candidates,
            "display_order": order,
            "diagnostics": {
                **diagnostics,
                "rank_version": RANK_VERSION,
                "rank_weights": RANK_WEIGHTS,
                "tie_break": "doc_id_ascending",
            },
            "created_at": now,
            "expires_at": now + ttl_seconds,
            "status": AWAITING_USER,
            "selection": [],
            "a_outcome": None,
            "trace": asdict(trace),
        }
        if agent_state is not None:
            bundle.update(
                original_query=agent_state["original_query"],
                search_query=query,
                agent_state=agent_state,
            )
        if "a1" in diagnostics:
            bundle["a1_state"] = diagnostics["a1"]
        if "a2" in diagnostics:
            bundle["a2_state"] = diagnostics["a2"]
        if "selection_decision" in diagnostics:
            bundle["selection_decision"] = diagnostics["selection_decision"]
            bundle["selection_mode"] = self.selection_mode
        if answers is not None:
            bundle["a_answers"] = answers
        return (
            self.repository.put(bundle, agent_run=agent_run, clarification=clarification) or bundle
        )

    def decision_outcome(self, bundle, page_size=5):
        if bundle["a_outcome"]:
            return {
                **bundle["a_outcome"],
                "candidate_set_id": bundle["candidate_set_id"],
                "context": bundle["context"],
            }
        decision = bundle.get("selection_decision", {})
        if self.selection_mode == "auto" and decision.get("auto_eligible"):
            result = self.confirm_tables(
                bundle["candidate_set_id"],
                decision["proposed_ids"],
                bundle["context"],
                origin="automatic",
            )
            return {
                **result,
                "candidate_set_id": bundle["candidate_set_id"],
                "context": bundle["context"],
            }
        response = outcome(
            "A",
            trace_ref=bundle["trace"]["run_id"],
            interaction=page_view(bundle, page_size=page_size),
        )
        if self.selection_mode == "auto" and decision.get("route") == "unresolved":
            failure = outcome("A", failure=StageFailure("a2_" + decision["reason"], "unresolved"))
            response.update(status=failure["status"], issues=failure["issues"])
            response["issues"][0]["retryable"] = True
        return response

    def search_tables(self, query, context, page_size=5, ttl_seconds=86400):
        if self.agent:
            return self.agent.search_tables(self, query, context, page_size, ttl_seconds)
        validate_context(context)
        self.validate_request(query, context)
        if (
            self.basis
            and self.basis.assess(query, next(iter(self.search.store.catalog)))["support_scope"]
            == "out_of_scope"
        ):
            raise StageFailure("source_out_of_scope", "unresolved")
        rows, diagnostics = self.retrieve(query)
        candidates, diagnostics = self.assess_rows(query, rows, diagnostics)
        bundle = self.save_candidates(query, context, candidates, diagnostics, ttl_seconds)
        return self.decision_outcome(bundle, page_size)

    def assess_rows(self, query, rows, diagnostics):
        candidates, rejected = assess_candidates(query, rows, self.search)
        diagnostics["rejected"] = diagnostics.get("rejected", []) + rejected
        if self.basis:
            decorate_candidates(
                query,
                candidates,
                self.basis,
                rank=self.retriever.policy in {"rrf", "raw_sum", "rrf_protected"},
            )
            if self.retriever.policy == "rrf_protected":
                protected = sorted(
                    (
                        c
                        for c in candidates
                        if not c["conflicts"] and "original_display_score" in c["rank_signals"]
                    ),
                    key=lambda c: (-c["rank_signals"]["original_display_score"], c["doc_id"]),
                )[:5]
                protected_ids = {c["doc_id"] for c in protected}
                for candidate in candidates:
                    candidate["rank_signals"]["display_group"] = (
                        0 if candidate["doc_id"] in protected_ids else 1
                    )
                diagnostics["protected_first_page_ids"] = [c["doc_id"] for c in protected]
            diagnostics["basis_states"] = {
                state: sum(c["basis_analysis"]["joint_state"] == state for c in candidates)
                for state in ("match", "conflict", "unknown")
            }
        diagnostics["assessment"] = {
            "displayable_count": len(display_order(candidates)),
            "conflicts": [
                {"doc_id": c["doc_id"], "reasons": [e["reason"] for e in c["conflicts"]]}
                for c in candidates
                if c["conflicts"]
            ],
            "unknown_candidate_count": sum(bool(c["unknowns"]) for c in candidates),
        }
        if self.a2_graph:
            question = diagnostics.get("a1")
            if question is None:
                source = diagnostics.get("question_source", {"original_query": query})
                question = prepare_question(**source)
            try:
                state = self.a2_graph.invoke(
                    {"question": question, "candidate_ids": [c["doc_id"] for c in candidates]}
                )
            except Exception:
                if self.table_policy is None:
                    raise
                diagnostics["selection_decision"] = failure_decision("a2_evidence_failed")
                return candidates, diagnostics
            assessment = state["assessment"]
            if "selection_decision" in state:
                diagnostics["selection_decision"] = state["selection_decision"]
                decision = diagnostics["selection_decision"]
                if decision.get("auto_eligible") and not set(decision["proposed_ids"]) <= set(
                    display_order(candidates)
                ):
                    decision.update(
                        auto_eligible=False,
                        route="unresolved",
                        reason="semantic_ambiguity",
                        reason_detail="existing_discovery_policy_conflict",
                    )
            diagnostics["a2"] = assessment
            for candidate in candidates:
                row = assessment["by_doc_id"][candidate["doc_id"]]
                candidate["a2_summary"] = {
                    key: row[key]
                    for key in (
                        "whole_request_state",
                        "whole_request_reason",
                        "a_unresolved_requirement_ids",
                        "b_condition_requirement_ids",
                        "conflict_requirement_ids",
                        "metadata_refresh_needed",
                    )
                }
        return candidates, diagnostics

    def clarify_tables(self, set_id, answer, context, page_size=5, ttl_seconds=86400):
        if self.agent is None:
            from .a2_interaction import clarify

            return clarify(self, set_id, answer, context, page_size, ttl_seconds)
        return self.agent.clarify(self, set_id, answer, context, page_size, ttl_seconds)

    def reassess_tables(self, set_id, context, page_size=5, ttl_seconds=86400):
        from .a2_interaction import clarify

        return clarify(self, set_id, "__retry__", context, page_size, ttl_seconds, retry=True)

    def get_candidates(self, set_id, context, page=1, page_size=5):
        bundle = self.repository.get(set_id, context, self.snapshots, active=True)
        return page_view(bundle, page, page_size)

    def validate_request(self, query, context):
        if not isinstance(query, str) or not query.strip() or len(query) > 10000:
            raise StageFailure("invalid_query")
        self.repository.register_request(context, digest(query))

    def confirm_tables(self, set_id, selection, context, *, origin="user"):
        def validate(bundle, ids):
            if origin == "automatic":
                if self.selection_mode != "auto" or self.table_policy is None:
                    raise StageFailure("automatic_selection_disabled", "unresolved")
                question = bundle.get("a1_state") or prepare_question(
                    bundle["original_query"], answers=bundle.get("a_answers", [])
                )
                self.table_policy.validate_proposal(question, bundle["a2_state"], ids)
            query = bundle.get("search_query", bundle["original_query"])
            if bundle.get("a_answers"):
                query = prepare_question(bundle["original_query"], answers=bundle["a_answers"])[
                    "effective_query"
                ]
            for key in ids:
                if key not in self.search.store.catalog:
                    raise StageFailure("invalid_official_id")
                check = assess_candidate(query, key, self.search)
                if check["excluded"]:
                    raise StageFailure("explicit_condition_conflict", "unresolved")
                if self.basis and self.basis.assess(query, key)["joint_state"] == "conflict":
                    raise StageFailure("explicit_condition_conflict", "unresolved")
                if (
                    self.agent
                    and self.agent.basis(self).assess(query, key)["joint_state"] == "conflict"
                ):
                    raise StageFailure("explicit_condition_conflict", "unresolved")
            return outcome(
                "A",
                {
                    "tables": [
                        {
                            "org_id": self.search.store.catalog[k]["org_id"],
                            "table_id": self.search.store.catalog[k]["table_id"],
                            "table_name": self.search.store.catalog[k]["title"],
                        }
                        for k in ids
                    ]
                },
                trace_ref=bundle["trace"]["run_id"],
            )

        return self.repository.confirm(
            set_id, selection, context, self.snapshots, validate, origin=origin
        )


def run_b(discovery, set_id, context, services, retry=False, runner=None):
    """Persist A and B independently; explicit retry never repeats A retrieval."""
    from .graphs import build_b_graph

    bundle = discovery.repository.get(set_id, context)
    state = {
        "query": bundle.get("search_query", bundle["original_query"]),
        **bundle["context"],
        "candidate_set_id": set_id,
        "stage_outcomes": {},
    }
    if "agent_state" in bundle:
        state["a_context"] = {
            "original_query": bundle["original_query"],
            "answers": bundle["agent_state"]["answers"],
            "unresolved_conditions": bundle["agent_state"]["unresolved_conditions"],
            "snapshots": bundle["snapshots"],
        }
    elif bundle.get("a_answers"):
        state["a_context"] = {
            "original_query": bundle["original_query"],
            "answers": bundle["a_answers"],
            "snapshots": bundle["snapshots"],
        }
    if bundle["a_outcome"]:
        state["a_result"] = bundle["a_outcome"]["result"]
        state["stage_outcomes"]["A"] = bundle["a_outcome"]
    trace = Trace()
    trace.add("b_execution_config", provider=type(services.model).__name__, retry=retry)
    attempt_id = None
    try:
        _, attempt_id, cached = discovery.repository.begin_b(
            set_id, context, discovery.snapshots, retry
        )
    except StageFailure as exc:
        result = outcome("B", failure=exc, trace_ref=trace.run_id)
    else:
        if cached:
            result = cached
        else:
            local_services = replace(services, trace=trace)
            try:
                if runner:
                    result = runner(state["query"], state["a_result"], local_services)
                else:
                    result = build_b_graph(local_services).invoke(
                        {"query": state["query"], "a_result": state["a_result"]},
                        {"recursion_limit": 10 + 4 * len(state["a_result"]["tables"])},
                    )["stage_outcome"]
            except Exception as exc:
                result = outcome(
                    "B",
                    failure=exc
                    if isinstance(exc, StageFailure)
                    else StageFailure("b_execution_error"),
                    trace_ref=trace.run_id,
                )
            except BaseException:
                discovery.repository.finish_b(
                    attempt_id,
                    outcome("B", failure=StageFailure("b_indeterminate", "unresolved")),
                    asdict(trace),
                    indeterminate=True,
                )
                raise
            try:
                discovery.repository.finish_b(attempt_id, result, asdict(trace))
            except StageFailure as exc:
                result = outcome("B", failure=exc, trace_ref=trace.run_id)
    state["stage_outcomes"]["B"] = result
    attempts = discovery.repository.get(set_id, context)["b_attempts"]
    if attempts:
        last = attempts[-1]
        config_event = next(
            (
                e
                for e in (last["trace"] or {}).get("events", [])
                if e["event"] == "b_execution_config"
            ),
            {},
        )
        state["b_execution"] = {
            "attempt_id": last["id"],
            "status": last["status"],
            "reused": attempt_id is None,
            "provider": config_event.get("provider"),
        }
    if result["status"] == "ok":
        state["b_result"] = result["result"]
    state["response"] = {
        "status": result["status"],
        "completed_stage": "B"
        if result["status"] == "ok"
        else "A"
        if "a_result" in state
        else None,
        "stopped_stage": "B",
        "issues": result["issues"],
        "trace_ref": result["trace_ref"],
        **({"confirmation": result["result"]} if result["status"] == "needs_confirmation" else {}),
        **(
            {"notices": result["result"]["period_notices"]}
            if result["status"] == "ok" and result["result"].get("period_notices")
            else {}
        ),
    }
    return state
