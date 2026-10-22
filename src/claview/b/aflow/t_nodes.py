"""T stage nodes (T1–T5): rule/model picks, arbitration, confidence and the A/B outputs.

Moved verbatim from nodes.py (node-structure N5); nodes.py assembles NODES."""

from __future__ import annotations

from . import rules
from .contract import COND_ALL, make_issue
from .j_nodes import _judge, cross_axis_support
from .node_common import Ctx
from .q_nodes import ambiguous_conditions


def t1(state, runtime: Ctx) -> dict:
    ranked = state["ranked"]
    return {"pick_rule": {"table_id": ranked[0]["table_id"] if ranked else None}}


def t2(state, runtime: Ctx) -> dict:
    """Legacy model-pick slot on the unscoped path. A v1 takes the model pick from Q6G grounding
    (scoped T2); the old card-level model call is not part of A v1."""
    return {"pick_model": {"status": "off", "table_id": None, "self_confidence": None, "model_note": None,
                           "used_evidence_ids": []}}


def _months_to_stamp(m: int) -> str:
    return f"{m // 12}.{m % 12 + 1:02d}"


def _alternatives(state, final_id: str, req: dict, selected: dict, ambiguous: frozenset = frozenset()) -> dict:
    """다른 카드 표로 요구를 채울 수 있는지 (docs/01 4절 T3 ③). status는 대안 표 하나로 met·partial인지이며,
    고른 표와 대안 표의 같은 주기 수록 구간 합집합이 요청 기간을 덮으면 met, 남는 구간은 gaps다."""
    if selected["status"] == "met":
        return {"status": "none", "by_tables": [], "gaps": []}
    sid = req["scope_id"]
    verdicts = {}
    for c in state["cards"]:
        tid = c["table_id"]
        if tid == final_id:
            continue
        verdicts[tid] = _judge(state["fit_period"].get(tid, {}).get(sid), state["fit_basis"].get(tid, {}),
                               state["matched_items"].get(tid, []), req, ambiguous)
    if not verdicts:
        return {"status": "none", "by_tables": [], "gaps": []}
    by_tables = [{"table_id": tid, "freq": cv["freq"], "covered": cv["coverage"]}
                 for tid, v in verdicts.items() if v["status"] in ("met", "partial") for cv in v["covered"]]
    status = "met" if any(v["status"] == "met" for v in verdicts.values()) else (
        "partial" if any(v["status"] == "partial" for v in verdicts.values()) else "unmet")
    gaps = []
    # 합집합은 기간 말고는 다 맞는 표만 쓴다. 기준·주기·단위가 어긋난 표의 기간으로 met을 만들지 않는다 (Codex F02)
    period_only = {"period_partial", "period_outside"}
    union_ok = {tid for tid, v in verdicts.items() if set(v["reason_codes"]) <= period_only}
    fp = state["fit_period"].get(final_id, {}).get(sid)
    period = fp["requested"]["period"] if fp else None
    open_end = bool(period) and period["end"] is None and period["kind"] == "since"  # 끝을 모르면 합집합으로 met을 만들지 않는다
    want = ((rules.stamp_months(period["start"]), rules.stamp_months(period["end"] or period["start"]))
            if period and not open_end else (None, None))
    if status != "met" and all(want):
        lo, hi = want[0][0], want[1][1]
        by_freq = {}
        own = ([{"freq": cv["freq"], "covered": cv["coverage"]} for cv in selected["covered"]]
               if set(selected["reason_codes"]) <= period_only else [])
        for b in own + [b for b in by_tables if b["table_id"] in union_ok]:  # 기간만 모자란 표들의 같은 주기 합집합
            a, z = rules.stamp_months(b["covered"][0]), rules.stamp_months(b["covered"][1])
            if a and z:
                by_freq.setdefault(b["freq"], []).append((a[0], z[1]))
        best = None
        for f, spans in by_freq.items():
            holes, cur = [], lo
            for a, z in sorted(spans):
                if a > cur:
                    holes.append((cur, min(a - 1, hi)))
                cur = max(cur, z + 1)
                if cur > hi:
                    break
            if cur <= hi:
                holes.append((cur, hi))
            holes = [h for h in holes if h[0] <= h[1]]
            if best is None or sum(z - a for a, z in holes) < sum(z - a for a, z in best[1]):
                best = (f, holes)
        if best is not None:
            if not best[1]:
                status = "met"
            gaps = [{"freq": best[0], "missing": [_months_to_stamp(a), _months_to_stamp(z)]} for a, z in best[1]]
    return {"status": status, "by_tables": by_tables, "gaps": gaps}


