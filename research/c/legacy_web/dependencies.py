"""Paths and import setup for the C part, wired to the integrated repository.

C no longer carries its own copies of A and B. It imports `claview.a`, `claview.b`
and `claview.pipeline` from the installed repository package, so only one version of
each exists in a process. The C modules keep their flat imports (`import c_renderer`),
which needs the two C folders on `sys.path`; `configure()` adds them.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# .../src/claview/c/abc_integration -> repository root
REPO_ROOT = HERE.parents[3]
# Name kept from the old C code: the folder that holds data/ and eval/.
B_ROOT = REPO_ROOT
C_DIR = HERE.parent  # src/claview/c


def _find_renderer_dir() -> Path:
    """Folder with c_renderer.py: bc_demo/ if present, else searched under src/claview/c."""
    expected = C_DIR / "bc_demo"
    if (expected / "c_renderer.py").is_file():
        return expected
    for found in sorted(C_DIR.rglob("c_renderer.py")):
        if "legacy_old_ab" not in found.parts:
            return found.parent
    return expected


C_ROOT = _find_renderer_dir()
UI_ROOT = HERE.parent / "ui_a_highcharts"
CATALOG = REPO_ROOT / "data" / "catalog" / "search_catalog.json"
METADATA = REPO_ROOT / "data" / "raw" / "boK_meta_raw.jsonl"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configure():
    """Disable external tracing, make the flat C modules importable, return version info."""
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    for folder in (HERE, C_ROOT):
        if not folder.is_dir() or (folder == C_ROOT and not (folder / "c_renderer.py").is_file()):
            raise RuntimeError(
                f"C folder not found: {folder} (expects c_renderer.py in src/claview/c/bc_demo/)"
            )
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
    import claview.pipeline.b_port as b_port

    return {
        "flow": b_port.TABLE_LIST_CONTRACT,
        "metadata_sha256": sha(CATALOG),
        "raw_metadata_sha256": sha(METADATA),
    }
