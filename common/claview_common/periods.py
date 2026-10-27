"""Period arithmetic shared by the modules: KOSIS period strings for the four frequencies.

    Y "2024"   H "202401".."202402"   Q "202401".."202404"   M "202401".."202412"

Only arithmetic and formatting: which period a question asks for is decided by the modules.
Errors are `PeriodError` (a ValueError) with a `code`; the messages are the ones C showed before
the move, and B maps the code to its own StageFailure.
"""

from __future__ import annotations

import re

WIDTH = {"Y": 1, "H": 2, "Q": 4, "M": 12}
FREQUENCIES = tuple(WIDTH)
MAX_SPAN = 20000


class PeriodError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def period_index(period: str, frequency: str) -> int:
    """Consecutive number of a period: 2024 -> 2024 (Y), 202401 -> 2024*width + 0 (H, Q, M)."""
    if frequency not in WIDTH or not isinstance(period, str):
        raise PeriodError("invalid_period", "지원하지 않는 주기 또는 시점입니다.")
    if frequency == "Y":
        if not re.fullmatch(r"\d{4}", period):
            raise PeriodError("invalid_period", "연간 시점은 YYYY 형식이어야 합니다.")
        return int(period)
    if not re.fullmatch(r"\d{6}", period) or not 1 <= int(period[4:]) <= WIDTH[frequency]:
        raise PeriodError("invalid_period", f"{frequency} 시점 형식 오류: {period}")
    return int(period[:4]) * WIDTH[frequency] + int(period[4:]) - 1


def periods_between(start: str, end: str, frequency: str) -> list[str]:
    """Every period from start to end, both included (at most MAX_SPAN steps)."""
    first, last = period_index(start, frequency), period_index(end, frequency)
    if first > last or last - first > MAX_SPAN:
        raise PeriodError("period_range", "조회 기간 범위를 확인해 주세요.")
    if frequency == "Y":
        return [str(n) for n in range(first, last + 1)]
    width = WIDTH[frequency]
    return [f"{n // width:04d}{n % width + 1:02d}" for n in range(first, last + 1)]


def period_label(period: str, frequency: str) -> str:
    """Korean label: 2024년, 2024년 상반기, 2024년 1분기, 2024년 1월."""
    period_index(period, frequency)
    if frequency == "Y":
        return f"{period}년"
    sub = int(period[4:])
    if frequency == "H":
        return f"{period[:4]}년 {'상반기' if sub == 1 else '하반기'}"
    return f"{period[:4]}년 {sub}{'분기' if frequency == 'Q' else '월'}"


def normalize_meta_period(value: str, frequency: str) -> str:
    """Period as written in the KOSIS metadata -> period string: "2024.01" (M), "2024 1/4" (Q), "2024 1/2" (H)."""
    if frequency == "M":
        result = value.replace(".", "")
    elif frequency in ("Q", "H"):
        parts = re.fullmatch(r"(\d{4})\s+([1-4])/4" if frequency == "Q" else r"(\d{4})\s+([1-2])/2", value)
        if not parts:
            raise PeriodError("invalid_metadata_period", f"{frequency} 메타 시점 형식 오류: {value}")
        result = parts[1] + "0" + parts[2]
    else:
        result = value
    period_index(result, frequency)
    return result