def _cross_axis_judge(state, req: dict, selected: dict) -> None:
    """T3: 표의 제공 형태(J 교차 분류 판정 cross_axis_support)와 요구의 교차 의도를 나눠 기록한다.

    교차 의도는 요구의 cross_intent("cross"·"each")로만 받는다. 지금 Q는 이 값을 내지 않으므로 모두 "unconfirmed"다.
    분류 조건이 둘이라는 것만으로 교차 요청으로 보지 않는다.
    - 의도 cross + single_axis: 확인된 요청을 표가 지원하지 못한다 → partial(cross_axis_unsupported).
    - 그 밖(의도 each·unconfirmed, supported·unknown): 판정을 바꾸지 않고 기록만 한다.
    supported도 축 위치로 본 것이라 실제 조합 제공은 확인하지 않았다."""
    tid = selected["table_id"]
    if not tid or tid not in state["meta"]:
        return
    verdict = cross_axis_support(state, req, tid)
    if verdict is None:
        return
    intent = req.get("cross_intent") if req.get("cross_intent") in ("cross", "each") else "unconfirmed"
    # support_basis: supported·single_axis는 축 구조로 본 것이다. 값이 실제로 있는지(모든 조합)는 확인하지 않았다
    selected["cross_axis"] = {**verdict, "intent": intent, "support_basis": "axis_structure"}
    if intent == "cross" and verdict["status"] == "single_axis":
        if selected["status"] in ("met", "unknown"):
            selected["status"] = "partial"
        if "cross_axis_unsupported" not in selected["reason_codes"]:
            selected["reason_codes"].append("cross_axis_unsupported")
        selected["gaps"].append({"code": "cross_axis_unsupported", "condition_ids": verdict["condition_ids"]})


def _cross_hold(selected: list) -> bool:
    """교차 의도 미확인 요구에서 고른 표가 같은 축 나열이면 high를 보류한다."""
    return any((s.get("cross_axis") or {}).get("status") == "single_axis"
               and s["cross_axis"].get("intent") == "unconfirmed" for s in selected)


