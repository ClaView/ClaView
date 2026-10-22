"""Variant tests of question interpretation (plan 08 T3, user decision 2 of 10/19).

Every specification case of ``test_spec_judgement`` is rewritten into surface variants that keep its meaning, and
each variant is interpreted on the same synthetic catalog:

- spaces outside names: doubled spaces, a request word glued to the word before it, a request word split into two
  request words of the lexicon ("보여 줘"), a comma with or without the space after it;
- spaces inside names: a space before the last two letters of a catalog name of the question;
- full-width marks: brackets, commas (NFKC reads them as the half-width ones);
- coordinator swaps: a joiner particle after a name ("와/과") replaced by the other joiner particles and by the
  conjunctive list words of the lexicon, a conjunctive list word replaced by a joiner particle, a middle dot between
  two names replaced by a comma;
- request-ending swaps: the final request word replaced by every final request form of the lexicon;
- particle swaps: the object and topic particles of a name of the first clause swapped (the same batchim form);
- auxiliary particles in a negated exclusion ("제외하지 말고" -> "제외하지는 말고", "제외하지도 말고");
- a glued request word with a sentence mark after it ("…를보여줘."); a middle dot of the lexicon between two
  relation phrases in place of a comma or a conjunctive list word ("A 대비 B 비율·C 대비 D 비율").

The words of every operation come from ``data/lexicon.json`` (``interpret/lexicon.py``). Pass rule (user decision
2): a variant fails when both readings are certain and their structures differ (requirements: indicator, targets,
breakdown, own / common / ambiguous conditions; relations with their relation word and operands in order) or when
only one of the two is certain. Two undecided readings may differ; such differences are counted. Failures of the code
these tests were written against are recorded in ``variant_baseline.json`` (expected failures, by case, operation,
variant text and failure kind, ``KINDS``): a failure not in the list fails the test, a listed variant whose failure
kind gets worse fails it, and a listed failure that changed or no longer occurs fails it too (the list is updated,
never the case expectations).
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pytest
import test_spec_judgement as J

from claview.a.interpret import lexicon as LX
from claview.a.interpret.phrases import read_question
from claview.a.interpret.pipeline import interpret_question

BASELINE = Path(__file__).with_name("variant_baseline.json")
CONJUNCTIVE = tuple(LX.LEX["coordinator"]["conjunctive"])
_HANGUL0, _HANGUL1 = ord(LX.HANGUL[0]), ord(LX.HANGUL[-1])


def _norm(s) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).replace(" ", "")


def _batchim(ch: str) -> bool:
    """The syllable ends in a consonant (a final consonant letter)."""
    o = ord(ch)
    return _HANGUL0 <= o <= _HANGUL1 and (o - _HANGUL0) % 28 != 0


def _pick(pair, after: str) -> str:
    """The form of a two-form particle (consonant form first in the lexicon) that follows ``after``."""
    return pair[0] if after and _batchim(after[-1]) else pair[-1]


def structure(analysis: dict) -> tuple:
    """(requirements, relations, certain) of an interpretation, spaces and full-width forms aside.

    A requirement is identified by all its fields (indicator, targets, breakdown, own / common / ambiguous
    conditions), not by its indicator alone. A relation keeps its condition (the relation word: kind, words and
    its place among the conditions of the same words) and its operands in their order (review p08 r1-6)."""
    conds = {c["id"]: c for c in analysis["conditions"]}

    def cv(i):
        c = conds[i]
        v = c.get("value") if c["kind"] not in ("period",) and c.get("value") is not None else c["quote"]
        return f"{c['kind']}:{_norm(v)}" + ("-" if (c.get("attrs") or {}).get("polarity") == "-" else "")

    same: dict[tuple, int] = {}
    cond_key = {}
    interp = analysis.get("interpretation") or {}
    used = {x.get("condition_id") for x in interp.get("relations") or []}
    # the place of a relation word among the relation words the interpretation uses (a word inside a catalog name
    # that 2.2 also found is no relation word: it does not shift the places)
    for c in sorted((c for c in analysis["conditions"] if c["id"] in used), key=lambda c: c["span"]):
        k = (c["kind"], _norm(c["quote"]))
        same[k] = same.get(k, 0) + 1
        cond_key[c["id"]] = (*k, same[k])
    reqs, keys = [], {}
    for r in interp.get("requirements") or []:
        ind = r.get("indicator") or {}
        key = (ind.get("name") or _norm(ind.get("quote")),
               tuple(sorted(_norm(t.get("name") or t["quote"]) for t in r["targets"])),
               tuple(sorted(_norm(b) for b in r.get("breakdown") or [])),
               tuple(sorted(cv(i) for i in r["condition_ids"] if conds[i]["kind"] != "breakdown")),
               tuple(sorted(cv(i) for i in r["shared_condition_ids"] if conds[i]["kind"] != "breakdown")),
               tuple(sorted(cv(i) for i in r["ambiguous_condition_ids"] if conds[i]["kind"] != "breakdown")))
        keys[r["id"]] = key
        reqs.append(key)
    rels = tuple(sorted(((x["kind"], cond_key.get(x.get("condition_id"), ("", "", 0)),
                          tuple(keys.get(i, i) for i in x["requirement_ids"]))
                         for x in interp.get("relations") or []), key=repr))
    return tuple(sorted(reqs)), rels, bool(analysis["judgement"]["certain"])


# ---------------------------------------------------------------- variant operations


def _tokens(analysis: dict, layer):
    return read_question(analysis["text"], analysis["conditions"], layer).toks


def _replace(q: str, a: int, b: int, new: str) -> str:
    return q[:a] + new + q[b:]


def op_spaces(q: str, toks) -> list[tuple[str, str]]:
    out = []
    if " " in q:
        out.append(("space_double", q.replace(" ", "  ")))
    reqs = [t for t in toks if t.kind == "word" and t.label in ("request", "clause_ends")]
    if reqs:
        t = reqs[-1]
        if t.start > 0 and q[t.start - 1] == " " and q[:t.start - 1].strip():
            glued = _replace(q, t.start - 1, t.start, "")
            out.append(("space_glue_request", glued))
            if unicodedata.normalize("NFKC", glued.rstrip()[-1:]) not in LX.SENTENCE_END:  # review p08 r1-5
                out.append(("space_glue_request_period", glued.rstrip() + LX.PERIOD))
        k = LX.index_key(t.quote)
        words = {LX.index_key(w) for w in LX.REQUEST_WORDS}
        cut = next((n for n in range(1, len(k)) if k[:n] in words and k[n:] in words and k == t.quote), None)
        if cut:
            out.append(("space_split_request", _replace(q, t.start, t.end, t.quote[:cut] + " " + t.quote[cut:])))
    if LX.COMMA + " " in q:
        out.append(("space_comma_tight", q.replace(LX.COMMA + " ", LX.COMMA)))
    return out


def op_name_spaces(q: str, toks) -> list[tuple[str, str]]:
    out = []
    for t in toks:
        if t.kind != "name" or t.lookup is not None or t.alias or " " in t.quote or len(t.quote) < 4:
            continue
        cut = t.end - 2
        out.append((f"space_in_name@{t.start}", _replace(q, cut, cut, " ")))
    return out


def op_full_width(q: str, toks) -> list[tuple[str, str]]:
    out = []
    if any(ch in q for ch in LX.OPEN + LX.CLOSE):
        out.append(("fw_paren", "".join(unicodedata.lookup("FULLWIDTH " + unicodedata.name(ch)) if ch in "()" else ch
                                        for ch in q)))
    if LX.COMMA in q:
        out.append(("fw_comma", q.replace(LX.COMMA, unicodedata.lookup("FULLWIDTH COMMA"))))
    return out


def op_coordinators(q: str, toks) -> list[tuple[str, str]]:
    out = []
    for n, t in enumerate(toks[:-1]):
        nxt = toks[n + 1]
        if t.kind in ("name", "qual", "cond") and t.suffix in LX.BARE_JOINERS and t.send < len(q) \
                and q[t.send] == " " and nxt.kind == "name":
            a, b = t.end, t.send
            for p in LX.JOINER_PARTICLES:
                if p in LX.BARE_JOINERS:
                    continue
                out.append((f"coord_particle@{t.start}:{p}", _replace(q, a, b, p)))
            for w in CONJUNCTIVE:
                out.append((f"coord_word@{t.start}:{w}", _replace(q, a, b, " " + w)))
            out.append((f"coord_comma@{t.start}", _replace(q, a, b, LX.COMMA)))
        if t.kind == "word" and t.label == "list_words" and LX.index_key(t.quote) in CONJUNCTIVE and n > 0:
            prev = toks[n - 1]
            if prev.kind == "name" and not prev.suffix and q[prev.end:t.start] == " " and nxt.kind == "name":
                joiner = _pick(LX.BARE_JOINERS[::-1], prev.quote)  # 과 after a consonant, 와 after a vowel
                out.append((f"coord_from_word@{t.start}", _replace(q, prev.end, t.end, joiner)))
        if t.kind == "punct" and t.label in LX.DOTS and n > 0 and toks[n - 1].kind == "name" and nxt.kind == "name" \
                and toks[n - 1].send == t.start and nxt.start == t.end:
            out.append((f"coord_dot_comma@{t.start}", _replace(q, t.start, t.end, LX.COMMA + " ")))
    return out


_MARKERS = [re.compile(e["regex"]) for kind in ("relation", "function", "display") for e in LX.MARKER_ENTRIES[kind]]


def _plain_request(w: str) -> bool:
    """A final request form that carries no relation, function or display word of its own ("비교해" asks for a
    comparison: swapping it in would change the meaning)."""
    return not any(m.search(w) for m in _MARKERS)


def op_relation_marks(q: str, toks) -> list[tuple[str, str]]:
    """A comma or a conjunctive list word between two relation phrases ("A 대비 B 비율, C 대비 D 비율") replaced by a
    middle dot of the lexicon (review p08 r1-3)."""
    dot = sorted(LX.DOTS)[0]
    out = []
    for n, t in enumerate(toks[:-2]):
        if not (t.kind == "cond" and t.cond_kind == "relation") or t.suffix:
            continue
        mark = toks[n + 1]
        joins = (mark.kind == "punct" and mark.label == LX.COMMA) \
            or (mark.kind == "word" and LX.index_key(mark.quote) in CONJUNCTIVE)
        if joins and any(x.kind == "cond" and x.cond_kind == "relation" for x in toks[n + 2:]):
            out.append((f"rel_dot@{t.start}", _replace(q, t.send, toks[n + 2].start, dot)))
    return out


def op_requests(q: str, toks) -> list[tuple[str, str]]:
    finals = [t for t in toks if t.kind == "word" and LX.index_key(t.quote) in LX.REQUEST_FINAL]
    if not finals:
        return []
    t = finals[-1]
    return [(f"req:{w}", _replace(q, t.start, t.end, w)) for w in LX.LEX["request"]["forms"]["final"]
            if LX.index_key(w) != LX.index_key(t.quote) and _plain_request(w)]


def op_particles(q: str, toks, analysis: dict, layer) -> list[tuple[str, str]]:
    reading = read_question(analysis["text"], analysis["conditions"], layer)
    first = min((g.clause for g in reading.groups), default=None)
    ends = {g.clause: g.clause_end for g in reading.groups}
    stop = ends.get(first, len(q))
    obj, top = LX.LEX["particles"]["object"], LX.LEX["particles"]["topic"]
    out = []
    for t in toks:
        if t.kind != "name" or t.start >= stop or t.suffix not in obj + top or t.send != t.end + len(t.suffix):
            continue
        swap = _pick(top if t.suffix in obj else obj, t.quote)
        out.append((f"particle@{t.start}:{swap}", _replace(q, t.end, t.send, swap)))
    return out


def op_negated_exclusion(q: str, toks) -> list[tuple[str, str]]:
    out = []
    end = LX.EXCLUDE_NEGATED_ENDING
    for by in LX.EXCLUDE_NEGATED_BY:
        at = q.find(f"{end} {by}")
        if at >= 0:
            for aux in LX.AUX:
                out.append((f"aux_neg:{aux}", _replace(q, at + len(end), at + len(end), aux)))
    return out


def variants(case: dict, analysis: dict, layer) -> list[tuple[str, str]]:
    q = case["question"]
    toks = _tokens(analysis, layer)
    out = op_spaces(q, toks) + op_name_spaces(q, toks) + op_full_width(q, toks) + op_coordinators(q, toks) \
        + op_requests(q, toks) + op_particles(q, toks, analysis, layer) + op_negated_exclusion(q, toks) \
        + op_relation_marks(q, toks)
    seen, uniq = set(), []
    for name, v in out:
        if v != q and v not in seen:
            seen.add(v)
            uniq.append((name, v))
    return uniq


# failure kinds, mildest first (review p08 r1-7): the variant lost the base's certainty (same structure, or another
# one), the variant is certain where the base is not (same structure, or another one), both certain and different
KINDS = ("lost_certainty", "lost_certainty_struct", "gained_certainty", "gained_certainty_struct",
         "both_certain_differ")


def failure_kind(base: tuple, got: tuple) -> str | None:
    """The failure kind of a variant reading against the base reading; None when it passes (user decision 2)."""
    if got == base:
        return None
    same = got[:2] == base[:2]
    if base[2] and got[2]:
        return "both_certain_differ"
    if base[2]:
        return "lost_certainty" if same else "lost_certainty_struct"
    if got[2]:
        return "gained_certainty" if same else "gained_certainty_struct"
    return None  # two undecided readings may differ (counted apart)


def run_case(case: dict, layers) -> dict:
    """{"failures": [(op, variant, kind)], "undecided_diffs": [(op, variant)], "n": variants}."""
    layer = layers.get(case["layer"])
    base_a = interpret_question(case["question"], layer)
    base = structure(base_a)
    fails, soft = [], []
    vs = variants(case, base_a, layer)
    for name, v in vs:
        got = structure(interpret_question(v, layer))
        kind = failure_kind(base, got)
        if kind is not None:
            fails.append((name, v, kind))
        elif got != base:
            soft.append((name, v))
    return {"failures": fails, "undecided_diffs": soft, "n": len(vs)}


def _baseline() -> dict:
    """case ID -> {(operation, variant): recorded failure kind}."""
    doc = json.loads(BASELINE.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for cid, op, v, kind in doc["failures"]:
        assert kind in KINDS, kind
        out.setdefault(cid, {})[(op, v)] = kind
    return out


def compare_with_baseline(failures: list, known: dict) -> dict:
    """New failures, worse ones (a listed variant whose failure kind got worse), changed or gone ones."""
    got = {(op, v): kind for op, v, kind in failures}
    out = {"new": [], "worse": [], "changed": [], "gone": []}
    for key, kind in got.items():
        if key not in known:
            out["new"].append((*key, kind))
        elif KINDS.index(kind) > KINDS.index(known[key]):
            out["worse"].append((*key, known[key], kind))
        elif kind != known[key]:
            out["changed"].append((*key, known[key], kind))
    out["gone"] = sorted(k for k in known if k not in got)
    return out


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    return J.SpecLayers(tmp_path_factory, J.CATALOGS)


@pytest.mark.parametrize("case", [pytest.param(c, id=c["id"]) for c in J.CASES])
def test_variants_keep_the_reading(case, layers):
    res = run_case(case, layers)
    diff = compare_with_baseline(res["failures"], _baseline().get(case["id"], {}))
    assert not diff["new"], f"new variant failures: {diff['new']}"
    assert not diff["worse"], f"recorded variant failures that got worse: {diff['worse']}"
    assert not diff["changed"] and not diff["gone"], \
        f"recorded variant failures changed or gone (update variant_baseline.json): {diff['changed']} {diff['gone']}"


def test_structure_keeps_relation_words_and_operand_order(layers):
    """Review p08 r1-6: two relations swapping their relation words, or a relation over reversed operands, or over
    another requirement of the same indicator, is another structure."""
    import copy

    a = interpret_question("가상A금리 대비 가상B금리 비율 및 가상C금리 대비 가상D금리 비율을 보여줘", layers.get("jt_rates"))
    base = structure(a)
    ratios = [x for x in a["interpretation"]["relations"]
              if next(c for c in a["conditions"] if c["id"] == x["condition_id"])["quote"] == "비율"]
    assert len(ratios) == 2
    swapped = copy.deepcopy(a)
    rel = [x for x in swapped["interpretation"]["relations"] if x["condition_id"] in {r["condition_id"] for r in ratios}]
    rel[0]["condition_id"], rel[1]["condition_id"] = rel[1]["condition_id"], rel[0]["condition_id"]
    assert structure(swapped) != base
    reversed_ = copy.deepcopy(a)
    x = next(r for r in reversed_["interpretation"]["relations"] if r["condition_id"] == ratios[1]["condition_id"])
    x["requirement_ids"] = list(reversed(x["requirement_ids"]))
    assert structure(reversed_) != base
    assert structure(copy.deepcopy(a)) == base  # must stay: the same reading is the same structure
    b = interpret_question("2020년 가상A금리와 2021년 가상A금리의 차이를 보여줘", layers.get("jt_rates"))
    other = copy.deepcopy(b)
    for r in other["interpretation"]["relations"]:
        r["requirement_ids"] = list(reversed(r["requirement_ids"]))
    if b["interpretation"]["relations"]:
        assert structure(other) != structure(b)  # two requirements of one indicator are told apart by their periods


def test_a_recorded_failure_that_gets_worse_fails():
    """Review p08 r1-7: a listed variant whose failure kind gets worse is reported; the same kind passes."""
    known = {("op", "v"): "lost_certainty"}
    assert compare_with_baseline([("op", "v", "lost_certainty")], known) == {"new": [], "worse": [], "changed": [],
                                                                            "gone": []}
    assert compare_with_baseline([("op", "v", "both_certain_differ")], known)["worse"]
    assert compare_with_baseline([("op", "v", "gained_certainty")], known)["worse"]
    assert compare_with_baseline([], known)["gone"] == [("op", "v")]
    assert compare_with_baseline([("op", "w", "lost_certainty")], known)["new"]
    milder = compare_with_baseline([("op", "v", "lost_certainty")], {("op", "v"): "both_certain_differ"})
    assert milder["changed"] and not milder["worse"]


def test_baseline_names_known_cases():
    ids = {c["id"] for c in J.CASES}
    assert set(_baseline()) <= ids
