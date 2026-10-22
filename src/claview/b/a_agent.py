"""Bounded advisory integration for user-confirmed A; live adoption remains deferred."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict, dataclass
from pathlib import Path

from .a_discovery import display_order, page_view
from .a_model_contracts import PROMPT_VERSION, clarification_template, make_packet
from .a_model_experiment import ExperimentLedger
from .a_model_runtime import AdviceCache, Limits, Price, RoleRuntime
from .a_retrieval import finalize_pool
from .a_semantics import AXES, BasisEvidence, choices_in, decorate_candidates, explicit_conditions
from .candidate_store import validate_context
from .contracts import StageFailure, fingerprint, outcome
from .document_corpus import source_entry
from .search import grams
from .search_documents import digest

VERSION = "claview-a-agent/1"
ROLES = ("QueryInterpreter", "EvidenceJudge", "DecisionClassifier")


@dataclass(frozen=True)
class AgentConfig:
    enabled: bool = False
    max_research: int = 1
    max_metadata: int = 1
    max_clarifications: int = 1
    max_calls: int = 4
    max_input_tokens: int = 512000
    max_output_tokens: int = 512
    max_seconds: int = 30
    max_candidates: int = 2
    max_evidence_chars: int = 6000

    def __post_init__(self):
        if (
            type(self.enabled) is not bool
            or any(
                type(value) is not int or value < 0
                for key, value in asdict(self).items()
                if key != "enabled"
            )
            or not (
                self.max_research <= 1
                and self.max_metadata <= 1
                and self.max_clarifications <= 1
                and self.max_calls <= 8
                and 1 <= self.max_candidates <= 5
                and 1 <= self.max_seconds <= 300
                and 1 <= self.max_output_tokens <= 4096
                and 512 <= self.max_evidence_chars <= 12000
            )
        ):
            raise ValueError("Invalid A agent limits")


def effective_query(original, answers):
    return original + "".join(f"\n사용자 기준 답변: {row['label']}" for row in answers)


class AAgent:
    """One finite workflow; providers are optional and must be explicitly mocked in this stage."""

    def __init__(
        self,
        config=AgentConfig(),
        providers=None,
        *,
        run_async=asyncio.run,
        provider_revision="injected-mock/1",
    ):
        self.config, self.providers, self.run_async = config, providers or {}, run_async
        self.provider_revision = provider_revision
        if set(self.providers) - set(ROLES):
            raise ValueError("Unknown A role")
        if any(not provider.is_mock for provider in self.providers.values()):
            raise StageFailure("a_live_roles_not_adopted", "unsupported")
        self._basis = {}

    def snapshot(self):
        return digest(
            {
                "version": VERSION,
                "config": asdict(self.config),
                "provider_revision": self.provider_revision,
                "prompt": PROMPT_VERSION,
                "providers": {role: provider.identity for role, provider in self.providers.items()},
                "implementation": {
                    name: fingerprint(Path(__file__).with_name(name))
                    for name in (
                        "a_agent.py",
                        "a_agent_scripted.py",
                        "a_model_contracts.py",
                        "a_model_providers.py",
                        "a_model_runtime.py",
                        "a_model_experiment.py",
                        "candidate_store.py",
                    )
                },
            }
        )

    def basis(self, discovery):
        key = (discovery.search.store.catalog_snapshot, discovery.search.store.snapshot)
        if key not in self._basis:
            self._basis[key] = BasisEvidence(discovery.search.store)
        return self._basis[key]

    def ambiguity_axes(self, discovery, query, candidates, answers):
        """Ask only when exclusive official table titles imply different table choices."""
        resolved = {c["axis"] for c in explicit_conditions(query)} | {a["axis"] for a in answers}
        axes = []
        visible = set(display_order(candidates))
        for axis in ("price_basis", "loan_basis"):
            if axis in resolved:
                continue
            alternatives = {
                tuple(sorted(choices_in(c["table_name"], axis)))
                for c in candidates
                if c["doc_id"] in visible
            }
            exclusive = {choices[0] for choices in alternatives if len(choices) == 1}
            if len(exclusive) > 1:
                axes.append(axis)
        return axes

    def features(self, discovery, query, candidates, diagnostics, answers):
        visible = display_order(candidates)[: self.config.max_candidates]
        basis = self.basis(discovery)
        analyses = [basis.assess(query, key) for key in visible]
        channels = diagnostics.get("runs", [{}])[0].get("channels", {})
        heads = {hits[0]["doc_id"] for hits in channels.values() if hits}
        return {
            "unresolved_mapping": any(
                t["action"] == "deferred" and t.get("source_spans")
                for t in diagnostics.get("mapping_resolution", {}).get("trace", [])
            ),
            "condition_uncertainty": bool(explicit_conditions(query))
            and any(a["joint_state"] == "unknown" for a in analyses),
            "candidate_conflict": any(c["conflicts"] for c in candidates),
            "table_basis_ambiguity": self.ambiguity_axes(discovery, query, candidates, answers),
            "metadata_gap": any(
                not discovery.search.store.records.get(key, {}).get("meta", {}).get("ITM")
                for key in visible
            ),
            "channel_disagreement": len(heads) > 1,
        }

    def packet(
        self, discovery, original, query, answers, candidates, *, metadata=False, interpreter=False
    ):
        store = discovery.search.store
        ids = [] if interpreter else display_order(candidates)[: self.config.max_candidates]
        documents, omissions = [], []
        used = 0
        for key in ids:
            row = store.catalog[key]
            fields = [
                ("catalog", "/" + field, row[field], digest(row))
                for field in ("title", "path", "depth")
            ]
            if metadata:
                record = store.records.get(key, {})
                items = record.get("meta", {}).get("ITM", [])
                ranked = sorted(
                    enumerate(items),
                    key=lambda pair: (
                        -len(grams(query) & grams(str(pair[1].get("ITM_NM", "")))),
                        pair[0],
                    ),
                )
                for index, item in ranked[:2]:
                    for field in ("ITM_NM", "UNIT_NM", "PRD_SE"):
                        if item.get(field):
                            fields.append(
                                (
                                    "metadata",
                                    f"/meta/ITM/{index}/{field}",
                                    item[field],
                                    digest(record),
                                )
                            )
            for source, pointer, value, record_hash in fields:
                entry = source_entry(source, pointer, value, pointer, record_hash)
                text = entry["quote"]
                if used + len(text) > self.config.max_evidence_chars:
                    omissions.append(
                        {"doc_id": key, "pointer": pointer, "reason": "evidence_char_budget"}
                    )
                    continue
                used += len(text)
                documents.append(
                    {
                        "doc_id": key,
                        "chunk_id": f"agent:{key}:{source}:{pointer}",
                        "text": text,
                        "text_hash": digest(text),
                        "source_refs": [
                            {**entry["ref"], "quote": text, "start": 0, "end": len(text)}
                        ],
                    }
                )
        conditions = []
        # Preserve the root query and each answer as separate user sources.
        sources = [
            ("query", original),
            *[(f"prior:{i}", answer["user_input"]) for i, answer in enumerate(answers)],
        ]
        if not interpreter:
            for source_id, text in sources:
                for condition in explicit_conditions(text):
                    span = condition["source_spans"][0]
                    conditions.append(
                        {
                            "condition_id": f"c{len(conditions)}",
                            "axis": condition["axis"],
                            "span": {
                                "source_id": source_id,
                                "start": span["start"],
                                "end": span["end"],
                                "quote": text[span["start"] : span["end"]],
                            },
                        }
                    )
            if not conditions:
                conditions.append(
                    {
                        "condition_id": "c0",
                        "axis": "concept",
                        "span": {
                            "source_id": "query",
                            "start": 0,
                            "end": len(original),
                            "quote": original,
                        },
                    }
                )
        versions = {
            "dictionary": discovery.search.mapping_dictionary.snapshot or "no-dictionary",
            "metadata": store.snapshot,
            "catalog": store.catalog_snapshot,
            "policy": digest(discovery.snapshots),
        }
        packet = make_packet(
            original,
            versions,
            prior_turns=[{"role": "user", "text": a["user_input"]} for a in answers],
            candidate_ids=ids,
            conditions=conditions,
            documents=documents,
            store=store,
            clarification_axes=self.ambiguity_axes(discovery, query, candidates, answers),
        )
        return packet, {"metadata": metadata, "evidence_chars": used, "omissions": omissions}

    def search_tables(
        self, discovery, query, context, page_size=5, ttl_seconds=86400, *, answers=()
    ):
        validate_context(context)
        if not isinstance(query, str) or not query.strip() or len(query) > 10000:
            raise StageFailure("invalid_query")
        answers = copy.deepcopy(list(answers))
        effective = effective_query(query, answers)
        discovery.validate_request(effective, context)
        run_id = digest(
            {
                "context": context,
                "original_query": query,
                "answers": answers,
                "snapshots": discovery.snapshots,
            }
        )
        cached = discovery.repository.claim_agent(run_id, context, digest(effective))
        if cached:
            bundle = discovery.repository.get(cached, context, discovery.snapshots, active=True)
            if bundle["a_outcome"]:
                return {
                    **bundle["a_outcome"],
                    "candidate_set_id": cached,
                    "context": bundle["context"],
                }
            return outcome(
                "A",
                interaction=page_view(bundle, page_size=page_size),
                trace_ref=bundle["trace"]["run_id"],
            )
        try:
            if (
                self.basis(discovery).assess(effective, next(iter(discovery.search.store.catalog)))[
                    "support_scope"
                ]
                == "out_of_scope"
            ):
                raise StageFailure("source_out_of_scope", "unresolved")
            rows, diagnostics = (
                discovery.retrieve(effective, original_query=query, answers=answers)
                if answers
                else discovery.retrieve(effective)
            )
            candidates, diagnostics = discovery.assess_rows(effective, rows, diagnostics)
            # Shared official condition checks run on every agent path, independent of model advice.
            if discovery.basis is None:
                decorate_candidates(effective, candidates, self.basis(discovery), rank=False)
            data = self.run_async(
                self._advise(
                    discovery,
                    query,
                    effective,
                    answers,
                    context,
                    rows,
                    candidates,
                    diagnostics,
                    run_id,
                )
            )
            candidates, diagnostics, agent_state = data
            bundle = discovery.save_candidates(
                effective,
                context,
                candidates,
                diagnostics,
                ttl_seconds,
                agent_state=agent_state,
                agent_run=run_id,
            )
            return outcome(
                "A",
                interaction=page_view(bundle, page_size=page_size),
                trace_ref=bundle["trace"]["run_id"],
            )
        except StageFailure as exc:
            discovery.repository.fail_agent(run_id, exc.code)
            raise

    async def _advise(
        self, discovery, original, query, answers, context, rows, candidates, diagnostics, run_id
    ):
        cfg = self.config
        features = self.features(discovery, query, candidates, diagnostics, answers)
        state = {
            "version": VERSION,
            "mode": "mock_integration",
            "original_query": original,
            "answers": answers,
            "features": features,
            "clarification": None,
            "research_rounds": 0,
            "metadata_rounds": 0,
            "roles": {},
            "trace": [
                "preserve_query",
                "dictionary_and_retrieve",
                "official_conditions",
                "call_gate",
            ],
            "limits": asdict(cfg),
            "termination_reason": "no_additional_judgment_needed",
        }
        directory = discovery.repository.path.parent / (
            discovery.repository.path.name + ".a-advice"
        )
        runtime = None
        ledger = None
        stopped = False

        async def invoke(role, metadata=False):
            nonlocal runtime, ledger, stopped
            if stopped:
                return None
            if role not in self.providers:
                state["roles"][role] = {"status": "fallback", "reason": "provider_missing"}
                state["termination_reason"] = "provider_missing"
                stopped = True
                return None
            if runtime is None:
                directory.mkdir(parents=True, exist_ok=True)
                ledger = ExperimentLedger(
                    directory / (run_id + ".sqlite"),
                    plan_id=run_id,
                    mode="mock",
                    limits={
                        "max_calls": max(1, cfg.max_calls),
                        "max_input_tokens": max(1, cfg.max_input_tokens),
                        "max_output_tokens": max(1, cfg.max_calls * cfg.max_output_tokens),
                        "max_seconds": cfg.max_seconds,
                        "max_cost_by_currency": {"KRW": "0"},
                    },
                    factor="1",
                    clock=discovery.repository.clock,
                )
                ledger.claim("workflow")
                runtime = RoleRuntime(
                    Limits(
                        max_calls=cfg.max_calls,
                        max_tokens=cfg.max_input_tokens + cfg.max_calls * cfg.max_output_tokens,
                        max_seconds=cfg.max_seconds,
                        max_retries=1,
                        max_output_tokens=cfg.max_output_tokens,
                    ),
                    enabled=True,
                    allow_live=False,
                    attempt_ledger=ledger,
                    cache=AdviceCache(directory / "cache.sqlite", clock=discovery.repository.clock),
                )
            packet, evidence = self.packet(
                discovery,
                original,
                query,
                answers,
                candidates,
                metadata=metadata,
                interpreter=role == "QueryInterpreter",
            )
            advice = await runtime.invoke(
                role,
                packet,
                self.providers[role],
                price=Price("KRW", "0", "0", "synthetic", "mock://integration-no-billing"),
            )
            state["roles"][role] = {
                **advice,
                "evidence_scope": evidence,
                "input_packet": packet,
                "input_hash": digest(packet),
            }
            state["trace"].append(role)
            if advice["status"] != "advice":
                stopped = True
                state["termination_reason"] = advice["reason"]
                return None
            return advice["value"]

        if any(features.values()):
            state["termination_reason"] = "bounded_advice_complete"
            if features["unresolved_mapping"] or features["channel_disagreement"]:
                interpreted = await invoke("QueryInterpreter")
                if interpreted and interpreted["expansions"] and cfg.max_research:
                    additions = []
                    for expansion in interpreted["expansions"]:
                        term = expansion["term"]
                        hits, _ = discovery.search.retrieve_pool_with_diagnostics(
                            term,
                            [],
                            discovery.config.channel_k,
                            discovery.config.candidate_pool_limit,
                            discovery.config.expansion_limit,
                        )
                        for rank, hit in enumerate(hits, 1):
                            additions.append(
                                {
                                    "doc_id": hit["doc_id"],
                                    "evidence": [
                                        {
                                            "channel": "model_expansion",
                                            "rank": rank,
                                            "expression": term,
                                            "source_span": expansion["span"],
                                        }
                                    ],
                                }
                            )
                    # Original request candidates, including earlier local additions, are protected.
                    previous_added = diagnostics.get("final_pool", {}).get("added_count", 0)
                    rows, fusion = finalize_pool(
                        rows,
                        additions,
                        discovery.search.store.catalog,
                        pool_limit=discovery.config.candidate_pool_limit,
                        addition_limit=max(0, discovery.config.expansion_limit - previous_added),
                    )
                    candidates, diagnostics = discovery.assess_rows(query, rows, diagnostics)
                    if discovery.basis is None:
                        decorate_candidates(query, candidates, self.basis(discovery), rank=False)
                    diagnostics["agent_expansion"] = fusion
                    state["research_rounds"] = 1
                    state["trace"].append("research_once_and_recheck_conditions")
            if not stopped and (
                features["condition_uncertainty"] or features["candidate_conflict"]
            ):
                if cfg.max_metadata:
                    state["metadata_rounds"] = 1
                    state["trace"].append("enrich_official_metadata")
                await invoke("EvidenceJudge", metadata=bool(state["metadata_rounds"]))
            decision = await invoke("DecisionClassifier", metadata=bool(state["metadata_rounds"]))
            if decision:
                action = decision["action"]
                state["suggested_action"] = action
                if (
                    action == "enrich_metadata"
                    and not state["metadata_rounds"]
                    and cfg.max_metadata
                ):
                    state["metadata_rounds"] = 1
                    state["trace"].append("enrich_official_metadata")
                    await invoke("EvidenceJudge", metadata=True)
                elif action == "ask_clarification":
                    axes = self.ambiguity_axes(discovery, query, candidates, answers)
                    if axes and len(answers) < cfg.max_clarifications:
                        axis = axes[0]
                        state["clarification"] = {
                            "axis": axis,
                            "question": clarification_template(axis),
                            "options": [
                                {"value": key, "label": values[0]}
                                for key, values in AXES[axis]["choices"].items()
                            ],
                        }
                        state["termination_reason"] = "awaiting_meaning_clarification"
                    else:
                        state["termination_reason"] = (
                            "clarification_not_table_changing_or_budget_exhausted"
                        )
                elif action == "insufficient":
                    state["termination_reason"] = "advisory_insufficient_evidence"
        summary = (
            runtime.summary()
            if runtime
            else {"calls": 0, "external_calls": 0, "trace": [], "reserved_tokens": 0}
        )
        state["model_usage"] = {
            "mode": "mock_integration",
            "calls": summary["calls"],
            "external_calls": summary["external_calls"],
            "cache_hits": sum(r["event"] == "cache_hit" for r in summary["trace"]),
            "meaning_verified": False,
        }
        state["runtime"] = summary
        state["unresolved_conditions"] = sorted(
            {
                check["label"]
                for candidate in candidates
                for check in candidate.get("basis_analysis", {}).get("checks", [])
                if check["state"] == "unknown"
            }
        )
        state["b_deferred"] = ["ITEM", "classification", "period", "frequency"]
        for candidate in candidates:
            candidate["model_advice"] = {
                "used": bool(summary["calls"] or state["model_usage"]["cache_hits"]),
                "mode": "mock_integration",
                "meaning_verified": False,
            }
            judgment = state["roles"].get("EvidenceJudge", {}).get("value")
            if judgment:
                condition_text = {
                    c["condition_id"]: c["span"]["quote"]
                    for c in state["roles"]["EvidenceJudge"]["input_packet"]["conditions"]
                }
                candidate["model_advice"]["condition_checks"] = [
                    {
                        "condition": condition_text[j["condition_id"]],
                        "state": j["verdict"],
                        "evidence": [citation["quote"] for citation in j["citations"]],
                    }
                    for j in judgment["judgments"]
                    if j["doc_id"] == candidate["doc_id"]
                ]
        state["trace"].append("persist_and_wait_for_user")
        if ledger:
            ledger.complete(
                {"advice": {"status": "fallback" if stopped else "advice"}, "runtime": summary}
            )
        return candidates, diagnostics, state

    def clarify(self, discovery, set_id, answer, context, page_size=5, ttl_seconds=86400):
        if not isinstance(answer, str):
            raise StageFailure("invalid_clarification_answer", "unresolved")
        previous = discovery.repository.clarified_result(
            set_id, answer, context, discovery.snapshots
        )
        if previous:
            if previous["a_outcome"]:
                return {
                    **previous["a_outcome"],
                    "candidate_set_id": previous["candidate_set_id"],
                    "context": previous["context"],
                }
            return outcome(
                "A",
                interaction=page_view(previous, page_size=page_size),
                trace_ref=previous["trace"]["run_id"],
            )
        bundle = discovery.repository.get(set_id, context, discovery.snapshots, active=True)
        agent_state = bundle.get("agent_state", {})
        pending = agent_state.get("clarification")
        if not pending or bundle["status"] != "awaiting_user":
            raise StageFailure("no_pending_clarification", "unresolved")
        options = {item["value"]: item["label"] for item in pending["options"]}
        if not isinstance(answer, str) or answer not in options:
            raise StageFailure("invalid_clarification_answer", "unresolved")
        answers = [
            *agent_state["answers"],
            {
                "axis": pending["axis"],
                "choice": answer,
                "label": options[answer],
                "user_input": options[answer],
                "parent_set_id": set_id,
                "source_revision": context["query_revision"],
            },
        ]
        revised = {**context, "query_revision": context["query_revision"] + 1}
        return self.search_tables(
            discovery, bundle["original_query"], revised, page_size, ttl_seconds, answers=answers
        )
