"""Absolute catalog checks of a model-step result (user decision 10/19), run after the model's decisions went back
through the judgement table (2.5 with ``overrides``), the certainty checks 1-7 and 2.7.

``verify_after_model(analysis, layer, ignored)`` returns failure codes, none when the settled requirements hold:
- V-req: no two requirements alike (indicator, targets, conditions, breakdown); a breakdown name is under the
  indicator in every common table (else a list or relation was collapsed); a relation word keeps its requirements
  apart; no requirement without an indicator unless the question is a broad one (targets only, no indicator);
- V-ind: every indicator is a catalog name (the words', or the candidate the model chose) held by a table; a target
  value is no requirement of its own when "의" + another requirement's indicator follows it;
- V-tgt / V-bd: every target shares a table with the indicator; every breakdown name is under the indicator in a
  common table; every X-byeol axis is an axis of a table of the indicator;
- V-cond: every attributed condition stands in one place (its requirements, common, or ambiguous); the basis values of
  a requirement are held by a table of its indicator;
- V-bundle: two requirements one of which is under the other in every common table are a merge the reading missed
  (unless a relation or their own conditions or targets keep them apart); a table group hint shares a table;
- V-cover: a leftover word the model read as a request or filler word is one of the list's.
Words from data/lexicon.json (interpret/lexicon.py). Nothing here names a case, table or question.
"""

from __future__ import annotations

from ..catalog.relations import TGT_TYPES, relations_of
from ..catalog.text import index_key
from .catalog_words import catalog_words
from .lexicon import AND_WORD, BYEOL, DOTS, GENITIVE_OF, JOINER_PARTICLES, MODEL_IGNORABLE, NAME_SPLIT, COMMA
from .phrases import key_of, read_question, split_ending
from .text import normalize_text
from .tokens import tokenize, word_conditions

UNDER = frozenset({"below", "in_series"})
SIG_KINDS = ("period", "frequency", "basis", "calc", "unit", "exclude")
_SPLIT = NAME_SPLIT
TARGET_LIST_PARTICLES = frozenset(JOINER_PARTICLES)  # joiners written against a listed target name
TARGET_LIST_MARKS = frozenset({COMMA}) | DOTS  # marks between listed target names


def _req_at(r: dict) -> int:
    return r["indicator"]["span"][0] if r.get("indicator") else r["span"][0]


def _relation_mapping(interp: dict, conds: dict, raw: str, toks=None) -> list[str]:
    """Review r2-4: each relation's operands are the requirements its word is over, not just two or more: consecutive
    requirements; a word between its operands ("A 대비 B") is over the one right before and right after it; a word
    after them is over the run since the previous trailing relation word, ending right before it; the operands stand in
    question order (the run and its order, not only the set). ``toks``: the token layer of the question
    (read here from the question and its conditions without a catalog when not given)."""
    from .settle import between, relation_operands  # settle imports nothing from here; the same rules

    if toks is None:
        toks = tokenize(normalize_text(raw), list(conds.values()), None)[0]

    reqs = interp["requirements"]
    order = [r["id"] for r in sorted(reqs, key=_req_at)]
    points = [(r["id"], _req_at(r)) for r in reqs]
    rel_conds = [c for c in conds.values() if c["kind"] == "relation"]
    out = []
    for x in interp["relations"]:
        c = conds.get(x.get("condition_id"))
        ids = list(x["requirement_ids"])
        if c is None or c["kind"] != "relation" or x["kind"] == "share_of" or len(ids) < 2 \
                or any(i not in order for i in ids):
            continue
        at = [order.index(i) for i in ids]
        k = sorted(at)
        before = [i for i, p in sorted(points, key=lambda kp: kp[1]) if p < c["span"][0]]
        after = [i for i, p in sorted(points, key=lambda kp: kp[1]) if p >= c["span"][1]]
        if k != list(range(k[0], k[0] + len(k))) or at != k:
            ok = False  # not consecutive, or not in question order (review p08 r1-4: the order is part of a ratio)
        elif between(points, c, rel_conds, toks):
            ok = bool(before and after) and ids == [before[-1], after[0]]
        else:  # the run starts after the previous trailing relation word and ends right before this one
            run = relation_operands(points, c, rel_conds, toks)
            ok = bool(before) and order[k[-1]] == before[-1] and (len(run) < 2 or ids == run)
        if not ok:
            out.append(f"V-req:relation_operands:{c['id']}")
    return out


