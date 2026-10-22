"""Q stage nodes (Q1–Q7): normalisation, condition extraction, requirements and search queries.

Moved verbatim from nodes.py (node-structure N5); nodes.py assembles NODES."""

from __future__ import annotations

import os
import re
import unicodedata

from . import rules
from .catalog import nospace
from .contract import make_issue
from .node_common import Ctx, _to_span


def q1(state, runtime: Ctx) -> dict:
    """원문 보존, 정리본(NFKC·공백 하나), 공백 제거본, 정리본 → 원문 위치표."""
    question = state["question"]
    chars, offsets = [], []
    prev_space = True
    for i, ch in enumerate(question):
        for out in unicodedata.normalize("NFKC", ch):
            if out.isspace():
                if prev_space:
                    continue
                out, prev_space = " ", True
            else:
                prev_space = False
            chars.append(out)
            offsets.append(i)
    while chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    query = "".join(chars)
    return {"query": query, "query_nospace": nospace(query), "offsets": offsets}


def _query_range(offsets: list[int], span) -> tuple[int, int]:
    """원문 Span을 정리본 색인 구간으로 되돌린다 (offsets의 역)."""
    first = next(k for k, o in enumerate(offsets) if o >= span[0])
    last = max(k for k, o in enumerate(offsets) if o < span[1])
    return first, last + 1


def _condition(prefix: str, n: int, state, i: int, j: int, status: str, **fields) -> dict:
    span = _to_span(state["offsets"], i, j)
    quote = state["question"][span[0]:span[1]] if span else state["query"][i:j]
    return {"condition_id": f"{prefix}{n}", "status": status, "quote": quote, "span": span, **fields}


def q2(state, runtime: Ctx) -> dict:
    """기간: 범위·이후·최근·연도 별칭·시점. 상대 기간은 as_of가 있을 때 연도만 푼다 (D7 전 규칙)."""
    view = {"question": state["question"], "query": state["query"], "offsets": state["offsets"]}
    state["query_nospace"]
    rows = []
    for n, p in enumerate(rules.find_periods(state["query"], runtime.context.config.as_of), 1):
        extra = {k: p[k] for k in ("amount", "unit", "relative") if k in p}
        rows.append(_condition("P", n, view, p["i"], p["j"], p["status"], kind=p["kind"], start=p["start"], end=p["end"], **extra))
    return {"cond_period": rows}


def q3(state, runtime: Ctx) -> dict:
    """명시된 주기만 (v1.8 R5 사전, rules.FREQUENCY_PATTERNS). 반복어(매달·분기마다)는 넣고 기간 안의 말은 뺀다."""
    view = {"question": state["question"], "query": state["query"], "offsets": state["offsets"]}
    state["query_nospace"]
    rows = [
        # 기간 끝 반복(분기말마다)은 끝 시점을 남긴다. B가 말일 값을 고를 때 쓴다 (Q3·Q4 공통 규칙)
        _condition("F", n, view, i, j, "explicit", value=value,
                   **({"anchor": "end"} if rules.frequency_anchor(state["query"], i, j) else {}))
        for n, (value, i, j) in enumerate(rules.find_frequencies(state["query"]), 1)
    ]
    return {"cond_frequency": rows}


