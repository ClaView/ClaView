"""Smoke tests of the catalog layer on the real raw files (349 tables). No network, no model, no question file.

Tolerances
- Tables: exactly 349 (the retained table list).
- Axes, values, axis names: within 1% of the EDA v3 counts (617 axes incl. 1 unnamed, 23,060 values incl. 1 unnamed,
  29 axis names; runs/eda_v3/README.md). The same snapshot gives the exact numbers; the 1% margin lets a corrected
  re-collection of the snapshot pass without retuning, while a loader that drops or duplicates rows fails.
- Rule-only axis roles (configuration A) against the EDA recount (runs/eda_v3/2_axis_roles/numbers.json, outside git;
  skipped when absent): every number within 2%, except that the compound rule here also takes "A-B" with a digit
  after the hyphen (draft example "정기예금-2%미만"), so compound values may only grow and exactly by the number the
  indicator values shrink.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from claview_a_legacy import paths

pytestmark = pytest.mark.skipif(not (paths.CATALOG_SEARCH.exists() and paths.CATALOG_META_RAW.exists()),
                                reason="catalog raw files absent")

EDA_COUNTS = {"axes": 617, "values": 23060, "axis_names": 29}
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "modules" / "a"


def _close(got: int, want: int, rel: float) -> bool:
    return abs(got - want) <= max(1, round(want * rel))


@pytest.fixture(scope="module")
def layer():
    from claview_a_legacy.catalog.layer import load_layer

    return load_layer()


def test_real_catalog_loads(layer):
    cat = layer.catalog
    assert len(cat.tables) == 349
    axes = list(cat.axes())
    values = [it for ax in axes for it in ax.items]
    assert _close(len(axes), EDA_COUNTS["axes"], 0.01)
    assert _close(len(values), EDA_COUNTS["values"], 0.01)
    assert _close(len({a.name for a in axes if a.named}), EDA_COUNTS["axis_names"], 0.04)
    assert sum(1 for v in values if v.name_eng) >= 0.99 * len(values)
    assert all(len(cat.sources[k]["sha256"]) == 64 for k in ("search_catalog", "meta_raw"))
    assert all(t.item_rows for t in cat.tables.values())


def test_real_roles_every_value_has_a_type_and_draft_status(layer):
    from claview_a_legacy.catalog.roles import VALUE_TYPES

    roles = layer.roles
    n_values = sum(len(ax.items) for ax in layer.catalog.axes()) + len(layer.catalog.tables)
    assert len(roles.values) == n_values
    assert {v.type for v in roles.values.values()} <= set(VALUE_TYPES)
    unknown = sum(1 for v in roles.values.values() if v.type == "unknown")
    assert unknown / n_values < 0.01
    assert {v.status for v in roles.values.values()} <= {"draft", "approved"}
    assert all(p["found"] != 1 for p in roles.link_problems)
    from claview_a_legacy.catalog.roles import load_overrides

    rows = load_overrides()
    assert sorted({r["source"] for r in roles.override_report}) == sorted({r.source for r in rows})


def test_real_rule_only_roles_match_the_eda_recount(layer):
    if not paths.EDA_AXIS_ROLE_NUMBERS.exists():
        pytest.skip("EDA numbers file absent (runs/ is outside git)")
    from claview_a_legacy.catalog.roles import build_axis_roles
    from claview_a_legacy.catalog.stats import role_numbers

    recount = json.loads(paths.EDA_AXIS_ROLE_NUMBERS.read_text(encoding="utf-8"))["recount"]
    mine = role_numbers(build_axis_roles(layer.catalog, use_draft_csv=False, overrides=False))
    for k, want in recount.items():
        if k in ("3층 결합", "3층 지표"):
            continue
        assert _close(mine[k], want, 0.02), (k, mine[k], want)
    moved = mine["3층 결합"] - recount["3층 결합"]
    assert 0 <= moved <= 0.02 * recount["3층 지표 축 값"]
    assert recount["3층 지표"] - mine["3층 지표"] == moved


def test_real_index_and_spans(layer):
    ix = layer.index
    assert ix.stats["catalog_keys"] > 5000 and ix.stats["span_keys"] > 5000
    some_table = next(iter(layer.catalog.tables.values()))
    assert any(c["kind"] == "table" and some_table.table_id in c["table_ids"] for c in ix.lookup(some_table.name))
    text = f"{some_table.name_front} 보여줘"
    spans = ix.find_spans(text)
    assert spans and spans[0]["start"] == 0 and text[spans[0]["start"]:spans[0]["end"]] == spans[0]["quote"]


def test_stats_module_writes_the_report(tmp_path):
    from claview_a_legacy.catalog import stats

    out = tmp_path / "catalog_stats.md"
    s = stats.run(out)
    text = out.read_text(encoding="utf-8")
    for heading in ("## 원자료", "## 카탈로그", "## 축 역할 네 층", "## 값의 형", "## 대상 값 사전", "## CSV 연결",
                    "## draft 덮어쓰기", "## 용어 색인", "## 동의어", "## 범위 밖"):
        assert heading in text
    assert s["catalog"]["tables"] == 349 and out.with_suffix(".json").exists()
    assert str(paths.ROOT) not in text  # paths shown relative to the checkout


CHILD = r'''
import claview_a_legacy._guard as g
g.install()
import json, os, sys, sysconfig
from claview_a_legacy.catalog.layer import load_layer
layer = load_layer()
name = next(iter(layer.catalog.tables.values())).name_front
spans = layer.index.find_spans(name + " 보여줘")
pkg = os.path.normcase(os.path.abspath(sys.argv[1]))
roots = [pkg] + [os.path.normcase(os.path.abspath(p)) for k, p in sysconfig.get_paths().items()
                 if k in ("stdlib", "platstdlib", "purelib", "platlib")]
roots.append(os.path.normcase(os.path.abspath(os.path.join(sys.base_prefix, "DLLs"))))
parent = os.path.dirname(pkg)  # the parent package claview itself (folder entry and docstring-only __init__)
parent_entry = {parent, os.path.join(parent, "__init__.py")}
def inside(f):
    f = os.path.normcase(os.path.abspath(f))
    return f in parent_entry or any(f == r or f.startswith(r.rstrip(os.sep) + os.sep) for r in roots)
bad = sorted(n for n, m in list(sys.modules.items())
             if any(isinstance(f, str) and not inside(f)
                    for f in [getattr(m, "__file__", None)] + list(getattr(m, "__path__", None) or [])))
print(json.dumps({"tables": len(layer.catalog.tables), "spans": len(spans), "bad": bad, "problems": g.problems()}))
'''


def test_child_loads_the_catalog_layer_inside_the_boundary(tmp_path):
    env = {k: v for k, v in os.environ.items()
           if k != "PYTHONPATH" and not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET"))}
    env.update({"PYTHONPATH": str(SRC), "PYTHONHASHSEED": "0"})
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", CHILD, str(SRC / "claview_a_legacy")], cwd=tmp_path,
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report == {"tables": 349, "spans": report["spans"], "bad": [], "problems": []} and report["spans"] >= 1
