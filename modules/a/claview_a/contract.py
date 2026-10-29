"""Model contract of the table-level path (plan 37, step 2): input assembly, output schema, parsing.

Input (``build_payload``): the raw question, the cards (``cards.CARD_LEGEND`` once, then every card) and the ask axes
with their labels. Nothing of the current path's analysis (segments, conditions, senses, scopes, period hints) goes in.
A payload over ``PAYLOAD_LIMIT`` characters is refused (``input_too_large``), never cut.

Output (``output_schema``, strict JSON schema): statistics (what the question asks to see, quoted), splitters, periods,
frequencies and calculations (quoted, never resolved here), choices (one table per statistic, by card reference
"gN.tN", with evidence quoted from that table's card), asks (an ask axis, one table per value), missing (statistics
with no card table, with search words), alternatives (record only).

Parsing (``parse_output``) checks the shape, the internal references and the structure that needs no judgement
(Codex review of step 2, user 2026-10-27 21:12 UTC): JSON, schema, no empty or blank string, statistic IDs (unique,
s1, s2 ...), every ``for`` non-empty, without repeats and naming a statistic, choices with evidence, missing rows with
search words, asks with two or more options of distinct values, every statistic in exactly one kind of decision
(choices, asks or missing) and at most once in choices and in missing, one ask row per statistic and axis (several
axes of one statistic are allowed), and each table reference resolved to a real table ID of the cards (``cards.refs``;
a real ID of a card is accepted too). An unresolved reference is a problem, not a crash. A parsed answer is not an
accepted one: whether a choice is right (evidence on the card, quotes in the question, options naming distinct
tables, period cover, the final table, ask conversion) is step 3.

Nothing here names a table, case or question.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .transport.canonical import canonical

from .cards import ASK_AXES, CARD_LEGEND, refs, render_cards

CONTRACT_VERSION = "tl-contract-2026-10-28.3"  # .3: evidence per ask option, required and non-empty
# answers written to an older contract, read only by the replay path that names it (user 2026-10-28 06:47 UTC)
LEGACY_CONTRACTS = ("tl-contract-2026-10-28.2",)  # no evidence field on ask options
SCHEMA_NAME = "tl_table_judgement"
PAYLOAD_LIMIT = 24_000  # characters of the canonical payload (the current path's input limit)
PROMPT_PATH = Path(__file__).resolve().parent / "data" / "prompt_tl.md"
EVIDENCE_KINDS = ("title", "bracket", "item")
_STAT_ID = re.compile(r"s[1-9]\d*")
_REF = re.compile(r"g[1-9]\d*\.t[1-9]\d*")


class ContractError(ValueError):
    """The input cannot be built or the output cannot be read; ``code`` names the failure kind."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------- input


def build_payload(question: str, cards: list[dict], unresolved: list[dict] | None = None) -> dict:
    """The judge's input. ``unresolved`` (a re-judgment after a re-search only, user 2026-10-29 08:18 UTC): the
    statistics the first judgment left missing with the condition quotes it attached to them; the first payload
    never has the key."""
    payload = {"question": question, "cards": CARD_LEGEND + "\n" + render_cards(cards), "ask_axes": dict(ASK_AXES)}
    if unresolved is not None:
        payload["unresolved"] = unresolved
    size = len(canonical(payload))
    if size > PAYLOAD_LIMIT:
        raise ContractError("input_too_large", f"{size} > {PAYLOAD_LIMIT}")
    return payload


# ---------------------------------------------------------------- output schema


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def output_schema() -> dict:
    s = {"type": "string"}
    ids = _arr(s)
    quoted = _arr(_obj({"quote": s, "for": ids}))
    evidence = _arr(_obj({"kind": {"type": "string", "enum": list(EVIDENCE_KINDS)}, "text": s}))
    return _obj({
        "statistics": _arr(_obj({"id": s, "quote": s, "name": s})),
        "splitters": _arr(_obj({"quote": s, "axis": {"type": "string", "enum": [*ASK_AXES, "other"]}, "for": ids})),
        "periods": quoted,
        "frequencies": quoted,
        "calculations": quoted,
        "choices": _arr(_obj({"for": ids, "table": s, "evidence": evidence})),
        "asks": _arr(_obj({"for": s, "axis": {"type": "string", "enum": list(ASK_AXES)},
                           "options": _arr(_obj({"value": s, "table": s, "evidence": evidence}))})),
        "missing": _arr(_obj({"for": ids, "search_words": _arr(s)})),
        "alternatives": _arr(_obj({"for": ids, "table": s, "why": s})),
    })


