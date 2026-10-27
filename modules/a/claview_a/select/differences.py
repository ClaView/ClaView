"""Plan 10 D step: the dimensions the alternative cover bundles of one requirement differ by. Model off: no call.

Input per requirement (after the bundle step, ``graph.apply_bundles``): the chosen table group, the contending family
alternatives (3.6 ``contenders``), the in-family stand-ins of the representative step (``unspecified`` marks) and the
alternatives the question-set filter dropped (``choice["filtered"]``). The unit compared is a cover bundle (a year-
range join is one bundle, never two alternatives).

Every difference is one dimension of a class:
- ``set``: the question set it (an own or shared single-value basis word, an "X별" word or a negated word that some
  bundle meets and others do not): only bundles meeting it stay (``set_dimension_filter``; 3.6 ``choose_table``);
- ``unset``: a dimension kind of a representative mark the question did not set: asked when its kind is one of
  ``ask_unset_kinds`` (data/selection_policy.json ``decision``), else the representative goes on (confirm);
- ``family``: other families (their core names differ, no dimension explains it): the score gap of the first two
  alternatives decides when ``family_score_gap`` is set and the gap reaches it (confirm, ``d_resolved``), else asked.

Result: ``single`` (nothing to tell apart), ``resolved`` (one bundle left by a set dimension or the score gap;
confirm, never auto), ``ask`` (the dimensions to ask, with their options: value -> bundle). Each option bundle is
marked ``complete`` when it gives every requested cell the current bundle gives. Nothing here names a table, case
or question; the words compared come from the catalog and the policy file.
"""

from __future__ import annotations

import json

from .. import paths
from .cover import other_values
from .tables import nospace

_POLICY = json.loads(paths.SELECTION_POLICY.read_text(encoding="utf-8")).get("decision") or {}
FAMILY_SCORE_GAP: float | None = _POLICY.get("family_score_gap")
ASK_UNSET_KINDS = frozenset(_POLICY.get("ask_unset_kinds") or ())
SET_FILTER = bool(_POLICY.get("set_dimension_filter"))
MIXED_KIND = "mixed_basis"
MAX_BUNDLES = 5


TITLE_SOURCE = "title"  # evidence source of a basis word found in the table name (select/evidence.py)


def _meets(a: dict, cid: str, words: dict, sources: dict) -> bool:
    """Every table of the bundle meets the word by its name (status match, source title): an item or path that
    looks like the word is weaker evidence and never drops another bundle."""
    return bool(a["tables"]) and all((words.get(t) or {}).get(cid) == "match"
                                     and (sources.get(t) or {}).get(cid) == TITLE_SOURCE for t in a["tables"])


def set_dimension_filter(contenders: list[dict], cover: dict,
                         enabled: bool | None = None) -> tuple[list, list, bool]:
    """(kept, dropped, settled) contenders by the dimensions the question set: for each own or shared single-value
    basis word, "X별" word or negated word (``constraints`` that are basis words; never an ambiguous one, never a
    value of a split axis: every split value is a cell already), when some contender meets it by its table names
    the others are dropped. dropped: [{family, table_ids, condition_id}]. ``settled``: one contender is left and its
    table names meet every such word (only then the filter decides the table; otherwise it only narrows the ask)."""
    if not (SET_FILTER if enabled is None else enabled) or len(contenders) < 2:
        return list(contenders), [], False
    req = cover.get("requirement") or {}
    basis = set((req.get("proof") or {}).get("basis") or [])
    words = cover.get("words") or {}
    sources = cover.get("word_sources") or {}
    conds = [c for c in req.get("constraints") or [] if c in basis]
    kept, dropped = list(contenders), []
    for cid in conds:
        fit = [a for a in kept if _meets(a, cid, words, sources)]
        if fit and len(fit) < len(kept):
            dropped += [{"family": a["family"], "table_ids": list(a["tables"]), "condition_id": cid}
                        for a in kept if a not in fit]
            kept = fit
    settled = bool(dropped) and len(kept) == 1 and all(_meets(kept[0], cid, words, sources) for cid in conds)
    return kept, dropped, settled


def _coverage(tables: list[str], cover: dict) -> set:
    prov = cover.get("provided") or {}
    cells = cover.get("cells") or set()
    return set().union(*(prov.get(t, set()) for t in tables)) & cells if tables else set()


def _bundle(tables: list[str], source: str, cover: dict, need: set, **kw) -> dict:
    scores = cover.get("scores") or {}
    return {"table_ids": list(dict.fromkeys(tables)), "source": source,
            "score": round(sum(scores.get(t, 0.0) for t in dict.fromkeys(tables)), 6),
            "complete": need <= _coverage(tables, cover), **kw}


