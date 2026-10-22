"""Prepare, inspect, or explicitly execute frozen A-role probes; default operations are offline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from claview.a_model_experiment import (
    ExperimentLedger,
    inside,
    prepare_plan,
    read,
    save_new,
    summarize,
    validate_grant,
    verify_plan,
)
from claview.a_model_providers import AProvider, ModelFault
from claview.a_model_runtime import Limits, Price, RoleRuntime

ROOT = Path(__file__).resolve().parents[1]


async def execute(plan, limits):
    """Only called after exact-plan approval validation and source verification."""
    providers = {}
    try:
        for model in dict.fromkeys(job["model"] for job in plan["jobs"]):
            variable = "CLOVASTUDIO_API_KEY" if model.startswith("HCX") else "TYPESAFE_API_KEY"
            providers[model] = AProvider(model, api_key=os.environ.get(variable))
        if not all(provider.available for provider in providers.values()):
            raise ValueError("Required provider credentials unavailable; no requests dispatched")
        ledger = ExperimentLedger(
            inside(ROOT, plan["ledger_path"]),
            plan_id=plan["plan_id"],
            mode="live",
            limits=limits,
            factor=plan["budget_reserve_factor"],
        )
        for job in plan["jobs"]:
            try:
                previous = ledger.claim(job["job_id"])
            except ModelFault:
                break
            if previous is not None:
                continue
            price = Price(**plan["prices"][job["model"]])
            runtime = RoleRuntime(
                Limits(
                    max_calls=plan["max_attempts_per_task"],
                    max_tokens=sum(job["reservation"]) * plan["max_attempts_per_task"],
                    max_seconds=plan["max_seconds_per_task"],
                    max_retries=plan["max_attempts_per_task"] - 1,
                    max_output_tokens=plan["max_output_tokens"],
                    currency=price.currency,
                    max_cost=limits["max_cost_by_currency"][price.currency],
                ),
                enabled=True,
                allow_live=True,
                attempt_ledger=ledger,
            )
            advice = await runtime.invoke(
                job["role"],
                read(inside(ROOT, job["file"]))["packet"],
                providers[job["model"]],
                price=price,
            )
            ledger.complete({"advice": advice, "runtime": runtime.summary()})
            if advice["status"] != "advice":
                break
        rows = ledger.results()
        return {
            "jobs": rows,
            "ledger": ledger.audit(),
            "summary": summarize(plan, rows, mode="live"),
        }
    finally:
        for provider in providers.values():
            await provider.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Explicit frozen probe config; historical result folders are archived",
    )
    prepare.add_argument("--pricing", type=Path, default=ROOT / "eval/configs/a_stage7_prices.json")
    prepare.add_argument("--scope", choices=("smoke", "roles"), default="smoke")
    prepare.add_argument("--repeat-smoke", action="store_true")
    prepare.add_argument("--output", type=Path, required=True)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--plan", type=Path, required=True)
    run = sub.add_parser("execute")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--grant", type=Path, required=True)
    run.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        plan = prepare_plan(
            ROOT,
            args.source,
            args.pricing,
            ledger_path=(args.output / "live-ledger.sqlite").resolve(),
            scope=args.scope,
            repeat_smoke=args.repeat_smoke,
        )
        args.output.mkdir(parents=True, exist_ok=False)
        save_new(args.output / "plan.json", plan)
        save_new(
            args.output / "grant-template.json",
            {
                "plan_id": plan["plan_id"],
                "allow_external_calls": False,
                "max_cost_by_currency": plan["limits"]["max_cost_by_currency"],
            },
        )
        save_new(args.output / "unmeasured.json", summarize(plan, [], mode="offline_preparation"))
    else:
        plan = read(args.plan)
        verify_plan(ROOT, plan)
        if args.command == "execute":
            if args.report.exists():
                raise ValueError("Report path already exists")
            limits = validate_grant(plan, read(args.grant))
            result = asyncio.run(execute(plan, limits))
            save_new(args.report, result)
            print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
            return
    print(
        json.dumps(
            {
                "status": "ready_offline",
                "jobs": len(plan["jobs"]),
                "distinct_cases": len({j["case_id"] for j in plan["jobs"]}),
                "limits": plan["limits"],
                "estimate": plan["estimate"],
                "external_calls": 0,
                "quality_status": plan["quality_status"],
                "live_blockers": [
                    "exact_plan_grant_and_positive_currency_budgets",
                    "account_access_unverified",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
