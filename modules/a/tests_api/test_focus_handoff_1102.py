"""Table-local scopes reach B's existing field while original quotes and provenance survive."""

from __future__ import annotations

import copy

from claview_a_api import screen as S
from claview_a_api.synthetic_judges import ScriptJudge, by_question

# Historical .7 fixtures (quote-only period rows): the focus handoff stays independent of contract .8's
# period rows
CONTRACT_VERSION = "tl-contract-2026-10-28.7"
QUESTION = "2024년 월별로 예금은행 대출금리의 신규취급액 기준과 잔액기준을 각각 비교해줘."


def response():
    return {
        "statistics": [
            {"id": "s1", "quote": "신규취급액 기준", "name": "신규 대출금리"},
            {"id": "s2", "quote": "잔액기준", "name": "잔액 대출금리"},
        ],
        "focuses": [
            {"for": ["s1"], "text": "예금은행 대출금리 신규취급액 기준",
             "quotes": ["예금은행 대출금리", "신규취급액 기준"]},
            {"for": ["s2"], "text": "예금은행 대출금리 잔액기준",
             "quotes": ["예금은행 대출금리", "잔액기준"]},
        ],
        "choices": [{"for": ["s1"], "table": "DT_121Y006"},
                    {"for": ["s2"], "table": "DT_121Y015"}],
        "splitters": [
            {"for": ["s1"], "quote": "신규취급액 기준", "axis": "loan_basis",
             "value": "신규취급액", "polarity": "required"},
            {"for": ["s2"], "quote": "잔액기준", "axis": "loan_basis",
             "value": "잔액기준", "polarity": "required"},
        ],
        "periods": [{"for": ["s1", "s2"], "quote": "2024년", "meaning": "range", "refers_to": []}],
        "frequencies": [{"for": ["s1", "s2"], "quote": "월별"}],
    }


def select(question, output, contract=CONTRACT_VERSION):
    judge = ScriptJudge(by_question({question: output}), answer_contract=contract)
    return S.select(question, "2026-10-29", S.Judges(judge, judge, "script")), judge


def test_scopes_use_existing_b_field_and_preserve_original_quotes():
    script = response()
    before = copy.deepcopy(script)
    out, judge = select(QUESTION, script)
    view = out["view"]
    assert script == before and judge.calls == 1
    assert view["status"] == "ready"
    assert [s["quote"] for s in view["statistics"]] == ["신규취급액 기준", "잔액기준"]
    sent = [t for r in view["handoff"]["ready"] for t in r["tables"]]
    assert [(t["table_id"], t["focus"]) for t in sent] == [
        ("DT_121Y006", "예금은행 대출금리 신규취급액 기준"),
        ("DT_121Y015", "예금은행 대출금리 잔액기준"),
    ]
    assert all(set(t) == {"org_id", "table_id", "table_name", "focus"} for t in sent)
    for statistic, scope in zip(view["statistics"], script["focuses"]):
        assert statistic["focus_provenance"] == {
            "text": scope["text"], "quotes": scope["quotes"], "source": "model_scope", "status": "grounded",
        }


def test_invalid_scope_source_is_not_sent_for_the_affected_statistic():
    script = response()
    script["focuses"][0]["quotes"] = ["질문에 없는 대상"]
    out, _ = select(QUESTION, script)
    view = out["view"]
    assert view["statistics"][0]["outcome"] == "failed"
    assert view["statistics"][0]["focus_provenance"]["status"] == "invalid"
    assert [t["table_id"] for r in view["handoff"]["ready"] for t in r["tables"]] == ["DT_121Y015"]


def test_saved_old_answers_keep_their_quote_focus_without_inventing_a_scope():
    script = response()
    script.pop("focuses")
    out, _ = select(QUESTION, script, "tl-contract-2026-10-28.6")
    assert out["view"]["status"] == "ready"
    for stat in out["view"]["statistics"]:
        assert stat["focus"] == stat["quote"]
        assert stat["focus_provenance"]["source"] == "legacy_quote"
        assert stat["focus_provenance"]["status"] == "verified_quote_only"


def test_basis_answer_changes_only_the_table_not_the_question_or_scope():
    question = "2024년 월별 예금은행 대출금리를 보여줘."
    script = {
        "statistics": [{"id": "s1", "quote": "예금은행 대출금리", "name": "대출금리"}],
        "focuses": [{"for": ["s1"], "text": "예금은행 대출금리", "quotes": ["예금은행 대출금리"]}],
        "asks": [{"for": "s1", "axis": "loan_basis", "options": [
            {"value": "신규취급액", "table": "DT_121Y006"},
            {"value": "잔액기준", "table": "DT_121Y015"},
        ]}],
        "periods": [{"for": ["s1"], "quote": "2024년", "meaning": "range", "refers_to": []}],
        "frequencies": [{"for": ["s1"], "quote": "월별"}],
    }
    out, judge = select(question, script)
    assert out["view"]["status"] == "needs_answer"
    item = out["view"]["questions"][0]
    scope = copy.deepcopy(out["view"]["statistics"][0]["focus_provenance"])
    done = S.answer(out["checkpoint"], out["view"]["session"]["session_id"], [{
        "question_id": item["question_id"], "question_version": item["question_version"],
        "option_id": next(o["option_id"] for o in item["options"] if o["value"] == "잔액기준"),
    }])
    assert judge.calls == 1 and done["view"]["status"] == "ready"
    assert done["view"]["session"]["question"] == question
    stat = done["view"]["statistics"][0]
    assert stat["focus_provenance"] == scope
    assert stat["tables"][0]["table_id"] == "DT_121Y015"
