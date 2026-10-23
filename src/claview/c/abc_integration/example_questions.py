"""첫 화면용 질문 목록. 골든 정답/표 코드/숫자는 브라우저로 전달하지 않는다."""
import json

from dependencies import B_ROOT


GROUPS = [
    ("한 지표의 시간 변화", [1, 2, 3, 6, 11, 12, 15, 22]),
    ("여러 지표의 시간 변화 비교", [4, 5, 7, 13, 16, 17, 19, 21]),
    ("구성항목 비교", [8, 9]),
    ("항목별 규모·증감 비교", [10, 20]),
    ("변화가 큰 시점 찾기", [18]),
    ("수치표 보기", [14]),
]
LABELS = [
    "실질 GDP 성장률", "총고정자본형성 증감률", "수출 증감률", "경상수지 비교",
    "서비스수지 비교", "가계신용 잔액", "기업 규모별 대출금리", "업권별 가계신용",
    "M2 상품 구성", "지역별 수출", "분기 GDP 성장률", "순대외금융자산",
    "용도별 대출금", "매출액영업이익률 표", "경제심리지수", "공산품·서비스 물가",
    "신선식품 물가 비교", "산업용 전력 물가 변화", "수입 원재료·중간재 물가",
    "수출 품목별 물가 변화", "국내공급 단계별 물가", "총산출물가",
]
NOTES = {
    9: {"badge": "합산 검증 후 누적", "text": "14개 상품 합계를 공식 M2 전체와 대조해 통과하면 누적막대·100% 누적막대를 제공합니다. 일부 항목을 숨겨도 비중 분모는 공식 전체로 유지합니다."},
    18: {"badge": "전월 변화율 기준", "text": "산업용 전력 물가지수의 전월 대비 변화율로 가장 크게 움직인 달을 찾습니다. 상승·하락을 모두 포함하고 동률도 표시합니다. 실제 전기요금 자료는 아닙니다."},
    20: {"badge": "연간 전년 대비", "text": "현재 B의 연간 주기를 유지해 2024년 지수를 2023년과 비교한 증감률(%)을 계산합니다. 전년동월 대비가 아니며, 원지수도 별도 카드에 보존합니다."},
}


def example_groups():
    # 화면과 검수에서 질문 문장이 달라지지 않도록 같은 원문을 사용한다.
    cases = json.loads((B_ROOT / "eval/cases/golden_set_v1.json").read_text(encoding="utf-8"))["cases"]
    queries = {c["case_id"]: c["query"] for c in cases}
    numbers = [n for _, items in GROUPS for n in items]
    if sorted(numbers) != list(range(1, 23)) or len(queries) != 22:
        raise ValueError("예시 질문은 01~22를 중복/누락 없이 포함해야 합니다.")
    return [{"title": title, "items": [
        {"id": f"CV-AB-{n:02d}", "number": f"{n:02d}", "label": f"{n:02d} · {LABELS[n-1]}",
         "q": queries[f"CV-AB-{n:02d}"], "note": NOTES.get(n)}
        for n in numbers]} for title, numbers in GROUPS]
