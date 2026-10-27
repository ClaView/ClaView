"""Shared success payloads, execution outcomes, and run configuration."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

Json = dict[str, Any]
AWAITING_USER = "awaiting_user"


class StageFailure(Exception):
    def __init__(self, code: str, status: str = "error", field: str | None = None):
        super().__init__(code)
        self.code, self.status, self.field = code, status, field


def outcome(
    stage: str,
    result: Json | None = None,
    failure: StageFailure | None = None,
    trace_ref: str | None = None,
    interaction: Json | None = None,
) -> Json:
    envelope = {
        "stage": stage,
        "status": failure.status if failure else "ok",
        "result": result,
        "issues": []
        if failure is None
        else [
            {
                "code": failure.code,
                "message": failure.code,
                "field": failure.field,
                "series_id": None,
                "retryable": False,
            }
        ],
        "trace_ref": trace_ref,
    }
    if interaction is not None:
        if failure is not None or result is not None:
            raise ValueError("Waiting outcomes cannot contain success or failure")
        envelope.update(status=AWAITING_USER, interaction=interaction)
    return envelope


def _today() -> date:
    return date.today()


def today_iso() -> str:
    """Query date (YYYY-MM-DD) used as the reference date unless a caller sets `as_of`."""
    return _today().isoformat()


@dataclass(frozen=True)
class Config:
    top_k: int = 10
    channel_k: int = 10
    candidate_policy: str = "union"
    candidate_pool_limit: int = 30
    expansion_limit: int = 10
    model_candidate_limit: int = 40
    structure_query: bool = False
    filter_frequency: bool = True
    search_scope: str = "global"
    hierarchy: bool = False
    max_route_rounds: int = 2
    beam_width: int = 2
    stop_table_count: int = 10
    max_context_chars: int = 160000
    # B's own request-size limit (shared max_context_chars still applies if it is lower).
    # HCX answered HTTP 429 for B requests of about 130k characters while ~110k ones went
    # through, so tables above this are pruned (build_b_context) instead of sent whole.
    max_b_context_chars: int = 120000
    # The model sees a slimmed copy of the table (no `path`, no empty `unit`; see
    # stages.model_table). HCX counts input tokens plus the requested output
    # cap against its per-minute token limit (60,000 TPM measured on the current key), so both
    # are kept small. False sends the table as stored, for comparing against the old format.
    compact_model_context: bool = True
    # Output cap of B's model call (was 8192). The longest golden answer is about 2,600
    # characters (14 rows listed one by one); 30 rows, the most B accepts without asking the
    # user, are about 8,000. At the roughly 0.5 token per character seen for codes, 6144 leaves
    # room for that. An answer cut off at the cap fails as `output_truncated`, it is never used.
    max_b_output_tokens: int = 6144
    max_selections: int = 100
    # Series-count cap checked after hierarchy expansion; above it B asks the user to confirm.
    max_expanded_selections: int = 30
    # How many times B may re-ask the model after an unusable `expand` (0 = never).
    max_expand_retries: int = 1
    max_axis_candidates: int = 80
    # Reference date of the run ("today"). Defaults to the query date, read when the Config is
    # built, so a long-lived process must build a new Config per query (or pass `as_of`).
    # Period resolution is anchored on each table's latest recorded period, not on this date;
    # `as_of` only decides whether a lag notice is needed and fills the notice text.
    as_of: str = field(default_factory=lambda: today_iso())
    period_policy: str = "explicit_only"
    transport_retries: int = 2

    def __post_init__(self):
        date.fromisoformat(self.as_of)
        if (
            self.top_k < 1
            or self.channel_k < 1
            or self.candidate_pool_limit < 1
            or self.expansion_limit < 0
            or self.model_candidate_limit < 1
            or self.max_route_rounds < 0
            or self.beam_width < 1
        ):
            raise ValueError("Invalid search budget")
        if self.max_b_context_chars < 1:
            raise ValueError("max_b_context_chars must be >= 1")
        if not 1 <= self.max_b_output_tokens <= 32768:
            raise ValueError("max_b_output_tokens must be between 1 and 32768")
        if self.max_expanded_selections < 1:
            raise ValueError("max_expanded_selections must be >= 1")
        if self.max_expand_retries < 0:
            raise ValueError("max_expand_retries must be >= 0")
        if self.candidate_policy not in {"legacy", "union"}:
            raise ValueError("Invalid candidate policy")
        if self.search_scope not in {"global", "hierarchy", "combined"}:
            raise ValueError("Invalid search scope")
        if self.period_policy != "explicit_only":
            raise ValueError("Only explicit_only is implemented; add a reviewed period policy")


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (
                "[REDACTED]"
                if re.search(r"authorization|api.?key|secret|password", k, re.I)
                else clean(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(apiKey=)[^&\s]+", r"\1[REDACTED]", value)
        return re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
    return value


@dataclass
class Trace:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    events: list[Json] = field(default_factory=list)

    def add(self, event: str, **payload: Any) -> None:
        self.events.append(clean({"event": event, **payload}))

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (self.run_id + ".json")
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return path
