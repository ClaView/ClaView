"""Compatibility entry point: the period resolver now lives in the shared package `claview_period`.

B code and older callers keep importing from here; the rules, the policy numbers and the contract
come from `claview_period` (the same files A uses). Nothing is redefined in this module.
"""

from __future__ import annotations

from claview_period import CONTRACT_VERSION, check_result, default_policy, resolve_period_for_table

_policy = default_policy()
# Read from the shared policy file (not copied numbers): kept as names for older callers and tests.
FREQUENCY_ORDER = _policy.frequency_order
PER_YEAR = dict(_policy.periods_per_year)
DEFAULT_RECENT_YEARS = dict(_policy.default_recent_years)
PERIOD_SOURCES = ("question", "catalog", "default", "user")

__all__ = [
    "CONTRACT_VERSION",
    "DEFAULT_RECENT_YEARS",
    "FREQUENCY_ORDER",
    "PERIOD_SOURCES",
    "PER_YEAR",
    "check_result",
    "resolve_period_for_table",
]
