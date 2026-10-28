# ruff: noqa: E501  (user-facing Korean text: ruff counts full-width characters as two columns)
"""Align combined series (units, frequency, period) before they are drawn together.

Input and output are the `data_result` of `docs/contracts/조회_결과.md`
(`{"series": [...], "relations": [...], ...}`). The functions here are pure and
deterministic: no model, no network, no clock. They never change `raw_value` and they
never change the series' `selection`; whatever they change is recorded in the new
`alignment` block and in `source_*` fields on the series, so the original numbers can
always be recovered.

What is aligned, and what is deliberately not:

* Same currency, different scale (십억원 / 백만원): converted exactly (Decimal) to the
  scale most series already use.
* Different currency (원 / 달러): NOT converted. There is no exchange-rate series in the
  catalog, and a rate would have to be chosen (average or end of period). The two units
  are put on separate axes and the notice says so. (Phase 2.)
* Different kind of unit (금액 / % / 지수): never converted; separate axes.
* Different period spans: cut to the common span; the cut is listed in the notices.
* Different frequency (월 / 분기): never converted silently. `align` returns
  `status="needs_choice"` with the options; the caller asks the user and calls `align`
  again with the chosen `frequency_policy`.
* Derived values (growth rates computed from amounts) are not made here. (Phase 2.)

At most two axes are offered (`alignment["axes"]`). With three or more units, or with
frequencies that stay different, `axes` is None and `axes_reason` says why.
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation, localcontext

from .metadata import periods_between

Json = dict

VERSION = "align/1"
FREQ_RANK = {"Y": 0, "H": 1, "Q": 2, "M": 3}  # coarse -> fine
SUBPERIODS = {"Y": 1, "H": 2, "Q": 4, "M": 12}  # sub-periods per year
FREQ_NAME = {"Y": "연간", "H": "반기", "Q": "분기", "M": "월"}

FREQUENCY_POLICIES = ("freq_mean", "freq_last", "freq_sum", "freq_none")
METHOD_OF = {"freq_mean": "mean", "freq_last": "last", "freq_sum": "sum"}
METHOD_LABEL = {"mean": "기간 평균", "last": "기간 마지막 시점 값(기말)", "sum": "기간 합계"}

SCALES = {
    "": 10**0,
    "천": 10**3,
    "만": 10**4,
    "십만": 10**5,
    "백만": 10**6,
    "천만": 10**7,
    "억": 10**8,
    "십억": 10**9,
    "백억": 10**10,
    "천억": 10**11,
    "조": 10**12,
}
CURRENCIES = {"원": "KRW", "달러": "USD"}
_MONEY = re.compile(r"(조|천억|백억|십억|억|천만|백만|십만|만|천)?\s*(원|달러)")
_INDEX = re.compile(r"\d{4}\s*=\s*\d+")
# "연리%"/"연%" are the units KOSIS uses for interest rates (52 + 2 items in the catalog).
_RATE = {"%", "%p", "％", "연리%", "연%"}
# Balance (stock) indicators: adding the balances of several months/quarters counts the same
# stock again, so a period sum is not offered for them. KOSIS gives no flow/stock flag; the
# item name is the signal (all 112 money-unit stock tables of the catalog carry one of these
# words in their item name). 신규/증감/거래 words mark a flow even when a balance word is present.
_BALANCE = re.compile(
    "잔액|말잔|평잔|잔고|보유|가계신용|가계대출|대출금|국제투자대조표|대외채권|대외채무|재무상태표"
)
_FLOW_WORD = re.compile("신규|증감|순발행|순취득|거래")


class AlignmentError(ValueError):
    """The requested alignment cannot be applied without inventing data."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---- units -----------------------------------------------------------------------------
def parse_unit(unit) -> Json:
    """Split a unit string into kind / currency / scale. Unknown strings stay unknown."""
    if unit is None:
        return {"unit": None, "kind": "unknown", "currency": None, "scale": None}
    text = unicodedata.normalize("NFKC", str(unit)).strip()
    match = _MONEY.fullmatch(text)
    if match:
        return {
            "unit": text,
            "kind": "money",
            "currency": CURRENCIES[match.group(2)],
            "scale": SCALES[match.group(1) or ""],
        }
    if text in _RATE:
        return {"unit": text, "kind": "rate", "currency": None, "scale": None}
    if _INDEX.search(text):
        return {"unit": text, "kind": "index", "currency": None, "scale": None}
    return {"unit": text or None, "kind": "other", "currency": None, "scale": None}


