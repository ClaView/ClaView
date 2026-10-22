"""Axis names of table metadata (container suffixes removed). No domain dictionary."""
from __future__ import annotations

import re

from .measure import norm


def axis_name(text):
    value = norm(text)
    # Metadata container suffixes, not domain aliases.
    return re.sub(r"(?:(?:코드|분류|구분)|별)+$", "", value)
