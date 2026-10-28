"""Model call boundary copied from the old A pipeline (commit 55fd41b). Not used in stage 1 (no live path).

Copied: ``ERROR_CLASSES``, ``ModelError``, ``ModelReply``, ``CachingClient``, ``_schema_for_hcx``, ``_count``,
``HcxClient``. Not copied: ``ModelClient`` protocol, ``check_schema``, ``FakeClient``.

One change: ``HcxClient`` takes the API key as a constructor argument only. It does not read environment
variables or ``.env``. The key is never put in messages or ``repr``.
Key safety (plan 06 review, 10/19): the key is checked before use (``usable_api_key``: not empty, no whitespace or
control characters after strip), and any exception while building or sending a request (an invalid header value
included, whose message would hold the header) becomes a ``ModelError`` carrying only the exception class name,
raised ``from None`` so that no traceback holds the original message.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Callable

from .._guard import NetworkBlocked


def usable_api_key(value) -> str | None:
    """The key after strip, or None when it is empty or holds whitespace or control characters. The value is never
    echoed: callers report only that it is not usable."""
    if not isinstance(value, str):
        return None
    key = value.strip()
    if not key or any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in key):
        return None
    return key

# Source: modules/a/claview_aflow/model.py:23 (commit 55fd41b), copied verbatim.
ERROR_CLASSES = ("transient", "auth", "schema", "other")


# Source: modules/a/claview_aflow/model.py:26-34 (commit 55fd41b), copied verbatim.
class ModelError(Exception):
    """클라이언트가 내는 호출 오류. error_class는 CallRow와 같은 분류다."""

    def __init__(self, error_class: str, detail: str = "", *, http_status=None, retry_after=0.0):
        if error_class not in ERROR_CLASSES:
            raise ValueError(f"error_class는 {ERROR_CLASSES} 중 하나: {error_class!r}")
        super().__init__(detail or error_class)
        self.error_class = error_class
        self.http_status, self.retry_after = http_status, retry_after


# Source: modules/a/claview_aflow/model.py:37-41 (commit 55fd41b), copied verbatim.
@dataclass(frozen=True)
class ModelReply:
    text: str  # 모델이 낸 JSON 문자열
    tokens_in: int = 0
    tokens_out: int = 0


# Source: modules/a/claview_aflow/model.py:127-160 (commit 55fd41b), copied verbatim.
class CachingClient:
    """같은 (node, system, payload, schema)의 결과를 재사용한다. 오류도 순서대로 기록해 다시 낸다 (Codex F10).

    키마다 받은 결과(응답 또는 ModelError)를 순서대로 남긴다. start_run() 뒤 그 키의 n번째 요청은
    기록의 n번째를 그대로 돌려주므로, 평가에서 Q6만 켠 실행과 둘 다 켠 실행이 같은 Q6 결과(실패·재시도 포함)를 본다.
    기록이 모자라면 마지막이 성공일 때 그 응답을, 아니면 실제로 요청한다. 실제 요청 수는 requests_sent로 센다.
    """

    def __init__(self, inner):
        self.inner, self.name, self.cache, self.requests_sent = inner, inner.name, {}, 0
        self._seen: dict = {}

    def start_run(self) -> None:
        self._seen = {}

    def complete(self, node: str, system: str, payload: dict, schema: dict) -> ModelReply:
        key = json.dumps([getattr(self.inner, "token_version", "legacy"), node, system, payload, schema], ensure_ascii=False, sort_keys=True)
        history = self.cache.setdefault(key, [])
        n = self._seen.get(key, 0)
        self._seen[key] = n + 1
        if n < len(history):
            out = history[n]
        elif history and isinstance(history[-1], ModelReply):
            out = history[-1]
        else:
            self.requests_sent += 1
            try:
                out = self.inner.complete(node, system, payload, schema)
            except ModelError as err:
                out = err
            history.append(out)
        if isinstance(out, ModelError):
            raise ModelError(out.error_class, str(out))
        return out


# Source: modules/a/claview_aflow/model.py:166-168 (commit 55fd41b), copied verbatim.
def _schema_for_hcx(schema: dict) -> dict:
    """$id는 응답 형식에 넣지 않는다. 나머지 키워드를 HCX가 다 지키는지는 첫 실제 호출에서 확인한다."""
    return {k: v for k, v in schema.items() if k != "$id"}


# Source: modules/a/claview_aflow/model.py:171-173 (commit 55fd41b), copied verbatim.
def _count(value) -> int:
    """토큰 수. 정수가 아니면 0 (bool 제외)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


