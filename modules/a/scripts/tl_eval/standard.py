"""The new path's evaluation standard (plan 37, step 4; user 2026-10-28 03:55 UTC).

Built from the confirmed answer files of the current path (``eval/cases/check40_v1.json``, ``golden16_v1.json``,
read only, never rewritten) into ``eval/tl_eval/standard_v1.json``. Per question it keeps apart:

- ``groups``: the answer table groups as the source files give them (one table of each group is needed; alternatives
  inside a group are the source's own). The source's statistic count is not given: ``statistics.status`` is
  ``unconfirmed`` and the scorer maps model statistics to groups by table only (an unclear mapping is ``review``);
- ``representative``: the tables a model may name as the representative of a group. Confirmed: the group's tables.
  ``proposed_equivalents``: other tables with the very same title (another edition, by string equality only), listed
  for the user to confirm; until then a representative among them is scored ``review``, never right or wrong;
- ``ask``: the source's expectation (check40 ``ask`` / ``ask_axis`` / ``options``; golden16 ``ambiguity``);
- ``new_policy``: where the new path's ask policy (user 2026-10-27 19:51 UTC: an approved axis that splits sibling
  tables is asked, not assumed) may expect another ask than the source, computed by a simple rule written here
  (same bracket-free title, approved axis values differ in the brackets, the question names none of them) and marked
  ``proposed``: the ask part of such a question is scored ``review`` until the user confirms it;
- ``final``: the period, frequency and basis the final tables must meet (check40 conditions; golden16 gives none:
  ``unconfirmed``) and the final table groups;
- ``out_of_scope``: what this path does not produce (B's row choices, currency inside a table, calculations, values).

Nothing is decided here that the sources leave open, and nothing is fitted to a run.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "modules" / "a"))
sys.path.insert(0, str(ROOT / "modules" / "a" / "scripts"))

from tl_eval import files as F  # noqa: E402

STANDARD_VERSION = "tl-standard-2026-10-28.1"
OUT = F.external("standard_v1.json")
SOURCES = {"check40": F.external("check40_v1.json"),
           "golden16": F.external("golden16_v1.json")}
ASK_AXES = F.PACKAGE_DIR / "data" / "ask_axes.json"
_BRACKETS = re.compile(r"\((.*)\)")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _name(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\(.*\)", "", title)).strip()


def _brackets(title: str) -> str:
    m = _BRACKETS.search(title)
    return m.group(1) if m else ""


def build(views: dict, as_of: str) -> dict:
    from claview_a.search.basis import values as basis_values
    from claview_a.periods import core as claview_period

    c40 = json.loads(SOURCES["check40"].read_text(encoding="utf-8"))
    g16 = json.loads(SOURCES["golden16"].read_text(encoding="utf-8"))
    axes = json.loads(ASK_AXES.read_text(encoding="utf-8"))
    by_title: dict[str, list[str]] = {}
    by_name: dict[str, list[str]] = {}
    for t, v in views.items():
        by_title.setdefault(v.title, []).append(t)
        by_name.setdefault(_name(v.title), []).append(t)

    def equivalents(groups):
        out = {}
        for g in groups:
            for t in g:
                same = [x for x in by_title.get(views[t].title, []) if x != t and not any(x in gg for gg in groups)]
                if same:
                    out[t] = sorted(same)
        return out

    def open_axes(question, groups):
        found = {}
        for g in groups:
            for t in g:
                sibs = by_name.get(_name(views[t].title), [])
                for axis in axes["ask_axes"]:
                    vals = {tuple(sorted(basis_values(_brackets(views[s].title), axis))) for s in sibs}
                    vals.discard(())
                    if len(vals) >= 2 and not basis_values(question, axis):
                        found.setdefault(axis, sorted("/".join(v) for v in vals))
        return found

    cases = []
    for c in g16["cases"]:
        if c["case_id"] not in c40["golden_cases"]:
            continue
        amb = (g16.get("ambiguity") or {}).get(c["case_id"])
        if amb:
            tables = amb["reasons"][0]["tables"]
            axis = next((a for a in axes["ask_axes"]
                         if len({tuple(sorted(basis_values(_brackets(views[t].title), a))) for t in tables})
                         == len(tables)), None)
            ask = {"expected": "required", "axis": axis, "axis_source_label": amb["reasons"][0]["axis"],
                   "tables": tables, "source": "golden16 ambiguity"}
        else:
            ask = {"expected": "not_expected", "source": "golden16 (no ambiguity record)"}
        cases.append(_case(c["case_id"], "golden16", c["question"], c["groups"], ask, None, views, equivalents,
                           open_axes, extra={"equivalence_unconfirmed": (g16.get("equivalence_unconfirmed") or {})
                                             .get(c["case_id"])}))
    for c in c40["cases"]:
        groups = list(c.get("groups") or [])
        ask = {"expected": c["ask"], "axis": c.get("ask_axis") or c.get("source_ask_axis"),
               "options": c.get("options"), "acceptable_under_policy": c.get("acceptable_under_policy"),
               "policy_pass": c.get("policy_pass"), "source": "check40_v1"}
        cases.append(_case(c["case_id"], "new24", c["question"], groups, ask, c.get("conditions"), views,
                           equivalents, open_axes, extra={"exclusion_check": c.get("exclusion_check"),
                                                          "calc_check": c.get("calc_check"),
                                                          "handoff_only": c.get("handoff_only"),
                                                          "open_points": c.get("open_points")}))
    policy = claview_period.default_policy()
    return {"version": STANDARD_VERSION, "as_of": as_of,
            "sources": {k: {"path": str(p), "sha256": sha(p)} for k, p in SOURCES.items()},
            "ask_axes": {"path": str(ASK_AXES), "sha256": sha(ASK_AXES), "version": axes["version"]},
            "period_policy": {"version": policy.version, "sha256": policy.sha256,
                              "default_recent_years": dict(policy.default_recent_years),
                              "frequency_order": list(policy.frequency_order)},
            "rules": {
                "representative": "right when it is a table of the group; review when it is only a proposed "
                                  "equivalent; wrong otherwise. Another edition or frequency is never wrong by "
                                  "itself, only when it is outside the confirmed range.",
                "new_policy": "proposed: the ask part is review until the user confirms the new expectation",
                "statistics": "unconfirmed count: model statistics are mapped to groups by table; unclear -> review",
                "final": "final tables against the groups, assigned months against the period, frequency and "
                         "bracket basis against the conditions; golden16 conditions are unconfirmed (not scored)",
                "out_of_scope": "B's rows (exclusion_check target rows), currency inside a table (handoff_only), "
                                "calculations (calc_check), values and units",
            },
            "cases": cases}


def _case(cid, part, question, groups, ask, conditions, views, equivalents, open_axes, extra) -> dict:
    allowed_groups = list(groups)
    if ask.get("options"):
        allowed_groups = allowed_groups or [[t] for t in ask["options"].values()]
    if ask.get("acceptable_under_policy"):
        allowed_groups = allowed_groups or [list(ask["acceptable_under_policy"])]
    axes_open = open_axes(question, allowed_groups or [[t] for t in ask.get("tables") or []])
    source_axis = ask.get("axis")
    differs = {a: v for a, v in axes_open.items()
               if not (ask["expected"] == "required" and a == source_axis)}
    if ask["expected"] == "policy_no_ask" and source_axis in axes_open:
        differs[source_axis] = axes_open[source_axis]
    final = {"status": "unconfirmed", "period": None, "frequency": None, "basis": []}
    if conditions is not None:
        final = {"status": "confirmed",
                 "period": next(({"from": x["from"], "to": x["to"]} for x in conditions if x["kind"] == "period"),
                                None),
                 "frequency": next((x["value"] for x in conditions if x["kind"] == "frequency"), None),
                 "basis": [{"axis": x["axis"], "value": x["value"]} for x in conditions if x["kind"] == "basis"]}
    return {"case_id": cid, "part": part, "question": question,
            "statistics": {"count": None, "status": "unconfirmed"},
            "groups": groups,
            "representative": {"confirmed_groups": allowed_groups, "confirmed_equivalents": {},
                               "proposed_equivalents": equivalents(allowed_groups), "status": "proposed"},
            "ask": ask,
            "new_policy": {"open_axes": axes_open, "differs": differs,
                           "status": "proposed" if differs else "same_as_source",
                           "note": "rule: approved-axis values differ among same-name tables and the question names "
                                   "none; whether the statistic is in every sibling is not checked here"},
            "final": {**final, "groups": groups},
            "out_of_scope": {k: v for k, v in extra.items() if v},
            }


STANDARD_V2_VERSION = "tl-standard-2026-10-28.2"
OUT_V2 = F.external("standard_v2.json")
DECISIONS = F.external("decisions_v2.json")


def apply_decisions(base: dict, decisions: dict) -> dict:
    """standard_v2: the v1 build with the user's decisions (``decisions_v2.json``, user 2026-10-28 04:36 UTC) laid
    on it, each with its status and evidence. Nothing else changes: a case without a decision keeps v1's content."""
    out = json.loads(json.dumps(base))
    out["version"] = STANDARD_V2_VERSION
    out["decisions"] = {"path": str(DECISIONS), "sha256": sha(DECISIONS), "version": decisions["version"],
                        "decided": decisions["decided"]}
    out["rules"] = {**out["rules"], **{f"decision_{k}": v for k, v in decisions["rules"].items()}}
    for case in out["cases"]:
        dec = decisions["cases"].get(case["case_id"])
        if not dec:
            continue
        case["decisions"] = dec
        if "equivalents" in dec:
            case["representative"]["equivalents_status"] = dec["equivalents"]["status"]
            case["representative"]["equivalents_note"] = dec["equivalents"]["why"]
        if "ask" in dec:
            a = dec["ask"]
            if a["status"] == "confirmed" and a["expected"] == "required":
                case["ask"] = {"expected": "required", "axis": a["axis"], "options": a["options"],
                               "source": "decisions_v2", "evidence": a["evidence"],
                               "source_expectation": a.get("source_expectation")}
                case["representative"]["confirmed_groups"] = [[t] for t in a["options"].values()]
                case["final"]["groups"] = []
                case["new_policy"]["status"] = "confirmed"
            elif a["status"] == "confirmed":
                case["ask"]["expected"] = a["expected"]
                case["ask"]["evidence"] = a["evidence"]
                case["new_policy"]["status"] = "confirmed"
            else:
                case["new_policy"]["status"] = "hold"
                case["new_policy"]["hold_why"] = a["why"]
        if "final" in dec:
            case["final"] = {**{k: v for k, v in dec["final"].items()}, "groups": case["final"]["groups"]}
    return out


