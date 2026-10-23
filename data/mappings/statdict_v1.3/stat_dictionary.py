# -*- coding: utf-8 -*-
"""통계어사전 조회 모듈 (A단계/검색 확장에서 import 해서 사용).

    from stat_dictionary import StatDict
    d = StatDict.load("통계어사전_v1/통계어사전_v1.json")
    d.lookup("주담대")              # 정규화키 정확 일치 → 항목 리스트
    d.expand("주담대 금리 알려줘")   # 질문 속 일상어를 찾아 공식어 후보를 돌려줌

매칭강도: '강' = 동의·약어·속어(공식어로 바꿔 검색해도 같은 통계), '중' = 하위 근사(공식어의 일부를 가리킴 → 확장 후보).
모호어(ambiguous) = 같은 일상어가 여러 공식어에 걸리는 경우 → 호출 측에서 되묻기/복수 후보 처리를 결정.
"""
import json
import re
import unicodedata

_DROP = re.compile(r"[\s·ㆍ・,\-_/()\[\]{}'\"“”‘’~:;!?·ㆍ]+")
_DOT = re.compile(r"(?<!\d)\.|\.(?!\d)")


def norm_term(s):
    """조회용 정규화키: NFKC → 소문자 → 공백·구두점 제거(숫자 사이 소수점은 유지)."""
    s = unicodedata.normalize("NFKC", str(s)).casefold()
    return _DOT.sub("", _DROP.sub("", s))


class StatDict:
    def __init__(self, entries, meta=None):
        self.entries = entries              # {정규화키: [항목 dict, ...]}
        self.meta = meta or {}
        self._keys = sorted(entries, key=len, reverse=True)

    @classmethod
    def load(cls, path):
        data = json.load(open(path, encoding="utf-8"))
        return cls(data["entries"], data.get("meta"))

    def lookup(self, text, min_strength="중", min_trust="낮음"):
        """질문 전체/일상어 하나가 사전의 키와 정확히 같을 때의 항목들."""
        out = self.entries.get(norm_term(text), [])
        return [e for e in out if _ok(e, min_strength, min_trust)]

    def expand(self, query, min_strength="중", min_len=2, min_trust="낮음"):
        """질문 안에서 사전의 일상어를 최장일치로 찾아 [{'일상어','공식어들','모호어','항목'}] 로 돌려줌.
        겹치는 일치는 긴 것만 채택. 키 길이 min_len 미만은 무시(오탐 방지)."""
        q = norm_term(query)
        taken = [False] * len(q)
        found = []
        for k in self._keys:
            if len(k) < min_len:
                break
            start = 0
            while True:
                i = q.find(k, start)
                if i < 0:
                    break
                if not any(taken[i:i + len(k)]):
                    es = [e for e in self.entries[k] if _ok(e, min_strength, min_trust)]
                    if es:
                        for t in range(i, i + len(k)):
                            taken[t] = True
                        offs = list(dict.fromkeys(e["공식어"] for e in es))
                        found.append({"일상어": es[0]["일상어"], "위치": i, "공식어들": offs,
                                      "모호어": len(offs) > 1 or any(e.get("모호어") == "Y" for e in es),
                                      "항목": es})
                start = i + 1
        return sorted(found, key=lambda f: f["위치"])


_TRUST = {"낮음": 0, "미분류": 1, "중간": 1, "높음": 2}


def _ok(e, min_strength, min_trust="낮음"):
    if not (e.get("매칭강도") == "강" or min_strength == "중"):
        return False
    return _TRUST.get(e.get("신뢰도", "미분류"), 1) >= _TRUST.get(min_trust, 0)