def q4(state, runtime: Ctx) -> dict:
    """기준어·단위와 원문 위치 (단계 3). role은 J4가 후보별로 정한다.

    탐지 책임: find_basis는 표 선택에 쓰는 기준어(통화·사전 비교어 포함), find_handoff는 이후 데이터
    조회·처리에 쓰는 조건(비교 기준 자연어·계산 요청, origin="handoff_rule"), find_units는 단위다.
    같은 기준어도 표에 따라 선택(select)·전달(handoff) 역할이 갈리므로 노드를 나누지 않는다(10/15 결정).
    """
    query, offsets = state["query"], state["offsets"]
    view = {"question": state["question"], "query": query, "offsets": offsets}
    state["query_nospace"]
    basis = []
    found = rules.find_basis(query)  # 표 가르기 기준어 (통화·사전 비교어 포함)
    # 조회·처리용 조건(비교 기준 자연어·계산 요청)은 따로 찾아 원문 순서로 합친다 (Q4 2단계). 노드는 하나로 둔다
    handoff = rules.find_handoff(query, [(i, j) for _, _, i, j in found])
    for axis, value, i, j, extra in sorted([(*f, {}) for f in found] + [(*h, {"origin": "handoff_rule"}) for h in handoff],
                                           key=lambda x: x[2]):
        tail = query[j:j + 12]
        neg = r"\s*(?:을|를|이|가|은|는)?\s*(?:뺀|빼고|제외|아니|아닌|하지\s*않|안\s*한)"
        polarity = "neg" if re.match(neg, tail) else "pos"
        basis.append(_condition("B", len(basis) + 1, view, i, j, "explicit", axis=axis, value=value, polarity=polarity,
                                **extra))
    units = [_condition("U", n, view, i, j, "explicit", value=value) for n, (value, i, j) in enumerate(rules.find_units(query), 1)]
    return {"cond_basis": basis, "cond_unit": units}


class _CatalogNames:
    """Q5가 나열을 다듬을 때 쓰는 운영 카탈로그 이름 조회. 열쇠는 공백·가운뎃점을 지운 이름이다."""

    def __init__(self, catalog):
        self._items: dict[str, dict[str, str]] = {}
        self._titles: set[str] = set()
        self._tables: list[str] = []  # 표마다 표명·분류값 이름을 이은 열쇠 (두 이름이 한 표에 함께 있는지)
        for tid, meta in (getattr(catalog, "meta_store", None) or {}).items():
            self._titles.add(self.key(meta.get("title", "").split("(")[0]))
            for it in meta.get("items", []):
                self._items.setdefault(self.key(it["name_raw"]), {}).setdefault(tid, it["name_raw"])
            self._tables.append("|".join([self.key(meta.get("title", ""))] +
                                         [self.key(it["name_raw"]) for it in meta.get("items", [])]))

    @staticmethod
    def key(text: str) -> str:
        return re.sub(f"[{rules._DOTS}]", "", nospace(text))

    def tables(self, text: str) -> set:
        return set(self._items.get(self.key(text), {}))

    def item(self, text: str, tables=None) -> str | None:
        """같은 이름의 분류값 이름(원문). tables를 주면 그 표 안에서만 찾는다."""
        rows = self._items.get(self.key(text), {})
        tid = next((t for t in sorted(rows) if tables is None or t in tables), None)
        return rows[tid] if tid else None

    def known(self, text: str) -> bool:
        k = self.key(text)
        return k in self._items or k in self._titles

    def together(self, a: str, b: str) -> bool:
        """두 말이 같은 표의 표명·분류값 안에 함께 있는지 ("은행"과 "총수신금리")."""
        ka, kb = self.key(a), self.key(b)
        return len(ka) >= 2 and len(kb) >= 2 and any(ka in t and kb in t for t in self._tables)

    def in_titles(self, text: str) -> bool:
        """표명 안에 그대로 있는 말인지 ("생산자물가"는 "생산자물가지수" 안에 있다)."""
        k = self.key(text)
        return len(k) >= 2 and any(k in t for t in self._titles)


_NAMES_CACHE: list = []


def _catalog_names(catalog) -> _CatalogNames:
    hit = next((n for owner, n in _NAMES_CACHE if owner is catalog), None)
    if hit is None:
        hit = _CatalogNames(catalog)
        _NAMES_CACHE.append((catalog, hit))
        del _NAMES_CACHE[:-2]
    return hit


