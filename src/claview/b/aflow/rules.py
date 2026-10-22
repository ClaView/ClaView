"""단계 1 규칙: 기간(Q2)·주기(Q3) 추출과 검색식 핵심어(Q7).

모두 정리본(Q1 query) 위의 정규식이다. 위치는 정리본 색인으로 돌려주고, 노드가 offsets로 원문 Span으로 바꾼다.
사전과 표현 목록은 언어 규칙으로만 정했다. 골든 22건에 맞춰 고치지 않는다 (docs/01 7절 층 분리).
"""

from __future__ import annotations

import re

from .catalog import nospace

# ---------------------------------------------------------------- Q3 주기 (v1.8 R5)

# (정규식, 값). 숫자 바로 뒤의 "년간"(3년간)·"분기"(1분기)·"상/하반기"는 기간이지 주기 요청이 아니다.
# 반복어(매달·해마다·분기마다·각 월)와 단위어(월 단위로·연 주기로)도 주기 요청이다.
# 평균·점(분기평균·분기점)은 계산 방식이나 다른 말이라 주기가 아니다. 월말·연말·월평균은 Q4 기준어·B 계산 몫.
# 기간 끝 반복(월말마다·분기말별로·매 반기말·각 연말)은 네 단위를 같은 규칙으로 잡고, q3가 끝 시점(anchor="end")을
# 남긴다 (2026-10-13 Q4 마무리, Q3·Q4 공통). 반복어 없는 말은 지금 판정을 그대로 둔다: "분기말"·"반기말"은 주기 이름
# (분기·반기)이 있어 그 주기와 끝 시점, "월말"·"연말"은 계산 방식이라 주기가 아니다(Q3 결정)
_END_REPEAT = r"\s*말\s*(?:마다|별로?|단위로)"
_END_EACH = r"(?:매|각)\s*"
FREQUENCY_PATTERNS: tuple[tuple[str, str], ...] = (
    (rf"(?<![0-9가-힣])(?:연|년)" + _END_REPEAT + rf"|{_END_EACH}(?:연|년|해)\s*말"
     r"|연간|(?:연도|년도)\s*(?:별|마다)|(?<![가-힣0-9])[연년해]\s*별|매\s*(?:연도|년도|년|해)|해\s*마다"
     r"|(?<![가-힣0-9])년\s*마다|각\s*(?:연도|년도)|(?<![가-힣0-9])(?:연도|년도|[연년])\s*단위로|[연년]단위"
     r"|(?<![가-힣0-9])[연년]\s*주기|(?<![0-9수몇])년간", "Y"),
    (r"(?<![상하0-9])반기" + _END_REPEAT + rf"|{_END_EACH}반기\s*말"
     r"|반기\s*(?:별|마다)|매\s*반기|각\s*반기|반\s*년\s*마다|반기\s*단위|반기\s*주기|(?<![상하0-9])반기(?!\s*평균)", "H"),
    (r"(?<![0-9전당])분기" + _END_REPEAT + rf"|{_END_EACH}분기\s*말"
     r"|분기\s*(?:별|마다)|매\s*분기|각\s*분기|쿼터\s*(?:별|마다)|분기\s*주기"
     r"|(?<![0-9전당])분기(?!점|\s*평균)", "Q"),
    (r"(?<![0-9])월" + _END_REPEAT + rf"|{_END_EACH}(?:월|달)\s*말"
     r"|월별|(?<![0-9])월\s+별|달\s*별|(?<!개)월간|매\s*월(?!요)|매\s*달(?!러)|(?<![0-9])(?<!개)(?:달|월)\s*마다|각\s*(?:월|달)(?![가-힣])"
     r"|다달이|달달이|(?<![0-9])월\s*단위로|월단위|(?<![0-9])월\s*주기", "M"),
)
# 주기 말 안이나 바로 뒤의 "말": 기간 끝이다 (q3 anchor). "분기말"은 주기 말이 "분기"라 뒤를 본다
FREQ_END = re.compile(r"말")
FREQ_END_AFTER = re.compile(r"\s*말(?![가-힣]*(?:씀|하))")


def frequency_anchor(query: str, i: int, j: int) -> str | None:
    """주기 말이 기간 끝을 가리키면 "end"."""
    return "end" if FREQ_END.search(query[i:j]) or FREQ_END_AFTER.match(query, j) else None

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


def _counted_value(m) -> str | None:
    n = m["n"]
    n = int(n) if n.isdigit() else _COUNT_WORDS[n]
    months = n * 12 if m["u"] in ("년", "해") else n
    return _MONTHS_FREQ.get(months)


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


# ---------------------------------------------------------------- Q2 기간

# 연도 자리에는 숫자 연도나 연도 별칭(올해·작년 등)이 온다. 별칭은 as_of의 연도로만 푼다.
# as_of의 달·분기에 기대는 말(지난달·지난 분기·최근 N개월)은 구간만 잡고 unresolved로 둔다. 월·분기와 공표 지연은 D7
_ALIAS = r"재작년|작년|지난해(?!외)|올해|금년"
_ALIAS_YEARS = {"재작년": -2, "작년": -1, "지난해": -1, "올해": 0, "금년": 0}


def _yr(n: int, suffix: str = r"\s*년?") -> str:
    return rf"(?:(?P<y{n}>\d{{4}}){suffix}|(?P<a{n}>{_ALIAS}))"


def _sub(n: int) -> str:
    # 분기·월 뒤의 "말"(분기말·7월말)은 구간에 넣되 "말잔"의 "말"은 남긴다
    return (rf"(?:\s*(?:(?P<q{n}>\d)\s*분기|(?P<m{n}>\d{{1,2}})\s*월(?!\d)|(?P<h{n}>[상하])\s*반기)"
            rf"(?:\s*말(?!잔))?)")


_SEP = r"\s*(?:부터|에서|~|-|–)\s*"
# 끝 연도를 생략한 범위("2026년 1월부터 6월까지")는 끝 단위가 시작 단위와 같아야 하고 "까지"로 닫혀야 한다
_RANGE = re.compile(_yr(1) + _sub(1) + "?" + _SEP
                    + rf"(?:{_yr(2)}{_sub(2)}?|(?P<omit>{_sub(3)})(?=\s*까지))" + r"\s*(?:까지)?")
_SINCE = re.compile(_yr(1) + _sub(1) + r"?\s*(?:이후|부터|이래)")
_RECENT = re.compile(r"(?:최근|지난)\s*(\d{1,2})\s*(년|개월|달|분기|반기)(?:\s*(?:간|동안))?")
_RECENT_UNIT = {"년": "year", "개월": "month", "달": "month", "분기": "quarter", "반기": "half"}
# 연도 별칭 시점: 올해 2분기, 작년 하반기, 작년 말, 작년 한 해, 올해 들어(→ 이후)
_REL = re.compile(rf"(?P<a1>{_ALIAS}){_sub(1)}?(?:\s*(?P<eoy>말)(?!잔)|\s*(?P<whole>한\s*해)|\s*(?P<into>들어))?"
                  r"|(?P<olinto>올\s+들어)")
