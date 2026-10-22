"""EDA v3 item 2b: recommended decisions for the axis-role confirmation sheet (recommendations only, not approved).

Reads runs/eda_v3/2_axis_roles/confirm_sheet.csv (181 rows) and adds four columns per row:
recommended, confidence (high|medium|low), evidence (meta facts), note (why it differs from the draft).
The decision column stays empty for the user.

How a row is decided
- Batch rules (written in summary_recommend.md) decide a row from meta facts only: value names and their English
  names (ITM_NM_ENG), target dictionary ratio, table name, the other axes of the table, hierarchy, units.
- Rows the rules cannot settle carry a per-row judgment (JUDGED below). These are analysis records for the user's
  review, not product logic; each one states its reason, and the evidence column still shows the meta facts.

Inputs: confirm_sheet.csv, layer2/layer4 CSVs of the same folder, the user's draft CSVs, the stored meta snapshot.
Not read: questions of any dataset (no question cross-check in this step), .env, network, models.

Usage (repo root): PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 scripts/a/eda_v3/e2b_recommend.py
"""

from __future__ import annotations

import collections
import re

from common import DRAFT, OUT, axes_of, clean_value, load_catalog, load_meta, paren_front, read_csv, write_csv
from roles import AxisRoles, base, basis_only, is_combined, is_total_key

DIR = OUT / "2_axis_roles"
FIELDS = ["구분", "표 ID", "표 이름", "축 이름", "값", "지금 판정", "근거",
          "recommended", "confidence", "evidence", "note", "decision"]

# ---------------------------------------------------------------- meta signals (language lists, not case tuned)

# English value names that point at a target dimension: age, sex, region/country, industry, institution/sector.
_ENG_TARGET = re.compile(
    r"(?i)(\b\d0s\b|and over|\bmale\b|\bfemale\b|region|province|metropolitan|seoul|busan|daegu|incheon|gwangju|"
    r"daejeon|ulsan|sejong|gyeonggi|gangwon|chung(?:cheong|buk|nam)|jeon(?:buk|nam)|jeolla|gyeong(?:buk|nam)|jeju|"
    r"\basia|asean|europe|european union|america|middle east|\bworld\b|china|japan|taiwan|russia|vietnam|india|"
    r"indonesia|singapore|australia|brazil|\buk\b|united kingdom|united states|u\.s\.a|germany|france|hong kong|"
    r"canada|mexico|switzerland|iran|belgium|thailand|sweden|spain|ireland|italy|netherlands|philippines|saudi|"
    r"u\.a\.e|jordan|libya|nations?\b|nationality|"
    r"manufactur|agricultur|mining|construction|wholesale|real estate|accommodation|information and communication|"
    r"electricity|water supply|human health|education|arts|public admin|business (?:activities|support|facilities)|"
    r"^services?$|fishing|forestry|all industr|"
    r"bank|credit union|mutual|insurance co|life insu|corporations?\b|government|households|npish|pension|trust|"
    r"investment fund|public (?:sector|corporations)|financial (?:institution|intermediar|auxiliar|compan|corporation)|cooperative|postal|"
    r"enterprises|merchant|non-?bank|other sectors?|small businesses|individuals|nonresident|rest of the world|"
    r"money issuers|depository|institution|private|employ)")
_ENG_BASIS = re.compile(r"(?i)^(?:[\d.\-a-zⅰ-ⅹ]+\s*)?(short-term|long-term|domestic currency|foreign currency|"
                        r"domestic market|foreign market|seasonally adjusted|original series)$")
# Table-name words that name the dimension of a generic axis, and the layer-1 target axes that already carry it.
_TITLE_DIM = {"지역별": ("지역코드별",), "업권별": ("보고기관별", "금융기관 유형코드별", "기관코드별"),
              "기관별": ("보고기관별", "금융기관 유형코드별", "기관코드별"), "산업별": ("업종코드별",),
              "경제활동별": ("업종코드별",), "부문별": ("제도부문코드별", "제도부문별")}
_INDUSTRY_TITLE = re.compile(r"경제활동별|산업별")
_INDEX_TITLE = re.compile(r"물가지수|금액지수|물량지수|산출물가지수")
_TARGET_TITLE = re.compile(r"경제활동별|산업별|주체별|부문별|기관형태별|지역별|국가별|업권별|기관별")
_GROUP_HEAD = re.compile(r"별$|구분$")


def eng_target(eng: str | None) -> bool:
    return bool(eng) and bool(_ENG_TARGET.search(eng))


def eng_basis(eng: str | None) -> bool:
    return bool(eng) and bool(_ENG_BASIS.match(eng.strip()))


