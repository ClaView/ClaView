"""Normalize date notation and preserve published rates' requested periods.

No test question, answer code or statistical value is used here. Original queries and
the shared B outcome remain in the run record; changes are explicit notices.
"""
import copy
import re
import unicodedata

from claview.contracts import StageFailure
from claview.stages import resolve_explicit_period, detect_baseline_extension, RELATIVE_WORDS, validate_period
from b_conditions import shifted


def normalize_b_query(query):
    notes=[]
    def month(m):
        notes.append(f"날짜 표기 {m[0]} → {m[1]}년 {int(m[2])}월")
        return f'{m[1]}년 {int(m[2])}월'
    # Do not interpret a complete daily date or an index base such as 2024.12=100 as a month request.
    result=re.sub(r'(?<![\d./-])((?:19|20)\d{2})[-./](0?[1-9]|1[0-2])(?![\d./=-])',month,query)
    def quarter(m):
        notes.append(f'날짜 표기 {m[0]} → {m[1]}년 {m[2]}분기')
        return f'{m[1]}년 {m[2]}분기'
    result=re.sub(r'(?<!\d)((?:19|20)\d{2})\s*[Qq]([1-4])(?!\d)',quarter,result)
    def half(m):
        n=1 if m[2]=='상' else 2
        notes.append(f'날짜 표기 {m[0]} → {m[1]}년 {n}반기')
        return f'{m[1]}년 {n}반기'
    result=re.sub(r'((?:19|20)\d{2})년\s*(상|하)반기',half,result)
    # 화면 레이아웃 '나눠 보기'는 하위 통계 항목을 전부 펼치라는 조건이 아니다.
    # 정확히 해당 UI 표현만 분리하고 '업권별', '세부 항목별' 등 조회 조건은 보존한다.
    def display_only(match):
        notes.append(f"표시 요청 ‘{match[0]}’은 C에서 처리합니다. B에는 조회 조건만 전달하며 원질문은 보존합니다.")
        return "그래프로"
    result=re.sub(r'항목별\s*나눠\s*보기(?:로)?',display_only,result)
    return result,notes


def preserve_rate_periods(query,b_result,store):
    result=copy.deepcopy(b_result)
    if store is None or re.search(RELATIVE_WORDS,query):return result,[]
    changes=[]
    def adjust(row):
        # Rates already published by the source need no earlier level to compute them again.
        unit=unicodedata.normalize('NFKC',row.get('unit') or '')
        if unit!='%' or not re.search(r'성장률|증감률|전년동기비|전기비|등락률|상승률|기여도',row['label']):return
        old=row['period'];freq=old['frequency'];n=detect_baseline_extension(query,freq)
        if not n:return
        table=store.get(row['org_id'],row['table_id'])
        resolved=resolve_explicit_period(query,table,freq)
        if resolved is None:return
        requested={**resolved,'start':shifted(resolved['start'],freq,n)}
        validate_period(requested,table)
        if old==resolved and old!=requested:
            row['period']=requested
            changes.append({'series_id':row['series_id'],'before':copy.deepcopy(old),'after':requested,
                'message':f"{row['label']}: 공표된 증감률을 사용하므로 이전 계산 기간을 덧붙이지 않습니다. {requested['start']}~{requested['end']}을 조회합니다."})
        elif old!=requested:
            raise StageFailure('published_rate_period_mismatch','unresolved')
    for row in result['selections']:adjust(row)
    for alt in result.get('frequency_alternatives',[]):
        for row in alt['selections']:adjust(row)
        if alt['selections']:alt['period']=copy.deepcopy(alt['selections'][0]['period'])
    if changes:result.setdefault('period_notices',[]).extend({'message':c['message']} for c in changes)
    return result,changes