class _Cat:
    def __init__(self, layer):
        self.layer = layer
        self.rel = relations_of(layer)
        self.words = catalog_words(layer)
        self._name_words = {t: [index_key(w) for w in _SPLIT.split(n) if w] for t, n in self.rel.table_names.items()}

    def tables(self, text: str | None) -> set[str]:
        return set(self.rel.tables_of(index_key(text))) if text else set()

    def indicator_tables(self, ind: dict) -> set[str]:
        key = index_key(ind.get("name") or ind["quote"])
        out = self.tables(ind.get("name")) | self.tables(ind["quote"])
        return out | {t for t, ws in self._name_words.items() if key in ws}

    def types(self, name: str) -> set[str]:
        return {c.get("type") for c in self.layer.name_spans().lookup(name)}

    def holds(self, t: str, word: str) -> bool:
        k = index_key(word)
        if not k:
            return False
        if t in self.rel.tables_of(k) or k in index_key(self.rel.table_names.get(t, "")):
            return True
        axis = k.removesuffix(BYEOL)
        return len(axis) >= 2 and any(axis in a for a in self.words.axis_words.get(t, ()))


def _head_key(ind: dict) -> str:
    return index_key(ind.get("name") or ind["quote"])


def _joined(toks: list, k: int) -> int | None:
    """The index of the name or condition token a joiner joins to token ``k`` ("A와 B", "A, B", "A 및 B", "A·B"), or
    None: a joiner particle written against it, or a comma, middle dot or list word right after it."""
    t = toks[k]
    nxt = k + 1
    if t.suffix:
        if t.suffix not in TARGET_LIST_PARTICLES:
            return None
    elif nxt < len(toks) and ((toks[nxt].kind == "punct" and toks[nxt].label in TARGET_LIST_MARKS)
                              or (toks[nxt].kind == "word" and toks[nxt].norm == AND_WORD)):
        nxt += 1
    else:
        return None
    return nxt if nxt < len(toks) and toks[nxt].kind in ("name", "cond") else None


def genitive_followers(toks: list, k: int, starts: dict) -> list:
    """The requirements whose indicators follow the genitive that ends a coordinated list starting at token ``k``
    ("A와 B의 X와 Y": X and Y). ``starts``: indicator token start -> requirement ID. Read on the token layer: the
    list joiners and the genitive are the particles, marks and list words of the lexicon (plan 08, finding 14)."""
    n = k
    while True:
        t = toks[n]
        if t.suffix == GENITIVE_OF:
            g = n + 1
            break
        if not t.suffix and n + 1 < len(toks) and toks[n + 1].kind == "word" and toks[n + 1].norm == GENITIVE_OF:
            g = n + 2
            break
        nx = _joined(toks, n)
        if nx is None:
            return []
        n = nx
    out = []
    while g < len(toks) and toks[g].start in starts:
        out.append(starts[toks[g].start])
        nx = _joined(toks, g)
        if nx is None:
            break
        g = nx
    return out


def _genitive_followers(toks: list, end: int, starts: dict) -> list[str]:
    """Requirements whose indicators follow "의" after a coordinated list that starts with the token ending at
    ``end``."""
    k = next((n for n, t in enumerate(toks) if t.kind in ("name", "cond") and t.end == end), None)
    return genitive_followers(toks, k, starts) if k is not None else []


