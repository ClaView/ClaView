"""Plan 17 requirement-local input projection. Interpretation is never modified here."""

from __future__ import annotations

import copy
import hashlib
import json

from ..catalog.facets import catalog_version
from . import model_refs, structure
from .evidence import requirement_of
from .facts import _condition_signature
from .needs import requirement_needs


def selection_input(analysis: dict, rid: str, layer, as_of: str | None = None, *, with_structure=False) -> dict:
    req = requirement_of(analysis, rid)
    model = model_refs.adopted(analysis)
    record = analysis.get("decomposition") or {}
    refs = model_refs.references(analysis, rid) if model else None
    by_id = {c["id"]: c for c in analysis.get("conditions") or []}
    own = list(req.get("condition_ids") or [])
    shared = list(req.get("shared_condition_ids") or [])
    ambiguous = list(req.get("ambiguous_condition_ids") or [])
    used = list(dict.fromkeys([*own, *shared, *ambiguous]))
    if any(cid not in by_id for cid in used):
        raise ValueError("selection input references an unknown condition")
    reading_id = record["reading_id"] if model else "rules"
    if model:
        # Model bindings remain the canonical proposal bindings. Their ID map travels
        # beside them; no second, independently edited attribution is manufactured.
        bindings = [b for b in record["bindings"] if b["condition_id"] in used]
        binding_ids = record["requirement_ids"]
    else:
        requirements = analysis["interpretation"]["requirements"]
        bindings = []
        for cid in used:
            members = [r["id"] for r in requirements if cid in [
                *(r.get("condition_ids") or []), *(r.get("shared_condition_ids") or [])]]
            uncertain = [r["id"] for r in requirements if cid in (r.get("ambiguous_condition_ids") or [])]
            scope = "unresolved" if uncertain else "question" if len(members) == len(requirements) else "requirements"
            bindings.append({"condition_id": cid, "scope": scope,
                             "requirement_ids": uncertain if uncertain else [] if scope == "question" else members,
                             "name_refs": []})
        binding_ids = {r["id"]: r["id"] for r in requirements}
    needs = requirement_needs(analysis, rid)
    structured = structure.read(analysis, rid) if with_structure else None
    identity = {
        "reading_id": reading_id, "indicator": req.get("indicator"), "targets": req.get("targets") or [],
        "basis_refs": [r for r in refs or [] if r["place"] == "basis"],
        "context": req.get("context"), "breakdown": req.get("breakdown") or [],
        "references": refs if model else {"quote": req.get("quote"), "span": req.get("span")},
        "own": sorted(_condition_signature(by_id[c]) for c in own),
        "shared": sorted(_condition_signature(by_id[c]) for c in shared),
        "ambiguous": sorted(_condition_signature(by_id[c]) for c in ambiguous),
    }
    if structured is not None and structured["state"] == "valid":
        identity["structure"] = structured
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()
    seeds = [s for s in record.get("assumption_seeds") or [] if model and (
        not s.get("requirement_keys") or
        any(k["reading_id"] == reading_id and k["requirement_id"] == rid for k in s["requirement_keys"]))]
    return copy.deepcopy({
        "reading_id": reading_id, "requirement_id": rid, "requirement_key": key,
        "applied_source": "model" if model else "rules", "indicator": req.get("indicator"),
        "targets": req.get("targets") or [], "basis_refs": identity["basis_refs"],
        "context": req.get("context"), "breakdowns": req.get("breakdown") or [],
        "positive_refs": refs or [], "negative_refs": needs["filter"],
        "unresolved_refs": needs["certainty"], "condition_bindings": bindings,
        "binding_requirement_ids": binding_ids, "conditions": [by_id[c] for c in used],
        "assumption_seeds": seeds,
        **({"structure": structured} if with_structure else {}),
        "candidate_occurrences": [{"ref": {k: v for k, v in r.items() if k != "candidates"}, **c}
                                  for r in refs or [] for c in r["candidates"]],
        "as_of": as_of, "catalog_version": catalog_version(layer),
    })
