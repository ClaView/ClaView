"""Single-attempt A transports. Runtime owns deadlines, retries and budgets."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass

import httpx

from .a_model_contracts import ACTIONS, BASE_PROMPT, PROMPTS, canonical


class ModelFault(Exception):
    """Only fixed internal codes may escape a provider; no server bodies or headers."""

    def __init__(self, code, *, usage=None):
        super().__init__(code)
        self.code = code
        self.usage = usage


@dataclass(frozen=True)
class Reply:
    value: dict
    usage: tuple[int, int] | None
    resolved_model: str
    diagnostics: dict


def token_usage(input_tokens, output_tokens):
    if any(type(x) is not int or x < 0 for x in (input_tokens, output_tokens)):
        return None
    return input_tokens, output_tokens


def hcx_wire_schema(schema):
    """Use only documented SO keywords; full strict validation remains local."""
    allowed = {
        "type",
        "description",
        "format",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "items",
        "properties",
        "required",
        "enum",
        "anyOf",
    }
    result = {k: v for k, v in schema.items() if k in allowed}
    if "properties" in result:
        result["properties"] = {k: hcx_wire_schema(v) for k, v in result["properties"].items()}
    if "items" in result:
        result["items"] = hcx_wire_schema(result["items"])
    if "anyOf" in result:
        result["anyOf"] = [hcx_wire_schema(v) for v in result["anyOf"]]
    return result


class AProvider:
    """Use MockTransport for offline tests; no credentials are loaded implicitly."""

    def __init__(self, model, *, transport=None, api_key=None):
        if model not in {"HCX-003", "HCX-007"} and not re.fullmatch(r"jev-\d+\.\d+\.\d+", model):
            raise ValueError("Use a supported HCX name or pinned Jev version")
        self.model = model
        self.is_mock = isinstance(transport, httpx.MockTransport)
        self._key = api_key
        self.client = httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        )

    @property
    def identity(self):
        return {
            "model": self.model,
            "adapter": "claview-a-http/1",
            "api": "systemone-v1"
            if self.model.startswith("jev-")
            else ("chat-v1" if self.model == "HCX-003" else "chat-v3-so"),
            "mode": "mock" if self.is_mock else "live",
        }

    @property
    def available(self):
        return self.is_mock or bool(self._key)

    def build(self, role, packet, schema, max_output_tokens, repair=False):
        system = BASE_PROMPT + PROMPTS[role]
        if repair:
            system += " 앞선 응답이 계약 검사에 실패했다. 원문 위치와 허용값을 다시 확인하라."
        if self.model.startswith("jev-"):
            if role != "DecisionClassifier":
                raise ModelFault("unsupported_model_role")
            return "https://api.typesafe.ai/v1/systemone", {
                "model": self.model,
                "state": packet,
                "questions": {
                    "next_action": {"type": "choice", "instructions": system, "criteria": ACTIONS}
                },
            }
        if self.model == "HCX-003":
            if not 1 <= max_output_tokens <= 4096:
                raise ModelFault("invalid_output_limit")
            system += " JSON schema: " + canonical(schema)
            body = {"maxTokens": max_output_tokens, "temperature": 0.01, "seed": 42}
            version = "v1"
        else:
            if not 1 <= max_output_tokens <= 32768:
                raise ModelFault("invalid_output_limit")
            body = {
                "maxCompletionTokens": max_output_tokens,
                "temperature": 0,
                "seed": 42,
                "thinking": {"effort": "none"},
                "responseFormat": {"type": "json", "schema": hcx_wire_schema(schema)},
            }
            version = "v3"
        body["messages"] = [
            {"role": "system", "content": system},
            {"role": "user", "content": canonical(packet)},
        ]
        return (
            f"https://clovastudio.stream.ntruss.com/{version}/chat-completions/{self.model}",
            body,
        )

    def reservation(self, body, max_output_tokens):
        """Reserve a full provider input window, not chars/4 presented as exact tokens."""
        if self.model == "HCX-003":
            input_limit = min(7600, 8192 - max_output_tokens)
        elif self.model == "HCX-007":
            input_limit = 128000 - max_output_tokens
        else:
            # Exactly one question, so the narrower state+question limit applies.
            input_limit = 32000
        # Conservative local preflight; provider tokenizer remains authoritative.
        if len(canonical(body).encode("utf-8")) + 256 > input_limit:
            raise ModelFault("input_preflight_limit")
        return input_limit, max_output_tokens

    async def send(self, url, body, timeout):
        if not self.is_mock and not self._key:
            raise ModelFault("model_unavailable")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
        }
        if self._key:
            headers["Authorization"] = "Bearer " + self._key
        try:
            response = await self.client.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            raise ModelFault("timeout") from None
        except httpx.RequestError:
            raise ModelFault("transport_error") from None
        if response.status_code != 200:
            code = {
                401: "model_unavailable",
                403: "model_unavailable",
                404: "model_unavailable",
                429: "quota",
                529: "overloaded",
            }.get(response.status_code, "provider_http_error")
            raise ModelFault(code)
        if self._key and self._key in response.text:
            raise ModelFault("unsafe_provider_response")
        try:
            wire = response.json()
            if not isinstance(wire, dict):
                raise ValueError
        except ValueError:
            raise ModelFault("format_error") from None
        if self.model.startswith("jev-"):
            return self._jev(wire)
        return self._hcx(wire)

    def _hcx(self, wire):
        usage = None
        try:
            if wire["status"]["code"] != "20000":
                raise ModelFault("provider_status_error")
            result = wire["result"]
            if self.model == "HCX-003":
                usage = token_usage(result.get("inputLength"), result.get("outputLength"))
                stopped = result["stopReason"] in {"end_token", "stop_before"}
            else:
                used = result.get("usage", {})
                usage = token_usage(used.get("promptTokens"), used.get("completionTokens"))
                stopped = result["finishReason"] == "stop"
            if not stopped:
                raise ModelFault("output_truncated", usage=usage)
            value = json.loads(result["message"]["content"])
            if not isinstance(value, dict):
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ModelFault("format_error", usage=usage) from None
        return Reply(value, usage, self.model, {})

    def _jev(self, wire):
        usage = None
        try:
            used = wire.get("usage", {})
            usage = token_usage(used.get("input_tokens"), used.get("output_tokens"))
            if wire["model"] != self.model:
                raise ModelFault("model_version_mismatch", usage=usage)
            if set(wire["answers"]) != {"next_action"}:
                raise ValueError
            answer = wire["answers"]["next_action"]
            probs = answer["probabilities"]
            values = [*probs.values(), answer["confidence"]]
            if (
                answer["type"] != "choice"
                or answer["choice"] not in ACTIONS
                or set(probs) != set(ACTIONS)
                or any(
                    type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                    for v in values
                )
                or not math.isclose(sum(probs.values()), 1, abs_tol=0.001)
                or probs[answer["choice"]] != max(probs.values())
            ):
                raise ValueError
            value = {"action": answer["choice"]}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ModelFault("format_error", usage=usage) from None
        return Reply(
            value,
            usage,
            wire["model"],
            {
                "provider_probabilities": probs,
                "provider_confidence": answer["confidence"],
                "a_correctness_probability": None,
            },
        )

    async def close(self):
        await self.client.aclose()