def t3(state, runtime: Ctx) -> dict:
    """최종 선택과 요구 판정 (단계 4, docs/01 4절 T3 ①~③)."""
    rule, model, cards = state["pick_rule"], state["pick_model"], state["cards"]
    state["cond_period"], state["cond_frequency"], state["relations"]
    by_table = {c["table_id"]: c for c in cards}
    issues = []
    final = {"table_id": rule["table_id"], "source": "rule" if rule["table_id"] else "none",
             "model_review": "not_used", "reason_codes": []}
    if model["status"] == "ok":  # ① 모델 선택 검사
        card = by_table.get(model["table_id"])
        card_ids = {r["evidence_id"] for r in card["reasons"]} if card else set()
        used = set(model["used_evidence_ids"])
        stances = {r["evidence_id"]: r.get("stance") for r in card["reasons"]} if card else {}
        only_against = bool(used) and all(stances.get(e) == "against" for e in used)  # 근거가 있는 것과 선택을 지지하는 것은 다르다 (Codex F07)
        if card and used <= card_ids and not only_against:
            final = {"table_id": model["table_id"], "source": "model", "model_review": "accepted", "reason_codes": []}
        else:
            final["model_review"] = "rejected"
            issues.append(make_issue("model_rejected", "T3", "인용 근거가 모두 반대 근거" if card and used <= card_ids
                                     else "후보 밖이거나 인용 근거가 그 카드에 없음"))

    def hard(tid):  # 주기 conflict·기간 outside가 걸린 요구가 있나
        found = set()
        for sid, fp in state["fit_period"].get(tid, {}).items():
            v = _judge(fp, {}, [], {"condition_ids": [], "shared_condition_ids": []})
            found |= {c for c in v["reason_codes"] if c in ("frequency_conflict", "period_outside")}
        return found

    unsure = ambiguous_conditions(state)  # 귀속이 애매한 조건으로는 모델 선택을 뒤집지 않는다 (7번 1단계)

    def basis_beaten(tid, other):  # tid가 기준어 select 충돌인 요구를 other는 막히지 않고 맞추나 (Codex F07)
        for req in state["requirements"]:
            args = (state["fit_basis"].get(tid, {}), state["matched_items"].get(tid, []), req, unsure)
            v = _judge(state["fit_period"].get(tid, {}).get(req["scope_id"]), *args)
            if "condition_conflict" in v["reason_codes"]:
                w = _judge(state["fit_period"].get(other, {}).get(req["scope_id"]), state["fit_basis"].get(other, {}),
                           state["matched_items"].get(other, []), req, unsure)
                if w["status"] != "unmet":
                    return True
        return False

    matrix = {}
    if final["table_id"]:
        if final["source"] == "model" and rule["table_id"] and final["table_id"] != rule["table_id"]:
            why = None
            if hard(final["table_id"]) and not hard(rule["table_id"]):
                why = "모델 선택에 주기·기간 불일치"
            elif basis_beaten(final["table_id"], rule["table_id"]):
                why = "모델 선택에 기준어 충돌, 규칙 1위는 그 요구를 맞춤"
            if why:
                final = {"table_id": rule["table_id"], "source": "rule", "model_review": "rejected",
                         "reason_codes": ["final_replaced_by_rule"]}  # ② 규칙 1위로 바꾼다
                issues.append(make_issue("final_replaced_by_rule", "T3", why))
        for code in sorted(hard(final["table_id"])):
            issues.append(make_issue(f"{code}_on_final", "T3", f"고른 표 {final['table_id']}"))
            final["reason_codes"].append(f"{code}_on_final")
        tid = final["table_id"]
        for req in state["requirements"]:  # ③ selected는 고른 표만으로
            v = _judge(state["fit_period"].get(tid, {}).get(req["scope_id"]), state["fit_basis"].get(tid, {}),
                       state["matched_items"].get(tid, []), req, unsure)
            selected = {"table_id": tid, **v}
            matrix[req["scope_id"]] = {"selected": selected, "alternatives": _alternatives(state, tid, req, v, unsure)}
            _cross_axis_judge(state, req, selected)
    else:
        for req in state["requirements"]:
            matrix[req["scope_id"]] = {
                "selected": {"table_id": None, "status": "unknown", "covered": [], "gaps": [], "reason_codes": []},
                "alternatives": {"status": "none", "by_tables": [], "gaps": []},
            }
    out = {"final": final, "requirement_matrix": matrix}
    if issues:
        out["issues"] = issues
    return out


_FATAL_CODES = {"frequency_conflict", "period_outside", "condition_conflict", "condition_conflict_handoff"}
# high는 1위가 2위보다 검색 점수로도 뚜렷이 앞서야 한다: 2위 검색 점수 < 0.8 × 1위 검색 점수.
# 개발 세트로만 골랐다(문턱 0.6~0.95, 2위 / 나머지 최고 비교, docs/01 14절). 조건이 없는 질문은 판정이 늘 met이라
# 이것이 없으면 갈리지 않는다
SEARCH_MARGIN = 0.8
# 1·2위 검색 점수가 거의 같고(2위 ≥ 0.95 × 1위) 맞춘 조건 수도 같으면 질문이 표를 가르지 못한 것이라 low다.
# 개발 세트로 골랐고 보류 세트에서 같은 방향을 확인했다(docs/01 22절)
NEAR_TIE = 0.95


def _near_tie(ranked: list) -> bool:
    if len(ranked) < 2 or ranked[0]["components"]["search"] <= 0:
        return False
    judged = [sum(v for k, v in r["components"].items() if k != "search") for r in ranked[:2]]
    return judged[0] == judged[1] and ranked[1]["components"]["search"] >= NEAR_TIE * ranked[0]["components"]["search"]


