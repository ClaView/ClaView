"""J stage nodes (J1–J5, J7): metadata, item/period/basis fit, scoring and candidate cards.

Moved verbatim from nodes.py (node-structure N5); nodes.py assembles NODES."""

from __future__ import annotations

import copy
import itertools
import re

from . import measure, rules
from .catalog import _BRACKET, norm_v1, nospace
from .contract import CANDIDATE_CARDS, COND_ALL, RULE_VERSIONS
from .metadata_evidence import item_prefix_evidence, item_prefix_support, scope_total_order
from .node_common import Ctx, _to_span, _tokens
from .q_nodes import _query_range, ambiguous_conditions


# A v1 algorithm constants (formerly run settings with a single adopted value).
TITLE_BONUS = 15.0  # J5: table stem appears verbatim in the question (chosen on the development layer, docs/01 23절)


def _missing_meta(table_id: str, snapshot: str) -> dict:
    return {
        "org_id": None, "title": None, "catalog_paths": [], "prd_by_freq": {}, "coverage_end": {},
        "publication_status": "unknown", "item_itm": None, "dims": [], "items": [], "unit": None,
        "cmmt": [], "source_snapshot": snapshot, "fetched_at": "unknown",
        "missing_fields": ["TBL", "PRD", "ITM", "UNIT", "CMMT"],
    }


def j1(state, runtime: Ctx) -> dict:
    catalog = runtime.context.catalog
    meta = {}
    for p in state["pool"]:
        tid = p["table_id"]
        # catalog는 읽기 전용이라 사본만 State에 넣는다 (v1.9 N2)
        meta[tid] = copy.deepcopy(catalog.meta_store[tid]) if tid in catalog.meta_store else _missing_meta(tid, catalog.meta_snapshot)
    return {"meta": meta}


_STOP_TERMS = frozenset({"그래프", "추이", "비교", "흐름", "자료", "표", "기준", "규모", "지표", "전체", "각각"})
_TAIL = re.compile(r"(?:으로|에서|의|을|를|이|가|은|는|로|에|와|과|도|만)$")


def _match_method(it: dict, term: str) -> str | None:
    if it["name_raw"] == term:
        return "exact"
    if it["name_nospace"] == nospace(term):
        return "nospace"
    if it["name_norm"] and it["name_norm"] == norm_v1(term):
        return "normalized"
    return None


def _j2_terms(state) -> list[dict]:
    """J2가 찾는 말: 조건(제외 → 분해 → 기준어)이 먼저, 그다음 조건 밖 질문어 1~3낱말 묶음."""
    query, offsets, reqs = state["query"], state["offsets"], state["requirements"]

    def scope_of_condition(cid):
        return next((r["scope_id"] for r in reqs if cid in r["condition_ids"]), None)

    terms = []
    for c in state["cond_exclude"]:
        terms.append({"texts": c["complement_hints"], "span": c["span"], "condition_id": c["condition_id"],
                      "scope_id": scope_of_condition(c["condition_id"]), "kind": "complement", "fallback": c["text"]})
    for c in state["cond_breakdown"]:
        terms.append({"texts": [c["text"]], "span": c["span"], "condition_id": c["condition_id"],
                      "scope_id": scope_of_condition(c["condition_id"]), "kind": "term"})
    for c in state["cond_basis"]:
        texts = [f for form in rules.basis_forms(c["axis"], c["value"]) for f in (form, form + "기준")]
        terms.append({"texts": texts, "span": c["span"], "condition_id": c["condition_id"],
                      "scope_id": scope_of_condition(c["condition_id"]), "kind": "term",
                      "polarity": c["polarity"]})  # "X하지 않은"의 X 분류값은 뺄 값이다 (Codex F01)
    taken = [_query_range(offsets, c["span"]) for f in ("cond_period", "cond_frequency", "cond_basis", "cond_unit",
                                                         "cond_breakdown", "cond_exclude") for c in state[f] if c["span"]]
    toks = [t for t in _tokens(query) if all(t[2] <= a or t[1] >= b for a, b in taken)]
    for n in (3, 2, 1):
        for k in range(len(toks) - n + 1):
            group = toks[k:k + n]
            if any(query[a[2]:b[1]].strip() for a, b in zip(group, group[1:])):
                continue  # 이웃 낱말만 묶는다
            i, j = group[0][1], group[-1][2]
            text = _TAIL.sub("", re.sub(r"[()\[\],.?!]+$", "", query[i:j]))
            j = i + len(text)
            text = re.sub(r"[()\[\],.?!]", "", text)
            if len(nospace(text)) < 2 or text in _STOP_TERMS:
                continue
            span = _to_span(offsets, i, j)
            scope = next((r["scope_id"] for r in reqs if span and r["span"][0] <= span[0] and span[1] <= r["span"][1]), None)
            terms.append({"texts": [text], "span": span, "condition_id": None, "scope_id": scope, "kind": "term"})
    return terms