# ---- number formatting ------------------------------------------------------------------
def _decimal(value) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite():
        raise InvalidOperation
    return number


def _format(number: Decimal, *, exact: bool = False) -> str:
    """Decimal text for a computed value.

    Rounded to 6 decimals, but never so far that a tiny value turns into 0: a non-zero value
    keeps at least 6 significant digits (4E-7 stays "0.0000004"). `exact=True` skips rounding,
    for results that are exact anyway (a power-of-ten rescale of the source digits).
    """
    with localcontext() as context:
        context.prec = 40
        if not exact and number != number.to_integral():
            number = number.quantize(Decimal(1).scaleb(min(-6, number.adjusted() - 5)))
        text = format(number.normalize(), "f")
    return "0" if text in {"-0", ""} else text


# ---- period arithmetic --------------------------------------------------------------------
def bucket(period: str, source: str, target: str) -> str:
    """The `target`-frequency period that contains the `source`-frequency `period`."""
    if source == target:
        return period
    year = int(period[:4])
    if target == "Y":
        return f"{year:04d}"
    sub = 1 if source == "Y" else int(period[4:])
    width = SUBPERIODS[source] // SUBPERIODS[target]
    return f"{year:04d}{(sub - 1) // width + 1:02d}"


def _span(series: Json) -> Json:
    return series.get("aligned_period") or series["selection"]["period"]


def _label(series: Json) -> str:
    return series["selection"].get("label") or series["series_id"]


def is_balance(series: Json) -> bool:
    """True when the series is (probably) a balance whose values must not be added up over time."""
    selection = series["selection"]
    names = [d.get("name") or "" for d in (selection.get("dimensions") or {}).values()]
    text = " ".join([selection.get("label") or "", *names])
    return bool(_BALANCE.search(text)) and not _FLOW_WORD.search(text)


def _notice(code: str, message: str, series_ids=()) -> Json:
    return {"code": code, "message": message, "series_ids": list(series_ids)}


# ---- frequency conversion -----------------------------------------------------------------
def _convert_frequency(series: Json, target: str, method: str) -> tuple[Json, Json]:
    """Down-convert one series (M->Q, Q->Y, ...). Returns (new series, report)."""
    source = series["frequency"]
    span = _span(series)
    fine = periods_between(span["start"], span["end"], source)
    in_span = set(fine)
    members: dict[str, list[str]] = {}
    for period in fine:
        members.setdefault(bucket(period, source, target), []).append(period)
    per_bucket = SUBPERIODS[source] // SUBPERIODS[target]
    dropped = [b for b, m in members.items() if len(m) < per_bucket]
    buckets = [b for b, m in members.items() if len(m) == per_bucket]
    if not buckets:
        raise AlignmentError(
            "alignment_period_too_short",
            f"{_label(series)}: 조회 기간이 {FREQ_NAME[target]} 한 기간보다 짧아 주기를 맞출 수 없습니다.",
        )
    by_period = {p["period"]: p for p in series["points"]}
    points, incomplete = [], []
    for name in buckets:
        rows = [by_period.get(p) for p in members[name]]
        if all(r is None for r in rows):
            continue
        observed = [r for r in rows if r and r["value_status"] == "observed"]
        if len(observed) != per_bucket:
            incomplete.append(name)
            points.append(
                {"period": name, "raw_value": None, "value": None, "value_status": "missing"}
            )
            continue
        numbers = [_decimal(r["value"]) for r in observed]
        if method == "sum":
            value = sum(numbers, Decimal(0))
        elif method == "last":
            value = numbers[-1]
        else:
            with localcontext() as context:
                context.prec = 40
                value = sum(numbers, Decimal(0)) / Decimal(per_bucket)
        points.append(
            {"period": name, "raw_value": None, "value": _format(value), "value_status": "observed"}
        )
    converted = copy.deepcopy(series)
    converted["source_frequency"] = source
    converted["source_points"] = copy.deepcopy(series["points"])
    converted["frequency"] = target
    converted["aligned_period"] = {"frequency": target, "start": buckets[0], "end": buckets[-1]}
    converted["points"] = points
    got = {p["period"] for p in points}
    converted["coverage"] = {
        "expected": len(buckets),
        "returned": len(points),
        "missing_periods": [b for b in buckets if b not in got],
    }
    report = {
        "series_id": series["series_id"],
        "from": source,
        "to": target,
        "method": method,
        "dropped_buckets": dropped,
        "incomplete_buckets": incomplete,
        "outside_span": sorted(set(by_period) - in_span),
    }
    return converted, report