# ---------------------------------------------------------------- per-row judgments (rule exceptions)
# key: (section, table ID, axis name or value) -> (recommended, confidence, reason). Sections: "1".."5".
# Reasons cite meta facts; the generated evidence column adds the measured facts.
JUDGED: dict[tuple[str, str, str], tuple[str, str, str]] = {
    # 2 boundary axes
    ("2", "DT_151Y001", "계정항목별"): (
        "눌린 축(위 지표: 가계신용·가계대출·판매신용 / 아래 대상: 예금은행·상호저축은행 등 기관)", "medium",
        "뿌리 가계신용 아래 가계대출·판매신용(영문 Loans to households / Merchant credit)이 있고 그 아래가 기관이다. "
        "기관은 두 부모 아래 갈려 있어(여신전문기관이 두 부모에 반복) 반복 묶음 조건(2개 이상)을 못 채워 4층에서 빠졌다."),
    ("2", "DT_151Y006", "계정항목별"): (
        "눌린 축(같은 단계 섞임: 주택관련대출·기타대출 = 지표 / 상호저축은행·신용협동조합·상호금융·새마을금고 = 대상)", "medium",
        "뿌리 비은행예금취급기관 바로 아래에 용도 2개(영문 Household housing-related loans / Others)와 기관 5개가 형제로 놓여 "
        "두 차원이 한 단계에 섞였다. 지역은 지역코드별 축이 따로 맡는다."),
    ("2", "DT_514Y002", "계정코드별"): (
        "대상 축(국내은행·비은행금융기관과 그 아래 기관·차주; '국내은행-대기업'은 대상+대상 결합)", "medium",
        "값 10개가 모두 기관 또는 기관-차주다(영문 Domestic Banks / Bank - Large Corporations / Mutual Savings Banks). "
        "지표는 표 이름(신용위험)이다. '국내은행-차주가중종합지수'는 국내은행 전체 지수라 합계 머리로 읽는다."),
    ("2", "DT_303Y004", "계정코드별"): (
        "대상 축(값 = 지역 × 결제통화 결합, 둘 다 대상; 뿌리 '수입' = 지표)", "medium",
        "값이 '달러 (아세안)'꼴로 통화와 지역을 함께 담는다(영문 US Dollar (Asean)). 수치 단위는 금액/비중 구분별 축"
        "(백만달러·%)이 정하므로 통화는 재는 단위가 아니라 거래를 가르는 구분이다."),
    ("2", "DT_303Y001", "계정코드별"): (
        "대상 축(값 = 지역 × 결제통화 결합, 둘 다 대상; 뿌리 '수출' = 지표)", "medium",
        "값이 '달러 (아세안)'꼴로 통화와 지역을 함께 담는다(영문 US Dollar (Asean)). 수치 단위는 금액/비중 구분별 축"
        "(백만달러·%)이 정하므로 통화는 재는 단위가 아니라 거래를 가르는 구분이다."),
    ("2", "DT_303Y005", "계정코드별"): (
        "대상 축(값 = 국가 × 결제통화 결합, 둘 다 대상; 뿌리 '수입' = 지표)", "medium",
        "부모가 국가(대만·러시아, 영문 Taiwan / Russia), 자식이 '달러 (대만)'꼴 통화다. 수치 단위는 금액/비중 구분별 축"
        "(백만달러·%)이 정한다. 국가 이름이 대상 사전(지역코드별 값)에 없어 비율이 47%로 낮게 나왔다."),
    ("2", "DT_303Y002", "계정코드별"): (
        "대상 축(값 = 국가 × 결제통화 결합, 둘 다 대상; 뿌리 '수출' = 지표)", "medium",
        "부모가 국가(대만·러시아, 영문 Taiwan / Russia), 자식이 '달러 (대만)'꼴 통화다. 수치 단위는 금액/비중 구분별 축"
        "(백만달러·%)이 정한다. 국가 이름이 대상 사전(지역코드별 값)에 없어 비율이 47%로 낮게 나왔다."),
    ("2", "DT_200Y151", "금액구분코드별"): (
        "지표 축(값 = 부문-지출항목 결합, 3층 결합으로 나눠 읽음)", "high",
        "값 44개가 모두 '일반정부-피용자보수(1)'꼴(영문 General government - Compensation of employees)이다. "
        "앞(일반정부·중앙정부·지방정부)은 대상, 뒤는 지표다."),
    ("2", "DT_200Y152", "금액구분코드별"): (
        "지표 축(값 = 부문-지출항목 결합, 3층 결합으로 나눠 읽음)", "high",
        "값 52개가 모두 '일반정부-피용자보수'꼴(영문 General government - Compensation of employees)이다. "
        "앞(일반정부·중앙정부·지방정부)은 대상, 뒤는 지표다."),
    ("2", "DT_200Y151", "계정항목별"): (
        "지표 축(정부 기능 분류 = 구성 항목)", "medium",
        "값이 일반공공행정·국방·교육 등 정부 기능 10개 + 합계(영문 General public services / Defense)다. "
        "지출을 용도로 나눈 구성 항목이라 확인표 5번 권장(구성 항목은 지표)과 맞춘다. 대상으로 볼 여지도 있다."),
    ("2", "DT_200Y152", "계정항목별"): (
        "지표 축(정부 기능 분류 = 구성 항목)", "medium",
        "값이 일반공공행정·국방·교육 등 정부 기능 10개 + 합계(영문 General public services / Defense)다. "
        "지출을 용도로 나눈 구성 항목이라 확인표 5번 권장(구성 항목은 지표)과 맞춘다. 대상으로 볼 여지도 있다."),
    ("2", "DT_200Y116", "계정항목별"): (
        "눌린 축(위 지표: 요소비용국민소득·기업 및 재산소득·국민처분가능소득 등 / 아래 대상: 법인·일반정부·가계 및 비영리단체)",
        "medium",
        "부문 값이 'a.법인'·'b.법인'·'c.법인'처럼 부모마다 앞 글자가 달라(영문은 모두 Corporations) 같은 이름으로 묶이지 않아 "
        "4층에서 빠졌다. 영문으로 보면 같은 부문 3개가 소득 항목 3개 아래 반복된다."),
    ("2", "DT_200Y111", "계정항목별"): (
        "대상 축(업종; 끝의 총부가가치·순생산물세·국내총생산은 지표/합계 머리)", "medium",
        "값 18개 중 15개가 업종(영문 Agriculture forestry and fishing / Mining and quarrying), "
        "3개가 총량(영문 Gross value added / Gross domestic product)이다."),
    ("2", "DT_200Y123", "계정항목별"): (
        "대상 축(업종; 총부가가치·순생산물세·국내총생산은 지표/합계 머리)", "medium",
        "값 대부분이 업종(영문 Manufacturing / Construction)이고 총량 3개(총부가가치·순생산물세·국내총생산)가 같은 단계에 있다."),
    ("2", "DT_200Y124", "계정항목별"): (
        "대상 축(업종; 총부가가치·순생산물세·국내총생산은 지표/합계 머리)", "medium",
        "값 대부분이 업종(영문 Manufacturing / Construction)이고 총량 3개(총부가가치·순생산물세·국내총생산)가 같은 단계에 있다."),
    # 3 pressed axes
    ("3", "DT_104Y016", "계정항목별"): (
        "위 지표(총대출금·금융자금·재정자금 등 자금 원천·대출 종류) / 아래 지표(시설자금·운전자금 등 용도)", "medium",
        "두 단계 모두 대출의 종류다(영문 Loans With Banking Funds / Equipment / Operation). 용도는 1번 자금구분별 권장(지표)과 맞춘다."),
    ("3", "DT_104Y013", "계정항목별"): (
        "위 지표(수신합계·비거주자예수금) / 아래 지표(원화예금·외화예금)", "medium",
        "아래 단계는 예금 종류다(영문 Deposits in Domestic Currency / Foreign Currency). 이름 속 원화·외화는 기준어지만 값 전체는 지표다."),
    ("3", "DT_104Y014", "계정항목별"): (
        "위 지표(수신합계·비거주자예수금) / 아래 지표(원화예금·외화예금)", "medium",
        "아래 단계는 예금 종류다(영문 Deposits in Domestic Currency / Foreign Currency). 이름 속 원화·외화는 기준어지만 값 전체는 지표다."),
    ("3", "DT_281Y004", "계정항목별"): (
        "위 기준+지표 결합(단기채권·장기채권: 단기·장기 = 기준) / 아래 지표(국공채·금융채)", "medium",
        "부모 영문이 Short-term Securities / Long-term Securities, 자식이 Government ＆ Public Bonds다. 만기가 위 단계를 가른다."),
    ("3", "DT_281Y005", "계정항목별"): (
        "위 기준+지표 결합(단기채권·장기채권: 단기·장기 = 기준) / 아래 지표(국공채·금융채)", "medium",
        "부모 영문이 Short-term Securities / Long-term Securities, 자식이 Government ＆ Public Bonds다. 만기가 위 단계를 가른다."),
    ("3", "DT_281Y006", "계정항목별"): (
        "위 지표(대출금·채권·예금 묶음, 단기·장기가 붙은 값은 기준+지표 결합) / 아래 지표(국채·금융채·기업어음 등)", "medium",
        "부모에 단기채권·장기채권·단기대출금·장기대출금(만기)과 결제성·비결제성 예금(예금 종류)이 섞여 있다. 아래 단계는 금융상품이다."),
    ("3", "DT_281Y007", "계정항목별"): (
        "위 지표(대출금·채권·예금 묶음, 단기·장기가 붙은 값은 기준+지표 결합) / 아래 지표(국채·금융채·기업어음 등)", "medium",
        "부모에 단기채권·장기채권·단기대출금·장기대출금(만기)과 결제성·비결제성 예금(예금 종류)이 섞여 있다. 아래 단계는 금융상품이다."),
    ("3", "DT_281Y001", "계정코드별"): (
        "위 기준(단기·장기) / 아래 지표(국채·금융채·기업어음 등)", "medium",
        "부모 단기채권·장기채권·단기대출금·장기대출금의 영문이 '1) Short-term' / '2) Long-term'뿐이다. 자식은 금융상품이다."),
    ("3", "DT_281Y002", "계정코드별"): (
        "위 기준(단기·장기) / 아래 지표(국채·금융채·기업어음 등)", "medium",
        "부모 단기채권·장기채권·단기대출금·장기대출금의 영문이 '1) Short-term' / '2) Long-term'뿐이다. 자식은 금융상품이다."),
    ("3", "DT_282Y004", "계정항목별"): (
        "맨 위 합계 머리(통화별·만기별·발행시장별 분류 머리) / 가운데 대상(거주자·비거주자 발행) / 아래 기준(원화·외화, 단기·장기, 국내·국외시장)",
        "medium",
        "세 단계다: Currency / Maturity / Market of Issuance 아래 Resident Issuer / Non-resident Issuer, 그 아래 "
        "Domestic Currency·Short Term·Domestic Market. 발행자 거주성을 대상으로 볼지 기준으로 볼지는 확인이 필요하다."),
    ("3", "DT_282Y005", "계정항목별"): (
        "맨 위 합계 머리(통화별·만기별·발행시장별 분류 머리) / 가운데 대상(거주자·비거주자 발행) / 아래 기준(원화·외화, 단기·장기, 국내·국외시장)",
        "medium",
        "세 단계다: Currency / Maturity / Market of Issuance 아래 Resident Issuer / Non-resident Issuer, 그 아래 "
        "Domestic Currency·Short Term·Domestic Market. 발행자 거주성을 대상으로 볼지 기준으로 볼지는 확인이 필요하다."),
    ("3", "DT_311Y004", "계정코드별"): (
        "위 대상(A.일반정부·B.중앙은행·C.예금취급기관·D.기타부문) / 가운데 기준(단기·장기) / 아래 지표(증권발행·차입금·현금및예금·무역신용)",
        "high",
        "세 단계다: 대외채무 아래 A~D 부문(영문 General government / Central bank), 그 아래 'A-1.단기'(Short-term), "
        "그 아래 'A-1.1.증권발행'(Debt securities). 부문 이름은 앞 글자 'A.' 때문에 사전과 맞지 않아 대상 비율이 0으로 나왔다."),
    ("3", "DT_311Y005", "계정코드별"): (
        "위 대상(A.일반정부·B.중앙은행·C.예금취급기관·D.기타부문) / 가운데 기준(단기·장기) / 아래 지표(현금및예금·대출금·무역신용·증권투자)",
        "high",
        "세 단계다: 대외채권 아래 A~D 부문(영문 General government / Central bank), 그 아래 단기·장기(Short-term), "
        "그 아래 상품. 부문 이름은 앞 글자 'A.' 때문에 사전과 맞지 않아 대상 비율이 0으로 나왔다."),
    ("3", "DT_311Y006", "계정코드별"): (
        "위 대상(A.일반정부·B.중앙은행·C.예금취급기관·D.기타부문) / 아래 기준(단기·장기)", "high",
        "부모가 부문(영문 General government / Central bank / Other Sectors), 자식이 'A-1.단기'(Short-term)다."),
    ("3", "DT_311Y002", "계정코드별"): (
        "위 지표(대외금융자산·대외금융부채) / 아래 지표(직접투자·증권투자·파생금융상품·기타투자)", "medium",
        "부모 영문 Assets / Liabilities, 자식 Direct Investment / Portfolio Investment. 자산·부채는 기준(1층 자산/부채별)으로 볼 "
        "여지가 있으나 대외금융자산·부채는 그 자체로 통계 이름이다. 지역은 지역코드별 축이 맡는다."),
    ("3", "DT_311Y003", "계정코드별"): (
        "위 지표(대외금융자산·대외금융부채) / 아래 지표(직접투자·증권투자·파생금융상품·기타투자)", "medium",
        "부모 영문 Assets / Liabilities, 자식 Direct Investment / Portfolio Investment. 자산·부채는 기준(1층 자산/부채별)으로 볼 "
        "여지가 있으나 대외금융자산·부채는 그 자체로 통계 이름이다."),
    ("3", "DT_303Y005", "계정코드별"): (
        "위 대상(국가: 대만·러시아·베트남 등) / 아래 대상(결제통화: 달러·엔화·원화·유로화)", "medium",
        "부모 영문이 국가(Taiwan / Russia), 자식이 'US Dollar (Taiwan)'꼴이다. 수치 단위는 금액/비중 구분별 축(백만달러·%)이 정하므로 "
        "통화는 재는 기준이 아니라 거래를 가르는 구분이다."),
    ("3", "DT_303Y002", "계정코드별"): (
        "위 대상(국가: 대만·러시아·베트남 등) / 아래 대상(결제통화: 달러·엔화·원화·유로화)", "medium",
        "부모 영문이 국가(Taiwan / Russia), 자식이 'US Dollar (Taiwan)'꼴이다. 수치 단위는 금액/비중 구분별 축(백만달러·%)이 정하므로 "
        "통화는 재는 기준이 아니라 거래를 가르는 구분이다."),
    ("3", "DT_303Y001", "계정코드별"): (
        "위 대상(지역: 아세안·EU·미국·일본 등) / 아래 대상(결제통화: 달러·엔화·원화·유로화)", "medium",
        "재계산은 축 전체를 대상 축으로 봐서 눌린 축 목록에서 뺐다. 단계로 읽어도 위는 지역(영문 Asean / European Union), "
        "아래는 통화(US Dollar (Asean))라 둘 다 대상이다. 결론은 2번 행(대상 축)과 같다."),
    ("3", "DT_303Y004", "계정코드별"): (
        "위 대상(지역: 아세안·EU·미국·일본 등) / 아래 대상(결제통화: 달러·엔화·원화·유로화)", "medium",
        "재계산은 축 전체를 대상 축으로 봐서 눌린 축 목록에서 뺐다. 단계로 읽어도 위는 지역(영문 Asean / European Union), "
        "아래는 통화(US Dollar (Asean))라 둘 다 대상이다. 결론은 2번 행(대상 축)과 같다."),
    ("3", "DT_200Y121", "계정항목별"): (
        "위 합계 머리(소득발생계정·본원소득분배계정 등 계정 묶음) / 아래 지표(피용자보수·영업잉여 등)", "medium",
        "같은 항목이 계정마다 반복되고 '(a)'·'(b)' 꼬리로 계정을 구분한다(영문 Generation of income account / "
        "Allocation of primary income account). 계정은 같은 항목을 가르는 맥락이라 질문에서는 지표에 붙여 읽는다."),
    ("3", "DT_200Y118", "계정항목별"): (
        "위 지표(재화와 서비스의 수출·수입) / 아래 지표(재화·서비스 구성 항목)", "medium",
        "자식 영문 Goods(F.O.B.) / Services. 구성 항목은 5번 권장(지표)과 맞춘다. 요구 수를 셀 때 분해 항목으로 읽는다(상세 설계 2.5)."),
    ("3", "DT_200Y101", "계정항목별"): (
        "맨 위 합계 머리(경제규모 및 국민소득·경제활동별 성장률 등 묶음) / 가운데 지표(국내총생산·최종소비지출 등) / "
        "아래 대상(업종·민간·정부·제도부문)과 지표가 섞임", "low",
        "값마다 단위가 다르다(십억원·억달러·만원·달러·%). 묶음 머리 아래 지표가 있고, 그 아래에 업종(농림어업·건설업)·주체(민간·정부)·"
        "제도부문(가계·기업·정부)과 하위 지표(건설투자·설비투자)가 함께 놓여 단계 하나로 역할을 정할 수 없다."),
    # 4 dual-use values: per-table context the batch rules do not settle
    ("4", "DT_101Y001", "MMF"): ("지표", "high", "M2 상품별 구성내역: 형제가 현금통화·요구불예금·수익증권 등 상품이다."),
    ("4", "DT_101Y002", "MMF"): ("지표", "high", "M2 상품별 구성내역: 형제가 현금통화·요구불예금·수익증권 등 상품이다."),
    ("4", "DT_101Y003", "MMF"): ("지표", "high", "M2 상품별 구성내역: 형제가 현금통화·요구불예금·수익증권 등 상품이다."),
    ("4", "DT_101Y004", "MMF"): ("지표", "high", "M2 상품별 구성내역: 형제가 현금통화·요구불예금·수익증권 등 상품이다."),
    ("4", "DT_281Y004", "신탁"): ("지표", "high", "'(6) 신탁' 영문 Money in Trust, 부모 '3. 기타예금' 아래 예금 상품이다."),
    ("4", "DT_281Y005", "신탁"): ("지표", "high", "'(6) 신탁' 영문 Money in Trust, 부모 '3. 기타예금' 아래 예금 상품이다."),
    ("4", "DT_111Y002", "예금취급기관"): (
        "대상", "medium", "Lf 상품별 구성내역에서 상품 9개를 묶는 기관 머리다(영문 Depository Corporations, 자식 9개)."),
    ("4", "DT_111Y004", "예금취급기관"): (
        "대상", "medium", "Lf 상품별 구성내역에서 상품을 묶는 기관 머리다(영문 Depository Corporations)."),
    ("4", "DT_304Y101", "정보 통신서비스"): (
        "지표", "medium", "지식서비스 무역통계(유형별): 형제가 지식재산권 사용료·문화 여가서비스·전문 사업서비스 등 서비스 유형이다. "
        "업종 이름(정보통신업)과 겹칠 뿐 여기서는 무역 서비스 품목이다."),
}

