# 현재 실행 구조와 책임

기준: PR #50 이후 새 A 단일 경로 정리. 실행 명령은 [루트 README](../README.md)를 따른다.

## 실행 경계

frontend는 pipeline HTTP만 부른다. pipeline은 같은 프로세스에서 A·B·C service를 부른다. module-a/b/c HTTP 컨테이너는 개별 시험·평가용이며 전체 실행의 네트워크 중간 단계가 아니다.

- `claview_pipeline.screen.ScreenPipeline`: 유일한 제품 실행기. AView와 사용자 답, 표별 기간 조건 전달 및 B의 답 보존 확인.
- `claview_pipeline.staged.StagePipeline`: 추상 A 단계, B 조건·조회·C 표현, 진행 이벤트, 중단과 주기 맞춤 재개, 실행 기록.
- `claview_pipeline.runtime.ServiceTransports`: 일반 live의 B·KOSIS 클라이언트와 호출 기록·인증 오류 중단.
- `claview_a_api.screen`: A 실행·답 적용과 AView. A 본체의 판단·기간·검증 정책을 호출한다.
- `claview_b_api.service`: B의 조건 결정·자료 조회·정렬 경계.
- `claview_c_api.service`: 네트워크 없이 표현 자료와 계산 카드 생성.

## 계약

요청·응답 모델은 `common/claview_common/contracts`가 기준이다. A HTTP는 `a_http`, 화면 전달은 `a_view`, B 요청은 `b`, 표별 기간은 `period_condition`을 쓴다. 표 식별자 기본형은 `table.TableRef`다. [계약 목록](contracts/README.md)을 참고한다.

사용자 답 필드 → B 계산 → B가 해석하지 못할 때 A 인용 기간 순서다. A 최근 N은 표의 최신 수록, 올해·작년은 기준일을 사용한다. 서로 다른 정책을 동일하다고 쓰지 않는다. B가 명시 답을 지켰다고 확인할 수 없으면 조회하지 않는다.

## 모드·기록

Docker Compose의 기본 모드는 live이며 개발·시험에서는 synthetic을 명시한다. 환경변수 없이 Python을 직접 실행할 때 공통 설정의 기본값은 synthetic이다. 합성 결과는 정확도가 아니다. live 설정 부족·호출 실패를 합성 값으로 숨기지 않는다. HTTP 입력으로 모드·키·기록 경로를 주입할 수 없다.

A 기록은 `runs/a_service`의 모드별 하위 폴더, 전체 흐름은 `runs/pipeline`의 모드별 폴더다. Docker는 프로젝트별 named volume을 사용한다. 되묻기와 저장 결과 복원은 이미 보관한 상태를 사용하며 답 적용은 A 모델 호출을 만들지 않는다.

## 의존 규칙과 배포

A·B·C 본체는 서로 import하지 않는다. API끼리도 import하지 않는다. 공통 모듈은 개별 모듈·pipeline을 import하지 않는다. A 본체는 표준 라이브러리·langgraph와 상대 import 규칙을 유지한다. `lint-imports`의 9개 계약으로 검사한다.

백엔드는 pipeline이 한 번 빌드한 이미지를 공유한다. 모듈 시험·연구 스크립트와 공통 시험 샘플은 제품 이미지에서 제외한다. C의 기간 계산·표시는 공통 기간 함수를 사용한다.

별도 B/C 골든 도구는 정답 표를 명시적으로 주입한다. 제품 실행기의 A 판단을 전역 monkeypatch하지 않는다. 합성 HTTP 전체 흐름은 `pipeline/scripts/smoke_stack.py`로 검사한다.
