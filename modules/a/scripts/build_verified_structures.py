"""Build modules/a/claview_a/data/verified_structures.json: the structure signatures the specification cases verified
(certainty check 8, decision 10/18; no model, no call).

Sources: the cases of modules/a/tests/spec/test_spec_judgement.py expected certain (``certain: True``) and the cases of
modules/a/tests/spec/test_spec_requirements.py whose judgement is expected certain. Each case runs 2.1-2.7 on its synthetic
catalog with check 8 off; a case expected certain that is not certain without check 8 stops the build (the
specification would be broken). Only signatures are written (general features, ``interpret.structure``), with the
number of cases behind each; no case ID and no question text.

Signatures are taken as they are, never widened: a structure with two or more item clauses, three or more list items,
a table-name context together with a relation or a list, or conditions in another clause is in the list only when a
case has exactly that signature (the ``marks`` field says which of these a signature has).

Usage: .venv/Scripts/python -B -X utf8 modules/a/scripts/build_verified_structures.py [--check]
--check compares the built list with the file and exits 1 when they differ (nothing is written).
"""

from __future__ import annotations

import collections
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules" / "a"), str(ROOT / "modules" / "a" / "tests"), str(ROOT / "modules" / "a" / "tests" / "spec")]

from claview_a import _guard  # noqa: E402

_guard.install_network_block()

from claview_a.interpret import structure  # noqa: E402

os.environ[structure.POLICY_ENV] = "0"  # signatures and certainty without check 8

import spec_catalogs  # noqa: E402
import test_spec_judgement as tj  # noqa: E402
import test_spec_requirements as tr  # noqa: E402
from spec_support import Layers  # noqa: E402

from claview_a.interpret.pipeline import interpret_question  # noqa: E402

SOURCES = ("modules/a/tests/spec/test_spec_judgement.py", "modules/a/tests/spec/test_spec_requirements.py")


class _Factory:
    def __init__(self, base: Path):
        self.base, self.n = base, 0

    def mktemp(self, name: str) -> Path:
        self.n += 1
        p = self.base / f"{name}_{self.n}"
        p.mkdir(parents=True)
        return p


def certain_cases():
    """(case, layers) of every case expected certain."""
    for c in tj.CASES:
        if c["expect"].get("certain") is True:
            yield c, "judgement"
    for c in tr.CASES:
        if (c["expect"].get("judgement") or {}).get("certain") is True:
            yield c, "requirements"


def marks(sig: dict) -> list[str]:
    out = []
    if sig["clauses"] >= 2:
        out.append("two_or_more_item_clauses")
    if any(n >= 3 for clause in sig["items"] for n in clause):
        out.append("three_or_more_items")
    if sig["context"] and (sig["relation_operands"] or any(n >= 2 for clause in sig["items"] for n in clause)):
        out.append("context_with_relation_or_list")
    if any(p.split("@")[1].rstrip("+") == "other" for p in sig["conditions"]):
        out.append("other_clause_conditions")
    return out


def build() -> dict:
    found: dict[str, dict] = {}
    counts: collections.Counter = collections.Counter()
    broken = []
    with tempfile.TemporaryDirectory() as tmp:
        layers = {"judgement": tj.SpecLayers(_Factory(Path(tmp) / "j"), tj.CATALOGS),
                  "requirements": Layers(_Factory(Path(tmp) / "r"), spec_catalogs.CATALOGS)}
        for case, source in certain_cases():
            analysis = interpret_question(case["question"], layers[source].get(case.get("layer")))
            j = analysis["judgement"]
            if not j["certain"]:
                broken.append(f"{source}: a case expected certain is not ({j['failed_conditions']})")
                continue
            key = j["structure"]
            found[key] = json.loads(key)
            counts[key] += 1
    if broken:
        raise SystemExit("\n".join(broken))
    structures = [{"signature": found[k], "cases": counts[k], "marks": marks(found[k])} for k in sorted(found)]
    return {
        "_about": ["Structure signatures verified by the specification cases expected certain (certainty check 8,",
                   "decision 10/18). Built by modules/a/scripts/build_verified_structures.py; do not edit by hand.",
                   "Signatures only (interpret/structure.py): no case ID, no question, no catalog name.",
                   "cases: number of cases with that signature; marks: the conservative features it has."],
        "sources": list(SOURCES), "policy_off_while_built": True, "cases": sum(counts.values()),
        "structures": structures,
    }


def main(args: list[str]) -> int:
    doc = build()
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if "--check" in args:
        same = structure.VERIFIED_FILE.exists() and structure.VERIFIED_FILE.read_text(encoding="utf-8") == text
        print("verified structures up to date" if same else "verified structures differ from the cases")
        return 0 if same else 1
    structure.VERIFIED_FILE.write_text(text, encoding="utf-8")
    print(f"{len(doc['structures'])} structures from {doc['cases']} cases -> "
          f"{structure.VERIFIED_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
