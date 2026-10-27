"""Module C's service boundary: data_result -> chart (views, series, notices). Pure: no fetch, no model.

The pipeline and /c/present both call these functions, so C's API measures the code path the
service runs. The helper names below are the only C names the pipeline uses.
"""

from __future__ import annotations

import copy

from claview_c.c_analysis import decorate_cards, renderer
from claview_c.c_renderer import period_label
from claview_c.composition import TOTAL_FETCH_ERROR, total_selection

__all__ = ["TOTAL_FETCH_ERROR", "decorate_cards", "period_label", "present", "total_selection"]


def present(query: str, data_result: dict) -> dict:
    """C stage envelope. data_result is the fetch result (series, relations, period notices)."""
    out = renderer(query, copy.deepcopy(data_result))
    return {"stage": "C", **out}
