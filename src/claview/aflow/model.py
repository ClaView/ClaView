"""모델 호출 경계 (A v1: Q6G 해석 한 번).

- Q6G(q6g.interpret)는 ModelClient.complete만 부른다. 어떤 회사의 모델·키를 쓰는지는 클라이언트 몫이며 이 모듈은 모른다.
- 원시 출력은 check_schema로 검사한 뒤에만 State로 옮긴다. 요청은 한 번이며 재시도하지 않는다.
- 정답·평가 기준은 입력에 넣지 않는다.
- FakeClient는 시험·평가 실행기용이며 네트워크를 쓰지 않는다. CachingClient는 같은 입력의 결과를 재사용한다.
- HcxClient는 실제 호출용이다. 키는 환경 변수에서 만들 때 한 번 읽고 기록·출력하지 않는다. 재시도는 하지 않는다.
  호출은 사용자 승인 뒤에만 한다.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol

ERROR_CLASSES = ("transient", "auth", "schema", "other")


class ModelError(Exception):
    """클라이언트가 내는 호출 오류. error_class는 CallRow와 같은 분류다."""

    def __init__(self, error_class: str, detail: str = "", *, http_status=None, retry_after=0.0):
        if error_class not in ERROR_CLASSES:
            raise ValueError(f"error_class는 {ERROR_CLASSES} 중 하나: {error_class!r}")
        super().__init__(detail or error_class)
        self.error_class = error_class
        self.http_status, self.retry_after = http_status, retry_after


@dataclass(frozen=True)
class ModelReply:
    text: str  # 모델이 낸 JSON 문자열
    tokens_in: int = 0
    tokens_out: int = 0


class ModelClient(Protocol):
    name: str

    def complete(self, node: str, system: str, payload: dict, schema: dict) -> ModelReply: ...


# ---------------------------------------------------------------- 지시문 (docs/01 "모델 노드의 프롬프트" 초안 + 5절 경계 문장)


# ---------------------------------------------------------------- 입력 (정답 필드는 State에 없고, 여기서도 정해진 필드만 읽는다)


# ---------------------------------------------------------------- 스키마 (docs/01 5절)


def check_schema(value, schema: dict, where: str = "$") -> list[str]:
    """위 두 스키마가 쓰는 키워드만 다루는 작은 검사기(새 의존성 없이). 어긋난 곳 목록을 돌려준다."""
    errors = []
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: 허용 값 밖"]
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind] if kind else []
    ok_type = {
        "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str), "null": lambda v: v is None,
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    }
    if kinds and not any(ok_type[k](value) for k in kinds):
        return [f"{where}: 타입이 {kinds}가 아님"]
    if isinstance(value, dict) and "properties" in schema:
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}.{key}: 없음")
        if schema.get("additionalProperties") is False:
            errors += [f"{where}.{k}: 정의 밖 키" for k in value if k not in schema["properties"]]
        for key, sub in schema["properties"].items():
            if key in value:
                errors += check_schema(value[key], sub, f"{where}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            errors.append(f"{where}: 개수 {len(value)}")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errors.append(f"{where}: 중복")
        for i, v in enumerate(value):
            errors += check_schema(v, schema.get("items", {}), f"{where}[{i}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
            errors.append(f"{where}: 길이 {len(value)}")
    if isinstance(value, int) and not isinstance(value, bool) and value < schema.get("minimum", value):
        errors.append(f"{where}: 최솟값 미만")
    return errors


# ---------------------------------------------------------------- 출력 검증과 변환 (docs/01 5절)


# ---------------------------------------------------------------- 호출과 재시도 (docs/01 3절)


# ---------------------------------------------------------------- 가짜 클라이언트 (시험·평가 실행기용, 네트워크 없음)


class FakeClient:
    """respond(node, payload, schema)가 dict(원시 출력)·str(그대로 text)·ModelError(던짐)를 돌려준다.

    requests에 받은 (node, system, payload, schema)를 남긴다. tokens는 입력·출력 글자 수로 채운다(실제 토큰 아님).
    """

    def __init__(self, respond: Callable[[str, dict, dict], object], name: str = "fake"):
        self.respond, self.name, self.requests = respond, name, []

    def complete(self, node: str, system: str, payload: dict, schema: dict) -> ModelReply:
        self.requests.append((node, system, payload, schema))
        out = self.respond(node, payload, schema)
        if isinstance(out, ModelError):
            raise out
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        return ModelReply(text, len(system) + len(json.dumps(payload, ensure_ascii=False)), len(text))


# ---------------------------------------------------------------- 응답 재사용 (평가 실행기용: 같은 입력이면 다시 부르지 않는다)


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


# ---------------------------------------------------------------- HCX (CLOVA Studio chat-completions v3)


def _schema_for_hcx(schema: dict) -> dict:
    """$id는 응답 형식에 넣지 않는다. 나머지 키워드를 HCX가 다 지키는지는 첫 실제 호출에서 확인한다."""
    return {k: v for k, v in schema.items() if k != "$id"}


def _count(value) -> int:
    """토큰 수. 정수가 아니면 0 (bool 제외)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class HcxClient:
    """HCX-007 구조화 출력. 오류 분류: 429·5xx·시간 초과·연결 오류 → transient, 401·403 → auth,
    응답 잘림·형식 이상 → schema, 그 밖 4xx·서비스 코드 오류 → other. 예외 문장에 키·헤더를 담지 않는다."""

    url = "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-007"
    max_tokens = {"Q6": 1024, "T2": 512}
    token_profiles = {"legacy": {"Q6": 1024, "T2": 512},
                      "q6g_tokens_v2": {"Q6": 1024, "T2": 512, "Q6G": 1536}}

    def __init__(self, key_env: str = "CLOVASTUDIO_API_KEY", min_interval: float = 1.0, timeout: float = 60.0,
                 opener: Callable | None = None, token_version: str = "legacy"):
        if token_version not in self.token_profiles:
            raise ValueError("Unknown HCX token version")
        key = os.environ.get(key_env)
        if not key:
            raise ModelError("auth", f"환경 변수 {key_env} 없음")
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
                raw = resp.read()
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
