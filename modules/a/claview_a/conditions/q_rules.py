"""Q1/Q3 rule functions copied from the old A pipeline (commit 55fd41b), unchanged except one rule change below.

Changed for the new A (plan 36, user 2026-10-28 14:59 UTC; partly changes the Q3 decision of 2026-10-13): a bare
"월말" is the monthly frequency and "연말"/"년말" the yearly one, with the end point (``anchor="end"``) left by
``frequency_anchor`` as for "분기말"/"반기말". A word joined to 말잔 ("월말잔액") and a word after a digit or another
Hangul letter ("7월말", "2024년 말", "작년 말", "전월말") are not; "월평균" stays not a frequency.

Scope of the copy (the helpers Q3 needs, decided in batch A of plan 02):
- ``nospace`` (catalog.py) for Q1.
- Q3 frequency rules: ``_END_REPEAT``, ``_END_EACH``, ``FREQUENCY_PATTERNS``, ``FREQ_END``, ``FREQ_END_AFTER``,
  ``frequency_anchor``, ``_COUNTED``, ``_COUNT_WORDS``, ``_MONTHS_FREQ``, ``_FREQ_NOT_AFTER``, ``_counted_value``,
  ``find_frequencies`` (rules.py:13-92).
- ``find_frequencies`` drops words inside period spans, so ``find_periods`` and its pattern/helper set come too
  (rules.py:95-273), with ``stamp_months`` (rules.py:564-583) that it calls. ``find_periods`` is copied whole:
  its date resolution (``_year``/``_bounds``/``_shift_year``/``_stamp``/``stamp_months``) is inline in the same
  function, and cutting it out would change the rule function. Q3 only uses the spans (``i``, ``j``) with
  ``as_of=None``; the rewritten Q2 must not rely on the resolved dates.
Not copied: Q4 basis/handoff/unit rules, Q5 list rules, Q7 core text, J3 period comparison.
Comments inside copied code are the original Korean comments.
"""

from __future__ import annotations
import re


# Source: src/claview/aflow/catalog.py:23-24 (commit 55fd41b), copied verbatim.
def nospace(text: str) -> str:
    return re.sub(r"\s+", "", text)


# (정규식, 값). 숫자 바로 뒤의 "년간"(3년간)·"분기"(1분기)·"상/하반기"는 기간이지 주기 요청이 아니다.
# 반복어(매달·해마다·분기마다·각 월)와 단위어(월 단위로·연 주기로)도 주기 요청이다.
# 평균·점(분기평균·분기점)은 계산 방식이나 다른 말이라 주기가 아니다. 월평균은 Q4 기준어·B 계산 몫.
# 기간 끝 반복(월말마다·분기말별로·매 반기말·각 연말)은 네 단위를 같은 규칙으로 잡고, q3가 끝 시점(anchor="end")을
# 남긴다 (2026-10-13 Q4 마무리, Q3·Q4 공통). 반복어 없는 "분기말"·"반기말"은 주기 이름(분기·반기)이 있어 그 주기와
# 끝 시점이다. 반복어 없는 "월말"·"연말"·"년말"도 그 주기와 끝 시점으로 읽는다(계획 36, 2026-10-13 Q3 결정 일부 변경).
# 말잔으로 이어지는 말(월말잔액)과 숫자·다른 글자 뒤의 말(7월말·2024년 말·작년 말·전월말)은 주기가 아니다
_END_REPEAT = r"\s*말\s*(?:마다|별로?|단위로)"
_END_EACH = r"(?:매|각)\s*"
_END_BARE = r"\s*말(?!잔)"
FREQUENCY_PATTERNS: tuple[tuple[str, str], ...] = (
    (rf"(?<![0-9가-힣])(?:연|년)" + _END_REPEAT + rf"|{_END_EACH}(?:연|년|해)\s*말|(?<![0-9가-힣])[연년]{_END_BARE}"
     r"|연간|(?:연도|년도)\s*(?:별|마다)|(?<![가-힣0-9])[연년해]\s*별|매\s*(?:연도|년도|년|해)|해\s*마다"
     r"|(?<![가-힣0-9])년\s*마다|각\s*(?:연도|년도)|(?<![가-힣0-9])(?:연도|년도|[연년])\s*단위로|[연년]단위"
     r"|(?<![가-힣0-9])[연년]\s*주기|(?<![0-9수몇])년간", "Y"),
    (r"(?<![상하0-9])반기" + _END_REPEAT + rf"|{_END_EACH}반기\s*말"
     r"|반기\s*(?:별|마다)|매\s*반기|각\s*반기|반\s*년\s*마다|반기\s*단위|반기\s*주기|(?<![상하0-9])반기(?!\s*평균)", "H"),
    (r"(?<![0-9전당])분기" + _END_REPEAT + rf"|{_END_EACH}분기\s*말"
     r"|분기\s*(?:별|마다)|매\s*분기|각\s*분기|쿼터\s*(?:별|마다)|분기\s*주기"
     r"|(?<![0-9전당])분기(?!점|\s*평균)", "Q"),
    (r"(?<![0-9])월" + _END_REPEAT + rf"|{_END_EACH}(?:월|달)\s*말|(?<![0-9가-힣])월{_END_BARE}"
     r"|월별|(?<![0-9])월\s+별|달\s*별|(?<!개)월간|매\s*월(?!요)|매\s*달(?!러)|(?<![0-9])(?<!개)(?:달|월)\s*마다|각\s*(?:월|달)(?![가-힣])"
     r"|다달이|달달이|(?<![0-9])월\s*단위로|월단위|(?<![0-9])월\s*주기", "M"),
)
# 주기 말 안이나 바로 뒤의 "말": 기간 끝이다 (q3 anchor). "분기말"은 주기 말이 "분기"라 뒤를 본다
FREQ_END = re.compile(r"말")
FREQ_END_AFTER = re.compile(r"\s*말(?![가-힣]*(?:씀|하))")