def j2(state, runtime: Ctx) -> dict:
    """질문어를 표의 분류값에 맞춘다 (단계 3). 같은 말끼리만 맞추며(exact·nospace·normalized), 제외 조건은 보완 분류값을 먼저 찾는다 (R1)."""
    catalog = runtime.context.catalog
    version = RULE_VERSIONS["normalize"]
    df = catalog.item_index["df"][version]
    state["question"], state["query_nospace"]
    terms = _j2_terms(state)
    for key, text_key in (("measures", "text"), ("breakdowns", "text"), ("search_terms", "term")):  # 단계 5: Q6 개념 term
        for c in state["concepts"][key]:
            terms.append({"texts": [c[text_key]], "span": c["span"], "condition_id": None,
                          "scope_id": c["scope_id"], "kind": "term"})
    matched, evidence = {}, {}
    for tid, meta in state["meta"].items():
        # 부모는 같은 축 안에서만 찾는다: 키는 (obj_id, item_id) (v1.9 N5)
        by_key = {(it["obj_id"], it["item_id"]): it for it in meta["items"]}
        sn = {d["obj_id"]: d["obj_id_sn"] for d in meta["dims"]}
        rows, seen = [], set()

        def add(it, term, t, method, polarity):
            key = (it["obj_id"], it["item_id"], term, t["condition_id"])
            if key in seen:
                return
            seen.add(key)
            path, up, visited = [], it["up_item_id"], {it["item_id"]}
            while up and (it["obj_id"], up) in by_key and up not in visited:  # 순환이면 거기서 멈춘다
                visited.add(up)
                parent = by_key[(it["obj_id"], up)]
                path.insert(0, {"item_id": up, "name": parent["name_raw"]})
                up = parent["up_item_id"]
            match_id = f"{tid}-m{len(rows) + 1}"
            rows.append({
                "match_id": match_id, "scope_id": t["scope_id"], "condition_id": t["condition_id"], "term": term,
                "term_span": t["span"], "obj_id": it["obj_id"], "obj_id_sn": sn.get(it["obj_id"]),
                "item_id": it["item_id"], "name_raw": it["name_raw"], "parent_path": path,
                "depth": len(path), "unit": it["unit"], "match_method": method,
                "df": df.get(it["name_norm"]), "df_scope": version, "polarity": polarity,
            })
            evidence[f"E-{match_id}"] = {
                "table_id": tid, "kind": "itm",
                "text": f"분류값 '{it['name_raw']}' "
                        + ("(제외 조건의 보완)" if method == "complement" else "(뺄 값)" if polarity == "neg" else "일치")
                        + (f" (상위: {path[-1]['name']})" if path else ""),
                "source_field": "ITM.ITM_NM", "source_snapshot": meta["source_snapshot"],
                "stance": "against" if polarity == "neg" else "support",
            }

        for t in terms:
            hit = False
            for text in t["texts"]:
                for it in meta["items"]:
                    method = _match_method(it, text)
                    if method:
                        hit = True
                        add(it, text, t, "complement" if t["kind"] == "complement" else method,
                            t.get("polarity", "pos"))
            if not hit and t["kind"] == "complement":
                for it in meta["items"]:
                    method = _match_method(it, t["fallback"])
                    if method:
                        add(it, t["fallback"], t, method, "neg")
        if rows:
            matched[tid] = rows
    return {"matched_items": matched, "evidence": evidence}


_PERIOD_RANK = {"covered": 0, "unknown": 1, "partial": 2, "outside": 3}  # 여러 기간이 한 요구에 걸리면 가장 나쁜 것


def _envelope(periods: list) -> dict | None:
    """요구에 걸린 요청 기간. 둘 이상이면 처음~끝을 묶고 kind는 range다 (단계 2 차분 E11)."""
    if not periods:
        return None
    if len(periods) == 1:
        p = periods[0]
        return {"start": p["start"], "end": p["end"], "kind": p["kind"]}
    # 표기 문자열이 아니라 달 구간으로 처음·끝을 고른다("2020-Q4"와 "2020-12"). 열린 끝(since)은 열린 채 둔다 (Codex F04)
    spans = [(rules.stamp_months(p["start"]), p) for p in periods]
    if any(s is None for s, _ in spans):
        return {"start": None, "end": None, "kind": "range"}
    first = min(spans, key=lambda x: x[0][0])[1]["start"]
    if any(p["end"] is None and p["kind"] == "since" for _, p in spans):
        return {"start": first, "end": None, "kind": "since"}
    ends = [(rules.stamp_months(p["end"] or p["start"]), p["end"] or p["start"]) for _, p in spans]
    if any(e is None for e, _ in ends):
        return {"start": None, "end": None, "kind": "range"}
    return {"start": first, "end": max(ends, key=lambda x: x[0][1])[1], "kind": "range"}


