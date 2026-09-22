# NCP 연결·기능 실험

- 담당: 채진현
- 갱신일: 2026-09-22
- 상태: 9종 최소 연결 확인. 재현용 공유 후보이며 과업 성능·특화 기능은 추가 검증 필요.

## 파일

- `ncp_probe.py`: 요청 구성·응답 검증
- `data/metadata-demo.json`: 두 통계표의 설명용 입력
- `tests/`: 외부 연결을 차단한 모의 응답 테스트

## 실행

저장소 루트에서 실행합니다. NCP 의존성은 선택 그룹으로 설치합니다.

```sh
uv run --locked --group ncp python -m pytest experiments/ncp/tests -q
uv run --locked --group ncp python -m experiments.ncp.ncp_probe chat
```

위 명령은 NCP API를 호출하지 않습니다. 실제 호출은 `--live` 지정 시 수행합니다. 키·권한·비용·호출 수를 확인한 뒤 실행합니다.

결과는 `artifacts/ncp-probes/`에 보관합니다. 공통 환경 설치와 기본 테스트는 이 디렉터리 없이 실행할 수 있습니다.

실험 결과: 9종 최소 요청 총 13회 `worked`. 전체 모델 비교·과업 정확도는 미확정입니다. [실행 결과와 내용 검수](../../docs/evaluation/NCP_초기호출_검증결과.md)를 참고합니다.

## 9종 확인 범위

- 대화 5종: `chat --model MODEL`
- 임베딩: `embedding --embedding-model v2` 또는 `clir-emb-dolphin`, `clir-sts-dolphin`
- 리랭커: `reranker`
- RAG Reasoning: `rag` (첫 도구 제안만 검사, 실제 검색·최종 답변 생성 아님)

[API 사용법과 재현 명령](../../docs/api/NCP_API_활용가이드.md)을 기준으로 필요한 종류만 진행합니다. 모델·도구별 권한과 비용을 확인한 뒤 `--live`를 추가합니다. 공통 환경 커밋에는 이 실험이 포함되어 있지 않습니다.

공식 Function calling 지원 모델에는 DASH-002·005·007이 포함되지만 현재 CLI의 `function`은 005/007만 허용합니다. `structured`와 `function`의 실호출, RAG의 도구 실행·결과 재전달은 아직 검증하지 않았습니다.
