"""Bounded advisory calls and a disposable cache, separate from CandidateStore."""

from __future__ import annotations

import asyncio
import copy
import sqlite3
import time
from dataclasses import asdict, dataclass
from decimal import Decimal

from .a_model_contracts import (
    BASE_PROMPT,
    PROMPT_VERSION,
    PROMPTS,
    canonical,
    fingerprint,
    role_schema,
    validate_grounding,
    validate_meaning,
)
from .a_model_providers import ModelFault
from .contracts import StageFailure
from .models import check_output


@dataclass(frozen=True)
class Price:
    currency: str
    input_per_million: str
    output_per_million: str
    checked_at: str
    source: str

    def __post_init__(self):
        if not self.currency or not self.checked_at or not self.source:
            raise ValueError("Price provenance required")
        for amount in (self.input_per_million, self.output_per_million):
            value = Decimal(amount)
            if not value.is_finite() or value < 0:
                raise ValueError("Invalid price")

    def cost(self, usage):
        return (
            Decimal(self.input_per_million) * usage[0] + Decimal(self.output_per_million) * usage[1]
        ) / 1_000_000


@dataclass(frozen=True)
class Limits:
    max_calls: int = 2
    max_tokens: int = 262144
    max_seconds: float = 30
    max_retries: int = 1
    max_output_tokens: int = 512
    currency: str = "KRW"
    max_cost: str = "0"

    def __post_init__(self):
        if (
            any(
                type(n) is not int or n < 0
                for n in (self.max_calls, self.max_tokens, self.max_retries, self.max_output_tokens)
            )
            or not 0 < self.max_seconds <= 300
            or not self.max_output_tokens
            or not Decimal(self.max_cost).is_finite()
            or Decimal(self.max_cost) < 0
        ):
            raise ValueError("Invalid A model budget")


class AdviceCache:
    """Only validated advice is cached. Never stores candidate selection or confirmation."""

    def __init__(self, path, *, ttl_seconds=3600, clock=time.time):
        if not 0 < ttl_seconds <= 86400:
            raise ValueError("Invalid cache TTL")
        self.path, self.ttl, self.clock = path, ttl_seconds, clock
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS a_advice (key TEXT PRIMARY KEY, "
                "expires REAL NOT NULL, value TEXT NOT NULL)"
            )

    def get(self, key):
        import json

        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT expires,value FROM a_advice WHERE key=?", (key,)).fetchone()
        if row is None or row[0] <= self.clock():
            return None
        try:
            return json.loads(row[1])
        except ValueError:
            return None

    def put(self, key, value):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO a_advice VALUES (?,?,?)",
                (key, self.clock() + self.ttl, canonical(value)),
            )