def j3(state, runtime: Ctx) -> dict:
    """요구 × 주기별 주기·기간 판정 (단계 2). 수록 끝으로 발행 중단을 판단하지 않는다."""
    periods, freqs = state["cond_period"], state["cond_frequency"]
    unsure = ambiguous_conditions(state)  # 귀속이 애매한 기간·주기는 요청으로 판정하지 않는다 (7번 1단계 보완 3)
    fit, evidence = {}, {}
    for tid, meta in state["meta"].items():
        fit[tid] = {}
        available = sorted(meta["prd_by_freq"], key=lambda f: "YHQM".index(f))
        for req in state["requirements"]:
            sid = req["scope_id"]
            ids = (set(req["condition_ids"]) | set(req["shared_condition_ids"])) - unsure
            req_periods = [c for c in periods if c["condition_id"] in ids]
            wanted = list(dict.fromkeys(c["value"] for c in freqs if c["condition_id"] in ids))
            eids = []
            if not available:
                freq_status = "unknown"
            elif not wanted:
                freq_status = "match"  # 주기 요청이 없으면 어긋날 것이 없다
            else:
                freq_status = "match" if set(wanted) & set(available) else "conflict"
            if wanted or not available:
                eid = f"J3:{tid}:{sid}:freq"
                evidence[eid] = {
                    "table_id": tid, "kind": "prd",
                    "text": f"요청 주기 {'·'.join(wanted) or '없음'}, 수록 주기 {'·'.join(available) or '없음'}",
                    "source_field": "meta.prd_by_freq", "source_snapshot": meta["source_snapshot"],
                    "stance": {"match": "support", "conflict": "against", "unknown": "unknown"}[freq_status],
                }
                eids.append(eid)
            by_freq = {}
            for f in available:
                cov = meta["prd_by_freq"][f]
                if not req_periods:
                    status, reason = "covered", "not_requested"
                else:
                    judged = [rules.compare_period(p, cov["start"], cov["end"], f) for p in req_periods]
                    status, reason = max(judged, key=lambda x: _PERIOD_RANK[x[0]])
                row = {"status": status, "coverage": [cov["start"], cov["end"]]}
                if reason:
                    row["reason"] = reason
                by_freq[f] = row
                if req_periods and (not wanted or f in wanted):
                    eid = f"J3:{tid}:{sid}:prd:{f}"
                    evidence[eid] = {
                        "table_id": tid, "kind": "prd", "text": f"{f} {cov['start']}~{cov['end']}",
                        "source_field": f"meta.prd_by_freq.{f}", "source_snapshot": meta["source_snapshot"],
                        "stance": {"covered": "support", "unknown": "unknown"}.get(status, "against"),
                    }
                    eids.append(eid)
            judged_freqs = [f for f in available if not wanted or f in wanted]
            ended = bool(req_periods) and bool(judged_freqs) and all(
                by_freq[f].get("reason") == "ended_before_request" and by_freq[f]["status"] == "outside" for f in judged_freqs
            )
            fit[tid][sid] = {
                "requested": {"frequencies": wanted, "period": _envelope(req_periods)},
                "frequency": {"status": freq_status, "available": available},
                "period_by_frequency": by_freq,
                "coverage_status": "ended_before_request" if ended else "n/a",
                "evidence_ids": eids,
            }
    return {"fit_period": fit, "evidence": evidence}


def _has_any(text: str, forms) -> bool:
    return any(nospace(f) in text for f in forms)


_BYEOL_MARK = re.compile(r"(?:등|같은|처럼)\s*$")  # "A, B 등 X별": 열거한 값이 X의 예다
_BYEOL_JOIN = re.compile(r"(?:와|과|및|이나|또는)$")


def _byeol_linked(query: str, offsets: list, c: dict, matches: list, other_spans: list, excluded: set,
                  list_ids: frozenset = frozenset()) -> list:
    """"X별" 조건에 문법으로 연결된 열거 값 중 표의 한 분류축 값과 맞는 것.

    연결로 보는 꼴은 둘뿐이다: "A, B(, C) 등 X별"(등·같은·처럼 바로 뒤 X별)과 "X별(A, B)". 열거는 쉼표·가운뎃점·
    빗금이나 와/과/및/이나/또는으로 이어진 항목 둘 이상이어야 하고, 괄호 안에서도 공백만으로는 항목을 나누지 않는다
    (여러 낱말로 된 이름 하나는 한 항목이다). 단순히 가까이 있는 낱말은 연결로 보지 않는다.
    근거로 세지 않는 것: 다른 조건의 낱말(condition_id 있음, 다른 조건 범위 안), 제외 대상과 같은 값, 제외 보완 값
    (complement), 같은 낱말의 중복. 단 Q5 열거(cond_breakdown, list_ids)는 이 열거 자체이므로 다른 조건으로 보지 않는다. 한 축에서 서로 다른 열거 낱말 둘 이상이 맞아야 하고, 아니면 빈 목록이다."""
    span = c.get("span")
    if not span or not offsets:
        return []
    pos = {o: i for i, o in enumerate(offsets)}  # 원문 위치 → 정리본 위치
    start = pos.get(span[0])
    if start is None:
        return []
    items = []
    head = query[:start]
    mark = _BYEOL_MARK.search(head)
    if mark:
        toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[^\s,·/()]+", head[:mark.start()])]
        chain = []
        for k in range(len(toks) - 1, -1, -1):
            word, i, j = toks[k]
            if chain:
                gap = head[j:chain[-1][1]]
                joined = re.search(r"[,·/]", gap) or _BYEOL_JOIN.search(word)
                if not joined:
                    break
            if chain and _BYEOL_JOIN.search(word):
                j -= len(_BYEOL_JOIN.search(word).group(0))
            chain.append((word, i, j))
        items = [(i, j) for _, i, j in chain] if len(chain) >= 2 else []
    else:
        value_end = start + len(c["value"])
        paren = re.match(r"\s*\(([^)]*)\)", query[value_end:])
        if paren:  # 괄호 안도 실제 구분자(쉼표·가운뎃점·빗금, 와/과/및/이나/또는)로만 나눈다. 공백만으로는 나누지 않는다
            base = value_end + paren.start(1)
            parts = []
            for seg in re.finditer(r"[^,·/]+", paren.group(1)):
                cut = seg.start()
                for jm in re.finditer(r"(?:와|과|및|이나|또는)(?=\s)", seg.group(0)):
                    parts.append((cut, seg.start() + jm.start()))
                    cut = seg.start() + jm.end()
                parts.append((cut, seg.end()))
            parts = [(i + len(t) - len(t.lstrip()), j - (len(t) - len(t.rstrip())))
                     for i, j in parts for t in [paren.group(1)[i:j]] if t.strip()]
            items = [(i + base, j + base) for i, j in parts] if len(parts) >= 2 else []
    spans = [_to_span(offsets, i, j) for i, j in items]
    by_obj: dict[str, dict] = {}
    for m in matches:
        ts = m.get("term_span")
        if (m["polarity"] != "pos" or m["obj_id"] == "ITEM" or m.get("match_method") == "complement"
                or m.get("condition_id") not in (None, *list_ids) or not ts):
            continue
        if any(a <= ts[0] and ts[1] <= b for a, b in other_spans):
            continue
        word = nospace(m["term"])
        if word in excluded:
            continue
        item = next((k for k, s in enumerate(spans) if s and s[0] <= ts[0] and ts[1] <= s[1]), None)
        if item is None:
            continue
        got = by_obj.setdefault(m["obj_id"], {})
        if item not in got and word not in {w for w, _ in got.values()}:  # 열거 항목 하나는 한 번, 같은 낱말도 한 번만 센다
            got[item] = (word, m["name_raw"])
    best = max(by_obj.values(), key=len, default={})
    return [name for _, name in best.values()] if len(best) >= 2 else []