SECTION1_RULE = {  # axis name -> (recommended, confidence, reason); axis-name rule, applies to every table with it
    "자금구분별": ("지표", "medium",
              "값 2개 시설자금·운전자금(영문 Facilities Investment / Working Capital), 표 이름 '용도별'(영문 By Purpose). "
              "대출금을 용도로 나눈 구성 항목이라 5번 권장(구성 항목은 지표)과 맞춘다. 같은 값이 예금은행 대출금(DT_104Y016)·"
              "지역별 대출금(DT_141Y003)의 계정항목별 축에서는 지표로 판정돼 있다. 기준(조건)으로 볼 여지도 있다."),
    "분류체계별": ("대상", "high",
              "값 2개 법인·비법인(영문 Incorporations / Unincorporations), 표 이름 '기업형태별'. 차주 기업의 형태라 "
              "1층 대상 축 기업규모별(대기업·중소기업)과 같은 성격이다."),
    "분위 구분코드별": ("기준", "high",
                "값 4개 평균값·1분위·2분위(중위수)·3분위(영문 Mean / First quartile / median). 같은 지표를 분포의 어느 값으로 "
                "냈는지 가르는 통계량이라 조건(기준)이다. 업종·규모는 업종코드별·기업규모별 축이, 지표는 계정항목별 축이 맡는다."),
}
# Draft md (section 3, 4층 table) gives stage roles for four tables; matched by table-name prefix.
DRAFT_MD_STAGES = {"예금은행 원화예금(예금주별": "위 지표 / 아래 대상", "예금취급기관 개관표": "위 대상 / 아래 지표",
                   "국가별 결제통화": "위 대상 / 아래 기준", "대외채무": "위 기준 / 아래 지표"}


