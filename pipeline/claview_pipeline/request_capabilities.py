"""Reject observed unsupported intents with catalog evidence before offering misleading tables."""
import re


def unsupported_request(query,catalog=None):
    """catalog: the B MetadataStore catalog (dict of docs with a 'title'); None skips the catalog check."""
    if re.search(r'예측해|예측하(?:라|세요|고|여)|예상해|주가.*전망해',query):
        return {'code':'forecast_not_supported','status':'unsupported',
                'message':'현재는 발표된 통계를 조회합니다. 미래 주가·통계값 예측은 지원하지 않습니다. 조회할 통계명과 과거 기간을 입력해 주세요.'}
    if re.fullmatch(r'\s*(?:그럼|그러면)?\s*(?:작년|올해|최근|이전)(?:만|으로)?\s*(?:보여줘|해줘)[.!?]?\s*',query):
        return {'code':'followup_subject_missing','status':'needs_confirmation',
                'message':'어떤 통계인지 함께 입력해 주세요. 현재는 앞 질문의 통계명을 자동으로 이어받지 않습니다. 예: 2024년 연간 실질 GDP 성장률을 보여줘.'}
    fx=re.search(r'원\s*[/·ㆍ-]?\s*달러|달러\s*[/·ㆍ-]?\s*원|엔화|유로화',query) and '환율' in query
    if fx and catalog is not None and not re.search('경영애로|애로사항|설문|응답.*비중',query):
        exchange_tables=[d['table_id'] for d in catalog.values() if '환율' in d['title']]
        if not exchange_tables:
            return {'code':'exchange_rate_catalog_missing','status':'unsupported',
                    'message':'현재 연결한 한국은행 KOSIS 통계표에는 요청한 환율 시계열이 없습니다. 경영애로사항의 ‘환율’ 응답 비중은 환율 값과 다른 통계이므로 대신 제시하지 않습니다.',
                    'evidence':{'catalog_table_count':len(catalog),'exchange_rate_tables':exchange_tables}}
    return None
