"""Requirement-local wrappers around the base nodes (A v1).

When Q6G interpretation succeeds, each requirement (scope) runs the base node on its own view;
otherwise (Q6G fallback or model off) the base whole-question node runs unchanged.

Each J node evaluates its own declared work on each scope. No sub-question graph
is invoked, and a scope's search scores never come from another scope's queries.
"""
from __future__ import annotations

import copy
import re

from .contract import COND_ALL, BASE_NODE_READS, make_issue
from . import q6g
from .candidate_rules import arbitration_veto
from .q6g_validation import strip_p
from .catalog import nospace

# 요구 검색식에서 뺄 비교·질문 표현. 낱말 하나 전체가 맞을 때만 뺀다
_SCOPE_NOISE = re.compile(r"비교\S*|견주\S*|견줘\S*|차이|격차|얼마\S*|몇|어떻게|어땠\S*|\S+는지|싶\S*|%|[을를이가은는의에와과도로]")
SCOPE_INPUTS = {"question", "query", "query_nospace", "offsets", "requirements", "search_queries", *COND_ALL}
SCOPED_FIELDS = {"matched_items": "matched_by_scope", "fit_period": "period_by_scope",
                 "fit_basis": "basis_by_scope", "ranked": "ranked_by_scope", "cards": "cards_by_scope"}


def active(state):
    """Requirement-local path: used when the Q6G interpretation succeeded."""
    return state.get("interpretation", {}).get("model", {}).get("status") == "ok"


def view_for(state, req, node):
    from . import nodes as helpers
    fields = set(BASE_NODE_READS[node]) | SCOPE_INPUTS | {"pool", "meta"}
    view = {key: state[key] for key in fields}
    sid = req["scope_id"]
    ids = set(req["condition_ids"] + req["shared_condition_ids"])
    for field in COND_ALL:
        view[field] = [c for c in view[field] if c["condition_id"] in ids]
    view["requirements"] = [req]
    if node == "J5" and len(state["requirements"]) > 1:
        # Other requirements' quotes, so J5 can tell a word they own from a word no quote covers.
        view["requirements"] = [dict(req, peer_quote_spans=[sp for r in state["requirements"]
                                                            if r["scope_id"] != req["scope_id"]
                                                            for sp in r["quote_spans"]])]
    view["pool"] = state["pool_by_scope"][sid]
    tids = {row["table_id"] for row in view["pool"]}
    view["meta"] = {tid: m for tid, m in view["meta"].items() if tid in tids}
    if len(state["requirements"]) > 1:
        # Keep original positions and offsets. Only the scope's verified words
        # and applicable conditions participate in lexical judging.
        unsure = helpers.ambiguous_conditions(state)
        spans = req["quote_spans"] + [c["span"] for field in COND_ALL for c in view[field]
                                      if c.get("span") and c["condition_id"] not in unsure]
        view["query"] = "".join(ch if any(a <= pos < b for a, b in spans) else " "
                                for ch, pos in zip(view["query"], view["offsets"]))
        view["query_nospace"] = nospace(view["query"])
        view["search_queries"] = [q for q in view["search_queries"] if q["scope_id"] == sid]
    for field, scoped in SCOPED_FIELDS.items():
        if field in BASE_NODE_READS[node]:
            view[field] = state[scoped][sid]
    if "evidence" in view:
        view["evidence"] = {eid: ev for eid, ev in view["evidence"].items() if eid.startswith(sid + "|")}
    if "relations" in view:
        view["relations"] = [r for r in view["relations"] if r.get("scope_id", sid) == sid]
    return view


def namespace(out, sid):
    out = copy.deepcopy(out)
    names = {eid: sid + "|" + eid for eid in out.get("evidence", {})}
    def visit(value):
        if isinstance(value, str):
            return names.get(value, value)
        if isinstance(value, list):
            return [visit(v) for v in value]
        if isinstance(value, dict):
            return {names.get(k, k): visit(v) for k, v in value.items()}
        return value
    return visit(out)


