# 실행 자료 안내

> **2026-10-23(계획 26) 안내**: 이 문서가 부르는 준비 스크립트(`scripts/prepare_document_corpus.py`)는 옛 A 모듈을 import해서 옛 A와 함께 저장소에서 지웠다. 같은 파일이 원격 이력(`b172d75`, `origin/integrated-router-20261022`)과 원본 작업 폴더(B b-1020 사본 등)에 남아 있다. 지금 저장소에서는 아래 재생성 절차를 실행할 수 없고, 이미 만든 자료(`data/` 아래)는 그대로 쓴다. 재생성이 필요하면 옛 A 의존을 걷어 낸 도구를 새로 만든다.

공식349표의 저장 목록·메타를 사용합니다. 이번 정리는 자료·골든셋을 변경하지 않았으며 API를 재수집하지 않았습니다.

| 위치 | 용도 |
|---|---|
| [search_catalog.json](catalog/search_catalog.json) | 공식 표명·ID·전체 목록 경로 |
| `raw/boK_meta_raw.jsonl` | 저장 메타 원문, Git 제외 |
| [search_definitions.json](catalog/search_definitions.json) | 검색 정의·근거 |
| [pipeline_manifest.json](catalog/pipeline_manifest.json) | 이관 해시 |
| [runtime 사전](mappings/runtime/README.md) | 79건, 승인0 |
| [A1 사전 실험](mappings/experiments/a1_source_checked_v1/README.md) | 명시 옵션으로만 쓰는 10건 출처 확인 |
| `mappings/review/`, `samples/`, 기타 catalog 자료 | 생성·초안 검수에 필요한 원천 |
| [기존22](../eval/cases/golden/golden_set_v1.json) · [Schema](../eval/cases/golden/golden_set_v1.schema.json) | 현재 평가 자료, 모두 단일 표 정답 |
| `eval/cases/legacy/a_stage2/`, `eval/cases/b/search_review/` | 계약 회귀·개발 테스트. 임의 추가48은 성능 지표에서 제외 |
| [B 기간 시험](../eval/cases/b/period/) | 지정 B ZIP의 증강13·스키마. 코드 정책 기능 시험으로 분리하고 기존22 지표에 합산하지 않음 |

전체 메타는 별도 전달받아 위 경로에 준비합니다. 현재 로컬에는 준비돼 있습니다. 저장 자료 묶음이 생성기 규약과 일치하면 `scripts/b/prepare_pipeline_data.py --source-root <자료묶음>`을 사용합니다. 목적지 내용이 다르면 덮어쓰지 않고 중단합니다. 전체 자료 없이 기능만 확인할 때는 [3표 fixture](../tests/b/fixtures/README.md)를 사용합니다.

기본 legacy 문서는 표명·경로·항목·분류·주석으로 실행 중 생성합니다. D0~D3는 비교 옵션이며 공식 인용·해시·부모 경로를 보존합니다. 목록 depth와 표 내부 항목 depth는 다릅니다. 큰 파생 문서·색인은 Git 제외입니다.

```powershell
$docRun = "runs/document-build-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
uv run --env-file .env --locked python scripts/prepare_document_corpus.py --output $docRun
```

생성기는 새 폴더에 기록하며 모델·통계 API를 호출하지 않습니다. 기본 검색은 legacy를 유지합니다. [사전 검수](../docs/guides/용어_매핑_작성과_검수.md) · [22건 평가](../docs/records/_이전_A1A2_20261006/A1_A2_22건_통합평가_6단계_2026-10-06.md)
