# claview_v3 출력 표본, 현재 A 작업 트리 (B 3단계 시험용)

- 만든 날: 2026-10-21
- 만든 코드: A 작업 트리(`claview-a-v1-n5-split`)의 `src/claview_v3`와 그 데이터(`reference/baseline-1006/data`, `docs/inputs/axis-roles-20261016`)를 임시 폴더에 복사해 `make_fixtures.py`로 실행. 복사본 판본: `contract/shapes.py` sha256 앞 12자리 `120160acad66`, `interpret/condition_fields.py` `cb95c856d574`.
- 실행 조건: `run_outer(question, "router", as_of="2026-10-20", decomposition={"mode": "rules"})`. 규칙 모드라 모델 호출 없음, `_guard.install_network_block()`으로 네트워크 차단. 외부 호출 0회.
- 질문 8개는 `fixtures/v3/`과 같다(이 시험을 위해 쓴 질문, 평가셋 문항 아님).
- 저장 필드: question, as_of, request_id, analysis(`decomposition`, `mentions`, `segments`, `ref_map` 포함), selections, selection_groups, selection_route, requirements, unresolved, selection.
- 규칙 모드라 `reading_id`는 None이고 `decomposition.inferred`는 요구마다 빈 배열이다. 표본은 모양 확인용이며 표 선택의 정답이 아니다.