def _is_byeol(c: dict) -> bool:
    """분류 "X별" 조건(기준어 사전의 분류 기준 값이 아닌 것)."""
    return c["axis"] == "classification" and c["value"] not in rules.BASIS_AXES["classification"]


def _linked_members(state: dict, c: dict, matches: list) -> list:
    """"X별" 조건의 연결 열거 값. 다른 조건의 범위는 빼고, 열거(cond_breakdown)는 "등 X별"로 이어지면 X의 예이므로 빼지 않는다."""
    others = [o["span"] for f in COND_ALL if f != "cond_breakdown" for o in state.get(f, [])
              if o.get("span") and o["condition_id"] != c["condition_id"]]
    excluded = {nospace(o.get("text") or o.get("quote") or "") for o in state.get("cond_exclude", [])} - {""}
    lists = frozenset(o["condition_id"] for o in state.get("cond_breakdown", []) if o.get("kind") == "list")
    return _byeol_linked(state["query"], state["offsets"], c, matches, others, excluded, lists)


_COMBINED_VALUE = re.compile(r"\S\s*[-×_/]\s*\S")  # 묶음 값 표기: 20대-수도권, 일반정부-피용자보수


def cross_axis_support(state: dict, req: dict, tid: str) -> dict | None:
    """교차 분류 지원 판정 J-b (j4_cross_axis, 기록 전용). 요구에 분류 "X별" 조건이 둘 이상일 때, 표가 그 분류들을
    서로 다른 축으로 줄 수 있는지 본다. 요청 의도(교차인지 각각인지)는 판정하지 않는다. 순위·충족에는 쓰지 않는다.

    조건마다 일치 근거가 있는 축을 모은다: 이름에 X가 든 분류축, X로 시작하거나 X와 같은 분류값의 축,
    X별에 연결된 열거 값의 축. 표명만으로 맞은 조건은 축 근거가 없다.
    - supported: 조건마다 서로 다른 축을 하나씩 줄 수 있다. 축 구조상의 지원 근거일 뿐, 모든 조합의 값이 실제로
      있다는 뜻이 아니다.
    - single_axis: 모든 조건이 축 근거를 가졌지만 서로 다른 축을 줄 수 없다(같은 축의 나열 값). 표가 요청을 지원하지
      못한다는 단정이 아니다(각각 요청이면 이 표로 충분할 수 있다).
    - unknown: 일치하지 않는 조건이 있거나(not_matched), 축 근거가 없는 조건이 있거나(no_axis_evidence), 공유 축에
      낱말을 '-'·'×'·'_'·'/'로 묶은 값이 있어 나열인지 교차인지 모른다(combined_values, '·'는 한 값 안의 나열이라 제외).
    서로 다른 "X별" 값이 둘 미만이면 None."""
    ids = set(req["condition_ids"] + req["shared_condition_ids"])
    conds, seen = [], set()
    for c in state["cond_basis"]:
        if c["condition_id"] in ids and _is_byeol(c) and c["value"] not in seen:  # 같은 "X별"이 두 번 잡혀도 분류는 하나다
            seen.add(c["value"])
            conds.append(c)
    if len(conds) < 2:
        return None
    meta, rows = state["meta"][tid], state["fit_basis"].get(tid, {})
    matches = state["matched_items"].get(tid, [])
    axes = {}
    for c in conds:
        stem = c["value"][:-1]
        got = {d["obj_id"] for d in meta["dims"] if stem in nospace(d["obj_nm"] or "")}
        got |= {it["obj_id"] for it in meta["items"] if it["name_nospace"] == stem or it["name_nospace"].startswith(stem)}
        members = _linked_members(state, c, matches)
        got |= {it["obj_id"] for it in meta["items"] if it["name_raw"] in members}
        axes[c["condition_id"]] = sorted(got)
    cids = [c["condition_id"] for c in conds]
    if any(rows.get(cid, {}).get("status") != "match" for cid in cids):
        status, reason = "unknown", "not_matched"
    elif any(not axes[cid] for cid in cids):
        status, reason = "unknown", "no_axis_evidence"
    elif any(len(set(pick)) == len(pick) for pick in itertools.product(*(axes[cid] for cid in cids))):
        status, reason = "supported", "distinct_axes"
    elif any(_COMBINED_VALUE.search(it["name_raw"] or "") for it in meta["items"]
             if it["obj_id"] in {a for cid in cids for a in axes[cid]}):
        status, reason = "unknown", "combined_values"  # 같은 축에 묶음 값(예: 부문-항목)이 있으면 나열인지 교차인지 모른다
    else:
        status, reason = "single_axis", "shared_axis"
    return {"status": status, "reason": reason, "condition_ids": cids, "axes": axes}


