"""Regression tests for how claview.b.fetch talks to the real KOSIS
statisticsParameterData.do API about the data cycle (반기/연간/분기/월).

KOSIS uses two different code sets for the two directions:

  request  `prdSe` : Y / H / Q / M  -- claview's internal letters, sent unchanged
  response `PRD_SE`: A / S / Q / M  -- English-initial (Annual/Semiannual/...)

Only Q and M coincide. History (both rounds confirmed against the live API
with scripts/verify_periods_kosis.py and scripts/probe_prdse.py, run by the
user with a real key):

1. 2026-10-02: normalize_rows compared the response's PRD_SE ("S"/"A") to the
   internal "H"/"Y", so every 반기 and every 연간 fetch failed
   unexpected_response_period. Fixed by checking the response against A/S.
2. That fix was also applied to the REQUEST, sending prdSe="S"/"A". It looked
   right on single-cycle tables (DT_105Y001 = 반기 only, DT_301Y015 = 연간
   only), which ignore prdSe, but 2026-10-06 runs on multi-cycle tables
   (DT_303Y001, DT_304Y101, DT_141Y008) got err 30 "데이터가 존재하지
   않습니다" for "S"/"A" and real rows for "H"/"Y". The request goes back to
   the internal letters; only the response check keeps A/S.

See claview/fetch.py's _RESPONSE_PRD_SE comment.
"""

from __future__ import annotations

from claview.b.contracts import StageFailure, Trace
from claview.b.fetch import _RESPONSE_PRD_SE, KosisClient, build_api_request, normalize_rows


def _selection(freq: str, start: str, end: str) -> dict:
    return {
        "series_id": "s1",
        "org_id": "301",
        "table_id": "DT_TEST",
        "item_id": "ITM1",
        "dimensions": {},
        "period": {"frequency": freq, "start": start, "end": end},
        "unit": None,
    }


def test_build_api_request_sends_the_internal_letter_as_prdse_for_every_frequency():
    """KOSIS's request `prdSe` is Y/H/Q/M -- the internal letters, unchanged.
    In particular 반기 must go out as "H" (not the response code "S") and 연간
    as "Y" (not "A"): multi-cycle tables treat prdSe as a real filter and
    answer err 30 to the response-side codes."""
    bounds = {
        "Y": ("2020", "2024"),
        "H": ("202101", "202502"),
        "Q": ("202001", "202004"),
        "M": ("202001", "202012"),
    }
    for freq, (start, end) in bounds.items():
        assert build_api_request(_selection(freq, start, end))["prdSe"] == freq


def test_request_and_response_cycle_codes_differ_for_semiannual_and_annual_only():
    """Pins the asymmetry itself, so nobody "simplifies" the two directions
    back into one mapping again: the code sent for 반기/연간 is not the code
    expected back, while 분기/월 are the same both ways."""
    sent = {
        f: build_api_request(_selection(f, "2021", "2021"))["prdSe"] for f in ("Y", "H", "Q", "M")
    }
    assert _RESPONSE_PRD_SE == {"Y": "A", "H": "S", "Q": "Q", "M": "M"}
    assert {f for f in sent if sent[f] != _RESPONSE_PRD_SE[f]} == {"Y", "H"}


def _row(prd_se: str, prd_de: str, value: str = "1") -> dict:
    return {
        "ORG_ID": "301",
        "TBL_ID": "DT_TEST",
        "ITM_ID": "ITM1",
        "PRD_SE": prd_se,
        "PRD_DE": prd_de,
        "DT": value,
    }


def test_normalize_rows_accepts_the_real_kosis_semiannual_code():
    selection = _selection("H", "202101", "202102")
    rows = [_row("S", "202101"), _row("S", "202102")]
    result, issues = normalize_rows(selection, rows, snapshot="test")
    assert not issues
    assert result["coverage"] == {"expected": 2, "returned": 2, "missing_periods": []}


