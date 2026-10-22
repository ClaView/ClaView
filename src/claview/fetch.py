"""Deterministic KOSIS request construction and response validation."""

from __future__ import annotations

import json
import time
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from .contracts import Config, Json, StageFailure, Trace, outcome
from .metadata import MetadataStore, periods_between
from .stages import canonicalize_selections, selection_key

# claview's internal frequency letters (Y/H/Q/M, used everywhere in
# metadata.py/stages.py for period math, derived from the Korean terms
# 년/반기/분기/월) are what the KOSIS statisticsParameterData.do API wants in
# its REQUEST `prdSe` parameter -- but NOT what it puts in its RESPONSE rows'
# PRD_SE field, which uses English-initial codes (Annual/Semiannual/...): "A"
# for 연간 and "S" for 반기 (Q and M coincide). Two different code sets for
# the two directions:
#
#   request  prdSe : Y / H / Q / M   (= the internal letters, sent unchanged)
#   response PRD_SE: A / S / Q / M
#
# History, because this was got wrong once: 2026-10-02 verify_periods_kosis.py
# showed every 반기/연간 fetch failing unexpected_response_period, because
# normalize_rows compared the response's "S"/"A" to the internal "H"/"Y". The
# fix applied that response mapping to the request as well. That looked right
# on the tables used then (DT_105Y001 declares only 반기, DT_301Y015 only
# 연간 -- a single-cycle table returns its one cycle whatever prdSe says), but
# 2026-10-06 runs against multi-cycle tables (DT_303Y001, DT_304Y101,
# DT_141Y008) returned err 30 "데이터가 존재하지 않습니다" for prdSe="S"/"A"
# and real rows for prdSe="H"/"Y" (scripts/probe_prdse.py, run by the user with
# a real key). So only the response check needs the A/S mapping.
_RESPONSE_PRD_SE = {"Y": "A", "H": "S", "Q": "Q", "M": "M"}


def build_api_request(selection: Json) -> Json:
    return {
        "orgId": selection["org_id"],
        "tblId": selection["table_id"],
        "itmId": selection["item_id"],
        **{axis: value["code"] for axis, value in selection["dimensions"].items()},
        "prdSe": selection["period"]["frequency"],
        "startPrdDe": selection["period"]["start"],
        "endPrdDe": selection["period"]["end"],
    }


def validate_b_result(b_result: Json, store: MetadataStore) -> None:
    try:
        rows = b_result["selections"]
        seen = set()
        ids = set()
        if not rows:
            raise StageFailure("conditions_incomplete", "unresolved")
        for row in rows:
            if set(row) != {
                "series_id",
                "label",
                "org_id",
                "table_id",
                "item_id",
                "dimensions",
                "period",
                "unit",
            }:
                raise StageFailure("invalid_b_result")
            if not isinstance(row["series_id"], str) or not row["series_id"]:
                raise StageFailure("invalid_series_id")
            if row["series_id"] in ids:
                raise StageFailure("duplicate_series_id")
            ids.add(row["series_id"])
            table = store.get(row["org_id"], row["table_id"])
            raw = {
                "selections": [
                    {
                        "item_id": row["item_id"],
                        "dimensions": {a: v["code"] for a, v in row["dimensions"].items()},
                        "period": row["period"],
                    }
                ]
            }
            checked = canonicalize_selections(raw, table, Config())[0]
            if row["dimensions"] != checked["dimensions"] or row["unit"] != checked["unit"]:
                raise StageFailure("metadata_label_or_unit_mismatch")
            key = selection_key(row)
            if key in seen:
                raise StageFailure("duplicate_selection")
            seen.add(key)
    except (KeyError, TypeError, AttributeError):
        raise StageFailure("invalid_b_result") from None


class DataClient(Protocol):
    def fetch(self, params: Json, trace: Trace) -> list[Json]: ...


class KosisClient:
    url = "https://kosis.kr/openapi/Param/statisticsParameterData.do"

    def __init__(self, api_key: str, retries: int = 2, client: httpx.Client | None = None):
        if not api_key:
            raise StageFailure("kosis_key_missing", "unresolved")
        self._key = api_key
        self.retries = retries
        self.client = client or httpx.Client(timeout=45, follow_redirects=False)

    def close(self):
        self.client.close()

    def fetch(self, params: Json, trace: Trace) -> list[Json]:
        trace.add("data_request", params=params)
        for attempt in range(self.retries + 1):
            trace.add("transport_attempt", service="KOSIS", attempt=attempt + 1)
            try:
                response = self.client.get(
                    self.url,
                    params={
                        **params,
                        "method": "getList",
                        "format": "json",
                        "jsonVD": "Y",
                        "apiKey": self._key,
                    },
                )
            except httpx.TransportError:
                if attempt < self.retries:
                    time.sleep(min(2, 0.25 * 2**attempt))
                    continue
                raise StageFailure("kosis_transport_error") from None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self.retries:
                    time.sleep(min(2, 0.25 * 2**attempt))
                    continue
            if response.status_code >= 400:
                raise StageFailure(f"kosis_http_{response.status_code}")
            try:
                rows = response.json()
            except ValueError:
                raise StageFailure("kosis_invalid_response") from None
            # Do not persist a service response that reflects the credential.
            if self._key in json.dumps(rows, ensure_ascii=False):
                trace.add("data_response_redacted", reason="credential_reflected")
                raise StageFailure("kosis_credential_reflected")
            if not isinstance(rows, list) or any(
                not isinstance(r, dict) or "err" in r for r in rows
            ):
                # Logged (not just raised) so a caller inspecting `trace.events`
                # after catching kosis_service_error can see what KOSIS actually
                # sent back (an error payload's "err" code/message, or a response
                # that wasn't the expected list-of-rows shape) instead of just the
                # generic failure code -- added 2026-10-02 while diagnosing
                # verify_periods_kosis.py's real-API runs.
                trace.add("data_response_error", params=params, rows=rows)
                raise StageFailure("kosis_service_error")
            trace.add("data_response", params=params, rows=rows)
            return rows
        raise StageFailure("kosis_transport_error")


