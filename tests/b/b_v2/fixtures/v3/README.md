# claview_v3 출력 표본 (B 어댑터 시험용)

- 만든 날: 2026-10-20
- 만든 코드: `claview-a-v1-n5-split/share/질문해석_공유_20261020`(A 파트 동결 공유본)을 임시 폴더에 복사해 `make_fixtures.py`로 실행.
- 실행 조건: `run_outer(question, "router", as_of="2026-10-20")`, 모델 끔(jev 없음), `_guard.install_network_block()`으로 네트워크 차단. 외부 호출 0회.
- 질문 8개는 이 시험을 위해 새로 쓴 것이다(공유본 예시 3개 포함). 평가셋(417문항) 문항이 아니며 성능 측정에 쓰지 않는다.
- 저장 필드: question, as_of, analysis, selections, selection_groups, selection_route, requirements, unresolved, selection.
- 모델을 끈 상태라 대부분의 선택이 confirm이고 경로 종류가 ask_selection이다. 표본은 모양 확인용이며 표 선택의 정답이 아니다.
