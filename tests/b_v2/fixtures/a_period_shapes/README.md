# A가 쓰는 기간 조건 표본 (B 3단계 보완 시험용)

- 만든 날: 2026-10-21
- 만든 코드: `tests/b_v2/fixtures/v3cur/`과 같은 A 작업 트리 사본(`contract/shapes.py` `120160acad66`, `interpret/condition_fields.py` `cb95c856d574`)에서 `make_a_period_shapes.py` 실행. `run_outer(..., as_of="2026-10-20", decomposition={"mode": "rules"})`, 네트워크 차단, 모델 호출 없음, 외부 호출 0회.
- 저장 필드: 질문, A의 기간·주기 조건(`analysis.conditions`), A의 `period_diagnosis`(같은 사본의 `interpret/condition_fields.py`).
- 관찰: 네 질문 모두 기간 조건은 `extract_status: "unresolved"`이고, A의 `period_diagnosis`는 `unsupported`다(옛 해석기 기준). 공용 해석기는 같은 조건을 해석한다. A 진단 갱신은 A 연결 작업으로 남는다.
- 질문 4개는 이 시험을 위해 쓴 문장이며 평가셋 문항이 아니다.
