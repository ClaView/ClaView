"""Scripted A judgments with the real AView service and synthetic B/fetch."""

from claview_a_api import screen as a_screen
from claview_a_api.synthetic_judges import ScriptJudge, by_question, first_card
from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
from claview_pipeline.screen import ScreenPipeline

PROCEED_Q = "M2 통화량 2023년 연간으로 보여줘"
WAIT_Q = "M2 통화량 2023년 연간"
NO_TABLE_Q = "없는지표 2023년 연간"
STATS = [{"id": "s1", "quote": "M2 통화량", "name": "M2"}]
SCRIPT = {
    PROCEED_Q: {"statistics": STATS, "periods": [{"quote": "2023년", "for": ["s1"]}],
                "frequencies": [{"quote": "연간", "for": ["s1"]}],
                "choices": [{"for": ["s1"], "table": "DT_161Y014"}]},
    WAIT_Q: {"statistics": STATS, "periods": [{"quote": "2023년", "for": ["s1"]}],
             "frequencies": [{"quote": "연간", "for": ["s1"]}],
             "asks": [{"for": "s1", "axis": "stock", "options": [
                 {"value": "평잔", "table": "DT_161Y013"}, {"value": "말잔", "table": "DT_161Y014"}]}]},
    NO_TABLE_Q: {"statistics": [{"id": "s1", "quote": "없는지표", "name": "missing"}],
                 "missing": [{"for": ["s1"], "search_words": ["없는지표"]}]},
}


def judges():
    judge = ScriptJudge(by_question(SCRIPT, first_card))
    return a_screen.Judges(judge, judge, "script")


def make_runner(store, *, model=True, client=True):
    from types import SimpleNamespace

    runner = ScreenPipeline(model=SyntheticSelectionModel(store) if model else None,
                            client=SyntheticFetchClient() if client else None,
                            session=SimpleNamespace(store=store), runs_dir=None, judges_factory=judges)
    runner.mode = "synthetic"
    return runner


def answer_payload(waiting, index=0):
    card = waiting["confirmation"]
    return {"session_id": card["session_id"], "answers": [
        {"question_id": question["requirement_id"], "question_version": question["question_version"],
         "option_id": question["options"][index]["option_id"]}
        for question in card["groups"]
    ]}
