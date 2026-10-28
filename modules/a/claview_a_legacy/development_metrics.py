"""Development repeat agreement, not accuracy. No model, selection or policy changes.

Compare accepted proposal structure, including bindings, polarity and retained
alternatives. Requirement IDs and unordered lists are normalised; segment order
and relation operand order are retained. Indistinguishable duplicate requirements
are marked non-comparable rather than merging their different scopes.
"""

from __future__ import annotations

from collections import defaultdict

from .catalog.text import index_key
from .interpret.decompose_archive import canonical, digest
from .interpret.decompose_payload import POLICY, spelling_key


def _ordered(value, field=""):
    if isinstance(value, dict):
        return {k: _ordered(v, k) for k, v in value.items()}
    if isinstance(value, list):
        items = [_ordered(x) for x in value]
        return items if field == "segment_ids" else sorted(items, key=canonical)
    return value


def _reading(reading):
    reqs = {r["id"]: _ordered({k: v for k, v in r.items() if k != "id"})
            for r in reading["requirements"]}
    identities = {rid: canonical(r) for rid, r in reqs.items()}
    if len(set(identities.values())) != len(identities):
        return None
    rows = _ordered({k: v for k, v in reading.items() if k not in (
        "requirements", "condition_bindings", "relations", "polarity_links")})
    rows["requirements"] = sorted(reqs.values(), key=canonical)
    for field in ("condition_bindings", "relations", "polarity_links"):
        rows[field] = []
        for item in reading[field]:
            row = _ordered({k: v for k, v in item.items() if k != "requirement_ids"})
            ids = [identities[x] for x in item["requirement_ids"]]
            row["requirement_ids"] = ids if field == "relations" else sorted(ids)
            rows[field].append(row)
        rows[field].sort(key=canonical)
    return rows


def proposal_signature(proposal, *, alternative_kept=False):
    primary = _reading(proposal["primary"])
    alternative = _reading(proposal["alternative"]) if alternative_kept else None
    if primary is None or alternative_kept and alternative is None:
        return None
    return digest({"primary": primary, "alternative": alternative,
                   "ambiguities": _ordered(proposal["ambiguities"])})


def selection_signature(analysis, selections):
    """Per-requirement table bundles: swapping tables between different needs differs."""
    refs = (analysis.get("decomposition") or {}).get("selection_refs") or {}
    reqs = {r["id"]: r for r in (analysis.get("interpretation") or {}).get("requirements", [])}
    bundles = []
    for row in selections:
        rid = row["requirement_id"]
        req = reqs.get(rid, {})
        identity = refs.get(rid) or {k: req.get(k) for k in (
            "indicator", "targets", "breakdown", "context", "condition_ids",
            "shared_condition_ids", "ambiguous_condition_ids")}
        bundles.append({"requirement": _ordered(identity), "status": row["status"],
                        "tables": sorted([[t.get("org_id"), t["table_id"]] for t in row["tables"]],
                                         key=canonical)})
    return digest(sorted(bundles, key=canonical))