def normalize_rows(selection: Json, rows: list[Json], snapshot: str) -> tuple[Json, list[str]]:
    p = selection["period"]
    expected = periods_between(p["start"], p["end"], p["frequency"])
    points = {}
    units = set()
    issues = []
    for row in rows:
        for source, target in [("ORG_ID", "org_id"), ("TBL_ID", "table_id"), ("ITM_ID", "item_id")]:
            if str(row.get(source, "")) != selection[target]:
                raise StageFailure("unexpected_response_series")
        for i in range(1, 9):
            axis = f"objL{i}"
            actual = str(row.get(f"C{i}", "") or "")
            wanted = selection["dimensions"].get(axis, {}).get("code", "")
            if actual != wanted:
                raise StageFailure("unexpected_response_series")
        period = str(row.get("PRD_DE", ""))
        if str(row.get("PRD_SE", "")) != _RESPONSE_PRD_SE[p["frequency"]] or period not in expected:
            raise StageFailure("unexpected_response_period")
        if period in points:
            raise StageFailure("duplicate_response_period")
        unit = row.get("UNIT_NM") or None
        if unit:
            # KOSIS sends full-width forms (e.g. "2020＝100") where metadata has "2020=100".
            units.add(unicodedata.normalize("NFKC", unit))
        raw = row.get("DT")
        if raw is None or str(raw).strip() in {"", "-", "..", "...", "…", "X", "x"}:
            value = None
            status = "missing"
            issues.append("missing_value")
        else:
            try:
                decimal = Decimal(str(raw).replace(",", ""))
                if not decimal.is_finite():
                    raise InvalidOperation
                value = format(decimal, "f")
                status = "observed"
            except InvalidOperation:
                value = None
                status = "parse_error"
                issues.append("value_parse_error")
        points[period] = {
            "period": period,
            "raw_value": raw,
            "value": value,
            "value_status": status,
        }
    if len(units) > 1 or (
        units
        and selection["unit"] is not None
        and unicodedata.normalize("NFKC", selection["unit"]) not in units
    ):
        raise StageFailure("response_unit_conflict")
    missing = [x for x in expected if x not in points]
    if missing:
        issues.append("missing_periods")
    result = {
        "series_id": selection["series_id"],
        "selection": selection,
        "frequency": p["frequency"],
        "unit": next(iter(units)) if units else selection["unit"],
        "points": [points[x] for x in expected if x in points],
        "coverage": {
            "expected": len(expected),
            "returned": len(points),
            "missing_periods": missing,
        },
        "provenance": {
            "params": build_api_request(selection),
            "metadata_snapshot": snapshot,
            "fetched_at": datetime.now(UTC).isoformat(),
        },
    }
    return result, sorted(set(issues))


def fetch_data(b_result: Json, store: MetadataStore, client: DataClient, trace: Trace) -> Json:
    series = []
    issues = []
    try:
        validate_b_result(b_result, store)
        for selection in b_result["selections"]:
            rows = client.fetch(build_api_request(selection), trace)
            result, problems = normalize_rows(selection, rows, store.snapshot)
            result["provenance"]["raw_response_ref"] = trace.run_id
            series.append(result)
            for code in problems:
                issues.append(
                    {
                        "code": code,
                        "message": code,
                        "field": None,
                        "series_id": selection["series_id"],
                        "retryable": False,
                    }
                )
        status = (
            "no_data" if all(not x["points"] for x in series) else "partial" if issues else "ok"
        )
        return {
            "stage": "fetch",
            "status": status,
            "result": {
                "series": series,
                **(
                    {"period_notices": b_result["period_notices"]}
                    if b_result.get("period_notices")
                    else {}
                ),
            },
            "issues": issues,
            "trace_ref": trace.run_id,
        }
    except StageFailure as exc:
        result = outcome("fetch", {"series": series} if series else None, exc, trace.run_id)
        return result
