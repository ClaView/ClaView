"""평가 전용 판단 기준. 서비스/추천기는 이 파일을 import하지 않는다.

질문의 목적, 명시 요청, 통계 해석 안전성으로 먼저 작성한 사람의 검토 초안이다.
실행 결과에서 기대값을 복사하거나, '코드와 같음'을 적절성의 근거로 삼지 않는다.
팀의 최종 합의나 실제 사용자 이해도 평가를 대신하는 자료는 아니다.
"""
VERSION = "chart-rubric/2026-10-21-v2"
SOURCES = [
    {"title": "영국 정부 통계 시각화 가이드", "url": "https://analysisfunction.civilservice.gov.uk/policy-store/data-visualisation-charts/"},
    {"title": "ONS 항목별 비교와 막대", "url": "https://service-manual.ons.gov.uk/data-visualisation/chart-types/bar-charts"},
]
KINDS = {"line": "선그래프", "bar": "묶음막대", "small_multiples": "항목별 나눠 보기",
         "horizontal": "한 시점 가로막대", "table": "수치표", "stacked": "누적막대", "percent_stacked": "100% 누적막대"}
SINGLE = ["line", "bar", "table"]
MULTI = ["line", "bar", "small_multiples", "horizontal", "table"]


def criterion(number, purpose, preferred, allowed, reason, caution=""):
    return dict(id=f"CV-AB-{number:02d}", purpose=purpose, preferred=preferred, allowed=allowed,
                forbidden=[k for k in KINDS if k not in allowed], reason=reason, caution=caution)


GOLDEN = [
    criterion(1,"연간 GDP 성장률 흐름",["line"],SINGLE,"시간 변화와 0% 위·아래를 비교. 이미 공표된 성장률을 재계산하지 않음."),
    criterion(2,"연간 투자 증감률 흐름",["line"],SINGLE,"시간 변화·음수·0 기준을 읽기 쉬운 선을 기본 후보로 함."),
    criterion(3,"연간 수출 증감률 흐름",["line"],SINGLE,"시간 변화가 핵심. 정확한 값은 표, 연도별 증감 크기는 막대도 허용."),
    criterion(4,"경상수지 전체와 구성 항목의 흐름 비교",["line"],MULTI,"동일 단위의 다섯 흐름을 비교. 전체와 부분을 함께 쌓으면 중복됨."),
    criterion(5,"서비스수지와 하위 항목의 흐름 비교",["line"],MULTI,"전체와 일부 하위 항목을 같은 축에서 비교하되 합산·비중으로 오해시키지 않음."),
    criterion(6,"분기 가계신용 잔액 흐름",["line"],SINGLE,"오랜 분기 시계열의 방향을 확인. 잔액을 기간별로 더하지 않음."),
    criterion(7,"두 기업대출 금리의 월별 흐름",["line"],MULTI,"사용자의 꺾은선 요청을 존중. 두 금리를 합산하거나 구성비로 만들지 않음."),
    criterion(8,"업권별 가계신용의 분기 비교",["bar"],MULTI,"사용자가 막대를 지정. 동명 항목은 상위 분류로 구분하고 전체 요청 항목을 함께 표시.","14항목×26분기는 혼잡함. 기본 보기는 유지하되 나눠 보기도 허용. 계층이 있다고 합산 가능한 것은 아님."),
    criterion(9,"M2 상품별 연간 구성과 전체 규모",["stacked"],list(KINDS),"누적 요청을 존중하되 공식 전체와 14개 구성항목의 합산 검증이 통과해야 함.","100%는 같은 시점 공식 전체가 분모. 항목 숨김으로 분모를 바꾸지 않음. 검증 실패 시 누적 두 종류 금지."),
    criterion(10,"지역별 수출의 기간별 비교",["bar"],MULTI,"사용자가 막대 요청. 지역별·연도별 원자료를 유지.","10년 합계/연평균/특정 연도 중 하나로 임의 집계하지 않음. 가로막대는 명시한 한 시점을 고른 대안."),
    criterion(11,"분기 GDP 성장률 추이",["line"],SINGLE,"꺾은선 요청과 시간 변화 목적을 존중. 공표 성장률을 다시 증감률로 계산하지 않음."),
    criterion(12,"분기 순대외금융자산 흐름",["line"],SINGLE,"꺾은선 요청을 존중하고 수준·단위를 표시."),
    criterion(13,"세 용도별 대출금의 월별 흐름",["line"],MULTI,"동일 단위의 세 계열 흐름 비교. 용도 간 배타적 구성이라는 증거 없이 누적 금지."),
    criterion(14,"연도별 정확한 수치표",["table"],SINGLE,"사용자가 표를 요청했으므로 표가 첫 보기. 사용자가 바꿀 때만 대안 그래프 허용."),
    criterion(15,"월별 경제심리지수 흐름",["line"],SINGLE,"월별 지수 수준의 방향을 확인. 지수 값을 합산하지 않음."),
    criterion(16,"두 생산자물가 지수의 월별 비교",["line"],MULTI,"두 계열의 방향·격차를 비교. 지수는 구성금액처럼 더하지 않음."),
    criterion(17,"신선식품과 제외 지수의 비교",["line"],MULTI,"동일 기준의 두 지수 흐름 비교. 두 지수를 합쳐 전체 지수로 보지 않음."),
    criterion(18,"변화가 가장 큰 달 찾기",["bar"],SINGLE,"기존 전월 변화율 절댓값 최대 정책을 유지하고 변화율·해당 월을 표시.","주 평가 대상은 계산 카드. 원지수는 별도 카드. 기준월 누락 시 최대 달을 단정하지 않음."),
    criterion(19,"원화기준 두 수입물가 흐름",["line"],MULTI,"같은 통화·지수 기준의 시간 비교. 서로 다른 통화/기준이면 같은 축 비교 금지."),
    criterion(20,"두 수출 품목의 연간 전년 대비 증감",["bar"],["bar","horizontal","table"],"기존 연간 비교 정책을 유지하고 두 증감률을 막대로 비교.","주 평가 대상은 계산 카드. 한 시점의 계산값을 선으로 연결하지 않음. 가로막대도 의미상 허용하나 현재 계산 카드에는 미구현. 원지수 2년치는 별도 카드."),
    criterion(21,"공급단계별 세 물가 흐름",["line"],MULTI,"세 지수의 동일 기준 흐름 비교. 원재료·중간재·최종재 지수를 단순 합산하지 않음."),
    criterion(22,"총산출물가의 월별 흐름",["line"],SINGLE,"월별 전체 지수의 시간 변화를 표시. 지수 수준과 가격 금액을 혼동하지 않음."),
]

