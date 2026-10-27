"""Data locations for claview_c (from the shared settings)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from claview_common.settings import PATHS

ROOT = PATHS.root
METADATA = PATHS.meta_raw_file


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