def _split_by_basis(req: dict, basis: list, conds: list) -> list | None:
    """같은 축 기준어가 둘 이상이면(예: 명목과 실질, 신규취급액 기준과 잔액 기준) 값마다 요구를 하나씩 둔다 (단계 3). 같은 값이 되풀이되면 한 요구로 묶는다 (Codex F03).

    요구 text·span은 질문 전체이며, 그 축이 아닌 조건은 모두 공유한다. 부정(X이 아니라)은 나누지 않는다.
    """
    by_axis: dict[str, list] = {}
    for c in basis:
        # 분류 기준("연령별 지역별")은 한 표의 두 축이라 요구를 나누지 않는다 (Q5 개선 1, Q-10)
        if c["polarity"] == "pos" and c["axis"] != "classification":
            by_axis.setdefault(c["axis"], []).append(c)
    axes = [a for a, cs in by_axis.items() if len({c["value"] for c in cs}) >= 2]
    if not axes:
        return None
    axis = axes[0]
    by_value: dict[str, list] = {}
    for c in by_axis[axis]:
        by_value.setdefault(c["value"], []).append(c["condition_id"])
    split_ids = [cid for ids in by_value.values() for cid in ids]
    shared = [c["condition_id"] for c in conds if c["condition_id"] not in split_ids]
    # 두 축 이상이 함께 갈리면(예: 명목·실질 × 원계열·계절조정) 결합을 정하지 않고 unresolved로 둔다
    status = "unresolved" if len(axes) > 1 else req.get("status", "ok")
    return [dict(req, scope_id=f"s{n}", condition_ids=ids, shared_condition_ids=list(shared), status=status)
            for n, ids in enumerate(by_value.values(), 1)]


def _split_by_measure(query: str, offsets: list, breakdowns: list, conds: list, known,
                      names=None) -> tuple[list, list] | None:
    """지표 비교 경계(measure_pair "와/과")로 요구를 지표마다 나눈다 (7번 1단계, 2026-10-13).

    귀속은 rules.measure_scopes를 따른다. 애매한 조건은 모든 요구가 shared로 갖되(B 전달에 싣는다) Q5 issue
    requirement_unresolved(condition_id)로 남겨, J5·T3·T4가 그 조건을 가점·탈락·met 근거로 쓰지 않게 한다.
    """
    def loc(c):
        return _query_range(offsets, c["span"]) if c["span"] else (None, None)

    pairs = sorted((r for r in breakdowns if r.get("measure_pair")), key=lambda r: loc(r)[0])
    cuts = [loc(b)[0] for a, b in zip(pairs, pairs[1:]) if re.fullmatch(r"\s*(?:와|과)\s*", query[loc(a)[1]:loc(b)[0]])]
    plan = rules.measure_scopes(query, cuts, [(c["condition_id"], c["condition_id"][0], *loc(c), c.get("axis"))
                                              for c in conds], known)
    if plan is None:
        return None
    shared = plan["common"] + plan["ambiguous"]
    cond_ranges = [loc(c) for c in conds if c["span"] and not c["condition_id"].startswith("R")]
    requirements = []
    for n, ((i, j), own) in enumerate(zip(plan["segments"], plan["own"]), 1):
        piece = re.sub(r"\s*(?:와|과)\s*$", "", query[i:j])  # 구절 사이 "와/과"는 어느 요구의 말도 아니다
        text = piece.strip(" ,")
        i += len(piece) - len(piece.lstrip(" ,"))
        start = i
        if n > 1 and (head := _shared_head(query, [loc(r) for r in pairs], i, i + len(text), cond_ranges, names)):
            start, words = head  # "은행 총대출금리와 총수신금리": 앞 이름의 꾸밈말을 뒤 이름도 쓴다
            text = f"{words} {text}"
        span = _to_span(offsets, start, i + len(piece.strip(" ,"))) if text else None
        if not span:
            return None
        requirements.append({"scope_id": f"s{n}", "text": text, "span": span, "status": "ok",
                             "condition_ids": own, "shared_condition_ids": list(shared)})
    notes = [make_issue("requirement_unresolved", "Q5", "지표가 둘 이상인 질문에서 귀속이 애매한 조건", condition_id=cid)
             for cid in plan["ambiguous"]]
    return requirements, notes