def _fit_basis(c: dict, meta: dict, title: str, paths: str, item_names: list, dim_names: list,
               members: list | None = None) -> tuple[str, str, str, str]:
    """기준어 하나 → (status, role, source, 근거 문장). 표명 > 경로 > 분류값 순으로 본다.
    members: "X별"에 연결된 분류값 이름(_byeol_linked). 근거가 없을 때 괄호의 다른 "Y별"만으로는
    conflict로 하지 않고 unknown이다(확인하지 못한 것을 어긋남으로 확정하지 않는다)."""
    axis, value = c["axis"], c["value"]
    forms = rules.basis_forms(axis, value)
    others = [f for v, fs in rules.BASIS_AXES.get(axis, {}).items() if v != value for f in fs]
    if axis == "classification" and value not in rules.BASIS_AXES["classification"]:
        # "X별": 표명 괄호의 다른 "Y별"이 반대 기준이다. 분류축 이름에 X가 있으면 표 안에서 나눌 수 있다
        stem = value[:-1]
        if value in title:
            return "match", "select", "title", f"표명에 '{value}'"
        if any(stem in d for d in dim_names) or any(n == stem or n.startswith(stem) for n in item_names):
            return "match", "handoff", "item", f"분류축·분류값에 '{stem}'"
        if re.search(r"[가-힣]{2,}별", re.sub(r"^[^(]*", "", title)):
            if members:  # X별에 연결된 열거 값이 이 표의 한 축 값이다
                return "match", "handoff", "item", f"분류값 '{'·'.join(members[:4])}'"
            return "unknown", "select", "none", ""  # 확인하지 못한 것을 어긋남으로 확정하지 않는다
        return "unknown", "select", "none", ""
    if _has_any(title, forms):
        return "match", "select", "title", f"표명에 '{value}'"
    if others and _has_any(title, others):
        return "conflict", "select", "title", f"표명이 반대 기준({title})"
    if _has_any(paths, forms):
        return "match", "select", "path", f"경로에 '{value}'"
    if axis in rules.TITLE_ONLY_AXES:
        return "unknown", "select", "none", ""
    if axis == "currency" and value in rules.CURRENCY_ITEM:
        return _fit_currency_item(value, meta)
    in_items = [n for n in item_names if _has_any(n, forms)]
    if in_items:
        return "match", "handoff", "item", f"분류값 '{in_items[0]}'"
    if others and any(_has_any(n, others) for n in item_names):
        return "conflict", "select", "item", "분류값에 반대 기준만 있음"
    return "unknown", "select", "none", ""


# ---- J4의 B 전달 조건 판정 (Q4 2단계). 표 가르기 기준어 판정(_fit_basis)과 나눠 둔다. 노드 분리는 4단계

def _fit_currency_item(value: str, meta: dict) -> tuple[str, str, str, str]:
    """통화가 표명·경로에 없을 때 분류값을 통화 표기 문법(rules.CURRENCY_ITEM)으로 본다."""
    raw = [it["name_raw"] or "" for it in meta["items"]]
    hits = [n for n in raw if rules.CURRENCY_ITEM[value].search(n)]
    if hits:
        return "match", "handoff", "item", f"분류값 '{hits[0]}'"
    if value in ("달러", "원화") and any(rules.FOREIGN_ITEM.search(n) for n in raw):
        # 달러: 외화에 달러가 들었는지 B가 본다. 원화: 외화 분류값이 반대 근거라 단위(원 환산 표시)로 일치시키지 않는다
        return "unknown", "handoff", "item", "분류값에 외화만 있음(통화별 값은 B가 확인)"
    if any(p.search(n) for v, p in rules.CURRENCY_ITEM.items() if v != value for n in raw):
        return "conflict", "select", "item", "분류값에 반대 기준만 있음"
    cur, unit = rules.unit_currency(meta)  # 표명·경로·분류값에 통화 근거가 없을 때만 표 단위를 본다
    if cur == value and value != rules.DEFAULT_CURRENCY:
        return "match", "select", "unit", f"단위 '{unit}'"
    if cur and cur != value and value in rules.UNIT_CURRENCY.values():
        # 반대 통화 단위는 충돌로 하지 않는다: 환산 자료가 있는지는 B가 본다
        return "unknown", "select", "unit", f"단위 '{unit}'(다른 통화)"
    return "unknown", "select", "none", ""


def _fit_handoff_only(c: dict, item_names: list) -> tuple | None:
    """비교 기준·계산 요청: 분류값에 있으면 그 근거만 남기고, 없으면 판정하지 않는다(None).
    기준어 뒤에 한글이 이어지면 다른 낱말이다 ("전기비데")."""
    pats = [re.escape(nospace(f)) + r"(?![가-힣])" for f in rules.basis_forms(c["axis"], c["value"])]
    hits = [n for n in item_names if any(re.search(p, n) for p in pats)]
    return ("match", "handoff", "item", f"분류값 '{hits[0]}'", "itm", "ITM.ITM_NM") if hits else None


