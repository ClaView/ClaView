"""The shared period policy file and its loader.

The policy is data, not code: the frequency order, periods per year and the default recent window
come from `period_policy.json` shipped in this package, so A and B read the same numbers from the
same file. `load_policy()` validates the file and fails loudly (`PolicyError`) on a missing or
malformed policy; nothing falls back to built-in numbers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

POLICY_FILE = "period_policy.json"
FREQUENCIES = ("Y", "H", "Q", "M")


class PolicyError(ValueError):
    """The policy file is missing, unreadable or breaks its shape."""


@dataclass(frozen=True)
class Policy:
    version: str
    sha256: str
    frequency_order: tuple[str, ...]
    periods_per_year: dict[str, int]
    default_recent_years: dict[str, int]

    def default_window(self, frequency: str) -> int:
        """Periods in the default recent window of a frequency (years x periods per year)."""
        return self.default_recent_years[frequency] * self.periods_per_year[frequency]


def _read(path: str | Path | None) -> bytes:
    if path is not None:
        try:
            return Path(path).read_bytes()
        except OSError as exc:
            raise PolicyError(f"policy file unreadable: {exc.__class__.__name__}") from None
    try:
        return resources.files(__package__).joinpath(POLICY_FILE).read_bytes()
    except OSError as exc:
        raise PolicyError(f"packaged policy file unreadable: {exc.__class__.__name__}") from None


def _check(data: Any) -> None:
    if not isinstance(data, dict):
        raise PolicyError("policy: not an object")
    if not isinstance(data.get("policy_version"), str) or not data["policy_version"].strip():
        raise PolicyError("policy: policy_version missing")
    order = data.get("frequency_order")
    if not isinstance(order, list) or sorted(order) != sorted(FREQUENCIES) or len(order) != 4:
        raise PolicyError("policy: frequency_order must list Y, H, Q, M once each")
    for key in ("periods_per_year", "default_recent_years"):
        table = data.get(key)
        if not isinstance(table, dict) or set(table) != set(FREQUENCIES):
            raise PolicyError(f"policy: {key} must cover Y, H, Q, M")
        for freq, value in table.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise PolicyError(f"policy: {key}.{freq} must be a positive integer")
    per_year = data["periods_per_year"]
    if per_year != {"Y": 1, "H": 2, "Q": 4, "M": 12}:
        raise PolicyError("policy: periods_per_year must be Y=1, H=2, Q=4, M=12")
    if [per_year[f] for f in order] != sorted(per_year[f] for f in order):
        raise PolicyError("policy: frequency_order must go from the longest to the shortest")


def load_policy(path: str | Path | None = None) -> Policy:
    """The validated policy: the packaged `period_policy.json`, or the file at `path`."""
    raw = _read(path)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PolicyError("policy: not valid JSON") from None
    _check(data)
    return Policy(
        version=data["policy_version"],
        sha256=hashlib.sha256(raw).hexdigest(),
        frequency_order=tuple(data["frequency_order"]),
        periods_per_year=dict(data["periods_per_year"]),
        default_recent_years=dict(data["default_recent_years"]),
    )