def test_normalize_rows_accepts_the_real_kosis_annual_code():
    selection = _selection("Y", "2024", "2025")
    rows = [_row("A", "2024"), _row("A", "2025")]
    result, issues = normalize_rows(selection, rows, snapshot="test")
    assert not issues
    assert result["coverage"] == {"expected": 2, "returned": 2, "missing_periods": []}


def test_normalize_rows_still_rejects_the_old_internal_letters_as_wire_codes():
    """Locks in the fix the other direction: KOSIS itself never returns
    PRD_SE=="H" for real 반기 data (it returns "S") or PRD_SE=="Y" for real
    annual data (it returns "A"), so a row claiming either old letter is not
    real KOSIS data and must stay rejected even after the fix -- the bugs
    (wrong request param, wrong response check) must not be replaced by a
    single new bug that just accepts every letter."""
    for freq, wrong_wire_code in [("H", "H"), ("Y", "Y")]:
        selection = _selection(
            freq, "202101" if freq == "H" else "2021", "202101" if freq == "H" else "2021"
        )
        rows = [_row(wrong_wire_code, "202101" if freq == "H" else "2021")]
        try:
            normalize_rows(selection, rows, snapshot="test")
        except StageFailure as exc:
            assert exc.code == "unexpected_response_period", freq
        else:
            raise AssertionError(
                f"expected StageFailure(unexpected_response_period) for freq={freq}"
            )


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttpxClient:
    """Stands in for httpx.Client so KosisClient.fetch's logic can be tested
    without real network access -- just enough surface (get/close) for
    KosisClient to drive."""

    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self._status_code = status_code

    def get(self, url, params):
        return _FakeResponse(self._status_code, self._payload)

    def close(self):
        pass


def test_kosis_client_logs_the_raw_response_on_a_service_error():
    """Reproduces exactly what the user's real run hit: KOSIS returning a
    single error dict (not a list of rows) for a service error (e.g.
    {"err": "30", "errMsg": "데이터가 존재하지 않습니다"}). Before the
    2026-10-02 fix, this information was discarded (raised with no trace of
    what KOSIS actually sent); now it's logged as a data_response_error
    trace event so a caller can tell a real "no data for this selection"
    response apart from a period-handling bug."""
    client = KosisClient(
        "key", client=_FakeHttpxClient({"err": "30", "errMsg": "데이터가 존재하지 않습니다"})
    )
    trace = Trace()
    try:
        client.fetch({"prdSe": "Q"}, trace)
    except StageFailure as exc:
        assert exc.code == "kosis_service_error"
    else:
        raise AssertionError("expected StageFailure(kosis_service_error)")
    logged = [e for e in trace.events if e["event"] == "data_response_error"]
    assert len(logged) == 1
    assert logged[0]["rows"] == {"err": "30", "errMsg": "데이터가 존재하지 않습니다"}


def test_normalize_rows_matches_full_width_kosis_unit():
    """2026-10-16 golden live run: KOSIS returned UNIT_NM "2020＝100" (full-width
    equals) for tables whose metadata unit is "2020=100"; 6 of 22 cases failed
    response_unit_conflict although the rows were fine."""
    selection = {**_selection("Y", "2024", "2025"), "unit": "2020=100"}
    rows = [{**_row("A", p), "UNIT_NM": "2020＝100"} for p in ("2024", "2025")]
    result, issues = normalize_rows(selection, rows, snapshot="test")
    assert not issues and result["unit"] == "2020=100"


def test_normalize_rows_still_rejects_a_different_unit():
    selection = {**_selection("Y", "2024", "2024"), "unit": "2020=100"}
    rows = [{**_row("A", "2024"), "UNIT_NM": "%"}]
    try:
        normalize_rows(selection, rows, snapshot="test")
    except StageFailure as exc:
        assert exc.code == "response_unit_conflict"
    else:
        raise AssertionError("unit conflict was accepted")
