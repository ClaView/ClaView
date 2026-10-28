"""Plan 18 Responses boundary: one transport attempt, raw-body archive, explicit replay.

No key is read on import or source construction. The production transport reads only
OPENAI_API_KEY immediately before a separately approved call. It never logs headers,
exception messages or HTTP error bodies. The stdlib HTTPS client sends once, with
no SDK retry layer, redirects or environment proxies. Its 60-second socket timeout
are not a promise that a server stops billing after a client timeout.

Plan 20 (Q2): both sources take bind_input (the digest of the raw mapping behind the
payload, given by decompose_run.decompose). The live source stores it in the archive
row (version 2); the replay source reads only a row with the same digest and misses
without one (no live fallback).
"""

from __future__ import annotations
import json
import os
from .canonical import canonical


class ProviderError(Exception):
    pass


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