# Source: modules/a/claview_aflow/model.py:176-267 (commit 55fd41b). Changed: __init__ takes ``api_key`` instead of
# ``key_env`` and never reads the environment. Everything else (request, pacing, error classes) is unchanged.
class HcxClient:
    """HCX-007 구조화 출력. 오류 분류: 429·5xx·시간 초과·연결 오류 → transient, 401·403 → auth,
    응답 잘림·형식 이상 → schema, 그 밖 4xx·서비스 코드 오류 → other. 예외 문장에 키·헤더를 담지 않는다."""

    url = "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-007"
    max_tokens = {"Q6": 1024, "T2": 512}
    token_profiles = {"legacy": {"Q6": 1024, "T2": 512},
                      "q6g_tokens_v2": {"Q6": 1024, "T2": 512, "Q6G": 1536}}

    def __init__(self, api_key: str, min_interval: float = 1.0, timeout: float = 60.0,
                 opener: Callable | None = None, token_version: str = "legacy"):
        if token_version not in self.token_profiles:
            raise ValueError("Unknown HCX token version")
        key = usable_api_key(api_key)
        if key is None:
            raise ModelError("auth", "API key not given or not usable")
        self._key = key
        self.name = "HCX-007"
        self.token_version = token_version
        self.max_tokens = dict(self.token_profiles[token_version])
        self.min_interval, self.timeout = min_interval, timeout
        self._opener = opener or urllib.request.urlopen
        self._last = None
        self._cooldown = 0.0

    def __repr__(self) -> str:  # 키가 출력되지 않게
        return "HcxClient(HCX-007)"

    def _wait(self) -> None:
        now = time.monotonic()
        gap = max(self.min_interval - (now - self._last) if self._last is not None else 0.0, self._cooldown)
        if gap > 0:
            time.sleep(gap)
        self._cooldown = 0.0
        self._last = time.monotonic()

    def complete(self, node: str, system: str, payload: dict, schema: dict) -> ModelReply:
        try:
            return self._read(self._send(node, system, payload, schema))
        except (ModelError, NetworkBlocked):  # the boundary guard's refusal names only the transport event
            raise
        except Exception as err:  # noqa: BLE001 - the class name only: a message may hold a header (the key)
            raise ModelError("other", type(err).__name__) from None

    def _send(self, node: str, system: str, payload: dict, schema: dict) -> bytes:
        body = {
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "thinking": {"effort": "none"},
            "responseFormat": {"type": "json", "schema": _schema_for_hcx(schema)},
            "temperature": 0, "seed": 42,
            "maxCompletionTokens": self.max_tokens.get(node, 512),
        }
        req = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Authorization": "Bearer " + self._key,
                                              "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
                                              "Content-Type": "application/json"})
        self._wait()
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            status = err.code
            if status == 429:
                retry = err.headers.get("retry-after", "") if err.headers else ""
                self._cooldown = min(float(retry), 30.0) if re.fullmatch(r"\d{1,4}(\.\d+)?", retry) else 5.0
                raise ModelError("transient", "HTTP 429", http_status=status, retry_after=self._cooldown) from None
            if status >= 500:
                raise ModelError("transient", f"HTTP {status}", http_status=status) from None
            if status in (401, 403):
                raise ModelError("auth", f"HTTP {status}", http_status=status) from None
            raise ModelError("other", f"HTTP {status}", http_status=status) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            raise ModelError("transient", type(err).__name__) from None

    def _read(self, raw) -> ModelReply:
        try:
            data = json.loads(raw)
        except ValueError:
            raise ModelError("schema", "응답이 JSON이 아님") from None
        # 바깥 응답은 경계에서 자료형을 확인한다. 형식 이상은 schema로 바꿔 규칙 경로로 돌아가게 한다 (F09)
        if not isinstance(data, dict):
            raise ModelError("schema", "응답 최상위가 객체가 아님")
        status = data.get("status")
        if not isinstance(status, dict):
            raise ModelError("schema", "status 형식 이상")
        if str(status.get("code")) != "20000":
            raise ModelError("other", "서비스 코드 오류")
        result = data.get("result")
        if not isinstance(result, dict):
            raise ModelError("schema", "result 형식 이상")
        if result.get("finishReason") == "length":
            raise ModelError("schema", "출력 잘림")
        message = result.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str):
            raise ModelError("schema", "message.content 없음")
        if self._key in text:
            raise ModelError("other", "응답에 민감 문자열")
        usage = result.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return ModelReply(text, _count(usage.get("promptTokens")), _count(usage.get("completionTokens")))
