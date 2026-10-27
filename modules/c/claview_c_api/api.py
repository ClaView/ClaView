"""Module C HTTP API. Run: uvicorn claview_c_api.api:app --port 8888 (PYTHONPATH: common, modules/c)."""

from __future__ import annotations

from claview_c.c_analysis import VERSION
from claview_common.api import create_app, timed
from claview_common.contracts import CRequest, Meta, Outcome, ViewModel
from claview_common.settings import load

from .service import present

app = create_app("module-c", "0.1.0", info=lambda: {"role": "chart/table view model", "analysis_version": VERSION})


@app.post("/c/present", response_model=ViewModel)
def present_view(body: CRequest) -> ViewModel:
    envelope, ms = timed(lambda: present(body.query, body.data_result))
    return ViewModel(outcome=Outcome(**envelope), meta=Meta(latency_ms=ms, mode=load().mode))
