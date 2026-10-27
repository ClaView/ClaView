"""Actual calls of the shared period package (claview_a.period_core) with a synthetic table (no catalog IDs),
printed as JSON. Moved from scripts/b_stage3/io_examples.py when the package moved into A (A is its only user)."""

import inspect
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
from claview_a import period_core as cp  # noqa: E402

print("import:", cp.__name__, cp.__file__.replace("\\", "/").split("src/")[-1])
print("signature: resolve_period_for_table" + str(inspect.signature(cp.resolve_period_for_table)))
pol = cp.default_policy()
print("versions:", cp.__version__, cp.CONTRACT_VERSION, pol.version, pol.sha256)
print("exports:", sorted(n for n in cp.__all__))

TABLE = {
    "periods": {
        "Y": {"start": "2000", "end": "2025"},
        "Q": {"start": "200001", "end": "202602"},
        "M": {"start": "200001", "end": "202608"},
    }
}


def expr(year=None, alias=None, sub_unit=None, sub_value=None):
    return {
        "text": "t",
        "year": year,
        "alias": alias,
        "sub_unit": sub_unit,
        "sub_value": sub_value,
        "end_mark": False,
    }


def period(form, start=None, end=None, amount=None, unit=None, relative=False, cid="P1"):
    return {
        "id": cid,
        "kind": "period",
        "quote": "q",
        "value": None,
        "attrs": {
            "form": form,
            "start_expr": start,
            "end_expr": end,
            "open_end": form == "since",
            "amount": amount,
            "unit": unit,
            "relative": relative,
            "invalid": False,
            "extract_status": "explicit",
        },
    }


def show(title, *args, **kw):
    out = cp.resolve_period_for_table(*args, **kw)
    keep = {
        k: out[k]
        for k in (
            "status",
            "issue",
            "frequency",
            "applied",
            "periods",
            "coverage",
            "anchor",
            "assumptions",
            "notices",
            "condition_fields",
        )
        if k in out
    }
    keep["slots.frequency"] = out["slots"]["frequency"]
    print(f"\n## {title}\n" + json.dumps(keep, ensure_ascii=False, indent=1))
    assert cp.check_result(out) == []


freq_m = {"id": "F1", "kind": "frequency", "quote": "q", "value": "M", "attrs": {"value": "M"}}
show(
    "1 partial overlap: M 2025-01..2026-12",
    [freq_m, period("range", expr("2025"), expr("2026"))],
    TABLE,
    "2026-10-20",
)
show("2 no overlap", [freq_m, period("range", expr("1980"), expr("1985"))], TABLE, "2026-10-20")
show(
    "3 recent 3 months, inferred [] (empty hint)",
    [period("recent", amount=3, unit="month", relative=True)],
    TABLE,
    "2026-10-20",
    None,
    [],
)
show(
    "4 bundle_common anchor 2026-06, recent 1 year",
    [freq_m, period("recent", amount=1, unit="year", relative=True)],
    TABLE,
    "2026-10-20",
    context={
        "bundle_id": "b1",
        "anchor_source": "bundle_common",
        "anchor": "2026-06",
        "simultaneous": True,
    },
)
show(
    "5 edition share 2010-01..2015-12 of 2005..2024",
    [freq_m, period("range", expr("2005"), expr("2024"))],
    TABLE,
    "2026-10-20",
    assignment=[{"start": "2010-01", "end": "2015-12"}],
)
show(
    "6 period_diagnosis unsupported",
    [freq_m, period("point", expr("2024"))],
    TABLE,
    "2026-10-20",
    condition_fields={
        "P1": {"source": "question", "confirmed": False, "period_diagnosis": "unsupported"}
    },
)
bad = period("point", expr("2024"))
bad["attrs"]["form"] = "sometime"
show("7 malformed form", [freq_m, bad], TABLE, "2026-10-20")
show(
    "8 one assigned segment 2024-04..2024-09 of 2024",
    [freq_m, period("range", expr("2024"), expr("2024"))],
    TABLE,
    "2026-10-20",
    assignment=[{"start": "2024-04", "end": "2024-09"}],
)
show(
    "9 two assigned segments: rejected before any query",
    [freq_m, period("range", expr("2024"), expr("2024"))],
    TABLE,
    "2026-10-20",
    assignment=[{"start": "2024-01", "end": "2024-03"}, {"start": "2024-10", "end": "2024-12"}],
)
invalid = period("point", expr("2024"))
invalid["attrs"]["invalid"] = True
show("10 attrs.invalid: malformed", [freq_m, invalid], TABLE, "2026-10-20")
old = period("recent", amount=3, unit="month", relative=True)
old["attrs"]["extract_status"] = "unresolved"
show("11 old extract_status unresolved, recent 3 months: read", [old], TABLE, "2026-10-20")
