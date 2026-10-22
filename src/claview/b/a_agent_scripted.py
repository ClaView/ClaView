"""Explicit local response fixtures through the real A-role wire adapter; never live HTTP."""

import json

import httpx

from .a_agent import ROLES, AAgent, AgentConfig
from .a_model_providers import AProvider
from .contracts import StageFailure, fingerprint


def scripted_agent(path, config=None, **kwargs):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(data, dict)
        or set(data) - set(ROLES)
        or any(
            not isinstance(values, list)
            or not values
            or any(not isinstance(v, dict) for v in values)
            for values in data.values()
        )
    ):
        raise StageFailure("invalid_a_response_fixture")
    providers = {}
    for role, values in data.items():
        sequence = iter(values)

        def handler(request, sequence=sequence):
            value = next(sequence, {"fault": "quota"})
            if "fault" in value:
                if value["fault"] == "timeout":
                    raise httpx.ReadTimeout("synthetic timeout", request=request)
                return httpx.Response(429 if value["fault"] == "quota" else 503)
            return httpx.Response(
                200,
                json={
                    "status": {"code": "20000"},
                    "result": {
                        "message": {"content": json.dumps(value, ensure_ascii=False)},
                        "finishReason": "stop",
                        "usage": {"promptTokens": 100, "completionTokens": 40},
                    },
                },
            )

        providers[role] = AProvider("HCX-007", transport=httpx.MockTransport(handler))
    return AAgent(
        config or AgentConfig(enabled=True),
        providers,
        provider_revision=fingerprint(path),
        **kwargs,
    )


async def close_agent(agent):
    if agent:
        for provider in agent.providers.values():
            await provider.close()
