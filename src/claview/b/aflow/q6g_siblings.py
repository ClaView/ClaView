"""Q6G 근거 표의 형제 표 교정 (A v1, Q6G 해석 직후).

모델이 고른 근거 표와 이름(괄호 앞)이 같은 후보 카드가 있을 때, 질문 조건에 더 맞는 형제로 바꾼다.
- 기간: 요구에 걸린 명시 기간이 있으면 수록 구간이 그 기간을 덮는 형제로. 기간이 없으면 형제 중
  수록 끝이 가장 늦은 표보다 STALE_MONTHS 넘게 일찍 끝난 표를 그 최신 형제로.
- 기준: 요구에 걸린 기준어(pos)가 형제 표명에만 있거나, 뺄 기준어(neg)가 고른 표명에만 있으면 그 형제로.
- 경계: 주기나 확정 기준(GUARD_AXES)이 다른 형제로는 바꾸지 않는다. 요청 주기가 있으면 형제가 그 주기를 갖고,
  없으면 고른 표의 주기를 모두 가져야 한다. 기준 축 값이 다르면 요구에 그 축의 기준어가 있고, 형제의 값이
  요청 값(긍정)에 맞고 뺄 값(부정)이 아닐 때만 바꾼다. 긍정 값을 가진 표에서 그 값이 없는 형제로도 가지 않는다.
  모델이 함께 든 근거를 앞에 두는 순서 바꾸기에도 같은 경계를 쓴다.
후보 카드 안에서만 바꾸고, 표 ID나 사례를 두지 않는다. 바꾼 내역은 interpretation.model.sibling_fix에 남긴다.
"""
from __future__ import annotations

import re

from . import rules
from .catalog import FREQ_CODE

STALE_MONTHS = 36
_RANK = {"covered": 0, "unknown": 1, "partial": 2, "outside": 3}
# 표 내용을 가르는 기준 축. 중소기업기본법·표본조사는 같은 통계의 작성 시기 표지라 넣지 않는다(기간·최신성으로 가른다)
GUARD_AXES = ("seasonal", "stock", "price", "loan_basis", "currency", "survey_view", "valuation", "classification")


def base_name(title: str) -> str:
    return re.split(r"[(（]", title or "")[0].strip()


def _freqs(table: dict) -> dict:
    out = {}
    for f in table.get("freqs", []):
        code = FREQ_CODE.get(f.get("se") or "")
        if code:
            out[code] = (f.get("start"), f.get("end"))
    return out


def _end_month(table: dict) -> int | None:
    ends = [rules.stamp_months(e) for _, e in _freqs(table).values()]
    ends = [e[1] for e in ends if e]
    return max(ends) if ends else None


def _period_rank(table: dict, periods: list, wanted: list) -> int:
    """요구 기간들 중 가장 나쁜 판정을, 쓸 수 있는 주기 중 가장 좋은 것으로 본다(J3와 같은 비교 함수)."""
    best = None
    for f, (start, end) in _freqs(table).items():
        if wanted and f not in wanted:
            continue
        worst = max(_RANK[rules.compare_period(p, start, end, f)[0]] for p in periods)
        best = worst if best is None else min(best, worst)
    return _RANK["unknown"] if best is None else best


def _paren(title: str) -> str:
    return re.sub(r"\s+", "", title[len(base_name(title)):])


def _basis_score(title: str, basis: list) -> int:
    text = _paren(title)
    score = 0
    for c in basis:
        value = re.sub(r"\s+", "", c["value"])
        if value and value in text:
            score += 1 if c.get("polarity", "pos") == "pos" else -1
    return score


def _axis_values(title: str) -> dict:
    """괄호 안 낱말 → {축: 값} (GUARD_AXES만). "계절조정계열", "잔액 기준"처럼 붙은 말도 읽는다."""
    out = {}
    for tok in rules.bracket_tokens(title):
        for t in (tok, re.sub(r"(기준|계열)$", "", tok)):
            for axis in GUARD_AXES:
                for value, forms in rules.BASIS_AXES[axis].items():
                    if t in {rules.nospace(f) for f in forms} | {rules.nospace(value)}:
                        out[axis] = value
            if t == "잔액":
                out["loan_basis"] = "잔액기준"
    return out


def _asked(basis: list) -> dict:
    """요구의 기준어 → {축: (긍정 값들, 부정 값들)}."""
    out = {}
    for c in basis:
        pos, neg = out.setdefault(c["axis"], (set(), set()))
        (neg if c.get("polarity") == "neg" else pos).add(c["value"])
    return out


def _compatible(cur: dict, other: dict, wanted: list, asked: dict) -> bool:
    """cur 대신 other를 써도 주기·확정 기준이 어긋나지 않는가."""
    have, give = set(_freqs(cur)), set(_freqs(other))
    need = (set(wanted) & have) if wanted else have
    if not need <= give:
        return False
    a, b = _axis_values(cur["name"]), _axis_values(other["name"])
    for x in GUARD_AXES:
        if a.get(x) == b.get(x):
            continue
        if x not in asked:
            return False  # 요구에 없는 축은 바꾸지 않는다
        pos, neg = asked[x]
        if b.get(x) in neg:
            return False  # 뺄 값으로 가지 않는다
        if pos and b.get(x) not in pos and (b.get(x) is not None or a.get(x) in pos):
            return False  # 요청 값과 다른 값, 또는 요청 값을 잃는 쪽으로 가지 않는다
    return True