def repeat_report(tasks, results, repetitions=3):
    """All-rules and incomplete groups never inflate agreement denominators.

    Model structure denominator: all-model groups with comparable signatures.
    Pipeline selection denominator: complete groups with at least one model result;
    mixed model/fallback groups are labelled, and all-model selection is also shown.
    """
    planned, observed = defaultdict(list), defaultdict(list)
    for task in tasks:
        planned[(task["profile"]["id"], task["case_id"])].append(task)
    for row in results:
        observed[(row["profile_id"], row["case_id"])].append(row)
    groups = []
    for (profile, case), expected in planned.items():
        rows = observed[(profile, case)]
        complete = (len(expected) == len(rows) == repetitions
                    and {r["repeat"] for r in rows} == set(range(1, repetitions + 1)))
        models = sum(r["primary_analysis"]["decomposition"]["applied_source"] == "model" for r in rows)
        status = "incomplete" if not complete else (
            "all_rules" if not models else "all_model" if models == repetitions else "mixed")
        comparable = status == "all_model" and all(r["structure_signature"] is not None for r in rows)
        structure = len({r["structure_signature"] for r in rows}) == 1 if comparable else None
        selection = len({r["selection_signature"] for r in rows}) == 1 if status in ("all_model", "mixed") else None
        groups.append({"profile_id": profile, "case_id": case, "status": status, "finished": len(rows),
                       "model_results": models, "structure_agrees": structure, "selection_agrees": selection,
                       "noncomparable_structure": status == "all_model" and not comparable,
                       "empty_selection_results": sum(not r["selections"] for r in rows)})

    def metric(field, subset):
        values = [g[field] for g in subset if g[field] is not None]
        return {"agree": sum(values), "eligible": len(values),
                "rate": sum(values) / len(values) if values else None}

    def totals(subset):
        return {"groups": len(subset), "status_counts": {
            s: sum(g["status"] == s for g in subset) for s in ("all_model", "mixed", "all_rules", "incomplete")},
                "structure": metric("structure_agrees", subset),
                "selection": metric("selection_agrees", subset),
                "all_model_selection": metric("selection_agrees", [g for g in subset if g["status"] == "all_model"])}

    return {"repetitions": repetitions, "groups": groups, "overall": totals(groups),
            "profiles": {p: totals([g for g in groups if g["profile_id"] == p]) for p, _ in planned}}


# ---------------------------------------------------------------- plan 19: supplementary agreement (not accuracy)
# Core meaning: what a primary reading asks for, read through the payload it answered (sense IDs as catalog name, role
# and path; refs without a sense as their words; condition IDs as the condition's kind, words and value). The strict
# metric above is unchanged. Requirements are paired by content (their r1/r2 labels never count), every requirement
# carries its own bindings and polarity links (decompose_settle._req_canon puts attribution in the identity the same
# way), duplicates inside a requirement are kept, an indicator of None is never merged with a context. Left out:
# the alternative reading, ambiguities, request refs, marker refs (list/divide/polarity markers) and unused refs that
# hold no content word. Relations of the old contract (no operand order meaning) are an unordered set of operand
# multisets: their direction is not comparable and is reported as such.

_COVERAGE = POLICY["coverage"]
_CONTENT_KINDS = frozenset({"name", "condition", "word"})
_FILLER = frozenset(index_key(w) for w in _COVERAGE["ignorable_words"] + _COVERAGE["confirmed_words"])
_PARTICLES = tuple(index_key(p) for p in _COVERAGE["remainder_particles"])
RELATION_DIRECTION_OLD_CONTRACT = "not_comparable"


def _filler(text: str) -> bool:
    k = index_key(text)
    return k in _FILLER or any(k.endswith(p) and k[:-len(p)] in _FILLER for p in _PARTICLES)


def _name_key(name) -> str:
    """Catalog names that differ only by whitespace or a footnote mark written apart are the same name: the sense
    table's approved spelling equivalence (``decompose_payload.spelling_key``, the one definition). Bracket contents
    stay apart ("(1)" / "(2)", "(2010)" / "(2015)"), as in the sense table."""
    return spelling_key(name)


class _PayloadView:
    def __init__(self, payload: dict):
        self.segs = {s["id"]: s for s in payload["segments"]}
        self.order = {s["id"]: n for n, s in enumerate(payload["segments"])}
        self.senses: dict[str, dict] = {}
        for row in payload["candidate_summary"]:
            if row["sense_id"] is not None:
                self.senses.setdefault(row["sense_id"], row)
        self.conds = {c["id"]: c for c in payload["conditions"]}

    def quote(self, ref: dict) -> str:
        if ref.get("subquote") is not None:
            return ref["subquote"]
        ids = sorted((s for s in ref["segment_ids"] if s in self.segs), key=self.order.get)
        return "".join(self.segs[s]["text"] for s in ids)

    def ref(self, ref: dict | None):
        if ref is None:
            return None
        z = ref.get("sense_id")
        if z is None:
            return ["words", index_key(self.quote(ref))]
        sense = self.senses.get(z)
        if sense is None:
            return ["unknown_sense", index_key(self.quote(ref))]
        return ["sense", _name_key(sense["name"]), sense["role"], sense["path"]]

    def refs(self, refs: list) -> list:
        return sorted((self.ref(r) for r in refs), key=canonical)  # a multiset: duplicates are kept

    def cond(self, cid: str) -> list:
        c = self.conds.get(cid)
        return ["condition", c["kind"], index_key(c["quote"]), c["value"]] if c else ["unknown_condition", cid]

    def content(self, ref: dict) -> bool:
        """The ref holds a content word (name, condition, or a word that is no filler or confirmed request word)."""
        for sid in ref["segment_ids"]:
            seg = self.segs.get(sid)
            if seg is None or seg["kind"] not in _CONTENT_KINDS:
                continue
            if seg["kind"] == "word" and _filler(ref["subquote"] if ref.get("subquote") is not None else seg["text"]):
                continue
            return True
        return False