def adapt(base):
    from . import nodes as helpers
    wrapped = dict(base)

    def q6_node(state, runtime):
        cfg = runtime.context.config
        if cfg.model == "on":
            return q6g.interpret(state, runtime)
        rules = copy.deepcopy(state["requirements_rule"])
        old_view = {key: state[key] for key in BASE_NODE_READS["Q6"] if key != "requirements"}
        old_view["requirements"] = rules
        interpretation = {"model": {"status": "off", "dropped": [], "measure_texts": {}}}
        out = base["Q6"](old_view, runtime)
        return {**out, "requirements": rules, "interpretation": interpretation}
    wrapped["Q6"] = q6_node

    def q7_node(state, runtime):
        if not active(state):
            return base["Q7"](state, runtime)
        view = {key: state[key] for key in BASE_NODE_READS["Q7"]}
        forbidden = [c["span"] for c in state["cond_exclude"] + state["cond_basis"]
                     if c.get("span") and (c in state["cond_exclude"] or c.get("polarity") == "neg")]
        view["query"] = "".join(ch if not any(a <= pos < b for a, b in forbidden) else " "
                                for ch, pos in zip(view["query"], view["offsets"]))
        out = base["Q7"](view, runtime)
        if len(state["requirements"]) > 1:
            unsure = helpers.ambiguous_conditions(state)  # 애매 조건은 요구별 검색식에 확정 조건처럼 넣지 않는다
            names = helpers._catalog_names(runtime.context.catalog)

            def clean(word):
                # 카탈로그 이름은 조사처럼 보이는 끝 글자를 떼지 않는다 ("생산자물가"가 "생산자물"이 되지 않게).
                # 이/가 끝 낱말은 표명 안에 그대로 있는 말도 이름으로 본다(Q5 measure_scopes와 같은 근거).
                bare = word.strip(".,!?")
                keep = names.known(bare) or (bare[-1:] in ("이", "가") and names.in_titles(bare))
                if keep:
                    return bare
                cut = strip_p(word)
                return bare if len(cut) < 2 else cut  # 한 글자만 남기는 조사 떼기는 하지 않는다 ("나이"→"나")

            def noise(word):
                # 비교·질문 표현과 괄호 뒤에 홀로 남은 조사("(동향)를"의 "를")는 표 이름에 없는 말이라 요구 검색식에서 뺀다.
                # 카탈로그 이름이면 남긴다
                return not names.known(word) and bool(_SCOPE_NOISE.fullmatch(word))
            for req in state["requirements"]:
                spans = req["quote_spans"] + [c["span"] for c in state["cond_basis"]
                    if c["condition_id"] in req["condition_ids"] + req["shared_condition_ids"] and c.get("span")
                    and c["condition_id"] not in unsure]
                words = []
                for a, b in sorted(spans):
                    if any(q6g.ov([a, b], cut) for cut in forbidden):
                        continue
                    # 괄호도 낱말 경계로 본다("증감(원계열"과 기준어 "원계열"이 따로 되풀이되지 않게)
                    piece = re.sub(r"[()]", " ", state["question"][a:b])
                    words.extend(clean(word) for word in piece.split())
                words = [word for word in words if not noise(word)]
                text = " ".join(dict.fromkeys(word for word in words if word))
                out["search_queries"].append({"query_id": "scope:" + req["scope_id"], "scope_id": req["scope_id"],
                                               "source": "scope", "text": text, "dropped": []})
        return out
    wrapped["Q7"] = q7_node

    def s5_node(state, runtime):
        if not active(state):
            return {**base["S5"](state, runtime), "pool_base_by_scope": {}}
        def fuse(qids):
            channels = {key: helpers._channel({qid: rows for qid, rows in state[key]["per_query"].items() if qid in qids})
                        for name, key in helpers.CHANNELS}
            result = base["S5"](channels, runtime)
            for row in result["pool_base"]:
                row["channel_raw_scores"] = {name: max((r["raw_score"] for rs in channels[key]["per_query"].values()
                    for r in rs if r["table_id"] == row["table_id"]), default=0.0) for name, key in helpers.CHANNELS}
            return result
        queries = state["search_queries"]
        common = fuse({q["query_id"] for q in queries if q["scope_id"] is None})
        pools, logs = {}, {}
        union = {row["table_id"]: row for row in common["pool_base"]}
        issues = list(common.get("issues", []))
        for req in state["requirements"]:
            sid = req["scope_id"]
            result = common if len(state["requirements"]) == 1 else fuse({q["query_id"] for q in queries if q["scope_id"] == sid})
            pools[sid], logs[sid] = result["pool_base"], result["fusion_log"]
            for row in pools[sid]:
                union.setdefault(row["table_id"], row)
            if result is not common:
                issues.extend(result.get("issues", []))
        log = common["fusion_log"] | {"by_scope": logs, "pool_by_scope": copy.deepcopy(pools)}
        return {"pool_base": list(union.values()), "pool_base_by_scope": pools, "fusion_log": log, "issues": issues}
    wrapped["S5"] = s5_node

    def s6_node(state, runtime):
        if not active(state):
            return {**base["S6"](state, runtime), "pool_by_scope": {}}
        result = base["S6"](state, runtime)
        union = {row["table_id"]: row for row in result["pool"]}
        scopes = {}
        for req in state["requirements"]:
            sid = req["scope_id"]
            pool = base["S6"]({"pool_base": state["pool_base_by_scope"][sid]}, runtime)["pool"]
            # The requirement-local path exists only for a successful Q6G interpretation,
            # so every Q6G grounding table of the requirement enters its pool.
            hints = list(dict.fromkeys(g["table_id"] for g in req["grounding"]))
            base_ids = [p["table_id"] for p in pool]
            for hint in hints:
                if any(p["table_id"] == hint for p in pool):
                    continue
                pool.append({"table_id": hint, "fused_score": None, "channels": {}, "entered_by": "model_grounded", "rank": len(pool) + 1})
                result["relations"].append({"table_id": hint, "anchor_ids": [], "relation_type": "model_grounded",
                    "rule_version": "q6g", "parent_mode": "scope", "scope_id": sid, "base_pool": base_ids})
            scopes[sid] = pool
            for row in pool:
                union.setdefault(row["table_id"], row)
        result["pool"] = [dict(row, rank=n) for n, row in enumerate(union.values(), 1)]
        result["pool_by_scope"] = scopes
        return result
    wrapped["S6"] = s6_node

    def s7_node(state, runtime):
        out = base["S7"](state, runtime)
        if active(state):
            out["search_log"]["pool_by_scope"] = copy.deepcopy(state["pool_by_scope"])
        return out
    wrapped["S7"] = s7_node

    def judge_node(name, output, scoped):
        def run(state, runtime):
            if not active(state):
                return {**base[name](state, runtime), scoped: {}}
            by_scope, evidence = {}, {}
            merged = [] if output in ("ranked", "cards") else {}
            for req in state["requirements"]:
                sid = req["scope_id"]
                view = view_for(state, req, name)
                out = namespace(base[name](view, runtime), sid)
                if name == "J5":
                    base_ids = {r["table_id"] for r in state["pool_base_by_scope"][sid]}
                    base_rows = [r for r in out[output] if r["table_id"] in base_ids]
                    base_order = {r["table_id"]: i for i, r in enumerate(base_rows, 1)}
                    for row in out[output]:
                        row["base_rank"] = base_order.get(row["table_id"])
                by_scope[sid] = out[output]
                evidence.update(out.get("evidence", {}))
                if isinstance(merged, list):
                    known = {r["table_id"] for r in merged}
                    merged.extend(row for row in out[output] if row["table_id"] not in known)
                else:
                    for tid, value in out[output].items():
                        if isinstance(value, list):
                            merged.setdefault(tid, []).extend(value)
                        else:
                            merged.setdefault(tid, {}).update(value)
            return {output: merged, scoped: by_scope, **({"evidence": evidence} if evidence else {})}
        return run
    for name, output in (("J2", "matched_items"), ("J3", "fit_period"), ("J4", "fit_basis"), ("J5", "ranked")):
        wrapped[name] = judge_node(name, output, SCOPED_FIELDS[output])

    def j7_node(state, runtime):
        if not active(state):
            return {**base["J7"](state, runtime), "cards_by_scope": {}}
        cards, by_scope = [], {}
        for req in state["requirements"]:
            sid = req["scope_id"]
            view = view_for(state, req, "J7")
            chosen = list(view["ranked"][:helpers.CANDIDATE_CARDS])
            hint = req["grounding"][0]["table_id"] if req["grounding"] else None
            if hint and hint not in {row["table_id"] for row in chosen}:
                chosen.extend(row for row in view["ranked"] if row["table_id"] == hint)
            view.update(ranked=chosen)
            own = helpers.card_rows(view, len(chosen))
            for row in own:
                row["card_id"] = sid + "|" + row["card_id"]
                row["scope_id"] = sid
                row["card_role"] = "model" if row["table_id"] == hint else "ranked"
            by_scope[sid] = own
            cards.extend(own)
        return {"cards": cards, "cards_by_scope": by_scope, "k": helpers.CANDIDATE_CARDS}
    wrapped["J7"] = j7_node

    def t1_node(state, runtime):
        if not active(state):
            return base["T1"](state, runtime)
        picks = {}
        for req in state["requirements"]:
            sid = req["scope_id"]
            ranked = state["ranked_by_scope"][sid]
            # J5 records the pre-injection ranking, including optional D3 policy.
            tid = next((row["table_id"] for row in ranked if row["base_rank"] == 1), None)
            picks[sid] = {"table_id": tid, "pick_rule_base": tid}
        first = next(iter(picks.values()), {"table_id": None})["table_id"]
        return {"pick_rule": {"table_id": first, "by_scope": picks}}
    wrapped["T1"] = t1_node

    def t2_node(state, runtime):
        if not active(state):
            return base["T2"](state, runtime)
        picks = {r["scope_id"]: {"table_id": r["grounding"][0]["table_id"]}
                 for r in state["requirements"] if r["grounding"]}
        return {"pick_model": {"status": "ok" if picks else "none", "source": "q6g", "picks": picks}}
    wrapped["T2"] = t2_node

    def t3_node(state, runtime):
        if not active(state):
            return base["T3"](state, runtime)
        finals, matrix, issues = {}, {}, []
        for req in state["requirements"]:
            sid = req["scope_id"]
            view = view_for(state, req, "T3")
            rule = state["pick_rule"]["by_scope"][sid]["table_id"]
            model = state["pick_model"]["picks"].get(sid, {}).get("table_id")
            veto = arbitration_veto(rule, model, state["ranked_by_scope"][sid], view,
                                    runtime.context.catalog.arbitration_properties)
            decision = q6g.arbitrate(rule, model, state["ranked_by_scope"][sid],
                                     grounded=bool(req["grounding"] and req["quote_spans"]), candidate_veto=veto)
            tid = decision["table_id"]
            final = {"table_id": tid, "source": "model" if decision["pick_source"] == "model" else "rule" if tid else "none",
                     "model_review": "accepted" if tid and tid == model else "not_used", "reason_codes": [], **decision}
            if decision["rejection"] == "no_evidence_cited":
                issues.append(make_issue("no_evidence_cited", "T3", scope_id=sid))
            if decision["pick_reason"] in ("veto", "condition", "unrequested_variant"):
                issues.append(make_issue("model_table_overridden", "T3", decision["pick_reason"], scope_id=sid))
            unsure = helpers.ambiguous_conditions(state)  # Q6G 요구면 비어 있다
            selected = {"table_id": tid, **helpers._judge(view["fit_period"].get(tid, {}).get(sid),
                        view["fit_basis"].get(tid, {}), view["matched_items"].get(tid, []), req, unsure)}
            for code in ("frequency_conflict", "period_outside"):
                if code in selected["reason_codes"]:
                    issues.append(make_issue(code + "_on_final", "T3", scope_id=sid))
                    final["reason_codes"].append(code + "_on_final")
            matrix[sid] = {"selected": selected, "alternatives": helpers._alternatives(view, tid, req, selected, unsure)
                           if tid else {"status": "none", "by_tables": [], "gaps": []}, "contender": decision["contender"]}
            # 요구별 화면(view)만 넘긴다. 필요한 조건·메타·분류 매치·질문 위치가 다 들어 있고, State 전체를 펼치면 T3 입력 계약 밖을 읽는다
            helpers._cross_axis_judge(view, req, selected)
            finals[sid] = final
        first = next(iter(finals.values()))
        return {"final": {**first, "by_scope": finals}, "requirement_matrix": matrix, "issues": issues}
    wrapped["T3"] = t3_node

    def selection_view(state, req, name):
        view = view_for(state, req, name)
        sid = req["scope_id"]
        view["final"] = state["final"]["by_scope"][sid]
        view["requirement_matrix"] = {sid: state["requirement_matrix"][sid]}
        if "pick_rule" in view:
            view["pick_rule"] = state["pick_rule"]["by_scope"][sid]
        if "pick_model" in view:
            tid = state["pick_model"]["picks"].get(sid, {}).get("table_id")
            view["pick_model"] = {"status": "ok" if tid else "off", "table_id": tid}
        view["issues"] = [i for i in state["issues"] if i.get("scope_id", sid) == sid]
        return view

    def t4_node(state, runtime):
        if not active(state):
            return {**base["T4"](state, runtime), "confidence_by_scope": {}}
        levels = {req["scope_id"]: base["T4"](selection_view(state, req, "T4"), runtime)["confidence"] for req in state["requirements"]}
        return {"confidence": min(levels.values(), key=("low", "medium", "high").index), "confidence_by_scope": levels}
    wrapped["T4"] = t4_node

    def t5_node(state, runtime):
        if not active(state) or not state["pool"]:
            return copy.deepcopy(base["T5"](state, runtime))
        results, handoffs, issues = [], [], []
        for req in state["requirements"]:
            sid = req["scope_id"]
            view = selection_view(state, req, "T5")
            view["confidence"] = state["confidence_by_scope"][sid]
            out = base["T5"](view, runtime)
            choice = state["final"]["by_scope"][sid]
            # Selection is allowed outside the top-k cards. Evidence comes from
            # the judged scope/table, independently of the display card limit.
            out["a_result"]["evidence"] = [dict(ev, evidence_id=eid) for eid, ev in view["evidence"].items()
                                           if ev["table_id"] == choice["table_id"]]
            for table in out["a_result"]["tables"]:
                table.update(pick_source=choice["pick_source"], pick_reason=choice["pick_reason"], scope_ids=[sid])
            for requirement in out["a_result"]["requirements"]:
                requirement.update(pick_source=choice["pick_source"], pick_reason=choice["pick_reason"], contender=choice["contender"])
            results.append(out["a_result"])
            handoffs.append(out["b_handoff"])
            issues.extend(out.get("issues", []))
        result, handoff = copy.deepcopy(results[0]), copy.deepcopy(handoffs[0])
        tables = {}
        for part in results:
            for table in part["tables"]:
                if table["table_id"] in tables:
                    tables[table["table_id"]]["scope_ids"].extend(table["scope_ids"])
                else:
                    tables[table["table_id"]] = copy.deepcopy(table)
        result.update(tables=list(tables.values()), confidence=state["confidence"],
                      requirements=[r for part in results for r in part["requirements"]],
                      conditions={**{f: copy.deepcopy(state[f]) for f in COND_ALL}, "requirements": copy.deepcopy(state["requirements"])},
                      matched_items=[m for part in results for m in part["matched_items"]],
                      evidence=list({ev["evidence_id"]: ev for part in results for ev in part["evidence"]}.values()),
                      coverage={sid: row["selected"]["covered"] for sid, row in state["requirement_matrix"].items()},
                      issue_codes=list(dict.fromkeys([i["code"] for i in state["issues"] + issues])))
        handoff.update(tables=copy.deepcopy(result["tables"]), scopes=[s for h in handoffs for s in h["scopes"]],
                       matched_items=[m for h in handoffs for m in h["matched_items"]],
                       item_itm_id=handoff["item_itm_id"] if len(tables) == 1 else None)
        return copy.deepcopy({"a_result": result, "b_handoff": handoff, "issues": issues})
    wrapped["T5"] = t5_node
    return wrapped
