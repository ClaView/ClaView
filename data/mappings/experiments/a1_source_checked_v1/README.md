# A1 공식 출처 확인용 사전 실험 v1

- 기준일: 2026-10-06
- 원본: [runtime 사전](../../runtime/search_mappings.json). 원본 79건은 수정하지 않는다.
- `dictionary.json`: 공백 표기 8건 + GDP/GNI 2건의 부분집합. 모두 draft, 운영 승인 0건
- `review.json`: 원본 79건의 기계적 공식 출처 확인, 적용 조건·범위·보류 이유
- 확인 주체: `codex_deterministic_source_check`. 사람 의미 검수 아님. `human_review=false`, `operational_approval=false`
- 적용: `--a1-mapping-review data/mappings/experiments/a1_source_checked_v1/review.json`을 명시한 A1 rules 실행만 허용
- 생성: `scripts/prepare_a1_mapping_review.py`; 읽을 때 현재 공식 캐시·원본 사전과 재대조

운영 approved 정책에서는 초안을 승인으로 취급하지 않는다. [검수 정책](../../../../docs/guides/용어_매핑_작성과_검수.md)에 따른 사람 검수는 대기 상태다. 기존 22건에서 정답 후보 포함·순위 개선을 입증하지 못했으므로 기본 활성화하지 않는다. 현재 결과는 [22건 통합 평가](../../../../docs/research/A1_A2_22건_통합평가_6단계_2026-10-06.md)를 따른다.