def _sorted(items: list) -> list:
    return sorted(items, key=canonical)


def core_view(proposal: dict, payload: dict) -> dict | None:
    """The core meaning of the primary reading (comment above); None when the reading cannot be read (malformed, or
    two requirements share one ID so that bindings cannot be placed)."""
    try:
        p = _PayloadView(payload)
        reading = proposal["primary"]
        reqs = reading["requirements"]
        rids = [r["id"] for r in reqs]
        if len(set(rids)) != len(rids):
            return None
        own = {rid: [] for rid in rids}
        unresolved = {rid: [] for rid in rids}
        question, open_conditions, name_content = [], [], []
        for b in reading["condition_bindings"]:
            key = p.cond(b["condition_id"])
            named = [rid for rid in dict.fromkeys(b["requirement_ids"]) if rid in own]
            if b["scope"] == "question":
                question.append(key)
            elif b["scope"] == "requirements":
                for rid in named:
                    own[rid].append(key)
            elif b["scope"] == "unresolved":
                if named:
                    for rid in named:
                        unresolved[rid].append(key)
                else:
                    open_conditions.append(key)
            else:  # name_content
                name_content.append([key, p.refs(b["name_refs"])])
        polarity = {rid: [] for rid in rids}
        question_polarity = []
        for link in reading["polarity_links"]:
            key = [link["kind"], p.refs(link["negative_refs"]), p.refs(link["positive_refs"])]
            named = [rid for rid in dict.fromkeys(link["requirement_ids"]) if rid in polarity]
            if named:
                for rid in named:
                    polarity[rid].append(key)
            else:
                question_polarity.append(key)  # empty requirement IDs: every requirement
        canon = {}
        for r in reqs:
            rid = r["id"]
            canon[rid] = canonical({
                "indicator": p.ref(r["indicator"]), "targets": p.refs(r["targets"]), "basis": p.refs(r["basis_refs"]),
                "context": p.ref(r["context"]),
                "breakdowns": _sorted([[b["kind"], p.ref(b["axis"]), p.refs(b["values"])] for b in r["breakdowns"]]),
                "conditions": _sorted(own[rid]), "unresolved_conditions": _sorted(unresolved[rid]),
                "polarity": _sorted(polarity[rid])})
        relations = sorted({canonical([p.cond(rel["condition_id"]),
                                       sorted(canon.get(x, "unknown_requirement") for x in rel["requirement_ids"])])
                            for rel in reading["relations"]})
        unused = sorted(index_key(p.quote(u["ref"])) for u in reading["unused_refs"] if p.content(u["ref"]))
        return {"requirements": sorted(canon.values()), "question_conditions": _sorted(question),
                "open_conditions": _sorted(open_conditions), "name_content": _sorted(name_content),
                "question_polarity": _sorted(question_polarity), "relations": relations,
                "relation_direction": RELATION_DIRECTION_OLD_CONTRACT, "unused_content": unused}
    except (KeyError, TypeError, AttributeError, IndexError):
        return None


def core_signature(proposal: dict, payload: dict) -> str | None:
    view = core_view(proposal, payload)
    return None if view is None else digest(view)


