"""jev record (plan 04, 3-1): mock replies give the three comparisons, replay without cache records missing, live is
refused without the explicit flag or the key. No transport is ever opened (the guard would fail the test)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from claview_a_legacy.reused import jev_provider
from claview_a_legacy.verify import jev
from claview_a_legacy.verify.jev import CRITERIA, QUESTION_NAME, JevRefused, JevSettings, record_jev

MODEL = "jev-1.0.0"
Q = "가상 질문: 갑지표와 을지표를 비교해줘"


def wire(choice: str, model: str = MODEL, changes: dict | None = None) -> dict:
    probs = {c: (0.6 if c == choice else 0.1) for c in CRITERIA}
    answer = {"type": "choice", "choice": choice, "probabilities": probs, "confidence": 0.7}
    answer.update(changes or {})
    return {"model": model, "answers": {QUESTION_NAME: answer}, "usage": {"input_tokens": 120, "output_tokens": 3}}


def mock(reply) -> JevSettings:
    return JevSettings(mode="mock", model=MODEL, mock_reply=reply)


@pytest.mark.parametrize("choice, ours, expected", [("2", 2, "same"), ("1", 2, "jev_less"), ("3", 2, "jev_more"),
                                                    ("5+", 6, "same"), ("5+", 4, "jev_more")])
def test_mock_comparisons(choice, ours, expected):
    record = record_jev(Q, ours, mock(wire(choice)))
    assert record["mode"] == "mock" and record["model"] == MODEL
    assert record["compare"] == expected
    assert record["count"] == (5 if choice == "5+" else int(choice))
    assert record["usage"] == {"input_tokens": 120, "output_tokens": 3}
    assert {"mode", "count", "compare", "model", "usage"} <= set(record)


def test_mock_reply_sees_the_request_body_of_the_copied_shape():
    seen = []

    def reply(body):
        seen.append(body)
        return wire("1")

    record_jev(Q, 1, mock(reply))
    body = seen[0]
    assert set(body) == {"model", "state", "questions"}
    assert body["model"] == MODEL and body["state"] == {"question": Q}
    question = body["questions"][QUESTION_NAME]
    assert set(body["questions"]) == {QUESTION_NAME}
    assert question["type"] == "choice" and question["criteria"] == ["1", "2", "3", "4", "5+"]
    assert "서로 다른 지표" in question["instructions"]


def test_off_records_nothing():
    assert record_jev(Q, 2, None) is None
    assert record_jev(Q, 2, {"mode": "off"}) is None


@pytest.mark.parametrize("bad, code", [
    (wire("2", changes={"probabilities": {"1": 0.5, "2": 0.6, "3": 0.0, "4": 0.0, "5+": 0.0}}), "format_error"),
    (wire("2", changes={"choice": "0"}), "format_error"),
    (wire("2", model="jev-9.9.9"), "model_version_mismatch"),
    ({"model": MODEL}, "format_error"),
])
def test_bad_replies_are_recorded_as_missing(bad, code):
    record = record_jev(Q, 2, mock(bad))
    assert record["compare"] == "missing" and record["count"] is None and record["fault"] == code


def test_unpinned_model_is_refused():
    with pytest.raises(ValueError):
        record_jev(Q, 2, JevSettings(mode="mock", model="jev-latest", mock_reply=wire("2")))


def test_replay_without_cache_is_missing(tmp_path):
    record = record_jev(Q, 2, JevSettings(mode="replay", model=MODEL, cache_dir=tmp_path / "jev_cache"))
    assert record == {"mode": "replay", "count": None, "compare": "missing", "model": MODEL, "usage": None,
                      "choice": None, "ours": 2}


def _cache_path(tmp_path, question=Q):
    _, body = jev.request(question, MODEL)
    return tmp_path / f"{jev.cache_key(body)}.json"


def test_replay_reads_the_cache_file_of_the_request(tmp_path):
    answer = jev_provider.parse_reply(wire("3"), MODEL, QUESTION_NAME, CRITERIA)
    jev.store_answer(_cache_path(tmp_path), answer)
    record = record_jev(Q, 2, JevSettings(mode="replay", model=MODEL, cache_dir=tmp_path))
    assert record["mode"] == "replay" and record["compare"] == "jev_more"
    other = record_jev(Q + " 다른", 2, JevSettings(mode="replay", model=MODEL, cache_dir=tmp_path))
    assert other["compare"] == "missing"


def test_live_needs_the_flag_and_the_key(tmp_path):
    with pytest.raises(JevRefused):
        record_jev(Q, 2, JevSettings(mode="live", model=MODEL, cache_dir=tmp_path), env={jev.KEY_ENV: "x"})
    with pytest.raises(JevRefused):
        record_jev(Q, 2, JevSettings(mode="live", model=MODEL, allow_external=True, cache_dir=tmp_path), env={})
    assert list(tmp_path.iterdir()) == []


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        record_jev(Q, 2, {"mode": "sometimes", "model": MODEL})


def test_recompare_keeps_the_answer():
    record = record_jev(Q, 2, mock(wire("2")))
    again = jev.recompare(record, 3)
    assert again["compare"] == "jev_less" and again["count"] == 2 and again["ours"] == 3
    assert jev.recompare(None, 3) is None


def test_provider_preflight_limit():
    with pytest.raises(jev_provider.JevFault) as error:
        jev_provider.build_request(MODEL, {"question": "가" * 40000}, QUESTION_NAME, "x", CRITERIA)
    assert error.value.code == "input_preflight_limit"


# ---------------------------------------------------------------- transport safety (no network: fake openers only)

KEY = "test-key-0000/abcdef"  # made-up value in the allowed key format; "/" is escaped as "\/" by some encoders


class _Response:
    def __init__(self, status, text):
        self.status, self._raw = status, text.encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.requests = reply, error, []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.reply


def _fault(opener, key=KEY):
    with pytest.raises(jev_provider.JevFault) as caught:
        jev_provider.send(jev_provider.URL, {"model": MODEL}, key, 1.0, opener=opener)
    error = caught.value
    assert key not in str(error) and error.__cause__ is None and error.__context__ is None
    return error.code


def test_default_opener_follows_no_redirect_and_no_environment_proxy():
    opener = jev_provider.make_opener()
    redirects = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert len(redirects) == 1 and isinstance(redirects[0], jev_provider._NoRedirect)
    assert redirects[0].redirect_request(None, None, 302, "Found", {}, "https://other.example/") is None
    proxies = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert all(h.proxies == {} for h in proxies)  # the default environment ProxyHandler is replaced


@pytest.mark.parametrize("code", [301, 302, 307, 308])
def test_redirect_is_a_fixed_fault(code):
    error = urllib.error.HTTPError("https://other.example/?k=" + KEY, code, "Moved " + KEY, {}, None)
    assert _fault(_Opener(error=error)) == "redirect_refused"


@pytest.mark.parametrize("bad", ["short", "has space-in-the-key", "line\nbreak-in-key", "tab\tin-the-key-x", "", None])
def test_key_format_is_checked_before_any_header(bad):
    opener = _Opener(reply=_Response(200, "{}"))
    with pytest.raises(jev_provider.JevFault) as caught:
        jev_provider.send(jev_provider.URL, {"model": MODEL}, bad, 1.0, opener=opener)
    assert caught.value.code == "invalid_key_format" and opener.requests == []
    assert not bad or bad not in str(caught.value)


@pytest.mark.parametrize("error, code", [
    (ValueError("Invalid header value b'Bearer " + KEY + "'"), "transport_error"),
    (urllib.error.URLError("refused " + KEY), "transport_error"),
    (OSError("socket " + KEY), "transport_error"),
    (TimeoutError("timed out " + KEY), "timeout"),
    (urllib.error.HTTPError("u", 429, "Too many " + KEY, {}, None), "quota"),
    (urllib.error.HTTPError("u", 500, "Server " + KEY, {}, None), "provider_http_error"),
])
def test_every_transport_error_is_a_fixed_code_without_the_key(error, code):
    assert _fault(_Opener(error=error)) == code


def test_sent_request_carries_the_key_only_in_its_header():
    opener = _Opener(reply=_Response(200, json.dumps(wire("2"))))
    jev_provider.send(jev_provider.URL, {"model": MODEL}, KEY, 1.0, opener=opener)
    request = opener.requests[0]
    assert request.get_header("Authorization") == "Bearer " + KEY and KEY not in request.data.decode("utf-8")


@pytest.mark.parametrize("text", [
    '{"model": "jev-1.0.0", "echo": "' + KEY.replace("/", "\\/") + '"}',
    '{"model": "jev-1.0.0", "echo": "\\u0074' + KEY[1:] + '"}',
    '{"model": "jev-1.0.0", "\\u0074' + KEY[1:] + '": 1}',
    '{"model": "jev-1.0.0", "echo": "' + KEY + '"}',
])
def test_a_reply_holding_the_key_is_refused_even_when_escaped(text):
    assert _fault(_Opener(reply=_Response(200, text))) == "unsafe_provider_response"


def test_non_200_and_bad_json_are_fixed_codes():
    assert _fault(_Opener(reply=_Response(500, "{}"))) == "provider_http_error"
    assert _fault(_Opener(reply=_Response(200, "not json"))) == "format_error"
    assert _fault(_Opener(reply=_Response(200, "[1, 2]"))) == "format_error"


def test_live_caches_only_the_checked_answer(tmp_path):
    reply = {**wire("2"), "debug": "raw reply detail"}
    reply["answers"][QUESTION_NAME]["note"] = "extra"
    settings = JevSettings(mode="live", model=MODEL, allow_external=True, cache_dir=tmp_path,
                           opener=_Opener(reply=_Response(200, json.dumps(reply))))
    record = record_jev(Q, 2, settings, env={jev.KEY_ENV: KEY})
    assert record["mode"] == "live" and record["compare"] == "same"
    stored = json.loads(_cache_path(tmp_path).read_text(encoding="utf-8"))
    assert set(stored) == {"answer"} and set(stored["answer"]) == set(jev_provider.CACHE_FIELDS)
    assert "debug" not in json.dumps(stored) and "note" not in json.dumps(stored)
    replay = record_jev(Q, 2, JevSettings(mode="replay", model=MODEL, cache_dir=tmp_path))
    assert (replay["count"], replay["compare"], replay["mode"]) == (2, "same", "replay")


def test_live_reply_that_fails_its_checks_is_not_cached(tmp_path):
    bad = wire("2", changes={"choice": "9"})
    settings = JevSettings(mode="live", model=MODEL, allow_external=True, cache_dir=tmp_path,
                           opener=_Opener(reply=_Response(200, json.dumps(bad))))
    record = record_jev(Q, 2, settings, env={jev.KEY_ENV: KEY})
    assert record["fault"] == "format_error" and list(tmp_path.iterdir()) == []


def test_replay_checks_the_cached_answer_again(tmp_path):
    path = _cache_path(tmp_path)
    path.write_text(json.dumps({"answer": {"model": MODEL, "choice": "2", "probabilities": {"2": 1.0},
                                           "confidence": 0.5, "usage": None}}), encoding="utf-8")
    assert record_jev(Q, 2, JevSettings(mode="replay", model=MODEL, cache_dir=tmp_path))["fault"] == "format_error"
    path.write_text("not json", encoding="utf-8")
    assert record_jev(Q, 2, JevSettings(mode="replay", model=MODEL,
                                        cache_dir=tmp_path))["fault"] == "cache_format_error"