def _measure_words(query: str, i: int, j: int, cond_ranges: list, after_cond: bool = False) -> list[tuple[int, int]]:
    """구간 안의 지표 낱말 위치: 조건 원문, 조건 뒤 "기준~" 말, 잇는 말·조사만 남는 낱말, 요청·"각각" 말을 뺀다."""
    request = [(m.start(), m.end()) for m in rules._REQUEST.finditer(query)]
    out = []
    for m in re.finditer(r"\S+", query[i:j]):
        a, b = i + m.start(), i + m.end()
        if any(x < b and a < y for x, y in cond_ranges):
            after_cond = True
            continue
        word = re.sub(r"(?:와|과|및)$", "", rules._PARTICLE.sub("", m.group().strip(".,?!")))
        if (after_cond and rules._BASIS_WORD.search(word)) or not word or word in ("및", "그리고") \
                or rules._ALL_MEASURES.search(word) or any(x <= a < y for x, y in request):
            after_cond = after_cond and rules._BASIS_WORD.search(word) is not None
            continue
        after_cond = False
        out.append((a, b))
    return out


def _share_tail_measure(query: str, offsets: list, requirements: list, placed: list, conds: list) -> None:
    """기간으로 나눈 요구 가운데 지표 낱말이 없는 요구는 마지막 구절의 끝 지표 구절을 함께 쓴다 (Q5 마무리).

    "2014년 A법 기준과 2015년 B법 기준 성장성 지표를 각각"에서 첫 요구는 조건뿐이다. 그 요구의 text에 공통 지표
    구절("성장성 지표")을 잇고 span은 그 구절까지 덮는다. 조건 귀속(condition_ids)은 바꾸지 않는다.
    """
    ranges = [_query_range(offsets, c["span"]) for c in conds if c["span"] and not c["condition_id"].startswith("R")]
    li, lj = placed[-1]
    last_cond = max((y for x, y in ranges if li <= x < lj), default=None)
    if last_cond is None:
        return
    tail = []
    for a, b in _measure_words(query, last_cond, lj, ranges, after_cond=True):
        word = query[a:b].rstrip(".,?!")
        tail.append((a, a + len(rules._PARTICLE.sub("", word))))
        if rules._PHRASE_END.search(word) or rules._PHRASE_END_SUBJECT.search(word):
            break
    if not tail:
        return
    ts, te = tail[0][0], tail[-1][1]
    for req, (i, j) in zip(requirements[:-1], placed[:-1]):
        if _measure_words(query, i, j, ranges):
            continue
        span = _to_span(offsets, i, te)
        if span:
            req.update(text=f"{req['text']} {query[ts:te]}", span=span)


_HEAD_STOP = re.compile(r"(?:으로|에서|을|를|이|가|은|는|로|에|도|와|과|만)[,.]*$")


def _shared_head(query: str, pairs: list[tuple[int, int]], i: int, j: int, cond_ranges: list, names) -> tuple[int, str] | None:
    """"X A와 B"에서 뒤 구절이 이름 B 하나뿐이면(비교 명사만 뒤따름) 앞 이름 A의 꾸밈말 X를 B도 쓴다 (Q5 마무리).

    X는 A 바로 앞의 조건이 아닌 낱말(최대 둘)이다. 조사로 끝나는 낱말·관형 동사에서 멈춘다("의"는 꾸밈이라 둔다).
    X와 B(또는 A와 겹치는 앞뒤 글자를 뺀 B의 고유 부분, "총수신금리"의 "수신")가 한 표의 표명·분류값에 함께 있을
    때만 공통으로 본다(카탈로그 근거). 아니면 None.
    """
    if names is None:
        return None
    rows = sorted(pairs)
    b = next((r for r in rows if r[0] == i), None)
    a = next((r for r in reversed(rows) if r[1] <= i), None)
    if b is None or a is None or not re.fullmatch(r"\s*(?:와|과)\s*", query[a[1]:b[0]]):
        return None
    rest = [w for w in query[b[1]:j].split() if w]
    if any(not rules._COMPARE.search(w) for w in rest):
        return None  # 뒤 구절에 제 꾸밈말이 있으면 나누지 않는다
    words = []
    for m in reversed(list(re.finditer(r"\S+", query[:a[0]]))):
        w = m.group()
        if any(x <= m.start() < y or x < m.end() <= y for x, y in cond_ranges) or _HEAD_STOP.search(w) \
                or rules._ADNOMINAL.search(w) or len(words) == 2:
            break
        words.insert(0, (m.start(), w.removesuffix("의")))
    if not words:
        return None
    head = " ".join(w for _, w in words)
    left, right = nospace(query[a[0]:a[1]]), nospace(query[b[0]:b[1]])
    pre = len(os.path.commonprefix([left, right]))
    suf = len(os.path.commonprefix([left[::-1], right[::-1]]))
    core = right[pre:len(right) - suf] if len(right) - pre - suf >= 2 else right  # "총수신금리" -> "수신"
    if not (names.together(head, right) or names.together(head, core)):
        return None
    return words[0][0], head


