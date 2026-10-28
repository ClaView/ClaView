"""Public A assumptions: adopted values, stable across turns, never proof."""
from __future__ import annotations

import copy
import hashlib
import json
import math

VERSION = "a-assumptions/2026-10-21.1"
FIELDS = {"assumption_id", "stage", "requirement_ids", "requirement_keys", "refs",
          "reason_code", "adopted", "source", "confirmed"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def reading_id(analysis):
    return (analysis.get("decomposition") or {}).get("reading_id") or "rules"


def check(envelope):
    errors = []
    if not isinstance(envelope, dict) or set(envelope) != {"version", "items"}:
        return ["a_assumptions: envelope"]
    if envelope["version"] != VERSION:
        errors.append("a_assumptions: version")
    if not isinstance(envelope["items"], list):
        return errors + ["a_assumptions: items"]
    ids = set()
    for row in envelope["items"]:
        if not isinstance(row, dict) or set(row) != FIELDS:
            errors.append("a_assumptions: fields")
            continue
        for key in ("assumption_id", "reason_code"):
            if not isinstance(row[key], str) or not row[key].strip():
                errors.append(f"a_assumptions: {key}")
        aid = row["assumption_id"]
        if isinstance(aid, str):
            if aid in ids:
                errors.append("a_assumptions: duplicate id")
            ids.add(aid)
        if row["stage"] != "A" or row["source"] not in ("question", "user", "catalog", "default") \
                or type(row["confirmed"]) is not bool:
            errors.append("a_assumptions: provenance")
        refs = row["refs"]
        if not isinstance(refs, dict) or set(refs) != {"segment_ids", "mention_ids", "condition_ids"}:
            errors.append("a_assumptions: refs")
        else:
            for values in refs.values():
                if not _strings(values):
                    errors.append("a_assumptions: ref list")
        keys = row["requirement_keys"]
        valid = isinstance(keys, list) and all(isinstance(k, dict) and set(k) == {"reading_id", "requirement_id"}
                and all(isinstance(v, str) and v.strip() for v in k.values()) for k in keys)
        if not valid or not _strings(row["requirement_ids"]) or (valid and
                set(row["requirement_ids"]) != {k["requirement_id"] for k in keys}):
            errors.append("a_assumptions: requirement keys")
        elif len({(k["reading_id"], k["requirement_id"]) for k in keys}) != len(keys):
            errors.append("a_assumptions: duplicate requirement keys")
        adopted = row["adopted"]
        if not isinstance(adopted, dict) or set(adopted) != {"value", "interpretation"} or not isinstance(
                adopted.get("interpretation"), str) or not adopted["interpretation"].strip():
            errors.append("a_assumptions: adopted")
        elif not _json_value(adopted["value"]):
            errors.append("a_assumptions: adopted JSON value")
    return errors


def _strings(values):
    return isinstance(values, list) and all(isinstance(x, str) and x.strip() for x in values) and len(values) == len(set(values))


def _json_value(value):
    if value is None or type(value) in (str, int, bool):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value(v) for v in value)
    return isinstance(value, dict) and all(isinstance(k, str) and _json_value(v) for k, v in value.items())


def build(state):
    analysis = state.get("analysis") or {}
    reading = reading_id(analysis)
    reqs = {r["id"]: r for r in (analysis.get("interpretation") or {}).get("requirements") or []}
    rows = []

    def add(reason, rids, value, text, *, refs=None, source="default", confirmed=False):
        rids = sorted(set(rids) & set(reqs))
        row = {"stage": "A", "requirement_ids": rids,
               "requirement_keys": [{"reading_id": reading, "requirement_id": r} for r in rids],
               "refs": {k: sorted(set((refs or {}).get(k) or []))
                        for k in ("segment_ids", "mention_ids", "condition_ids")},
               "reason_code": reason, "adopted": {"value": copy.deepcopy(value), "interpretation": text},
               "source": source, "confirmed": confirmed}
        row["assumption_id"] = "a:" + digest([state.get("request_id"), state.get("question_version"), row])
        if row not in rows:
            rows.append(row)

    for seed in (analysis.get("decomposition") or {}).get("assumption_seeds") or []:
        if seed.get("reading_id") not in (None, reading):
            continue
        rids = [k["requirement_id"] for k in seed.get("requirement_keys") or []] or list(reqs)
        add(seed["reason_code"], rids, {r: reqs[r].get("quote") for r in rids if r in reqs},
            "현재 채택한 해석에 미해결 의미가 남아 있습니다.", refs=seed.get("refs"),
            source=seed.get("source") if seed.get("source") in ("question", "catalog", "default") else "default")
    session = ((state.get("a_outcome") or {}).get("parts") or {}).get("session") or {}
    answered = session.get("choices") or {}
    reading_choice = session.get("reading_choice") or {}
    if reading_choice.get("reading_id") == reading:
        add("user_interpretation", list(reqs), reading, "사용자가 선택한 해석을 적용했습니다.", source="user", confirmed=True)
    route = state.get("selection_route") or {}
    eligible = set(route.get("eligible") or [])
    waiting = bool((state.get("control") or {}).get("pending_question")) or route.get("kind") == "ask"
    for s in state.get("selections") or []:
        rid = s["requirement_id"]
        if waiting or rid not in eligible or rid not in reqs or not s.get("tables"):
            continue
        value = [t["table_id"] for t in s["tables"]]
        choice = answered.get(rid)
        if choice and choice.get("tables") == value:
            add("user_selection", [rid], value, "사용자가 선택한 표 묶음을 적용했습니다.", source="user", confirmed=True)
        elif reading_choice or s.get("status") != "auto" or not (analysis.get("judgement") or {}).get("certain"):
            add("first_assignment", [rid], value, "현재 해석의 1순위 표 묶음으로 진행합니다.")
    if (analysis.get("judgement") or {}).get("certain") is False:
        add("interpretation_unsettled", list(reqs), {r: reqs[r].get("quote") for r in reqs},
            "해석의 확실성은 미확정 상태입니다. 조회 성공과 별개로 유지합니다.")
    result = {"version": VERSION, "items": rows}
    errors = check(result)
    if errors:
        raise ValueError("; ".join(errors))
    return result
