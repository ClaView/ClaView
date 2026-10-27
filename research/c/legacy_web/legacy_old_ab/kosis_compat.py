"""원본 fetch 계약을 보존하면서 KOSIS의 호환 문자 표기만 정리한다.

예: 2020＝100(전각 등호) == 2020=100. 2015=100은 다른 기준이다.
숫자 DT, 분류 코드, 기간은 건드리지 않는다. 원응답은 Trace에 그대로 남는다.
"""
import unicodedata
from claview.fetch import KosisClient


def normalize_unit_labels(rows, trace):
    normalized, changes = [], set()
    for source in rows:
        row = dict(source)
        unit = row.get("UNIT_NM")
        if isinstance(unit, str):
            canonical = unicodedata.normalize("NFKC", unit).strip()
            if canonical != unit:
                changes.add((unit, canonical))
                row["UNIT_NM"] = canonical
        normalized.append(row)
    if changes:
        trace.add("unit_typography_normalized", changes=[{"raw": a, "normalized": b} for a, b in sorted(changes)],
                  rule="Unicode NFKC + outer whitespace only; numeric values unchanged")
    return normalized


class UnitCompatibleKosisClient(KosisClient):
    def fetch(self, params, trace):
        # 상위 클라이언트가 인증·재시도·민감정보 검사와 원응답 기록을 담당한다.
        rows = super().fetch(params, trace)
        return normalize_unit_labels(rows, trace)