OUT_V3 = F.external("standard_v3.json")
DECISIONS_V3 = F.external("decisions_v3.json")
STANDARD_V3_VERSION = "tl-standard-2026-10-28.3"


def apply_requirements(v2: dict, decisions: dict) -> dict:
    """standard_v3: standard_v2 with the requirement targets the user confirmed (``decisions_v3.json``, user
    2026-10-28 08:13 UTC: CV-AB-17, CV-AB-21). Nothing else changes; v2 stays as it is."""
    out = json.loads(json.dumps(v2))
    out["version"] = STANDARD_V3_VERSION
    out["base"] = {"path": str(OUT_V2), "sha256": sha(OUT_V2), "version": v2["version"]}
    out["decisions_v3"] = {"path": str(DECISIONS_V3), "sha256": sha(DECISIONS_V3), "version": decisions["version"],
                           "decided": decisions["decided"]}
    out["rules"] = {**out["rules"], **{f"decision_{k}": v for k, v in decisions["rules"].items()}}
    for case in out["cases"]:
        dec = decisions["cases"].get(case["case_id"])
        if dec and "requirements" in dec:
            case["requirements"] = dec["requirements"]
    return out


def main_v3() -> dict:
    """Builds standard_v3 from the standard_v2 file as it is (v1 and v2 are not rebuilt)."""
    v3 = apply_requirements(json.loads(OUT_V2.read_text(encoding="utf-8")),
                            json.loads(DECISIONS_V3.read_text(encoding="utf-8")))
    OUT_V3.write_text(json.dumps(v3, ensure_ascii=False, indent=1), encoding="utf-8")
    return v3


def main(as_of: str = "2026-10-22") -> dict:
    from claview_a.catalog.layer import load_layer
    from claview_a.search.tables import selection_catalog

    data = build(selection_catalog(load_layer()).views, as_of)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    v2 = apply_decisions(data, json.loads(DECISIONS.read_text(encoding="utf-8")))
    OUT_V2.write_text(json.dumps(v2, ensure_ascii=False, indent=1), encoding="utf-8")
    return v2


if __name__ == "__main__":
    d = main()
    print(d["version"], len(d["cases"]), "cases; ask:",
          {c["case_id"]: c["new_policy"]["status"] for c in d["cases"] if c["new_policy"]["status"] != "same_as_source"},
          "; final confirmed:", sum(1 for c in d["cases"] if c["final"]["status"] == "confirmed"))
