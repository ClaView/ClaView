"""jev requirement-count record (plan 04, 3-1): how many different indicators jev reads in the question.

Recorded only: the router never reads ``jev_record``. Modes:
- ``off`` (default): no record (None).
- ``mock``: a test reply given in the settings (a wire dict, or a function of the request body); no transport.
- ``replay``: the cache folder only (``runs/jev_cache/<sha256 of the request body>.json`` holding ``{"answer":
  {model, choice, probabilities, confidence, usage}}``, checked again on reading); a missing file is recorded as
  ``missing``. No transport.
- ``live``: refused unless the settings carry the explicit ``allow_external`` flag and the key is in the environment
  variable ``KEY_ENV``. The key is read, never stored, printed or logged. Only the checked answer fields are written
  to the cache (``store_answer``), never the raw reply.

Record ``{mode, count, compare, model, usage}`` plus ``choice`` (jev's raw choice), ``ours`` (the count compared
against) and ``fault`` when the reply failed its checks: ``count`` is the
number jev chose ("5+" read as 5, at least five), ``compare`` sets it against the number of different indicators in
the requirements: ``same``, ``jev_less``, ``jev_more`` or ``missing`` (no usable jev number).
The request shape is the one of ``reused/jev_provider.py``; the wording below is ours.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..paths import ROOT
from ..reused import jev_provider
from ..reused.jev_provider import JevFault

MODES = ("off", "mock", "replay", "live")
CACHE_DIR = ROOT / "runs" / "jev_cache"
KEY_ENV = "JEV_API_KEY"
QUESTION_NAME = "indicator_count"
CRITERIA = ("1", "2", "3", "4", "5+")
OPEN_TOP = "5+"
INSTRUCTIONS = (
    "질문에 서로 다른 지표가 몇 개인가? 지표는 질문이 값을 보려는 통계 계열이다. "
    "같은 지표를 기간·주기·기준·대상만 바꿔 다시 말한 것은 하나로 센다. "
    "기간, 단위, 보여 주는 방식(표·그래프·추이)은 지표가 아니다. "
    "지표가 하나도 없거나 0개로 보이면 1을 고른다. 다섯 개 이상이면 5+를 고른다."
)


class JevRefused(PermissionError):
    """Live jev was asked for without the explicit flag or without the key."""


@dataclass(frozen=True)
class JevSettings:
    mode: str = "off"
    model: str | None = None  # pinned jev-X.Y.Z, needed by every mode but off
    allow_external: bool = False  # live only: explicit flag (the CLI's --allow-external)
    mock_reply: dict | Callable[[dict], dict] | None = None
    cache_dir: Path | None = None  # replay/live; default CACHE_DIR
    timeout: float = 30.0
    opener: Any = None  # live only: transport override for tests (default: jev_provider.make_opener())


def settings_from(value: Any) -> JevSettings:
    if value is None:
        return JevSettings()
    if isinstance(value, JevSettings):
        return value
    if isinstance(value, dict):
        return JevSettings(**value)
    raise TypeError("jev settings must be JevSettings, a dict or None")


def request(question: str, model: str) -> tuple[str, dict]:
    return jev_provider.build_request(model, {"question": question}, QUESTION_NAME, INSTRUCTIONS, CRITERIA)


def cache_key(body: dict) -> str:
    return hashlib.sha256(jev_provider.canonical(body).encode("utf-8")).hexdigest()


def read_count(choice: str | None) -> int | None:
    """The jev number; "0" (never offered, kept for safety) is read as 1 and "5+" as 5."""
    if choice is None:
        return None
    if choice == OPEN_TOP:
        return 5
    value = int(choice)
    return max(value, 1)


def compare(jev_count: int | None, choice: str | None, ours: int | None) -> str:
    if jev_count is None or ours is None:
        return "missing"
    if choice == OPEN_TOP and ours >= jev_count:
        return "same"
    if jev_count == ours:
        return "same"
    return "jev_less" if jev_count < ours else "jev_more"


def _record(mode: str, model: str, ours: int | None, answer: dict | None, fault: str | None = None,
            usage: dict | None = None) -> dict:
    choice = answer["choice"] if answer else None
    count = read_count(choice)
    out = {"mode": mode, "count": count, "compare": compare(count, choice, ours), "model": model,
           "usage": answer["usage"] if answer else usage, "choice": choice, "ours": ours}
    if fault:
        out["fault"] = fault
    return out


def _parse(mode: str, model: str, ours: int | None, wire: Any) -> dict:
    try:
        return _record(mode, model, ours, jev_provider.parse_reply(wire, model, QUESTION_NAME, CRITERIA))
    except JevFault as fault:
        return _record(mode, model, ours, None, fault.code, fault.usage)


def recompare(record: dict | None, ours: int | None) -> dict | None:
    """Same jev answer against a new requirement count (no new request: jev reads the question only)."""
    if record is None:
        return None
    out = dict(record)
    out["compare"], out["ours"] = compare(record.get("count"), record.get("choice"), ours), ours
    return out


def record_jev(question: str, ours: int | None, settings: JevSettings | dict | None = None,
               env: dict | None = None) -> dict | None:
    s = settings_from(settings)
    if s.mode not in MODES:
        raise ValueError("unknown jev mode")
    if s.mode == "off":
        return None
    if s.mode == "live":
        # Checked before anything else, so a refused live run builds no request and reads no file.
        if not s.allow_external:
            raise JevRefused("live jev needs the explicit allow_external flag")
        key = (os.environ if env is None else env).get(KEY_ENV)
        if not key:
            raise JevRefused("live jev needs its key in the environment")
    model = jev_provider.check_model(s.model)
    url, body = request(question, model)
    if s.mode == "mock":
        if s.mock_reply is None:
            raise ValueError("mock jev needs mock_reply")
        wire = s.mock_reply(body) if callable(s.mock_reply) else s.mock_reply
        return _parse("mock", model, ours, wire)
    path = (s.cache_dir or CACHE_DIR) / f"{cache_key(body)}.json"
    if s.mode == "replay":
        if not path.is_file():
            return _record("replay", model, ours, None)
        return _replay(path, model, ours)
    try:
        wire = jev_provider.send(url, body, key, s.timeout, opener=s.opener)
        answer = jev_provider.parse_reply(wire, model, QUESTION_NAME, CRITERIA)
    except JevFault as fault:
        return _record("live", model, ours, None, fault.code, fault.usage)
    store_answer(path, answer)
    return _record("live", model, ours, answer)


def store_answer(path: Path, answer: dict) -> None:
    """Cache only the checked answer fields, never the raw reply (it could echo request data)."""
    kept = {name: answer[name] for name in jev_provider.CACHE_FIELDS}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"answer": kept}, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _replay(path: Path, model: str, ours: int | None) -> dict:
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        answer = stored["answer"] if isinstance(stored, dict) else None
    except (OSError, ValueError, KeyError):
        return _record("replay", model, ours, None, "cache_format_error")
    try:
        return _record("replay", model, ours, jev_provider.check_answer(answer, model, CRITERIA))
    except JevFault as fault:
        return _record("replay", model, ours, None, fault.code, fault.usage)