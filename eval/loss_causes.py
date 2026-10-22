"""Loss-cause diagnostic for one question (moved from the archived stage-4 runner): where the answer table
sits (top1 / top_k / pool / not_in_pool) and which judged signals put the final table ahead (Codex Y-01)."""
from __future__ import annotations

import re

JUDGED = ("period", "basis", "items", "title_conditions")  # 조건 판정 몫. title 등 다른 순위 신호는 넣지 않는다 (Codex Y-08)
_BRACKET = re.compile(r"\s*\(.*?\)\s*")  # J5 동률 키와 같은 이름 규칙


def judged(row: dict) -> float:
    """J5 점수의 조건 판정 몫. 같으면 조건 판정으로는 갈리지 않는 동률이다."""
    return sum(row["components"].get(k, 0.0) for k in JUDGED)


def _stem(meta: dict, tid: str) -> str:
    return _BRACKET.sub(" ", meta[tid]["title"] or tid).strip()


def causes(gold: set, ranked: list, final: str, meta: dict, k: int) -> tuple[str, list[str]]:
    """놓친 원인 (Codex Y-01). 위치 하나와 손해 표지 여럿을 따로 적는다.

    위치: top1 / top_k(카드 안) / pool(카드 밖) / not_in_pool. 손해: 정답 중 가장 앞 행과 최종 표를 비교한다.
    """
    pos = next((i for i, r in enumerate(ranked) if r["table_id"] in gold), None)
    if final in gold:
        return "top1", []
    where = "not_in_pool" if pos is None else "top_k" if pos < k else "pool"
    top = next((r for r in ranked if r["table_id"] == final), None)
    if pos is None or top is None:
        return where, []
    g = ranked[pos]
    tags = []
    if "blocked" in g["flags"] and "blocked" not in top["flags"]:
        tags.append("blocked")
    if judged(g) < judged(top):
        tags.append("judged_lower")
    elif judged(g) > judged(top):
        tags.append("judged_higher")  # 판정은 앞서는데 다른 신호로 밀림
    else:
        same_stem = _stem(meta, g["table_id"]) == _stem(meta, final)
        tags.append("sibling_tie" if same_stem else "judged_tie")
        tags.append("search_tie" if round(g["components"]["search"], 6) == round(top["components"]["search"], 6)
                    else "search_lower" if g["components"]["search"] < top["components"]["search"] else "search_higher")
    other = {k2: g["components"].get(k2, 0.0) - top["components"].get(k2, 0.0)
             for k2 in set(g["components"]) | set(top["components"]) if k2 not in JUDGED and k2 != "search"}
    tags += [f"{k2}_lower" for k2, d in sorted(other.items()) if d < 0]
    return where, tags
