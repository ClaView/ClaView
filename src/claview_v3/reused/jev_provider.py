"""Request shape and reply checks of the jev systemone choice question (one question per request).

Source (shape copied, read as text only): the working folder's ``src/claview/a_model_providers.py`` -
``AProvider.__init__`` (pinned ``jev-X.Y.Z`` model names), ``AProvider.build`` for jev models (URL and body
``{model, state, questions: {name: {type: "choice", instructions, criteria}}}``), ``AProvider.reservation`` (32000
input limit for one question, local preflight), ``AProvider.send`` (headers, status-code mapping, key-echo check) and
``AProvider._jev`` (reply checks: model version, the one answer, choice inside the criteria, probabilities over
exactly the criteria, finite values in [0, 1], sum 1, the choice has the highest probability; usage
``input_tokens``/``output_tokens``).

Changes from the source: the question name and criteria are parameters (the source fixed ``next_action`` and its
action list); httpx is replaced by ``urllib.request`` because this package allows no third-party module but
langgraph; the send function is used only by the live mode of ``verify.jev``, which needs an explicit flag and the
key from the environment. The prompt wording is not copied (``verify.jev`` has its own). Fault codes are fixed
strings: no server body, header, URL or key is ever put in an exception, and none is chained to one.
Transport safety kept from the source and made explicit for urllib: redirects are not followed (3xx ->
``redirect_refused``), environment proxies are not used, the key must match ``KEY_PATTERN`` before any header is
built, and a reply that holds the key (raw or after JSON decoding) is refused. Only the checked answer fields
(``CACHE_FIELDS``) may be stored by the caller.
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
import uuid
from typing import Any

URL = "https://api.typesafe.ai/v1/systemone"
MODEL_PATTERN = re.compile(r"jev-\d+\.\d+\.\d+")
INPUT_LIMIT = 32000  # one question: the state + question limit of the source


class JevFault(Exception):
    """Only fixed internal codes may leave this module."""

    def __init__(self, code: str, *, usage: dict | None = None):
        super().__init__(code)
        self.code = code
        self.usage = usage


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def check_model(model: Any) -> str:
    if not isinstance(model, str) or not MODEL_PATTERN.fullmatch(model):
        raise ValueError("Use a pinned Jev version (jev-X.Y.Z)")
    return model


def build_request(model: str, state: dict, name: str, instructions: str, criteria: tuple[str, ...]):
    """(url, body) of one choice question; raises JevFault when the body is over the local preflight limit."""
    body = {
        "model": check_model(model),
        "state": state,
        "questions": {name: {"type": "choice", "instructions": instructions, "criteria": list(criteria)}},
    }
    if len(canonical(body).encode("utf-8")) + 256 > INPUT_LIMIT:
        raise JevFault("input_preflight_limit")
    return URL, body


def token_usage(used: Any) -> dict | None:
    if not isinstance(used, dict):
        return None
    pair = used.get("input_tokens"), used.get("output_tokens")
    if any(type(x) is not int or x < 0 for x in pair):
        return None
    return {"input_tokens": pair[0], "output_tokens": pair[1]}


CACHE_FIELDS = ("model", "choice", "probabilities", "confidence", "usage")


def check_answer(answer: Any, model: str, criteria: tuple[str, ...]) -> dict:
    """The checks of the source's ``_jev`` on one answer ``{model, choice, probabilities, confidence, usage}``.

    Used on a reply (``parse_reply``) and on a cached answer (replay), so a cache file is held to the same checks.
    Returns a new dict with exactly ``CACHE_FIELDS``; raises JevFault otherwise.
    """
    usage = None
    try:
        if not isinstance(answer, dict):
            raise ValueError
        usage = answer.get("usage")
        if usage is not None and token_usage(usage) != usage:
            raise ValueError
        if answer["model"] != model:
            raise JevFault("model_version_mismatch", usage=usage)
        probs = answer["probabilities"]
        values = [*probs.values(), answer["confidence"]]
        if (
            answer["choice"] not in criteria
            or set(probs) != set(criteria)
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values)
            or not math.isclose(sum(probs.values()), 1, abs_tol=0.001)
            or probs[answer["choice"]] != max(probs.values())
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise JevFault("format_error", usage=usage) from None
    return {"model": answer["model"], "choice": answer["choice"], "probabilities": {c: probs[c] for c in criteria},
            "confidence": answer["confidence"], "usage": usage}


def parse_reply(wire: Any, model: str, name: str, criteria: tuple[str, ...]) -> dict:
    """Checked answer ``{model, choice, probabilities, confidence, usage}`` of a reply; JevFault otherwise."""
    usage = None
    try:
        if not isinstance(wire, dict):
            raise ValueError
        usage = token_usage(wire.get("usage", {}))
        if wire["model"] != model:
            raise JevFault("model_version_mismatch", usage=usage)
        if set(wire["answers"]) != {name}:
            raise ValueError
        answer = wire["answers"][name]
        if answer["type"] != "choice":
            raise ValueError
        candidate = {"model": wire["model"], "choice": answer["choice"], "probabilities": answer["probabilities"],
                     "confidence": answer["confidence"], "usage": usage}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise JevFault("format_error", usage=usage) from None
    return check_answer(candidate, model, criteria)


# ---------------------------------------------------------------- transport (live mode only)

_STATUS_CODES = {401: "model_unavailable", 403: "model_unavailable", 404: "model_unavailable", 429: "quota",
                 529: "overloaded"}
# Characters a bearer key may hold; anything else (spaces, line breaks, control characters) is refused before a
# header is built, so no header error can carry the key.
KEY_PATTERN = re.compile(r"[A-Za-z0-9._~+/=-]{8,512}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect (the source's ``follow_redirects=False``): the Authorization header must not go to
    another host. urllib then raises HTTPError with the 3xx code, mapped to ``redirect_refused``."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_opener() -> urllib.request.OpenerDirector:
    """No redirects and no proxies from the environment (the source's ``trust_env=False``)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def check_key(api_key: Any) -> str:
    if not isinstance(api_key, str) or not KEY_PATTERN.fullmatch(api_key):
        raise JevFault("invalid_key_format")
    return api_key


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def echoes_key(wire: Any, api_key: str) -> bool:
    """The key anywhere in the decoded reply (JSON escapes such as ``\\/`` or ``\\u0041`` are undone by decoding)."""
    return any(api_key in s for s in _strings(wire)) or api_key in canonical(wire)


def _http_code(status: int) -> str:
    if 300 <= status < 400:
        return "redirect_refused"
    return _STATUS_CODES.get(status, "provider_http_error")


def send(url: str, body: dict, api_key: str, timeout: float, opener: Any = None) -> dict:
    """POST one request (live mode only). The caller has already checked the explicit flag and the key's presence.

    Every failure leaves as a JevFault with a fixed code, raised outside the ``except`` block so that no exception
    holding a URL, header or key is chained to it.
    """
    key = check_key(api_key)
    fault = None
    status, raw = None, b""
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     # same request-id header as the source's shared send
                     "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()), "Authorization": "Bearer " + key},
        )
        with (opener or make_opener()).open(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        fault = _http_code(error.code)
    except TimeoutError:
        fault = "timeout"
    except Exception:  # URLError, OSError, header ValueError, ...: never re-raised as it may hold the key
        fault = "transport_error"
    if fault is not None:
        raise JevFault(fault)
    if status != 200:
        raise JevFault(_http_code(status) if isinstance(status, int) else "provider_http_error")
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    if key in text:
        raise JevFault("unsafe_provider_response")
    try:
        wire = json.loads(text)
    except ValueError:
        wire = None
    if not isinstance(wire, dict):
        raise JevFault("format_error")
    if echoes_key(wire, key):
        raise JevFault("unsafe_provider_response")
    return wire
