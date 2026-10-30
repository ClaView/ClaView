"""Settings shared by every service and module: paths, keys and mode, read from the environment in one place.

Key values are wrapped in `Secret`: repr/str/format never show them, and `public()` reports only
whether a key is set. Code that sends a key calls `.reveal()` right where it builds the request.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MODES = ("synthetic", "replay", "live")
KEY_ENVS = {"openai": "OPENAI_API_KEY", "kosis": "KOSIS_API_KEY"}

# The model B asks to pick items and classification values. Fixed in code (not read from the environment) so a
# run is always the one that was measured; change it here, with a new measurement, when the model changes.
B_MODEL_ID = "gpt-6.1-terra"

# Repository root: the folder that holds data/ and eval/ (/claview in the image).
ROOT = Path(os.environ.get("PROJECT_MAIN_DIRECTORY") or Path(__file__).resolve().parents[2])


@dataclass(frozen=True)
class Paths:
    root: Path
    data: Path
    catalog_file: Path  # 349-table list (search_catalog.json)
    meta_raw_file: Path  # metadata snapshot, one table per line (boK_meta_raw.jsonl)
    eval_cases: Path
    golden_file: Path
    runs: Path

    @classmethod
    def under(cls, root: Path) -> Paths:
        data = root / "data"
        cases = root / "eval" / "cases"
        return cls(
            root=root,
            data=data,
            catalog_file=data / "catalog" / "search_catalog.json",
            meta_raw_file=data / "raw" / "boK_meta_raw.jsonl",
            eval_cases=cases,
            golden_file=cases / "golden" / "golden_set_v1.json",
            runs=root / "runs",
        )


PATHS = Paths.under(ROOT)


def usable_key(value) -> str | None:
    """The key after strip, or None when it is empty or holds whitespace or control characters.
    The value is never echoed: callers report only that it is not usable."""
    if not isinstance(value, str):
        return None
    key = value.strip()
    if not key or any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in key):
        return None
    return key


class Secret:
    """A key value that does not show itself in repr, str, format or logs."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __format__(self, spec) -> str:
        return repr(self)

    def __eq__(self, other) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)


def _secret(env: Mapping[str, str], name: str) -> Secret | None:
    key = usable_key(env.get(name))
    return Secret(key) if key else None


def reveal(secret: Secret | None) -> str | None:
    return secret.reveal() if secret is not None else None


@dataclass(frozen=True)
class Settings:
    mode: str
    paths: Paths
    openai_key: Secret | None
    kosis_key: Secret | None

    @property
    def live(self) -> bool:
        return self.mode == "live"

    def public(self) -> dict:
        """What /health and /info may show: whether a key is set, never its value."""
        return {
            "mode": self.mode,
            "openai_key_set": self.openai_key is not None,
            "kosis_key_set": self.kosis_key is not None,
        }


def load(env: Mapping[str, str] | None = None) -> Settings:
    """Read the environment (or `env`, for tests). Keys that are empty or malformed count as not set."""
    env = os.environ if env is None else env
    mode = env.get("CLAVIEW_MODE", "synthetic").strip().lower()
    if mode not in MODES:
        raise ValueError(f"CLAVIEW_MODE must be one of {MODES}")
    root = env.get("PROJECT_MAIN_DIRECTORY")
    return Settings(
        mode=mode,
        paths=Paths.under(Path(root)) if root else PATHS,
        openai_key=_secret(env, KEY_ENVS["openai"]),
        kosis_key=_secret(env, KEY_ENVS["kosis"]),
    )
