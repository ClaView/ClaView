"""Prepare/approve a fixed development batch, or replay it. Default command never calls a model.

No evaluation labels are sent to the provider. The sample file is a proposed list,
not a gold/expectation change. Live needs both a flag and the reviewed manifest hash.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from . import _guard, paths
from .agent.decomposition import CallLedger
from .agent.graph import run_outer
from .catalog.facets import catalog_version
from .catalog.layer import load_layer
from .interpret.decompose_archive import ResponseArchive, canonical, digest, now
from .interpret.decompose_payload import build_payload, is_input_limit
from .interpret.decompose_provider import LiveSource, ReplaySource, ResponseSettings, extract, versions
from .interpret.decompose_schema import build_schema
from .interpret.decompose_settle import compare_structures
from .interpret.pipeline import interpret_question
from .development_metrics import proposal_signature, repeat_report, selection_signature
from .period import package_identity

SAMPLE = paths.ROOT / "eval/cases/plan18_sample_proposal.json"
MODELS = paths.PACKAGE_DATA_DIR / "plan18_models.json"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2))


def sample_rows(sample):
    ids = [x["case_id"] for x in sample["cases"]]
    mini = read(paths.MINI_SETS)
    allowed = {x["case_id"] for x in mini["dev_mini"] if x["dataset"] == "development"}
    comparisons = sample["comparison_ids"]
    if (len(ids) != 20 or len(set(ids)) != 20 or not set(ids) <= allowed
            or len(comparisons) != 5 or len(set(comparisons)) != 5 or not set(comparisons) <= set(ids)):
        raise ValueError("sample must be 20 distinct development questions and a 5-question subset")
    corpus = {x["case_id"]: x for x in read(paths.QUESTIONS_417) if x["case_id"] in set(ids)}
    if set(corpus) != set(ids) or any(x["dataset"] != "development" for x in corpus.values()):
        raise ValueError("sample corpus mismatch")
    return [{**corpus[x["case_id"]], "focus": x["focus"]} for x in sample["cases"]]


def settings(profile, policy):
    return ResponseSettings(profile["model"], profile["effort"], policy["max_output_tokens"], policy["max_input_bytes"])


def input_of(question, layer):
    rules = interpret_question(question, layer)
    payload, _ = build_payload(rules["text"], rules["conditions"], rules["segments"], rules["mentions"], layer)
    if is_input_limit(payload):
        raise ValueError("sample exceeds decomposition input limits")
    return rules, payload, build_schema(payload)


def runtime_versions():
    """Hash A code/policy only. No secrets, old sources, B package or runtime cache."""
    root = Path(__file__).resolve().parent
    files = [p for p in sorted(root.rglob("*"))
             if p.is_file() and p.suffix in (".py", ".json", ".txt", ".md", ".csv", ".jsonl")]
    out = {p.relative_to(root).as_posix(): digest(p.read_text(encoding="utf-8")) for p in files}
    for path in (paths.AXIS_NAME_ROLES, paths.GENERIC_AXIS_ROLES, paths.COLLAPSED_AXES):
        out[path.relative_to(paths.ROOT).as_posix()] = digest(path.read_text(encoding="utf-8"))
    out["dependency/claview_period"] = digest(package_identity())
    return out


def make_tasks(inputs, sample, policy):
    tasks = []
    for profile in policy["profiles"]:
        chosen = inputs if profile["count"] == 20 else [x for x in inputs if x["case_id"] in sample["comparison_ids"]]
        if len(chosen) != profile["count"]:
            raise ValueError("profile count differs from the sample")
        for repeat in range(1, policy["repetitions"] + 1):
            for row in chosen:
                request = settings(profile, policy).request(row["payload"], row["schema"])
                size = len(canonical(request).encode("utf-8"))
                if size > policy["max_input_bytes"]:
                    raise ValueError("request exceeds input byte cap")
                tasks.append({"call_id": f'{profile["id"]}:{row["case_id"]}:repeat{repeat}',
                              "case_id": row["case_id"], "repeat": repeat, "profile": profile,
                              "request_hash": digest(request), "request_bytes": size,
                              # Conservative planning estimate, not a tokenizer count.
                              "input_token_budget_estimate": size + 1024,
                              "output_token_cap": policy["max_output_tokens"]})
    if len(tasks) != policy["max_calls"] or len({t["call_id"] for t in tasks}) != len(tasks):
        raise ValueError("batch count differs from approved plan scope")
    return tasks


def prepare(out: Path) -> dict:
    _guard.install_network_block()
    sample, policy = read(SAMPLE), read(MODELS)
    questions = sample_rows(sample)
    layer = load_layer(include_external_synonyms=False)
    inputs = []
    default_as_of = date.today().isoformat()
    for row in questions:
        rules, payload, schema = input_of(row["question"], layer)
        inputs.append({"case_id": row["case_id"], "question": row["question"],
                       "as_of": row.get("as_of") or default_as_of,
                       "as_of_source": "corpus" if row.get("as_of") else "preparation_date",
                       "focus": row["focus"], "rules": rules, "payload": payload, "schema": schema})
    tasks = make_tasks(inputs, sample, policy)
    manifest = {"version": 2, "prepared_at": now(), "sample": sample, "policy": policy, "inputs": inputs,
                "tasks": tasks, "catalog_version": catalog_version(layer), "code_versions": versions(),
                "runtime_versions": runtime_versions(),
                "purpose": "development_only_not_model_accuracy", "real_calls": 0}
    manifest["approval_hash"] = digest(manifest)
    out.mkdir(parents=True, exist_ok=False)
    write(out / "manifest.json", manifest)
    cost = sum((t["input_token_budget_estimate"] * t["profile"]["input_usd_per_million"] +
                t["output_token_cap"] * t["profile"]["output_usd_per_million"]) / 1_000_000 for t in tasks)
    summary = {"sample_questions": len(inputs), "max_calls": len(tasks), "repetitions": policy["repetitions"],
               "calls_by_profile": {p["id"]: p["count"] * policy["repetitions"] for p in policy["profiles"]},
               "approval_hash": manifest["approval_hash"],
               "request_bytes_min": min(t["request_bytes"] for t in tasks),
               "request_bytes_max": max(t["request_bytes"] for t in tasks),
               "conservative_cost_estimate_usd": cost,
               "conservative_cost_estimate_krw": cost * policy["fx_krw_per_usd_assumption"],
               "coverage_gaps": sample["coverage_gaps"], "network_attempts": len(_guard.NETWORK_ATTEMPTS)}
    write(out / "preflight.json", summary)
    lines = ["# 계획 18 호출 전 표본 확인", "", "제안 목록이며 실제 호출 승인은 아직 받지 않았다.", "",
             "| 번호 | 사례 | 질문 원문 | 확인할 꼴 | 규칙 certain |", "|---|---|---|---|---|"]
    for i, row in enumerate(inputs, 1):
        lines.append(f'| {i} | {row["case_id"]} | {row["question"]} | {row["focus"]} | {row["rules"]["judgement"]["certain"]} |')
    lines += ["", "추가 두 설정에 쓸 5문항: " + ", ".join(sample["comparison_ids"]), "",
              "현재 표본에서 빠진 꼴: " + ", ".join(sample["coverage_gaps"]), "",
              "요금과 모델: https://developers.openai.com/api/docs/models · https://developers.openai.com/api/docs/pricing",
              "", "설정별 3회 반복: luna low 60회, luna high 15회, sol low 15회. 합계 90회.",
              "반복별 요청 ID와 원장은 독립이다. 응답은 반복끼리 재사용하지 않으며 전체 예약 상한은 90회다.",
              "세 번 모두 규칙 복귀한 묶음은 일치율에서 제외하고, 혼합·미완료 묶음도 따로 표시한다.",
              "기준일은 문항의 as_of 또는 준비한 날짜로 고정한다. 기존 선택기 legacy/router 기준의 개발 자료다."]
    (out / "표본.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def check_manifest(path: Path, approval: str | None = None) -> dict:
    manifest = read(path)
    expected = digest({k: v for k, v in manifest.items() if k != "approval_hash"})
    if manifest.get("approval_hash") != expected or approval is not None and approval != expected:
        raise ValueError("reviewed manifest differs")
    if manifest.get("version") != 2 or manifest["policy"].get("repetitions") != 3:
        raise ValueError("prepare a new three-repeat manifest; old archives are preserved")
    if manifest["policy"]["max_calls"] != 90:
        raise ValueError("plan18 requires the reviewed 90-call scope")
    questions = sample_rows(manifest["sample"])
    frozen = {x["case_id"]: x for x in manifest["inputs"]}
    if any(frozen[x["case_id"]]["question"] != x["question"] for x in questions):
        raise ValueError("corpus question differs")
    if manifest["tasks"] != make_tasks(manifest["inputs"], manifest["sample"], manifest["policy"]):
        raise ValueError("manifest task IDs, repeats or generation settings differ")
    return manifest


def usage_cost(usage, profile):
    """Reported tokens at the frozen rate, not an invoice; output already includes reasoning."""
    if not isinstance(usage, dict):
        return None
    incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    if any(type(x) is not int or x < 0 for x in (incoming, outgoing, cached)) or cached > incoming:
        return None
    return ((incoming - cached) * profile["input_usd_per_million"] +
            cached * profile["cached_input_usd_per_million"] + outgoing * profile["output_usd_per_million"]) / 1_000_000


def analysis_view(analysis):
    """Compare semantic output, omitting only the intentional live/replay mode difference."""
    return {**analysis, "decomposition": {k: v for k, v in analysis["decomposition"].items()
                                           if k != "execution_mode"}}


def run_batch(manifest_path: Path, out: Path, mode: str, approved: bool, approval_hash: str | None,
              original_results: Path | None = None) -> dict:
    if mode not in ("live", "replay"):
        raise ValueError("unknown execution mode")
    if mode == "live" and (not approved or not approval_hash):
        raise ValueError("live needs explicit approval and the reviewed manifest hash")
    if mode == "replay":
        _guard.install_network_block()
    manifest = check_manifest(manifest_path, approval_hash if mode == "live" else None)
    layer = load_layer(include_external_synonyms=False)
    current_versions = runtime_versions()
    current_catalog = catalog_version(layer)
    if mode == "live" and (current_versions != manifest["runtime_versions"]
                           or current_catalog != manifest["catalog_version"]):
        raise ValueError("runtime or catalog changed; prepare and review before live")
    frozen = {x["case_id"]: x for x in manifest["inputs"]}
    current = {cid: input_of(row["question"], layer) for cid, row in frozen.items()}
    for task in manifest["tasks"]:  # check the entire generation batch before sending anything
        _, payload, schema = current[task["case_id"]]
        request = settings(task["profile"], manifest["policy"]).request(payload, schema)
        if digest(request) != task["request_hash"]:
            raise ValueError("input, prompt, schema or generation settings changed; no live call")
    out.mkdir(parents=True, exist_ok=False)
    archive = ResponseArchive(manifest_path.parent / "archive", manifest["policy"]["max_calls"])
    ledgers, rows, formats = {}, [], {}
    stop_reason = None
    selection = manifest["policy"]["selection"]
    write(out / "run.json", {"mode": mode, "runtime_versions": current_versions, "catalog_version": current_catalog,
                             "approval_hash": manifest["approval_hash"], "selection": selection})
    for task in manifest["tasks"]:
        if runtime_versions() != current_versions:
            stop_reason = "runtime_changed_during_batch"
            break
        row = frozen[task["case_id"]]
        source_args = (archive, task["call_id"], settings(task["profile"], manifest["policy"]))
        source = LiveSource(*source_args, approved=approved) if mode == "live" else ReplaySource(*source_args)
        ledger = CallLedger()  # independent results per repeat; only the archive budget is shared
        state = run_outer(row["question"], selection["variant"], layer=layer, request_id=task["call_id"],
                          question_version=1, as_of=row["as_of"], candidate_policy=selection["candidate_policy"],
                          decomposition_ledger=ledger,
                          decomposition={"mode": "gpt", "execution_mode": mode, "proposal_source": source})
        ledgers[task["call_id"]] = ledger.snapshot()
        analysis = state["analysis"]
        entry, = ledgers[task["call_id"]].values()
        primary = entry["analysis"]  # before the router can adopt an alternative
        measured = state["response"]["decomposition"]
        measured["source_measurement"] = entry.get("source_measurement", {})
        signature = None
        if primary["decomposition"]["applied_source"] == "model":
            _, payload, schema = current[task["case_id"]]
            raw = archive.read(task["call_id"], settings(task["profile"], manifest["policy"]).request(payload, schema))
            signature = proposal_signature(json.loads(extract(raw["raw_response"])[0]),
                alternative_kept=primary["decomposition"]["verify"]["alternative"] == "kept")
        cost = usage_cost(measured.get("usage"), task["profile"]) if measured["real_calls"] else 0.0
        rules = current[task["case_id"]][0]
        result = {"call_id": task["call_id"], "case_id": task["case_id"], "profile_id": task["profile"]["id"],
                  "repeat": task["repeat"], "question_version": 1, "as_of": row["as_of"],
                  "primary_analysis": primary, "analysis": analysis, "execution": measured,
                  "selections": state["selections"], "selection_route": state.get("selection_route"),
                  "response": state["response"], "structure_signature": signature,
                  "selection_signature": selection_signature(analysis, state["selections"]),
                  "cost_usd_from_reported_usage": cost,
                  "rules_structure_changed": not compare_structures(rules["interpretation"], rules["judgement"],
                                                                      primary["interpretation"], primary["judgement"])["same"]}
        if original_results is not None:
            previous = original_results / (digest(task["call_id"]) + ".json")
            result["replay_comparison"] = "missing_original"
            if previous.is_file():
                original = read(previous)
                same = (analysis_view(analysis) == analysis_view(original["analysis"])
                        and state["selections"] == original["selections"]
                        and signature == original["structure_signature"])
                result["replay_comparison"] = "same" if same else "changed"
        write(out / (digest(task["call_id"]) + ".json"), result)
        rows.append(result)
        reason = primary["decomposition"]["fallback_reason"]
        profile = task["profile"]["id"]
        formats[profile] = formats.get(profile, 0) + 1 if reason == "format" else 0
        if mode == "live" and (reason == "transport" or formats[profile] >= manifest["policy"]["consecutive_format_failure_limit"]):
            stop_reason = "transport" if reason == "transport" else "consecutive_format_failures"
            break  # no retry or silent schema/model change; semantic fallbacks do not stop the batch
    summary = {"mode": mode, "finished": len(rows), "planned": len(manifest["tasks"]),
               "real_calls": sum(r["execution"]["real_calls"] for r in rows),
               "fake_calls": sum(r["execution"]["fake_calls"] for r in rows),
               "replay_calls": sum(r["execution"]["replay_calls"] for r in rows),
               "adopted": sum(r["analysis"]["decomposition"]["applied_source"] == "model" for r in rows),
               "fallbacks": [{"call_id": r["call_id"], "reason": r["analysis"]["decomposition"]["fallback_reason"]}
                             for r in rows if r["analysis"]["decomposition"]["fallback_reason"]],
               "current_versions": versions(), "approval_hash": manifest["approval_hash"], "stop_reason": stop_reason,
               "repeat_agreement": repeat_report(manifest["tasks"], rows, manifest["policy"]["repetitions"]),
               "cost_usd_from_reported_usage": sum(r["cost_usd_from_reported_usage"] or 0 for r in rows),
               "usage_missing_calls": sum(r["execution"]["real_calls"] and r["cost_usd_from_reported_usage"] is None
                                          for r in rows),
               "cost_is_invoice": False,
               "replay_comparisons": {r["call_id"]: r["replay_comparison"] for r in rows if "replay_comparison" in r},
               "network_attempts_blocked": len(_guard.NETWORK_ATTEMPTS)}
    write(out / "summary.json", summary)
    write(out / "ledger.json", ledgers)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "live", "replay"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--approve-live", action="store_true")
    parser.add_argument("--approval-hash")
    parser.add_argument("--original-results", type=Path, help="Compare replay against this saved live result directory")
    args = parser.parse_args()
    if args.mode != "prepare" and args.manifest is None:
        parser.error("live/replay needs --manifest")
    try:
        result = prepare(args.out) if args.mode == "prepare" else run_batch(
            args.manifest, args.out, args.mode, args.approve_live, args.approval_hash, args.original_results)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        parser.exit(1, f"plan18 stopped ({type(exc).__name__}); no exception text is logged\n")


if __name__ == "__main__":
    main()
