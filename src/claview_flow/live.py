"""Explicit opt-in for B's live clients (HCX selection model, KOSIS fetch).

Nothing in this repository calls this by default. Live runs need the requester's
approval of call count and cost first (AGENTS.md). Only environment variable NAMES
appear here; key values are never returned in errors or logged.
"""

from __future__ import annotations

import logging
import os

HCX_KEY_ENV = "CLOVASTUDIO_API_KEY"
KOSIS_KEY_ENV = "KOSIS_API_KEY"


class LiveNotAllowed(PermissionError):
    """Live B clients were requested without the explicit opt-in."""


def live_b_clients(allow_external: bool = False, *, hcx_min_interval: float = 0):
    """Return (HcxModel, KosisClient) for real B runs, only when `allow_external is True`."""
    if allow_external is not True:
        raise LiveNotAllowed("live B clients need allow_external=True (approved live run)")
    missing = [name for name in (HCX_KEY_ENV, KOSIS_KEY_ENV) if not os.environ.get(name)]
    if missing:
        raise RuntimeError("missing environment variables: " + ", ".join(missing))
    from claview.fetch import KosisClient
    from claview.models import HcxModel

    # KosisClient puts the key in the query string; httpx logs full request URLs at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return (
        HcxModel(os.environ[HCX_KEY_ENV], min_interval=hcx_min_interval),
        KosisClient(os.environ[KOSIS_KEY_ENV]),
    )