@dataclass(frozen=True)
class Settings:
    model: str
    effort: str = "low"
    max_output_tokens: int = 4096

    def request(self, payload: dict) -> dict:
        """The Responses API request (the same shape as the current path's ``ResponseSettings.request``)."""
        return {"model": self.model, "reasoning": {"effort": self.effort}, "instructions": prompt_text(),
                "input": canonical(payload), "text": {"format": {"type": "json_schema", "name": SCHEMA_NAME,
                                                                 "strict": True, "schema": output_schema()}},
                "max_output_tokens": self.max_output_tokens, "store": False, "service_tier": "default"}


# ---------------------------------------------------------------- parsing


def _conforms(value, schema: dict, path: str = "$") -> str | None:
    """First place where ``value`` breaks the schema subset used here (object, array, string, enum), else None."""
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            return f"{path}: not an object"
        props = schema["properties"]
        if set(value) != set(props):
            return f"{path}: keys {sorted(set(value) ^ set(props))}"
        for key, sub in props.items():
            if (err := _conforms(value[key], sub, f"{path}.{key}")) is not None:
                return err
        return None
    if kind == "array":
        if not isinstance(value, list):
            return f"{path}: not an array"
        for i, item in enumerate(value):
            if (err := _conforms(item, schema["items"], f"{path}[{i}]")) is not None:
                return err
        return None
    if not isinstance(value, str):
        return f"{path}: not a string"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: {value!r} not allowed"
    return None


def _resolve(ref: str, by_ref: dict[str, str], ids: set[str]) -> str | None:
    if _REF.fullmatch(ref):
        return by_ref.get(ref)
    return ref if ref in ids else None


def _blank(value, path: str = "$") -> str | None:
    """First string that is empty or blank, else None."""
    if isinstance(value, str):
        return path if not value.strip() else None
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    for key, sub in items:
        if (got := _blank(sub, f"{path}.{key}" if isinstance(value, dict) else f"{path}[{key}]")) is not None:
            return got
    return None


def _structure(out: dict, strict_options: bool = True) -> None:
    """Structure errors that need no judgement (module docstring); raises ``ContractError``."""
    for key in ("splitters", "periods", "frequencies", "calculations", "choices", "missing", "alternatives"):
        for i, row in enumerate(out[key]):
            if not row["for"]:
                raise ContractError("empty_for", f"{key}[{i}]")
            if len(set(row["for"])) != len(row["for"]):
                raise ContractError("duplicate_for", f"{key}[{i}]")
    for i, c in enumerate(out["choices"]):
        if not c["evidence"]:
            raise ContractError("empty_evidence", f"choices[{i}]")
    for i, a in enumerate(out["asks"]):
        for j, o in enumerate(a["options"]):
            if strict_options and not o["evidence"]:
                raise ContractError("empty_option_evidence", f"asks[{i}].options[{j}]")
    for i, m in enumerate(out["missing"]):
        if not m["search_words"]:
            raise ContractError("empty_search_words", f"missing[{i}]")
    seen_axes: set[tuple[str, str]] = set()
    for i, a in enumerate(out["asks"]):
        if len(a["options"]) < 2:
            raise ContractError("ask_options_too_few", f"asks[{i}]")
        values = [o["value"].strip() for o in a["options"]]
        if len(set(values)) != len(values):
            raise ContractError("ask_option_value_duplicate", f"asks[{i}]")
        if (a["for"], a["axis"]) in seen_axes:
            raise ContractError("ask_axis_repeated", f"asks[{i}]: {a['for']} {a['axis']}")
        seen_axes.add((a["for"], a["axis"]))
    kinds: dict[str, set[str]] = {}
    for key in ("choices", "missing"):
        seen: set[str] = set()
        for row in out[key]:
            for sid in row["for"]:
                if sid in seen:
                    raise ContractError("statistic_repeated", f"{key}: {sid}")
                seen.add(sid)
                kinds.setdefault(sid, set()).add(key)
    for a in out["asks"]:
        kinds.setdefault(a["for"], set()).add("asks")
    for st in out["statistics"]:
        got = kinds.get(st["id"], set())
        if not got:
            raise ContractError("statistic_unplaced", st["id"])
        if len(got) > 1:
            raise ContractError("statistic_multi_kind", f"{st['id']}: {sorted(got)}")


