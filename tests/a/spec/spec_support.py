"""Shared helpers of the specification cases (plan 02, 4-3). Not a test module.

A case is a dict written from one old test row (``key`` = ``file::function[case]`` of tests/a/spec_ledger.csv):
- ``question`` (synthetic sentence of the old test), ``layer`` (name of a synthetic catalog below, or None),
  ``node`` (the new node whose behaviour the case pins down; also the xfail reason),
- an ``expect`` block in the new output shape. Expectations are partial: only what the old test asserted (moved) or
  its new form (changed) is written, so shape checks run with ``partial=True``.

Every case gets two tests: a shape test of the case data that must pass now, and a behaviour test that runs the new
node. Nodes not written yet raise NotImplementedError, so their behaviour tests are
``xfail(strict=True, raises=NotImplementedError, reason="2구간: <node>")``; any other error fails.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from catalog_synth import write_inputs

from claview.a.catalog.layer import load_layer
from claview.a.contract import check as shape
from claview.a.contract import shapes
from claview.a.interpret import conditions as finders
from claview.a.interpret.pipeline import interpret_question
from claview.a.interpret.text import normalize_text
from claview.a.reused import q_rules
from claview.a.select import evidence as ev

MISSING = object()
IMPLEMENTED = frozenset({"find_frequency_conditions", "stamp_months (reused)", "package_scan",
                         # plan 04 M1-1 (2.2 finders)
                         "find_period_conditions", "find_basis_conditions", "find_calc_conditions",
                         "find_unit_conditions", "find_breakdown_conditions", "find_exclude_conditions",
                         # plan 04 M1-3 (2.3-2.5)
                         "split_fragments", "attach_terms", "judge_fragments",
                         # plan 04 cycle 1 (2.6-2.7)
                         "model_slot", "settle_requirements",
                         # plan 04 cycle 1 (3.4 evidence checks)
                         "check_evidence", "check_basis", "check_cross_axis", "check_period", "check_unit"})
FINDERS = {
    "period": "find_period_conditions", "frequency": "find_frequency_conditions", "basis": "find_basis_conditions",
    "unit": "find_unit_conditions", "calc": "find_calc_conditions", "breakdown": "find_breakdown_conditions",
    "exclude": "find_exclude_conditions", "relation": "find_relation_conditions",
    "function": "find_function_conditions", "display": "find_display_conditions",
}
WITH_LAYER = {"find_breakdown_conditions", "find_exclude_conditions"}

# ---------------------------------------------------------------- builders


def span_of(question: str, quote: str, nth: int = 0) -> list[int]:
    start = -1
    for _ in range(nth + 1):
        start = question.find(quote, start + 1)
        if start < 0:
            raise ValueError(f"{quote!r} (occurrence {nth}) not in {question!r}")
    return [start, start + len(quote)]


def cond(question: str, cid: str, kind: str, quote: str | None = None, *, nth: int = 0, value=MISSING,
         attrs: dict | None = None) -> dict:
    """Partial condition. The span is computed from the quote's position in the question."""
    out: dict = {"id": cid, "kind": kind}
    if quote is not None:
        out["quote"] = quote
        out["span"] = span_of(question, quote, nth)
    if value is not MISSING:
        out["value"] = value
    if attrs is not None:
        out["attrs"] = attrs
    return out


def ex(text: str, year: str | None = None, alias: str | None = None, unit: str | None = None,
       value: str | None = None, end: bool = False) -> dict:
    """A period end as written (shapes.PeriodExpr)."""
    return {"text": text, "year": year, "alias": alias, "sub_unit": unit, "sub_value": value, "end_mark": end}


def pattrs(form: str, start: dict | None = None, end: dict | None = None, *, open_end: bool | None = None,
           relative: bool = False, status: str = "explicit", invalid: bool = False, amount: int | None = None,
           unit: str | None = None) -> dict:
    """Full period attributes (shapes.PeriodAttrs)."""
    return {"form": form, "start_expr": start, "end_expr": end,
            "open_end": (form == "since") if open_end is None else open_end, "amount": amount, "unit": unit,
            "relative": relative, "invalid": invalid, "extract_status": status}