# 새 문장/명시 표현에 대한 평가. source는 오프라인 C 규칙 검사의 실제 저장 자료 출처.
# live=True 8건은 별도로 A→B→KOSIS 실제 조회까지 진행한다.
NEW = [
    dict(id="P01",live=True,source=16,table="DT_404Y014",query="2024년 공산품과 서비스의 생산자물가를 연간 값으로 가로 막대그래프로 비교해줘.",preferred=["horizontal"],allowed=["horizontal","bar","table"],shape="annual"),
    dict(id="P02",live=True,source=16,table="DT_404Y014",query="2024년 공산품과 서비스의 생산자물가를 월별로 항목별 나눠 보기로 보여줘.",preferred=["small_multiples"],allowed=MULTI),
    dict(id="P03",live=True,source=16,table="DT_404Y014",query="2024년 공산품과 서비스의 생산자물가를 월별 표 형식으로 보여줘.",preferred=["table"],allowed=MULTI),
    dict(id="P04",live=True,source=9,table="DT_161Y008",query="2021년부터 2025년까지 M2 통화량(원계열, 말잔 기준)을 상품별 구성비 100% 차트로 보여줘.",preferred=["percent_stacked"],allowed=list(KINDS)),
    dict(id="P05",live=True,source=7,table="DT_121Y006",query="2024년 예금은행 대기업대출과 중소기업대출 금리를 신규취급액 기준으로 월별 누적막대그래프로 보여줘.",preferred=["line"],allowed=MULTI,notice="chart_request_not_available"),
    dict(id="P06",live=True,source=16,table="DT_404Y014",query="2024년 공산품과 서비스의 생산자물가를 월별 비교 그래프로 보여줘.",preferred=["line"],allowed=MULTI),
    dict(id="P07",live=True,source=16,table="DT_404Y014",query="2024년 공산품과 서비스의 생산자물가를 연간 값으로 선그래프로 비교해줘.",preferred=["bar","horizontal"],allowed=["bar","horizontal","table"],shape="annual",notice="chart_request_not_available"),
    dict(id="P08",live=True,source=16,table="DT_404Y014",query="2024년 공산품과 서비스의 생산자물가를 월별 원그래프로 비교해줘.",preferred=["line"],allowed=MULTI,notice="unsupported_chart_request"),
    dict(id="P09",source=16,query="2024년 두 생산자물가를 수치표 말고 선그래프로 보여줘.",preferred=["line"],allowed=MULTI),
    dict(id="P10",source=16,query="2024년 두 생산자물가를 막대그래프 말고 선그래프로 비교해줘.",preferred=["line"],allowed=MULTI),
    dict(id="P11",source=16,query="2024년 두 생산자물가를 선그래프 말고 막대그래프로 비교해줘.",preferred=["bar"],allowed=MULTI),
    dict(id="P12",source=16,query="2024년 두 생산자물가를 월별 데이터 테이블로 정리해줘.",preferred=["table"],allowed=MULTI),
    dict(id="P13",source=8,query="마지막 분기 업권별 가계신용을 가로막대로 비교해줘.",preferred=["horizontal"],allowed=["bar","horizontal","table"],shape="last"),
    dict(id="P14",source=16,query="2024년 두 생산자물가를 월별 가로막대로 보여줘.",preferred=["line"],allowed=MULTI,notice="chart_request_not_available"),
    dict(id="P15",source=9,query="2021~2025년 M2 상품별 구성비를 보여줘.",preferred=["percent_stacked"],allowed=list(KINDS)),
    dict(id="P16",source=9,query="M2 상품 일부만으로 구성비 100% 누적막대를 보여줘.",preferred=["line"],allowed=MULTI,shape="subset",notice="chart_request_not_available"),
    dict(id="P17",source=8,query="업권별 가계신용 14개 항목을 각각 나눠 선그래프로 보여줘.",preferred=["small_multiples"],allowed=MULTI),
    dict(id="P18",source=16,query="두 생산자물가의 월별 흐름을 산점도로 보여줘.",preferred=["line"],allowed=MULTI,notice="unsupported_chart_request"),
    dict(id="P19",source=16,query="두 생산자물가를 선그래프와 막대그래프로 둘 다 보여줘.",preferred=["line","bar"],allowed=MULTI,notice="multiple_chart_requests"),
    dict(id="P20",source=9,query="M2 구성을 100% 누적막대로 보여줘.",preferred=["line"],allowed=MULTI,shape="missing_total",notice="chart_request_not_available"),
    dict(id="P21",source=8,query="업권별 가계신용을 그래프로 비교해줘.",preferred=["line"],allowed=MULTI),
    dict(id="P22",source=16,query="생산자물가를 표 형식 말고 선그래프로 보여줘.",preferred=["line"],allowed=MULTI),
]
