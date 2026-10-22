"""Q6g interpretation and table arbitration; transport is injected, never built here.

Candidate generation and cumulative V1-V4 retain the frozen 2026-10-10
measurement behavior. The eval copies remain an independent regression oracle.
"""
from __future__ import annotations

import collections
import copy
import hashlib
import json
import time

from .contract import COND_ALL, make_issue
from .model import check_schema
from . import q6g_candidates as candidates
from . import rules
from . import q6g_siblings
from .q6g_validation import validate_requirements, ov

SYSTEM_SHA256 = hashlib.sha256(candidates.SYSTEM.encode("utf8")).hexdigest()
HARD_FLAGS = {"frequency_conflict", "period_outside", "condition_conflict", "blocked"}
_CACHE = []


def tables_from_rows(documents, rows):
    """Keep raw metadata iteration, names, axis order and period spelling frozen."""
    result = {}
    for tid, row in rows.items():
        meta = row["meta"]
        axes = collections.OrderedDict()
        for item in sorted([x for x in meta.get("ITM", []) or [] if isinstance(x, dict) and x.get("OBJ_ID") != "ITEM"],
                           key=lambda x: int(x.get("OBJ_ID_SN") or 99)):
            axes.setdefault(item.get("OBJ_ID"), {"name": item.get("OBJ_NM"), "values": []})["values"].append(copy.deepcopy(item))
        doc = documents.get(tid, {})
        paths = doc.get("catalog_paths", [""])
        result[tid] = {"id": tid, "name": row.get("name", doc.get("title", tid)),
                       "path": [s.strip() for s in paths[0].split(">") if s.strip()], "axes": axes,
                       "freqs": [{"se": p.get("PRD_SE"), "start": p.get("STRT_PRD_DE"), "end": p.get("END_PRD_DE")}
                                 for p in meta.get("PRD", []) or [] if isinstance(p, dict)]}
    return result


def build_request(state, catalog):
    tables = catalog.q6g_tables
    if not tables:
        raise ValueError("Q6g catalog resources unavailable")
    bm = next((index for owner, index in _CACHE if owner is tables), None)
    if bm is None:
        bm = candidates.BM25({tid: candidates.doc_text(row) for tid, row in tables.items()})
        _CACHE.append((tables, bm))
        del _CACHE[:-2]
    # The archived input used exclusion IDs (M-12). Correcting that input is a
    # separate prompt/version change; no corrected input is relabelled as replay.
    row = {"question": state["question"], "Q": {
        "period": [[c["quote"], c["kind"]] for c in state["cond_period"]],
        "freq": [[c["quote"], c["value"]] for c in state["cond_frequency"]],
        # Q4 개선으로 새로 잡히는 기준어는 모델 요청에 넣지 않는다(저장 요청·응답과 같은 입력 유지). 요구 귀속은 아래
        # _requirements가 state의 모든 조건을 원문 위치로 나누므로 그대로 된다. 모델에 줄지는 Q6G 재측정 때 정한다
        "basis": [[c["condition_id"], c["quote"], c["axis"], c["value"], c["polarity"]] for c in state["cond_basis"]
                  if not rules.q6g_hidden(c)],
        "exclude": [[c["condition_id"], c["text"]] for c in state["cond_exclude"]],
    }}
    payload, cards = candidates.build_payload(tables, bm, row)
    return payload, cards, candidates.schema_for_payload(payload)


def system_prompt():
    """The A v1 Q6G system prompt and its hash (stored responses are keyed on it)."""
    return candidates.SYSTEM, SYSTEM_SHA256