def _fit_unit(c: dict, units: set) -> tuple:
    """단위는 B 전달 전용이다. 부분 문자열이 아니라 배율·바탕으로 비교하고, 환산 여부는 정하지 않는다 (Codex F05)."""
    if not units:  # 단위가 메타에 없으면 unknown (docs/01 4절 J4)
        return "unknown", "handoff", "none", "", "unit", ""
    verdict = rules.compare_unit(c["value"], sorted(units))
    if verdict == "same":
        return "match", "handoff", "item", f"단위 '{c['value']}'", "unit", "UNIT.UNIT_NM"
    if verdict == "differ":
        return "conflict", "handoff", "item", f"단위 {sorted(units)[:3]}", "unit", "UNIT.UNIT_NM"
    text = f"단위 {sorted(units)[:3]} (배율 다름, 환산 필요)" if verdict == "rescale" else ""
    return "unknown", "handoff", "item" if text else "none", text, "unit", "UNIT.UNIT_NM" if text else ""


def j4(state, runtime: Ctx) -> dict:
    """후보 × 조건별 기준어·단위·제외 판정 (단계 3). 불일치도 지우지 않고 status로 남긴다."""
    fit, evidence = {}, {}
    for tid, meta in state["meta"].items():
        title = nospace(meta["title"] or "")
        paths = nospace(" ".join(meta["catalog_paths"]))
        item_names = [it["name_nospace"] for it in meta["items"]]
        dim_names = [nospace(d["obj_nm"] or "") for d in meta["dims"]]
        matches = state["matched_items"].get(tid, [])
        rows = {}

        def put(cid, status, role, source, text, kind, field):
            eids = []
            if source != "none":
                eid = f"J4:{tid}:{cid}"
                evidence[eid] = {"table_id": tid, "kind": kind, "text": text, "source_field": field,
                                 "source_snapshot": meta["source_snapshot"],
                                 "stance": {"match": "support", "conflict": "against"}.get(status, "unknown")}
                eids.append(eid)
            rows[cid] = {"status": status, "role": role, "source": source, "evidence_ids": eids}

        for c in state["cond_basis"]:
            if c["axis"] in rules.HANDOFF_ONLY_AXES:  # B 전달 조건 (Q4 2단계)
                row = _fit_handoff_only(c, item_names)
                if row:
                    put(c["condition_id"], *row)
                continue
            members = None
            if _is_byeol(c):
                members = _linked_members(state, c, matches)
            status, role, source, text = _fit_basis(c, meta, title, paths, item_names, dim_names, members)
            if c["polarity"] == "neg" and role == "select" and status in ("match", "conflict"):
                status = "conflict" if status == "match" else "match"  # "X 제외"는 X 전용 표와 어긋난다
            elif c["polarity"] == "neg" and role == "handoff" and status == "match":
                # "X 아닌": 분류값에 반대 기준이 있으면 그것을 고르고 X는 뺀다. X만 있으면 X 전용 표다 (Codex F01)
                others = [f for v, fs in rules.BASIS_AXES.get(c["axis"], {}).items() if v != c["value"] for f in fs]
                if others and any(_has_any(n, others) for n in item_names):
                    text = f"{text}은 B에서 빼고 반대 기준 분류값을 고른다"
                else:
                    status, role, text = "conflict", "select", f"{text}만 있어 뺄 수 없음"
            kind, field = {"title": ("title", "TBL.TBL_NM"), "path": ("path", "catalog_paths"),
                           "item": ("itm", "ITM.ITM_NM"), "unit": ("unit", "UNIT.UNIT_NM")}.get(source, ("title", ""))
            put(c["condition_id"], status, role, source, text, kind, field)
        units = {u for u in [meta["unit"], (meta["item_itm"] or {}).get("unit")] + [it["unit"] for it in meta["items"]] if u}
        for c in state["cond_unit"]:
            put(c["condition_id"], *_fit_unit(c, units))
        for c in state["cond_exclude"]:
            comp = [m for m in matches if m["condition_id"] == c["condition_id"] and m["match_method"] == "complement"]
            name = nospace(c["text"])
            if comp:  # 보완 분류값이 있으면 그 값을 고르면 된다 (R1)
                put(c["condition_id"], "match", "handoff", "item", f"보완 분류값 '{comp[0]['name_raw']}'", "itm", "ITM.ITM_NM")
            elif name in title:
                put(c["condition_id"], "conflict", "select", "title", f"표명이 '{c['text']}' 전용", "title", "TBL.TBL_NM")
            elif any(name == n for n in item_names):
                put(c["condition_id"], "match", "handoff", "item", f"분류값 '{c['text']}'을 B에서 뺀다", "itm", "ITM.ITM_NM")
            else:
                put(c["condition_id"], "unknown", "handoff", "none", "", "itm", "")
        fit[tid] = rows
    return {"fit_basis": fit, "evidence": evidence}


_PERIOD_ORDER = ("covered", "unknown", "partial", "outside")  # 요구 하나의 기간은 판정 주기 중 가장 좋은 것