def bundle_signature(selections: list[dict]) -> str:
    """Table bundles only: the multiset of per-requirement table sets, without the requirement identity or status
    (a supplementary view of the selection agreement, which also keeps which requirement got which tables)."""
    return digest(_sorted([_sorted([[t.get("org_id"), t["table_id"]] for t in row["tables"]]) for row in selections]))


def core_repeat_report(tasks, rows, expected_counts: dict, repetitions=3) -> dict:
    """Supplementary repeat agreement with the same groups and denominators as ``repeat_report``.

    ``rows``: ``{profile_id, case_id, repeat, model_adopted, core_signature, requirement_count, relation_count,
    bundle_signature}`` (``core_signature`` of the stored proposal whether adopted or not; None: not readable).
    core: all-model groups whose three signatures are readable (the strict structure denominator). core_all_proposals:
    complete groups whose three proposals are readable, adopted or not. bundle: all-model and mixed groups (the strict
    selection denominator), and all-model only. A group whose repeats agree but whose requirement count differs from
    the approved expectation (``expected_counts``: case ID -> count; counts only) is the same answer repeated, counted
    apart; agreement is never read as correctness.
    """
    planned, observed = defaultdict(list), defaultdict(list)
    for task in tasks:
        planned[(task["profile"]["id"], task["case_id"])].append(task)
    for row in rows:
        observed[(row["profile_id"], row["case_id"])].append(row)
    groups = []
    for (profile, case), expected in planned.items():
        got = observed[(profile, case)]
        complete = (len(expected) == len(got) == repetitions
                    and {r["repeat"] for r in got} == set(range(1, repetitions + 1)))
        models = sum(bool(r["model_adopted"]) for r in got)
        status = "incomplete" if not complete else (
            "all_rules" if not models else "all_model" if models == repetitions else "mixed")
        readable = complete and all(r["core_signature"] is not None for r in got)
        core_all = len({r["core_signature"] for r in got}) == 1 if readable else None
        core = core_all if status == "all_model" else None
        bundle = len({r["bundle_signature"] for r in got}) == 1 if status in ("all_model", "mixed") else None
        counts = sorted({r["requirement_count"] for r in got if r["requirement_count"] is not None})
        want = expected_counts.get(case)

        def repeated_wrong(agree):
            if not agree or want is None or len(counts) != 1:
                return None
            return counts[0] != want

        groups.append({"profile_id": profile, "case_id": case, "status": status, "core_agrees": core,
                       "core_all_proposals_agrees": core_all, "bundle_agrees": bundle,
                       "requirement_counts": counts, "expected_count": want,
                       "same_count_mismatch_repeated": repeated_wrong(core),
                       "same_count_mismatch_repeated_all_proposals": repeated_wrong(core_all),
                       "relations_present": any((r.get("relation_count") or 0) > 0 for r in got),
                       "relation_direction": RELATION_DIRECTION_OLD_CONTRACT})

    def metric(field, subset):
        values = [g[field] for g in subset if g[field] is not None]
        return {"agree": sum(values), "eligible": len(values), "rate": sum(values) / len(values) if values else None}

    def flagged(field, subset):
        return sorted([g["profile_id"], g["case_id"]] for g in subset if g[field])

    def totals(subset):
        return {"groups": len(subset),
                "core": metric("core_agrees", subset),
                "core_all_proposals": metric("core_all_proposals_agrees", subset),
                "bundle": metric("bundle_agrees", subset),
                "all_model_bundle": metric("bundle_agrees", [g for g in subset if g["status"] == "all_model"]),
                "same_count_mismatch_repeated": flagged("same_count_mismatch_repeated", subset),
                "same_count_mismatch_repeated_all_proposals": flagged(
                    "same_count_mismatch_repeated_all_proposals", subset),
                "relation_direction": {"value": RELATION_DIRECTION_OLD_CONTRACT,
                                       "groups_with_relations": sum(g["relations_present"] for g in subset)}}

    return {"repetitions": repetitions, "groups": groups, "overall": totals(groups),
            "profiles": {p: totals([g for g in groups if g["profile_id"] == p]) for p, _ in planned}}
