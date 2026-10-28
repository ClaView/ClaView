"""build_payload (plan 15 r3, 4): the model input of the decomposition proposal, and the local sense table.

The payload holds only what the model needs to propose a structure, by IDs the code made:

- ``raw``: the question as given (never cut);
- ``segments``: ``{id, text, kind, marker}`` of every segment (3.1); ``marker`` is the marker class a marker mention
  shows over that one segment (list, divide, negation, exclusion, relation), else None;
- ``conditions``: ``{id, kind, quote, value, segment_ids, extract_status, attrs}`` with the existing 2.2 IDs; the
  attributes are the ones ``decompose_policy.json`` names per kind; ``extract_status`` only for periods (else None);
- ``candidate_summary``: per name mention and sense ``{mention_id, segment_ids, sense_id, name, role, path}`` (a name
  mention without candidates: one row with sense None);
- ``relations``: ``{kind, sense_ids}`` among the senses sent (same_axis, parent_child [parent, child], sibling,
  table_value [table name, value]);
- ``truncation``: senses or relations omitted, unclassified roles present, the shrink steps applied;
- ``limits``: requirement slots and alternatives the output may use.

A sense (``sense_id`` z1, z2, ...) is a code-made temporary ID grouping the raw candidates (3.3) with the same catalog
name (spacing and a footnote mark aside: plan 19 stage 5) and role; ``local["senses"]`` keeps every row of it (table,
axis, value IDs). No table, axis or value code, rule requirement draft, certainty, judgement row, catalog dump, answer
or case ID is put in the payload. Plan 19 stage 5: the senses of one mention are shown by role, then those carrying
the question's positive explicit basis or calc conditions first (``build_senses``); the ones over the per-mention
limit are recorded with the reason in ``local["omitted_senses"]`` and kept locally.

``INPUT_VERSION`` labels the model input (segments, mentions, senses, payload and prompt) of this code; a stored
response of another input version is never ported to it (its request hash differs).

Over a limit the descriptions are shrunk first (``shrink_steps``); when the payload is still too large, or the
question has more segments than the limit (or none), the payload is ``{"input_limit": {reason, value, limit}}``
and the caller takes the rules path.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import TYPE_CHECKING

from .. import paths
from ..catalog.text import index_key, nfkc, nospace, strip_footnote
from ..contract.shapes import Condition, Mention, Segment, TextView

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

POLICY_FILE = paths.PACKAGE_DATA_DIR / "decompose_policy.json"
PROMPT_FILE = paths.PACKAGE_DATA_DIR / "prompts" / "decompose.md"
POLICY: dict = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
LIMITS: dict = POLICY["limits"]
_ROLE_RANK = {r: n for n, r in enumerate(POLICY["senses"]["role_order"])}
_PATH_RANK = {p: n for n, p in enumerate(POLICY["senses"]["path_order"])}
RELATION_KINDS = ("same_axis", "parent_child", "sibling", "table_value")
# plan 19 stages 4-5: dot-name lookup, request and list-marker context rules, sense order and spelling merge, prompt
# rules, shrink order (record only; never sent, never a replay key of its own: the request hash already changes)
INPUT_VERSION = "plan19-stage45-v1"


def prompt_text() -> str:
    """The system prompt of the decomposition proposal (general principles only; placeholder names)."""
    return PROMPT_FILE.read_text(encoding="utf-8")


def is_input_limit(payload: dict) -> bool:
    return "input_limit" in payload


def _limit(reason: str, value: int, limit: int) -> dict:
    return {"input_limit": {"reason": reason, "value": value, "limit": limit}}


def _row_key(row: dict) -> tuple:
    return tuple(row.get(k) for k in ("table_id", "obj_id", "value_id", "value_name", "role", "path", "type"))


def _best_path(rows: list[dict]) -> str:
    return min((r["path"] for r in rows), key=lambda p: _PATH_RANK.get(p, len(_PATH_RANK)))


# ---------------------------------------------------------------- plan 19 stage 5: sense order and spelling variants


def spelling_key(name: str | None) -> str:
    """The key under which catalog names that differ only in spacing or a trailing footnote mark are one sense
    ("A (B)" / "A( B )", "A(B) 1)" / "A(B)"): NFKC, the footnote mark dropped when it is written apart and leaves the
    brackets balanced, every whitespace removed. Brackets, their contents and other marks are kept ("A(B)" and "A"
    stay two senses)."""
    s = nfkc(name or "").strip()
    cut = strip_footnote(s)
    if cut != s and not (s[len(cut):len(cut) + 1].isspace() and cut.count("(") == cut.count(")")):
        cut = s
    return nospace(cut)


@lru_cache(maxsize=8192)
def _meanings(text: str) -> frozenset[tuple[str, str]]:
    """(axis, value) of the positive basis and calc words the rules' own condition reader (``q4_rows``) finds in a
    text: the slot axes and the classification words of the condition word list; no word list of its own."""
    from ..reused.q_nodes import normalize
    from .condition_rules import q4_rows

    return frozenset((r["axis"], index_key(str(r["value"]))) for r in q4_rows(normalize(text)["query"])
                     if r["polarity"] == "+")


def question_meanings(conditions: list[Condition] | None) -> set[tuple[str, str]]:
    """(axis, value) of the question-level explicit basis and calc conditions asked for positively: polarity ``+`` and
    not inside an exclusion condition. Never the model's output nor a condition binding."""
    conditions = conditions or []
    excluded = [c["span"] for c in conditions if c["kind"] == "exclude"]
    out: set[tuple[str, str]] = set()
    for c in conditions:
        attrs = c.get("attrs") or {}
        axis, value = attrs.get("axis"), attrs.get("value") or c.get("value")
        if c["kind"] not in ("basis", "calc") or not axis or not value or attrs.get("polarity") != "+":
            continue
        if any(a <= c["span"][0] and c["span"][1] <= b for a, b in excluded):
            continue
        out.add((axis, index_key(str(value))))
    return out


