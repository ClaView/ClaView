"""Frozen A-role probes and a durable, fail-closed whole-experiment attempt budget."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from .a_model_contracts import (
    BASE_PROMPT,
    PROMPT_VERSION,
    PROMPTS,
    canonical,
    fingerprint,
    role_schema,
)
from .a_model_providers import AProvider, ModelFault
from .a_model_runtime import Price
from .contracts import fingerprint as file_hash

VERSION = "claview-a-experiment/1"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Path escapes repository")
    return path


def seal(value):
    return {**value, "plan_id": fingerprint(value)}


def verify_plan(root, plan):
    content = {k: v for k, v in plan.items() if k != "plan_id"}
    if plan.get("schema_version") != VERSION or plan.get("plan_id") != fingerprint(content):
        raise ValueError("Plan identity mismatch")
    for relative, expected in plan["frozen_files"].items():
        if file_hash(inside(root, relative)) != expected:
            raise ValueError(f"Frozen file changed: {relative}")
    for job in plan["jobs"]:
        data = read(inside(root, job["file"]))
        packet = data["packet"]
        if (
            fingerprint(packet) != job["packet_hash"]
            or data["schema"] != role_schema(job["role"], packet)
            or data["prompt_version"] != PROMPT_VERSION
            or data["system"] != BASE_PROMPT + PROMPTS[job["role"]]
        ):
            raise ValueError("Frozen role contract mismatch")


def prepare_plan(root, source, pricing, *, ledger_path, scope="smoke", repeat_smoke=False):
    """Read only frozen dev packets. Constructing requests performs no network or key access."""
    root, source, pricing = Path(root).resolve(), Path(source).resolve(), Path(pricing).resolve()
    ledger_relative = inside(root, ledger_path).relative_to(root).as_posix()
    config, prices = read(source), read(pricing)
    if scope not in {"smoke", "roles"} or config["input_split"] != "dev" or config["read_gold"]:
        raise ValueError("Only exposed dev probes are supported")
    factor = Decimal(prices["budget_reserve_factor"])
    if not factor.is_finite() or factor < 1:
        raise ValueError("Invalid budget reserve factor")
    frozen = {source.relative_to(root).as_posix(): file_hash(source)}
    frozen[pricing.relative_to(root).as_posix()] = file_hash(pricing)
    for name, relative in config["source_paths"].items():
        actual = file_hash(inside(root, relative))
        if actual != config["source_sha256"][name]:
            raise ValueError(f"Stage 6 source changed: {name}")
        frozen[relative] = actual
    changed_code = []
    for relative, previous in config["code_sha256"].items():
        actual = file_hash(inside(root, relative))
        if actual != previous:
            changed_code.append(relative)
    # Stage 7 adds an optional durable ledger; role contracts/providers must remain frozen.
    if set(changed_code) - {"src/claview/a_model_runtime.py"}:
        raise ValueError("Unexpected Stage 6 code drift")
    for path in [
        *sorted((root / "src/claview").glob("a_model_*.py")),
        root / "scripts/run_a_model_experiment.py",
    ]:
        frozen[path.relative_to(root).as_posix()] = file_hash(path)
    for relative in [
        "pyproject.toml",
        "uv.lock",
        "data/catalog/search_definitions.json",
        "eval/configs/a_stage7_protocol.json",
        "eval/a_stage2_metric_policy.json",
        *[
            f"src/claview/{name}.py"
            for name in (
                "a_conditions",
                "a_discovery",
                "a_eval_contracts",
                "a_eval_predict",
                "a_eval_score",
                "a_retrieval",
                "a_semantics",
                "candidate_store",
                "contracts",
                "document_corpus",
                "mappings",
                "metadata",
                "models",
                "search",
                "search_documents",
            )
        ],
    ]:
        frozen[relative] = file_hash(inside(root, relative))
    jobs, net, reserved = [], {}, {}
    total_input = total_output = 0
    attempts = config["max_attempts_per_task"]
    for task in config["tasks"]:
        smoke = (
            task["case_id"] == config["initial_probe"][task["role"]]
            and task["profile"] == config["initial_probe"]["profile"]
        )
        if scope == "smoke" and not smoke:
            continue
        path = inside(source.parent, task["file"])
        if file_hash(path) != task["sha256"]:
            raise ValueError("Stage 6 packet changed")
        relative = path.relative_to(root).as_posix()
        frozen[relative] = task["sha256"]
        data = read(path)
        for repetition in range(1 + int(repeat_smoke and smoke)):
            for model in task["models"]:
                provider = object.__new__(AProvider)
                provider.model = model
                _, body = provider.build(
                    task["role"], data["packet"], data["schema"], config["max_output_tokens"]
                )
                reservation = provider.reservation(body, config["max_output_tokens"])
                price = Price(**prices["prices"][model])
                amount = price.cost(reservation) * attempts
                net[price.currency] = net.get(price.currency, Decimal(0)) + amount
                reserved[price.currency] = (
                    reserved.get(price.currency, Decimal(0)) + amount * factor
                )
                total_input += reservation[0] * attempts
                total_output += reservation[1] * attempts
                jobs.append(
                    {
                        "job_id": ":".join(
                            (
                                task["case_id"],
                                task["role"],
                                task["profile"],
                                model,
                                f"r{repetition}",
                            )
                        ),
                        "case_id": task["case_id"],
                        "role": task["role"],
                        "profile": task["profile"],
                        "model": model,
                        "repetition": repetition,
                        "file": relative,
                        "packet_hash": task["packet_hash"],
                        "reservation": list(reservation),
                    }
                )
    # Repeat observations occur after the first pass; retries remain within each task.
    jobs.sort(key=lambda j: j["repetition"])
    plan = seal(
        {
            "schema_version": VERSION,
            "ledger_path": ledger_relative,
            "scope": scope,
            "input_split": "dev",
            "allow_external_calls": False,
            "read_gold": False,
            "parent_config": source.relative_to(root).as_posix(),
            "parent_code_changes": changed_code,
            "frozen_files": frozen,
            "prices": prices["prices"],
            "budget_reserve_factor": str(factor),
            "jobs": jobs,
            "max_attempts_per_task": attempts,
            "max_output_tokens": config["max_output_tokens"],
            "max_seconds_per_task": config["max_seconds_per_task"],
            "limits": {
                "max_calls": len(jobs) * attempts,
                "max_input_tokens": total_input,
                "max_output_tokens": total_output,
                "max_seconds": 600 if scope == "smoke" else 3600,
                "max_cost_by_currency": {k: "0" for k in net},
            },
            "estimate": {
                "basis": "full_context_reservation_not_expected_usage_or_invoice",
                "net_cost_by_currency": {k: str(v) for k, v in net.items()},
                "buffered_cost_by_currency": {k: str(v) for k, v in reserved.items()},
            },
            "quality_status": "inconclusive_no_live_observations",
        }
    )
    verify_plan(root, plan)
    return plan


def validate_grant(plan, grant):
    """A grant records the human-approved scope; preparing a plan never creates approval."""
    if grant.get("plan_id") != plan["plan_id"] or grant.get("allow_external_calls") is not True:
        raise ValueError("Explicit grant for this exact plan is required")
    if not isinstance(grant.get("max_cost_by_currency"), dict):
        raise ValueError("Explicit currency budgets required")
    limits = {**plan["limits"], "max_cost_by_currency": grant["max_cost_by_currency"]}
    if set(limits["max_cost_by_currency"]) != set(plan["limits"]["max_cost_by_currency"]):
        raise ValueError("Currency budgets must match plan")
    for amount in limits["max_cost_by_currency"].values():
        value = Decimal(amount)
        if not value.is_finite() or value <= 0:
            raise ValueError("Positive finite currency budget required")
    return limits


class ExperimentLedger:
    """Persist reservations before dispatch. Uncertain interrupted jobs cannot auto-resume."""

    def __init__(
        self, path, *, plan_id, mode, limits, factor="1.2", clock=time.time, stop_on_failure=True
    ):
        if mode not in {"mock", "live"}:
            raise ValueError("Invalid experiment mode")
        for key in ("max_calls", "max_input_tokens", "max_output_tokens", "max_seconds"):
            if type(limits[key]) is not int or limits[key] <= 0:
                raise ValueError("Invalid experiment limit")
        self.factor = Decimal(factor)
        if not self.factor.is_finite() or self.factor < 1:
            raise ValueError("Invalid reservation factor")
        for amount in limits["max_cost_by_currency"].values():
            if not Decimal(amount).is_finite() or Decimal(amount) < 0:
                raise ValueError("Invalid currency budget")
        self.path, self.clock, self.mode, self.limits = Path(path), clock, mode, limits
        self.stop_on_failure = stop_on_failure
        identity_fields = {"plan_id": plan_id, "mode": mode, "limits": limits, "factor": factor}
        if not stop_on_failure:
            identity_fields["independent_jobs_continue_after_recorded_failure"] = True
        identity = canonical(identity_fields)
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS experiment (identity TEXT, started REAL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT, result TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, job TEXT, "
                "input INTEGER, output INTEGER, currency TEXT, cost TEXT, "
                "record TEXT, done INTEGER)"
            )
            row = db.execute("SELECT identity,started FROM experiment").fetchone()
            if row is None:
                self.started = clock()
                db.execute("INSERT INTO experiment VALUES (?,?)", (identity, self.started))
            elif row[0] != identity:
                raise ValueError("Ledger identity/budget/mode mismatch")
            else:
                self.started = row[1]
        self.job_id = None

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def claim(self, job_id):
        with self.transaction() as db:
            row = db.execute("SELECT state,result FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row and row[0] == "complete":
                return json.loads(row[1])
            if row and not self.stop_on_failure:
                raise ModelFault("experiment_job_already_attempted")
            if db.execute(
                "SELECT 1 FROM jobs WHERE state='running' OR (? AND state='failed')",
                (self.stop_on_failure,),
            ).fetchone():
                raise ModelFault("experiment_stopped_or_indeterminate")
            db.execute("INSERT INTO jobs VALUES (?, 'running', NULL)", (job_id,))
        self.job_id = job_id
        return None

    def reserve(self, record):
        with self.transaction() as db:
            row = db.execute("SELECT state FROM jobs WHERE id=?", (self.job_id,)).fetchone()
            if row != ("running",) or record["provider"]["mode"] != self.mode:
                raise ModelFault("experiment_job_or_mode_mismatch")
            remaining = self.limits["max_seconds"] - (self.clock() - self.started)
            if remaining <= 0 or self.clock() < self.started:
                raise ModelFault("experiment_time_budget")
            if db.execute("SELECT 1 FROM attempts WHERE done=0").fetchone():
                raise ModelFault("experiment_indeterminate_attempt")
            rows = db.execute("SELECT input,output,currency,cost FROM attempts").fetchall()
            price = record["price"]
            if price is None:
                raise ModelFault("verified_price_required")
            currency = price["currency"]
            amount = Decimal(record["reserved_cost"]) * self.factor
            input_tokens, output_tokens = (
                record["reserved_input_tokens"],
                record["reserved_output_tokens"],
            )
            if (
                len(rows) + 1 > self.limits["max_calls"]
                or sum(r[0] for r in rows) + input_tokens > self.limits["max_input_tokens"]
                or sum(r[1] for r in rows) + output_tokens > self.limits["max_output_tokens"]
                or currency not in self.limits["max_cost_by_currency"]
                or sum((Decimal(r[3]) for r in rows if r[2] == currency), Decimal(0)) + amount
                > Decimal(self.limits["max_cost_by_currency"].get(currency, "0"))
            ):
                raise ModelFault("experiment_call_token_or_cost_budget")
            cursor = db.execute(
                "INSERT INTO attempts(job,input,output,currency,cost,record,done) "
                "VALUES (?,?,?,?,?,?,0)",
                (
                    self.job_id,
                    input_tokens,
                    output_tokens,
                    currency,
                    str(amount),
                    canonical(record),
                ),
            )
            return cursor.lastrowid, remaining

    def finish(self, ticket, record):
        with self.transaction() as db:
            cursor = db.execute(
                "UPDATE attempts SET record=?,done=1 WHERE id=? AND job=? AND done=0",
                (canonical(record), ticket, self.job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Unknown or completed attempt")

    def complete(self, result):
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM attempts WHERE done=0").fetchone():
                raise ModelFault("experiment_indeterminate_attempt")
            state = "complete" if result["advice"]["status"] == "advice" else "failed"
            db.execute(
                "UPDATE jobs SET state=?,result=? WHERE id=? AND state='running'",
                (state, canonical(result), self.job_id),
            )
        self.job_id = None

    def results(self):
        with self.transaction() as db:
            rows = db.execute("SELECT id,state,result FROM jobs ORDER BY rowid").fetchall()
        return [
            {"job_id": key, "state": state, "result": json.loads(value) if value else None}
            for key, state, value in rows
        ]

    def audit(self):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT input,output,currency,cost,record,done FROM attempts"
            ).fetchall()
        return {
            "mode": self.mode,
            "reserved_attempts": len(rows),
            "pending_attempts": sum(not r[5] for r in rows),
            "reserved_input_tokens": sum(r[0] for r in rows),
            "reserved_output_tokens": sum(r[1] for r in rows),
            "buffered_reserved_cost_by_currency": {
                currency: str(sum((Decimal(r[3]) for r in rows if r[2] == currency), Decimal(0)))
                for currency in self.limits["max_cost_by_currency"]
            },
            "attempts": [json.loads(r[4]) for r in rows],
        }


def summarize(plan, rows, *, mode):
    """Infrastructure observations only; no synthetic/fallback result counts as model quality."""
    groups = {}
    completed = {r["job_id"]: r for r in rows}
    for job in plan["jobs"]:
        key = f"{job['role']}:{job['profile']}:{job['model']}"
        group = groups.setdefault(
            key,
            {
                "scheduled": 0,
                "observed": 0,
                "called_jobs": 0,
                "validated_advice": 0,
                "fallbacks": 0,
                "attempts": 0,
                "repairs": 0,
                "repeat_attempts": 0,
                "latencies": [],
                "usage": [],
                "estimated_cost": Decimal(0),
                "unknown_usage": 0,
                "cases": set(),
                "job_latencies": [],
                "cost_by_attempt_kind": {},
            },
        )
        group["scheduled"] += 1
        row = completed.get(job["job_id"])
        if mode != "live" or not row or not row["result"]:
            continue
        result = row["result"]
        group["cases"].add(job["case_id"])
        group["job_latencies"].append(result["runtime"].get("elapsed_seconds", 0))
        group["observed"] += 1
        live_attempts = [
            trace
            for trace in result["runtime"]["trace"]
            if trace["event"] == "attempt" and trace["provider"]["mode"] == "live"
        ]
        group["called_jobs"] += int(bool(live_attempts))
        success = (
            result["advice"]["status"] == "advice"
            and bool(live_attempts)
            and not result["advice"].get("cache_hit")
        )
        group["validated_advice"] += int(success)
        group["fallbacks"] += int(result["advice"]["status"] == "fallback")
        for trace in live_attempts:
            kind = ("repeat" if job["repetition"] > 0 else "initial") + (
                "_repair" if trace["repair"] else ""
            )
            bucket = group["cost_by_attempt_kind"].setdefault(
                kind,
                {"attempts": 0, "known_estimated_cost": Decimal(0), "unknown_cost_attempts": 0},
            )
            bucket["attempts"] += 1
            if trace["estimated_cost"] is None:
                bucket["unknown_cost_attempts"] += 1
            else:
                bucket["known_estimated_cost"] += Decimal(trace["estimated_cost"])
            group["attempts"] += 1
            group["repairs"] += int(trace["repair"])
            group["repeat_attempts"] += int(job["repetition"] > 0)
            group["latencies"].append(trace["elapsed_seconds"])
            group["usage"].append(trace["usage"])
            if trace["estimated_cost"] is None:
                group["unknown_usage"] += 1
            else:
                group["estimated_cost"] += Decimal(trace["estimated_cost"])
    for group in groups.values():
        for bucket in group["cost_by_attempt_kind"].values():
            bucket["known_estimated_cost"] = str(bucket["known_estimated_cost"])
        group["distinct_observed_cases"] = len(group.pop("cases"))
        group["observed_job_rate"] = group["observed"] / group["scheduled"]
        group["called_job_rate"] = group["called_jobs"] / group["scheduled"]
        job_latencies = sorted(group.pop("job_latencies"))
        for label, quantile in (("p50", 0.5), ("p95", 0.95)):
            group[f"job_latency_{label}_seconds"] = (
                job_latencies[math.ceil(len(job_latencies) * quantile) - 1]
                if job_latencies
                else None
            )
        latencies = sorted(group.pop("latencies"))
        usage = group.pop("usage")
        group["known_input_tokens"] = sum(u["input_tokens"] for u in usage if u)
        group["known_output_tokens"] = sum(u["output_tokens"] for u in usage if u)
        group["attempt_latency_p50_seconds"] = (
            latencies[math.ceil(len(latencies) * 0.5) - 1] if latencies else None
        )
        group["attempt_latency_p95_seconds"] = (
            latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None
        )
        group["estimated_cost"] = (
            str(group["estimated_cost"])
            if group["attempts"] and not group["unknown_usage"]
            else None
        )
        group["semantic_accuracy"] = None
        group["quality_status"] = "inconclusive_pending_paired_effect_and_human_review"
    return {
        "mode": mode,
        "plan_id": plan["plan_id"],
        "groups": groups,
        "actual_invoice_cost": None,
        "model_adoption": "hold",
        "note": "Contract validity is not semantic correctness; baseline is unchanged.",
    }