def basis(axis: str, value: str, polarity: str | None = None, origin: str | None = None) -> dict:
    out = {"axis": axis, "value": value}
    if polarity is not None:
        out["polarity"] = polarity
    if origin is not None:
        out["origin"] = origin
    return out


def case_id(case: dict) -> str:
    file, rest = case["key"].split("::", 1)
    stem = file.removeprefix("test_").removesuffix(".py")
    return f"{stem}::{rest}" + (f"#{case['part']}" if case.get("part") else "")


def number_parts(cases: list[dict]) -> list[dict]:
    """Give a part number to cases that share a key, so pytest IDs stay unique."""
    seen: dict[str, int] = {}
    for c in cases:
        n = seen.get(c["key"], 0) + 1
        seen[c["key"]] = n
        if n > 1 or sum(1 for d in cases if d["key"] == c["key"]) > 1:
            c["part"] = n
    return cases


def behaviour_params(cases: list[dict]) -> list:
    out = []
    for c in cases:
        marks = []
        if c["node"] not in IMPLEMENTED:
            marks.append(pytest.mark.xfail(strict=True, raises=NotImplementedError, reason=f"2구간: {c['node']}"))
        out.append(pytest.param(c, id=case_id(c), marks=marks))
    return out


def shape_params(cases: list[dict]) -> list:
    return [pytest.param(c, id=case_id(c)) for c in cases]


# ---------------------------------------------------------------- comparison


