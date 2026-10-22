"""A's AdapterPort bound to the real B functions (adapter, contract checks, run, fetch).

Mirrors the wiring of A's offline probe (scripts/completion2_b_offline.py) without
its synthetic parts: the B selection model and the KOSIS client are injected.
"""

from __future__ import annotations

from typing import Any

from claview.a.agent.assumptions import VERSION as A_ASSUMPTIONS_VERSION
from claview.a.agent.b_connection import AdapterPort
from claview.b.adapter_v3 import to_b_request
from claview.b.contract import (
    A_ASSUMPTION_VERSIONS,
    B_CONTRACT_VERSION,
    check_b_result,
    check_request,
    check_scope_result,
)
from claview.b.run import run_b_request
from claview.contracts import Config, Trace
from claview.fetch import DataClient, build_api_request, fetch_data
from claview.metadata import MetadataStore
from claview.models import SelectionModel
from claview.services import Services

NOT_RUN_FETCH_REASON = "no_fetch_client"


def check_versions() -> None:
    """A's public assumption version must be one B accepts (checked before any port exists)."""
    if A_ASSUMPTIONS_VERSION not in A_ASSUMPTION_VERSIONS:
        raise RuntimeError(
            f"B ({B_CONTRACT_VERSION}) does not accept A assumptions {A_ASSUMPTIONS_VERSION}"
        )


def make_b_port(
    store: MetadataStore,
    *,
    model: SelectionModel,
    fetch_client: DataClient | None = None,
    search: Any = None,
    as_of: str | None = None,
) -> AdapterPort:
    """Build the port A calls after its outer graph returns.

    `model` answers B's selection prompts. `fetch_client` reads values; when it is None
    the fetch step does not run and reports a labelled not-run plan, so A's
    `execution_status` cannot become `completed`. `as_of` is used only when a request
    carries none (A always sets one). The port keeps the services of its latest request
    in `port.services["b"]` (one request at a time; not thread-safe).
    """
    check_versions()
    services: dict[str, Services] = {}

    def run_b(request: dict, holder: dict) -> dict:
        when = request.get("as_of") or as_of
        config = Config(as_of=when) if when else Config()
        holder["b"] = Services(store, search, model, config, Trace())
        return run_b_request(request, holder["b"])

    def fetch(result: dict, holder: dict) -> dict:
        selections = {"selections": result["selections"]}
        if fetch_client is None:
            return {
                "status": "not_run",
                "reason": NOT_RUN_FETCH_REASON,
                "requests": [build_api_request(s) for s in result["selections"]],
            }
        return fetch_data(selections, store, fetch_client, holder["b"].trace)

    return AdapterPort(
        adapter=to_b_request,
        check_request=check_request,
        check_scope_result=check_scope_result,
        check_result=check_b_result,
        run_b=run_b,
        fetch=fetch,
        services=services,
        contract_version=B_CONTRACT_VERSION,
        a_assumptions_version=A_ASSUMPTIONS_VERSION,
    )