def sense_compat(names: list[str], asked: set[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """(matched, conflicting) of one sense against the question's positive meanings: ``axis:value`` its names (the
    sense's catalog name and its rows' value names) carry that the question asks for, and the ones of a slot axis the
    question asks with another value (the classification and family axes name no slot: ``table.DICT_FREE_AXES``)."""
    if not asked:
        return [], []
    from .table import DICT_FREE_AXES

    carried = set().union(*(_meanings(n) for n in names)) if names else set()
    slots = {a for a, _ in asked if a not in DICT_FREE_AXES}
    matched = sorted(f"{a}:{v}" for a, v in carried & asked)
    conflicting = sorted(f"{a}:{v}" for a, v in carried - asked if a in slots)
    return matched, conflicting


def build_senses(mentions: list[Mention], conditions: list[Condition] | None = None) -> dict:
    """The local sense table: ``senses`` (sense ID -> name, role, best path, every raw row, the mentions holding it,
    the catalog names merged into it), ``mention_senses`` (name mention -> its sense IDs in display order, every one),
    ``mention_kept`` (the ones under the per-mention limit), ``omitted_senses`` (the ones over it, with the reason;
    they stay in the table: not sending is no deletion), ``sense_compat`` and ``question_meanings`` (plan 19 stage 5).

    One sense per (catalog name, role) over the whole question: a name written twice shows the same sense IDs. Names
    that differ only in spacing or a footnote mark are one sense (``spelling_key``); every raw row, role and table
    stays. Display order per mention: by role (policy), then (plan 19 stage 5) the senses whose names carry more of the
    question's positive explicit basis or calc conditions (``question_meanings``; a negated one raises nothing), then
    fewer conflicting slot values, then path, more rows and the name. Without conditions the order is the old one."""
    asked = question_meanings(conditions)
    senses: dict[str, dict] = {}
    by_key: dict[tuple, str] = {}
    mention_senses: dict[str, list[str]] = {}
    mention_kept: dict[str, list[str]] = {}
    omitted: list[dict] = []
    compat: dict[str, dict] = {}
    cap = LIMITS["senses_per_mention"]
    for m in mentions:
        if m["kind"] != "name":
            continue
        groups: dict[tuple, list[dict]] = {}
        for c in m["candidates"]:
            groups.setdefault((spelling_key(c.get("value_name")), c["role"]), []).append(c)
        info = {}
        for key, rows in groups.items():
            names = sorted({r.get("value_name") or "" for r in rows})
            # the display name: the spelling most rows have (then the first in code order)
            shown = min(names, key=lambda n: (-sum((r.get("value_name") or "") == n for r in rows), n))
            matched, conflicting = sense_compat(names, asked)
            info[key] = (shown, names, matched, conflicting)
        order = sorted(groups.items(), key=lambda kv: (_ROLE_RANK.get(kv[0][1], len(_ROLE_RANK)),
                                                       -len(info[kv[0]][2]), len(info[kv[0]][3]),
                                                       _PATH_RANK.get(_best_path(kv[1]), len(_PATH_RANK)),
                                                       -len(kv[1]), info[kv[0]][0]))
        ids = []
        for key, rows in order:
            shown, names, matched, conflicting = info[key]
            zid = by_key.get(key)
            if zid is None:
                zid = f"z{len(senses) + 1}"
                by_key[key] = zid
                senses[zid] = {"name": shown, "role": key[1], "path": _best_path(rows), "rows": [],
                               "mention_ids": [], "names": []}
            s = senses[zid]
            seen = {_row_key(r) for r in s["rows"]}
            s["rows"] += [dict(r) for r in rows if _row_key(r) not in seen]
            s["path"] = min(s["path"], _best_path(rows), key=lambda p: _PATH_RANK.get(p, len(_PATH_RANK)))
            s["mention_ids"].append(m["mention_id"])
            s["names"] = sorted(set(s["names"]) | set(names))
            if matched or conflicting:
                compat[zid] = {"matched": matched, "conflicting": conflicting}
            ids.append(zid)
        mention_senses[m["mention_id"]] = ids
        mention_kept[m["mention_id"]] = ids[:cap]
        omitted += [{"mention_id": m["mention_id"], "sense_id": z, "name": senses[z]["name"],
                     "role": senses[z]["role"], "rank": n, "reason": "senses_per_mention"}
                    for n, z in enumerate(ids, start=1) if n > cap]
    return {"senses": senses, "mention_senses": mention_senses, "mention_kept": mention_kept,
            "omitted_senses": omitted, "sense_compat": compat,
            "question_meanings": sorted(f"{a}:{v}" for a, v in asked)}


def segment_markers(segments: list[Segment], mentions: list[Mention]) -> dict[str, str | None]:
    """Segment ID -> the marker class a mention shows over that one segment (a marker word, a list dot, a marker
    condition written as one segment), else None."""
    out: dict[str, str | None] = {s["segment_id"]: None for s in segments}
    for m in mentions:
        if m["marker"] is not None and len(m["segment_ids"]) == 1 and out.get(m["segment_ids"][0]) is None:
            out[m["segment_ids"][0]] = m["marker"]
    return out


def condition_segments(conditions: list[Condition], mentions: list[Mention]) -> dict[str, list[str]]:
    """Condition ID -> the segments of its condition mention."""
    out: dict[str, list[str]] = {c["id"]: [] for c in conditions}
    for m in mentions:
        if m["kind"] == "condition":
            for cid in m["condition_ids"]:
                out.setdefault(cid, [])
                out[cid] += [s for s in m["segment_ids"] if s not in out[cid]]
    return out


def _condition_row(c: Condition, sids: list[str]) -> dict:
    attrs = c.get("attrs") or {}
    keep = POLICY["condition_attrs"].get(c["kind"], [])
    return {"id": c["id"], "kind": c["kind"], "quote": c["quote"], "value": c["value"], "segment_ids": list(sids),
            "extract_status": attrs.get("extract_status") if c["kind"] == "period" else None,
            "attrs": {k: attrs[k] for k in keep if k in attrs}}


# ---------------------------------------------------------------- relations among the senses sent


def _by_table(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["table_id"], []).append(r)
    return out


def _ancestors(parents: dict, table: str, obj: str, item: str) -> list[str]:
    up, out, cur, seen = parents.get((table, obj), {}), [], None, {item}
    cur = up.get(item)
    while cur is not None and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = up.get(cur)
    return out


def _pair_kinds(a: list[dict], b: list[dict], parents: dict) -> set[tuple[str, bool]]:
    """Relation kinds between two senses over the tables both have rows in: (kind, a first)."""
    out: set[tuple[str, bool]] = set()
    ta, tb = _by_table(a), _by_table(b)
    for t in ta.keys() & tb.keys():
        for x in ta[t]:
            for y in tb[t]:
                if x["obj_id"] is None and y["obj_id"] is not None:
                    out.add(("table_value", True))
                elif y["obj_id"] is None and x["obj_id"] is not None:
                    out.add(("table_value", False))
                elif x["obj_id"] is None or x["obj_id"] != y["obj_id"]:
                    continue
                elif x["value_id"] is None or y["value_id"] is None:
                    if x["value_id"] != y["value_id"]:
                        out.add(("same_axis", True))  # an axis name and a value of that axis
                elif x["value_id"] == y["value_id"]:
                    continue
                elif y["value_id"] in _ancestors(parents, t, x["obj_id"], x["value_id"]):
                    out.add(("parent_child", False))  # y is the parent
                elif x["value_id"] in _ancestors(parents, t, y["obj_id"], y["value_id"]):
                    out.add(("parent_child", True))
                elif parents.get((t, x["obj_id"]), {}).get(x["value_id"]) == \
                        parents.get((t, y["obj_id"]), {}).get(y["value_id"]):
                    out.add(("sibling", True))
                else:
                    out.add(("same_axis", True))
    return out


def sense_relations(sense_ids: list[str], senses: dict, mention_kept: dict, layer: CatalogLayer | None) -> list[dict]:
    """Relations among the senses of different name mentions (alternative senses of one mention are not related)."""
    if layer is None:
        return []
    from ..catalog.relations import relations_of

    parents = relations_of(layer).parents
    holders: dict[str, set[str]] = {z: set() for z in sense_ids}
    for mid, ids in mention_kept.items():
        for z in ids:
            if z in holders:
                holders[z].add(mid)
    out: list[dict] = []
    seen: set = set()
    for i, a in enumerate(sense_ids):
        for b in sense_ids[i + 1:]:
            if not any(x != y for x in holders[a] for y in holders[b]):
                continue
            for kind, a_first in sorted(_pair_kinds(senses[a]["rows"], senses[b]["rows"], parents)):
                pair = [a, b] if a_first or kind in ("same_axis", "sibling") else [b, a]
                key = (kind, tuple(pair) if kind in ("parent_child", "table_value") else tuple(sorted(pair)))
                if key not in seen:
                    seen.add(key)
                    out.append({"kind": kind, "sense_ids": pair})
    return out


# ---------------------------------------------------------------- payload


def _size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


def _shrink(payload: dict, step: str) -> None:
    if step == "condition_attrs":
        for c in payload["conditions"]:
            c["attrs"] = {}
    elif step == "candidate_paths":
        for row in payload["candidate_summary"]:
            row["path"] = None
    elif step == "relations":
        if payload["relations"]:
            payload["truncation"]["relations_omitted"] = True
        payload["relations"] = []
    payload["truncation"]["shrunk"].append(step)


def local_mention_spans(text: TextView, conditions: list[Condition], segments: list[Segment],
                        mentions: list[Mention], layer: CatalogLayer | None) -> dict[str, list[int]]:
    """Mention ID -> [start, end) (``mentions.mention_spans``), only when the recount gives the mentions handed in
    (same IDs, kinds and segments); else empty (the verification then reads subquotes by catalog name only)."""
    from .mentions import mention_spans

    spans = mention_spans(text, conditions, segments, layer)
    if len(spans) != len(mentions):
        return {}
    for m in mentions:
        sp = spans.get(m["mention_id"])
        if sp is None:
            return {}
        sids = [s["segment_id"] for s in segments if sp[0] < s["span"][1] and s["span"][0] < sp[1]]
        if sids != m["segment_ids"]:
            return {}
    return spans


class _ConditionToken:
    """A basis condition seen as the condition token the rules path reads (kind ``cond``), so that the existing slot
    and swap rule (``table._exclusive``, ``table._basis_slot``) and ``reading._is_byeol`` apply to it unchanged."""

    kind = "cond"
    inner: tuple = ()

    def __init__(self, cond: Condition):
        self.cond = cond
        self.cond_kind = cond["kind"]


def basis_exclusive_pairs(conditions: list[Condition], layer: CatalogLayer | None) -> list[list[str]]:
    """Plan 19 stage 3: pairs of basis conditions of the question that the rules path holds exclusive (two values of
    one slot of the condition word list, or words that stand in for each other in table names: ``table._exclusive``
    with the catalog swap pairs of ``catalog_words``), as the view entries ``reading.read_item`` makes of them. A
    classification X-byeol word is a breakdown axis there (``reading._is_byeol``), never such a value. No new basis
    list; never sent (``local`` only)."""
    from .phrases import key_of
    from .reading import Relations, _is_byeol
    from .table import _exclusive

    rel = Relations(layer)
    entries = []
    for c in conditions:
        tok = _ConditionToken(c)
        if c["kind"] != "basis" or _is_byeol(tok):
            continue
        entries.append((c["id"], ((c.get("attrs") or {}).get("axis", "basis"), key_of(str(c.get("value"))), tok)))
    out = []
    for i, (a, x) in enumerate(entries):
        for b, y in entries[i + 1:]:
            if _exclusive(x, y, rel):
                out.append([a, b])
    return out


def build_payload(text: TextView, conditions: list[Condition], segments: list[Segment], mentions: list[Mention],
                  layer: CatalogLayer | None = None) -> tuple[dict, dict]:
    """(payload, local). ``local``: the sense table (``build_senses``, with the question's conditions: plan 19 stage 5
    order, omitted senses and their reason), ``segment_markers``, ``condition_segments``,
    ``mention_spans`` (mention ID -> raw span, for subquote checks; never sent), ``basis_exclusive`` (plan 19 stage 3:
    ``basis_exclusive_pairs``; never sent) and ``sense_ids`` (the senses sent, in order)."""
    table = build_senses(mentions, conditions)
    local = {**table, "segment_markers": segment_markers(segments, mentions),
             "condition_segments": condition_segments(conditions, mentions),
             "mention_spans": local_mention_spans(text, conditions, segments, mentions, layer),
             "basis_exclusive": basis_exclusive_pairs(conditions, layer), "sense_ids": []}
    if not segments or not text["raw"].strip():
        return _limit("empty_question", len(segments), 1), local
    if len(segments) > LIMITS["segments"]:
        return _limit("segments", len(segments), LIMITS["segments"]), local
    senses, kept = table["senses"], table["mention_kept"]
    summary: list[dict] = []
    sent: list[str] = []
    for m in mentions:
        if m["kind"] != "name":
            continue
        ids = kept.get(m["mention_id"], [])
        if not ids:
            summary.append({"mention_id": m["mention_id"], "segment_ids": list(m["segment_ids"]), "sense_id": None,
                            "name": None, "role": None, "path": None})
        for z in ids:
            s = senses[z]
            summary.append({"mention_id": m["mention_id"], "segment_ids": list(m["segment_ids"]), "sense_id": z,
                            "name": s["name"], "role": s["role"], "path": s["path"]})
            if z not in sent:
                sent.append(z)
    sent.sort(key=lambda z: int(z[1:]))
    local["sense_ids"] = sent
    relations = sense_relations(sent, senses, kept, layer)
    omitted_senses = any(len(table["mention_senses"][mid]) > len(ids) for mid, ids in kept.items())
    markers = local["segment_markers"]
    payload = {
        "raw": text["raw"],
        "segments": [{"id": s["segment_id"], "text": s["quote"], "kind": s["kind"], "marker": markers[s["segment_id"]]}
                     for s in segments],
        "conditions": [_condition_row(c, local["condition_segments"].get(c["id"], [])) for c in conditions],
        "candidate_summary": summary,
        "relations": relations[:LIMITS["relations"]],
        "truncation": {"senses_omitted": omitted_senses, "relations_omitted": len(relations) > LIMITS["relations"],
                       "unclassified_roles": any(senses[z]["role"] == "unclassified" for z in sent), "shrunk": []},
        "limits": {"requirements": LIMITS["requirements"], "alternatives": LIMITS["alternatives"]},
    }
    for step in POLICY["shrink_steps"]["steps"]:
        if _size(payload) <= LIMITS["payload_chars"]:
            break
        _shrink(payload, step)
    if _size(payload) > LIMITS["payload_chars"]:
        return _limit("payload_chars", _size(payload), LIMITS["payload_chars"]), local
    return payload, local
