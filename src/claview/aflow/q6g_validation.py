"""Frozen cumulative V1-V4 validation (2026-10-10), with drop diagnostics.

Source: q6g-e2e-20261010/replay.py, retained independently in eval/q6g_replay.py.
No live client or answer-dependent selection.
Known defects retained (M-03): PERIOD_WORDS matches substrings (이전소득수지),
locate uses the first occurrence for every word and ignores ordering.
V3 merges by first grounding only. V4 hands off
not_in_* but CM uses only grounded requirements, falling back when all are gone.
These are measurement behavior, not newly approved contract rules.
"""
import re

PERIOD_WORDS = re.compile(r"(\d{2,4}\s*년|최근|이후|이전|부터|까지|분기|월별|연간|연도별|매월|매년|반기|\d+\s*개월)")
PARTICLES = ("으로", "에서", "처럼", "을", "를", "의", "와", "과", "로", "이", "가", "은", "는", "에", "도")
FUNC = {"비교", "비교해줘", "비교하고", "보여줘", "보고", "싶다", "싶어", "싶어.", "싶다.", "나눠", "나눠서", "찾아줘", "각각", "함께", "추이", "나란히", "보고싶다"}
AXIS = re.compile(r"^[가-힣A-Za-z0-9·]+별$")


def strip_p(w):
    w = w.strip(".,!?")
    for p in PARTICLES:
        if w.endswith(p) and len(w) > len(p):
            return w[: -len(p)]
    return w


def ov(a, b):
    return a[0] < b[1] and b[0] < a[1]


def locate(quote, q, pf):
    words = []
    missing = False
    for w in quote.split():
        i = q.find(w)
        if i < 0:
            w = strip_p(w)
            i = q.find(w) if w else -1
        end = i + len(w)
        if i < 0:
            missing = True
            break
        words.append((w, (i, end)))
    if missing:
        # Whitespace-insensitive fallback (A v1). Only newly accepted whitespace
        # matches get stricter ordering; the literal path keeps its filters.
        offsets = [i for i, ch in enumerate(q) if not ch.isspace()]
        compact = "".join(q[i] for i in offsets)
        words, cursor = [], 0
        for w in quote.split():
            found = compact.find(w, cursor)
            if found < 0:
                w = strip_p(w)
                found = compact.find(w, cursor) if w else -1
            if found < 0:
                return None
            cursor = found + len(w)
            words.append((w, (offsets[found], offsets[cursor - 1] + 1)))
    keep, cut = [], False
    for w, sp in words:
        if any(ov(sp, x) for x in pf) or PERIOD_WORDS.search(w):
            cut = True
            continue
        keep.append((w, sp))
    if not keep or (cut and all(strip_p(w) in FUNC or w in FUNC for w, _ in keep)):
        return None
    return keep


def mtoks(s):
    return {t for t in re.split(r"[^가-힣A-Za-z0-9]+", s or "") if len(t) >= 2}


def validate_requirements(r, pay):
    cid = r["case_id"]
    if r["model_status"] != "ok" or not r.get("raw"):
        return None
    q = pay[cid]["payload"]["question"]
    conds = pay[cid]["payload"]["conditions"]
    pf = [(q.find(t), q.find(t) + len(t)) for t in conds["period"] + conds["frequency"] if t and q.find(t) >= 0]
    tab = {c["cand_id"]: c["table_id"] for c in pay[cid]["cards_meta"]}
    kept, dropped = [], []
    drop_trace = []  # (raw_index, reason) recorded at the moment of each drop

    def drop(item_index, reason):
        drop_trace.append((item_index, reason))
    for raw_index, x in enumerate(r["raw"]["requirements"]):
        keep = locate(x["quote"], q, pf)
        g = [tab[y["cand_id"]] for y in x["grounding"] if y["cand_id"] in tab]
        c = {"quote": x["quote"], "words": keep, "status": x["status"] if g or x["status"] != "grounded" else "not_in_candidates",
             "picks": g, "basis": {b for b in x["basis_quotes"] if b in q}, "measure": x["measure_text"]}
        c["raw_index"] = raw_index  # raw requirement identity (never the quote text)
        if keep is None:
            reason = "period_only" if PERIOD_WORDS.search(x["quote"]) else "not_in_question"
            dropped.append({"quote": x["quote"], "reason": reason})
            drop(raw_index, reason)
            continue
        sp = [s for _, s in keep]
        clash = [k for k in kept if any(ov(a, b) for a in sp for b in k["sp"])]
        if clash:
            own = [s for s in sp if not any(ov(s, b) for k in clash for b in k["sp"])]
            other_b = set().union(*[k["basis"] for k in clash])
            other_m = set().union(*[mtoks(k["measure"]) for k in clash])
            dist_m = {t for t in mtoks(c["measure"]) - other_m if t in q}
            if not own and not (c["basis"] - other_b) and not dist_m:
                dropped.append({"quote": x["quote"], "reason": "overlap"})
                drop(raw_index, "overlap")
                continue
        c["sp"] = sp
        kept.append(c)
    if kept:
        main = [k for k in kept if not all(AXIS.match(strip_p(w)) or strip_p(w) in FUNC or w in FUNC for w, _ in k["words"])]
        if main:
            dropped.extend({"quote": k["quote"], "reason": "axis_only"} for k in kept if k not in main)
            for k in kept:
                if not any(k is m for m in main):
                    drop(k.get("raw_index"), "axis_only")
            kept = main  # 축 말·기능어만인 요구는 분해 조건으로 남는다(하위 질문 모두에 그대로 둔다)
    from .candidate_rules import merge_definition_requirements
    before = kept
    kept = merge_definition_requirements(q, kept, dropped)
    for k in before:
        if not any(k is m for m in kept):
            drop(k.get("raw_index"), "definition_merged")
    merged, seen = [], {}
    for k in kept:
        f = k["picks"][0] if k["picks"] and k["status"] == "grounded" else None
        if f and f in seen:
            dropped.append({"quote": k["quote"], "reason": "merged"})
            seen[f]["extra_sp"] = seen[f].get("extra_sp", []) + k["sp"]  # 합친 요구의 낱말은 합쳐진 요구 몫
            # Record only; nothing downstream reads this for conditions or selection.
            seen[f].setdefault("merged_from", []).append(k)
            continue
        if f:
            seen[f] = k
        merged.append(k)
    grounded = [k for k in merged if k["status"] == "grounded"]
    handed = [k for k in merged if k["status"] != "grounded"]
    dropped.extend({"quote": k["quote"], "reason": "not_grounded"} for k in handed)
    for k in handed:
        drop(k.get("raw_index"), "not_grounded")
    return {"q": q, "grounded": grounded, "handed": handed, "dropped": dropped, "drop_trace": drop_trace}