def ambiguous_conditions(state) -> frozenset:
    """Q5가 지표별 요구로 나누며 귀속을 정하지 못한 조건 (7번 1단계). 가점·탈락·met 근거로 쓰지 않는다."""
    issues = state["issues"]
    if any(r.get("source") == "q6g" for r in state["requirements"]):
        return frozenset()  # Q6G 요구는 자기 귀속을 쓴다
    return _q5_unsure(issues)


def _q5_unsure(issues) -> frozenset:
    return frozenset(i["condition_id"] for i in issues
                     if i["code"] == "requirement_unresolved" and i["node"] == "Q5" and i.get("condition_id"))


def q5(state, runtime: Ctx) -> dict:
    """분해·제외 조건과 요구 (단계 2). 요구는 기본 1개이고, 기간이 구절 일부에만 걸릴 때만 나눈다 (v1.8 R2)."""
    query, offsets = state["query"], state["offsets"]
    view = {"question": state["question"], "query": query, "offsets": offsets}
    periods = state["cond_period"]
    others = [c for f in ("cond_frequency", "cond_basis", "cond_unit") for c in state[f]]
    taken = [_query_range(offsets, c["span"]) for c in periods + others if c["span"]]

    def free(i: int, j: int) -> bool:
        return all(j <= a or i >= b for a, b in taken)

    names = _catalog_names(runtime.context.catalog)
    chains = rules.find_lists(query, lambda w: names.known(w) or names.in_titles(w))
    found = [x for x in rules.find_excludes(query) if free(x[1], x[2])]
    cond_taken = list(taken)
    # "식료품과 에너지를 뺀"의 앞 이름도 제외다 (개선 6)
    members = [(n, i, j, [f"{n}이외", f"{n}제외", f"{n} 제외"]) for n, i, j in
               rules.exclude_list_members(query, chains, found, cond_taken, names) if free(i, j)]
    excludes = []
    for name, i, j, hints in sorted(found + members, key=lambda x: x[1]):
        taken.append((i, j))
        excludes.append(_condition("X", len(excludes) + 1, view, i, j, "explicit", text=name, complement_hints=hints))
    breakdowns = []
    for part in rules.refine_lists(query, chains, cond_taken, names):
        # 다른 조건(기간 등)과 겹치거나 한 글자인 이름을 빼고도 둘 이상 남는 나열만 분해 조건이다.
        # 카탈로그 분류값 이름 하나로 되붙인 것은 하나여도 둔다
        kept = [r for r in part if len(nospace(r["text"])) >= 2 and free(r["i"], r["j"])]
        if len(kept) < 2 and not (kept and kept[0]["source"] == "catalog_merge"):
            continue
        for r in kept:
            extra = {"measure_pair": True} if r.get("measure_pair") else {}
            breakdowns.append(_condition("R", len(breakdowns) + 1, view, r["i"], r["j"], "explicit",
                                         text=r["text"], kind="list", source=r["source"], **extra))

    # 기간 위치는 Q2가 낸 span을 정리본 색인으로 되돌려 쓴다
    located = [dict(c, i=_query_range(offsets, c["span"])[0], j=_query_range(offsets, c["span"])[1]) for c in periods if c["span"]]
    cuts, ambiguous = rules.split_points(query, sorted(located, key=lambda c: c["i"]))
    conds = periods + others + breakdowns + excludes
    issues, requirements, placed = [], [], []
    bounds = [0, *cuts, len(query)]
    segments = [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]
    for n, (i, j) in enumerate(segments, 1):
        text = query[i:j].strip(" ,")
        while text and query[i] in " ,":
            i += 1
        span = _to_span(offsets, i, i + len(text)) if text else None
        if not span:
            continue
        own, shared = [], []
        for c in conds:
            pos = _query_range(offsets, c["span"])[0] if c["span"] else None
            if len(segments) == 1:
                own.append(c["condition_id"])
            elif c["condition_id"].startswith("P"):
                if pos is not None and i <= pos < j:
                    own.append(c["condition_id"])
            elif pos is not None and i <= pos < j and (n < len(segments) or c["condition_id"].startswith("B")):
                own.append(c["condition_id"])  # 기준어는 마지막 구절에 있어도 그 구절의 요구다 (단계 3)
            elif n == 1 and (pos is None or (pos >= segments[-1][0] and not c["condition_id"].startswith("B"))):
                # 위치가 없거나 마지막 구절 끝말(주기·단위 등)인 조건만 모든 요구가 함께 쓴다.
                # 가운데 구절의 조건은 그 구절 것이다 (Codex F03)
                shared.append(c["condition_id"])
        requirements.append({"scope_id": f"s{n}", "text": text, "span": span,
                             "status": "unresolved" if ambiguous else "ok",
                             "condition_ids": own, "shared_condition_ids": shared})
        placed.append((i, i + len(text)))
    if len(requirements) > 1:
        _share_tail_measure(query, offsets, requirements, placed, conds)
    measured = None
    if len(requirements) == 1 and not ambiguous and requirements[0]["status"] == "ok":
        measured = _split_by_measure(query, offsets, breakdowns, conds, lambda w: names.known(w) or names.in_titles(w),
                                     names)
    if measured:
        requirements, notes = measured
        issues.extend(notes)
    elif len(requirements) == 1 and not ambiguous:
        requirements = _split_by_basis(requirements[0], state["cond_basis"], conds) or requirements
    if len(requirements) > 1:
        shared = requirements[0]["shared_condition_ids"]
        for r in requirements[1:]:
            r["shared_condition_ids"] = list(shared)
    if not ambiguous and any(r["status"] == "unresolved" for r in requirements):
        issues.append(make_issue("requirement_unresolved", "Q5", "두 축 이상이 함께 갈려 요구 결합이 애매하다",
                                 scope_id=requirements[0]["scope_id"]))
    if ambiguous and requirements:
        issues.append(make_issue("requirement_unresolved", "Q5", "기간 사이에 잇는 말 없이 다른 말이 있어 귀속이 애매하다",
                                 scope_id=requirements[0]["scope_id"]))
    return {"cond_breakdown": breakdowns, "cond_exclude": excludes, "requirements_rule": requirements, "issues": issues}