def _convert_period(period: Json | None, target: str) -> Json | None:
    """requested_period in the target frequency (None if it cannot be expressed)."""
    if not period:
        return None
    frequency = period.get("frequency")
    if frequency == target:
        return copy.deepcopy(period)
    if frequency not in FREQ_RANK or FREQ_RANK[frequency] < FREQ_RANK[target]:
        return None
    return {
        "frequency": target,
        "start": bucket(period["start"], frequency, target),
        "end": bucket(period["end"], frequency, target),
    }


# ---- unit conversion ----------------------------------------------------------------------
def _convert_units(series_list: list[Json], notices: list, conversions: list) -> None:
    parsed = {s["series_id"]: parse_unit(s.get("unit")) for s in series_list}
    money = [s for s in series_list if parsed[s["series_id"]]["kind"] == "money"]
    by_currency: dict[str, list[Json]] = {}
    for s in money:
        by_currency.setdefault(parsed[s["series_id"]]["currency"], []).append(s)
    for members in by_currency.values():
        scales = [parsed[s["series_id"]]["scale"] for s in members]
        if len(set(scales)) < 2:
            continue
        # target: the scale most series already use; ties go to the first series.
        target = max(dict.fromkeys(scales), key=lambda x: (scales.count(x), -scales.index(x)))
        target_unit = next(
            parsed[s["series_id"]]["unit"]
            for s in members
            if parsed[s["series_id"]]["scale"] == target
        )
        for s in members:
            info = parsed[s["series_id"]]
            if info["scale"] == target:
                continue
            factor = Decimal(info["scale"]) / Decimal(target)
            for point in s["points"]:
                if point["value_status"] == "observed":
                    point["value"] = _format(_decimal(point["value"]) * factor, exact=True)
            s["source_unit"] = s.get("unit")
            s["unit"] = target_unit
            conversions.append(
                {
                    "series_id": s["series_id"],
                    "from_unit": info["unit"],
                    "to_unit": target_unit,
                    "factor": _format(factor, exact=True),
                }
            )
        names = ", ".join(_label(s) for s in members if parsed[s["series_id"]]["scale"] != target)
        notices.append(
            _notice(
                "unit_scale_converted",
                f"단위 배율을 {target_unit}로 맞췄습니다({names}). 같은 통화의 배율만 환산했으며 원래 값은 보존했습니다.",
                [s["series_id"] for s in members if parsed[s["series_id"]]["scale"] != target],
            )
        )
    currencies = sorted({parsed[s["series_id"]]["currency"] for s in money})
    if len(currencies) > 1:
        units = " · ".join(dict.fromkeys(parsed[s["series_id"]]["unit"] for s in money))
        notices.append(
            _notice(
                "currency_not_converted",
                f"통화가 다른 지표({units})는 환율로 환산하지 않았습니다. 서로 다른 단위로 따로 표시하며 값의 크기를 직접 비교할 수 없습니다.",
                [s["series_id"] for s in money],
            )
        )


