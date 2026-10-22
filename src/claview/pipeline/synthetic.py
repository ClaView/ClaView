"""SYNTHETIC B model and fetch client for offline wiring checks only.

Not a model, not KOSIS, and not evidence of accuracy. The synthetic model answers
every B selection task with the first item, the first value of each dimension and
the first period frequency in the table metadata (the pattern of A's former offline
probe of B). Its answer passes through B's ScriptedModel so
B's own trace events and schema trimming apply. The synthetic fetch client returns
the value "1" for each requested period. Used by tests/pipeline, by
`claview run --synthetic-b` (and `scripts/pipeline/run_flow.py`), and as the fetch
client of the golden replay (`claview.pipeline.evaluate`).
"""

from __future__ import annotations

from claview.b.contracts import StageFailure, Trace
from claview.b.fetch import _RESPONSE_PRD_SE
from claview.b.metadata import MetadataStore, periods_between
from claview.b.models import ScriptedModel

LABEL = "synthetic wiring check (not model or value accuracy)"


class SyntheticSelectionModel:
    """Answers B's `B:<org>:<table>` tasks from metadata only; counts its calls."""

    label = LABEL

    def __init__(self, store: MetadataStore):
        self.store = store
        self.calls: list[str] = []

    def complete(self, task, system, context, schema, max_tokens, trace: Trace):
        self.calls.append(task)
        kind, _, key = task.partition(":")
        org_id, _, table_id = key.partition(":")
        if kind != "B" or not org_id or not table_id:
            raise StageFailure("synthetic_task_unsupported")
        meta = self.store.get(org_id, table_id)
        row = {
            "item_id": next(iter(meta["items"])),
            "dimensions": {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()},
            "period": {"frequency": next(iter(meta["periods"])), "start": "x", "end": "x"},
            "expand": {a: "none" for a in meta["dimensions"]},
        }
        replay = ScriptedModel({task: [{"selections": [row]}]})
        return replay.complete(task, system, context, schema, max_tokens, trace)


class SyntheticFetchClient:
    """Returns one row with value "1" per requested period; counts its calls."""

    label = LABEL

    def __init__(self):
        self.calls: list[dict] = []

    def fetch(self, params: dict, trace: Trace) -> list[dict]:
        self.calls.append(dict(params))
        dims = {k.replace("objL", "C"): v for k, v in params.items() if k.startswith("objL")}
        periods = periods_between(params["startPrdDe"], params["endPrdDe"], params["prdSe"])
        return [
            {
                "ORG_ID": params["orgId"],
                "TBL_ID": params["tblId"],
                "ITM_ID": params["itmId"],
                "PRD_SE": _RESPONSE_PRD_SE[params["prdSe"]],
                "PRD_DE": period,
                "DT": "1",
                **dims,
            }
            for period in periods
        ]
