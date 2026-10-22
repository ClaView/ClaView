"""build_schema (plan 15 r3, 5.1-5.2): the per-question JSON Schema of the decomposition proposal, and ``validate``,
a reader of the JSON Schema subset ``build_schema`` writes (no third-party validator in the environment).

Rules of 5.1: every object has ``additionalProperties: false`` and lists every field as required (none: null or
[]); segment, condition and sense IDs are per-question enumerations, requirement IDs the slots r1..r<limit>; an empty
ID set never gives an empty enumeration (the array is limited to ``maxItems: 0`` or the field is null-only); no
numeric position, catalog code or confidence field. The objects and their field names come from the shapes of
``contract/shapes.py`` (``Proposal`` and the shapes it holds): a field added there without a schema here is an error.
"""

from __future__ import annotations

import typing
from typing import get_args

from ..contract import shapes
from .decompose_payload import LIMITS, POLICY, is_input_limit

REF = "#/$defs/Ref"
ID_FIELDS = frozenset({"segment_ids", "sense_id", "condition_id", "condition_ids", "requirement_ids", "id"})


def requirement_slots() -> list[str]:
    return [f"r{n}" for n in range(1, LIMITS["requirements"] + 1)]


def _literal(tp) -> list[str]:
    return list(get_args(tp))


def _enum(values: list[str]) -> dict:
    return {"type": "string", "enum": list(values)}


def _id_or_null(values: list[str]) -> dict:
    return {"anyOf": [_enum(values), {"type": "null"}]} if values else {"type": "null"}


def _id_one(values: list[str]) -> dict:
    return _enum(values) if values else {"type": "null"}


def _id_array(values: list[str], max_items: int | None = None, min_items: int | None = None) -> dict:
    if not values:
        return {"type": "array", "items": {"type": "null"}, "maxItems": 0}
    out: dict = {"type": "array", "items": _enum(values)}
    if min_items is not None:
        out["minItems"] = min_items
    if max_items is not None:
        out["maxItems"] = max_items
    return out


def _refs() -> dict:
    return {"type": "array", "items": {"$ref": REF}}


def _ref_or_null() -> dict:
    return {"anyOf": [{"$ref": REF}, {"type": "null"}]}


def _object(tp, props: dict) -> dict:
    fields = list(typing.get_type_hints(tp))
    if set(fields) != set(props):
        raise ValueError(f"schema of {tp.__name__} differs from its shape: {sorted(set(fields) ^ set(props))}")
    return {"type": "object", "additionalProperties": False, "required": fields,
            "properties": {f: props[f] for f in fields}}


def build_schema(payload: dict) -> dict:
    """The JSON Schema of one question's proposal (``Proposal``)."""
    if is_input_limit(payload):
        raise ValueError("no schema for an input_limit payload (the rules path is taken)")
    seg_ids = [s["id"] for s in payload["segments"]]
    if not seg_ids:
        raise ValueError("no schema for a question without segments")
    cond_ids = [c["id"] for c in payload["conditions"]]
    rel_kinds = set(POLICY["relation_condition_kinds"]["kinds"])
    rel_ids = [c["id"] for c in payload["conditions"] if c["kind"] in rel_kinds]
    sense_ids = list(dict.fromkeys(r["sense_id"] for r in payload["candidate_summary"] if r["sense_id"] is not None))
    slots = requirement_slots()
    n_req = len(slots)
    req_ids = _id_array(slots, max_items=n_req)
    defs = {
        "Ref": _object(shapes.ProposalRef, {
            "segment_ids": _id_array(seg_ids, min_items=1),
            "subquote": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "sense_id": _id_or_null(sense_ids)}),
        "Breakdown": _object(shapes.ProposalBreakdown, {
            "kind": _enum(_literal(shapes.BreakdownKind)), "axis": _ref_or_null(), "values": _refs(),
            "marker_refs": _refs()}),
        "Requirement": _object(shapes.RequirementProposal, {
            "id": _enum(slots), "indicator": _ref_or_null(), "targets": _refs(), "basis_refs": _refs(),
            "context": _ref_or_null(), "breakdowns": {"type": "array", "items": {"$ref": "#/$defs/Breakdown"}}}),
        "ConditionBinding": _object(shapes.ConditionBinding, {
            "condition_id": _id_one(cond_ids), "scope": _enum(_literal(shapes.BindingScope)),
            "requirement_ids": req_ids, "name_refs": _refs()}),
        "Relation": _object(shapes.RelationProposal, {"condition_id": _id_one(rel_ids), "requirement_ids": req_ids}),
        "PolarityLink": _object(shapes.PolarityLink, {
            "kind": _enum(_literal(shapes.PolarityKind)), "marker_refs": _refs(), "negative_refs": _refs(),
            "positive_refs": _refs(), "requirement_ids": req_ids}),
        "UnusedRef": _object(shapes.UnusedRef, {"ref": {"$ref": REF}, "reason": _enum(_literal(shapes.UnusedReason))}),
        "Ambiguity": _object(shapes.Ambiguity, {
            "refs": _refs(), "kind": _enum(_literal(shapes.AmbiguityKind)), "condition_ids": _id_array(cond_ids),
            "reason_code": _enum(_literal(shapes.AmbiguityReason))}),
    }
    bindings: dict = {"type": "array", "items": {"$ref": "#/$defs/ConditionBinding"}}
    if not cond_ids:
        bindings["maxItems"] = 0
    relations: dict = {"type": "array", "items": {"$ref": "#/$defs/Relation"}}
    if not rel_ids:
        relations["maxItems"] = 0
    defs["Reading"] = _object(shapes.Reading, {
        "requirements": {"type": "array", "items": {"$ref": "#/$defs/Requirement"}, "minItems": 1, "maxItems": n_req},
        "condition_bindings": bindings, "relations": relations,
        "polarity_links": {"type": "array", "items": {"$ref": "#/$defs/PolarityLink"}},
        "request_refs": _refs(), "unused_refs": {"type": "array", "items": {"$ref": "#/$defs/UnusedRef"}}})
    alternative = {"anyOf": [{"$ref": "#/$defs/Reading"}, {"type": "null"}]} if LIMITS["alternatives"] >= 1 \
        else {"type": "null"}
    top = _object(shapes.Proposal, {"primary": {"$ref": "#/$defs/Reading"}, "alternative": alternative,
                                    "ambiguities": {"type": "array", "items": {"$ref": "#/$defs/Ambiguity"}}})
    return {"$defs": defs, **top}