# Source: src/claview/aflow/rules.py:42-44 (commit 55fd41b), copied verbatim.
def frequency_anchor(query: str, i: int, j: int) -> str | None:
    """주기 말이 기간 끝을 가리키면 "end"."""
    return "end" if FREQ_END.search(query[i:j]) or FREQ_END_AFTER.match(query, j) else None

# Source: src/claview/aflow/rules.py:46-56 (commit 55fd41b), counted-frequency patterns copied verbatim.
# "N개월마다·N달 단위로·N년 주기로"는 N으로 주기를 정한다. 지원하지 않는 N(2개월마다)은 주기를 비우고 안쪽 말(월마다)도 막는다.
_COUNTED = re.compile(
    r"(?P<n>\d+|한|일|두|이|세|석|삼|네|넉|사|다섯|오|여섯|육|열두|십이)\s*(?P<u>개월|달|년|해)"
    r"\s*(?:마다|단위로?|주기로?|간격으로?)"
)
_COUNT_WORDS = {"한": 1, "일": 1, "두": 2, "이": 2, "세": 3, "석": 3, "삼": 3, "네": 4, "넉": 4, "사": 4,
                "다섯": 5, "오": 5, "여섯": 6, "육": 6, "열두": 12, "십이": 12}
_MONTHS_FREQ = {1: "M", 3: "Q", 6: "H", 12: "Y"}

# 바로 앞에 오면 그 단위는 한 시점이나 비교 기준이다 (직전 분기보다, 같은 분기, 동분기, 당분기).
_FREQ_NOT_AFTER = re.compile(r"(?:직전|지난|이번|저번|다음|같은|동|전|당)\s*$")


# Source: src/claview/aflow/rules.py:59-63 (commit 55fd41b), copied verbatim.
def _counted_value(m) -> str | None:
    n = m["n"]
    n = int(n) if n.isdigit() else _COUNT_WORDS[n]
    months = n * 12 if m["u"] in ("년", "해") else n
    return _MONTHS_FREQ.get(months)