# ---- common period -------------------------------------------------------------------------
def _common_period(result: Json, series_list: list[Json], notices: list) -> Json | None:
    frequencies = {s["frequency"] for s in series_list}
    if len(frequencies) != 1:
        return None
    frequency = next(iter(frequencies))
    spans = [_span(s) for s in series_list]
    start, end = max(x["start"] for x in spans), min(x["end"] for x in spans)
    if start > end:
        notices.append(
            _notice(
                "no_common_period",
                "함께 요청한 지표의 조회 기간이 서로 겹치지 않아 기간을 맞추지 못했습니다. 각 지표의 원래 기간을 그대로 표시합니다.",
                [s["series_id"] for s in series_list],
            )
        )
        return None
    trimmed = []
    for s, span in zip(series_list, spans, strict=True):
        if span["start"] == start and span["end"] == end:
            continue
        s["aligned_period"] = {"frequency": frequency, "start": start, "end": end}
        s["points"] = [p for p in s["points"] if start <= p["period"] <= end]
        expected = periods_between(start, end, frequency)
        got = {p["period"] for p in s["points"]}
        s["coverage"] = {
            "expected": len(expected),
            "returned": len(s["points"]),
            "missing_periods": [p for p in expected if p not in got],
        }
        trimmed.append(f"{_label(s)}({span['start']}~{span['end']})")
    if trimmed:
        notices.append(
            _notice(
                "common_period_applied",
                f"지표마다 조회 기간이 달라 공통 기간 {start}~{end}로 맞췄습니다. 기간이 달랐던 지표: {', '.join(trimmed)}.",
                [s["series_id"] for s in series_list],
            )
        )
    requested = result.get("requested_period")
    if requested and requested.get("frequency") == frequency:
        new_start, new_end = max(requested["start"], start), min(requested["end"], end)
        if new_start > new_end:
            result["requested_period"] = None
            notices.append(
                _notice(
                    "requested_period_outside_common",
                    "요청한 표시 기간이 공통 기간 밖이라 공통 기간 전체를 표시합니다.",
                )
            )
        else:
            result["requested_period"] = {**requested, "start": new_start, "end": new_end}
    return {"frequency": frequency, "start": start, "end": end}


# ---- axes ----------------------------------------------------------------------------------
def _axes(series_list: list[Json], notices: list) -> tuple[list | None, str | None]:
    if len({s["frequency"] for s in series_list}) != 1:
        return None, "mixed_frequency"
    units: dict = {}
    for s in series_list:
        info = parse_unit(s.get("unit"))
        entry = units.setdefault(info["unit"], {"info": info, "ids": []})
        entry["ids"].append(s["series_id"])
    if len(units) > 2:
        return None, "too_many_units"
    ordered = sorted(units.items(), key=lambda kv: 0 if kv[1]["info"]["kind"] == "money" else 1)
    axes = []
    for index, (unit, entry) in enumerate(ordered):
        axes.append(
            {
                "axis_id": f"A{index + 1}",
                "side": "left" if index == 0 else "right",
                "unit": unit,
                "kind": entry["info"]["kind"],
                "series_ids": entry["ids"],
                "draw": "line",
            }
        )
    if (
        len(axes) == 2
        and axes[0]["kind"] == "money"
        and axes[1]["kind"] in {"rate", "index", "other"}
    ):
        axes[0]["draw"] = "column"
    if len(axes) == 2:
        names = " · ".join(str(a["unit"] or "단위 미제공") for a in axes)
        notices.append(
            _notice(
                "dual_axis",
                f"단위가 서로 다른 지표({names})를 왼쪽·오른쪽 축에 나눠 그렸습니다. 축 눈금이 달라 선·막대의 높이를 직접 비교할 수 없습니다.",
                [i for a in axes for i in a["series_ids"]],
            )
        )
    return axes, None


# ---- public API ----------------------------------------------------------------------------
def frequency_choice(series_list: list[Json]) -> Json | None:
    """The question to ask when frequencies differ; None when they already agree."""
    frequencies = {s["frequency"] for s in series_list}
    if len(frequencies) < 2:
        return None
    target = min(frequencies, key=FREQ_RANK.__getitem__)
    movers = [s for s in series_list if s["frequency"] != target]
    money_only = all(parse_unit(s.get("unit"))["kind"] == "money" for s in movers)
    balances = [s for s in movers if is_balance(s)]
    additive = money_only and not balances
    name = FREQ_NAME[target]
    options = [
        {
            "option_id": "freq_mean",
            "label": f"{name} 기준으로 맞추기 · 더 짧은 주기의 값은 기간 평균",
        },
        {
            "option_id": "freq_last",
            "label": f"{name} 기준으로 맞추기 · 더 짧은 주기의 값은 기간 마지막 시점 값(기말)",
        },
    ]
    if additive:
        options.append(
            {
                "option_id": "freq_sum",
                "label": f"{name} 기준으로 맞추기 · 더 짧은 주기의 값은 기간 합계",
            }
        )
    options.append({"option_id": "freq_none", "label": "맞추지 않고 주기별로 따로 보기"})
    parts = ", ".join(f"{_label(s)}({FREQ_NAME[s['frequency']]})" for s in series_list)
    text = f"함께 요청한 지표의 주기가 서로 다릅니다: {parts}. 어떻게 맞출까요? 맞추면 값이 바뀌므로 방법을 골라 주세요(원래 값은 보존됩니다)."
    if money_only and balances:
        names = ", ".join(_label(s) for s in balances)
        text += f" 잔액 지표({names})는 기간 합계로 바꾸면 같은 잔액을 여러 번 더하게 되어 합계 선택지를 제공하지 않습니다."
    return {
        "question_id": "frequency_alignment",
        "text": text,
        "target_frequency": target,
        "series": [
            {"series_id": s["series_id"], "label": _label(s), "frequency": s["frequency"]}
            for s in series_list
        ],
        "options": options,
    }


