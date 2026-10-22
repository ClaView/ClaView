"""Measure evidence (J5 component), derived from Q7/recorded measures and catalog fields.

No answer labels, case IDs, table preferences, or external requests. Exact lexical
coverage is evidence of a topic match, not proof of identical statistical meaning.
"""
from __future__ import annotations

import json
import re
import unicodedata

from . import rules

VALUE = {"none": 0.0, "partial": 0.5, "match": 1.0}
COMPONENT_WEIGHT = 10.0  # One existing condition unit; fixed before comparison.
CONDITIONS = ("cond_period", "cond_frequency", "cond_basis", "cond_unit", "cond_breakdown", "cond_exclude")
_WORD = re.compile(r"[가-힣A-Za-z0-9]+")
_TAIL = re.compile(r"(?:으로|에서|의|을|를|이|가|은|는|로|에|와|과|도|만)$")
# Linguistic/display words, not a domain dictionary. Semantic forms come from metadata.
_FUNCTION = frozenset({"그래프", "추이", "비교", "흐름", "자료", "표", "기준", "규모", "지표", "전체", "각각"})
_CACHE = []


def norm(text):
    return re.sub(r"[^가-힣a-z0-9]", "", unicodedata.normalize("NFKC", text).lower())


def fields(meta):
    itm = meta.get("item_itm") or {}
    if itm.get("name"):
        yield {"field": "ITM", "text": itm["name"], "item_id": itm.get("itm_id")}
    for item in meta.get("items", []):
        if item.get("name_raw"):
            yield {"field": "classification_value", "text": item["name_raw"],
                   "obj_id": item["obj_id"], "item_id": item["item_id"]}
    if meta.get("title"):
        yield {"field": "title", "text": meta["title"]}
    for path in meta.get("catalog_paths", []):
        # A path hit must occur within one segment, not across unrelated levels.
        for segment in path.split(">"):
            yield {"field": "catalog_path", "text": segment.strip()}


class MeasureIndex:
    def __init__(self, catalog):
        self.table_fields = {tid: [(norm(f["text"]), f) for f in fields(meta)]
                             for tid, meta in catalog.meta_store.items()}
        self.labels = tuple({s for entries in self.table_fields.values() for s, _ in entries})
        self.aliases = {}
        for pair in catalog.abbr_pairs:
            a, b = norm(pair["abbr"]), norm(pair["long"])
            if len(a) >= 2 and len(b) >= 2:
                for key, value in ((a, b), (b, a)):
                    self.aliases.setdefault(key, []).append((value, dict(pair)))
        self.known = {}

    def forms(self, term):
        key = norm(term)
        return [(key, None)] + self.aliases.get(key, [])

    def recognizes(self, term):
        key = norm(term)
        if key not in self.known:
            self.known[key] = len(key) >= 2 and any(f in s for f, _ in self.forms(term) for s in self.labels)
        return self.known[key]


def index_for(catalog):
    for old, index in _CACHE:
        if old is catalog:
            return index
    index = MeasureIndex(catalog)
    _CACHE.append((catalog, index))
    del _CACHE[:-2]
    return index


def _trim(word, index):
    # Don't strip a particle-looking ending from a catalog word itself.
    if index.recognizes(word):
        return word
    return _TAIL.sub("", word)


def rule_phrases(state, index):
    """Q7 core minus Q2-Q5 spans, retaining exact original spans for each atom."""
    query, offsets, question = state["query"], state["offsets"], state["question"]
    core = next((q["text"] for q in state["search_queries"] if q["source"] == "core"), query)
    core_norm = norm(core)
    removed = [c["span"] for key in CONDITIONS for c in state[key] if c.get("span")]
    keep = [not any(a <= offset < b for a, b in removed) for offset in offsets]
    for match in rules._REQUEST.finditer(query):
        for i in range(match.start(), match.end()):
            keep[i] = False
    remaining = "".join(ch if keep[i] else " " for i, ch in enumerate(query))
    terms = []
    for match in _WORD.finditer(remaining):
        word = _trim(match.group(), index)
        if norm(word) in _FUNCTION or not index.recognizes(word) or norm(word) not in core_norm:
            continue
        start, end = match.start(), match.start() + len(word)
        span = [offsets[start], offsets[end - 1] + 1]
        terms.append({"text": word, "span": span, "quote": question[span[0]:span[1]]})
    if not terms:
        return []
    span = [terms[0]["span"][0], terms[-1]["span"][1]]
    return [{"text": " ".join(t["text"] for t in terms), "span": span,
             "quote": question[span[0]:span[1]], "span_kind": "cover_of_literal_terms", "source": "Q7_core", "terms": terms}]


