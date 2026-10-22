"""정답 표 묶음과 집합 채점 (1단계, 2026-10-10).

사용자 결정(10/10):
- 출력 표 개수는 정답 표 개수와 같아야 한다. "항상 표 1개" 계약은 폐기 대상이다(계약 v2에서 바꾼다).
- 정답 표가 여럿이면 **모두 나와야 정답**이다. 그래서 묶음(group)은 표마다 하나다.
- 채점은 정답 여부와 함께, 찾은 정답 표와 못 찾은 정답 표를 나눠 기록한다. 이후 개선 자료로 쓴다.

묶음 형식은 group 목록이고 group은 허용 표 ID 목록이다. 지금 규칙에서는 모든 group이 표 1개다.
형식만 목록으로 둔 것은 이후 검수에서 "형제 중 어느 것이든"이 확정되는 사례를 담기 위해서다.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

GROUPS = Path(__file__).resolve().parent / "cases" / "expected_table_groups_v1.json"
ROLES = GROUPS.with_name("case_roles_v1.json")
ROLES_V2 = GROUPS.with_name("case_roles_v2.json")


def twin_group_id(groups: list[list[str]]) -> str:
    """Identify equal answer groups independently of group/table order."""
    canonical = sorted(sorted(set(group)) for group in groups)
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "tg-" + hashlib.sha256(encoded).hexdigest()[:16]


def load_case_roles(path: Path = ROLES) -> dict[str, dict]:
    """Load evaluation-only roles; never pass them to the A graph."""
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def load_groups(path: Path = GROUPS) -> dict[str, list[list[str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {cid: row["groups"] for cid, row in data["cases"].items()}


def groups_from_tables(table_ids) -> list[list[str]]:
    """규칙(10/10): 정답 표마다 묶음 하나. 순서는 유지하고 중복은 뺀다."""
    seen, out = set(), []
    for t in table_ids:
        if t not in seen:
            seen.add(t)
            out.append([t])
    return out


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


def summarize_sets(rows: list[dict], case_roles: dict[str, dict] | None = None) -> dict:
    """Summarize set scores with optional evaluation-only role filtering.

    Role summaries require unique case IDs and agreement with the existing
    boundary flag. Twin weighting averages binary case scores within each
    answer group, then weights every group equally (including singletons).
    V1 keeps legacy denominators; V2 excludes only clarify plus boundary and
    reports overall/single/multi using the unchanged answer group count.
    """
    role_summary = _summarize_roles(rows, case_roles) if case_roles is not None else {}
    sub = [r for r in rows if not r["boundary"] and
           not (case_roles is not None and case_roles[r["case_id"]].get("clarify", False))]
    multi = [r for r in sub if r["sets"]["n_groups"] > 1]
    single = [r for r in sub if r["sets"]["n_groups"] == 1]
    n_groups = sum(r["sets"]["n_groups"] for r in sub)

    def frac(rs, key="set_exact"):
        return f'{sum(r["sets"][key] for r in rs)}/{len(rs)}'

    where = {}
    for r in sub:
        for m in r["sets"]["missing"]:
            where[m["where"]] = where.get(m["where"], 0) + 1
    result = {
        "set_exact": frac(sub), "set_exact_single": frac(single), "set_exact_multi": frac(multi),
        "groups_found": f'{sum(len(r["sets"]["found"]) for r in sub)}/{n_groups}',
        "groups_in_top_k": f'{sum(r["sets"]["groups_in_top_k"] for r in sub)}/{n_groups}',
        "missing_where": where,
        "extra_tables": sum(len(r["sets"]["extra"]) for r in sub),
        "output_count_short": sum(r["sets"]["n_output"] < r["sets"]["n_groups"] for r in sub),
    }
    result.update(role_summary)
    return result


def _summarize_roles(rows: list[dict], case_roles: dict[str, dict]) -> dict:
    by_role = {role: [] for role in ("golden_regression", "dev_pool", "boundary")}
    seen = set()
    has_clarify = any("clarify" in info for info in case_roles.values())
    excluded = {role: [] for role in by_role}
    for row in rows:
        cid = row["case_id"]
        if cid in seen:
            raise ValueError(f"Duplicate scored case: {cid}")
        seen.add(cid)
        if cid not in case_roles:
            raise ValueError(f"Missing case role: {cid}")
        info = case_roles[cid]
        role = info["role"]
        if role not in by_role or info["origin"] not in {"golden", "dev", "hold"}:
            raise ValueError(f"Invalid case role/origin: {cid}")
        if row["boundary"] != (role == "boundary"):
            raise ValueError(f"Boundary role disagrees with evaluation: {cid}")
        if "layer" in row and row["layer"] != info["origin"]:
            raise ValueError(f"Origin disagrees with evaluation: {cid}")
        if role != "boundary" and (role == "golden_regression") != (info["origin"] == "golden"):
            raise ValueError(f"Role disagrees with origin: {cid}")
        if has_clarify and type(info.get("clarify")) is not bool:
            raise ValueError(f"Missing or invalid clarify flag: {cid}")
        if role == "dev_pool":
            twin = info["twin_group"]
            if not isinstance(twin, str) or not twin:
                raise ValueError(f"Missing twin group: {cid}")
        if info.get("clarify", False):
            if role == "boundary":
                raise ValueError(f"Boundary cannot also be clarify: {cid}")
            excluded[role].append(row)
        else:
            by_role[role].append(row)

    result = {
        "by_role": {
            role: {"cases": len(by_role[role]), **summarize_sets(by_role[role])}
            for role in ("golden_regression", "dev_pool")
        } | {"boundary": {"cases": len(by_role["boundary"]), "scored_cases": 0}},
        "dev_pool_twins": _summarize_twins(by_role["dev_pool"], case_roles),
    }
    if has_clarify:
        result["clarify"] = {
            "cases": sum(map(len, excluded.values())),
            "by_role": {role: len(rs) for role, rs in excluded.items()},
            "case_ids": sorted(r["case_id"] for rs in excluded.values() for r in rs),
        }
        # Table count comes from unchanged answer groups, never predicted outputs.
        for role in ("golden_regression", "dev_pool"):
            members = by_role[role]
            result["by_role"][role].update(
                input_cases=len(members) + len(excluded[role]),
                clarify_cases=len(excluded[role]),
                by_table_count={
                    name: {"cases": len(rs), **summarize_sets(rs),
                           **({"twins": _summarize_twins(rs, case_roles)} if role == "dev_pool" else {})}
                    for name, rs in _table_count_slices(members).items()
                },
            )
    return result


def _table_count_slices(rows: list[dict]) -> dict[str, list[dict]]:
    return {"overall": rows,
            "single": [r for r in rows if r["sets"]["n_groups"] == 1],
            "multi": [r for r in rows if r["sets"]["n_groups"] > 1]}


def _summarize_twins(rows: list[dict], case_roles: dict[str, dict]) -> dict:
    twins = defaultdict(list)
    for row in rows:
        twins[case_roles[row["case_id"]]["twin_group"]].append(row)

    group_rows = {}
    total = Fraction()
    for twin, members in sorted(twins.items()):
        correct = sum(row["sets"]["set_exact"] for row in members)
        rate = Fraction(correct, len(members))
        total += rate
        group_rows[twin] = {
            "case_ids": sorted(row["case_id"] for row in members),
            "origins": dict(sorted(Counter(case_roles[row["case_id"]]["origin"] for row in members).items())),
            "set_exact": f"{correct}/{len(members)}",
            "accuracy": float(rate),
        }
    macro = total / len(twins) if twins else None
    return {
        "method": "mean_of_within_group_set_exact_rates",
        "cases": len(rows),
        "groups": len(twins),
        "duplicate_groups": sum(len(members) > 1 for members in twins.values()),
        "singleton_groups": sum(len(members) == 1 for members in twins.values()),
        "set_exact_macro": float(macro) if macro is not None else None,
        "set_exact_macro_fraction": str(macro) if macro is not None else None,
        "by_group": group_rows,
    }