def align(data_result: Json, *, frequency_policy: str | None = None) -> Json:
    """Return an aligned copy of `data_result` with an `alignment` block.

    `frequency_policy` is one of FREQUENCY_POLICIES and only matters when the series have
    different frequencies. Without it the result has `alignment["status"] == "needs_choice"`
    and no series are changed.
    """
    if frequency_policy is not None and frequency_policy not in FREQUENCY_POLICIES:
        raise AlignmentError("alignment_policy_invalid", "지원하지 않는 주기 맞춤 방법입니다.")
    result = copy.deepcopy(data_result)
    series_list = result.get("series") or []
    notices: list[Json] = []
    alignment: Json = {
        "version": VERSION,
        "status": "unchanged",
        "frequency": None,
        "frequency_policy": None,
        "frequency_conversions": [],
        "unit_conversions": [],
        "common_period": None,
        "axes": None,
        "axes_reason": None,
        "notices": notices,
    }
    result["alignment"] = alignment
    if len(series_list) < 2:
        alignment["axes_reason"] = "single_series"
        return result

    choice = frequency_choice(series_list)
    if choice is not None:
        if frequency_policy is None:
            alignment["status"] = "needs_choice"
            alignment["choice"] = choice
            return result
        alignment["frequency_policy"] = frequency_policy
        if frequency_policy == "freq_none":
            result["requested_period"] = None
            notices.append(
                _notice(
                    "frequency_not_aligned",
                    "주기가 다른 지표를 맞추지 않고 주기별로 따로 표시합니다. 표시 기간은 조회된 전체 기간입니다.",
                    [s["series_id"] for s in series_list],
                )
            )
        else:
            method = METHOD_OF[frequency_policy]
            allowed = {o["option_id"] for o in choice["options"]}
            if frequency_policy not in allowed:
                raise AlignmentError(
                    "alignment_policy_not_allowed",
                    "금액이 아닌 지표(비율·지수 등)와 잔액 지표는 기간 합계로 바꿀 수 없습니다.",
                )
            target = choice["target_frequency"]
            reports = []
            for index, s in enumerate(series_list):
                if s["frequency"] != target:
                    series_list[index], report = _convert_frequency(s, target, method)
                    reports.append(report)
            alignment["frequency_conversions"] = reports
            result["requested_period"] = _convert_period(
                data_result.get("requested_period"), target
            )
            by_id = {s["series_id"]: s for s in series_list}
            moved = ", ".join(_label(by_id[r["series_id"]]) for r in reports)
            note = f"주기를 {FREQ_NAME[target]}로 맞췄습니다. {moved}의 값은 {METHOD_LABEL[method]}입니다. 기간 안의 값이 하나라도 없으면 그 기간 값은 비워 둡니다. 원래 값은 보존했습니다."
            cut = sorted({b for r in reports for b in r["dropped_buckets"]})
            if cut:
                note += f" 일부만 포함된 기간({', '.join(cut)})은 제외했습니다."
            notices.append(_notice("frequency_converted", note, [r["series_id"] for r in reports]))
            for r in reports:
                if r["incomplete_buckets"]:
                    notices.append(
                        _notice(
                            "frequency_bucket_incomplete",
                            f"{_label(by_id[r['series_id']])}: 값이 빠진 기간이 있어 {len(r['incomplete_buckets'])}개 {FREQ_NAME[target]} 값을 비웠습니다.",
                            [r["series_id"]],
                        )
                    )
        alignment["status"] = "aligned"

    before = len(alignment["unit_conversions"])
    _convert_units(series_list, notices, alignment["unit_conversions"])
    if len(alignment["unit_conversions"]) > before:
        alignment["status"] = "aligned"
    alignment["common_period"] = _common_period(result, series_list, notices)
    if alignment["common_period"] and any("common_period_applied" == n["code"] for n in notices):
        alignment["status"] = "aligned"
    frequencies = {s["frequency"] for s in series_list}
    alignment["frequency"] = next(iter(frequencies)) if len(frequencies) == 1 else None
    alignment["axes"], alignment["axes_reason"] = _axes(series_list, notices)
    if notices:
        result["period_notices"] = [
            *(result.get("period_notices") or []),
            *copy.deepcopy(notices),
        ]
    return result