# Source: src/claview/aflow/rules.py:66-92 (commit 55fd41b), copied verbatim.
def find_frequencies(query: str) -> list[tuple[str, int, int]]:
    """(값, 시작, 끝) 목록. 원문 순서.

    Q2 기간 안에 든 말(최근 6개월간, 지난 분기)과 시점·비교를 가리키는 앞말 뒤의 단위는 주기가 아니다.
    겹치면 긴 말이 이긴다 (6개월마다는 반기지 월마다가 아니다).
    """
    periods = [(p["i"], p["j"]) for p in find_periods(query, None)]
    found: list[tuple[str | None, int, int]] = []
    for m in _COUNTED.finditer(query):
        if m.start() and re.match(r"[0-9가-힣]", query[m.start() - 1]) and not m["n"].isdigit():
            continue  # "통일 년"처럼 낱말 안의 수사는 수가 아니다
        found.append((_counted_value(m), m.start(), m.end()))
    for pattern, value in FREQUENCY_PATTERNS:
        for m in re.finditer(pattern, query):
            found.append((value, m.start(), m.end()))
    kept: list[tuple[str | None, int, int]] = []
    for value, i, j in sorted(found, key=lambda x: (x[1] - x[2], x[1])):
        if not any(i < kj and ki < j for _, ki, kj in kept):
            kept.append((value, i, j))
    rows = []
    for value, i, j in sorted(kept, key=lambda x: x[1]):
        if value is None or any(i < pj and pi < j for pi, pj in periods):
            continue
        if _FREQ_NOT_AFTER.search(query[max(0, i - 4):i]):
            continue
        rows.append((value, i, j))
    return rows


# 연도 자리에는 숫자 연도나 연도 별칭(올해·작년 등)이 온다. 별칭은 as_of의 연도로만 푼다.
# as_of의 달·분기에 기대는 말(지난달·지난 분기·최근 N개월)은 구간만 잡고 unresolved로 둔다. 월·분기와 공표 지연은 D7
_ALIAS = r"재작년|작년|지난해(?!외)|올해|금년"
_ALIAS_YEARS = {"재작년": -2, "작년": -1, "지난해": -1, "올해": 0, "금년": 0}


# Source: src/claview/aflow/rules.py:103-104 (commit 55fd41b), copied verbatim.
def _yr(n: int, suffix: str = r"\s*년?") -> str:
    return rf"(?:(?P<y{n}>\d{{4}}){suffix}|(?P<a{n}>{_ALIAS}))"


# Source: src/claview/aflow/rules.py:107-110 (commit 55fd41b), copied verbatim.
def _sub(n: int) -> str:
    # 분기·월 뒤의 "말"(분기말·7월말)은 구간에 넣되 "말잔"의 "말"은 남긴다
    return (rf"(?:\s*(?:(?P<q{n}>\d)\s*분기|(?P<m{n}>\d{{1,2}})\s*월(?!\d)|(?P<h{n}>[상하])\s*반기)"
            rf"(?:\s*말(?!잔))?)")


_SEP = r"\s*(?:부터|에서|~|-|–)\s*"
# 끝 연도를 생략한 범위("2026년 1월부터 6월까지")는 끝 단위가 시작 단위와 같아야 하고 "까지"로 닫혀야 한다
_RANGE = re.compile(_yr(1) + _sub(1) + "?" + _SEP
                    + rf"(?:{_yr(2)}{_sub(2)}?|(?P<omit>{_sub(3)})(?=\s*까지))" + r"\s*(?:까지)?")
_SINCE = re.compile(_yr(1) + _sub(1) + r"?\s*(?:이후|부터|이래)")
# "최근 8개 분기"/"최근 2개 반기" are the same request as "최근 8분기"/"최근 2반기" (user 2026-10-29 07:51 UTC)
_RECENT = re.compile(r"(?:최근|지난)\s*(\d{1,2})\s*(년|개월|달|개\s*분기|분기|개\s*반기|반기)(?:\s*(?:간|동안))?")
_RECENT_UNIT = {"년": "year", "개월": "month", "달": "month", "분기": "quarter", "반기": "half",
                "개분기": "quarter", "개반기": "half"}
# 연도 별칭 시점: 올해 2분기, 작년 하반기, 작년 말, 작년 한 해, 올해 들어(→ 이후)
_REL = re.compile(rf"(?P<a1>{_ALIAS}){_sub(1)}?(?:\s*(?P<eoy>말)(?!잔)|\s*(?P<whole>한\s*해)|\s*(?P<into>들어))?"
                  r"|(?P<olinto>올\s+들어)")
# as_of의 달·분기에 기대는 말
_REL_MQ = re.compile(r"(?:지난|이번|저번)\s*(?:달|분기)")
_POINT = re.compile(_yr(1, r"\s*년").replace(f"|(?P<a1>{_ALIAS})", "") + _sub(1) + r"?(?P<month_span>\s*한\s*달\s*(?:간|동안)?)?")
# 바로 뒤에 오면 비교 기준(Q4 몫)이지 조회 기간이 아니다
_COMPARE_AFTER = re.compile(r"(?:\s*말)?\s*(?:보다|대비|에\s*비(?:해|하여|하면)|(?:와|과)\s*비교|"
                            r"같은\s*(?:달|월|분기|반기|기간|시기|해)|동월|동기|동분기)")