def diff(expected, actual, path: str = "$") -> list[str]:
    """Problems where ``actual`` does not contain ``expected``: dict keys are a subset, lists match element-wise."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected a dict, got {actual!r}"]
        out = []
        for k, v in expected.items():
            if k not in actual:
                out.append(f"{path}.{k}: missing")
            else:
                out += diff(v, actual[k], f"{path}.{k}")
        return out
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [f"{path}: expected {len(expected)} items, got {actual!r}"]
        out = []
        for k, (e, a) in enumerate(zip(expected, actual)):
            out += diff(e, a, f"{path}[{k}]")
        return out
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


# ---------------------------------------------------------------- synthetic catalogs


def itm(obj_id: str, item_id: str, name: str, parent: str | None = None, unit: str | None = None,
        obj_name: str | None = None, order: int = 1) -> dict:
    row = {"OBJ_ID": obj_id, "OBJ_ID_SN": str(order), "OBJ_NM": obj_name or obj_id, "ITM_ID": item_id,
           "ITM_NM": name}
    if parent is not None:
        row["UP_ITM_ID"] = parent
    if unit is not None:
        row["UNIT_NM"] = unit
    return row


MONTHLY = (("월", "2000.01", "2026.08"),)


def table(tid: str, title: str, items: list[dict], *, path: str = "가상 > 통계",
          periods=MONTHLY, units=()) -> dict:
    return {"id": tid, "title": title, "path": path, "items": items, "periods": list(periods), "units": list(units)}


def write_catalog(folder: Path, tables: list[dict]) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    search, meta = folder / "search_catalog.json", folder / "boK_meta_raw.jsonl"
    docs = [{"table_id": t["id"], "org_id": "301", "title": t["title"], "path": t["path"]} for t in tables]
    search.write_text(json.dumps({"source_date": "2026-10-01", "documents": docs}, ensure_ascii=False),
                      encoding="utf-8")
    rows = []
    for t in tables:
        prd = [{"PRD_SE": se, "STRT_PRD_DE": a, "END_PRD_DE": b} for se, a, b in t["periods"]]
        rows.append({"tblId": t["id"], "orgId": "301", "name": t["title"],
                     "meta": {"TBL": [{"TBL_NM": t["title"]}], "PRD": prd, "ITM": t["items"],
                              "UNIT": [{"UNIT_NM": u} for u in t["units"]], "CMMT": []}})
    meta.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return search, meta


def build_layer(folder: Path, tables: list[dict]):
    search, meta = write_catalog(folder / "raw", tables)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


class Layers:
    """Builds each named synthetic catalog once per session."""

    def __init__(self, factory, catalogs: dict[str, list[dict]]):
        self.factory, self.catalogs, self.cache = factory, catalogs, {}

    def get(self, name: str | None):
        if name is None:
            return None
        if name not in self.cache:
            self.cache[name] = build_layer(self.factory.mktemp(f"spec_{name}"), self.catalogs[name])
        return self.cache[name]


def catalog_problems(case: dict, catalogs: dict[str, list[dict]]) -> list[str]:
    name = case.get("layer")
    if name is None:
        return []
    if name not in catalogs:
        return [f"layer {name!r} is not a synthetic catalog"]
    ids = {t["id"] for t in catalogs[name]}
    wanted = set(case.get("tables") or []) | {c.get("table") for c in case.get("expect", {}).get("checks", [])}
    wanted |= {c.get("table") for c in case.get("expect", {}).get("absent", []) if c.get("table") != "*"}
    return [f"table {t!r} not in catalog {name!r}" for t in sorted(wanted - {None} - ids)]


# ---------------------------------------------------------------- conditions cases


def condition_shape(case: dict, catalogs: dict | None = None) -> list[str]:
    q, exp = case["question"], case["expect"]
    out = catalog_problems(case, catalogs or {})
    if not set(case["kinds"]) <= set(FINDERS):
        out.append(f"unknown kinds {case['kinds']}")
    conds = exp.get("conditions", [])
    out += shape.check_conditions(conds, q, partial=True)
    out += [f"conditions[{k}]: kind {c.get('kind')} not in {case['kinds']}" for k, c in enumerate(conds)
            if c.get("kind") not in case["kinds"]]
    for k, pattern in enumerate(exp.get("absent", [])):
        out += [f"absent[{k}]: {p}" for p in shape.check_conditions([pattern], q, partial=True)]
    if "conditions" not in exp and "absent" not in exp:
        out.append("no expectation")
    return out


def run_condition_case(case: dict, layers: Layers) -> list:
    text = normalize_text(case["question"])
    found = []
    for kind in case["kinds"]:
        fn = getattr(finders, FINDERS[kind])
        found += fn(text, layers.get(case.get("layer"))) if FINDERS[kind] in WITH_LAYER else fn(text)
    assert shape.check_conditions(found, case["question"]) == []  # full shape of the real output
    return found


def condition_problems(case: dict, found: list) -> list[str]:
    exp = case["expect"]
    out = []
    if "conditions" in exp:
        got = sorted(found, key=lambda c: (c["span"][0], c["id"]))
        out += diff(exp["conditions"], got, "conditions")
    for k, pattern in enumerate(exp.get("absent", [])):
        if any(not diff(pattern, c) for c in found):
            out.append(f"absent[{k}]: {pattern} was found")
    return out


# ---------------------------------------------------------------- analysis cases (fragments, requirements)


def mark_span(question: str, mark) -> list[int]:
    quote, nth = (mark, 0) if isinstance(mark, str) else mark
    return span_of(question, quote, nth)


WHERE = ("common", "ambiguous", "attached")


def analysis_shape(case: dict) -> list[str]:
    q, exp = case["question"], case["expect"]
    out = []
    partial = {"conditions": exp.get("conditions", [])}
    reqs = exp.get("requirements")
    if reqs is not None or "status" in exp or "common_condition_ids" in exp:
        interp = {"requirements": reqs or []}
        for f in ("status", "common_condition_ids"):
            if f in exp:
                interp[f] = exp[f]
        partial["interpretation"] = interp
    out += shape.check_analysis(partial, q, partial=True)
    n = exp.get("requirement_count")
    if reqs is not None and n is not None and len(reqs) != n:
        out.append("requirements: length differs from requirement_count")
    for mark, where in exp.get("attribution", []):
        try:
            mark_span(q, mark)
        except ValueError as e:
            out.append(str(e))
        if not (where in WHERE or (isinstance(where, int) and (n is None or 0 <= where < n))):
            out.append(f"attribution {mark!r}: bad place {where!r}")
    for group_key in ("together", "apart"):
        for group in exp.get(group_key, []):
            for mark in group:
                try:
                    mark_span(q, mark)
                except ValueError as e:
                    out.append(f"{group_key}: {e}")
    for k, prefix in (exp.get("requirement_starts") or {}).items():
        if prefix not in q or (n is not None and not 0 <= int(k) < n):
            out.append(f"requirement_starts[{k}]: {prefix!r}")
    if "all_requirements" in exp:
        out += [f"all_requirements: {p}" for p in shape.check_interpretation(
            {"requirements": [exp["all_requirements"] | {"id": "r"}]}, q, partial=True)]
    if "judgement" in exp:
        out += shape.check_judgement(exp["judgement"], partial=True)
    unknown = set(exp) - {"conditions", "requirements", "requirement_count", "status", "common_condition_ids",
                          "attribution", "own_includes", "together", "apart", "requirement_starts", "all_requirements",
                          "judgement", "core_equals", "core_startswith", "core_contains", "core_excludes", "fragments"}
    out += [f"expect: unknown key {u!r}" for u in sorted(unknown)]
    cond_ids = {c["id"] for c in exp.get("conditions", [])}
    for k, ids in (exp.get("own_includes") or {}).items():
        out += [f"own_includes[{k}]: unknown ID {i!r}" for i in ids if i not in cond_ids]
    for key in ("core_contains", "core_excludes", "core_startswith"):
        for s in ([exp[key]] if isinstance(exp.get(key), str) else exp.get(key, [])):
            if s not in q and key != "core_excludes":
                out.append(f"{key}: {s!r} not in the question")
    if "fragments" in exp:
        out += shape.check_fragments(exp["fragments"], q, partial=True)
    return out


def run_analysis(case: dict, layers: Layers) -> dict:
    analysis = interpret_question(case["question"], layers.get(case.get("layer")))
    assert shape.check_analysis(analysis, case["question"]) == []
    return analysis


def _place(question: str, analysis: dict, mark) -> object:
    a, b = mark_span(question, mark)
    hits = [c for c in analysis["conditions"] if c["span"][0] < b and a < c["span"][1]]
    if len(hits) != 1:
        return f"<{len(hits)} conditions over {mark!r}>"
    cid = hits[0]["id"]
    interp = analysis["interpretation"]
    if cid in interp["common_condition_ids"]:
        return "common"
    reqs = interp["requirements"]
    if reqs and all(cid in r["ambiguous_condition_ids"] for r in reqs):
        return "ambiguous"
    owners = [k for k, r in enumerate(reqs) if cid in r["condition_ids"]]
    return owners[0] if len(owners) == 1 else f"<owners {owners}>"


def analysis_problems(case: dict, analysis: dict) -> list[str]:
    q, exp = case["question"], case["expect"]
    interp = analysis["interpretation"]
    out = []
    by_id = {c["id"]: c for c in analysis["conditions"]}
    for c in exp.get("conditions", []):
        out += diff(c, by_id.get(c["id"], {}), f"conditions.{c['id']}")
    if "requirement_count" in exp and len(interp["requirements"]) != exp["requirement_count"]:
        out.append(f"requirements: {len(interp['requirements'])} != {exp['requirement_count']}")
    if "requirements" in exp:
        out += diff(exp["requirements"], interp["requirements"], "requirements")
    for f in ("status", "common_condition_ids"):
        if f in exp:
            out += diff(exp[f], interp[f], f)
    for mark, where in exp.get("attribution", []):
        got = _place(q, analysis, mark)
        if where == "attached":
            ok = got == "common" or isinstance(got, int)
        else:
            ok = got == where
        if not ok:
            out.append(f"attribution {mark!r}: expected {where!r}, got {got!r}")
    for group in exp.get("together", []):
        places = [_place(q, analysis, m) for m in group]
        if len(set(map(str, places))) != 1 or not isinstance(places[0], int):
            out.append(f"together {group}: placed {places}")
    for group in exp.get("apart", []):
        places = [_place(q, analysis, m) for m in group]
        if len(set(map(str, places))) != len(places):
            out.append(f"apart {group}: placed {places}")
    for k, prefix in (exp.get("requirement_starts") or {}).items():
        reqs = interp["requirements"]
        if int(k) >= len(reqs) or not reqs[int(k)]["quote"].startswith(prefix):
            out.append(f"requirement_starts[{k}]: does not start with {prefix!r}")
    if "all_requirements" in exp:
        # "every requirement is X" needs at least one requirement (the old tests compared the set of statuses,
        # {r["status"] for r in reqs} == {...}, which an empty list does not pass)
        if not interp["requirements"]:
            out.append("all_requirements: no requirements")
        for k, r in enumerate(interp["requirements"]):
            out += diff(exp["all_requirements"], r, f"requirements[{k}]")
    if "judgement" in exp:
        out += diff(exp["judgement"], analysis["judgement"], "judgement")
    for k, ids in (exp.get("own_includes") or {}).items():
        own = interp["requirements"][int(k)]["condition_ids"] if int(k) < len(interp["requirements"]) else []
        out += [f"own_includes[{k}]: {i} missing" for i in ids if i not in own]
    core = " ".join(f["quote"] for f in analysis["fragments"] if f["role"] == "core")
    if "core_equals" in exp and core != exp["core_equals"]:
        out.append(f"core: {core!r} != {exp['core_equals']!r}")
    if "core_startswith" in exp and not core.startswith(exp["core_startswith"]):
        out.append(f"core: {core!r} does not start with {exp['core_startswith']!r}")
    out += [f"core: {s!r} missing" for s in exp.get("core_contains", []) if s not in core]
    out += [f"core: {s!r} present" for s in exp.get("core_excludes", []) if s in core]
    if "fragments" in exp:
        out += diff(exp["fragments"], analysis["fragments"], "fragments")
    return out


# ---------------------------------------------------------------- evidence cases


def _check_key(spec: dict) -> tuple:
    return spec.get("requirement", "r1"), spec.get("table"), spec["name"], spec.get("condition")


def evidence_shape(case: dict, catalogs: dict) -> list[str]:
    exp = case["expect"]
    out = catalog_problems(case, catalogs)
    cond_ids = None
    if "input" in case:
        out += shape.check_analysis(case["input"], case.get("question"), partial=True)
        cond_ids = {c["id"] for c in case["input"].get("conditions", [])}
        for k, prior in enumerate(case.get("prior", [])):
            out += [f"prior[{k}]: {p}" for p in shape.check_check(prior, "prior", cond_ids, partial=True)]
    if "conditions" in exp:
        out += shape.check_conditions(exp["conditions"], case.get("question"), partial=True)
        cond_ids = (cond_ids or set()) | {c["id"] for c in exp["conditions"]}
    for k, spec in enumerate(exp.get("checks", [])):
        path = f"checks[{k}]"
        unknown = set(spec) - {"requirement", "table", "name", "condition", "rows_condition", "rows_contains", "check",
                               "check_not", "stances", "row_text_contains"}
        out += [f"{path}: unknown key {u!r}" for u in sorted(unknown)]
        body = dict(spec.get("check", {}), name=spec["name"])
        out += shape.check_check(body, path, cond_ids, partial=True)
        if "check_not" in spec:
            out += shape.check_check(dict(spec["check_not"], name=spec["name"]), f"{path}.check_not", cond_ids,
                                     partial=True)
        if spec.get("condition") is not None and cond_ids is not None and spec["condition"] not in cond_ids:
            out.append(f"{path}.condition: unknown ID {spec['condition']!r}")
        out += [f"{path}.stances: {s!r}" for s in spec.get("stances", []) if s not in ("support", "against", "unknown")]
        for r, row in enumerate(spec.get("rows_contains", [])):
            out += [f"{path}.rows_contains[{r}]: {p}" for p in shape.check_check(
                {"name": "matched_items", "detail": {"rows": [row]}}, path, cond_ids, partial=True)]
    for k, spec in enumerate(exp.get("absent", [])):
        if spec["name"] not in shapes.DETAIL_BY_NAME:
            out.append(f"absent[{k}]: unknown check name {spec['name']!r}")
    if not exp.get("checks") and not exp.get("absent") and "conditions" not in exp and not case.get("input_unchanged"):
        out.append("no expectation")
    return out


def run_evidence_case(case: dict, layers: Layers) -> tuple[dict, list]:
    """(doc, analysis conditions). Pipeline cases interpret the question first; direct cases call one check."""
    layer = layers.get(case.get("layer"))
    if "input" in case:
        inp = ev.EvidenceInput(copy.deepcopy(case["input"]), layer, case.get("as_of"))
        before = copy.deepcopy(inp.analysis)
        fn = getattr(ev, f"check_{case['check']}")
        checks, rows = fn(inp, case.get("requirement", "r1"), case["tables"][0], copy.deepcopy(case.get("prior", [])))
        if case.get("input_unchanged"):
            assert inp.analysis == before, "the check changed its input"
        doc = {"evidence": [{"requirement_id": case.get("requirement", "r1"), "table_id": case["tables"][0],
                             "checks": checks}], "evidence_rows": rows}
        assert shape.check_evidence(doc) == []
        return doc, case["input"].get("conditions", [])
    analysis = interpret_question(case["question"], layer)
    doc = ev.check_evidence(analysis, case["tables"], layer, case.get("as_of"))
    assert shape.check_evidence(doc) == []
    return doc, analysis["conditions"]


def _checks_of(doc: dict, requirement: str, table: str, name: str, condition: str | None) -> list[dict]:
    out = []
    for entry in doc["evidence"]:
        if entry["requirement_id"] != requirement or (table != "*" and entry["table_id"] != table):
            continue
        for c in entry["checks"]:
            if c["name"] == name and (condition is None or c["detail"].get("condition_id") == condition):
                out.append(c)
    return out


def evidence_problems(case: dict, doc: dict, found_conditions: list) -> list[str]:
    exp = case["expect"]
    out = []
    if "conditions" in exp:
        by_id = {c["id"]: c for c in found_conditions}
        for c in exp["conditions"]:
            out += diff(c, by_id.get(c["id"], {}), f"conditions.{c['id']}")
    rows = doc.get("evidence_rows", {})
    for spec in exp.get("checks", []):
        req, table, name, condition = _check_key(spec)
        hits = _checks_of(doc, req, table, name, condition)
        label = f"{req}x{table}:{name}:{condition}"
        if len(hits) != 1:
            out.append(f"{label}: {len(hits)} checks")
            continue
        got = copy.deepcopy(hits[0])
        if spec.get("rows_condition") is not None:
            got["detail"]["rows"] = [r for r in got["detail"]["rows"] if r["condition_id"] == spec["rows_condition"]]
        out += diff(spec.get("check", {}), got, label)
        for r, row in enumerate(spec.get("rows_contains", [])):
            if not any(not diff(row, a) for a in got["detail"].get("rows", [])):
                out.append(f"{label}: no matched row like rows_contains[{r}]")
        if "check_not" in spec and not diff(spec["check_not"], got):
            out.append(f"{label}: matches {spec['check_not']} but must not")
        stances = [rows.get(e, {}).get("stance") for e in got["evidence_ids"]]
        if "stances" in spec and stances != spec["stances"]:
            out.append(f"{label}: stances {stances} != {spec['stances']}")
        if "row_text_contains" in spec:
            texts = [rows.get(e, {}).get("text") or "" for e in got["evidence_ids"]]
            if not any(spec["row_text_contains"] in t for t in texts):
                out.append(f"{label}: no evidence row text contains {spec['row_text_contains']!r}")
    for spec in exp.get("absent", []):
        req, table, name, condition = _check_key(spec)
        if _checks_of(doc, req, table, name, condition):
            out.append(f"{req}x{table}:{name}:{condition}: present but must be absent")
    return out


# ---------------------------------------------------------------- old segmentation helper for attribution cases


def old_measure_count(query: str, measures: int | None = None) -> int:
    """Number of measures the old attribution test cut the question into ('와/과 ' after a word)."""
    cuts = re.findall(r"(?<=\S)(?:와|과) ", query)
    if measures:
        cuts = cuts[:measures - 1]
    return len(cuts) + 1


stamp_months = q_rules.stamp_months