def verify_after_model(analysis: dict, layer, ignored=()) -> list[str]:
    if layer is None:
        return []
    cat = _Cat(layer)
    rel = cat.rel
    interp = analysis["interpretation"]
    reqs = interp["requirements"]
    conds = {c["id"]: c for c in analysis["conditions"]}
    raw = analysis["text"]["raw"]
    out: list[str] = []
    ind_tables = {r["id"]: cat.indicator_tables(r["indicator"]) for r in reqs if r["indicator"]}

    def own_sig(r: dict) -> set[str]:
        return {c for c in r["condition_ids"] if conds.get(c, {}).get("kind") in SIG_KINDS}

    # ---- V-req
    seen: dict[tuple, str] = {}
    for r in reqs:
        ind = r["indicator"]
        sig = (_head_key(ind) if ind else None, tuple(sorted(index_key(t.get("name") or t["quote"])
                                                              for t in r["targets"])),
               tuple(sorted(set(r["condition_ids"]) | set(r["shared_condition_ids"]))),
               tuple(sorted(index_key(b) for b in r.get("breakdown") or [])))
        if sig in seen:
            out.append(f"V-req:duplicate:{seen[sig]},{r['id']}")
        seen.setdefault(sig, r["id"])
    any_indicator = any(r["indicator"] for r in reqs)
    for r in reqs:
        if r["indicator"] is None and (any_indicator or not r["targets"]):
            out.append(f"V-req:no_indicator:{r['id']}")
    related = {kind for kind in ("ratio", "diff", "percent_of")}
    reading = read_question(analysis["text"], analysis["conditions"], layer)
    words = word_conditions(reading.toks, "relation")  # a relation word inside a catalog name is part of the name
    for c in conds.values():
        if c["kind"] == "relation" and c["attrs"].get("relation") in related and c["id"] in words:
            # each relation word has its own operands (finding 7): a relation of this condition over two or more
            if not any(len(x["requirement_ids"]) >= 2 and x.get("condition_id") == c["id"]
                       for x in interp["relations"] if x["kind"] != "share_of"):
                out.append(f"V-req:relation:{c['id']}")
    out += _relation_mapping(interp, conds, raw, reading.toks)

    # ---- V-ind, V-tgt, V-bd
    starts = {r["indicator"]["span"][0]: r["id"] for r in reqs if r["indicator"]}
    for r in reqs:
        ind = r["indicator"]
        names = [ind.get("name") or ind["quote"]] if ind else [t.get("name") or t["quote"] for t in r["targets"]]
        if names and all((ty := cat.types(n)) and ty <= TGT_TYPES for n in names):
            end = ind["span"][1] if ind else max(t["span"][1] for t in r["targets"])
            if [x for x in _genitive_followers(reading.toks, end, starts) if x != r["id"]]:
                out.append(f"V-ind:target_before_genitive:{r['id']}")
        if ind is None:
            continue
        tables = ind_tables[r["id"]]
        if not ind.get("name") or not tables:
            out.append(f"V-ind:not_catalog:{r['id']}")
            continue
        for t in r["targets"]:
            held = cat.tables(t.get("name")) or cat.tables(t["quote"])
            if not held & tables:
                out.append(f"V-tgt:not_together:{r['id']}|{t['quote']}")
        head = _head_key(ind)
        for b in r.get("breakdown") or []:
            key = index_key(b)
            if rel.occurrences(key):
                rds = rel.readings(key, head)
                n_under = sum(1 for x in rds.values() if x & UNDER)
                if n_under == 0:
                    out.append(f"V-bd:not_under:{r['id']}|{b}")
                elif not all(x and x <= UNDER for x in rds.values()):
                    out.append(f"V-req:collapsed:{r['id']}|{b}")
            elif split_ending(key_of(b))[0].endswith(BYEOL):
                if not any(cat.holds(t, split_ending(key_of(b))[0]) for t in tables):
                    out.append(f"V-bd:axis:{r['id']}|{b}")
            else:
                out.append(f"V-bd:not_catalog:{r['id']}|{b}")

    # ---- V-cond
    common = set(interp["common_condition_ids"])
    for cid, c in conds.items():
        if c["kind"] not in SIG_KINDS or c["attrs"].get("polarity", "+") != "+" or c.get("status") == "negated":
            continue
        owners = [r for r in reqs if cid in r["condition_ids"]]
        amb = any(cid in r["ambiguous_condition_ids"] for r in reqs)
        places = (cid in common) + amb + bool(owners)
        heads = {_head_key(r["indicator"]) if r["indicator"] else None for r in owners}
        if places > 1 or len(heads) > 1:
            out.append(f"V-cond:place:{cid}")
    for r in reqs:
        if r["indicator"] is None:
            continue
        # a basis word inside the requirement's own names ("원화" of a name starting with it) is held by that name
        inside = [r["indicator"]["span"]] + [t["span"] for t in r["targets"]]
        basis = [conds[c] for c in dict.fromkeys(list(r["condition_ids"]) + list(r["shared_condition_ids"]))
                 if c in conds and conds[c]["kind"] == "basis" and conds[c]["attrs"].get("polarity", "+") == "+"
                 and not any(a <= conds[c]["span"][0] and conds[c]["span"][1] <= b for a, b in inside)]
        if basis and not any(all(cat.holds(t, str(c.get("value") or c["quote"])) or cat.holds(t, c["quote"])
                                 for c in basis) for t in ind_tables[r["id"]]):
            out.append(f"V-cond:basis:{r['id']}")

    # ---- V-bundle
    in_relation = [set(x["requirement_ids"]) for x in interp["relations"]]
    with_ind = [r for r in reqs if r["indicator"]]
    for i, a in enumerate(with_ind):
        for b in with_ind[i + 1:]:
            if any({a["id"], b["id"]} <= s for s in in_relation) or own_sig(a) != own_sig(b):
                continue
            if {t["span"][0] for t in a["targets"]} != {t["span"][0] for t in b["targets"]}:
                continue
            ka, kb = _head_key(a["indicator"]), _head_key(b["indicator"])
            if ka == kb:
                continue
            for lower, upper, x, y in ((kb, ka, a, b), (ka, kb, b, a)):
                rds = rel.readings(lower, upper)
                if rds and all(v and v <= UNDER for v in rds.values()):
                    out.append(f"V-bundle:merge:{x['id']}|{y['id']}")
                    break
    for grp in interp.get("table_group_hint") or []:
        sets = [ind_tables.get(rid, set()) for rid in grp]
        if sets and not set.intersection(*sets):
            out.append(f"V-bundle:hint:{','.join(grp)}")

    # ---- V-cover
    fillers = MODEL_IGNORABLE
    for w in ignored:
        if split_ending(key_of(w))[0] not in fillers and key_of(w) not in fillers:
            out.append(f"V-cover:leftover:{w}")
    for w in analysis["judgement"].get("unused_names") or []:
        out.append(f"V-cover:unused:{w}")
    for w in analysis["judgement"].get("leftover") or []:
        out.append(f"V-cover:leftover:{w}")
    return list(dict.fromkeys(out))


__all__ = ["verify_after_model"]