# as_of의 달·분기에 기대는 말
_REL_MQ = re.compile(r"(?:지난|이번|저번)\s*(?:달|분기)")
_POINT = re.compile(_yr(1, r"\s*년").replace(f"|(?P<a1>{_ALIAS})", "") + _sub(1) + r"?(?P<month_span>\s*한\s*달\s*(?:간|동안)?)?")
# 바로 뒤에 오면 비교 기준(Q4 몫)이지 조회 기간이 아니다
_COMPARE_AFTER = re.compile(r"(?:\s*말)?\s*(?:보다|대비|에\s*비(?:해|하여|하면)|(?:와|과)\s*비교|"
                            r"같은\s*(?:달|월|분기|반기|기간|시기|해)|동월|동기|동분기)")


def _stamp(year: str, quarter: str | None, month: str | None) -> str:
    if quarter:
        return f"{year}-Q{quarter}"
    if month:
        return f"{year}-{int(month):02d}"
    return year


def _shift_year(as_of: str, years: int) -> str:
    return str(int(as_of[:4]) - years + 1)


def _year(m, n: int, as_of: str | None) -> str | None:
    """n번 연도 자리의 값. 별칭은 as_of가 없으면 None."""
    if m.groupdict().get(f"y{n}"):
        return m.group(f"y{n}")
    if not as_of:
        return None
    return str(int(as_of[:4]) + _ALIAS_YEARS[m.group(f"a{n}")])


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


def _is_relative(m, *ns: int) -> bool:
    return any(m.groupdict().get(f"a{n}") for n in ns)


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
            amount, unit = int(m.group(1)), _RECENT_UNIT[m.group(2)]
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


# ---------------------------------------------------------------- Q7 핵심어

# 요청·서술 표현. 표 이름에 쓰이지 않는 말만 둔다.
REQUEST_PATTERNS: tuple[str, ...] = (
    r"보여\s*(?:줘|주세요|주십시오|줄래)",
    r"찾아\s*(?:줘|주세요|줄래)",
    r"알려\s*(?:줘|주세요|줄래)",
    r"비교해\s*(?:줘|주세요|줄래)",
    r"(?:보고|알고|찾고|비교하고)\s*싶(?:어|다|어요|습니다)",
    r"궁금해|궁금하다",
    r"추이(?:를|가)?",
    r"(?:을|를)?\s*각각",
    r"같은\s*기간으로",
    r"기준으로",
    r"(?:으로|로)\s*나눠서",
    r"자료(?:를|가)?",
    # 그래프 요청 (docs/01 4절 Q7: 그래프·요청 표현 제거)
    r"(?:꺾은선|막대|선|원|파이)?\s*(?:그래프|차트)(?:로|를|으로)?",
    r"(?:만들어|그려)\s*(?:줘|주세요|줄래)",
)
_REQUEST = re.compile("|".join(f"(?:{p})" for p in REQUEST_PATTERNS))