def parse_output(text: str, cards: list[dict], answer_contract: str = CONTRACT_VERSION) -> dict:
    """{output, tables, problems}: the parsed output, every table reference resolved to a real table ID (None when
    unresolved), and the non-fatal problems (unresolved references). A broken shape or structure raises
    ``ContractError`` with its code: ``not_json``, ``schema``, ``empty_string``, ``statistic_id``, ``statistic_ref``,
    ``empty_for``, ``duplicate_for``, ``empty_evidence``, ``empty_search_words``, ``ask_options_too_few``,
    ``ask_option_value_duplicate``, ``ask_axis_repeated``, ``statistic_repeated``, ``statistic_multi_kind``,
    ``statistic_unplaced``, ``empty_option_evidence`` (contract .3). ``answer_contract`` names the contract the
    answer was written to: the current one (live runs) or a legacy one (a replay of a stored answer, read with that
    contract's absences recorded under ``problems``)."""
    try:
        out = json.loads(text)
    except (TypeError, ValueError):
        raise ContractError("not_json") from None
    if answer_contract not in (CONTRACT_VERSION, *LEGACY_CONTRACTS):
        raise ValueError(f"unknown answer contract {answer_contract}")
    legacy = []
    if answer_contract in LEGACY_CONTRACTS and isinstance(out, dict) and isinstance(out.get("asks"), list):
        # contract .2 had no evidence field on ask options: the field is absent by the contract of the time, not
        # broken by the model. Read as an empty list and recorded as an absence, never as a violation of .3
        for i, a in enumerate(out["asks"]):
            for j, o in enumerate((a.get("options") or []) if isinstance(a, dict) else []):
                if isinstance(o, dict) and "evidence" not in o:
                    o["evidence"] = []
                    legacy.append(f"asks[{i}].options[{j}]")
    if (err := _conforms(out, output_schema())) is not None:
        raise ContractError("schema", err)
    if (blank := _blank(out)) is not None:
        raise ContractError("empty_string", blank)
    stat_ids = [s["id"] for s in out["statistics"]]
    if not stat_ids or any(not _STAT_ID.fullmatch(i) for i in stat_ids) or len(set(stat_ids)) != len(stat_ids):
        raise ContractError("statistic_id", ", ".join(stat_ids) or "none")
    known = set(stat_ids)
    fors = [f for key in ("splitters", "periods", "frequencies", "calculations", "choices", "missing", "alternatives")
            for row in out[key] for f in row["for"]] + [a["for"] for a in out["asks"]]
    unknown = sorted(set(fors) - known)
    if unknown:
        raise ContractError("statistic_ref", ", ".join(unknown))
    _structure(out, strict_options=answer_contract == CONTRACT_VERSION)
    by_ref = refs(cards)
    ids = set(by_ref.values())
    tables: dict[str, str | None] = {}
    problems: list[dict] = []
    places = [(f"choices[{i}]", c["table"]) for i, c in enumerate(out["choices"])]
    places += [(f"asks[{i}].options[{j}]", o["table"]) for i, a in enumerate(out["asks"])
               for j, o in enumerate(a["options"])]
    places += [(f"alternatives[{i}]", a["table"]) for i, a in enumerate(out["alternatives"])]
    for where, ref in places:
        got = _resolve(ref, by_ref, ids)
        tables[ref] = got
        if got is None:
            problems.append({"code": "table_ref_unknown", "where": where, "ref": ref})
    if legacy:
        problems.append({"code": "option_evidence_field_absent", "contract": answer_contract, "where": legacy,
                         "note": "absent in the contract of the answer; not a violation"})
    return {"version": CONTRACT_VERSION, "answer_contract": answer_contract, "output": out, "tables": tables,
            "problems": problems}