def _requirements(state, checked, raw, cards):
    kept = checked["grounded"]
    reqs = []
    all_basis = set().union(*(k["basis"] for k in kept)) if kept else set()
    def owners_of(span):
        if not span:
            return []
        # A condition may include a shared connector (e.g. '기준'). Assign it
        # by the greatest original-character coverage, not any overlap.
        covered = [sum(any(a <= pos < b for a, b in k["sp"] + k.get("extra_sp", []))
                       for pos in range(*span) if not state["question"][pos].isspace()) for k in kept]
        return [i for i, count in enumerate(covered) if count == max(covered) and count > 0]
    for n, item in enumerate(kept, 1):
        spans = [list(sp) for sp in item["sp"] + item.get("extra_sp", [])]
        own, shared = [], []
        for field in COND_ALL:
            for condition in state[field]:
                cid, span = condition["condition_id"], condition.get("span")
                owners = owners_of(span)
                if field == "cond_breakdown" and len(kept) > 1 and len(owners) == 1:
                    continue  # Separate measure names are requirements, not breakdowns.
                if field == "cond_basis" and condition["quote"].strip() in all_basis:
                    if condition["quote"].strip() in item["basis"]:
                        own.append(cid)
                    continue
                if field == "cond_basis" and owners and len(owners) < len(kept):
                    if n - 1 in owners:
                        own.append(cid)
                    continue
                if field in ("cond_period", "cond_frequency") and len(state[field]) > 1 and span:
                    # A condition before a measure belongs to its nearest following
                    # quote; trailing conditions attach to the nearest preceding one.
                    positions = [min(sp[0] for sp in k["sp"]) for k in kept]
                    following = [i for i, pos in enumerate(positions) if pos >= span[1]]
                    owner = min(following, key=lambda i: positions[i]) if following else max(range(len(kept)), key=lambda i: positions[i])
                    if owner == n - 1:
                        own.append(cid)
                    continue
                shared.append(cid)
        original = raw["requirements"][item["raw_index"]]  # the raw row itself, not the first row with the same quote
        grounding = []
        for tid in item["picks"]:
            card = next(c for c in cards if c["_table_id"] == tid)
            refs = set().union(*(set(g["value_refs"]) for g in original.get("grounding", []) if g["cand_id"] == card["cand_id"]))
            grounding.append({"table_id": tid, "item_ids": [v["_itm"] for v in card["values"] if v["ref"] in refs]})
        reqs.append({"scope_id": f"s{n}", "text": " ".join(state["question"][a:b] for a, b in spans),
                     "span": [min(a for a, b in spans), max(b for a, b in spans)], "status": "ok",
                     "condition_ids": own, "shared_condition_ids": shared, "source": "q6g",
                     "quote_spans": spans, "grounding": grounding, "measure": item["measure"],
                     "basis_quotes": sorted(item["basis"]), "calc": original.get("calc")})
    return reqs


def _raw_record(index, raw, cards):
    row = raw["requirements"][index]
    tables = {c["cand_id"]: c["_table_id"] for c in cards}
    return {"raw_index": index, "quote": row["quote"], "measure_text": row["measure_text"], "status": row["status"],
            "calc": row.get("calc"), "basis_quotes": list(row.get("basis_quotes", [])),
            "grounding": [{"table_id": tables.get(g["cand_id"]), "value_refs": list(g.get("value_refs", []))}
                          for g in row.get("grounding", [])]}


def requirement_trace(checked, raw, cards):
    """Record of every raw requirement: kept (with scope), merged into, or dropped.

    Diagnostic record only. Conditions, selection and B handoff do not read it.
    """
    trace, n = [], 0
    for item in checked["grounded"]:
        n += 1
        entry = {"scope_id": f"s{n}", **_raw_record(item["raw_index"], raw, cards), "outcome": "kept", "merged": []}
        entry["merged"] = [dict(_raw_record(m["raw_index"], raw, cards), outcome="merged") for m in item.get("merged_from", [])]
        trace.append(entry)
    accounted = {t["raw_index"] for t in trace} | {m["raw_index"] for t in trace for m in t["merged"]}
    # Reasons are linked by raw index at the moment of the drop, never by quote text.
    reasons = dict(checked["drop_trace"])
    for index, row in enumerate(raw["requirements"]):
        if index not in accounted:
            trace.append(dict(_raw_record(index, raw, cards), outcome="dropped", reason=reasons.get(index, "unaccounted")))
    return trace