def t4(state, runtime: Ctx) -> dict:
    """확신도 (단계 4, docs/01 4절 T4·22절). high는 아래가 모두 맞을 때만, low는 final 없음·error issue·조건 충돌·
    1·2위 검색 동률. 모델 자기 확신도는 쓰지 않는다(변별력 없음, 19·21절)."""
    final, matrix = state["final"], state["requirement_matrix"]
    rule, model = state["pick_rule"], state["pick_model"]
    tid = final["table_id"]
    if tid is None or not state["requirement_matrix"] or any(i["severity"] == "error" for i in state["issues"]):
        return {"confidence": "low"}
    selected = [row["selected"] for row in matrix.values()]
    if any(set(s["reason_codes"]) & _FATAL_CODES for s in selected):
        return {"confidence": "low"}
    if _near_tie(state["ranked"]):
        return {"confidence": "low"}  # T4는 confidence만 쓴다(계약 v1.9). 이유는 ranked에서 다시 셀 수 있다
    row = next((r for r in state["ranked"] if r["table_id"] == tid), None)
    fb = state["fit_basis"].get(tid, {})
    periods = state["fit_period"].get(tid, {})
    no_doubt = all(v["status"] == "match" for v in fb.values()) and all(
        _judge(fp, {}, [], {"condition_ids": [], "shared_condition_ids": []})["status"] == "met" for fp in periods.values())
    model_ok = model["status"] != "ok" or (
        final["model_review"] == "accepted" and model["table_id"] == rule["table_id"])
    ranked = state["ranked"]
    clear = bool(ranked) and ranked[0]["table_id"] == tid and ranked[0]["components"]["search"] > 0 and (
        len(ranked) == 1 or ranked[1]["components"]["search"] < SEARCH_MARGIN * ranked[0]["components"]["search"])
    if _cross_hold(selected):
        return {"confidence": "medium"}
    high = (
        bool(selected) and all(s["status"] == "met" for s in selected)
        and no_doubt and row is not None and row["policy_state"] == "none_needed" and model_ok and clear
    )
    return {"confidence": "high" if high else "medium"}


def _scopes(state, fit_basis_final: dict) -> list:
    conds = {f: state[f] for f in COND_ALL}
    unsure = ambiguous_conditions(state)
    scopes = []
    for req in state["requirements"]:
        ids = set(req["condition_ids"]) | set(req["shared_condition_ids"])
        # 귀속이 애매한 조건은 후보 요구마다 싣고 그 사실을 함께 넘긴다 (7번 1단계)
        pick = lambda f: [dict(c, attribution="ambiguous") if c["condition_id"] in unsure else c  # noqa: E731
                          for c in conds[f] if c["condition_id"] in ids]
        scopes.append(
            {
                "scope_id": req["scope_id"],
                "period": pick("cond_period"),
                "frequency": pick("cond_frequency"),
                "basis": [dict(c, role=fit_basis_final.get(c["condition_id"], {}).get("role")
                               or ("handoff" if c["axis"] in rules.HANDOFF_ONLY_AXES else None)) for c in pick("cond_basis")],
                "unit": pick("cond_unit"),
                "exclude": pick("cond_exclude"),
            }
        )
    return scopes


def _whole_question_pick(final: dict) -> tuple[str, str]:
    """Pick source/reason on the whole-question path, named as in the requirement-local arbitration.

    States only what T3 recorded: a rule pick with no model pick available (Q6G fallback or model off)
    is ("rule", "no_model"); any other combination is reported from T3's own fields, never relabelled.
    """
    if final["source"] == "rule" and final["model_review"] == "not_used":
        return "rule", "no_model"
    return final["source"], f"model_review:{final['model_review']}"