class RoleRuntime:
    """One instance per request. All roles and repair attempts share this ledger."""

    def __init__(
        self,
        limits=Limits(),
        *,
        cache=None,
        enabled=False,
        allow_live=False,
        prompt_version=PROMPT_VERSION,
        model_revision="public-spec-2026-10-05",
        clock=time.monotonic,
        attempt_ledger=None,
    ):
        self.limits, self.cache = limits, cache
        self.enabled, self.allow_live = enabled, allow_live
        self.prompt_version, self.model_revision = prompt_version, model_revision
        self.clock, self.started = clock, clock()
        self.attempt_ledger = attempt_ledger
        self.calls = self.external_calls = self.reserved_tokens = 0
        self.reserved_cost = Decimal(0)
        self.trace = []
        self.blocked = False
        self._lock = asyncio.Lock()

    def fallback(self, reason):
        self.trace.append({"event": "fallback", "reason": reason})
        return {
            "status": "fallback",
            "reason": reason,
            "value": None,
            "advisory_only": True,
            "fallback": "existing_search_or_template",
        }

    def summary(self):
        return {
            "calls": self.calls,
            "external_calls": self.external_calls,
            "reserved_tokens": self.reserved_tokens,
            "reserved_cost": str(self.reserved_cost),
            "currency": self.limits.currency,
            "actual_cost": "0" if not self.external_calls else None,
            "actual_cost_status": "no_external_calls"
            if not self.external_calls
            else "invoice_not_available",
            "elapsed_seconds": self.clock() - self.started,
            "trace": copy.deepcopy(self.trace),
        }

    async def invoke(self, role, packet, provider, *, price=None):
        async with self._lock:
            return await self._invoke(role, copy.deepcopy(packet), provider, price)

    async def _invoke(self, role, packet, provider, price):
        if not self.enabled:
            return self.fallback("disabled")
        if not provider.is_mock and not self.allow_live:
            return self.fallback("live_disabled")
        if self.blocked:
            return self.fallback("request_budget_blocked")
        if (price and price.currency != self.limits.currency) or (
            not provider.is_mock and not price
        ):
            return self.fallback("verified_price_required")
        if not provider.available:
            return self.fallback("model_unavailable")
        if role == "ClarificationWriter":
            return self.fallback("template_preferred_writer_deferred")
        if role == "EvidenceJudge" and (not packet["candidate_ids"] or not packet["conditions"]):
            return self.fallback("no_judgment_pairs")
        schema = role_schema(role, packet)
        key = fingerprint(
            {
                "packet": packet,
                "role": role,
                "schema": schema,
                "prompt": BASE_PROMPT + PROMPTS[role],
                "prompt_version": self.prompt_version,
                "model_revision": self.model_revision,
                "provider": provider.identity,
                "max_output_tokens": self.limits.max_output_tokens,
            }
        )
        if self.cache:
            try:
                cached = self.cache.get(key)
                if cached:
                    check_output(cached["value"], schema)
                    validation = validate_grounding(role, cached["value"], packet)
                    validation.update(validate_meaning(role, cached["value"], packet))
                    self.trace.append({"event": "cache_hit", "key": key, "role": role})
                    return {**cached, "validation": validation, "cache_hit": True}
            except (sqlite3.Error, StageFailure, KeyError, TypeError):
                self.trace.append({"event": "cache_ignored", "role": role})
        last = "not_attempted"
        for attempt in range(self.limits.max_retries + 1):
            remaining = self.limits.max_seconds - (self.clock() - self.started)
            if remaining <= 0:
                return self.fallback("time_budget")
            try:
                url, body = provider.build(
                    role, packet, schema, self.limits.max_output_tokens, repair=attempt > 0
                )
                reservation = provider.reservation(body, self.limits.max_output_tokens)
            except ModelFault as fault:
                return self.fallback(fault.code)
            cost = price.cost(reservation) if price else Decimal(0)
            if (
                self.calls >= self.limits.max_calls
                or self.reserved_tokens + sum(reservation) > self.limits.max_tokens
                or self.reserved_cost + cost > Decimal(self.limits.max_cost)
            ):
                return self.fallback("call_token_or_cost_budget")
            record = {
                "event": "attempt",
                "role": role,
                "key": key,
                "attempt": attempt + 1,
                "repair": attempt > 0,
                "provider": provider.identity,
                "reserved_input_tokens": reservation[0],
                "reserved_output_tokens": reservation[1],
                "reserved_cost": str(cost),
                "price": asdict(price) if price else None,
                "actual_cost": "0" if provider.is_mock else None,
            }
            ticket = None
            if self.attempt_ledger is not None:
                try:
                    ticket, experiment_remaining = self.attempt_ledger.reserve(record)
                    remaining = min(remaining, experiment_remaining)
                except ModelFault as fault:
                    return self.fallback(fault.code)
            self.calls += 1
            self.external_calls += int(not provider.is_mock)
            self.reserved_tokens += sum(reservation)
            self.reserved_cost += cost
            self.trace.append(record)
            began = self.clock()
            usage = None
            result = None
            try:
                reply = await asyncio.wait_for(provider.send(url, body, remaining), remaining)
                usage = reply.usage
                if self.attempt_ledger is not None:
                    record["parsed_response"] = reply.value
                    record["resolved_model"] = reply.resolved_model
                    record["diagnostics"] = reply.diagnostics
                check_output(reply.value, schema)
                record["schema_valid"] = True
                validation = validate_grounding(role, reply.value, packet)
                record["source_grounding_valid"] = True
                validation.update(validate_meaning(role, reply.value, packet))
                result = {
                    "status": "advice",
                    "value": reply.value,
                    "validation": validation,
                    "diagnostics": reply.diagnostics,
                    "model": reply.resolved_model,
                    "cache_hit": False,
                    "advisory_only": True,
                }
                last = "ok"
            except (TimeoutError, ModelFault, StageFailure) as fault:
                # wait_for owns the workflow deadline; HTTP timeouts are ModelFaults.
                # Do not depend on a second clock reading crossing the same boundary.
                last = "time_budget" if isinstance(fault, TimeoutError) else fault.code
                if isinstance(fault, ModelFault):
                    usage = fault.usage
            record.update(
                status=last,
                elapsed_seconds=self.clock() - began,
                usage={"input_tokens": usage[0], "output_tokens": usage[1]} if usage else None,
                estimated_cost=str(price.cost(usage)) if price and usage else None,
            )
            if self.attempt_ledger is not None:
                self.attempt_ledger.finish(ticket, record)
            if self.clock() - self.started > self.limits.max_seconds:
                return self.fallback("time_budget")
            if usage and (usage[0] > reservation[0] or usage[1] > reservation[1]):
                self.blocked = True
                return self.fallback("usage_exceeded_reservation")
            if result:
                if usage is None:
                    self.blocked = True
                    return self.fallback("usage_missing")
                if self.cache:
                    try:
                        self.cache.put(key, result)
                    except sqlite3.Error:
                        self.trace.append({"event": "cache_write_failed", "role": role})
                return result
            # Only format/grounding corrections are retried. Network/quota faults fall back.
            if last not in {
                "format_error",
                "invalid_model_output",
                "query_span_mismatch",
                "generated_official_id",
                "judgment_pair_mismatch",
                "explicit_condition_conflict",
                "missing_supporting_citation",
                "citation_mismatch",
                "missing_judgment_pair",
            }:
                break
        return self.fallback(last)