def core_text(query: str, drop_spans: list[tuple[int, int]]) -> tuple[str, list[str]]:
    """조건 구간과 요청 표현을 지운 핵심어 검색식, 지운 조각 목록."""
    keep = [True] * len(query)
    dropped = []
    for i, j in sorted(drop_spans):
        dropped.append(query[i:j])
        for k in range(i, j):
            keep[k] = False
    for m in _REQUEST.finditer(query):
        if all(keep[m.start():m.end()]):
            dropped.append(m.group(0))
            for k in range(m.start(), m.end()):
                keep[k] = False
    text = "".join(ch if keep[k] else " " for k, ch in enumerate(query))
    text = re.sub(r"[.?!,]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(), dropped


# ---------------------------------------------------------------- Q5 분해·제외 (단계 2)

_WORD = r"[가-힣A-Za-z0-9]"
# 이름 끝에 붙는 조사. 나열의 마지막 이름에서만 뗀다. "처럼"은 "원재료와 중간재처럼"의 꼬리다 (Q5 개선 5)
_PARTICLE = re.compile(r"(?:으로|에서|처럼|의|을|를|이|가|은|는|로|에|도)$")
# "X(을/를/은/는) 뺀·빼고·제외한·제외하고·제외", "X이외(의)". 이/가는 이름 끝 글자와 헷갈려 떼지 않는다 (Q5 개선 6)
_EXCLUDE = re.compile(rf"({_WORD}+?)(?:을|를|은|는)?\s*(?:뺀|빼고|제외한|제외하고|제외)|({_WORD}+?)이외")
# 괄호 안 "A에서 B를 뺀 것"은 B를 빼라는 말이 아니라 계산 정의다 (Q5 개선 6)
_DEFINITION_TAIL = re.compile(r"\s*(?:것|값|금액|수치)\s*$")
# "X별(로)": 분류축으로 나누라는 말. 주기 단어(연도별·월별·분기별·반기별)는 Q3 몫이다.
_AXIS = re.compile(rf"(?<!{_WORD})({_WORD}{{2,}}?)별(?:로)?(?!{_WORD})")
# 기간 끝 반복("분기말별로")도 주기 말이라 분류 기준이 아니다 (Q3·Q4 공통 규칙)
_FREQ_AXIS = {"연도", "월", "분기", "반기", "년도", "연", "월말", "분기말", "반기말", "연말", "년말"}
# 가운뎃점. Q1 NFKC 뒤 "ㆍ"는 U+119E가 되고 "・"는 그대로 남는다 (data-X1)
_DOTS = "·\u119e\u30fb"


def find_lists(query: str, known=None) -> list[list[tuple[str, int, int]]]:
    """"·" 또는 "와/과 + 공백"으로 나열된 이름 사슬. 사슬마다 [(이름, 시작, 끝)] (docs/01 4절 Q5). 와/과는 뗀다.

    known(이름)이 참이면 마지막 이름 끝의 "이/가"를 조사로 보고 떼지 않는다(예: "생산자물가"가 "생산자물"이 되지 않게).
    """
    out = []
    for m in re.finditer(rf"{_WORD}+(?:(?:\s*[{_DOTS}]\s*|\s+){_WORD}+)*", query):
        chunk, base = m.group(0), m.start()
        # 덩어리를 공백·가운뎃점으로 나눈 뒤, 이웃 이름이 "·" 또는 "와/과 "로 이어진 사슬만 고른다
        parts = [(p.group(0), base + p.start(), base + p.end()) for p in re.finditer(rf"{_WORD}+", chunk)]
        links = []
        for (a, ai, aj), (b, bi, bj) in zip(parts, parts[1:]):
            gap = query[aj:bi]
            links.append(any(d in gap for d in _DOTS) or (a[-1] in "와과" and len(a) >= 3))
        chain: list[tuple[str, int, int]] = []
        for k, part in enumerate(parts):
            if chain and links[k - 1]:
                chain.append(part)
            else:
                if len(chain) >= 2:
                    out.append(_clean_chain(chain, query, known))
                chain = [part]
        if len(chain) >= 2:
            out.append(_clean_chain(chain, query, known))
    return out


def _clean_chain(chain, query, known=None):
    rows = []
    for k, (word, i, j) in enumerate(chain):
        if k < len(chain) - 1 and word[-1] in "와과" and not any(d in query[j:chain[k + 1][1]] for d in _DOTS):
            word, j = word[:-1], j - 1
        if k == len(chain) - 1:
            stripped = _PARTICLE.sub("", word)
            if word[-1] in "이가" and known and known(word):
                stripped = word
            j -= len(word) - len(stripped)
            word = stripped
        rows.append((word, i, j))
    return rows


# ---------------------------------------------------------------- Q5 나열 다듬기 (Q5 개선, 2026-10-13)
#
# find_lists의 사슬을 원문 구조와 운영 카탈로그 근거로 다듬는다. 카탈로그로 이름을 고칠 때는 같은 표의 분류값 이름이
# 있을 때만이고, 원문 위치(인용)는 그대로 둔다. 판단 근거는 이름마다 source로 남긴다.
#   list: 원문 그대로 / catalog_merge: 한 분류값 이름을 쪼갠 것을 되붙임 / catalog_tail: 공통 꼬리 분배 /
#   catalog_head: 공통 머리말(뒤 낱말) 분배

# 나열 앞에 와도 수식어가 아닌 말("생산자물가 중 식료품과", "CSI 가운데 외식비와", "신규취급액 기준 고정금리대출과")
_LIST_BOUNDARY = frozenset({"중", "가운데", "기준"})
# 낱말 끝 조사·어미. 이것으로 끝나는 앞 낱말은 나열을 꾸미는 맨 명사가 아니다
_TOKEN_TAIL = re.compile(r"(?:으로|에서|에게|처럼|부터|까지|의|을|를|은|는|이|가|로|에|도|와|과|만)$")


def _token_before(query: str, i: int):
    """i 바로 앞(공백 하나 사이)의 낱말 (낱말, 시작, 끝) 또는 None."""
    m = re.search(rf"({_WORD}+) $", query[:i])
    return (m.group(1), m.start(1), m.end(1)) if m else None


def _phrase_after(query: str, j: int) -> list[tuple[str, int, int]]:
    """j 뒤로 공백 하나씩 이어진 낱말들. 조사·어미가 붙은 낱말에서 끝난다 (그 낱말 포함)."""
    out, k = [], j
    while (m := re.match(rf" ({_WORD}+)", query[k:])):
        word, a, b = m.group(1), k + 1, k + m.end()
        out.append((word, a, b))
        k = b
        if _TOKEN_TAIL.search(word) or not re.match(rf" {_WORD}", query[k:]):
            break
    return out


def list_modified(query: str, i: int, taken: list, names) -> bool:
    """나열 첫 이름 바로 앞 낱말이 그 이름을 꾸미는 맨 명사인지. 조건 구간·경계어·조사가 붙은 낱말이면 아니다.
    바로 앞이 괄호 끝이면("금리(잔액 기준) 흐름과") 앞 구절의 꾸밈을 받는 것으로 본다."""
    if query[:i].endswith(") "):
        return True
    before = _token_before(query, i)
    return bool(before and all(before[2] <= a or before[1] >= b for a, b in taken) and before[0] not in _LIST_BOUNDARY
                and (names.known(before[0]) or not _TOKEN_TAIL.search(before[0])))


def exclude_list_members(query: str, chains: list, excludes: list, taken: list, names) -> list[tuple[str, int, int]]:
    """"식료품과 에너지를 뺀"처럼 제외 이름이 나열의 끝이면 앞 이름도 제외다 (개선 6).

    사슬 앞이 꾸밈을 받지 않고 앞 이름이 모두 카탈로그 분류값일 때만이다
    ("신선식품 생산자물가와 신선식품을 뺀 물가"의 생산자물가는 제외가 아니다).
    """
    starts = {i for _, i, _, _ in excludes}
    out = []
    for chain in chains:
        if chain[-1][1] in starts and not list_modified(query, chain[0][1], taken, names) \
                and all(names.tables(w) for w, _, _ in chain[:-1]):
            out.extend(chain[:-1])
    return out


def refine_lists(query: str, chains: list, taken: list, names) -> list[list[dict]]:
    """사슬을 다듬어 [{text, i, j, source}] 묶음으로 낸다. names는 카탈로그 조회(nodes._CatalogNames).

    1. 한 분류값 이름을 쪼갠 사슬("인력난·인건비상승", "특허 및 실용신안권")은 되붙인다 (개선 4).
    2. 마지막 이름의 꼬리를 앞 이름에 붙인 이름이 같은 표 분류값이면 붙인다 ("상품·…·이전소득수지") (개선 3).
    3. "와/과" 뒤 이름이 뒤 낱말로 이어지면 두 이름 구절이 나란한지 본다 (개선 2).
       - "A와 B H"에서 A+H, B+H가 같은 표 분류값이면 둘 다 H를 붙인다 ("기타사업서비스와 가공서비스 수지").
       - "X A와 B … A"(같은 머리말이 되풀이)는 두 지표 구절의 비교다. 늘린 이름이 같은 표 분류값이면 늘리고,
         아니면 두 이름에 measure_pair 표시를 단다 ("예금은행 … 금리와 상호저축은행 … 금리").
       - 사슬 앞이 맨 명사로 꾸며지고 뒤 이름도 이어지면(또는 "의"로 이어지면) 두 지표 구절의 비교로 보고 표시를 단다.
         두 이름이 모두 카탈로그 이름이면 표시하지 않는다. 낱말 수가 아니라 양쪽 구절이 각자 꾸밈을 갖는지와
         카탈로그 근거로 판단한다. 표시만 하고 나열에서 빼지는 않는다(빼면 정답 2건이 잃는다, Q5_구현결과 참고).
    """
    def free(i, j):
        return all(j <= a or i >= b for a, b in taken)

    out = []
    for chain in chains:
        rows = [{"text": w, "i": i, "j": j, "source": "list"} for w, i, j in chain]
        # 1. 되붙임: 사슬 전체가 한 분류값 이름
        joined = "".join(r["text"] for r in rows)
        whole = names.item(joined) or names.item("".join(r["text"] for r in rows[:-1]) + "및" + rows[-1]["text"])
        if whole:
            out.append([{"text": whole, "i": rows[0]["i"], "j": rows[-1]["j"], "source": "catalog_merge"}])
            continue
        for r in rows:  # "W 및 X"가 한 분류값 이름
            m = re.search(rf"({_WORD}+) 및 $", query[:r["i"]])
            if m and (name := names.item(m.group(1) + "및" + r["text"])):
                r.update(text=name, i=m.start(1), source="catalog_merge")
        # 2. 공통 꼬리 분배
        last = rows[-1]["text"].removesuffix("별")
        for n in range(len(last) - 1, 0, -1):
            tail, hit = last[-n:], False
            tables = names.tables(last)
            for r in rows[:-1]:
                if r["source"] == "list" and (cand := names.item(r["text"] + tail, tables)):
                    r.update(text=cand, source="catalog_tail")
                    hit = True
            if hit:
                break
        # 3. "와/과" 잇기 검사
        parts, cur = [], [rows[0]]
        before = _token_before(query, rows[0]["i"])
        modified = list_modified(query, rows[0]["i"], taken, names)
        for a, b in zip(rows, rows[1:]):
            gap = query[a["j"]:b["i"]]
            keep = True
            if not any(d in gap for d in _DOTS):
                raw = re.match(rf"{_WORD}+", query[b["i"]:]).group(0)
                after = _phrase_after(query, b["i"] + len(raw)) if raw == b["text"] else []
                # "A와 B의 C"는 보통 나란한 나열이지만, 앞 이름이 꾸밈을 받으면 두 지표 구절이다 ("전산업 부채비율과 자금순환표의 …")
                genitive = raw == b["text"] + "의" and bool(_phrase_after(query, b["i"] + len(raw)))
                if genitive and modified and not (names.known(a["text"]) and names.known(b["text"])):
                    keep = False
                elif after:
                    head = _PARTICLE.sub("", after[-1][0])
                    h = _PARTICLE.sub("", after[0][0])
                    shared = len(after) == 1 and names.item(a["text"] + h) and names.item(b["text"] + h)
                    if shared and names.tables(a["text"] + h) & names.tables(b["text"] + h):
                        a.update(text=names.item(a["text"] + h), source="catalog_head")
                        b.update(text=names.item(b["text"] + h), j=after[0][1] + len(h), source="catalog_head")
                    elif nospace(head) == nospace(a["text"]):
                        left = f"{before[0]} {a['text']}" if modified else a["text"]
                        right_j = after[-1][1] + len(head)
                        right = query[b["i"]:right_j]
                        lt, rt = names.tables(left), names.tables(right)
                        if lt & rt:
                            a.update(text=names.item(left, lt & rt), i=before[1] if modified else a["i"], source="catalog_head")
                            b.update(text=names.item(right, lt & rt), j=right_j, source="catalog_head")
                        else:
                            keep = False
                    elif modified and not (names.known(a["text"]) and names.known(b["text"])):
                        keep = False
            if not keep:
                # 지표 비교 경계는 표시만 하고 나열은 그대로 둔다 (사용자 결정 2026-10-13 대기, 추천안).
                # 잘린 조각이 표명 가점을 주던 정답 2건이 있어 빼는 일은 7번(지표별 요구) 설계에서 함께 정한다
                a["measure_pair"] = b["measure_pair"] = True
            cur.append(b)
        parts.append(cur)
        out.extend(p for p in parts if len(p) >= 2)
    return out


def find_excludes(query: str) -> list[tuple[str, int, int, list[str]]]:
    """제외 조건 (이름, 시작, 끝, complement_hints). 구간은 제외 표현까지 포함한다.

    괄호 안 "A에서 B를 뺀 것"(계산 정의)은 제외로 보지 않는다. "(직접투자 제외 증권투자분)" 같은 괄호 안 제외는 그대로다.
    """
    out = []
    for m in _EXCLUDE.finditer(query):
        name = m.group(1) or m.group(2)
        if len(name) < 2:
            continue
        lo, hi = query.rfind("(", 0, m.start()), query.find(")", m.end())
        if (lo >= 0 and hi >= 0 and ")" not in query[lo:m.start()]
                and re.search(r"에서\s*$", query[lo + 1:m.start()]) and _DEFINITION_TAIL.fullmatch(query[m.end():hi])):
            continue
        out.append((name, m.start(), m.end(), [f"{name}이외", f"{name}제외", f"{name} 제외"]))
    return out


def find_axes(query: str) -> list[tuple[str, int, int]]:
    """"X별" 분류축 (X, 시작, 끝). 주기 단어는 뺀다."""
    return [(m.group(1), m.start(), m.end()) for m in _AXIS.finditer(query) if m.group(1) not in _FREQ_AXIS]


def split_points(query: str, periods: list[dict]) -> tuple[list[int], bool]:
    """요구를 나눌 위치(정리본 색인)와 귀속이 애매한지.

    기간이 둘 이상이고 "기간 + 다른 말 + 와/과/및/," 다음에 다음 기간이 오면 거기서 나눈다(예: 2014년 A 기준과 2015년 B 기준).
    "2019년과 2021년"처럼 사이에 이은 말만 있으면 한 요구다. 사이에 다른 말이 있는데 잇는 말이 없으면 애매하다.
    """
    cuts, ambiguous = [], False
    for a, b in zip(periods, periods[1:]):
        gap = query[a["j"]:b["i"]]
        words = re.sub(r"[\s,]|와|과|및|그리고", "", gap)
        if not words:
            continue
        if re.search(r"(?:와|과|및|,|그리고)\s*$", gap):
            cuts.append(b["i"])
        else:
            ambiguous = True
    return cuts, ambiguous


# ---------------------------------------------------------------- J3 기간 비교 (단계 2)

# 주기별 한 시점의 달 수. 저장 끝보다 한두 시점 뒤 요청은 snapshot_lag (docs/01 4절 J3, D7 전)
FREQ_MONTHS = {"Y": 12, "H": 6, "Q": 3, "M": 1}
LAG_PERIODS = 2


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


def compare_period(req: dict, cov_start: str | None, cov_end: str | None, freq: str) -> tuple[str, str | None]:
    """요청 기간 하나와 한 주기의 수록 구간 비교 → (status, reason)."""
    if req["status"] != "explicit" or req["start"] is None:
        return "unknown", "period_unresolved"
    a, b = stamp_months(cov_start), stamp_months(cov_end)
    rs = stamp_months(req["start"])
    re_ = stamp_months(req["end"]) if req["end"] else None
    if not (a and b and rs) or (req["end"] and not re_):
        return "unknown", "stamp_unparsed"
    lo, hi = a[0], b[1]
    want_lo, want_hi = rs[0], (re_[1] if re_ else None)
    lag = FREQ_MONTHS[freq] * LAG_PERIODS
    if want_hi is None:  # since, 끝이 열림. as_of 없이는 끝을 모른다
        if want_lo > hi:
            return ("unknown", "snapshot_lag") if want_lo - hi <= lag else ("outside", "ended_before_request")
        # 끝을 정하지 않은 채 수록 안이면 covered로 두되 open_end를 남긴다. 끝의 뜻은 D7에서 정한다 (Codex F04)
        return ("covered", "open_end") if want_lo >= lo else ("partial", "starts_after_request")
    if want_hi < lo:
        return "outside", "starts_after_request"
    if want_lo > hi:
        return ("unknown", "snapshot_lag") if want_lo - hi <= lag else ("outside", "ended_before_request")
    if want_lo >= lo and want_hi <= hi:
        return "covered", None
    if want_hi > hi and want_lo >= lo and want_hi - hi <= lag:
        return "unknown", "snapshot_lag"
    return "partial", "starts_after_request" if want_lo < lo else "ended_before_request"


# ---------------------------------------------------------------- Q4 기준어·단위 (단계 3)

# 축 → 값 → 질문에서 찾는 표현. 표·분류값 쪽 표기도 같은 표현으로 찾는다(공백 없앤 문자열 포함).
# 같은 축의 다른 값은 반대 기준이다(J4 conflict 근거). Q4는 role을 정하지 않는다 (v1.6 Codex B).
BASIS_AXES: dict[str, dict[str, tuple[str, ...]]] = {
    "seasonal": {"원계열": ("원계열",), "계절조정": ("계절조정",)},
    "stock": {"말잔": ("말잔", "기말잔액", "월말잔액"), "평잔": ("평잔", "평균잔액", "월평균잔액")},
    "price": {"명목": ("명목",), "실질": ("실질",)},
    "loan_basis": {"신규취급액": ("신규취급액",), "잔액기준": ("잔액기준",)},
    # 달러표시·원화표시: 국민계정 분류값 표기 ("1인당 국민총소득(명목 달러표시)"). 계약통화: 수출입물가 (Q4 2단계)
    "currency": {"원화": ("원화",), "달러": ("달러기준", "미달러", "달러화", "달러표시"), "계약통화": ("계약통화",)},
    "size_law": {"중소기업기본법": ("중소기업기본법",), "조세특례제한법": ("조세특례제한법",)},
    "comparison": {"전년동기비": ("전년동기비", "전년동기대비", "전년동월비"), "전기비": ("전기비", "전기대비", "전월비")},
    # KOSIS 메타 EDA 발견 3: 형제 표를 가르는 축 중 빠져 있던 것 (2026-10-13 Q4 1단계)
    "survey_view": {"실적": ("실적",), "전망": ("전망",)},  # 대출행태서베이의 동향/전망은 표 메타로 갈리지 않아 넣지 않는다
    "survey_method": {"표본조사": ("표본조사",)},
    # 시가는 명목·실질과 한 축에 두지 않는다: 반대 기준을 분류값 부분 문자열로 보는 J4가 "도시가스"를 시가로 읽는다
    "valuation": {"시가": ("시가",)},
    "classification": {"기본분류": ("기본분류",), "특수분류": ("특수분류",), "품목별": ("품목별",), "용도별": ("용도별",)},
}
# 기준어 뒤에 오면 다른 낱말이다 (시가총액·시가지·시가배당률)
_NOT_BEFORE = {"시가": r"(?!\s*(?:총액|지|배당))"}
# 표명·경로로만 판정하는 축. 실적/전망은 분류값 이름("가계부채전망CSI", "가동률실적BSI")에 지표 이름으로 붙어 있어
# 분류값으로 보면 소비자동향조사 표가 "전망"과 일치한다고 잘못 읽는다
TITLE_ONLY_AXES = {"survey_view", "valuation"}


def basis_forms(axis: str, value: str) -> tuple[str, ...]:
    return BASIS_AXES.get(axis, {}).get(value, (value,))


def find_basis(query: str) -> list[tuple[str, str, int, int]]:
    """(축, 값, 시작, 끝). 사전 기준어를 먼저 찾고, 남은 "X별"을 분류 기준(classification, 값 "X별")으로 둔다.

    표현은 낱말 앞에서 시작할 때만 잡는다(글자 사이 공백은 허용: "월말 잔액"). "월말 잔액"의 "말잔"처럼
    낱말 경계를 넘는 우연한 일치를 막는다. 주기 단어(연도별·월별 등)는 Q3 몫이라 뺀다.
    "잔액"은 흔히 금액을 뜻하므로 "잔액 기준"일 때만 기준어다.
    """
    found, taken = [], []
    for axis, values in BASIS_AXES.items():
        for value, forms in values.items():
            for form in sorted(forms, key=len, reverse=True):
                pattern = rf"(?<!{_WORD})" + r"\s*".join(map(re.escape, form)) + _NOT_BEFORE.get(form, "")
                for m in re.finditer(pattern, query):
                    i, j = m.span()
                    if all(j <= a or i >= b for a, b in taken):
                        taken.append((i, j))
                        found.append((axis, value, i, j))
    for m in _AXIS.finditer(query):
        if m.group(1) in _FREQ_AXIS or not all(m.end() <= a or m.start() >= b for a, b in taken):
            continue
        taken.append(m.span())
        found.append(("classification", m.group(1) + "별", m.start(), m.end()))
    return sorted(found, key=lambda x: x[2])


# ---------------------------------------------------------------- Q4 B 전달 조건 (Q4 2단계, 2026-10-13)

# 표를 가르지 않고 B가 값을 고르거나 계산할 때 쓰는 조건. 표 가르기 축(BASIS_AXES)과 코드를 나눠 둔다.
# 실제 노드 분리는 4단계에서 한다. 이 절의 조건은 아래 넷이다.
# - 통화(currency): 표명·경로에 있으면 표 가르기(BASIS_AXES), 분류값에만 있으면 B 전달(CURRENCY_ITEM 문법)
# - 비교 기준(comparison): KOSIS 메타에서 표명·경로에 없고 분류값("…(전년동기비)")에만 있다. 분류값에 있으면
#   match(handoff)로 남기고, 없으면 판정하지 않는다. 사전어는 BASIS_AXES, 자연어 표현은 HANDOFF_PATTERNS
# - 계산 요청(calc): "몇 %". J4가 판정하지 않는다
# - 단위(unit): 형제 표를 단위로 가르는 경우가 없어 B 전달 전용이다(EDA 4절). J4 단위 판정은 확신도에만 쓴다
B_HANDOFF_CONDITIONS = ("currency", "comparison", "calc", "unit")

HANDOFF_ONLY_AXES = {"comparison", "calc"}
_THAN = r"\s*(?:보다|대비|에\s*비해|에\s*비하여|과\s*비교해?|와\s*비교해?|비(?![가-힣]))"
HANDOFF_PATTERNS = (
    # 1년 전·작년 같은 달과 견주면 전년동기비. 연간 자료의 "전년보다"도 같은 값이다
    (r"(?:전년|작년|지난해|전년도)\s*(?:같은|동)\s*(?:달|월|분기|기간|때|시기|기)" + _THAN, "comparison", "전년동기비"),
    (r"전년\s*동\s*(?:월|분기|기)(?:" + _THAN + r")?", "comparison", "전년동기비"),
    (r"(?<![0-9])(?:1|한)\s*년\s*전" + _THAN, "comparison", "전년동기비"),
    (r"(?:전년|작년|지난해|전년도)" + _THAN, "comparison", "전년동기비"),
    # 바로 앞 기간과 견주면 전기비
    (r"(?<![가-힣])전\s*(?:월|분기말|분기|반기|기)" + _THAN, "comparison", "전기비"),
    (r"(?:지난|직전)\s*(?:달|월|분기|반기)" + _THAN, "comparison", "전기비"),
    (r"(?:한\s*달|1\s*개월|석\s*달|3\s*개월)\s*전" + _THAN, "comparison", "전기비"),
    # "몇 %"는 백분율로 답하라는 계산 요청이다
    (r"몇\s*(?:%|퍼센트|프로)", "calc", "백분율"),
)


def find_handoff(query: str, taken: list[tuple[int, int]]) -> list[tuple[str, str, int, int]]:
    """(축, 값, 시작, 끝). 이미 잡힌 기준어 구간(taken)과 겹치면 뺀다. 같은 자리는 앞 패턴과 긴 일치가 먼저다."""
    found, spans = [], list(taken)
    for pattern, axis, value in HANDOFF_PATTERNS:
        for m in re.finditer(pattern, query):
            i, j = m.span()
            if all(j <= a or i >= b for a, b in spans):
                spans.append((i, j))
                found.append((axis, value, i, j))
    return sorted(found, key=lambda x: x[2])


# 분류값의 통화 표기 (Q4 2단계 통화 검수, 2026-10-13). 표명·경로는 위 사전으로 보고, 분류값은 이 문법으로 본다.
# - 통화 이름 그대로인 분류값("달러 (미국)")도 그 통화다. 다만 "싱가폴달러"·"홍콩달러화"·"백만달러"처럼 앞에 다른
#   글자가 붙은 달러는 아니다
# - 통화 이름 뒤에는 통화를 꾸미는 말만 온다. "원화수입수수료"(생산자물가 품목)의 원화는 통화 기준이 아니다
# - "외화"는 원화가 아닌 통화를 묶은 값이다. 달러·원화를 물었는데 외화만 있으면 판단 불가다(계약통화는 외화와 다른 기준)
_CUR_TAIL = r"(?=$|[^가-힣]|기준|표시|화|결제|예금|예수금|예치금|대출금|대출|채권|자금)"
CURRENCY_ITEM = {
    "원화": re.compile(r"원화" + _CUR_TAIL),
    "달러": re.compile(r"(?:(?<![가-힣])달러|미달러)" + _CUR_TAIL),
    "계약통화": re.compile(r"계약통화" + _CUR_TAIL),
}
FOREIGN_ITEM = re.compile(r"외화" + _CUR_TAIL)
# 표 단위의 통화 (Q4 마무리 보완, 2026-10-13). 표명·경로·분류값에 통화 근거가 없을 때만 쓴다.
# - 돈 단위만 본다: "백만달러" → 달러, "십억원" → 원화. 환율("원/달러")·%·지수는 통화 근거가 아니다
# - 표 전체 단위(UNIT_NM, 대표 분류값 단위)가 있으면 그것으로, 없으면 분류값 단위가 한 통화로만 되어 있을 때 쓴다
# - 혼합 단위(달러와 만원이 섞인 표)는 근거로 쓰지 않는다
# - 원은 KOSIS 표 대부분(201표)의 보고 단위라 원화 일치의 근거로 쓰지 않는다. 쓰면 원 단위 표마다 기준어 점수가
#   붙어 순위가 흔들린다. 일치 근거는 원이 아닌 통화(달러) 단위만이다
DEFAULT_CURRENCY = "원화"
_MONEY_UNIT = re.compile(r"(?:십억|백만|십만|천만|천|만|억|조)?(원|달러)")
UNIT_CURRENCY = {"원": "원화", "달러": "달러"}


def unit_currency(meta: dict) -> tuple[str | None, str]:
    """(통화, 근거 단위 문자열). 명확한 단위 근거가 없거나 섞여 있으면 (None, "")."""
    def money(units):
        found = {}
        for u in units:
            m = _MONEY_UNIT.fullmatch(re.sub(r"\s+", "", u or ""))
            if m:
                found.setdefault(UNIT_CURRENCY[m.group(1)], u)
        return found
    table = money([meta.get("unit"), (meta.get("item_itm") or {}).get("unit")])
    found = table or money([it.get("unit") for it in meta.get("items", [])])
    if len(found) != 1:
        return None, ""
    (cur, unit), = found.items()
    return cur, unit

# 숫자 뒤 %("5% 이상", "2~3%대")는 값 조건, "몇 %"는 계산 요청이라 단위가 아니다 (Q4 2단계)
_UNIT = re.compile(r"(?:(?:십억|백만|천만|천|억|조)\s*)?(?:원|달러)\s*단위|(?<![0-9몇.])(?<![0-9몇]\s)(?:퍼센트|%)")


def find_units(query: str) -> list[tuple[str, int, int]]:
    """(단위, 시작, 끝). "X 단위"의 X, 또는 %."""
    return [(re.sub(r"\s+|단위$", "", m.group(0)).replace("퍼센트", "%"), m.start(), m.end()) for m in _UNIT.finditer(query)]


_UNIT_SCALE = {"": 1, "천": 10**3, "백만": 10**6, "천만": 10**7, "억": 10**8, "십억": 10**9, "조": 10**12}
_UNIT_PARTS = re.compile(r"(십억|백만|천만|천|억|조)?(원|달러|%)")


def unit_parts(unit: str) -> tuple[int, str] | None:
    """단위 문자열 → (배율, 바탕). "십억원" → (10^9, 원), "%" → (1, %). 읽지 못하면 None (Codex F05)."""
    m = _UNIT_PARTS.fullmatch(re.sub(r"\s+", "", unit or "").replace("퍼센트", "%"))
    return (_UNIT_SCALE[m.group(1) or ""], m.group(2)) if m else None


def compare_unit(wanted: str, available: list[str]) -> str:
    """요청 단위와 표 단위들 → same(같음) · rescale(바탕만 같고 배율 다름) · differ(다름, "지수" 같은 다른 단위 포함)
    · unknown(요청 단위를 읽지 못하거나 표에 단위가 없음)."""
    w = unit_parts(wanted)
    have = [p for p in (unit_parts(u) for u in available) if p]
    if w is None or not available:
        return "unknown"
    if w in have:
        return "same"
    if any(b == w[1] for _, b in have):
        return "rescale"
    return "differ"


# Q6G 모델 요청 conditions.basis에서 빼는 조건(Q4 1·2단계 추가분). 사용자 결정 2026-10-13: 모델에 줄지는 Q6G 재측정 때 정한다
Q6G_HIDDEN_AXES = {"survey_view", "survey_method", "valuation", "calc"}
Q6G_HIDDEN_VALUES = {("currency", "계약통화")}


def q6g_hidden(c: dict) -> bool:
    """Q4 개선으로 새로 잡히는 기준어인가. B 전달 규칙(find_handoff)으로 잡힌 조건은 origin이 붙는다."""
    return c["axis"] in Q6G_HIDDEN_AXES or (c["axis"], c["value"]) in Q6G_HIDDEN_VALUES or c.get("origin") == "handoff_rule"


# ---------------------------------------------------------------- D3 기본값 정책 초안 (비교용)

# D3는 처음 정한 축만 읽는다. Q4 1단계에서 더한 실적/전망·시가·표본조사는 형제 표의 내용 차이로 남긴다
# (기본값으로 고를 축이 아니다: 실적과 전망, 시가와 명목은 기본값이 없다)
_POLICY_SKIP = {("survey_view", None), ("survey_method", None), ("valuation", None)}


def _axis_of(token: str) -> tuple[str, str] | None:
    """괄호 안 낱말 하나 → (축, 값). "계절조정계열", "잔액 기준"처럼 붙은 말도 읽는다."""
    for tok in (token, re.sub(r"(기준|계열)$", "", token)):
        for axis, values in BASIS_AXES.items():
            for value, forms in values.items():
                if (axis, None) in _POLICY_SKIP or (axis, value) in _POLICY_SKIP:
                    continue
                if tok in {nospace(f) for f in forms} | {nospace(value)}:
                    return axis, value
    if tok == "잔액":
        return "loan_basis", "잔액기준"
    return None


def bracket_tokens(title: str) -> list[str]:
    return [nospace(x) for b in re.findall(r"\((.*?)\)", title or "") for x in b.split(",") if nospace(x)]


# ---------------------------------------------------------------- Q5 지표별 요구 (7번 1단계, 2026-10-13)

# 지표 구절 끝: 목적·주제 조사로 끝나는 낱말. 이/가는 그 낱말이 카탈로그 이름("생산자물가")이 아닐 때만
_PHRASE_END = re.compile(r"(?:을|를|은|는)[.,?!]*$")
_PHRASE_END_SUBJECT = re.compile(r"(?:이|가)[.,?!]*$")
# 끝말 조건을 모든 지표에 걸린 것으로 볼 수 있는 말
_ALL_MEASURES = re.compile(r"모두|각각|둘\s*다|함께|모든")
_REPEATABLE = frozenset("PF")  # 기간·주기는 앞머리에만 있으면 공통이다
# 두 지표를 함께 받는 비교 표현. "대비"는 "전년동월대비" 같은 기준어와 겹쳐 넣지 않는다
_COMPARE = re.compile(r"차이|격차|스프레드|비교|견주|견줘")
# 한 지표를 지목하는 낱말 끝: "X는", "X만", "X에만", "X에는". 만·에만은 대상을 좁히는 한정이다
_DESIGNATE = re.compile(r"(?:에만|에는|만|은|는)[,]*$")
_RESTRICT = re.compile(r"(?:에만|만)[,]*$")
_TOPIC_END = re.compile(r"(?:은|는)[,]*$")
_PARALLEL = re.compile(r"(?:와|과)$")
_BASIS_WORD = re.compile(r"^기준")  # "기준은·기준만"은 조건 쪽 말이지 지표 이름이 아니다
# 문장 끝(소수점은 아니다)과 절 끝(연결 어미)
_SENTENCE_END = re.compile(r"[?!]|\.(?!\d)")
_CLAUSE_END = re.compile(r"\S(?:고|되|며|면서|해서|지만)[,]*$")
# 지목한 대상에 적용하지 않는다는 말: 그 지목은 적용 대상으로 확정하지 않는다
_NEGATE = re.compile(r"말고|않|빼고|빼서|빼면|제외|아닌")
# 두 지표 구절을 재료로 한 값 하나를 만드는 말("A와 B를 상계한 순수취액"): 지표 둘로 나누지 않는다 (Q5 마무리)
_COMBINE = re.compile(r"^(?:상계|차감|공제|합산|합계|합하|합친|더한|더해|뺀|빼서)")
# 지표 구절을 꾸미는 관형 동사("내다본", "나간", "발행된"). 은/는 끝은 주제 조사와 겹쳐 넣지 않는다
_ADNOMINAL = re.compile(r"\S(?:본|한|된|난|온|간|낸|친|운|인|던|진|린|른|쓴|든)$")


def _measure_names(query: str, segments: list, conds: list, end: int) -> list[str]:
    """지표마다 지목 대조에 쓸 이름 (공백 없앰). 괄호 설명, 조건 원문과 그 뒤 "기준~" 낱말, 마지막 지표의
    끝말 비교 명사("차이를")는 이름이 아니다."""
    spans = [(c[2], c[3]) for c in conds if c[2] is not None and c[1] != "R"]  # 분해 이름은 지표 구절의 말이다
    names = []
    for k, (a, b) in enumerate(segments):
        text = re.sub(r"\([^)]*\)", lambda m: " " * len(m.group()), query[a:b])
        words = []
        after_cond = False
        for m in re.finditer(r"\S+", text):
            p = a + m.start()
            in_cond = any(i <= p < j for i, j in spans)
            if in_cond or (after_cond and _BASIS_WORD.search(m.group())):
                after_cond = in_cond
                continue
            after_cond = False
            words.append(m.group())
        if k == len(segments) - 1 and words and _COMPARE.search(words[-1]) and len(words) > 1:
            words = words[:-1]
        names.append("".join(words))
    return names


def _designee(names: list[str], words: list, k: int, skip) -> int | str | None:
    """words[k]가 가리키는 지표 번호. 지목이 아니면 None, 대상을 하나로 못 정하면 "unsure".

    - 한 낱말로 여러 지표에 맞으면("금리만") 앞 낱말을 붙여 한 지표로 좁힌다("대출 금리만"). 공백 차이는 무시한다.
    - "A와 B에만"처럼 병렬로 지목하면 하나로 줄이지 않는다("unsure").
    - "X만·X에만"인데 X가 어느 지표 이름에도 없으면("전자에만", 질문에 없는 지표) 한정을 버리지 않는다("unsure").
    """
    p, word = words[k]
    m = _DESIGNATE.search(word)
    stem = word[:m.start()]
    if len(stem) < 2 or skip(p) or _BASIS_WORD.search(stem):
        return None
    hits = None
    for n in range(1, min(k + 1, 4) + 1):
        phrase = "".join(w for _, w in words[k - n + 1:k]) + stem
        found = [x for x, name in enumerate(names) if phrase in name]
        if not found:
            break
        hits = found
        if len(found) == 1:
            break
    if hits is None:
        return "unsure" if _RESTRICT.search(word) else None
    if k > 0 and _PARALLEL.search(words[k - 1][1]):
        prev = words[k - 1][1][:-1]
        if len(prev) >= 2 and any(prev in name for name in names):
            return "unsure"
    return hits[0] if len(hits) == 1 else "unsure"


def _tail_owner(query: str, segments: list, names: list, end: int, i: int, other_basis: bool, skip) -> int | str | None:
    """끝말 조건 하나의 귀속 (7번 1단계 보완 3). 지표 번호, "common", "unsure"(지목 대상 불명), None(판별 못 함).

    판정은 조건이 속한 절(마지막 지표 구절 뒤, 문장 끝·연결 어미 "~고·~되·~며"로 나눈 부분)을 먼저 본다.
    1. 같은 문장에 적용하지 않는다는 지목("A에만 적용하지 말고", "A만 빼고")이 있으면 "unsure".
    2. 조건의 절에서 지표를 지목하면 그 지표 것이다. 조건 앞의 가장 가까운 지목, 없으면 뒤의 지목. 다른 절의
       지목은 이 조건의 근거가 아니다. 대상을 하나로 못 정하면 "unsure".
    3. 지목 없이 지표 구절과 다른 문장에 붙은 조건은 판별 못 함.
    4. 다른 지표가 같은 축의 기준을 따로 가졌으면 마지막 지표 구절 바로 뒤일 때만 마지막 지표 것.
    5. 조건의 절에 지표가 아닌 주제("설명은", "색상은")가 있으면 "모두·각각·비교"는 그 주제의 말이므로 판별 못 함.
    6. 조건의 절에 "모두·각각"이 있으면 공통.
    7. 마지막 지표 구절 끝의 비교 명사("차이를"), 또는 조건의 절이나 지표 구절을 받는 첫 절의 비교 표현이 있으면 공통.
    8. 그 밖에는 판별 못 함.
    """
    breaks = [m.end() for m in _SENTENCE_END.finditer(query, end, i)]
    start = breaks[-1] if breaks else end
    stop = _SENTENCE_END.search(query, i)
    stop = stop.start() if stop else len(query)
    words = [(m.start(), m.group()) for m in re.finditer(r"\S+", query[:stop])]
    words = [(p, w) for p, w in words if p >= start]
    clause_of, n = [], 0
    for _, w in words:
        clause_of.append(n)
        if _CLAUSE_END.search(w):
            n += 1
    here = next((clause_of[k] for k, (p, w) in enumerate(words) if p <= i < p + len(w)), None)
    if here is None:
        here = max((clause_of[k] for k, (p, _) in enumerate(words) if p < i), default=0)
    marks = [(k, _designee(names, words, k, skip)) for k, (p, w) in enumerate(words) if _DESIGNATE.search(w)]
    marks = [(k, d) for k, d in marks if d is not None]
    for c in {clause_of[k] for k, _ in marks}:
        text = " ".join(w for k, (_, w) in enumerate(words) if clause_of[k] == c)
        if _NEGATE.search(text):
            return "unsure"
    mine = [(words[k][0], d) for k, d in marks if clause_of[k] == here]
    before = [d for p, d in mine if p < i]
    after = [d for p, d in mine if p >= i]
    if before or after:
        return before[-1] if before else after[0]
    if breaks:
        return None
    if other_basis:
        return len(segments) - 1 if not query[end:i].strip() else None
    clause = [w for k, (p, w) in enumerate(words) if clause_of[k] == here]
    foreign = any(_TOPIC_END.search(w) and len(w) >= 3 and not _COMPARE.search(w) and not _BASIS_WORD.search(w)
                  and not skip(p) for k, (p, w) in enumerate(words) if clause_of[k] == here)
    if foreign:
        return None
    if any(_ALL_MEASURES.search(w) for w in clause):
        return "common"
    last = query[:end].split()[-1]
    first = [w for k, (_, w) in enumerate(words) if clause_of[k] == 0]
    if _COMPARE.search(last) or any(_COMPARE.search(w) for w in clause + first):
        return "common"
    return None


def measure_scopes(query: str, cuts: list[int], conds: list[tuple], known=None) -> dict | None:
    """지표 구절 경계(cuts: 다음 지표 구절이 시작하는 색인)로 조건을 나눈다.

    conds는 (condition_id, 종류 글자 P/F/B/U/R/X, 시작 색인, 끝 색인). 위치가 없으면 색인은 None. 돌려주는 값은
    {"segments": [(i, j)...], "own": [[cid]...], "common": [cid], "ambiguous": [cid]}이고,
    마지막 지표 구절의 끝을 정하지 못하면 None(나누지 않는다).

    - 같은 종류 조건이 둘 이상의 구간에 있으면(반복) 각자 자기 구간의 지표 것이다.
    - 지표 구절 안(첫 구간은 앞머리 뒤)에만 있으면 그 지표 것이다.
    - 앞머리·끝말의 기간·주기는 공통이다.
    - 앞머리 기준어·단위·제외·분해는 애매다.
    - 끝말 조건은 `_tail_owner`를 따른다(조건이 속한 절의 지목 우선, "모두·각각·비교"는 그 조건에 걸릴 때만 공통.
      지목 대상을 못 정하면 기간·주기도 애매, 지목이 없어 판별 못 한 기간·주기는 공통, 나머지는 애매). conds의 다섯째 값(기준어 축)이 있으면 지표마다 다른 기준을 가렸는지 본다.
    """
    if not cuts:
        return None
    request = [m.start() for m in _REQUEST.finditer(query) if m.start() >= cuts[-1]]
    end = None
    pending = False  # 끝말 낱말 뒤에 관형 동사가 와서 지표 구절이 이어지는지 보는 중
    found_name = False
    for m in re.finditer(r"\S+", query[cuts[-1]:]):
        b = cuts[-1] + m.end()
        nxt = b + len(query[b:]) - len(query[b:].lstrip())
        word = m.group().rstrip(".,?!")
        if pending:
            bare = _PARTICLE.sub("", word)
            found_name = found_name or bool(known and len(bare) >= 2 and known(bare))
        subject = _PHRASE_END_SUBJECT.search(word) and len(word) >= 3 and not (known and known(word))
        if _PHRASE_END.search(word) or subject or nxt in request:
            if end is None:
                end = b
                # "6개월 뒤를 내다본 향후경기전망 지수"처럼 끝말 뒤 관형 동사가 지표 이름을 꾸미면 구절이 이어진다
                after = re.match(r"\S+", query[nxt:])
                if nxt not in request and after and _ADNOMINAL.search(after.group().rstrip(".,?!")) \
                        and not (known and known(after.group())):
                    pending = True
                    continue
                break
            if found_name:
                end = b  # 이어진 구절에 카탈로그 이름이 있을 때만 끝을 늘린다(없으면 처음 끝 그대로)
            break
    if end is None or end >= len(query.rstrip()):
        return None
    # "A와 B를 상계한 X"는 두 구절을 재료로 한 값 하나다. 지표 둘로 나누지 않는다
    after = query[end:].lstrip()
    if _COMBINE.search(after):
        return None
    bounds = [0, *cuts, end]
    segments = [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]
    # 앞머리: 첫 구간에서 조건이 아닌 첫 낱말 앞
    ranges = [(c[2], c[3]) for c in conds if c[2] is not None and c[1] != "R"]  # 분해 이름은 지표 구절의 말이다
    head_end = 0
    for m in re.finditer(r"\S+", query[:segments[0][1]]):
        if any(i <= m.start() < j for i, j in ranges):
            continue
        head_end = m.start()
        break

    def place(pos):
        if pos is None or pos >= end:
            return "tail"
        if pos < head_end:
            return "head"
        return next(k for k, (a, b) in enumerate(segments) if a <= pos < b)

    places = {c[0]: place(c[2]) for c in conds}
    kinds = {}
    for c in conds:
        where = places[c[0]]
        if where != "tail":
            kinds.setdefault(c[1], set()).add(0 if where == "head" else where)
    names = _measure_names(query, segments, conds, end)
    cond_spans = [(c[2], c[3]) for c in conds if c[2] is not None and c[1] != "R"]

    def skip(p):  # 조건 원문 안의 낱말은 지목이 아니다
        return any(a <= p < b for a, b in cond_spans)

    owned_groups = {(c[1], c[4]) for c in conds if len(c) > 4 and c[4] is not None and isinstance(places[c[0]], int)}
    own = [[] for _ in segments]
    common, ambiguous = [], []
    for c in conds:
        cid, kind, i = c[0], c[1], c[2]
        where = places[cid]
        repeated = len(kinds.get(kind, ())) >= 2
        if isinstance(where, int):
            own[where].append(cid)
        elif where == "head" and repeated:
            own[0].append(cid)
        elif where == "tail" and i is not None:
            group = c[4] if len(c) > 4 else None
            owner = _tail_owner(query, segments, names, end, i, (kind, group) in owned_groups and group is not None,
                                skip)
            if isinstance(owner, int):
                own[owner].append(cid)
            elif owner == "unsure":
                ambiguous.append(cid)
            elif owner == "common" or kind in _REPEATABLE:
                common.append(cid)
            else:
                ambiguous.append(cid)
        elif kind in _REPEATABLE:
            common.append(cid)
        else:
            ambiguous.append(cid)
    return {"segments": segments, "own": own, "common": common, "ambiguous": ambiguous}
