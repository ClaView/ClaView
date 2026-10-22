# B 3단계 확인 스크립트

모두 오프라인(모델·KOSIS·네트워크 호출 없음). 저장소 루트에서 실행한다.

| 스크립트 | 내용 |
|---|---|
| `run_logged.py <repo> <log> <명령...>` | 명령을 실행하고 명령·시작/끝 시각·종료 코드·코드 트리 sha256·주요 파일 sha256·출력을 로그로 남긴다 |
| `compare_golden.py <repo>` | 골든 22건의 저장 조건으로 공용 해석기를 돌려 정답 기간과 대조 |
| `v3_through_b.py <repo> [v3\|v3cur]` | `tests/b_v2/fixtures/<폴더>`의 A 출력 표본을 B 요청으로 바꿔 B를 돌린다. 모델 응답은 메타데이터에서 만든 합성(첫 항목·첫 값)이라 정확도 근거가 아니다 |
| `io_examples.py` | 합성 표로 공용 패키지를 실제 호출한 입출력 예시 7건(계약 검사 포함) |

예:

```powershell
uv run --offline python scripts/b_stage3/run_logged.py . eval/results/b_stage3_20261021/after_review2/04_golden_period_compare.log uv run --offline python scripts/b_stage3/compare_golden.py .
```