# Source: src/claview/aflow/rules.py:131-136 (commit 55fd41b), copied verbatim.
def _stamp(year: str, quarter: str | None, month: str | None) -> str:
    if quarter:
        return f"{year}-Q{quarter}"
    if month:
        return f"{year}-{int(month):02d}"
    return year


# Source: src/claview/aflow/rules.py:139-140 (commit 55fd41b), copied verbatim.
def _shift_year(as_of: str, years: int) -> str:
    return str(int(as_of[:4]) - years + 1)


# Source: src/claview/aflow/rules.py:143-149 (commit 55fd41b), copied verbatim.
def _year(m, n: int, as_of: str | None) -> str | None:
    """n번 연도 자리의 값. 별칭은 as_of가 없으면 None."""
    if m.groupdict().get(f"y{n}"):
        return m.group(f"y{n}")
    if not as_of:
        return None
    return str(int(as_of[:4]) + _ALIAS_YEARS[m.group(f"a{n}")])


# Source: src/claview/aflow/rules.py:152-161 (commit 55fd41b), copied verbatim.
def _unit(m, n: int) -> tuple[str, str | None] | None:
    """n번 하위 단위의 종류와 값. 없으면 ("Y", None). 범위 밖 값(5분기·13월)이면 None."""
    q, mo, h = m.group(f"q{n}"), m.group(f"m{n}"), m.group(f"h{n}")
    if q:
        return ("Q", q) if 1 <= int(q) <= 4 else None
    if mo:
        return ("M", mo) if 1 <= int(mo) <= 12 else None
    if h:
        return "H", h
    return "Y", None


# Source: src/claview/aflow/rules.py:164-173 (commit 55fd41b), copied verbatim.
def _bounds(year: str, unit: tuple[str, str | None]) -> tuple[str, str]:
    """한 단위의 처음·끝 표기. 반기는 달 구간이다(point라도 start≠end)."""
    kind, value = unit
    if kind == "Q":
        return (_stamp(year, value, None),) * 2
    if kind == "M":
        return (_stamp(year, None, value),) * 2
    if kind == "H":
        return (f"{year}-01", f"{year}-06") if value == "상" else (f"{year}-07", f"{year}-12")
    return year, year


# Source: src/claview/aflow/rules.py:176-177 (commit 55fd41b), copied verbatim.
def _is_relative(m, *ns: int) -> bool:
    return any(m.groupdict().get(f"a{n}") for n in ns)