def t5(state, runtime: Ctx) -> dict:
    """A 결과와 B 전달 (v1.8 6절). State에는 새 issue만 돌려준다."""
    config = runtime.context.config
    question, run_meta = state["question"], state["run_meta"]
    issues_in = state["issues"]
    requirements = state["requirements"]
    conditions = {f: state[f] for f in COND_ALL}
    conditions["requirements"] = requirements
    for f in ("confidence", "requirement_matrix", "final", "meta", "matched_items", "fit_basis", "fit_period", "cards", "evidence"):
        state[f]

    if not requirements:  # 판정할 요구가 없다: 근거 없는 표를 내지 않고 정해진 빈 결과와 사유를 돌려준다
        new = [make_issue("no_requirements", "T5", "판정할 요구가 없다")]
        a_result = {"tables": [], "confidence": "low", "requirements": [], "conditions": conditions,
                    "matched_items": [], "evidence": [], "coverage": {},
                    "issue_codes": [i["code"] for i in issues_in] + ["no_requirements"]}
        b_handoff = {"query": question, "tables": [], "as_of": config.as_of, "scopes": [],
                     "item_itm_id": None, "matched_items": [], "meta_snapshot": run_meta["meta_snapshot"]}
        return {"a_result": a_result, "b_handoff": b_handoff, "issues": new}
    if not state["pool"]:  # 빈 풀 출력 (제안, D1)
        new = [make_issue("no_candidates", "T5", "후보 풀이 비었다")]
        a_result = {
            "tables": [],
            "confidence": "low",
            "requirements": [
                {"scope_id": r["scope_id"], "text": r["text"], "status": "unknown", "gaps": [],
                 "reason_codes": ["no_candidates"], "alternatives": {"status": "none", "by_tables": [], "gaps": []}}
                for r in requirements
            ],
            "conditions": conditions,
            "matched_items": [],
            "evidence": [],
            "coverage": {},
            "issue_codes": [i["code"] for i in issues_in] + ["no_candidates"],
        }
        b_handoff = {
            "query": question, "tables": [], "as_of": config.as_of, "scopes": _scopes(state, {}),
            "item_itm_id": None, "matched_items": [], "meta_snapshot": run_meta["meta_snapshot"],
        }
        return {"a_result": a_result, "b_handoff": b_handoff, "issues": new}

    final = state["final"]
    tid = final["table_id"]
    meta = state["meta"][tid]
    matrix = state["requirement_matrix"]
    card = next((c for c in state["cards"] if c["table_id"] == tid), None)
    used = [r["evidence_id"] for r in card["reasons"]] if card else []
    # Same selection fields as the requirement-local path: linked requirements and the pick actually made
    # on this whole-question path (rule pick; the old card-level model pick is not part of A v1).
    pick_source, pick_reason = _whole_question_pick(final)
    tables = [{"org_id": meta["org_id"], "table_id": tid, "table_name": meta["title"],
               "pick_source": pick_source, "pick_reason": pick_reason,
               "scope_ids": [r["scope_id"] for r in requirements]}]
    matched = state["matched_items"].get(tid, [])
    a_result = {
        "tables": tables,
        "confidence": state["confidence"],
        "requirements": [
            {
                "scope_id": r["scope_id"], "text": r["text"],
                "status": matrix[r["scope_id"]]["selected"]["status"],
                "gaps": matrix[r["scope_id"]]["selected"]["gaps"],
                "reason_codes": matrix[r["scope_id"]]["selected"]["reason_codes"],
                "alternatives": matrix[r["scope_id"]]["alternatives"],
                "pick_source": pick_source, "pick_reason": pick_reason, "contender": None,
            }
            for r in requirements
        ],
        "conditions": conditions,
        "matched_items": matched,
        "evidence": [dict(state["evidence"][eid], evidence_id=eid) for eid in used],
        "coverage": state["fit_period"].get(tid, {}),
        "issue_codes": [i["code"] for i in issues_in],
    }
    b_handoff = {
        "query": question,
        # The B handoff keeps the adopted baseline shape; scope_ids/pick_* stay in a_result only.
        "tables": [{k: t[k] for k in ("org_id", "table_id", "table_name")} for t in tables],
        "as_of": config.as_of,
        "scopes": _scopes(state, state["fit_basis"].get(tid, {})),
        "item_itm_id": (meta["item_itm"] or {}).get("itm_id"),
        "matched_items": [
            {
                "obj_id": m["obj_id"], "obj_id_sn": m["obj_id_sn"], "item_id": m["item_id"], "name": m["name_raw"],
                "parent_path": m["parent_path"], "scope_id": m["scope_id"], "condition_id": m["condition_id"],
                "match_method": m["match_method"], "polarity": m["polarity"],
            }
            for m in matched
        ],
        "meta_snapshot": run_meta["meta_snapshot"],
    }
    return {"a_result": a_result, "b_handoff": b_handoff}