def q6(state, runtime: Ctx) -> dict:
    """Legacy concept slot. A v1 interprets with Q6G (q6g.interpret); the old concept call is not part of A v1."""
    return {"concepts": {"status": "off", "measures": [], "breakdowns": [], "search_terms": []}}


def q7(state, runtime: Ctx) -> dict:
    """원문 + 핵심어(기간·주기 조건과 요청 표현을 뺀 것). 카탈로그 약어 확장은 A v1에 없다 (D6)."""
    query, offsets = state["query"], state["offsets"]
    for f in ("cond_basis", "cond_unit", "cond_breakdown", "cond_exclude", "requirements"):
        state[f]  # 단계 3에서 쓴다
    inverse = {}
    for k, o in enumerate(offsets):
        inverse.setdefault(o, k)
    drop = []
    for f in ("cond_period", "cond_frequency"):
        for c in state[f]:
            if c["span"]:
                drop.append((inverse[c["span"][0]], inverse[c["span"][1] - 1] + 1))
    queries = [{"query_id": "q1", "text": query, "source": "original", "scope_id": None, "dropped": []}]
    core, dropped = rules.core_text(query, drop)
    if core and core != query:
        queries.append({"query_id": "q2", "text": core, "source": "core", "scope_id": None, "dropped": dropped})
    ctx = runtime.context
    ctx.catalog.aliases  # 승인 별칭 0건
    return {"search_queries": queries}