def fix(state, reqs, cards, tables):
    """reqs의 grounding을 형제 교정한 새 목록과 교정 기록을 돌려준다."""
    by_tid = {c["_table_id"]: c for c in cards}
    conds = {c["condition_id"]: c for f in ("cond_period", "cond_frequency", "cond_basis") for c in state[f]}
    log = []
    for req in reqs:
        ids = set(req["condition_ids"]) | set(req["shared_condition_ids"])
        periods = [conds[i] for i in ids if i in conds and i.startswith("P") and conds[i]["status"] == "explicit"
                   and conds[i].get("start")]
        wanted = [conds[i]["value"] for i in ids if i in conds and i.startswith("F")]
        basis = [conds[i] for i in ids if i in conds and i.startswith("B") and conds[i]["axis"] != "classification"]
        asked = _asked([conds[i] for i in ids if i in conds and i.startswith("B")])
        new, seen = [], set()
        for g in req["grounding"]:
            tid = g["table_id"]
            mine = tables.get(tid)
            sibs = [c["_table_id"] for c in cards if c["_table_id"] != tid and mine is not None
                    and base_name(c["title"]) == base_name(mine["name"]) and c["_table_id"] in tables
                    and _compatible(mine, tables[c["_table_id"]], wanted, asked)]
            chosen, reason = tid, None
            if sibs:
                group = [tid] + sibs
                if periods:
                    rank = {t: _period_rank(tables[t], periods, wanted) for t in group}
                    better = [t for t in sibs if rank[t] < rank[tid] and rank[t] <= _RANK["unknown"]]
                    if better:
                        chosen, reason = min(better, key=lambda t: (rank[t], -(_end_month(tables[t]) or 0))), "period"
                else:
                    ends = {t: _end_month(tables[t]) for t in group}
                    latest = max((e for e in ends.values() if e is not None), default=None)
                    if latest is not None and ends[tid] is not None and latest - ends[tid] > STALE_MONTHS:
                        fresh = [t for t in sibs if ends[t] is not None and latest - ends[t] <= STALE_MONTHS]
                        if fresh:
                            score = {t: _basis_score(tables[t]["name"], basis) for t in fresh}
                            chosen, reason = max(fresh, key=lambda t: (score[t], ends[t])), "stale"
                if reason is None and basis:
                    score = {t: _basis_score(tables[t]["name"], basis) for t in group}
                    ok = [t for t in sibs if score[t] > score[tid]
                          and (not periods or _period_rank(tables[t], periods, wanted) <= _period_rank(tables[tid], periods, wanted))]
                    if ok:
                        chosen, reason = max(ok, key=lambda t: (score[t], _end_month(tables[t]) or 0)), "basis"
            if chosen in seen:
                continue
            seen.add(chosen)
            if reason:
                log.append({"scope_id": req["scope_id"], "from": tid, "to": chosen, "reason": reason})
                card = by_tid[chosen]
                names = {v["name"] for v in by_tid[tid]["values"] if v["_itm"] in g["item_ids"]} if tid in by_tid else set()
                new.append({"table_id": chosen, "item_ids": [v["_itm"] for v in card["values"] if v["name"] in names]})
            else:
                new.append(g)
        # 형제 교정 뒤에도 앞선 근거가 기간에 맞지 않으면 모델이 함께 든 다른 근거를 앞에 둔다(순서만 바꾼다)
        if len(new) > 1:
            if periods:
                key = {g["table_id"]: _period_rank(tables[g["table_id"]], periods, wanted) if g["table_id"] in tables
                       else _RANK["unknown"] for g in new}
                key = {t: max(r, _RANK["unknown"]) for t, r in key.items()}  # covered와 unknown은 같은 자리
            else:
                ends = {g["table_id"]: _end_month(tables[g["table_id"]]) if g["table_id"] in tables else None for g in new}
                latest = max((e for e in ends.values() if e is not None), default=None)
                key = {t: int(latest is not None and e is not None and latest - e > STALE_MONTHS) for t, e in ends.items()}
            head = tables.get(new[0]["table_id"])
            order = sorted(new, key=lambda g: key[g["table_id"]])
            if order[0] is not new[0] and head is not None:
                # 경계를 넘는 근거는 앞으로 올리지 않는다: 올릴 수 있는 첫 근거만 맨 앞에 둔다
                lead = next(g for g in order if g is new[0] or (
                    g["table_id"] in tables and _compatible(head, tables[g["table_id"]], wanted, asked)))
                order = new if lead is new[0] else [lead] + [g for g in order if g is not lead]
            if order[0]["table_id"] != new[0]["table_id"]:
                log.append({"scope_id": req["scope_id"], "from": new[0]["table_id"], "to": order[0]["table_id"],
                            "reason": "reorder_period" if periods else "reorder_stale"})
            new = order
        req["grounding"] = new
    return reqs, log