# ---------------------------------------------------------------- the subset reader


def _type_ok(value, t: str) -> bool:
    if t == "null":
        return value is None
    if t == "string":
        return isinstance(value, str)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    raise ValueError(f"type {t!r} is outside the schema subset")


def _resolve(schema: dict, root: dict) -> dict:
    while "$ref" in schema:
        name = schema["$ref"]
        if not name.startswith("#/$defs/"):
            raise ValueError(f"reference {name!r} is outside the schema subset")
        schema = root["$defs"][name.removeprefix("#/$defs/")]
    return schema


def _err(path: str, rule: str, detail: str, **extra) -> dict:
    field = path.rsplit(".", 1)[-1].split("[", 1)[0] if "." in path else ""
    return {"path": path, "rule": rule, "field": field, "detail": detail, **extra}


def _validate(value, schema: dict, root: dict, path: str, out: list[dict]) -> None:
    schema = _resolve(schema, root)
    if "anyOf" in schema:
        tries = []
        for arm in schema["anyOf"]:
            errs: list[dict] = []
            _validate(value, arm, root, path, errs)
            if not errs:
                return
            tries.append((arm, errs))
        # report the arm of the value's own type (an unknown ID: the enumeration arm), else the first
        typed = [errs for arm, errs in tries if "type" in _resolve(arm, root)
                 and _type_ok(value, _resolve(arm, root)["type"])]
        out += typed[0] if typed else tries[0][1]
        return
    t = schema.get("type")
    if t is not None and not _type_ok(value, t):
        out.append(_err(path, "type", f"expected {t}", got=type(value).__name__))
        return
    if "enum" in schema and value not in schema["enum"]:
        out.append(_err(path, "enum", f"{value!r} is not allowed"))
    if t == "object":
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                out.append(_err(f"{path}.{name}", "required", "missing"))
        if schema.get("additionalProperties") is False:
            out += [_err(f"{path}.{k}", "additional", "not allowed") for k in value if k not in props]
        for k, sub in props.items():
            if k in value:
                _validate(value[k], sub, root, f"{path}.{k}", out)
    elif t == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(_err(path, "minItems", f"{len(value)} < {schema['minItems']}", bound=schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(_err(path, "maxItems", f"{len(value)} > {schema['maxItems']}", bound=schema["maxItems"]))
        if "items" in schema:
            for k, v in enumerate(value):
                _validate(v, schema["items"], root, f"{path}[{k}]", out)


def validate(instance, schema: dict) -> list[dict]:
    """Errors ``{path, rule, field, detail[, got, bound]}`` of an instance against a schema of the subset (type, enum, properties,
    required, additionalProperties, items, minItems, maxItems, anyOf, local $ref); empty when it is valid."""
    out: list[dict] = []
    _validate(instance, schema, schema, "$", out)
    return out


def lint_schema(schema: dict) -> list[str]:
    """5.1 rules over a schema: every object closed with every field required, no empty enumeration, no numeric
    field."""
    out: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                if node.get("additionalProperties") is not False:
                    out.append(f"{path}: object not closed")
                if set(node.get("required", [])) != set(node.get("properties", {})):
                    out.append(f"{path}: not every field required")
            if "enum" in node and not node["enum"]:
                out.append(f"{path}: empty enum")
            if node.get("type") in ("number", "integer"):
                out.append(f"{path}: numeric field")
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for k, v in enumerate(node):
                walk(v, f"{path}[{k}]")

    walk(schema, "$")
    return out