def model_phrases(question, measures, state, index):
    """Use recorded measure text, anchored to validated original quote spans.

    A paraphrased measure keeps its quote anchor; it never invents a literal
    measure span. Condition-only or unanchored measures cannot establish a match.
    """
    excluded = [c["span"] for key in CONDITIONS for c in state[key] if c.get("span")]
    result, rejected = [], []
    for measure in measures:
        text, spans = measure["text"], measure["spans"]
        spans = [s for s in spans if 0 <= s[0] < s[1] <= len(question)]
        useful = [s for s in spans if any(not any(a <= i < b for a, b in excluded) for i in range(*s))]
        terms = []
        for match in _WORD.finditer(text):
            word = _trim(match.group(), index)
            if norm(word) in _FUNCTION or not index.recognizes(word):
                continue
            occurrences = [[m.start(), m.end()] for m in re.finditer(re.escape(word), question, re.IGNORECASE)]
            # Explicit condition words are not a main-measure hit.
            if occurrences and all(any(a <= s[0] and s[1] <= b for a, b in excluded) for s in occurrences):
                continue
            terms.append({"text": word, "span": next((s for s in occurrences if
                          any(a <= s[0] and s[1] <= b for a, b in useful)), None)})
        if not useful or not terms:
            rejected.append({"text": text, "spans": spans, "reason": "condition_only_or_unanchored"})
            continue
        result.append({"text": text, "source": "Q6g_measures", "span": [min(s[0] for s in useful), max(s[1] for s in useful)],
                       "span_kind": "requirement_quote_anchor",
                       "anchor_spans": useful, "anchor_quotes": [question[a:b] for a, b in useful],
                       "terms": terms})
    return result, rejected


def assess(phrases, table_id, meta, index):
    """경로 단 이름은 낱말 하나가 단 이름 전체와 같을 때, 또는 경로 **마지막 단**(표 바로 위) 이름이 측정 문구
    전체와 같을 때(괄호 안·공백 제외) 경로 일치로 인정한다. 일부 낱말만 겹치거나 상위 단과 같으면 인정하지 않는다."""
    entries = index.table_fields.get(table_id)
    if entries is None:
        entries = [(norm(f["text"]), f) for f in fields(meta)]
    leaves = {p.split(">")[-1].strip() for p in meta.get("catalog_paths", [])}
    phrase_results = []
    for phrase in phrases:
        hits, best = [], 0.0
        terms = list(dict.fromkeys(t["text"] for t in phrase["terms"]))
        for text, field in entries:
            matched = []
            for term in terms:
                found = next(((form, alias) for form, alias in index.forms(term) if form and form in text), None)
                if found:
                    matched.append({"term": term, "form": found[0], "alias_source": found[1]})
            coverage = len(matched) / len(terms) if terms else 0.0
            if coverage:
                # A breadcrumb substring can name the parent survey, not this
                # table's measure. Use an exact path-segment name only; retain
                # broader context hits as evidence without awarding a match.
                path_stem = norm(re.sub(r"\([^)]*\)", "", field["text"]))
                used = field["field"] != "catalog_path" or any(m["form"] == path_stem for m in matched) or (
                    field["text"] in leaves and coverage == 1 and norm(phrase.get("text") or "") == path_stem)
                if used:
                    best = max(best, coverage)
                hits.append({**field, "matched_terms": matched, "coverage": coverage, "used_for_status": used})
        # Keep every best field type and a bounded number of examples per type.
        evidence = []
        for field in ("ITM", "classification_value", "title", "catalog_path"):
            evidence.extend(sorted((h for h in hits if h["field"] == field), key=lambda h: -h["coverage"])[:3])
        phrase_results.append({"phrase": phrase, "status": "match" if best == 1 else "partial" if best else "none",
                               "coverage": best, "evidence": evidence})
    status = max((p["status"] for p in phrase_results), key=VALUE.get, default="none")
    by_field = {}
    for field in ("ITM", "classification_value", "title", "catalog_path"):
        coverage = max((h["coverage"] for p in phrase_results for h in p["evidence"]
                        if h["field"] == field and h["used_for_status"]), default=0.0)
        by_field[field] = "match" if coverage == 1 else "partial" if coverage else "none"
    return {"status": status, "by_field": by_field, "available": bool(phrases),
            "reason": "lexical_measure_coverage" if phrases else "no_measure_extracted", "phrases": phrase_results}


def evidence_row(table_id, assessment, snapshot):
    return {"table_id": table_id, "kind": "measure", "text": json.dumps(assessment, ensure_ascii=False),
            "source_field": "Q7_core × ITM/classification_value/title/catalog_path",
            "source_snapshot": snapshot,
            "stance": {"match": "support", "partial": "unknown", "none": "against"}[assessment["status"]]
            if assessment["available"] else "unknown"}