# Source: src/claview/aflow/rules.py:180-273 (commit 55fd41b), copied verbatim.
def find_periods(query: str, as_of: str | None) -> list[dict]:
    """{kind, start, end, status, i, j[, amount, unit, relative]} 목록.

    겹치면 앞선 규칙(범위 → 이후 → 최근 → 연도 별칭 → 달·분기 상대 → 시점)이 이긴다.
    relative=true는 as_of에 기대는 표현이라는 표시다. as_of가 없거나 달·분기 단위라 풀지 못하면 start/end=None, unresolved다.
    """
    taken: list[tuple[int, int]] = []
    out = []

    def free(m) -> bool:
        return all(m.end() <= a or m.start() >= b for a, b in taken)

    def compared(m) -> bool:
        return bool(_COMPARE_AFTER.match(query, m.end()))

    def add(m, kind, start, end, *, relative=False, **extra):
        taken.append(m.span())
        resolved = start is not None
        row = {"kind": kind, "start": start, "end": end, "status": "explicit" if resolved else "unresolved",
               "i": m.start(), "j": m.end(), **extra}
        if not resolved:
            row.update(start=None, end=None)
        if relative:
            row["relative"] = True
        out.append(row)

    for m in _RANGE.finditer(query):
        if not free(m):
            continue
        relative = _is_relative(m, 1, 2)
        if relative and compared(m):
            taken.append(m.span())
            continue
        y1, u1 = _year(m, 1, as_of), _unit(m, 1)
        if m.group("omit"):
            y2, u2 = y1, _unit(m, 3)
            if u1 is None or u2 is None or u1[0] != u2[0] or u1[0] == "Y":
                continue  # 끝 단위가 시작과 다르면 범위로 보지 않는다
        else:
            y2, u2 = _year(m, 2, as_of), _unit(m, 2)
        start = end = None
        if y1 and y2 and u1 and u2:
            start, end = _bounds(y1, u1)[0], _bounds(y2, u2)[1]
            if m.group("omit") and stamp_months(start)[0] > stamp_months(end)[1]:
                start = end = None  # "12월부터 2월까지"의 연도 넘김은 추측하지 않는다
        add(m, "range", start, end, relative=relative)
    for m in _SINCE.finditer(query):
        if not free(m):
            continue
        relative = _is_relative(m, 1)
        if relative and compared(m):
            taken.append(m.span())
            continue
        y1, u1 = _year(m, 1, as_of), _unit(m, 1)
        add(m, "since", _bounds(y1, u1)[0] if y1 and u1 else None, None, relative=relative)
    for m in _RECENT.finditer(query):
        if free(m):
            amount, unit = int(m.group(1)), _RECENT_UNIT[re.sub(r"\s+", "", m.group(2))]
            start = end = None
            if as_of and unit == "year":  # 연 단위만 푼다. 월·분기·반기와 공표 지연 규칙은 D7
                start, end = _shift_year(as_of, amount), as_of[:4]
            add(m, "recent", start, end, relative=True, amount=amount, unit=unit)
    for m in _REL.finditer(query):
        if not free(m):
            continue
        if compared(m):
            taken.append(m.span())
            continue
        if m.group("olinto"):
            add(m, "since", as_of[:4] if as_of else None, None, relative=True)
            continue
        y1, u1 = _year(m, 1, as_of), _unit(m, 1)
        if m.group("into"):
            add(m, "since", _bounds(y1, u1)[0] if y1 and u1 else None, None, relative=True)
        elif m.group("a1") in ("올해", "금년") and u1 == ("Y", None) and not m.group("eoy") and not m.group("whole"):
            add(m, "since", y1, None, relative=True)  # 올해 = 올해 1월부터, 끝 열림(기존 open_end 계약)
        elif m.group("eoy") and u1 == ("Y", None):
            add(m, "point", f"{y1}-12" if y1 else None, f"{y1}-12" if y1 else None, relative=True)
        else:
            bounds = _bounds(y1, u1) if y1 and u1 else (None, None)
            add(m, "point", *bounds, relative=True)
    for m in _REL_MQ.finditer(query):
        if not free(m):
            continue
        if compared(m):
            taken.append(m.span())
            continue
        add(m, "point", None, None, relative=True)
    for m in _POINT.finditer(query):
        if free(m):
            u1 = _unit(m, 1)
            bounds = _bounds(m.group("y1"), u1) if u1 else (None, None)
            add(m, "point", *bounds)
    return sorted(out, key=lambda x: x["i"])


# Source: src/claview/aflow/rules.py:564-583 (commit 55fd41b), copied verbatim.
def stamp_months(stamp: str | None) -> tuple[int, int] | None:
    """요청 표기(YYYY, YYYY-Qn, YYYY-MM)나 메타 표기(1970, 1960 1/4, 2002 1/2, 1970.01, API의 197001)를 달 구간으로."""
    if not stamp:
        return None
    s = stamp.strip()
    if m := re.fullmatch(r"(\d{4})", s):
        y = int(m.group(1)) * 12
        return y, y + 11
    if m := re.fullmatch(r"(\d{4})-Q([1-4])", s) or re.fullmatch(r"(\d{4})\s+([1-4])/4", s):
        y = int(m.group(1)) * 12 + 3 * (int(m.group(2)) - 1)
        return y, y + 2
    if m := re.fullmatch(r"(\d{4})\s+([12])/2", s):
        y = int(m.group(1)) * 12 + 6 * (int(m.group(2)) - 1)
        return y, y + 5
    if m := re.fullmatch(r"(\d{4})[-.](\d{1,2})", s) or re.fullmatch(r"(\d{4})(\d{2})", s):
        if not 1 <= int(m.group(2)) <= 12:  # 00월·13월은 읽지 않는다 (Codex F04)
            return None
        y = int(m.group(1)) * 12 + int(m.group(2)) - 1
        return y, y
    return None
