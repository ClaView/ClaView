"""Plan 56 order 6-2b (bundle 5): the AView keeps the values a basis excludes (they were dropped before)."""

from __future__ import annotations

from claview_a_api import view as V


def test_the_excluded_values_go_with_the_basis():
    d = {"basis": {"seasonal": {"value": ["원계열"], "source": "model_choice", "excluded": ["계절조정"],
                                "excluded_source": "splitter"},
                   "stock": {"value": ["말잔"], "source": "splitter"}}}
    rows = {b["axis"]: b for b in V._basis(d)}
    assert rows["seasonal"]["excluded"] == ["계절조정"] and rows["seasonal"]["value"] == ["원계열"]
    assert rows["stock"]["excluded"] == []