def _judge(fp: dict | None, fb: dict, matches: list, req: dict, ambiguous: frozenset = frozenset()) -> dict:
    """고른 표 하나로 요구 하나를 판정한다 (T3 selected, J5 막힘·조건 수, T4가 같은 규칙을 쓴다).

    unmet: 주기 conflict, 요청 기간이 판정 주기 모두에서 outside, select 역할 조건 conflict.
    partial: 기간 partial, handoff 역할 조건 conflict.  unknown: 주기·기간·조건 unknown, 분해 이름 미매치.
    """
    ids = req["condition_ids"] + req["shared_condition_ids"]
    codes, gaps, covered = [], [], []
    level = 0  # 0 met, 1 unknown, 2 partial, 3 unmet

    def note(lv, code, **gap):
        nonlocal level
        level = max(level, lv)
        if code not in codes:
            codes.append(code)
        gaps.append({"code": code, **gap})

    if fp is not None:
        wanted = fp["requested"]["frequencies"]
        rows = {f: v for f, v in fp["period_by_frequency"].items() if not wanted or f in wanted}
        if fp["frequency"]["status"] == "conflict":
            note(3, "frequency_conflict", requested=wanted, available=fp["frequency"]["available"])
        elif fp["frequency"]["status"] == "unknown":
            note(1, "frequency_unknown")
        if rows:
            best = min(rows.values(), key=lambda v: _PERIOD_ORDER.index(v["status"]))
            covered = [{"freq": f, "coverage": v["coverage"]} for f, v in rows.items() if v["status"] in ("covered", "partial")]
            status = best["status"]
            if fp["requested"]["period"] is not None and status != "covered":
                lv, code = {"unknown": (1, "period_unknown"), "partial": (2, "period_partial"),
                            "outside": (3, "period_outside")}[status]
                note(lv, code, reason=best.get("reason"), requested=fp["requested"]["period"])
    for cid in ids:
        if cid in ambiguous:  # 귀속 미확정: 충돌이어도 unmet·partial로, 맞아도 met 근거로 쓰지 않는다 (7번 1단계)
            note(1, "condition_ambiguous", condition_id=cid)
            continue
        if cid in fb:
            row = fb[cid]
            if row["status"] == "conflict":
                if row["role"] == "select":
                    note(3, "condition_conflict", condition_id=cid)
                else:
                    note(2, "condition_conflict_handoff", condition_id=cid)
            elif row["status"] == "unknown":
                note(1, "condition_unknown", condition_id=cid)
        elif cid.startswith("R") and not any(m["condition_id"] == cid and m["polarity"] == "pos" for m in matches):
            note(1, "breakdown_unmatched", condition_id=cid)
    if req.get("status") == "unresolved":  # 귀속이 애매한 요구는 met·high로 올리지 않는다 (Codex F03)
        note(1, "requirement_unresolved")
    status = ("met", "unknown", "partial", "unmet")[level]
    return {"status": status, "covered": covered, "gaps": gaps, "reason_codes": codes}


def _covered_conditions(fb: dict, matches: list, ambiguous: frozenset = frozenset()) -> tuple[set, set]:
    """맞춘 조건: J4 match인 조건, 그 밖에 분류값이 pos로 맞은 조건 (단계 3 진단 순위와 같은 셈).
    귀속이 애매한 조건은 맞아도 세지 않는다 (7번 1단계)."""
    basis = {cid for cid, v in fb.items() if v["status"] == "match"} - ambiguous
    items = {m["condition_id"] for m in matches if m["condition_id"] and m["polarity"] == "pos"} - basis - ambiguous
    return basis, items


def _title_stem(title: str) -> str:
    """표명 일치에 쓰는 이름: 첫 괄호 앞, 공백 제거 (23절. 중첩 괄호에도 같은 결과)."""
    return nospace(title.split("(")[0])


def _title_condition_forms(state) -> dict:
    """표명에서 찾을 조건 표현 (23절). 뺄 값(neg)은 넣지 않는다. 분류 기준 "X별"은 표명에 X로도 쓰인다."""
    forms = {}
    for c in state["cond_basis"]:
        if c.get("polarity", "pos") != "pos":
            continue
        texts = {nospace(f) for f in rules.basis_forms(c["axis"], c["value"])}
        texts |= {t[:-1] for t in texts if t.endswith("별") and len(t) >= 3}
        forms[c["condition_id"]] = {t for t in texts if len(t) >= 2}
    for c in state["cond_breakdown"]:
        t = nospace(c["text"])
        if len(t) >= 2:
            forms[c["condition_id"]] = {t}
    return forms