def _written(value: str, qtext: str) -> bool:
    key = nospace(value)
    return len(key) >= 2 and key in qtext


def dimension_record(selection: dict, state: dict, views: dict, policy: dict | None = None,
                     qtext: str = "") -> dict:
    """The D record of one requirement (module docstring): {bundles, dimensions, result, ask_reasons,
    resolved_by, gap}. ``policy`` replaces the file's ``decision`` values (family_score_gap, ask_unset_kinds);
    ``qtext``: the question without spaces and without its negated words (cover.question_words): a stand-in lacking
    a value of the chosen table that the question writes is no option (the question set that value)."""
    policy = policy or {}
    gap_threshold = policy.get("family_score_gap", FAMILY_SCORE_GAP)
    ask_kinds = frozenset(policy.get("ask_unset_kinds", ASK_UNSET_KINDS))
    choice = state.get("choice") or {}
    cover = state.get("cover") or {}
    current = [t["table_id"] for t in selection.get("tables") or []]
    need = _coverage(current, cover)
    chosen_family = (choice.get("chosen") or {}).get("family")
    bundles = [_bundle(current, "current", cover, need, family=chosen_family, value=None)]
    for a in choice.get("contenders") or []:
        if a["family"] != chosen_family:
            bundles.append(_bundle(a["tables"], "family", cover, need, family=a["family"], value=None))
    dims: list[dict] = []
    ask, resolved = [], []
    family = [b for b in bundles if b["source"] == "family"]
    gap = (selection.get("signals") or {}).get("score_gap")
    if family:
        dims.append({"dimension": "family", "class": "family", "kind": "family",
                     "values": [b["family"] for b in bundles if b["source"] in ("current", "family")], "gap": gap})
        # the score decides only over a settled interpretation (an undecided one is not resolved by title words)
        settled = "interpretation_undecided" not in (selection.get("reasons") or [])
        if gap_threshold is not None and gap is not None and gap >= gap_threshold and settled:
            resolved.append("score_gap")
        else:
            ask.append("family_alternatives")
    elif selection.get("model_step") == "pending" or selection.get("alternatives"):
        ask.append("family_alternatives")  # contenders not recorded in the state (kept as the old row 6)
    for m in selection.get("unspecified") or []:
        kinds = set((m.get("kind") or "").split("+")) - {""}
        replaced = []
        written = []
        for other in m.get("other_table_ids") or []:
            tables = [other if t in m.get("table_ids", []) else t for t in current] if current else [other]
            lacks = [v for t in m.get("table_ids") or [] if t in views and other in views
                     for v in other_values(views[other], views[t]) if _written(v, qtext)]
            if lacks:  # the question writes a value the stand-in lacks: no option
                written += lacks
                continue
            values = []
            for t in m.get("table_ids") or []:
                if t in views and other in views:
                    values += other_values(views[t], views[other])
            replaced.append(_bundle(tables, "replacement", cover, need, family=chosen_family,
                                    value=list(dict.fromkeys(values))[:4], dimension=m.get("dimension")))
        bundles += replaced
        own = []
        for t in m.get("table_ids") or []:
            for other in m.get("other_table_ids") or []:
                if t in views and other in views:
                    own += other_values(views[other], views[t])
        bundles[0]["value"] = list(dict.fromkeys((bundles[0]["value"] or []) + own))[:4]
        if written and not replaced:
            dims.append({"dimension": m.get("dimension") or "", "class": "set", "kind": m.get("kind") or "",
                         "values": sorted(set(written)), "gap": None})
            resolved.append("written_value")
            continue
        dims.append({"dimension": m.get("dimension") or "", "class": "unset", "kind": m.get("kind") or "",
                     "values": sorted(set(m.get("other_values") or [])), "gap": None})
        if kinds & ask_kinds:
            ask.append("unspecified_mixed_basis" if MIXED_KIND in kinds else "unspecified_dimension")
    for f in choice.get("filtered") or []:
        dims.append({"dimension": f["condition_id"], "class": "set", "kind": "set", "values": [], "gap": None,
                     "dropped": list(f["table_ids"])})
    if choice.get("d_resolved"):
        resolved.append("set_dimension")
    result = "ask" if ask else "resolved" if resolved else "single"
    # recorded only: what the set-dimension filter would drop among the contenders left (also when it is off)
    _, would, _ = set_dimension_filter(choice.get("contenders") or [], cover, enabled=True)
    return {"bundles": bundles[:1 + MAX_BUNDLES * 2], "dimensions": dims, "result": result,
            "ask_reasons": list(dict.fromkeys(ask)), "resolved_by": list(dict.fromkeys(resolved)), "gap": gap,
            "set_would_drop": [d["condition_id"] for d in would]}
