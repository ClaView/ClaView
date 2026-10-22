"""Plan 18 Responses boundary: one transport attempt, raw-body archive, explicit replay.

No key is read on import or source construction. The production transport reads only
OPENAI_API_KEY immediately before a separately approved call. It never logs headers,
exception messages or HTTP error bodies. The stdlib HTTPS client sends once, with
no SDK retry layer, redirects or environment proxies. Its 60-second socket timeout
are not a promise that a server stops billing after a client timeout.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .decompose_archive import ResponseArchive, canonical, digest, now
from .decompose_payload import INPUT_VERSION, prompt_text
from ..period import package_identity


class ProviderError(Exception):
    pass


def safe_error(exc: Exception) -> str:
    allowed = {"transport", "key_unavailable", "unsafe_response", "request_limit", "response_format",
               "incomplete", "refusal", "empty_output"}
    value = str(exc) if type(exc) is ProviderError else "transport"
    return value if value in allowed or re.fullmatch(r"http_[1-5][0-9]{2}", value) else "transport"


def versions() -> dict:
    """Digests of the code and data behind a decomposition call (record only, never a replay key). Plan 19 stages
    4-5: the input layers (segments, mentions, tokens, lexicon, condition words, occurrence lookup), the prompt and the
    input version label are recorded too."""
    folder = Path(__file__).resolve().parents[1]
    result = {name: digest((folder / name).read_text(encoding="utf-8")) for name in (
        "interpret/decompose_payload.py", "interpret/decompose_schema.py", "interpret/decompose_verify.py",
        "interpret/decompose_refs.py", "interpret/decompose_structure.py",
        "interpret/decompose_settle.py", "interpret/decompose_run.py", "interpret/condition_fields.py",
        "interpret/decompose_provider.py", "interpret/decompose_archive.py", "development.py",
        "contract/check.py", "contract/shapes.py",
        "data/decompose_policy.json", "data/lexicon.json", "period.py", "data/period_dependency.json",
        "interpret/segments.py", "interpret/mentions.py", "interpret/tokens.py", "interpret/lexicon.py",
        "interpret/condition_rules.py", "data/condition_words.json", "catalog/occurrences.py",
        "data/prompts/decompose.md")}
    result["dependency/claview_period"] = digest(package_identity())
    result["input_version"] = INPUT_VERSION
    return result


@dataclass(frozen=True)
class ResponseSettings:
    model: str
    effort: str = "low"
    max_output_tokens: int = 8192
    max_input_bytes: int = 100_000

    def __post_init__(self):
        if not self.model or self.effort not in ("none", "low", "medium", "high", "xhigh", "max"):
            raise ValueError("invalid model settings")
        if not 1 <= self.max_output_tokens <= 8192 or not 1 <= self.max_input_bytes <= 100_000:
            raise ValueError("invalid development token/byte limits")

    def request(self, payload: dict, schema: dict) -> dict:
        return {"model": self.model, "reasoning": {"effort": self.effort}, "instructions": prompt_text(),
                "input": canonical(payload), "text": {"format": {
                    "type": "json_schema", "name": "question_decomposition", "strict": True, "schema": schema}},
                "max_output_tokens": self.max_output_tokens, "store": False, "service_tier": "default"}


class OpenAITransport:
    kind = "live"

    @staticmethod
    def _key() -> str:
        value = os.environ.get("OPENAI_API_KEY", "").strip()
        if not value or any(c.isspace() or ord(c) < 32 or ord(c) > 126 for c in value):
            raise ProviderError("key_unavailable")
        return value

    def preflight(self) -> None:
        self._key()

    def send(self, request: dict) -> str:
        import http.client

        key = self._key()
        connection = None
        try:
            connection = http.client.HTTPSConnection("api.openai.com", timeout=60.0)
            connection.request("POST", "/v1/responses", body=canonical(request).encode("utf-8"),
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise ProviderError("http_" + str(response.status))
            body = response.read().decode("utf-8")
            if key in body:
                raise ProviderError("unsafe_response")
            return body
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("transport") from None
        finally:
            if connection is not None:
                connection.close()


def extract(raw: str) -> tuple[str, dict | None]:
    try:
        response = json.loads(raw)
        usage = response.get("usage")
        if response.get("status") != "completed":
            raise ProviderError("incomplete")
        parts = [c for o in response.get("output", []) if o.get("type") == "message"
                 for c in o.get("content", [])]
        if any(c.get("type") == "refusal" for c in parts):
            raise ProviderError("refusal")
        texts = [c["text"] for c in parts if c.get("type") == "output_text"]
        if len(texts) != 1 or not isinstance(texts[0], str) or not texts[0].strip():
            raise ProviderError("empty_output")
        return texts[0], usage
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ProviderError("response_format") from None


class LiveSource:
    execution_mode = "live"

    def __init__(self, archive: ResponseArchive, call_id: str, settings: ResponseSettings, *,
                 approved: bool = False, transport=None):
        self.archive, self.call_id, self.settings = archive, call_id, settings
        self.approved, self.transport = approved, transport or OpenAITransport()
        self.measurement = {}

    def validate(self) -> None:
        if self.approved is not True:
            raise ValueError("live execution requires explicit approval")
        if self.transport.kind not in ("live", "fake") or not self.call_id:
            raise ValueError("invalid source configuration")

    def fingerprint(self) -> dict:
        return {"mode": self.execution_mode, "archive": str(self.archive.root.resolve()), "call_id": self.call_id,
                "settings": vars(self.settings), "kind": self.transport.kind,
                "prompt": digest(prompt_text()), "versions": versions()}

    def __call__(self, payload, schema):
        self.measurement = {"real_calls": 0, "fake_calls": 0, "replay_calls": 0, "usage": None}
        self.validate()
        request = self.settings.request(payload, schema)
        if len(canonical(request).encode("utf-8")) > self.settings.max_input_bytes:
            raise ProviderError("request_limit")
        self.transport.preflight()
        row = self.archive.reserve(self.call_id, request, versions(), self.transport.kind)
        start = time.perf_counter()
        try:
            row["attempts"] = 1
            self.archive.update(row)  # a crash after this point must never cause a resend
            self.measurement["real_calls" if self.transport.kind == "live" else "fake_calls"] = 1
            raw = self.transport.send(request)
            if not isinstance(raw, str):
                raise ProviderError("response_format")
            row.update(status="received", raw_response=raw, response_hash=digest(raw))
            try:
                body = json.loads(raw)
                row["usage"] = body.get("usage") if isinstance(body, dict) else None
            except ValueError:
                pass
            self.measurement["usage"] = row["usage"]
            return extract(raw)[0]
        except Exception as exc:
            # Unknown exception messages are never persisted, even from injected transports.
            row["error_code"] = safe_error(exc)
            if row["raw_response"] is None:
                row["status"] = "failed"
            self.measurement["error_code"] = row["error_code"]
            raise ProviderError(row["error_code"]) from None
        finally:
            row.update(finished_at=now(), latency_ms=(time.perf_counter() - start) * 1000)
            self.measurement["latency_ms"] = row["latency_ms"]
            self.archive.update(row)


class ReplaySource:
    execution_mode = "replay"

    def __init__(self, archive: ResponseArchive, call_id: str, settings: ResponseSettings):
        self.archive, self.call_id, self.settings = archive, call_id, settings
        self.measurement = {}

    def validate(self) -> None:
        if not self.call_id:
            raise ValueError("replay needs a call ID")

    def fingerprint(self) -> dict:
        return {"mode": self.execution_mode, "archive": str(self.archive.root.resolve()), "call_id": self.call_id,
                "settings": vars(self.settings), "prompt": digest(prompt_text()), "versions": versions()}

    def __call__(self, payload, schema):
        self.measurement = {"real_calls": 0, "fake_calls": 0, "replay_calls": 1, "usage": None}
        row = self.archive.read(self.call_id, self.settings.request(payload, schema))
        # Generation identity must match; old verifier/settler versions are provenance, not cache keys.
        self.measurement.update(archived_usage=row["usage"], archived_versions=row["versions"],
                                current_versions=versions(), transport_kind=row["transport_kind"])
        return extract(row["raw_response"])[0]