def j5(state, runtime: Ctx) -> dict:
    """후보 점수 (단계 4, 23절). 점수 = 검색 점수 + 10 × 맞춘 조건 수 + 10 × 표명에 드러난 조건 수
    + title_bonus × (표명이 질문에 그대로 있음) − 100 × (모든 요구가 막힘).

    표명 일치(기본 15)는 조건 하나보다 앞서고 둘보다는 뒤다: 질문이 말한 주제가 주변 조건 하나를 이긴다.

    맞춘 조건이 하나 많으면 검색 점수(채널 둘 이상이어도 3 이하)보다 앞선다. 같은 점수면 풀 순서다.
    불일치는 지우지 않고 flags에 남긴다. 판정이 같은 표끼리 표명 괄호 앞 이름이 같으면 기본값 정책(D3)이
    필요하므로 undecided이고, D3 전에는 적용하지 않는다.
    """
    meta, reqs = state["meta"], state["requirements"]
    config = runtime.context.config
    qn = state["query_nospace"]
    cond_forms = _title_condition_forms(state)
    measure_index = measure.index_for(runtime.context.catalog)
    phrases = measure.rule_phrases(state, measure_index)
    if len(reqs) == 1 and reqs[0].get("source") == "q6g":
        model_phrases, _ = measure.model_phrases(state["question"], [{"text": reqs[0]["measure"],
            "spans": reqs[0]["quote_spans"]}], state, measure_index)
        if model_phrases:
            phrases = model_phrases
    measure_ev = {}
    rows = []
    unsure = ambiguous_conditions(state)
    for p in state["pool"]:
        tid = p["table_id"]
        fb, matches = state["fit_basis"].get(tid, {}), state["matched_items"].get(tid, [])
        verdicts = {r["scope_id"]: _judge(state["fit_period"].get(tid, {}).get(r["scope_id"]), fb, matches, r, unsure)
                    for r in reqs}
        blocked = bool(reqs) and all(v["status"] == "unmet" for v in verdicts.values())
        basis, items = _covered_conditions(fb, matches, unsure)
        title = meta[tid]["title"] or ""
        tn = nospace(title)
        by_title = {cid for cid, fs in cond_forms.items() if any(f in tn for f in fs)} - basis - items - unsure
        stem = _title_stem(title)
        title_hit = len(stem) >= 2 and stem in qn
        search = p["fused_score"] or 0.0
        flags = ["sibling"] if p["entered_by"] == "sibling" else []
        flags += ["blocked"] if blocked else []
        flags += [f"{c}:{sid}" for sid, v in verdicts.items() for c in v["reason_codes"]]
        components = {"search": search, "period": -100.0 if blocked else 0.0,
                      "basis": 10.0 * len(basis), "items": 10.0 * len(items),
                      "title_conditions": 10.0 * len(by_title),
                      "title": TITLE_BONUS if title_hit else 0.0}
        # Prefix-condition evidence ('중앙정부-최종소비지출'): condition evidence only, never an exact item binding.
        prefix_support = item_prefix_support(state, meta[tid], basis | items | by_title | unsure)
        if prefix_support:
            components["item_prefix"] = 10.0 * len(prefix_support)
            flags.append("cx_item_prefix")
            measure_ev[f"cx_item_prefix:{tid}"] = item_prefix_evidence(tid, prefix_support, meta[tid])
        assessment = measure.assess(phrases, tid, meta[tid], measure_index)
        components["measure"] = measure.COMPONENT_WEIGHT * measure.VALUE[assessment["status"]]
        flags.append("measure_" + assessment["status"])
        measure_ev[f"measure:{tid}"] = measure.evidence_row(tid, assessment, meta[tid]["source_snapshot"])
        rows.append({
            "table_id": tid, "score": round(sum(components.values()), 6), "components": components,
            "flags": flags, "policy_state": "none_needed", "policy_name": None,
            "_tie": (blocked, len(basis) + len(items) + len(by_title) + len(prefix_support), _BRACKET.sub(" ", meta[tid]["title"] or tid).strip()),
            "_rank": p["rank"],
        })
    ties = {}
    for r in rows:
        ties.setdefault(r["_tie"], []).append(r)
    for group in ties.values():
        if len(group) > 1:
            for r in group:
                r["policy_state"] = "undecided"  # 기본값 정책이 없으면 순서는 검색 순서 그대로 둔다
    rows.sort(key=lambda r: (r["_tie"][0], -r["score"], r["_rank"]))  # 막힌 표는 먼저 뒤로 (Codex F06)
    # Leading semantic tie whose extra axis has no total: reorder only within that tie (no score change).
    measure_ev.update(scope_total_order(rows, state, meta, config.as_of))
    return {"ranked": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows], "evidence": measure_ev}


def j7(state, runtime: Ctx) -> dict:
    """후보 카드 (단계 4). 카드 수 k(CANDIDATE_CARDS)도 여기서 정해 State에 남긴다(옛 J6 병합)."""
    return {"cards": card_rows(state, CANDIDATE_CARDS), "k": CANDIDATE_CARDS}


def card_rows(state, k: int) -> list:
    """상위 k개 순위 표의 카드: 표 ID·표명·경로·수록 범위, 형제와 표명 차이, 기존 근거(against·unknown 포함)."""
    meta, evidence, relations = state["meta"], state["evidence"], state["relations"]
    for f in ("matched_items", "fit_period", "fit_basis"):
        state[f]
    cards = []
    for i, r in enumerate(state["ranked"][:k]):
        tid = r["table_id"]
        m = meta[tid]
        siblings = []
        for rel in relations:
            if tid in rel["anchor_ids"] and rel["table_id"] != tid:
                other = meta.get(rel["table_id"], {}).get("title")
                siblings.append({"table_id": rel["table_id"], "relation_type": rel["relation_type"],
                                 "difference": f"{m['title']} / {other}" if other else None})
        stance_order = {"against": 0, "unknown": 1, "support": 2}
        reasons = sorted(
            ({"evidence_id": eid, "text": e["text"], "stance": e["stance"]} for eid, e in evidence.items() if e["table_id"] == tid),
            key=lambda x: (stance_order[x["stance"]], x["evidence_id"]),
        )
        cards.append({
            "card_id": f"c{i + 1}", "table_id": tid, "title": m["title"],
            "path": m["catalog_paths"][0] if m["catalog_paths"] else None,
            "coverage_by_freq": {f: [v["start"], v["end"]] for f, v in m["prd_by_freq"].items()},
            "siblings": siblings, "reasons": reasons,
        })
    return cards
