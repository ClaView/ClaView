"""Where the table-level evaluator reads its files.

The evaluation questions and everything built from them (standards, decisions, case files, archived runs) stay
outside the repository, as one fixed bundle. Point ``CLAVIEW_A_EVAL_DIR`` at the unpacked bundle's ``tl_eval``
folder; without it the evaluator looks in ``runs/a_eval_files`` (git-ignored). A file named in a standard or a
manifest by an outside path is looked up in that folder by its file name.
"""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[4]
A_DIR = ROOT / "modules" / "a"
PACKAGE_DIR = A_DIR / "claview_a"
EVALUATOR_DIR = Path(__file__).resolve().parent
EVAL_DIR_ENV = "CLAVIEW_A_EVAL_DIR"
EVAL_DIR = Path(os.environ.get(EVAL_DIR_ENV) or ROOT / "runs" / "a_eval_files")


def external(name: str | Path) -> Path:
    """The evaluation folder's file with the same file name (``name`` may be an outside or relative path)."""
    return EVAL_DIR / PureWindowsPath(str(name)).name


def present(*names: str) -> bool:
    """True when every named file is in the evaluation folder."""
    return all(external(n).is_file() for n in names)