def stage_role(names, lookup, R) -> tuple[str, float, float]:
    """Role of one stage of a pressed axis from dictionary hits, English names and basis words."""
    keys = [base(n) for n in names if base(n)]
    if not keys:
        return "지표", 0.0, 0.0
    tgt = sum(1 for k in keys if k in R.target_dict or eng_target(lookup.get(k))) / len(keys)
    bas = sum(1 for k in keys if basis_only(k, R.basis_vocab) or eng_basis(lookup.get(k))) / len(keys)
    return ("대상" if tgt >= 0.5 else "기준" if bas >= 0.5 else "지표"), tgt, bas


def main() -> None:
    meta, catalog = load_meta(), load_catalog()
    R = AxisRoles(meta)
    title = {tid: (catalog.get(tid, {}).get("title") or t["name"]) for tid, t in meta.items()}
    path = {tid: catalog.get(tid, {}).get("path", "") for tid in meta}
    axes = {(ax["tid"], ax["name"]): ax for ax in R.axes}
    sheet = read_csv(DIR / "confirm_sheet.csv")
    l2 = {(r["표 ID"], r["축 이름"]): r for r in read_csv(DIR / "layer2_generic_axes.csv")}
    l4 = {r["표 ID"]: r for r in read_csv(DIR / "layer4_pressed_axes.csv")}
    draft_l2 = {(r["표 ID"], r["축 이름"]): r for r in read_csv(DRAFT / "일반축_축마다_판정.csv")}
    draft_l1 = {r["축 이름"]: r["역할 초안"] for r in read_csv(DRAFT / "축이름_역할_초안.csv")}
    used_rules = collections.Counter()
    out = []

    def ex_eng(vals, n=5, skip_total=True):
        picked = [v for v in vals if not (skip_total and is_total_key(base(v.get("ITM_NM"))))][:n]
        return " / ".join(f"{clean_value(v.get('ITM_NM'))}({v.get('ITM_NM_ENG') or '-'})" for v in picked)

    def depth_of(vals):
        ids = {v["ITM_ID"]: v for v in vals}
        best = 0
        for v in vals:
            d, cur = 0, v
            while cur.get("UP_ITM_ID") in ids and d < 12:
                cur, d = ids[cur["UP_ITM_ID"]], d + 1
            best = max(best, d)
        return best + 1

    def units_txt(vals):
        u = sorted({v.get("UNIT_NM") for v in vals if v.get("UNIT_NM")})
        return f"값마다 단위 다름({'·'.join(u[:5])})" if len(u) > 1 else ""

    def other_axes(tid, name):
        return ", ".join(f"{a['name']}({len(a['values'])}, 1층 {a['layer1']})" for a in R.axes
                         if a["tid"] == tid and a["name"] != name)

    def same_stem(tid, name):
        stem = paren_front(title[tid])
        hits = [(a["tid"], a) for a in R.axes if a["name"] == name and a["tid"] != tid
                and paren_front(title[a["tid"]]) == stem]
        if not hits:
            return ""
        roles = collections.Counter(f"{'대상' if a['role'] == 'target' else '지표'} {a['ratio']:.0%}" for _t, a in hits)
        return f"같은 표 이름 앞부분('{stem}') {len(hits)}표의 같은 축: " + ", ".join(f"{k} {v}표" for k, v in roles.items())

    def eng_ratio(vals):
        vs = [v for v in vals if base(v.get("ITM_NM")) and not is_total_key(base(v.get("ITM_NM")))]
        return (sum(1 for v in vs if eng_target(v.get("ITM_NM_ENG"))) / len(vs)) if vs else 0.0

    for row in sheet:
        sec = row["구분"][0]
        tid, axname, val = row["표 ID"], row["축 이름"], row["값"]
        rec = conf = ev = note = ""

        if sec == "1":
            ax = axes[(tid, axname)]
            rec, conf, why = SECTION1_RULE[axname]
            used_rules["1-축 이름 규칙"] += 1
            ev = f"{why} 표 '{title[tid]}'(영문 {meta[tid]['name_eng']}); 다른 축: {other_axes(tid, axname)}."
            d = draft_l1.get(axname, "")
            if d and d not in rec:
                note = "초안 1층은 사람 확인(역할 미정)이라 권장 역할을 새로 냈다."

        elif sec == "2":
            ax = axes[(tid, axname)]
            vals = ax["values"]
            r_dict = ax["ratio"]
            r_eng = eng_ratio(vals)
            m = _TARGET_TITLE.search(title[tid])
            covered = None
            for word, l1axes in _TITLE_DIM.items():
                if word in title[tid]:
                    hit = [a["name"] for a in R.axes if a["tid"] == tid and a["name"] in l1axes]
                    if hit:
                        covered = (word, hit[0])
            nonroot = [v for v in vals if v.get("UP_ITM_ID")] or vals
            r_nonroot, _h, _n = R.target_ratio(nonroot)
            facts = [f"값 예: {ex_eng(vals)}", f"대상 사전 {r_dict:.0%}({ax['hit']}/{ax['n_nontotal']})",
                     f"영문 대상 이름 {r_eng:.0%}", f"깊이 {depth_of(vals)}단"]
            if u := units_txt(vals):
                facts.append(u)
            if o := other_axes(tid, axname):
                facts.append(f"다른 축: {o}")
            if s := same_stem(tid, axname):
                facts.append(s)
            kid_of = {v.get("UP_ITM_ID") for v in vals}
            heads = [clean_value(v.get("ITM_NM")) for v in vals
                     if v["ITM_ID"] in kid_of and _GROUP_HEAD.search(clean_value(v.get("ITM_NM")))]
            if heads:
                facts.append(f"분류 머리('…별') {len(heads)}개: {' / '.join(heads[:5])}(합계 머리로 읽음)")
            j = JUDGED.get(("2", tid, axname))
            if j:
                rec, conf, why = j
                used_rules["2-행별 판단"] += 1
                ev = why + " " + "; ".join(facts) + "."
            elif covered and r_nonroot < 0.3 and r_eng < 0.6:
                rec, conf = "지표 축", "high"
                used_rules["2-R1 다른 축이 차원을 맡음"] += 1
                ev = (f"표 이름의 '{covered[0]}'는 1층 대상 축 {covered[1]}이 맡는다; 이 축 값은 지표다. " + "; ".join(facts) + ".")
            elif r_eng >= 0.6 or (r_dict >= 0.5 and r_eng >= 0.4):
                rec = "대상 축"
                conf = "high" if (m and r_dict >= 0.5) or (r_eng >= 0.6 and (m or r_dict >= 0.5 or r_eng >= 0.75)) else "medium"
                used_rules["2-R2 영문·사전이 대상 차원"] += 1
                ev = "; ".join(facts) + (f"; 표 이름에 '{m.group(0)}'" if m else "") + "."
            elif (ind := _INDUSTRY_TITLE.search(title[tid])) and not covered and (r_dict >= 0.3 or r_eng >= 0.4):
                rec = "대상 축"
                conf = "high" if (r_eng >= 0.5 or r_dict >= 0.5) else "medium"
                used_rules["2-R2b 표 이름의 업종 차원"] += 1
                ev = (f"표 이름의 '{ind.group(0)}'를 맡는 1층 축(업종코드별)이 이 표에 없다. " + "; ".join(facts) + ".")
            else:
                rec = "지표 축"
                conf = "high" if r_dict < 0.3 and r_eng < 0.3 else "medium"
                used_rules["2-R3 나머지는 지표 축"] += 1
                ev = "; ".join(facts) + "."
            d = draft_l2.get((tid, axname))
            dr = d["역할 판정"] if d else ""
            kind = "대상" if rec.startswith("대상") else "지표" if rec.startswith("지표") else "눌린"
            now = row["지금 판정"]
            diffs = []
            if dr and dr != kind:
                diffs.append(f"초안 {dr}({d['근거']})")
            if now != kind and now != dr:
                diffs.append(f"재계산 {now}")
            if diffs:
                note = "다름: " + ", ".join(diffs) + ". "
                if kind == "대상" and r_dict < 0.5:
                    note += (f"영문 이름 {r_eng:.0%}가 대상 차원(지역·국가·업종·기관·부문)을 가리키는데, 그 값(권역·국가·세분 업종 등)이 "
                             f"대상 사전에 없어 사전 비율이 {r_dict:.0%}로 낮았다.")
                elif kind == "대상":
                    note += f"재계산(사전 {r_dict:.0%})과 같고 초안 비율과만 다르다(사전 구성 차이)."
                elif kind == "지표":
                    note += "값이 지표이고 대상 차원은 다른 축이나 결합 값의 앞부분이 맡는다."
                else:
                    note += "한 축에 지표와 대상이 단계(또는 같은 단계)로 함께 있어 축 하나로 정하면 한쪽을 잃는다."

        elif sec == "3":
            ax = axes.get((tid, axname))
            lookup = {}
            if ax:
                for v in ax["values"]:
                    lookup.setdefault(base(v.get("ITM_NM")), v.get("ITM_NM_ENG"))
            r4 = l4.get(tid, {})
            kids = [x.strip() for x in r4.get("반복 자식", "").split(" / ") if x.strip()]
            pars = [x.strip() for x in r4.get("부모", "").split(" / ") if x.strip()]
            up, ut, ub = stage_role(pars, lookup, R)
            lo, lt, lb = stage_role(kids, lookup, R)
            facts = [f"위 단계 예: " + " / ".join(f"{p}({lookup.get(base(p)) or '-'})" for p in pars[:4]),
                     f"아래 단계 예: " + " / ".join(f"{k}({lookup.get(base(k)) or '-'})" for k in kids[:4]),
                     f"위 대상 {ut:.0%}·기준 {ub:.0%}, 아래 대상 {lt:.0%}·기준 {lb:.0%}(사전 또는 영문)"]
            if ax and (u := units_txt(ax["values"])):
                facts.append(u)
            j = JUDGED.get(("3", tid, axname))
            if j:
                rec, conf, why = j
                used_rules["3-행별 판단"] += 1
                ev = why + " " + "; ".join(facts) + "."
            else:
                rec = f"위 {up} / 아래 {lo}"
                strong = all(max(t, b) >= 0.6 or max(t, b) <= 0.3 for t, b in ((ut, ub), (lt, lb)))
                conf = "high" if strong else "medium"
                used_rules["3-R 단계마다 사전·영문·기준어"] += 1
                ev = "; ".join(facts) + "."
            draft_md = next((v for k, v in DRAFT_MD_STAGES.items() if title[tid].startswith(k)), "")
            now = row["지금 판정"]
            now_simple = re.sub(r"\([\d.]+\)", "", now).replace("  ", " ").strip()
            if draft_md and simplify(rec, "3") != draft_md:
                dwhy = []
                if "기준" in draft_md and "기준" not in simplify(rec, "3"):
                    dwhy.append("통화는 재는 단위(금액/비중 구분별 축이 정함)가 아니라 거래를 가르는 구분이라 대상으로 읽음")
                if "가운데" in simplify(rec, "3"):
                    dwhy.append("기준 단계 위에 부문 단계가 더 있어 세 단계로 읽음")
                note = f"초안 예시 '{draft_md}'와 다름" + (f": {'; '.join(dwhy)}" if dwhy else "") + ". "
            if now_simple != simplify(rec, "3") and now != "재계산에서 눌린 축 아님":
                why = []
                rs = simplify(rec, "3")
                if "가운데" in rs:
                    why.append("세 단계로 읽음")
                if "대상" in rs and "대상" not in now_simple:
                    why.append("사전에 없는 부문·주체·발행자 이름(가계부문·민간·A.일반정부·거주자 발행 등)을 영문 이름으로 대상으로 읽음")
                elif rs.count("대상") > now_simple.count("대상"):
                    why.append("사전에 없는 국가·부문 이름을 영문으로 대상으로 읽음")
                if "기준" in rs and "기준" not in now_simple:
                    why.append("영문 Short-term·Long-term 등으로 만기를 기준으로 읽음")
                if "합계 머리" in rs:
                    why.append("묶음 머리를 합계 머리로 읽음")
                if "기준" in now_simple and "기준" not in rs:
                    why.append("통화는 단위가 아니라 거래 구분이라 대상으로 읽음")
                note += f"재계산 '{now_simple}'와 다름: " + ("; ".join(why) or "영문 이름·계층을 함께 봄") + "."
            elif now == "재계산에서 눌린 축 아님":
                l2r = l2.get((tid, axname), {})
                note += (f"초안과 재계산이 갈림: 초안은 지표 축({l2r.get('근거(초안)', '')}) 안의 눌린 축으로 봤고, 재계산은 대상 축"
                         f"(사전 {l2r.get('대상 사전 비율(재계산)', '')})이라 눌린 축이 아니다. 권장은 두 단계 모두 대상이라 결론이 재계산(대상 축)과 "
                         "같다. 초안 예시의 '아래 기준'(국가별 결제통화)과는 다르다.")
            note = note.strip()

        elif sec == "4":
            k = base(val)
            # full table list (the sheet shows only five); same selection as e2_axis_roles.py
            ctx = []
            for ax in R.axes:
                if ax["role"] != "measure":
                    continue
                by_parent = collections.defaultdict(list)
                for v in ax["values"]:
                    by_parent[v.get("UP_ITM_ID")].append(v)
                ids = {v["ITM_ID"]: v for v in ax["values"]}
                for v in ax["values"]:
                    if base(v.get("ITM_NM")) != k or R.value_role[(ax["tid"], ax["obj_id"], v["ITM_ID"])] != "target":
                        continue
                    sib = [s for s in by_parent[v.get("UP_ITM_ID")] if s is not v]
                    ratio, _h, n = R.target_ratio(by_parent[v.get("UP_ITM_ID")])
                    if n >= 2 and ratio < 0.5:
                        anc, cur = [], v
                        while cur.get("UP_ITM_ID") in ids and len(anc) < 6:
                            cur = ids[cur["UP_ITM_ID"]]
                            anc.append(clean_value(cur.get("ITM_NM")))
                        ctx.append({"tid": ax["tid"], "v": v, "sib": sib, "anc": anc,
                                    "kids": sum(1 for x in ax["values"] if x.get("UP_ITM_ID") == v["ITM_ID"])})
            per_table = {}
            for c in ctx:
                t = c["tid"]
                if t in per_table:
                    continue
                v, sib, anc = c["v"], c["sib"], c["anc"]
                j = JUDGED.get(("4", t, val))
                sib_comb = sum(1 for s in sib if is_combined(s.get("ITM_NM") or "")
                               and base(re.split(r"\s*-\s*", clean_value(s.get("ITM_NM")))[0]) in R.target_dict)
                sib_sector = sum(1 for s in sib if clean_value(s.get("ITM_NM")).replace(" ", "").split("(")[0].endswith("부문"))
                if j:
                    per_table[t] = (j[0], j[1], j[2], "행별 판단")
                elif c["kids"] and _GROUP_HEAD.search(clean_value(v.get("ITM_NM"))):
                    per_table[t] = ("합계 머리", "high", f"'{clean_value(v.get('ITM_NM'))}'(영문 {v.get('ITM_NM_ENG')})는 "
                                    f"자식 {c['kids']}개를 묶는 분류 머리다.", "R4a 분류 머리")
                elif _INDEX_TITLE.search(title[t]):
                    per_table[t] = ("지표", "high", f"{title[t]}: 지수 표의 품목 분류(단위 {v.get('UNIT_NM') or '-'})이고 "
                                    f"부모 {anc[0] if anc else '-'} 아래 품목 형제({len(sib)}개)와 나란하다.", "R4b 지수 표의 품목")
                elif _TARGET_TITLE.search(title[t]) or any(_TARGET_TITLE.search(a) for a in anc):
                    where = (_TARGET_TITLE.search(title[t]) or next(_TARGET_TITLE.search(a) for a in anc if _TARGET_TITLE.search(a)))
                    per_table[t] = ("대상", "high", f"{title[t]}: 표 이름이나 상위 값에 '{where.group(0)}'이 있고 영문 "
                                    f"{v.get('ITM_NM_ENG')}다.", "R4c 대상 차원 이름")
                elif sib_sector * 2 >= len(sib) and sib:
                    per_table[t] = ("대상", "high", f"{title[t]}: 형제가 '…부문'(가계부문·지방정부부문 등)이고 영문 "
                                    f"{v.get('ITM_NM_ENG')}다.", "R4d 부문 형제")
                elif sib_comb >= 1 and sib_comb * 2 >= len(sib) - 1:
                    per_table[t] = ("대상", "high", f"{title[t]}: 형제가 '기관-상품' 결합(예: "
                                    f"{next(clean_value(s.get('ITM_NM')) for s in sib if is_combined(s.get('ITM_NM') or ''))})이거나 기관이다.",
                                    "R4e 기관-상품 결합 형제")
                else:
                    sib_eng = sum(1 for s in sib if eng_target(s.get("ITM_NM_ENG")))
                    if eng_target(v.get("ITM_NM_ENG")) and sib and sib_eng * 2 >= len(sib):
                        per_table[t] = ("대상", "high" if sib_eng >= 0.6 * len(sib) else "medium",
                                        f"{title[t]}: 값과 형제 {sib_eng}/{len(sib)}의 영문이 기관·부문 이름이다(영문 "
                                        f"{v.get('ITM_NM_ENG')}; 형제 예 {' / '.join((s.get('ITM_NM_ENG') or '') for s in sib[:3])}).",
                                        "R4f 영문 형제가 기관·부문")
                    else:
                        per_table[t] = ("대상", "low", f"{title[t]}: 규칙으로 정하지 못함(사전 일치만 근거).", "미정")
            groups = collections.defaultdict(list)
            for t, (r, c_, why, rule) in sorted(per_table.items()):
                groups[(r, c_)].append((t, why, rule))
            for (_r, _c), lst in groups.items():
                for _t, _w, rule in lst:
                    used_rules["4-" + rule] += 1
            if len({r for r, _c in groups}) == 1:
                rec = next(iter(groups))[0]
            else:
                rec = " / ".join(f"{r}({', '.join(t for t, _w, _ru in lst)})" for (r, _c), lst in groups.items())
            order = {"low": 0, "medium": 1, "high": 2}
            conf = min((c_ for _r, c_ in groups), key=lambda x: order[x])
            whys = []
            for (r, _c), lst in groups.items():
                ids = [t for t, _w, _ru in lst]
                whys.append(f"[{r}] {lst[0][1]}" + (f" 외 {len(lst) - 1}표" if len(lst) > 1 else "")
                            + f" (표: {', '.join(ids[:12])}{' …' if len(ids) > 12 else ''})")
            ev = f"표 {len(per_table)}개: " + " ".join(whys)
            if rec != "대상":
                note = "초안 규칙(사전 정확 일치 → 대상)과 다름: 값이 놓인 자리(형제·표)가 " + (
                    "상품·품목이거나 분류 머리다." if "대상" not in rec else "표마다 갈린다.")

        elif sec == "5":
            ax = axes[(tid, axname)]
            vals = ax["values"]
            comb = [v for v in vals if is_combined(v.get("ITM_NM") or "")]
            tgt = [clean_value(v.get("ITM_NM")) for v in vals
                   if R.value_role.get((tid, ax["obj_id"], v["ITM_ID"])) in ("target", "target_sibling")]
            rec, conf = "지표", "high"
            facts = [f"값 {len(vals)}개, 깊이 {depth_of(vals)}단", f"값 예: {ex_eng(vals)}"]
            if u := units_txt(vals):
                facts.append(u)
            used_rules["5-R 구성 항목은 지표"] += 1
            if comb:
                left = sorted({clean_value(re.split(r"\s*-\s*", clean_value(v.get("ITM_NM")))[-1]) for v in comb})
                note = (f"예외 아님, 다만 결합 값 {len(comb)}개('{clean_value(comb[0].get('ITM_NM'))}')는 용도-기관으로 나눠 읽는다"
                        f"(기관: {' / '.join(left[:4])} = 대상).")
                conf = "medium"
            if tgt:
                note += (" " if note else "") + (f"사전 일치로 대상이 된 값 {len(tgt)}개({' / '.join(tgt[:5])})는 이 표에서 품목·용도 "
                                                 "이름이라 지표로 둔다(4번 행 권장과 같음)." if _INDEX_TITLE.search(title[tid]) else
                                                 f"대상 값 {len(tgt)}개({' / '.join(tgt[:3])})는 뿌리 기관(합계)이다.")
            ev = "; ".join(facts) + "."

        out.append({**row, "recommended": rec, "confidence": conf, "evidence": ev, "note": note, "decision": ""})

    write_csv(DIR / "confirm_sheet_recommended.csv", out, FIELDS)
    write_summary(out, used_rules)