def interpret(state, runtime):
    ctx = runtime.context
    rule = copy.deepcopy(state["requirements_rule"])
    empty = {"status": "off", "measures": [], "breakdowns": [], "search_terms": []}
    system, system_sha = system_prompt()
    info = {"status": "fallback", "dropped": [], "measure_texts": {}, "system_sha256": system_sha}
    out = {"requirements": rule, "concepts": empty, "interpretation": {"model": info}, "issues": [], "calls": []}
    try:
        payload, cards, schema = build_request(state, ctx.catalog)
    except ValueError:
        info["reason"] = "catalog_resources_unavailable"
        out["issues"].append(make_issue("q6g_fallback", "Q6", info["reason"]))
        return out
    info["payload_sha256"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf8")).hexdigest()
    failure = "missing_client"
    if ctx.client is not None:
        call = {"node": "Q6G", "attempt": 1, "model": ctx.client.name, "ok": False,
                "error_class": None, "tokens_in": 0, "tokens_out": 0, "ms": 0}
        start = time.perf_counter()
        transport_pending = True
        try:
            reply = ctx.client.complete("Q6G", system, payload, schema)
            transport_pending = False
            call.update(tokens_in=reply.tokens_in, tokens_out=reply.tokens_out)
            raw = json.loads(reply.text)
            errors = check_schema(raw, schema)
            if errors:
                failure = "schema"
                info["validation_errors"] = errors[:5]
            else:
                pay = {"request": {"payload": payload, "cards_meta": [{"cand_id": c["cand_id"], "table_id": c["_table_id"]} for c in cards]}}
                checked = validate_requirements({"case_id": "request", "model_status": "ok", "raw": raw}, pay)
                reqs = _requirements(state, checked, raw, cards)
                info["requirement_trace"] = requirement_trace(checked, raw, cards)
                reqs, info["sibling_fix"] = q6g_siblings.fix(state, reqs, cards, ctx.catalog.q6g_tables)
                info["dropped"] = checked["dropped"]
                if reqs:
                    out["requirements"] = reqs
                    info.update(status="ok", measure_texts={r["scope_id"]: r["measure"] for r in reqs}, note=raw["note"])
                    call["ok"] = True
                    failure = None
                else:
                    failure = "no_grounded_requirements"
        except LookupError:
            if not transport_pending:
                raise
            failure = "replay_miss"
            out["issues"].append(make_issue("replay_miss", "Q6", "Recorded request absent; no live fallback"))
        except (ValueError, TypeError):
            failure = "schema"
        except Exception as error:
            if not transport_pending:
                raise
            failure = str(getattr(error, "error_class", "client_failure"))
        call["error_class"] = failure
        call["ms"] = round((time.perf_counter() - start) * 1000, 3)
        out["calls"].append(call)
    if failure:
        info["reason"] = failure
        out["issues"].append(make_issue("q6g_fallback", "Q6", failure))
    if info["dropped"]:
        out["issues"].append(make_issue("q6g_dropped", "Q6", json.dumps(info["dropped"], ensure_ascii=False)))
    return out


def hardset(row):
    return {flag.split(":")[0] for flag in row.get("flags", [])} & HARD_FLAGS


def condition_score(row):
    """Condition evidence of a ranked row (the measure component is not a condition)."""
    parts = row.get("components", {})
    return sum(parts.get(key, 0) for key in ("basis", "items", "title_conditions", "period", "item_prefix"))


def arbitrate(rule, model, rows, *, grounded, candidate_veto=None):
    """Contract 10-4 condition arbitration, including model validation before any rule."""
    index = {r["table_id"]: r for r in rows}
    rejection = None
    if model and (not grounded or model not in index):
        rejection = "no_evidence_cited" if not grounded else "not_judged_in_scope"
        model = None
    reason, source, chosen = "no_model", "rule", rule
    if model == rule and model:
        reason, source = "same", "same"
    elif model:
        if not rule:
            chosen, source, reason = model, "model", "model"
        elif hardset(index[model]) - hardset(index[rule]):
            reason = "veto"
        elif candidate_veto:
            reason = candidate_veto
        elif condition_score(index[rule]) > condition_score(index[model]):
            reason = "condition"
        else:
            chosen, source, reason = model, "model", "model"
    loser = model if chosen == rule else rule
    return {"table_id": chosen, "pick_source": source if chosen else "none", "pick_reason": reason,
            "rejection": rejection,
            "contender": {"table_id": loser, "source": "model" if chosen == rule else "rule", "pick_reason": reason}
            if loser and loser != chosen else None}