# ---- combining requirements ----------------------------------------------------------------
def _key(series: Json) -> str:
    selection = series["selection"]
    dims = {k: v.get("code") for k, v in (selection.get("dimensions") or {}).items()}
    return json.dumps(
        [
            selection.get("org_id"),
            selection.get("table_id"),
            selection.get("item_id"),
            dims,
            selection.get("period"),
        ],
        sort_keys=True,
        ensure_ascii=False,
    )


def combine_results(parts: list[Json]) -> Json:
    """Merge several `data_result`s (one per requirement) into one, for a shared chart.

    Series ids are prefixed (`R1-S001`) so they cannot collide; identical selections are
    kept once. `requested_period` survives only when all parts agree.
    """
    if not parts:
        raise AlignmentError("combine_empty", "합칠 조회 결과가 없습니다.")
    if len(parts) == 1:
        return copy.deepcopy(parts[0])
    merged: Json = {"series": [], "relations": [], "period_notices": [], "user_periods": []}
    kept: dict[str, str] = {}  # selection key -> merged series id of the copy that was kept
    renamed: dict[tuple[int, str], str] = {}
    related: set[tuple[str, str]] = set()
    for number, part in enumerate(parts, 1):
        for series in part.get("series") or []:
            key = _key(series)
            if key in kept:
                # a duplicate is not copied, but relations that name it follow the kept copy
                renamed[(number, series["series_id"])] = kept[key]
                continue
            series = copy.deepcopy(series)
            new_id = f"R{number}-{series['series_id']}"
            kept[key] = new_id
            renamed[(number, series["series_id"])] = new_id
            series["series_id"] = new_id
            series["selection"]["series_id"] = new_id
            merged["series"].append(series)
        for relation in part.get("relations") or []:
            ancestor = renamed.get((number, relation.get("ancestor_series_id")))
            descendant = renamed.get((number, relation.get("descendant_series_id")))
            if ancestor and descendant and ancestor != descendant and (ancestor, descendant) not in related:
                related.add((ancestor, descendant))
                merged["relations"].append(
                    {
                        **copy.deepcopy(relation),
                        "ancestor_series_id": ancestor,
                        "descendant_series_id": descendant,
                    }
                )
        merged["period_notices"].extend(copy.deepcopy(part.get("period_notices") or []))
        merged["user_periods"].extend(copy.deepcopy(part.get("user_periods") or []))
    requested = [p.get("requested_period") for p in parts]
    if all(r == requested[0] for r in requested) and requested[0] is not None:
        merged["requested_period"] = copy.deepcopy(requested[0])
    elif any(requested):
        merged["period_notices"].append(
            _notice(
                "requested_period_not_shared",
                "함께 표시하는 지표의 표시 기간이 서로 달라 조회된 기간을 기준으로 표시합니다.",
            )
        )
    # The official total fetched for a composition (M2) belongs to the whole result: keep the
    # first one found, or else the first failure note. C checks it against the merged series.
    for field in ("composition_total", "composition_error"):
        found = next((p[field] for p in parts if p.get(field)), None)
        if found is not None and "composition_total" not in merged:
            merged[field] = copy.deepcopy(found)
    if any(p.get("_synthetic") for p in parts):
        merged["_synthetic"] = True
    return merged