def write_summary(out, used_rules) -> None:
    by_sec = collections.defaultdict(list)
    for r in out:
        by_sec[r["구분"]].append(r)
    lines = ["# 2b. 축 역할 확인표 권장 판정 (권장, 승인 전)", "",
             "- 입력: `confirm_sheet.csv` 181행. 출력: `confirm_sheet_recommended.csv`(원래 칸 + recommended·confidence·evidence·note, decision 빈 칸).",
             "- 근거는 메타 스냅숏의 사실만 썼다: 값 이름과 영문 이름(ITM_NM_ENG), 대상 사전 비율, 표 이름·다른 축, 계층 깊이, 값 단위, 같은 표 이름 앞부분을 가진 다른 표의 판정.",
             "- 질문(골든·개발) 대조는 이번에 하지 않았다. 새200·블라인드 30은 열지 않았다. 호출 0회.",
             "- 모든 판정은 권장이다. decision 칸은 사용자가 채운다.", "", "## 구분별 권장 분포", ""]
    for sec, rows in sorted(by_sec.items()):
        lines += [f"### {sec} ({len(rows)}행)", "", "| 권장 | 행 수 |", "|---|---|"]
        c = collections.Counter(simplify(r["recommended"], sec) for r in rows)
        for k, v in c.most_common():
            lines.append(f"| {k} | {v} |")
        cc = collections.Counter(r["confidence"] for r in rows)
        lines += ["", "확신: " + ", ".join(f"{k} {cc[k]}" for k in ("high", "medium", "low") if cc[k]), ""]
    lines += ["## 일괄 규칙", "",
              "| 구분 | 규칙 | 적용 수(1·2·3·5번은 행, 4번은 값×표) |", "|---|---|---|"]
    rule_text = {
        "1-축 이름 규칙": "축 이름마다 하나: 자금구분별 → 지표(용도 구성 항목), 분류체계별 → 대상(기업 형태), 분위 구분코드별 → 기준(통계량)",
        "2-R1 다른 축이 차원을 맡음": "표 이름의 '지역별·업권별·산업별·부문별'을 1층 대상 축(지역코드별·보고기관별·업종코드별 등)이 이미 맡고, 이 축의 뿌리 밖 값 사전 비율 < 30%·영문 대상 이름 < 60% → 지표 축",
        "2-R2 영문·사전이 대상 차원": "영문 값 이름의 60% 이상이 지역·국가·업종·기관·부문·연령·성별을 가리키거나, 사전 비율 ≥ 50%이고 영문 ≥ 40% → 대상 축. (표 이름 '…별' + 사전 ≥ 50%) 또는 (영문 ≥ 60% + 표 이름 '…별'·사전 ≥ 50%·영문 ≥ 75% 중 하나)면 high",
        "2-R3 나머지는 지표 축": "위 둘에 안 걸리면 지표 축. 사전·영문 모두 30% 미만이면 high",
        "3-R 단계마다 사전·영문·기준어": "위 단계(부모)와 아래 단계(반복 자식)마다 사전 또는 영문 대상 이름 ≥ 50% → 대상, 기준어(단기·장기·원화·외화 등) ≥ 50% → 기준, 나머지 지표",
        "4-R4a 분류 머리": "값 이름이 '…별'·'…구분'이고 자식이 있으면 합계 머리(분류 머리)",
        "4-R4b 지수 표의 품목": "물가지수·금액지수·물량지수 표에서는 업종 이름과 같은 값도 품목 분류 → 지표",
        "4-R4c 대상 차원 이름": "표 이름이나 상위 값에 경제활동별·산업별·주체별·부문별·기관형태별·지역별 등이 있으면 → 대상",
        "4-R4d 부문 형제": "형제 절반 이상이 '…부문' → 대상",
        "4-R4e 기관-상품 결합 형제": "형제 절반 이상이 '기관-상품' 결합(앞부분이 사전에 있음) → 대상",
        "4-R4f 영문 형제가 기관·부문": "값과 형제 절반 이상의 영문 이름이 기관·부문(Banks·Government·Mutual Credits 등) → 대상",
        "2-R2b 표 이름의 업종 차원": "표 이름에 '경제활동별·산업별'이 있는데 업종코드별 축이 표에 없고, 사전 ≥ 30% 또는 영문 ≥ 40% → 대상 축(세분 업종이 사전에 없어 비율이 낮은 경우)",
        "5-R 구성 항목은 지표": "초안 권장대로 지표. 결합 값·사전 일치 값이 있으면 note에 적음",
    }
    for k, v in sorted(used_rules.items()):
        if "행별" in k:
            continue
        lines.append(f"| {k.split('-')[0]} | {rule_text.get(k, '규칙으로 못 정함(low)')} | {v} |")
    lines += ["", "규칙 예외(행별 판단): " + ", ".join(f"{k.split('-')[0]}번 {v}행" for k, v in sorted(used_rules.items()) if "행별" in k)
              + ". 사유는 각 행 evidence 앞부분에 있다.", ""]
    low = [r for r in out if r["confidence"] == "low"]
    lines += [f"## confidence low ({len(low)}행, 먼저 볼 것)", ""]
    if low:
        lines += ["| 구분 | 표 ID | 축/값 | 권장 | 이유 |", "|---|---|---|---|---|"]
        for r in low:
            lines.append(f"| {r['구분']} | {r['표 ID']} | {r['축 이름'] or r['값']} | {r['recommended']} | {r['evidence'][:160]} |")
    else:
        lines.append("없음")
    med = [r for r in out if r["confidence"] == "medium"]
    lines += ["", f"medium {len(med)}행은 CSV에서 confidence로 걸러 본다.", ""]
    diff = [r for r in out if "다름" in r["note"]]
    s1 = [r for r in out if r["구분"].startswith("1") and "사람 확인" in r["note"]]
    lines += [f"## 초안·재계산과 다른 권장 ({len(diff)}행)", "",
              f"1번 중 초안이 사람 확인(미정)으로 남긴 {len(s1)}행은 '다름'이 아니라 새 권장이라 이 표에서 뺐다"
              f"(자금구분별 4행 → 지표, 분류체계별 1행 → 대상). 분위 구분코드별은 초안(기준)과 같다.", "", "| 구분 | 표 ID | 축/값 | 지금 판정 | 권장 | 이유 |", "|---|---|---|---|---|---|"]
    for r in diff:
        lines.append(f"| {r['구분']} | {r['표 ID']} | {r['축 이름'] or r['값']} | {r['지금 판정']} | {r['recommended']} | {r['note'][:260]} |")
    cur = [r for r in out if "결제통화" in r["표 이름"] and r["구분"][0] in "23"]
    lines += ["", f"## 결제통화 표: 초안·재계산·권장 ({len(cur)}행)", "",
              "| 구분 | 표 ID | 표 이름 | 지금 판정(재계산) | 권장 | note |", "|---|---|---|---|---|---|"]
    for r in cur:
        lines.append(f"| {r['구분']} | {r['표 ID']} | {r['표 이름']} | {r['지금 판정']} | {r['recommended']} | {r['note'][:260]} |")
    lines += ["", "- 초안: 지역별 2표는 지표 축(48%) 안의 눌린 축, 국가별 2표는 '위 대상(국가) / 아래 기준(통화)'(초안 4층 예시).",
              "- 재계산: 지역별 2표는 대상 축(53%, 통화 이름이 1층 통화별 축 값과 맞음), 국가별 2표는 지표 축(47%) 안의 눌린 축 '위 지표 / 아래 대상'.",
              "- 권장: 네 표 모두 대상 축(지역·국가 × 결제통화, 둘 다 대상). 통화를 기준으로 보지 않는 근거는 수치 단위를 금액/비중 구분별 축(백만달러·%)이 따로 정한다는 점이다. 확신 medium."]
    lines += ["", "## 확인표 밖에서 본 것 (참고, 판정 안 함)", "",
              "- M2 경제주체별 보유현황 구판 4표(DT_101Y005·006·014·015)의 계정항목별 축은 재계산 지표(사전 20%)로 확인표에 오르지 않았지만, "
              "신판 4표(DT_161Y009~012, 권장 대상 축)와 값 구성(가계 및 비영리단체·기업·기타금융기관·기타부문)이 같다.",
              "- ICT·콘텐츠 산업 특수분류(DT_304Y105·106)의 계정항목별 축도 재계산 지표(사전 20·25%)인데 값 대부분이 업종이다(4번 행에서 대상 권장).",
              "- 부문 이름 앞 글자('A.일반정부', 'a.법인')를 정규화에서 떼지 못해 사전 일치가 빠진다. 열쇠 규칙(common.py `_LEAD_NUM`)을 고치면 3번 일부가 규칙으로 풀린다(이번에는 고치지 않음).",
              "", "## 한계", "",
              "- 영문 대상 이름 판정은 고정 낱말 목록(e2b_recommend.py `_ENG_TARGET`)이다. 'Services'·'Construction'처럼 업종과 지출 항목이 같은 영문을 쓰는 경우가 있어 행별 판단으로 보정했다.",
              "- 질문 대조를 하지 않아 권장이 요구 수 세기에 주는 효과는 재지 않았다."]
    (DIR / "summary_recommend.md").write_text("\n".join(lines) + "\n", "utf8")
    print("\n".join(lines))


def simplify(rec: str, sec: str) -> str:
    if sec.startswith("2"):
        return rec.split("(")[0].strip()
    if sec.startswith("4") and "(" in rec:
        return "표마다 다름: " + " / ".join(sorted({p.split("(")[0].strip() for p in rec.split(" / ")}))
    if sec.startswith("3"):
        return re.sub(r"\([^()]*(?:\([^()]*\)[^()]*)*\)", "", rec).replace("  ", " ").strip()
    return rec


if __name__ == "__main__":
    main()
