# A 사용자 선택 실행과 복구

2026-10-06 기준입니다. 기본값은 모델 off, baseline/legacy, approved 사전, A1 rules, A2 evidence, 판단 shadow입니다. 검색과 확정에 같은 자료·설정·기준 날짜를 사용합니다.

## 검색과 확정

```powershell
uv sync --locked
$aRun = "runs/manual-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
New-Item -ItemType Directory -Path $aRun | Out-Null
$common = @("--db", "$aRun/candidates.sqlite3", "--session", "manual", "--request", "import-prices", "--as-of", "2026-10-02")
uv run --env-file .env --locked claview search-tables @common --query "2024년 수입 원재료와 중간재의 물가 흐름을 원화 기준으로 월별 비교해줘" --output "$aRun/search.json"
$saved = Get-Content -Raw -Encoding utf8 "$aRun/search.json" | ConvertFrom-Json
$setId = $saved.interaction.candidate_set_id
uv run --env-file .env --locked claview list-candidates @common --set-id $setId
```

한 페이지 5개는 표시 개수입니다. 확정할 표 개수가 아닙니다. 표명·항목·단위·공식 근거를 확인합니다. 위 질문에서 필요한 표를 공식 ID로 확정하는 예시입니다.

```powershell
uv run --env-file .env --locked claview confirm-tables @common --set-id $setId --select 301:DT_401Y018 --output "$aRun/confirmed.json"
uv run --env-file .env --locked claview candidate-status @common --set-id $setId
```

번호 선택은 `--select 1`, 복수 표는 `--select 1 3`입니다. 번호는 전체 목록 기준입니다. 한 표 안의 여러 계열은 B가 처리하므로 항목 수를 표 수로 취급하지 않습니다.

확정 전에는 `awaiting_user/result=null`, 확정 후에는 `A.tables`를 반환합니다. confirm은 B를 호출하지 않습니다. 같은 집합은 재사용하고 다른 집합은 `selection_conflict`입니다. 재선택은 새 묶음에서 수행합니다.

새 터미널에서는 `$aRun`에 기존 디렉터리를 지정하고 `$common`, `$saved`, `$setId`를 다시 설정하면 검색 없이 이어갑니다. 원문을 바꾸려면 `--revision`을 증가시켜 새 묶음을 만듭니다.

## 판단·답변·복구

- `shadow`: 판단만 기록하고 사용자가 확정합니다.
- `auto`: 제한된 공식 명칭 요구에서만 자동 확정합니다. 기존22는 자동0건입니다.
- `clarify-tables --answer <저장된 선택지 값>`: 답변을 별도 저장합니다. 같은 후보면 A2만, 검색 요구가 바뀌면 A1부터 다시 실행합니다.
- `reassess-tables`: 저장 요구를 새 판본으로 재판단합니다.

답변 후 반환된 새 set-id·revision을 사용합니다. `candidate-status`에서 `a1_state`, `a2_state`, `selection_decision`, `selection_origin`, `a_answers`를 확인합니다. 비교용 `--a1-mode off --a2-mode off --a-selection-mode off`도 검색과 확정에 동일하게 지정합니다.

| 상태 | 처리 |
|---|---|
| 후보 없음·의미 미확인 | 질문·공식 근거 확인, 새 검색 또는 직접 선택 |
| 메타 기간 부족 | 갱신 필요와 실제 API 미지원 구분 |
| stale_query / context_mismatch | session·request·revision·set-id 확인 |
| candidate_set_expired | 기본24시간 대기 TTL 경과, 새 검색 |
| snapshot_mismatch | 자료·코드·설정 변경, 새 묶음 |
| b_retry_required / b_indeterminate | 상태 확인 후 B만 명시 재시도 |

## B 모의 연결

위 예시 표를 확정한 묶음에서 다음 명령은 연결만 시험합니다.

```powershell
uv run --env-file .env --locked claview run-b @common --set-id $setId --b-responses tests/fixtures/b_contract_responses.json
```

B 실패 후 A는 유지됩니다. 원인을 해결하고 `--retry`를 붙이면 B부터 다시 실행합니다. 성공한 B는 결과를 재사용합니다. 실패 시험은 아직 B를 실행하지 않은 새 묶음에 `b_empty_responses.json`을 사용합니다. lease·시도 ID로 중복과 늦은 응답을 구분하지만 외부 요청의 정확히 한 번 실행까지 보장하지 않습니다.

실제 B 의미·수치 검증과 팀원 구현 통합은 별도 범위입니다. [B 계약](../modules/b/design/A에서_B로_전달하는_인터페이스.md)을 확인합니다.

## 평가와 호환 실행

```powershell
$aEval = "runs/a-eval-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
uv run --env-file .env --locked python scripts/evaluate_a_stage6.py --output $aEval
uv run --env-file .env --locked python -m pytest -q
uv run --env-file .env --locked claview demo --case CV-AB-19
```

통합 평가는 전체 메타와 보존된 기준 기록을 사용하고 외부 연결을 차단합니다. [22건 결과](_이전_A1A2_20261006/A1_A2_22건_통합평가_6단계_2026-10-06.md)의 해석을 따릅니다.

`TableDiscovery`·`build_discovery_graph`가 사용자 선택 경로입니다. `build_a_graph`·`select_tables`·`evaluate-a`·`run --selection-mode legacy`는 기존 자동 선택 호환 경로입니다. 새 A와 결과를 합산하지 않습니다.
