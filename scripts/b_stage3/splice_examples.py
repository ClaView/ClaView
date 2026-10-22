"""Plan 06 input/output examples: the adapter on a current-A fixture with a plan-19 structure
(provisional place) and plan-17 bundles. Offline; prints JSON.

Usage: splice_examples.py <repo root>
"""

import copy
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from claview.b.adapter_v3 import to_b_request  # noqa: E402
from claview.b.contract import check_request  # noqa: E402

fixtures = root / "tests" / "b_v2" / "fixtures" / "v3cur"


def load(name):
    return json.loads((fixtures / f"{name}.json").read_text(encoding="utf-8"))


def show(title, value):
    print(f"\n## {title}\n" + json.dumps(value, ensure_ascii=False, indent=1))


# 1. a plan-19 structure (contract document 5, normal example): the requirement's slice, the old
#    words not re-applied. Candidate rows the document writes as "…" are filled in the same shape.
def ref(place, segment_ids, span, quote, name, candidates):
    return {
        "place": place,
        "segment_ids": segment_ids,
        "span": span,
        "quote": quote,
        "name": name,
        "candidates": candidates,
    }


busan = {
    "table_id": "T_A",
    "obj_id": "REG",
    "value_id": "ra2",
    "value_name": "가상부산",
    "role": "target",
    "path": "exact",
    "type": "item_row",
}
seoul = {**busan, "value_id": "ra1", "value_name": "가상서울"}
marker = ref("polarity_marker", ["s3"], [5, 7], "말고", None, [])
negative = ref("polarity_negative", ["s1"], [0, 4], "가상서울", "가상서울", [seoul])
state = load("v3cur_fx5")
analysis = state["analysis"]
[req] = analysis["interpretation"]["requirements"]
rid = req["id"]
analysis["judgement"]["negated"] = ["가계"]
decomposition = analysis["decomposition"]
decomposition["applied_source"] = "model"  # a structure is an adopted model reading's
decomposition["structure"] = {
    "version": "structure-2026-10-21.1",
    "requirements": {
        rid: {
            "targets": [ref("target", ["s5"], [8, 12], "가상부산", "가상부산", [busan])],
            "breakdowns": [],
            "basis": [],
            "context": None,
        }
    },
    "polarity": [
        {
            "link_id": "p1",
            "kind": "replace",
            "markers": [marker],
            "negative": [negative],
            "positive": [
                ref("polarity_positive", ["s5"], [8, 12], "가상부산", "가상부산", [busan])
            ],
            "requirement_ids": [],
            "scope_status": "clear",
        }
    ],
    "confirmed_effects": [],  # held empty in structure-2026-10-21.1 (final document)
}
request = to_b_request(state)
[scope] = request["scopes"]
show("1 structure slice (hints.structure)", scope["hints"]["structure"])
show(
    "1 old words and value-row negation under a structure",
    {
        "breakdown": scope["hints"]["breakdown"],
        "negated": scope["hints"]["negated"],
        "value_rows.negated": [r["negated"] for r in scope["hints"]["value_rows"]],
        "check_request": check_request(request),
        "contract_version": request["contract_version"],
    },
)

# 2. plan-17 bundle read order
base = load("v3cur_fx4")
[sel] = base["selections"]
table_id = sel["tables"][0]["table_id"]
b = {
    "bundle_id": "b1",
    "context": {"bundle_id": "b1", "anchor_source": "table_latest", "anchor": None},
    "assignments": {table_id: [{"start": "2020-01", "end": "2024-12"}]},
}
for title, signals, public in (
    ("2a plan17 ready", {"plan17": {"b_handoff_ready": True, "bundle": b}}, None),
    ("2b plan17 not ready", {"plan17": {"b_handoff_ready": False, "bundle": b}}, None),
    ("2c public over a different plan17", {"plan17": {"b_handoff_ready": True, "bundle": b}},
     {**b, "bundle_id": "b0", "context": {**b["context"], "bundle_id": "b0"}}),
):  # fmt: skip
    state = copy.deepcopy(base)
    [s] = state["selections"]
    s["signals"].update(signals)
    if public:
        s["bundle"] = public
    [scope] = to_b_request(state)["scopes"]
    show(
        title,
        {"bundle": scope.get("bundle"), "adapter_notices": scope.get("adapter_notices", [])},
    )

# 3. a bundle that does not fit its selection
state = copy.deepcopy(base)
[s] = state["selections"]
s["bundle"] = {**b, "context": {**b["context"], "bundle_id": "b2"}}
request = to_b_request(state)
show("3 bundle_id differs from its context", request["input_errors"])

# 4. version gate and the three kinds of null (user decision 10/21 05:13)
for title, record in (
    (
        "4a unsupported version: a contract error",
        {"structure": {"version": "structure-2099-01-01.1"}, "applied_source": "model"},
    ),
    (
        "4b rules null with its reason: the old fields, the reason kept",
        {
            "structure": None,
            "structure_reason": "rules_structure_unrecorded",
            "applied_source": "rules",
        },
    ),
    (
        "4c null on an adopted model reading: a contract error",
        {
            "structure": None,
            "structure_reason": "rules_structure_unrecorded",
            "applied_source": "model",
        },
    ),
    ("4d null without its reason: a contract error", {"structure": None}),
    ("4e no structure field (an older output): the old fields", {}),
):
    state = load("v3cur_fx5")
    state["analysis"]["judgement"]["negated"] = ["가계"]
    state["analysis"]["decomposition"].update(record)
    request = to_b_request(state)
    [scope] = request["scopes"]
    show(
        title,
        {
            "input_errors": request.get("input_errors", []),
            "structure": scope["hints"].get("structure"),
            "structure_reason": scope["hints"].get("structure_reason"),
            "negated": scope["hints"]["negated"],
        },
    )
