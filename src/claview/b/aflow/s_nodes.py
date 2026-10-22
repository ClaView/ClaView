"""S stage nodes (S1–S3, S5–S7): search channels, fusion, pool injection and the search log.

Moved verbatim from nodes.py (node-structure N5); nodes.py assembles NODES."""

from __future__ import annotations

import copy
import math

from .catalog import grams_v1, norm_v1
from .contract import FUSION_METHOD, FUSION_PARAMS, RULE_VERSIONS, make_issue
from .node_common import Ctx, _channel, _rank_rows


CHANNELS = (("title", "hits_title"), ("item", "hits_item"), ("model", "hits_model"))
POOL_CAP = 30


def _overlap(q: frozenset, doc: frozenset, idf: dict) -> float:
    """질문 2-gram과 문서 2-gram의 idf 가중 겹침을 문서 길이(√)로 나눈다."""
    if not doc:
        return 0.0
    return sum(idf.get(g, 0.0) for g in q & doc) / math.sqrt(len(doc))


PATH_WEIGHT = 0.3  # 경로는 표명보다 약한 근거다


def s1(state, runtime: Ctx) -> dict:
    """표명·경로 채널: 문자 2-gram idf 겹침 (단계 1)."""
    ctx = runtime.context
    lex = ctx.catalog.lexicon[RULE_VERSIONS["normalize"]]
    idf = lex["idf"]["title"]
    per_query = {}
    for q in state["search_queries"]:
        qg = grams_v1(norm_v1(q["text"]))
        scores = {
            t: _overlap(qg, lex["title"][t], idf) + PATH_WEIGHT * _overlap(qg, lex["path"][t], idf)
            for t in lex["title"]
        }
        per_query[q["query_id"]] = _rank_rows(scores)
    return {"hits_title": _channel(per_query)}


def s2(state, runtime: Ctx) -> dict:
    """분류값 채널: 표마다 가장 잘 맞는 분류값 이름 하나의 2-gram idf 겹침 (단계 1, ITEM 행 제외)."""
    ctx = runtime.context
    lex = ctx.catalog.lexicon[RULE_VERSIONS["normalize"]]
    idf = lex["idf"]["item"]
    ctx.catalog.item_index  # df는 J2가 쓴다
    per_query = {}
    for q in state["search_queries"]:
        qg = grams_v1(norm_v1(q["text"]))
        scores = {}
        for t, rows in lex["items"].items():
            best = max((_overlap(qg, g, idf) for _, g in rows), default=0.0)
            if best > 0:
                scores[t] = best
        per_query[q["query_id"]] = _rank_rows(scores)
    return {"hits_item": _channel(per_query)}


def s3(state, runtime: Ctx) -> dict:
    """모델 검색어 채널. 검색어가 없으면 기본형 (v1.8 S3)."""
    terms = state["concepts"]["search_terms"]
    if not terms:
        return {"hits_model": {"per_query": {}, "ranked_full": [], "merged": []}}
    sub = {"search_queries": [{"query_id": f"m{i + 1}", "text": t["term"]} for i, t in enumerate(terms)]}
    title = s1(sub, runtime)["hits_title"]["per_query"]
    item = s2(sub, runtime)["hits_item"]["per_query"]
    per_query = {}
    for qid in title:
        scores = {r["table_id"]: r["raw_score"] for r in title[qid]}
        for r in item[qid]:
            scores[r["table_id"]] = scores.get(r["table_id"], 0.0) + r["raw_score"]
        per_query[qid] = _rank_rows(scores)
    return {"hits_model": _channel(per_query)}


def s5(state, runtime: Ctx) -> dict:
    """Score fusion of the search channels: weighted sum of max-normalised channel scores."""
    params = FUSION_PARAMS
    weights = params["weights"]
    fused: dict[str, float] = {}
    channel_rank: dict[str, dict] = {}
    for name, key in CHANNELS:
        rows = state[key][params["channel_input"]]  # E3: merged 또는 ranked_full
        if not rows:
            continue
        top = max(r["score"] for r in rows) or 1.0
        w = weights.get(name, 1.0)
        for r in rows:
            tid = r["table_id"]
            part = w * r["score"] / top
            fused[tid] = fused.get(tid, 0.0) + part
            channel_rank.setdefault(tid, {})[name] = r["rank"]
    order = sorted(fused, key=lambda t: (-round(fused[t], 12), min(channel_rank[t].values()), t))
    ties, issues = [], []
    by_score: dict[float, list] = {}
    for t in order:
        by_score.setdefault(round(fused[t], 12), []).append(t)
    kept = set(order[:POOL_CAP])
    for group in by_score.values():
        if len(group) > 1:
            ties.append({"table_ids": group, "broken_by": "best_channel_rank,table_id"})
            # 동률은 모두 fusion_log에 남기고, 절단 경계를 가를 때만 issue로 올린다 (v1.9 E4)
            if 0 < len(kept.intersection(group)) < len(group):
                issues.append(make_issue("ties_broken", "S5", f"풀 절단({POOL_CAP}) 경계의 동률 {len(group)}개"))
    pool_base = [
        {"table_id": t, "fused_score": fused[t], "channels": channel_rank[t]} for t in order[:POOL_CAP]
    ]
    fusion_log = {
        "method": FUSION_METHOD,
        "params": copy.deepcopy(params),
        "normalize": "max",
        "ties": ties,
        "cut": {"pre": order, "post": [p["table_id"] for p in pool_base]},
    }
    out = {"pool_base": pool_base, "fusion_log": fusion_log}
    if issues:
        out["issues"] = issues
    return out


def s6(state, runtime: Ctx) -> dict:
    """pool_base를 entered_by=search로 옮긴다 (v1.8 S6). 요구별 모델 근거 표는 scoped S6가 더한다."""
    pool = [
        {"table_id": p["table_id"], "fused_score": p["fused_score"], "channels": dict(p["channels"]), "entered_by": "search"}
        for p in state["pool_base"]
    ]
    for i, p in enumerate(pool):
        p["rank"] = i + 1
    return {"pool": pool, "relations": []}


def s7(state, runtime: Ctx) -> dict:
    """정답 없이 검색 기록만 남긴다. 정답 위치는 평가 실행기가 계산한다."""
    run_meta = state["run_meta"]
    per_channel = {}
    for name, key in CHANNELS:
        ch = state[key]
        per_channel[name] = {
            "pre_cut": [{"table_id": r["table_id"], "rank": r["rank"]} for r in ch["ranked_full"]],
            "post_cut": [{"table_id": r["table_id"], "rank": r["rank"]} for r in ch["merged"]],
        }
    pool = state["pool"]
    state["relations"]
    return {
        "search_log": {
            "versions": {
                "catalog": run_meta["catalog_snapshot"],
                "meta": run_meta["meta_snapshot"],
                "rule_versions": copy.deepcopy(RULE_VERSIONS),
                "df_scope": RULE_VERSIONS["normalize"],
            },
            "per_channel": per_channel,
            "fusion": copy.deepcopy(state["fusion_log"]),
            "pool": [{"table_id": p["table_id"], "rank": p["rank"], "entered_by": p["entered_by"]} for p in pool],
            "sibling_added": sum(p["entered_by"] == "sibling" for p in pool),
            "pool_size": len(pool),
        }
    }


def route_after_s7(state) -> str:
    """조건부 간선은 이것 하나 (v1.8 3절)."""
    return "T5" if not state["pool"] else "J1"
