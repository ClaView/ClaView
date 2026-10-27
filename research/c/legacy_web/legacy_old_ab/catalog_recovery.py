"""A가 놓친 표를 메타정보에서 다시 찾아 '확인용 후보'로만 제공한다.

정답표 번호/질문 번호를 쓰지 않는다. A의 분석을 고치거나 자동 확정하지도 않는다.
조건 단어(지역별, 수출 등)까지 검색하고, 일상어 정의를 공식 용어와 연결한다.
사용자가 확인한 뒤에도 B가 항목·기간을 다시 검증해야 실제 조회할 수 있다.
"""
import re
from claview_v3.select.tables import selection_catalog, norm_v1
from claview_v3.select.candidates import search_catalog


def search_terms(query):
    # 연도/그래프 요청 같은 공통 문구를 검색 근거로 세지 않는다.
    words = re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9]+", query)
    stop = {"최근", "년간", "연간", "연도별", "분기별", "월별", "그래프", "막대그래프",
            "꺾은선", "비교", "비교하는", "보여줘", "만들어줘", "흐름", "어떻게",
            "달랐는지", "추이", "규모", "전체", "국내", "기준", "가격", "물가"}
    tokens = []
    for word in words:
        word = re.sub(r"(?:으로|에서|부터|까지|처럼|보다|와|과|의|을|를|가|이)$", "", word)
        if len(word) >= 2 and word not in stop:
            tokens.append(word)
    # 공식 통계 정의에 기반한 검색어 확장이다. 계산이나 통계값 합산은 하지 않는다.
    # 한국은행 '우리나라의 물가통계(2024)'의 총산출물가지수 설명:
    # 국내 생산품의 국내출하와 수출을 포함하는 물가지수.
    definition = ("물가" in query and "국내" in query and "생산" in query and "수출" in query
                  and any(w in query for w in ("팔", "판매", "출하")))
    if definition:
        tokens.append("총산출물가지수")
    return list(dict.fromkeys(tokens)), definition


def recovery_candidates(query, layer):
    if layer is None:
        return []
    terms, definition = search_terms(query)
    if not terms:
        return []
    views = selection_catalog(layer).views
    search = search_catalog([{"query_id": "recovery", "text": " ".join(terms),
                              "source": "whole_question_metadata"}], layer)
    ranking = {r["table_id"] for channel in ("title", "item") for r in search[channel]["merged"]}
    requested_freq = next((f for word, f in (("월별", "M"), ("분기", "Q"), ("연간", "Y"),
                                            ("연도별", "Y"), ("반기", "H")) if word in query), None)
    found = []
    for tid in ranking:
        v = views[tid]
        if not v.has_meta or not v.org_id or (requested_freq and requested_freq not in v.frequencies()):
            continue
        title = norm_v1(v.title)
        names = [norm_v1(i.name_raw) for i in v.items]
        axes = [norm_v1(n) for _, n in v.dims]
        hits, score = [], 0
        for term in terms:
            key = norm_v1(term)
            # '생산자물가'처럼 지수 생략 표현과 '지역별'처럼 분류 조건도 살린다.
            axis_key = key.removesuffix("별")
            in_title = key in title
            in_item = any(key in name for name in names)
            in_axis = len(axis_key) >= 2 and any(axis_key in a for a in axes)
            if in_title or in_item or in_axis:
                hits.append(term)
                score += (8 if in_title else 0) + (4 if in_item else 0) + (5 if in_axis else 0)
                score += min(len(key), 8) / 10
        definition_hit = definition and "총산출물가지수" in title
        if definition_hit:
            score += 30
        # 단어 하나만 우연히 겹치는 표는 새 후보로 올리지 않는다.
        if len(hits) < 2 and not definition_hit:
            continue
        found.append((score, tid, hits, definition_hit))
    found.sort(key=lambda row: (-row[0], row[1]))
    return [{"table_id": tid, "matched_terms": hits,
             "note": ("국내 생산품의 국내출하·수출을 함께 보는 물가 → 총산출물가지수. 이 해석이 맞는지 확인해 주세요."
                      if defined else "표 제목·분류·항목에서 찾은 단어: " + " · ".join(hits))}
            for _, tid, hits, defined in found[:5]]
