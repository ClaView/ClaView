"""claview_common.periods gives the same results and failures as the B and C functions it replaced.

The `old_b_*` and `old_c_*` functions are frozen copies of the code before the move (B
claview_b/metadata.py, C claview_c/c_renderer.py at commit 059cdc1). They are the reference only.
"""

from __future__ import annotations

import re

import pytest

from claview_common import periods

# ---------------------------------------------------------------- frozen references (do not edit)

OLD_WIDTHS = {"H": 2, "Q": 4, "M": 12}
OLD_WIDTH = {"Y": 1, "H": 2, "Q": 4, "M": 12}


class OldStageFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def old_b_period_number(value, frequency):
    patterns = {"Y": r"\d{4}", "H": r"\d{4}0[1-2]", "Q": r"\d{4}0[1-4]", "M": r"\d{4}(0[1-9]|1[0-2])"}
    if not isinstance(value, str) or frequency not in patterns or not re.fullmatch(patterns[frequency], value):
        raise OldStageFailure("invalid_period")
    year = int(value[:4])
    return year if frequency == "Y" else year * OLD_WIDTHS[frequency] + int(value[4:]) - 1


def old_b_periods_between(start, end, frequency):
    a, b = old_b_period_number(start, frequency), old_b_period_number(end, frequency)
    if a > b or b - a > 20000:
        raise OldStageFailure("invalid_period")
    if frequency == "Y":
        return [str(n) for n in range(a, b + 1)]
    width = OLD_WIDTHS[frequency]
    return [f"{n // width:04d}{n % width + 1:02d}" for n in range(a, b + 1)]


def old_b_normalize_period(value, frequency):
    if frequency == "M":
        result = value.replace(".", "")
    elif frequency == "Q":
        match = re.fullmatch(r"(\d{4})\s+([1-4])/4", value)
        if not match:
            raise OldStageFailure("invalid_metadata_period")
        result = match[1] + "0" + match[2]
    elif frequency == "H":
        match = re.fullmatch(r"(\d{4})\s+([1-2])/2", value)
        if not match:
            raise OldStageFailure("invalid_metadata_period")
        result = match[1] + "0" + match[2]
    else:
        result = value
    old_b_period_number(result, frequency)
    return result


def old_c_period_index(period, frequency):
    if frequency not in OLD_WIDTH or not isinstance(period, str):
        raise ValueError("지원하지 않는 주기 또는 시점입니다.")
    if frequency == "Y":
        if not re.fullmatch(r"\d{4}", period):
            raise ValueError("연간 시점은 YYYY 형식이어야 합니다.")
        return int(period)
    if not re.fullmatch(r"\d{6}", period) or not 1 <= int(period[4:]) <= OLD_WIDTH[frequency]:
        raise ValueError(f"{frequency} 시점 형식 오류: {period}")
    return int(period[:4]) * OLD_WIDTH[frequency] + int(period[4:]) - 1


def old_c_periods_between(start, end, frequency):
    first, last = old_c_period_index(start, frequency), old_c_period_index(end, frequency)
    if first > last or last - first > 20000:
        raise ValueError("조회 기간 범위를 확인해 주세요.")
    if frequency == "Y":
        return [str(n) for n in range(first, last + 1)]
    width = OLD_WIDTH[frequency]
    return [f"{n // width:04d}{n % width + 1:02d}" for n in range(first, last + 1)]


def old_c_period_label(period, frequency):
    old_c_period_index(period, frequency)
    if frequency == "Y":
        return f"{period}년"
    sub = int(period[4:])
    if frequency == "H":
        return f"{period[:4]}년 {'상반기' if sub == 1 else '하반기'}"
    return f"{period[:4]}년 {sub}{'분기' if frequency == 'Q' else '월'}"


# ---------------------------------------------------------------- comparison helpers


def outcome(fn, *args):
    """(result, None) or (None, error class name, message or code)."""
    try:
        return ("ok", fn(*args))
    except OldStageFailure as exc:
        return ("b_error", exc.code)
    except periods.PeriodError as exc:
        return ("error", exc.code, str(exc))
    except ValueError as exc:
        return ("error", str(exc))


def as_b(fn, *args):
    """New function seen through B's adapter rule: any PeriodError becomes its code."""
    try:
        return ("ok", fn(*args))
    except periods.PeriodError as exc:
        code = "invalid_metadata_period" if exc.code == "invalid_metadata_period" else "invalid_period"
        return ("b_error", code)


def as_c(fn, *args):
    try:
        return ("ok", fn(*args))
    except ValueError as exc:
        return ("error", str(exc))


FREQS = ["Y", "H", "Q", "M", "D", "", None]
YEARS = ["1900", "1999", "2024", "2100", "0000", "9999"]
SUBS = ["00", "01", "02", "03", "04", "05", "12", "13", "1", "001", "ab"]
ODD = ["", " ", "2024 ", "２０２４", "202401 ", "2024-01", "20241", "2024011", None, 2024]


def period_inputs():
    values = list(YEARS) + [y + s for y in YEARS for s in SUBS] + ODD
    return [(v, f) for v in values for f in FREQS]


@pytest.mark.parametrize(("value", "freq"), period_inputs())
def test_period_index_matches_b_and_c(value, freq):
    assert as_b(periods.period_index, value, freq) == outcome(old_b_period_number, value, freq)
    assert as_c(periods.period_index, value, freq) == outcome(old_c_period_index, value, freq)


@pytest.mark.parametrize(("value", "freq"), period_inputs())
def test_period_label_matches_c(value, freq):
    assert as_c(periods.period_label, value, freq) == outcome(old_c_period_label, value, freq)


RANGES = [
    ("2015", "2025", "Y"),
    ("2025", "2015", "Y"),
    ("2024", "2024", "Y"),
    ("1900", "9999", "Y"),
    ("202401", "202512", "M"),
    ("202312", "202401", "M"),
    ("202401", "202313", "M"),
    ("200001", "202404", "Q"),
    ("202402", "202401", "Q"),
    ("202001", "202402", "H"),
    ("190001", "999912", "M"),
    ("2024", "202401", "M"),
    ("202401", "202402", "Y"),
    ("202401", "202402", "D"),
]


@pytest.mark.parametrize(("start", "end", "freq"), RANGES)
def test_periods_between_matches_b_and_c(start, end, freq):
    assert as_b(periods.periods_between, start, end, freq) == outcome(old_b_periods_between, start, end, freq)
    assert as_c(periods.periods_between, start, end, freq) == outcome(old_c_periods_between, start, end, freq)


META = [
    ("2024.01", "M"),
    ("2024.12", "M"),
    ("2024.13", "M"),
    ("202401", "M"),
    ("2024.1", "M"),
    ("2024 1/4", "Q"),
    ("2024 4/4", "Q"),
    ("2024 5/4", "Q"),
    ("2024  2/4", "Q"),
    ("2024 1/2", "Q"),
    ("2024 1/2", "H"),
    ("2024 2/2", "H"),
    ("2024 3/2", "H"),
    ("2024", "Y"),
    ("24", "Y"),
    ("2024", "D"),
]


@pytest.mark.parametrize(("value", "freq"), META)
def test_normalize_meta_period_matches_b(value, freq):
    assert as_b(periods.normalize_meta_period, value, freq) == outcome(old_b_normalize_period, value, freq)


def test_error_is_a_value_error_with_code():
    with pytest.raises(ValueError) as caught:
        periods.periods_between("2025", "2015", "Y")
    assert caught.value.code == "period_range"
