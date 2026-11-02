"""Model contract of the table-level path (plan 37, step 2): input assembly, output schema, parsing.

Input (``build_payload``): the raw question, the cards (``cards.CARD_LEGEND`` once, then every card) and the ask axes
with their labels. Nothing of the current path's analysis (segments, conditions, senses, scopes, period hints) goes in.
A payload over ``PAYLOAD_LIMIT`` characters is refused (``input_too_large``), never cut.

Output (``output_schema``, strict JSON schema): statistics (what the question asks to see, quoted), splitters, periods
and frequencies (quoted; from contract .8 with their meaning structured by the model: every statistic has a period row
and a frequency row, ``not_stated`` when the user said none, and the code computes dates from the values), calculations
(quoted, never resolved here), choices (one table per statistic, by card reference
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

from .cards import ASK_AXES, CARD_LEGEND, refs, render_cards
from .conditions.words import BASIS_AXES
from .transport.canonical import canonical

# .3: evidence per ask option, required and non-empty; .4: splitter value and polarity, the period meaning (and the
# statistics a same-range period refers to), the aggregation rows (plan 56 order 6-3, users 2026-10-31 15:23, 17:53,
# 17:56 and 18:28 UTC); .5: the average basis of a period average and its quote on the aggregation rows (plan 56
# order 6-3c, user 2026-10-31 20:10 UTC); .6: the changes a re-judgment makes to a target's basis rows, each with its
# quote and reason, and the re-judgment's input of fixed context and targets (plan 56 order 9, bundle 10);
# .7: a table-specific scope per statistic with original-question source excerpts, separate from its short quote;
# .8: the period and frequency rows carry their meaning structured by the model (form, calendar values or a count back
# from as_of, read failure; a frequency value or not_stated), one row of each per statistic at least (BOK20 live check
# 11-02, user 02:37 UTC: the model structures, the code computes and checks)
# .9: calendar offsets distinct from recent windows, incomplete user bounds and unsupported meanings distinct from
# internal interpretation errors. The row shape stays the same; older schemas keep their own enum sets.
# .10: a recent range may name its calendar end; the code computes the start from the count and unit.
CONTRACT_VERSION = "tl-contract-2026-10-28.10"
# answers written to an older contract, read only by the replay path that names it (user 2026-10-28 06:47 UTC)
LEGACY_CONTRACTS = ("tl-contract-2026-10-28.2",  # no evidence field on ask options
                    "tl-contract-2026-10-28.3")  # no polarity, period meaning or aggregation fields
# earlier contracts with the polarity, period meaning and aggregation fields, read as written (no average basis)
EARLIER_CONTRACTS = ("tl-contract-2026-10-28.4",  # no average basis
                     "tl-contract-2026-10-28.5",  # no changes of a re-judgment
                     "tl-contract-2026-10-28.6",  # no grounded table-specific focus
                     "tl-contract-2026-10-28.7",  # periods and frequencies quoted only (read by the quote reader)
                     "tl-contract-2026-10-28.8",  # structured periods without calendar offsets or partial bounds
                     "tl-contract-2026-10-28.9")  # relative ranges have no explicit calendar endpoint
CHANGES_CONTRACTS = ("tl-contract-2026-10-28.6", "tl-contract-2026-10-28.7", "tl-contract-2026-10-28.8",
                     "tl-contract-2026-10-28.9", "tl-contract-2026-10-28.10")
FOCUS_CONTRACTS = ("tl-contract-2026-10-28.7", "tl-contract-2026-10-28.8", "tl-contract-2026-10-28.9",
                   "tl-contract-2026-10-28.10")
# the contracts whose period and frequency rows carry the model's structured meaning (``periods.semantic``)
SEMANTIC_PERIOD_CONTRACTS = ("tl-contract-2026-10-28.8", "tl-contract-2026-10-28.9", "tl-contract-2026-10-28.10")
# Calendar offsets and incomplete user bounds started in .9; bounded recent ranges started in .10.
CALENDAR_PERIOD_CONTRACTS = ("tl-contract-2026-10-28.9", "tl-contract-2026-10-28.10")
BOUNDED_RECENT_CONTRACTS = ("tl-contract-2026-10-28.10",)
# the contracts whose ask options carry evidence (a list, never one version compared: plan 56 order 6-3)
OPTION_EVIDENCE_CONTRACTS = ("tl-contract-2026-10-28.3", "tl-contract-2026-10-28.4", "tl-contract-2026-10-28.5",
                             "tl-contract-2026-10-28.6", "tl-contract-2026-10-28.7", "tl-contract-2026-10-28.8",
                             "tl-contract-2026-10-28.9", "tl-contract-2026-10-28.10")
# the instructions' own version (the prompt text is also hashed in the run info)
# .2: the average basis and quotes that state it (plan 56 6-3c); .3: the re-judgment of the targets only and its
# changes, an ask only when the meaning differs and two grounded alternatives remain, options of other cards (plan 56
# orders 9 and 12, ⑥-다); .6: periods and frequencies structured by meaning, one row of each per statistic (contract .8)
PROMPT_VERSION = "tl-prompt-2026-11-03.10"
POLARITIES = ("required", "any_of", "excluded", "allowed", "undetermined", "unreadable", "not_basis")
PERIOD_MEANINGS = ("range", "latest_available", "same_as", "user_undecided")
# contract .8: an excluded period (a first-stage limit, as before) and a period the user did not state
SEMANTIC_PERIOD_MEANINGS = (*PERIOD_MEANINGS, "excluded", "not_stated")
PERIOD_FORMS = ("absolute", "relative", "none")
PERIOD_UNITS = ("Y", "H", "Q", "M", "none")
READ_STATES = ("ok", "failed")
FREQUENCY_VALUES = ("Y", "H", "Q", "M", "not_stated", "unreadable")
FREQUENCY_ANCHORS = ("end", "none")
FREQUENCIES = ("Y", "H", "Q", "M")
METHODS = ("sum", "period_end", "period_average", "weighted", "unknown")
SOURCE_BASES = ("end_balance", "average_balance", "flow", "ratio", "unknown")
AVERAGE_BASES = ("calendar_day", "simple_of_periods", "business_day", "observation_day", "unknown")
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


def build_payload(question: str, cards: list[dict], unresolved: list[dict] | None = None,
                  fixed: dict | None = None, targets: list[dict] | None = None) -> dict:
    """The judge's input. ``unresolved`` (a re-judgment after a re-search only, user 2026-10-29 08:18 UTC): the
    statistics the first judgment left missing with the condition quotes it attached to them. ``fixed`` and
    ``targets`` (the one self-recovery, contract .6, plan 56 order 9): the context that stays (the question and the
    statistics already decided, never to be changed) and the statistics to judge again, each with its quotes, why it
    failed and the facts found. The first payload has none of these keys."""
    payload = {"question": question, "cards": CARD_LEGEND + "\n" + render_cards(cards), "ask_axes": dict(ASK_AXES),
               # contract .4: a splitter's value is one of its axis's values (plan 56 order 6-3)
               "basis_values": {axis: list(BASIS_AXES.get(axis, {})) for axis in ASK_AXES}}
    if unresolved is not None:
        payload["unresolved"] = unresolved
    if fixed is not None:
        payload["fixed"] = fixed
    if targets is not None:
        payload["targets"] = targets
    size = len(canonical(payload))
    if size > PAYLOAD_LIMIT:
        raise ContractError("input_too_large", f"{size} > {PAYLOAD_LIMIT}")
    return payload


# ---------------------------------------------------------------- output schema


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def _enum(values) -> dict:
    return {"type": "string", "enum": list(values)}


def output_schema(contract: str = CONTRACT_VERSION) -> dict:
    """The output schema of ``contract``: the current one, or an older one an answer was written to."""
    s = {"type": "string"}
    ids = _arr(s)
    quoted = _arr(_obj({"quote": s, "for": ids}))
    evidence = _arr(_obj({"kind": _enum(EVIDENCE_KINDS), "text": s}))
    axis = _enum([*ASK_AXES, "other"])
    old = contract in LEGACY_CONTRACTS
    fields = {
        "statistics": _arr(_obj({"id": s, "quote": s, "name": s})),
        "splitters": _arr(_obj({"quote": s, "axis": axis, "for": ids} if old else
                               {"quote": s, "axis": axis, "value": s, "polarity": _enum(POLARITIES), "for": ids})),
        "periods": quoted if old else _arr(_obj({"quote": s, "for": ids, "meaning": _enum(PERIOD_MEANINGS),
                                                 "refers_to": ids})),
        "frequencies": quoted,
        "calculations": quoted,
        "choices": _arr(_obj({"for": ids, "table": s, "evidence": evidence})),
        "asks": _arr(_obj({"for": s, "axis": _enum(ASK_AXES),
                           "options": _arr(_obj({"value": s, "table": s, "evidence": evidence}))})),
        "missing": _arr(_obj({"for": ids, "search_words": _arr(s)})),
        "alternatives": _arr(_obj({"for": ids, "table": s, "why": s})),
    }
    if not old:
        row = {"for": ids, "requested_frequency": _enum(FREQUENCIES), "source_frequency": _enum(FREQUENCIES),
               "method": _enum(METHODS), "source_basis": _enum(SOURCE_BASES), "evidence": evidence,
               "weights": _arr(s)}
        if contract != "tl-contract-2026-10-28.4":
            row |= {"average_basis": _enum(AVERAGE_BASES), "average_evidence": evidence}
        fields["aggregations"] = _arr(_obj(row))
    if contract in CHANGES_CONTRACTS:  # contract .6: a re-judgment's changes of basis rows
        fields["changes"] = _arr(_obj({"for": ids, "axis": axis, "value": s, "from": _enum(POLARITIES),
                                       "to": _enum(POLARITIES), "quote": s, "reason": s}))
    if contract in FOCUS_CONTRACTS:
        fields["focuses"] = _arr(_obj({"for": ids, "text": s, "quotes": _arr(s)}))
    if contract in SEMANTIC_PERIOD_CONTRACTS:  # contract .8: the meaning of each period and frequency, structured
        meanings = SEMANTIC_PERIOD_MEANINGS
        forms = PERIOD_FORMS
        if contract in CALENDAR_PERIOD_CONTRACTS:
            meanings = (*meanings, "unsupported")
            forms = (*forms, "calendar_offset")
        fields["periods"] = _arr(_obj({"quote": s, "for": ids, "meaning": _enum(meanings),
                                       "refers_to": ids, "form": _enum(forms), "start": s, "end": s,
                                       "count": s, "unit": _enum(PERIOD_UNITS), "read": _enum(READ_STATES)}))
        fields["frequencies"] = _arr(_obj({"quote": s, "for": ids, "value": _enum(FREQUENCY_VALUES),
                                           "anchor": _enum(FREQUENCY_ANCHORS)}))
    return _obj(fields)


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
    for key in ("splitters", "periods", "frequencies", "calculations", "choices", "missing", "alternatives",
                "aggregations", "focuses"):
        for i, row in enumerate(out.get(key) or []):
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
    if "focuses" in out:
        focused = set()
        for i, row in enumerate(out["focuses"]):
            if len(row["for"]) != 1:
                raise ContractError("focus_statistic_count", f"focuses[{i}]")
            sid = row["for"][0]
            if sid in focused:
                raise ContractError("focus_repeated", sid)
            focused.add(sid)
            if not row["quotes"]:
                raise ContractError("empty_focus_quotes", sid)
        missing_focus = sorted({s["id"] for s in out["statistics"]} - focused)
        if missing_focus:
            raise ContractError("focus_missing", ", ".join(missing_focus))
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


def parse_output(text: str, cards: list[dict], answer_contract: str = CONTRACT_VERSION,
                 fixed_ids: tuple[str, ...] | list[str] = ()) -> dict:
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
    if answer_contract not in (CONTRACT_VERSION, *EARLIER_CONTRACTS, *LEGACY_CONTRACTS):
        raise ValueError(f"unknown answer contract {answer_contract}")
    legacy = []
    if answer_contract not in OPTION_EVIDENCE_CONTRACTS and isinstance(out, dict) and isinstance(out.get("asks"), list):
        # contract .2 had no evidence field on ask options: the field is absent by the contract of the time, not
        # broken by the model. Read as an empty list and recorded as an absence, never as a violation of .3
        for i, a in enumerate(out["asks"]):
            for j, o in enumerate((a.get("options") or []) if isinstance(a, dict) else []):
                if isinstance(o, dict) and "evidence" not in o:
                    o["evidence"] = []
                    legacy.append(f"asks[{i}].options[{j}]")
    if (err := _conforms(out, output_schema(answer_contract))) is not None:
        raise ContractError("schema", err)
    if (blank := _blank(out)) is not None:
        raise ContractError("empty_string", blank)
    stat_ids = [s["id"] for s in out["statistics"]]
    if not stat_ids or any(not _STAT_ID.fullmatch(i) for i in stat_ids) or len(set(stat_ids)) != len(stat_ids):
        raise ContractError("statistic_id", ", ".join(stat_ids) or "none")
    known = set(stat_ids)
    fors = [f for key in ("splitters", "periods", "frequencies", "calculations", "choices", "missing", "alternatives",
                          "aggregations", "changes", "focuses") for row in out.get(key) or [] for f in row["for"]] + \
        [a["for"] for a in out["asks"]]
    # a period's refers_to may also name a statistic the re-judgment was given as fixed (``fixed_ids``): it is read,
    # never judged again; every row's ``for`` still names the answer's own statistics only (Q077, owner 11-01 12:26)
    refers = [x for p in out["periods"] for x in p.get("refers_to") or []]
    unknown = sorted((set(fors) - known) | (set(refers) - known - set(fixed_ids)))
    if unknown:
        raise ContractError("statistic_ref", ", ".join(unknown))
    _structure(out, strict_options=answer_contract in OPTION_EVIDENCE_CONTRACTS)
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
