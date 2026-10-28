"""ClaView shared period package: the period/frequency resolver, its policy file and its contract.

    from claview_period import resolve_period_for_table, load_policy, check_result

(Kept inside the new A as ``claview_a.periods``: this module is the package's former ``__init__``, byte for byte
but this note; its files sit beside it unchanged and the callers read it as ``claview_period``.)

A and B import this package (same files, same version); neither copies its rules or numbers.
"""

from ._version import __version__
from .contract import (
    CONTRACT_VERSION,
    STATUSES,
    check_assignment,
    check_conditions,
    check_context,
    check_result,
    check_table_meta,
)
from .policy import Policy, PolicyError, load_policy
from .resolver import default_policy, resolve_period_for_table

__all__ = [
    "CONTRACT_VERSION",
    "STATUSES",
    "Policy",
    "PolicyError",
    "__version__",
    "check_assignment",
    "check_conditions",
    "check_context",
    "check_result",
    "check_table_meta",
    "default_policy",
    "load_policy",
    "resolve_period_for_table",
]
