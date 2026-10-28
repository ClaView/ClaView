"""Module B's service boundary: what the API calls. Keeps HTTP out of B's graph code (claview_b).

    resolve(query, tables, as_of) -> B stage envelope (b_result in "result" when ok)
    fetch(b_result)               -> fetch stage envelope (data_result in "result")
    align(data_result, ...)       -> data_result with "alignment" (units, frequency, period, axes)
    combine_results([...])        -> one data_result from several requirements (one chart)
    merge_frequency_choices([...])-> one frequency question for several results aligned apart

Clients follow CLAVIEW_MODE: "live" uses HCX and KOSIS (keys from the environment);
any other mode uses the synthetic model and fetch client (wiring only, not accuracy).
"""

from __future__ import annotations

import copy
import functools
import logging
from dataclasses import dataclass, field
from typing import Any

from claview_b.align import AlignmentError, align, combine_results, merge_frequency_choices
from claview_b.contracts import Config, StageFailure, Trace, outcome
from claview_b.fetch import KosisClient, build_api_request, fetch_data
from claview_b.graphs import build_b_graph
from claview_b.metadata import MetadataStore
from claview_b.services import Services
from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
from claview_common import settings as settings_module

CATALOG = settings_module.PATHS.catalog_file
METADATA = settings_module.PATHS.meta_raw_file

# Alignment of fetched results (pure functions in claview_b.align): the pipeline reaches them here.
__all__ = [
    "AlignmentError",
    "BService",
    "align",
    "combine_results",
    "live_clients",
    "merge_frequency_choices",
    "store",
    "synthetic_clients",
]


class _Counting:
    """Wraps a model or fetch client and counts calls (for the response meta)."""

    def __init__(self, inner, method: str):
        self.inner, self.method, self.calls = inner, method, 0

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if name != self.method:
            return attr

        def counted(*args, **kwargs):
            self.calls += 1
            return attr(*args, **kwargs)

        return counted


@functools.cache
def store() -> MetadataStore:
    return MetadataStore(CATALOG, METADATA)


def live_clients(*, hcx_key=None, openai_key=None, kosis_key=None, min_interval: float = 35, retries=None):
    """(selection model, KOSIS client) from keys; None for a missing key (never invented)."""
    from claview_b.models import HcxModel, OpenAIModel

    # KosisClient puts the key in the query string; httpx logs full URLs at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    extra = {} if retries is None else {"retries": retries}
    if openai_key and not hcx_key:
        model = OpenAIModel(openai_key)
    else:
        model = HcxModel(hcx_key, min_interval=min_interval, **extra) if hcx_key else None
    return model, (KosisClient(kosis_key) if kosis_key else None)


def synthetic_clients():
    """(synthetic model, synthetic fetch client): wiring checks only, not accuracy."""
    return SyntheticSelectionModel(store()), SyntheticFetchClient()


@dataclass
class BService:
    model: Any
    client: Any
    mode: str
    counters: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> BService:
        cfg = settings_module.load()
        if cfg.live:
            model, client = live_clients(
                hcx_key=settings_module.reveal(cfg.hcx_key),
                openai_key=settings_module.reveal(cfg.openai_key),
                kosis_key=settings_module.reveal(cfg.kosis_key),
                min_interval=cfg.hcx_min_interval,
            )
            return cls(model, client, cfg.mode)
        return cls(*synthetic_clients(), cfg.mode)

    def resolve(self, query: str, tables: list[dict], as_of: str | None = None) -> tuple[dict, int]:
        """(B envelope, model calls). A missing model is reported as a failed B stage, not invented."""
        trace = Trace()
        if self.model is None:
            return outcome("B", failure=StageFailure("hcx_key_missing"), trace_ref=trace.run_id), 0
        model = _Counting(self.model, "complete")
        services = Services(store(), None, model, Config(as_of=as_of) if as_of else Config(), trace)
        a_result = {"tables": copy.deepcopy(tables)}
        try:
            result = build_b_graph(services).invoke(
                {"query": query, "a_result": a_result}, {"recursion_limit": 10 + 4 * len(tables)}
            )["stage_outcome"]
        except StageFailure as exc:
            result = outcome("B", failure=exc, trace_ref=trace.run_id)
        return result, model.calls

    def fetch(self, b_result: dict) -> tuple[dict, int]:
        """(fetch envelope, external calls). Without a client: status not_run with the request plan."""
        if self.client is None:
            plan = [build_api_request(s) for s in b_result.get("selections") or []]
            return {
                "stage": "fetch",
                "status": "not_run",
                "result": {"requests": plan},
                "issues": [{"code": "kosis_key_missing", "message": None}],
                "trace_ref": None,
            }, 0
        client = _Counting(self.client, "fetch")
        return fetch_data(b_result, store(), client, Trace()), client.calls
