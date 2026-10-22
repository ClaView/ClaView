"""ClaView shared period package: the period/frequency resolver, its policy file and its contract.

    from claview_period import resolve_period_for_table, load_policy, check_result

A imports this package; the current B (10/16 compact, plan 25) reads periods from the question itself and does not
import it. Whoever uses it imports these same files at this version and copies none of its rules or numbers.
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
