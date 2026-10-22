# A 평가 계약 테스트 자료

현재 평가 기준은 [기존 원본22](../golden_set_v1.json)입니다. 이 폴더의 회귀22는 같은 원문·정답의 A 계약 투영본입니다.

임의 추가48(dev36·expression4·family8)은 **테스트 전용**입니다. 실제 성능 지표·개선율에 합산하지 않습니다. gold/test라는 파일명은 사람 검수를 뜻하지 않습니다. draft와 최초 manifest를 보존합니다. 유보12건은 과거 진단에서 이미 개봉했으며 새 블라인드 자료가 아닙니다.

`tests/test_a_eval_stage2.py`가 원 질문·정답 격리·분할 해시를 검사하므로 파일을 유지합니다. 정답은 실행 후 평가기만 읽습니다. 과거 생성기·점수·개봉 기록은 별도 보관본으로 이관했습니다.

```powershell
uv run --env-file .env --locked python -m pytest -q tests/test_a_eval_stage2.py
```

향후 자료 추가·증강은 별도 의미 검수 후 새 판본으로 진행합니다.
