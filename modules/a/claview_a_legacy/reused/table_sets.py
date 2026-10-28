"""Set scoring copied from the old evaluation code (eval/table_sets.py, commit 55fd41b).

Copied: ``score_sets`` only (standard library, no I/O). Not copied: answer-group and role loaders (data is read
through ``claview_a_legacy.paths`` by new code), ``summarize_sets`` and its role/twin helpers.
"""

from __future__ import annotations


# Source: eval/table_sets.py:52-92 (commit 55fd41b), copied verbatim.
def score_sets(groups: list[list[str]], output: list[str], ranked: list[str], pool: list[str], k: int) -> dict:
    """집합 채점.

    - set_exact: 최대 이분 매칭이 모든 묶음을 서로 다른 출력 표에 대응시키고 출력 수가 같다.
      출력은 집합이다. [[X], [X]]는 서로 다른 두 표를 요구하므로 [X]나 [X, X]로 만족할 수 없다.
    - found / missing: 묶음별로 찾았나. 못 찾은 묶음은 어디까지 왔는지(top_k·pool·not_in_pool)와 순위를 남긴다.
    - extra: 출력했지만 어느 묶음에도 없는 표.
    """
    out = list(dict.fromkeys(output))
    allowed = {t for g in groups for t in g}
    owner = {}

    def augment(group_index, visited):
        for table in out:
            if table not in groups[group_index] or table in visited:
                continue
            visited.add(table)
            if table not in owner or augment(owner[table], visited):
                owner[table] = group_index
                return True
        return False

    for group_index in range(len(groups)):
        augment(group_index, set())
    matched = {group_index: table for table, group_index in owner.items()}
    found, missing = [], []
    for i, g in enumerate(groups):
        rank = next((n + 1 for n, t in enumerate(ranked) if t in g), None)
        row = {"group": i, "tables": g, "rank": rank}
        if i in matched:
            found.append({**row, "output": matched[i]})
        else:
            where = "not_in_pool" if not any(t in g for t in pool) else ("top_k" if rank is not None and rank <= k else "pool")
            missing.append({**row, "where": where})
    extra = [t for t in out if t not in allowed]
    return {
        "set_exact": bool(groups) and not missing and not extra and len(out) == len(groups),
        "n_groups": len(groups), "n_output": len(out),
        "found": found, "missing": missing, "extra": extra,
        "groups_in_top_k": sum(1 for g in groups if any(t in g for t in ranked[:k])),
    }
