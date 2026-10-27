"""A's boundary to the versioned shared period package; no copied period rules.

The support probe is NOT a catalog table or a proposed query. It runs the public
resolver on a monthly calendar with a fixed request-date anchor, discards every
resolved period and reads only the diagnosis. Selection and B must resolve again
with real table metadata. No probe date/range is written into A state or applied.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path


from . import paths, period_core

_EXPECTED = json.loads((paths.PACKAGE_DATA_DIR / "period_dependency.json").read_text(encoding="utf-8"))


def package_identity() -> dict:
    policy = period_core.default_policy()
    identity = {"package": period_core.__version__, "contract": period_core.CONTRACT_VERSION,
                "policy": policy.version, "policy_sha256": policy.sha256}
    if identity != _EXPECTED:
        raise RuntimeError("shared period package/contract/policy differs from the pinned dependency")
    folder = Path(period_core.__file__).resolve().parent
    identity["files"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(folder.iterdir()) if p.suffix in (".py", ".json")}
    if identity["files"].get("period_policy.json") != identity["policy_sha256"]:
        raise RuntimeError("shared period policy changed after loading")
    return identity


def diagnose_period(attrs: dict, as_of: str) -> str:
    """Only expression support, not table coverage, actual period or attribution."""
    package_identity()
    anchor = date.fromisoformat(as_of).strftime("%Y-%m")
    result = period_core.resolve_period_for_table(
        [{"id": "period_probe", "kind": "period", "attrs": attrs}],
        {"periods": {"M": {"start": "000101", "end": "999912"}}}, as_of,
        inferred=[], context={"bundle_id": "support_probe", "anchor_source": "bundle_common", "anchor": anchor})
    issue = result.get("issue") or {}
    if result["status"] == "invalid_input" or issue.get("detail") == "start_after_end":
        return "malformed"
    if result["status"] in ("ok", "no_data_in_range"):
        return "ok"  # coverage never determines expression support
    return "unsupported"
