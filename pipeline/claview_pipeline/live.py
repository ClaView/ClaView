"""Explicit opt-in for B's live clients (HCX selection model, KOSIS fetch).

Nothing in this repository calls this by default. Live runs need the requester's
approval of call count and cost first (AGENTS.md). Only environment variable NAMES
appear here; key values are never returned in errors or logged.
"""

from __future__ import annotations

from claview_common.settings import KEY_ENVS, load, reveal

HCX_KEY_ENV = KEY_ENVS["hcx"]
KOSIS_KEY_ENV = KEY_ENVS["kosis"]


class LiveNotAllowed(PermissionError):
    """Live B clients were requested without the explicit opt-in."""


def live_b_clients(allow_external: bool = False, *, hcx_min_interval: float = 0):
    """Return (HcxModel, KosisClient) for real B runs, only when `allow_external is True`."""
    if allow_external is not True:
        raise LiveNotAllowed("live B clients need allow_external=True (approved live run)")
    cfg = load()
    missing = [name for name, key in ((HCX_KEY_ENV, cfg.hcx_key), (KOSIS_KEY_ENV, cfg.kosis_key)) if key is None]
    if missing:
        raise RuntimeError("missing environment variables: " + ", ".join(missing))
    from claview_b_api.service import live_clients

    return live_clients(hcx_key=reveal(cfg.hcx_key), kosis_key=reveal(cfg.kosis_key), min_interval=hcx_min_interval)
