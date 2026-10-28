"""Model step of 2.6 (plan 06): decision points, apply and recheck, reply checks, the model-off path, and the request
client of the model run.

Questions and catalogs are the synthetic ones of the judgement specification (modules/a/tests_legacy/spec/test_spec_judgement.py,
"가상" names; no real table, case or question). The model is a fake client; the request client runs over a fake
transport. No network.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

SPEC = Path(__file__).resolve().parent / "spec"
if str(SPEC) not in sys.path:
    sys.path.insert(0, str(SPEC))

from spec_support import build_layer, itm, table  # noqa: E402

from claview_a_legacy.contract.check import check_analysis  # noqa: E402
from claview_a_legacy.interpret import model_step, nodes  # noqa: E402
from claview_a_legacy.interpret.conditions import find_conditions  # noqa: E402
from claview_a_legacy.interpret.model_calls import RunClient, request_key  # noqa: E402
from claview_a_legacy.interpret.pipeline import interpret_question  # noqa: E402
from claview_a_legacy.interpret.text import normalize_text  # noqa: E402
from claview_a_legacy.reused.model import HcxClient, ModelError, ModelReply  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "modules" / "a" / "scripts" / "run_interp_model.py"


@pytest.fixture(autouse=True)
def _check_8_on(monkeypatch):
    """These model-step cases were written with certainty check 8 on (confirm points exist only then). The policy
    file turned check 8 off on 2026-10-19 (user decision), so this module pins it on for its runs."""
    monkeypatch.setenv("CLAVIEW_V3_VERIFIED_STRUCTURES_ONLY", "1")

ACC = "계정항목별"
ORG = "기관코드별"
BASE = "기준년도구분별"


def ax(obj: str, axis_name: str, values: list, order: int = 1) -> list[dict]:
    ids: dict[str, str] = {}
    rows = []
    for k, v in enumerate(values, 1):
        name, parent = (v if isinstance(v, tuple) else (v, None))
        ids[name] = f"{obj}{k}"
        rows.append(itm(obj, ids[name], name, parent=ids.get(parent) if parent else None, obj_name=axis_name,
                        order=order))
    return rows


CATALOGS = {
    "bop": [table("T1", "가상국제수지표", ax("A", ACC, ["가상경상수지", ("가상상품수지", "가상경상수지"),
                                                    ("가상서비스수지", "가상경상수지")]))],
    "mixed": [
        table("T2", "가상지수표갑", ax("A", ACC, ["가상갑지수", ("가상을지수", "가상갑지수"), "가상A지출",
                                            ("가상B투자", "가상A지출"), ("가상C지출", "가상A지출")])),
        table("T3", "가상지수표을", ax("A", ACC, ["가상갑지수", "가상을지수", "가상A지출", "가상B투자", "가상C지출"])),
    ],
    "sib": [
        table("T7", "가상국외표", ax("A", ACC, ["가상국외자산순증", "가상국외부채순증"])),
        table("T8", "가상대외채무표", ax("A", ACC, ["가상대외채무"])),
        table("T9", "가상대외채권표", ax("A", ACC, ["가상대외채권"])),
    ],
    "scope_one": [
        table("T_LR", "가상대출금리", ax("O", ORG, ["가상은행", "가상조합"])),
        table("T_DR", "가상예금금리", ax("O", ORG, ["가상조합"])),
    ],
    "p_name": [
        table("T_NM1", "가상통화표(말잔)", ax("A", ACC, ["가상M3(말잔 원계열)", "가상현금"])),
        table("T_NM2", "가상통화표(평잔)", ax("A", ACC, ["가상M3(평잔 원계열)", "가상현금"])),
        table("T_NM3", "가상자본수지표", ax("A", ACC, ["가상국외순증(F)", "가상국내순증"])),
    ],
    "rates": [table("T_RT", "가상금리표", ax("A", ACC, ["가상A금리", "가상B금리", "가상C금리", "가상D금리"])
                    + ax("B", BASE, ["신규취급액", "잔액"], order=2))],
    # a lower item also held by a table without its head (split supported)
    "confirm_split": [table("T1", "가상국제수지표", ax("A", ACC, ["가상경상수지", ("가상상품수지", "가상경상수지"),
                                                            ("가상서비스수지", "가상경상수지")])),
                      table("T10", "가상상품표", ax("A", ACC, ["가상상품수지", "가상상품수출"]))],
    # under the head in one common table, on another axis in the other (not row 3, not row 2: "one" supported)
    "confirm_one": [table("T11", "가상지수표병", ax("A", ACC, ["가상X지수", ("가상Y지수", "가상X지수")])),
                    table("T12", "가상지수표정", ax("A", ACC, ["가상X지수"]) + ax("B", ACC, ["가상Y지수"], order=2))],
    "ground": [table("T13", "가상지표표", ax("A", ACC, ["가상갑지수", "가상을지수"]))],
    "gloss": [table("T14", "가상예금표", ax("A", ACC, ["가상A예금", ("가상B예금", "가상A예금")])),
              table("T15", "가상예금표둘", ax("A", ACC, ["가상B예금", "가상C예금"]))],
    "copy2": [table(f"T2{k}", f"가상생산({a}, {b})", ax("A", ACC, ["가상산출"]))
              for k, (a, b) in enumerate([("명목", "원계열"), ("명목", "계절조정"), ("실질", "원계열"), ("실질", "계절조정")])],
    "unused_unknown": [table("T18", "가상기업표", ax("O", "가상업종별", ["가상제조업", "가상전산업"])
                             + ax("A", ACC, ["가상매출률"], order=2))],
    "unused_target": [table("T19", "가상기업표둘", ax("O", ORG, ["가상제조업", "가상전산업"])
                            + ax("A", ACC, ["가상매출률"], order=2))],
    # the row 3 pair of "mixed" and a table of another indicator whose name starts with the head's
    "rename": [table("T2", "가상지수표갑", ax("A", ACC, ["가상갑지수", ("가상을지수", "가상갑지수")])),
               table("T3", "가상지수표을", ax("A", ACC, ["가상갑지수", "가상을지수"])),
               table("T44", "가상총액표", ax("A", ACC, ["가상갑지수총액"]))],
    "role": [table("T50", "가상기관표", ax("O", ORG, ["가상조합", "가상혼합"]) + ax("A", ACC, ["가상잔액"], order=2)),
             table("T52", "가상혼합표", ax("A", ACC, ["가상혼합", "가상잔액"]) + ax("O", ORG, ["가상조합"], order=2))],
    # "role" with a table of another indicator (a second requirement that names an indicator)
    "role_two": [table("T50", "가상기관표", ax("O", ORG, ["가상조합", "가상혼합"]) + ax("A", ACC, ["가상잔액"], order=2)),
                 table("T52", "가상혼합표", ax("A", ACC, ["가상혼합", "가상잔액"]) + ax("O", ORG, ["가상조합"], order=2)),
                 table("T53", "가상수지표", ax("A", ACC, ["가상경상수지"]))],
    # copies of one indicator by two basis axes (finding 5: the owner of a condition among copies)
    "basis2": [table("T60", "가상GDP표", ax("A", ACC, ["가상GDP"]) + ax("P", BASE, ["명목", "실질"], order=2)
                     + ax("S", BASE, ["원계열", "계절조정"], order=3))],
    "two_ind": [table("T20", "가상기업표셋", ax("O", ORG, ["가상제조업", "가상전산업"])
                      + ax("A", ACC, ["가상매출률", "가상회전율"], order=2))],
    # lexical candidates of a guessed name: two indicator values and a target value
    "name_filter": [table("T30", "가상무역표", ax("A", ACC, ["가상수입액", "가상수입량"])),
                    table("T31", "가상기관건수표", ax("O", ORG, ["가상수입처"]) + ax("A", ACC, ["가상건수"], order=2))],
}

MIXED_Q = "가상갑지수와 가상을지수를 보여줘"  # row 3 (spec J3)
LEFTOVER_Q = "가상경상수지 속사정을 보여줘"  # check 5 (spec C5)
SCOPE_Q = "가상은행의 가상대출금리와 가상예금금리를 보여줘"  # check 7 (spec C7)
CERTAIN_Q = "가상경상수지를 보여줘"  # certain (spec C5n)
APPOSITIVE_Q = "가상대외채무, 즉 가상대외채권을 보여줘"  # appositive, not together (spec AP1)
NAME_Q = "가상M3를 보여줘"  # family-level name, not exact (spec P5)
ATTRIBUTION_Q = "가상A금리와 가상B금리를 신규취급액 기준으로 보여주고 가상C금리와 가상D금리를 각각 보여줘"  # spec V1b
SPLIT_Q = "가상경상수지와 가상상품수지를 보여줘"  # row 2, check 8 only
ONE_Q = "가상X지수와 가상Y지수를 보여줘"  # row 8, check 8 only
SIBLING_Q = "가상국외자산순증과 가상국외부채순증을 보여줘"  # row 8 siblings, check 8 only
GROUND_Q = "가상갑지수는 원계열로, 가상을지수는 계절조정으로 보여줘"  # ungrounded split
GLOSS_Q = "가상A예금(가상B예금)을 보여줘"  # bracket gloss
COPY2_Q = "가상산출을 명목과 실질, 원계열과 계절조정으로 보여줘"  # two basis axes
NAME_PART_Q = "가상경상수지 서비스를 보여줘"  # leftover word next to a name
CONDITION_Q = "가상경상수지를 계절조절로 보여줘"  # leftover word near a condition word
UNUSED_Q = "가상제조업과 가상전산업의 가상매출률을 같이 보여줘"  # unused names (values of an axis of unknown role)
TARGET_Q = "가상매출률을 가상제조업 가상전산업으로 보여줘"


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    cache: dict = {}

    def get(name: str):
        if name not in cache:
            cache[name] = build_layer(tmp_path_factory.mktemp(f"model_step_{name}"), CATALOGS[name])
        return cache[name]

    return get


class FakeModel:
    name = "fake"

    def __init__(self, respond):
        self.respond, self.requests = respond, []

    def complete(self, node, system, payload, schema):
        self.requests.append((node, system, payload, schema))
        out = self.respond(payload)
        if isinstance(out, ModelError):
            raise out
        return ModelReply(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False), 1, 1)


def answers(points, want: dict[str, str]) -> dict:
    """A reply choosing, per point kind, the option with the wanted effect (keep for the other kinds)."""
    rows = []
    for p in points:
        effect = want.get(p.label, want.get(p.kind, "keep"))
        option = next(o for o in p.options if o.effect == effect)
        rows.append({"point": p.id, "choice": option.id, "reason": "시험"})
    return {"answers": rows}


def run_with(question: str, layer, want: dict[str, str]):
    prep = model_step.prepare_question(question, layer)
    fake = FakeModel(lambda payload: answers(prep.points, want))
    return prep, fake, interpret_question(question, layer, model=fake)


def point(prep, kind: str):
    """The first point of a kind ("reading") or of a kind and form ("reading:confirm")."""
    found = [p for p in prep.points if kind in (p.kind, p.label)]
    assert found, f"no {kind} point among {[p.label for p in prep.points]}"
    return found[0]


def effects(p) -> list[str]:
    return [o.effect for o in p.options]


def option(p, effect: str):
    return next(o for o in p.options if o.effect == effect)


# ---------------------------------------------------------------- model off: today's output


@pytest.mark.parametrize("name,question", [("mixed", MIXED_Q), ("bop", LEFTOVER_Q), ("scope_one", SCOPE_Q),
                                           ("bop", CERTAIN_Q), (None, MIXED_Q)])
def test_model_off_output_is_the_rule_path(layers, name, question):
    layer = layers(name) if name else None
    text = normalize_text(question)
    conds = find_conditions(text, layer)
    frags = nodes.split_fragments(text, conds, layer)
    terms = nodes.attach_terms(frags, layer, text, conds)
    judgement = nodes.model_slot(nodes.judge_fragments(text, conds, frags, terms, layer))
    interp = nodes.settle_requirements(text, conds, frags, terms, judgement, layer)
    want = {"text": text, "conditions": conds, "fragments": frags, "terms": terms, "judgement": judgement,
            "interpretation": interp}
    new_fields = ("segments", "mentions", "ref_map")  # plan 15 record fields, outside the rule-path output
    for got in (interpret_question(question, layer), interpret_question(question, layer, model=None)):
        assert all(f in got for f in new_fields)
        assert {k: v for k, v in got.items() if k not in new_fields} == want
        assert check_analysis(got, question) == []


def test_certain_question_is_not_sent(layers):
    layer = layers("bop")
    fake = FakeModel(lambda payload: {"answers": []})
    got = interpret_question(CERTAIN_Q, layer, model=fake)
    assert got["judgement"]["certain"] is True and fake.requests == []
    assert got == interpret_question(CERTAIN_Q, layer)
    assert "model_step" not in got["judgement"] and "model" not in got["judgement"]


# ---------------------------------------------------------------- points, payload, schema


def test_points_payload_and_schema(layers):
    prep = model_step.prepare_question(MIXED_Q, layers("mixed"))
    reading = point(prep, "reading")
    assert {"one", "keep", "unknown"} <= {o.effect for o in reading.options}
    for p in prep.points:
        effects = [o.effect for o in p.options]
        assert effects[-2:] == ["keep", "unknown"]
    ids = [o.id for p in prep.points for o in p.options]
    assert len(ids) == len(set(ids))
    pay = prep.payload
    assert pay["question"] == MIXED_Q and [p["id"] for p in pay["points"]] == [p.id for p in prep.points]
    assert set(pay) >= {"question", "conditions", "fragments", "draft", "points"}
    text = json.dumps(pay, ensure_ascii=False)
    assert "가상지수표갑" not in text and "가상지수표을" not in text  # table names are never sent
    answers_schema = prep.schema["properties"]["answers"]
    assert answers_schema["minItems"] == answers_schema["maxItems"] == len(prep.points)
    items = [answers_schema["items"]] if len(prep.points) == 1 else answers_schema["items"]["anyOf"]
    for p, item in zip(prep.points, items):
        assert item["properties"]["point"]["enum"] == [p.id]
        assert item["properties"]["choice"]["enum"] == [o.id for o in p.options]


def test_name_point_lists_candidates_with_counts_and_paths(layers):
    prep = model_step.prepare_question(NAME_Q, layers("p_name"))
    name = point(prep, "name")
    names = [o for o in name.options if o.effect == "name"]
    assert {o.text for o in names} == {"가상M3(말잔 원계열)", "가상M3(평잔 원계열)"}
    assert len(names) <= model_step.MAX_NAMES
    assert {o.effect for o in name.options} >= {"absent", "keep", "unknown"}
    for o in names:
        assert o.info["tables"] == 1
        assert o.info["paths"] == [f"{ACC} > {o.text}"]
    text = json.dumps(prep.payload, ensure_ascii=False)
    assert "가상통화표" not in text  # no table name


def test_word_point_of_a_leftover_word(layers):
    # "part of a name" and "condition" are offered only with concrete candidates; this word has none
    prep = model_step.prepare_question(LEFTOVER_Q, layers("bop"))
    word = point(prep, "word:leftover")
    assert "속사정을" in word.ask
    assert {o.effect for o in word.options} == {"ignore", "keep", "unknown"}
    assert option(word, "ignore").text == "요청·채움 표현(뜻 없음)"


def test_too_many_points_are_not_sent(layers, monkeypatch):
    monkeypatch.setattr(model_step, "MAX_POINTS", 0)
    fake = FakeModel(lambda payload: {"answers": []})
    got = interpret_question(MIXED_Q, layers("mixed"), model=fake)
    j = got["judgement"]
    assert fake.requests == [] and j["model_step"] == "skipped" and j["certain"] is False
    assert j["model"]["notes"][0].startswith("too_many_points")
    assert all(model_step.ASK_REASON in r["reasons"] for r in got["interpretation"]["requirements"])
    assert check_analysis(got, MIXED_Q) == []


def test_no_indicator_question_is_not_sent_and_asks(layers):
    """Check 9 (user decision 10/19): a question of target values only is not sent; every requirement asks."""
    question = "가상제조업과 가상전산업을 보여줘"
    fake = FakeModel(lambda payload: {"answers": []})
    got = interpret_question(question, layers("unused_target"), model=fake)
    j = got["judgement"]
    assert fake.requests == [] and j["model_step"] == "skipped" and j["certain"] is False
    assert "no_indicator" in j["failed_conditions"] and j["model"]["notes"] == ["ask:no_indicator"]
    assert got["interpretation"]["status"] == "ask"
    assert all(model_step.ASK_REASON in r["reasons"] for r in got["interpretation"]["requirements"])
    assert check_analysis(got, question) == []


def test_question_with_an_indicator_is_still_sent(layers):
    """Check 9 only: a question naming an indicator goes to the model as before."""
    prep = model_step.prepare_question(UNUSED_Q, layers("unused_target"))
    assert prep.skip != "no_indicator"


# ---------------------------------------------------------------- apply and recheck


# the confirm_one case of this test was dropped on 2026-10-19 (user decision: check 8 stays off and its confirm-point
# tests are not kept once the verified list holds their structures)
@pytest.mark.parametrize("name,question,label", [("mixed", MIXED_Q, "reading:mixed")])
def test_merge_against_a_table_reading_goes_through_the_table_and_ends_as_ask(layers, name, question, label):
    # the merge is an override of 2.5 (row 2 for the group); checks 1-7 pass on the new judgement, but the catalog
    # V-check (head + lower in every common table) fails: the draft stays and the requirements ask
    layer = layers(name)
    prep, fake, got = run_with(question, layer, {label: "one"})
    one = option(point(prep, label), "one")
    assert one.info["under_head_tables"] == 1 and one.info["common_tables"] == 2
    j = got["judgement"]
    assert len(fake.requests) == 1 and fake.requests[0][0] == model_step.NODE
    assert j["model_step"] == "rejected" and j["certain"] is False and j["model"]["outcome"] == "rejected"
    assert any(n.startswith(model_step.RECHECK_NOTE + "V-req:collapsed:") for n in j["model"]["notes"])
    off = interpret_question(question, layer)["interpretation"]
    on = got["interpretation"]
    assert on["status"] == "ask" and [r["indicator"]["quote"] for r in on["requirements"]] == \
        [r["indicator"]["quote"] for r in off["requirements"]]
    assert all(model_step.ASK_REASON in r["reasons"] for r in on["requirements"])
    assert j["model"]["prompt_sha256"] == model_step.prompt_sha256()
    assert check_analysis(got, question) == []


def test_choices_are_overrides_of_the_judgement_table(layers):
    # the recheck is 2.5 again with the overrides: the merged judgement has one draft of row 2
    layer = layers("mixed")
    prep = model_step.prepare_question(MIXED_Q, layer)
    p = point(prep, "reading:mixed")
    ov, asks = model_step.overrides_of(prep.ctx, prep.points, {p.id: option(p, "one").id})
    assert asks == [] and ov.groups and next(iter(ov.groups.values()))["reading"] == "one"
    chk = model_step.recheck(prep.ctx, ov)
    (draft,) = chk.judgement["requirement_draft"]
    assert draft["row"] == 2 and draft["breakdown"] == ["가상을지수"]
    assert "mixed_reading" not in chk.judgement["failed_conditions"]  # the model decided the row 3 reading
    assert any(f.startswith("V-req:collapsed") for f in chk.failures)
    # without overrides the table is the rule path exactly
    text = normalize_text(MIXED_Q)
    conds = find_conditions(text, layer)
    frags = nodes.split_fragments(text, conds, layer)
    terms = nodes.attach_terms(frags, layer, text, conds)
    assert nodes.judge_fragments(text, conds, frags, terms, layer, overrides=model_step.Overrides()) == \
        nodes.judge_fragments(text, conds, frags, terms, layer)


def test_scope_choice_is_applied_and_accepted(layers):
    prep, _fake, got = run_with(SCOPE_Q, layers("scope_one"), {"scope": "first"})
    scope = point(prep, "scope")
    assert {o.effect for o in scope.options} == {"all", "first", "separate", "keep", "unknown"}
    j = got["judgement"]
    assert j["model_step"] == "done" and j["certain"] is False
    reqs = got["interpretation"]["requirements"]
    with_x = [r for r in reqs if any(t["quote"] == "가상은행" for t in r["targets"])]
    assert len(with_x) == 1 and with_x[0]["indicator"]["quote"] == "가상대출금리"
    assert all(model_step.CONFIRMED_REASON in r["reasons"] for r in reqs) and got["interpretation"]["status"] == "ok"
    assert check_analysis(got, SCOPE_Q) == []


def test_cannot_decide_marks_its_requirement_ask(layers):
    prep, _fake, got = run_with(LEFTOVER_Q, layers("bop"), {"word": "unknown"})
    word = point(prep, "word")
    j = got["judgement"]
    assert j["model_step"] == "done" and j["certain"] is False
    assert j["model"]["choices"][word.id] == next(o.id for o in word.options if o.effect == "unknown")
    asked = [r for r in got["interpretation"]["requirements"] if model_step.ASK_REASON in r["reasons"]]
    assert asked and {r["id"] for r in asked} <= set(word.requirement_ids)
    assert got["interpretation"]["status"] == "ask"
    assert check_analysis(got, LEFTOVER_Q) == []


def test_request_word_is_no_leftover_word(layers):
    # user decision 10/19 21:45 (decision 1): "알고" is a request word of the lexicon, so check 5 finds no leftover
    # word and there is no word:leftover point (was the ok=True case of the test below)
    question = "가상경상수지를 알고 싶어"
    prep = model_step.prepare_question(question, layers("bop"))
    assert not any(p.label == "word:leftover" for p in prep.points)
    got = interpret_question(question, layers("bop"))
    assert got["judgement"]["certain"] is True and got["judgement"]["leftover"] == []


@pytest.mark.parametrize("question,ok", [(LEFTOVER_Q, False)])
def test_ignored_word_must_be_a_request_or_filler_word(layers, question, ok):
    # check 5 leaves the ignored word out; V-cover asks it to be a word of the request/filler list ("알고" is,
    # "속사정" is not); the judgement is never certain
    prep, _fake, got = run_with(question, layers("bop"), {"word:leftover": "ignore"})
    assert point(prep, "word:leftover")
    j = got["judgement"]
    assert j["certain"] is False and j["model_step"] == ("done" if ok else "rejected")
    assert got["interpretation"]["status"] == ("ok" if ok else "ask")
    if not ok:
        assert f"{model_step.RECHECK_NOTE}V-cover:leftover:속사정을" in j["model"]["notes"]
    assert check_analysis(got, question) == []


def test_attribution_choice_goes_through_settle(layers):
    prep, _fake, got = run_with(ATTRIBUTION_Q, layers("rates"), {"attribution": "common"})
    att = point(prep, "attribution")
    cid = att.data["condition"]
    j = got["judgement"]
    assert j["model_step"] == "done" and j["certain"] is False and j["attribution"][cid] == "common"
    interp = got["interpretation"]
    assert cid in interp["common_condition_ids"]
    assert all(cid not in r["ambiguous_condition_ids"] for r in interp["requirements"])
    assert check_analysis(got, ATTRIBUTION_Q) == []


def test_appositive_choice_takes_the_second_name(layers):
    prep, _fake, got = run_with(APPOSITIVE_Q, layers("sib"), {"appositive": "same_b"})
    point(prep, "appositive")
    j = got["judgement"]
    assert j["model_step"] == "done" and j["certain"] is False
    (req,) = got["interpretation"]["requirements"]
    assert req["indicator"]["quote"] == "가상대외채권" and req["status"] == "ok"
    assert check_analysis(got, APPOSITIVE_Q) == []


def test_name_choice_sets_the_catalog_name(layers):
    prep, _fake, got = run_with(NAME_Q, layers("p_name"), {"name": "name"})
    name = point(prep, "name")
    first = next(o for o in name.options if o.effect == "name")
    j = got["judgement"]
    assert j["model_step"] == "done" and j["certain"] is False
    req = got["interpretation"]["requirements"][0]
    assert req["indicator"]["name"] == first.text and req["indicator"]["path"] != "exact"
    assert check_analysis(got, NAME_Q) == []


# ---------------------------------------------------------------- evidence, supported options, new point forms


def _record_ok(got) -> None:
    """The record keeps the contract fields only (recorded kind of the six, option type/tables/paths)."""
    for p in got["judgement"]["model"]["points"]:
        assert p["kind"] in ("name", "appositive", "reading", "scope", "attribution", "word")
        for o in p["options"]:
            assert set(o) <= {"id", "text", "effect", "type", "tables", "paths"}


def test_confirm_point_for_a_check_8_only_draft(layers):
    layer = layers("confirm_split")
    prep, fake, got = run_with(SPLIT_Q, layer, {"reading:confirm": "split"})
    assert prep.ctx.judgement["failed_conditions"] == ["unverified_structure"]
    (p,) = prep.points
    assert p.label == "reading:confirm" and p.kind == "reading"
    assert p.info == {"draft_names": [{"req": "r1", "names": [{"name": "가상경상수지", "type": "지표", "tables": 1},
                                                              {"name": "가상상품수지", "type": "지표", "tables": 2}]}]}
    assert effects(p) == ["split", "keep", "unknown"]
    assert option(p, "split").info == {"names": [{"name": "가상경상수지", "tables": 1},
                                                 {"name": "가상상품수지", "tables": 2, "without_head": 1}]}
    # the keep option carries the evidence of the drafted head + lower reading (finding 2 of the live run)
    assert option(p, "keep").info == {"common_tables": 1, "under_head_tables": 1}
    sent = fake.requests[0][2]["points"][0]
    assert sent["draft_names"] == p.info["draft_names"] and sent["options"][0]["names"][1]["without_head"] == 1
    assert sent["options"][1]["under_head_tables"] == 1
    assert fake.requests[0][2]["draft"][0]["note"] == "머리 + 하위 분해"
    # the split goes through the table (an override) and passes checks 1-7, but V-bundle sees the lower item under the
    # head in every common table (the conflict check of the live run): the draft stays and the requirement asks
    j = got["judgement"]
    assert j["model_step"] == "rejected" and j["certain"] is False
    assert f"{model_step.RECHECK_NOTE}V-bundle:merge:r1|r2" in j["model"]["notes"]
    (req,) = got["interpretation"]["requirements"]
    assert req["breakdown"] == ["가상상품수지"] and model_step.ASK_REASON in req["reasons"]
    assert model_step.CONFIRMED_REASON not in req["reasons"] and got["interpretation"]["status"] == "ask"
    _record_ok(got)
    assert check_analysis(got, SPLIT_Q) == []
    # keeping the drafted head + lower reading is confirmed
    _prep, _fake, kept = run_with(SPLIT_Q, layer, {"reading:confirm": "keep"})
    assert kept["judgement"]["model_step"] == "done" and kept["interpretation"]["status"] == "ok"


# 2026-10-19 user decision (check 8 stays off): the confirm-point tests on the sibling and confirm_one questions were
# dropped -- their structures are verified now, so no confirm point is prepared for them.
@pytest.mark.parametrize("choice", ["differ"])
def test_no_confirmation_after_cannot_decide_or_different(layers, choice):
    question, name, want = (SIBLING_Q, "sib", {"reading:confirm": "unknown"}) if choice == "unknown" else \
        (APPOSITIVE_Q, "sib", {"appositive": "differ"})
    _prep, _fake, got = run_with(question, layers(name), want)
    interp = got["interpretation"]
    assert got["judgement"]["model_step"] == "done" and interp["status"] == "ask"
    assert all(model_step.CONFIRMED_REASON not in r["reasons"] for r in interp["requirements"])


@pytest.mark.parametrize("name,question,effect", [
    ("bop", SPLIT_Q, "split"),  # the lower item is held by no table without its head
])
def test_unsupported_options_are_not_offered(layers, name, question, effect):
    prep = model_step.prepare_question(question, layers(name))
    assert prep.points and all(effect not in effects(p) for p in prep.points)


def test_grounding_point_of_an_ungrounded_split(layers):
    # user decision 10/19 21:45 (decision 3): an ungrounded split no longer blocks certainty; the requirements carry
    # the reason "ungrounded" (table selection decides not_in_catalog) and no grounding point is prepared (only
    # check 8, on in this module, is left: its structure is not verified)
    prep = model_step.prepare_question(GROUND_Q, layers("ground"))
    assert prep.ctx.judgement["failed_conditions"] == ["unverified_structure"]
    assert not any(p.label == "reading:grounding" for p in prep.points)
    got = interpret_question(GROUND_Q, layers("ground"))
    assert "ungrounded_split" not in got["judgement"]["failed_conditions"]
    assert [r["reasons"] for r in got["interpretation"]["requirements"]] == [["ungrounded"], ["ungrounded"]]
    assert check_analysis(got, GROUND_Q) == []


def test_gloss_point_inside_one_item(layers):
    prep, _fake, got = run_with(GLOSS_Q, layers("gloss"), {"appositive:gloss": "gloss_b"})
    p = point(prep, "appositive:gloss")
    assert effects(p) == ["gloss_a", "gloss_b", "differ", "keep", "unknown"]
    assert option(p, "keep").info == {"type": "지표", "tables": 1, "common_tables": 1, "under_head_tables": 1}
    assert option(p, "gloss_b").info == {"type": "지표", "tables": 2}
    assert got["judgement"]["model_step"] == "done"
    (req,) = got["interpretation"]["requirements"]
    assert req["indicator"]["quote"] == "가상B예금" and req["breakdown"] == []
    _record_ok(got)
    assert check_analysis(got, GLOSS_Q) == []


def test_appositive_keep_is_the_first_name_and_not_offered_twice(layers):
    prep, _fake, got = run_with(APPOSITIVE_Q, layers("sib"), {"appositive": "keep"})
    p = point(prep, "appositive")
    assert "same_a" not in effects(p)
    assert option(p, "keep").text == "초안 그대로(같은 지표: '가상대외채무')"
    assert option(p, "same_b").info == {"type": "지표", "tables": 1, "common_tables": 0}
    (req,) = got["interpretation"]["requirements"]
    assert got["judgement"]["model_step"] == "done"
    assert req["indicator"]["quote"] == "가상대외채무" and req["status"] == "ok"
    assert check_analysis(got, APPOSITIVE_Q) == []


def test_copy_per_combination_of_two_basis_axes(layers):
    prep, _fake, got = run_with(COPY2_Q, layers("copy2"), {"reading:basis": "copy"})
    p = point(prep, "reading:basis")
    assert option(p, "copy").info == {"copies": [{"basis": b, "tables": 1} for b in
                                                 ("명목·원계열", "명목·계절조정", "실질·원계열", "실질·계절조정")]}
    assert got["judgement"]["model_step"] == "done"
    reqs = got["interpretation"]["requirements"]
    assert len(reqs) == 4 and all(len(r["condition_ids"]) == 2 and r["status"] == "ok" for r in reqs)
    assert check_analysis(got, COPY2_Q) == []


def test_leftover_word_candidates_are_concrete(layers):
    layer = layers("bop")
    word = point(model_step.prepare_question(NAME_PART_Q, layer), "word:leftover")
    part = option(word, "name_part")
    assert part.text == "이름의 일부: 가상서비스수지" and part.info["tables"] == 1 and part.info["paths"]
    word = point(model_step.prepare_question(CONDITION_Q, layer), "word:leftover")
    assert option(word, "condition").text == "조건: 계절조정"
    _prep, _fake, got = run_with(NAME_PART_Q, layer, {"word:leftover": "name_part"})
    assert got["judgement"]["model_step"] == "done"  # recorded; the code cannot verify it, so the requirement asks
    assert all(model_step.ASK_REASON in r["reasons"] for r in got["interpretation"]["requirements"])


def test_unknown_indicator_word_is_not_ignorable(layers):
    question = "가상경상수지와 가상무명값 보여줘"
    prep, _fake, got = run_with(question, layers("bop"), {"word:leftover": "absent_indicator"})
    word = point(prep, "word:leftover")
    assert effects(word) == ["absent_indicator", "keep", "unknown"]
    assert got["judgement"]["model_step"] == "done"
    reqs = got["interpretation"]["requirements"]
    assert len(reqs) == 2 and model_step.ASK_REASON in reqs[1]["reasons"]
    assert check_analysis(got, question) == []


def test_unused_name_is_never_ignorable(layers):
    prep = model_step.prepare_question(UNUSED_Q, layers("unused_unknown"))
    words = [p for p in prep.points if p.label == "word:unused"]
    assert words and all("ignore" not in effects(p) for p in words)
    # a target value held by the indicator's tables: attach it as a target (a requirement of its own is not offered:
    # no item of the judgement table could stand it)
    prep = model_step.prepare_question(TARGET_Q, layers("unused_target"))
    ctx = prep.ctx
    ctx.judgement = {**ctx.judgement, "failed_conditions": ["unused_name"], "unused_names": ["가상전산업"]}
    (p,) = model_step.word_points(ctx)
    assert effects(p) == ["attach_target", "keep", "unknown"]
    assert option(p, "attach_target").info == {"tables": 1}


def test_target_before_genitive_attaches_to_the_indicators_after_it(layers):
    # "A와 B의 X와 Y": a target-only name before "의" is the target of X and Y, never a requirement of its own
    question = "가상전산업과 가상제조업의 가상매출률과 가상회전율을 비교해줘"
    layer = layers("two_ind")
    text = normalize_text(question)
    conds = find_conditions(text, layer)
    frags = nodes.split_fragments(text, conds, layer)
    terms = nodes.attach_terms(frags, layer, text, conds)
    j = nodes.judge_fragments(text, conds, frags, terms, layer)
    ctx = model_step.make_context(text, conds, frags, terms, j, layer)
    ctx.judgement = {**ctx.judgement, "failed_conditions": ["unused_name"], "unused_names": ["가상제조업"]}
    (p,) = model_step.word_points(ctx)
    assert effects(p) == ["attach_target", "attach_target", "attach_target_all", "keep", "unknown"]
    assert option(p, "attach_target_all").value == ["r1", "r2"]
    p.id = "P1"
    for k, o in enumerate(p.options, 1):
        o.id = f"o{k}"
    ov, asks = model_step.overrides_of(ctx, [p], {"P1": option(p, "attach_target_all").id})
    assert asks == [] and len(ov.attach) == 2
    chk = model_step.recheck(ctx, ov)
    assert not [f for f in chk.failures if f.startswith(("V-ind", "V-req", "V-tgt"))]
    assert all(len({t["span"][0] for t in r["targets"]}) == len(r["targets"])
               for r in chk.interpretation["requirements"])  # attached once


def test_name_point_offers_no_target_only_candidate(layers):
    prep = model_step.prepare_question("가상수입을 보여줘", layers("name_filter"))
    p = point(prep, "name")
    offered = [o for o in p.options if o.effect == "name"]
    assert {o.text for o in offered} == {"가상수입액", "가상수입량"}  # the target value 가상수입처 cannot be the indicator
    assert all(o.info["type"] == "지표" and o.info["tables"] == 1 for o in offered)
    assert "가상수입처" in {k for k, _s in prep.ctx.rel.lexical("가상수입")}  # it is a lexical candidate


def test_name_point_for_an_exact_name_of_an_unknown_type(layers):
    question = "가상매출률과 가상제조업을 보여줘"
    prep, _fake, got = run_with(question, layers("unused_unknown"), {})
    p = point(prep, "name")
    keep = option(p, "keep")
    assert keep.text == "초안 그대로(가상제조업)" and keep.info["type"] == "모름" and keep.info["tables"] == 1
    assert "absent" in effects(p)
    # keeping a name of an unknown type: check 1 fails on the new judgement as on the draft: ask
    assert got["judgement"]["model_step"] == "rejected" and f"{model_step.RECHECK_NOTE}inexact_term" in \
        got["judgement"]["model"]["notes"]
    assert got["interpretation"]["status"] == "ask" and check_analysis(got, question) == []


# ---------------------------------------------------------------- V-checks after the model (both directions)


def _rule(question: str, layer) -> dict:
    import copy as _copy

    return _copy.deepcopy(interpret_question(question, layer))


def _codes(a: dict, layer, ignored=()) -> list[str]:
    from claview_a_legacy.interpret.model_verify import verify_after_model

    return verify_after_model(a, layer, ignored)


def _kinds(codes: list[str]) -> set[str]:
    return {":".join(c.split(":")[:2]) for c in codes}


@pytest.mark.parametrize("name,question", [("bop", CERTAIN_Q), ("sib", SIBLING_Q), ("rates", ATTRIBUTION_Q),
                                           ("bop", SPLIT_Q), ("scope_one", "가상조합의 가상대출금리를 보여줘")])
def test_v_checks_pass_on_sound_readings(layers, name, question):
    assert _codes(_rule(question, layers(name)), layers(name)) == []


def test_v_req_checks(layers):
    sib = layers("sib")
    a = _rule(SIBLING_Q, sib)
    reqs = a["interpretation"]["requirements"]
    reqs.append({**reqs[0], "id": "r3"})
    assert "V-req:duplicate" in _kinds(_codes(a, sib))
    a = _rule(SIBLING_Q, sib)
    a["interpretation"]["requirements"][1]["indicator"] = None
    a["interpretation"]["requirements"][1]["targets"] = []
    assert "V-req:no_indicator" in _kinds(_codes(a, sib))
    rates = layers("rates")
    a = _rule("가상A금리와 가상B금리의 차이를 보여줘", rates)
    assert "V-req:relation" not in _kinds(_codes(a, rates))
    a["interpretation"]["relations"] = []
    assert "V-req:relation" in _kinds(_codes(a, rates))
    mixed = layers("mixed")
    a = _rule(MIXED_Q, mixed)
    r1, _r2 = a["interpretation"]["requirements"]
    r1["breakdown"] = ["가상을지수"]
    a["interpretation"]["requirements"] = [r1]
    assert "V-req:collapsed" in _kinds(_codes(a, mixed))


def test_v_ind_checks(layers):
    sib = layers("sib")
    a = _rule(SIBLING_Q, sib)
    a["interpretation"]["requirements"][0]["indicator"]["name"] = None
    assert "V-ind:not_catalog" in _kinds(_codes(a, sib))
    two = layers("two_ind")
    question = "가상제조업의 가상매출률을 보여줘"
    a = _rule(question, two)
    (r,) = a["interpretation"]["requirements"]
    assert _codes(a, two) == []
    target = next(t for t in r["targets"] if t["quote"] == "가상제조업")
    r["targets"] = [t for t in r["targets"] if t is not target]
    broad = {**r, "id": "r0", "indicator": None, "targets": [target], "quote": "가상제조업", "span": target["span"]}
    a["interpretation"]["requirements"] = [broad, r]
    assert "V-ind:target_before_genitive" in _kinds(_codes(a, two))


def test_v_target_and_breakdown_checks(layers):
    scope = layers("scope_one")
    a = _rule("가상조합의 가상대출금리와 가상예금금리를 보여줘", scope)
    assert "V-tgt:not_together" not in _kinds(_codes(a, scope))
    a = _rule(SCOPE_Q, scope)  # 가상은행 drafted on both: 가상예금금리's table does not hold it
    assert "V-tgt:not_together" in _kinds(_codes(a, scope))
    bop = layers("bop")
    a = _rule(SPLIT_Q, bop)
    (r,) = a["interpretation"]["requirements"]
    r["indicator"] = {**r["indicator"], "name": "가상상품수지", "quote": "가상상품수지"}
    r["breakdown"] = ["가상경상수지"]  # the upper value as a breakdown of the lower one
    assert "V-bd:not_under" in _kinds(_codes(a, bop))
    r["breakdown"] = ["가상없음별"]
    assert "V-bd:axis" in _kinds(_codes(a, bop))
    r["breakdown"] = ["모르는말"]
    assert "V-bd:not_catalog" in _kinds(_codes(a, bop))


def test_v_condition_checks(layers):
    rates = layers("rates")
    a = _rule(ATTRIBUTION_Q, rates)
    interp = a["interpretation"]
    cid = next(c["id"] for c in a["conditions"] if c["kind"] == "basis")
    assert "V-cond:place" not in _kinds(_codes(a, rates))
    interp["common_condition_ids"] = [cid]
    interp["requirements"][0]["condition_ids"] = list(interp["requirements"][0]["condition_ids"]) + [cid]
    assert "V-cond:place" in _kinds(_codes(a, rates))
    ground = layers("ground")
    assert "V-cond:basis" in _kinds(_codes(_rule(GROUND_Q, ground), ground))  # 원계열 in no table of 가상갑지수
    assert "V-cond:basis" not in _kinds(_codes(_rule(ATTRIBUTION_Q, rates), rates))


def test_v_bundle_checks(layers):
    layer = layers("confirm_split")
    prep = model_step.prepare_question(SPLIT_Q, layer)
    p = point(prep, "reading:confirm")
    ov, _asks = model_step.overrides_of(prep.ctx, prep.points, {p.id: option(p, "split").id})
    chk = model_step.recheck(prep.ctx, ov)
    assert "V-bundle:merge:r1|r2" in chk.failures  # 가상상품수지 under 가상경상수지 in every common table
    sib = layers("sib")
    assert "V-bundle:merge" not in _kinds(_codes(_rule(SIBLING_Q, sib), sib))  # siblings
    a = _rule("가상대외채무와 가상대외채권을 보여줘", sib)
    a["interpretation"]["table_group_hint"] = [["r1", "r2"]]  # no table holds both
    assert "V-bundle:hint" in _kinds(_codes(a, sib))
    a["interpretation"]["table_group_hint"] = []
    assert "V-bundle:hint" not in _kinds(_codes(a, sib))


def test_v_cover_checks(layers):
    bop = layers("bop")
    a = _rule(CERTAIN_Q, bop)
    assert _codes(a, bop, ["알고"]) == []
    assert "V-cover:leftover:속사정을" in _codes(a, bop, ["속사정을"])
    a["judgement"]["unused_names"] = ["가상서비스수지"]
    assert "V-cover:unused:가상서비스수지" in _codes(a, bop)


# ---------------------------------------------------------------- draft notes and the role point


def test_draft_notes_say_how_the_code_formed_each_requirement(layers):
    prep = model_step.prepare_question("가상경상수지와 가상경상수지를 보여줘", layers("bop"))
    assert [r["note"] for r in prep.payload["draft"]] == ["반복된 같은 이름을 하나로 합침"]
    assert "같은 이름의 반복은 하나로 합친 초안이 맞다" in model_step.prompt_text()


def _patched_roles(layer, axes):
    """Read the given (table, axis) pairs as indicator axes (the synthetic roles read every axis of T52 as
    targets); returns the saved roles to restore."""
    from claview_a_legacy.catalog.relations import relations_of

    rel = relations_of(layer)
    saved = dict(rel.axis_roles)
    for k in axes:
        rel.axis_roles[k] = "indicator"
    return rel, saved


def _pick(prep, test) -> FakeModel:
    """A fake model choosing, per point, the first option ``test(point, option)`` accepts (keep otherwise)."""
    rows = []
    for p in prep.points:
        o = next((o for o in p.options if test(p, o)), None) or next(o for o in p.options if o.effect == "keep")
        rows.append({"point": p.id, "choice": o.id, "reason": "시험"})
    return FakeModel(lambda payload: {"answers": rows})


def test_model_choice_leaves_the_rule_path_unchanged(layers):
    """Finding 4: a model choice is applied on copies; the cached reading of the question is not changed, so the
    rule path run after it (same layer, same question) is the rule path run before it."""
    layer = layers("p_name")
    before = interpret_question(NAME_Q, layer)
    prep, _fake, got = run_with(NAME_Q, layer, {"name": "name"})
    assert got["judgement"]["model_step"] in ("done", "rejected")
    assert got["interpretation"] != before["interpretation"] or got["judgement"] != before["judgement"]
    assert interpret_question(NAME_Q, layer) == before


@pytest.mark.parametrize("owner,other", [("d2", "d1"), ("d1", "d2")])
def test_attribution_owner_is_the_chosen_copy(layers, owner, other):
    """Finding 5: of two copies on one indicator token, the condition goes to the copy the model chose."""
    layer = layers("basis2")
    question = "가상GDP를 명목과 실질로 보여줘. 원계열 기준으로 보여줘"
    prep = model_step.prepare_question(question, layer)
    fake = _pick(prep, lambda p, o: p.kind == "attribution" and o.effect == "owner" and o.value == owner)
    got = interpret_question(question, layer, model=fake)
    assert got["judgement"]["model_step"] == "done", got["judgement"]["model"].get("notes")
    cid = next(c["id"] for c in got["conditions"] if c["quote"] == "원계열")
    by_draft = {d["id"]: d for d in got["judgement"]["requirement_draft"]}
    assert cid in by_draft[owner]["condition_ids"] and cid not in by_draft[other]["condition_ids"]


@pytest.mark.parametrize("chosen", ["가상서비스수지", "가상상품수지"])
def test_appositive_choice_applies_the_chosen_candidate(layers, chosen):
    """Finding 6: "A, 즉 B, 즉 C": the chosen candidate itself is applied, not the first one."""
    layer = layers("bop")
    question = "가상경상수지, 즉 가상상품수지, 즉 가상서비스수지를 보여줘"
    prep = model_step.prepare_question(question, layer)
    fake = _pick(prep, lambda p, o: p.kind == "appositive" and o.effect == "same_b" and o.value.tok.quote == chosen)
    got = interpret_question(question, layer, model=fake)
    (req,) = got["interpretation"]["requirements"]
    assert req["indicator"]["quote"] == chosen, got["judgement"]["model"].get("notes")


def test_role_rule_reads_a_target_as_the_indicator(layers):
    """Role rule of the judgement table (user decision 10/19): one target name on an indicator axis of a table holding
    the other target is the indicator; no model point is needed."""
    layer = layers("role")
    rel, saved = _patched_roles(layer, [("T52", "A")])
    try:
        question = "가상조합의 가상혼합을 알려줘"
        prep, fake, got = run_with(question, layer, {})
        assert not [p for p in prep.points if p.label == "name:role"]
        (req,) = got["interpretation"]["requirements"]
        assert req["indicator"]["quote"] == "가상혼합" and [t["quote"] for t in req["targets"]] == ["가상조합"]
        assert "no_indicator" not in got["judgement"]["failed_conditions"]
        assert check_analysis(got, question) == []
    finally:
        rel.axis_roles.clear()
        rel.axis_roles.update(saved)


def test_role_point_reads_a_target_as_the_indicator(layers):
    """Two target names on indicator axes of a table holding the other: no role rule, the model's role point
    decides (here: the last target read as the indicator)."""
    layer = layers("role_two")
    rel, saved = _patched_roles(layer, [("T52", "A"), ("T52", "O")])
    try:
        question = "가상조합의 가상혼합을 알려주고 가상경상수지도 보여줘"
        prep, _fake, got = run_with(question, layer, {"name:role": "as_indicator"})
        p = point(prep, "name:role")
        assert p.kind == "name" and effects(p) == ["as_indicator", "keep", "unknown"]
        assert option(p, "as_indicator").info["with_targets"] == 1
        j = got["judgement"]
        assert j["model_step"] == "done", j["model"].get("notes")
        req = got["interpretation"]["requirements"][0]
        assert req["indicator"]["quote"] == "가상혼합" and [t["quote"] for t in req["targets"]] == ["가상조합"]
        assert check_analysis(got, question) == []
    finally:
        rel.axis_roles.clear()
        rel.axis_roles.update(saved)


# ---------------------------------------------------------------- reply and call failures


@pytest.mark.parametrize("reply,problem", [
    ("not json", "reply_not_json"),
    ({"answers": []}, "reply_missing_point"),
    ("wrong_choice", "reply_unknown_choice"),
    ({"result": 1}, "reply_shape"),
])
def test_bad_reply_fails_and_keeps_the_draft(layers, reply, problem):
    layer = layers("mixed")
    prep = model_step.prepare_question(MIXED_Q, layer)

    def respond(payload):
        if reply == "wrong_choice":
            return {"answers": [{"point": p.id, "choice": "o999", "reason": ""} for p in prep.points]}
        return reply

    got = interpret_question(MIXED_Q, layer, model=FakeModel(respond))
    j = got["judgement"]
    assert j["model_step"] == "failed" and j["model"]["notes"] == [problem] and j["certain"] is False
    assert got["interpretation"] == interpret_question(MIXED_Q, layer)["interpretation"]
    assert check_analysis(got, MIXED_Q) == []


def test_call_error_fails_and_keeps_the_draft(layers):
    layer = layers("mixed")
    got = interpret_question(MIXED_Q, layer, model=FakeModel(lambda payload: ModelError("transient", "t")))
    j = got["judgement"]
    assert j["model_step"] == "failed" and j["model"]["notes"] == ["error:transient"]
    assert got["interpretation"] == interpret_question(MIXED_Q, layer)["interpretation"]


# ---------------------------------------------------------------- review 10/19 (Codex r1) findings 3-6


def test_merge_evidence_is_read_for_the_final_head_name(layers):
    # finding 3: a name chosen for the head goes into the table first; the merge is then read for the final name,
    # under which the lower item is in no table: V-bd fails for it
    layer = layers("rename")
    prep = model_step.prepare_question(MIXED_Q, layer)
    reading = point(prep, "reading:mixed")
    ov, _asks = model_step.overrides_of(prep.ctx, prep.points, {reading.id: option(reading, "one").id})
    head = prep.ctx.drafts[prep.ctx.did[reading.requirement_ids[0]]].indicator.tok
    ov.names[(head.start, head.end)] = "가상갑지수총액"
    chk = model_step.recheck(prep.ctx, ov)
    (req,) = chk.interpretation["requirements"]
    assert req["indicator"]["name"] == "가상갑지수총액" and req["indicator"]["path"] == "lexical"
    assert "V-bd:not_under:r1|가상을지수" in chk.failures


def test_attribution_needs_a_table_holding_the_basis_value(layers):
    # finding 4: no table of either indicator holds 계절조정 (with_condition 0): every attribution ends as ask
    question = "가상A금리와 가상B금리의 차이를 보여줘. 계절조정 기준으로"
    layer = layers("rates")
    p = point(model_step.prepare_question(question, layer), "attribution")
    assert [o.info.get("with_condition") for o in p.options if o.effect == "owner"] == [0, 0]
    off = interpret_question(question, layer)["interpretation"]
    for effect in ("owner", "common"):
        _prep, _fake, got = run_with(question, layer, {"attribution": effect})
        j = got["judgement"]
        assert j["model_step"] == "rejected", effect
        assert any(n.startswith(f"{model_step.RECHECK_NOTE}V-cond:basis:") for n in j["model"]["notes"])
        on = got["interpretation"]
        assert on["status"] == "ask" and [r["ambiguous_condition_ids"] for r in on["requirements"]] == \
            [r["ambiguous_condition_ids"] for r in off["requirements"]]
    # a held basis value is accepted (spec V1b, 신규취급액 on the basis axis of the table)
    assert run_with(ATTRIBUTION_Q, layers("rates"), {"attribution": "common"})[2]["judgement"]["model_step"] == "done"


def test_http_429_leaves_the_step_pending(layers):
    # finding 5: a deferred request is not a failure; a later run sends it again
    layer = layers("mixed")
    got = interpret_question(MIXED_Q, layer, model=FakeModel(
        lambda payload: ModelError("transient", "HTTP 429", http_status=429, retry_after=1.0)))
    j = got["judgement"]
    assert j["model_step"] == "pending" and j["certain"] is False
    assert j["model"]["notes"] == [model_step.DEFERRED_NOTE] and j["model"]["outcome"] == "skipped"
    assert got["interpretation"] == interpret_question(MIXED_Q, layer)["interpretation"]
    assert check_analysis(got, MIXED_Q) == []


@pytest.mark.parametrize("answer,problem", [
    ({"choice": "C", "reason": 7}, "reply_shape"),  # reason not a string
    ({"choice": "C"}, "reply_shape"),  # reason missing
    ({"choice": "C", "reason": "r", "note": "x"}, "reply_shape"),  # a field the schema has not
    ({"choice": "C", "reason": "가" * 81}, "reply_reason_too_long"),
    ("top_extra", "reply_shape"),  # a top-level field the schema has not
    ("duplicate", "reply_duplicate_point"),
])
def test_reply_must_hold_the_schema_exactly(layers, answer, problem):
    # finding 6
    layer = layers("mixed")
    prep = model_step.prepare_question(MIXED_Q, layer)
    (p,) = prep.points
    keep = option(p, "keep").id
    if answer == "top_extra":
        reply = {"answers": [{"point": p.id, "choice": keep, "reason": "r"}], "extra": 1}
    elif answer == "duplicate":
        reply = {"answers": [{"point": p.id, "choice": keep, "reason": "r"}] * 2}
    else:
        reply = {"answers": [{"point": p.id, **{k: (keep if v == "C" else v) for k, v in answer.items()}}]}
    got = interpret_question(MIXED_Q, layer, model=FakeModel(lambda payload: reply))
    assert got["judgement"]["model_step"] == "failed" and got["judgement"]["model"]["notes"] == [problem]
    good = {"answers": [{"point": p.id, "choice": keep, "reason": "가" * 80}]}
    # a well-formed reply is read (then rechecked: keeping the row 3 draft still fails check 2, so it asks)
    assert interpret_question(MIXED_Q, layer, model=FakeModel(lambda payload: good))["judgement"]["model_step"] \
        == "rejected"


def test_reason_length_agrees_in_prompt_schema_and_parser(layers):
    prep = model_step.prepare_question(MIXED_Q, layers("mixed"))
    items = prep.schema["properties"]["answers"]["items"]
    assert model_step.REASON_MAX == 80 and items["properties"]["reason"]["maxLength"] == 80
    assert "reason은 질문 글자와 근거를 들어 80자 이하" in model_step.prompt_text()


# ---------------------------------------------------------------- the request client (fake transport)


FAKE_KEY = "fake-key-for-tests"
PAYLOAD = {"question": "q", "points": [{"id": "P1"}]}
SCHEMA = {"type": "object"}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(steps: list, sent: list):
    def opener(req, timeout):
        sent.append(req.full_url)
        step = steps.pop(0)
        if isinstance(step, int):
            raise urllib.error.HTTPError("https://example.invalid", step, "x", {"retry-after": "0"}, None)
        body = {"status": {"code": "20000"},
                "result": {"message": {"content": step}, "finishReason": "stop",
                           "usage": {"promptTokens": 100, "completionTokens": 7}}}
        return _Resp(json.dumps(body).encode())
    return opener


def test_run_client_defers_429_without_billing(tmp_path):
    cache, ledger = tmp_path / "cache.jsonl", tmp_path / "ledger.jsonl"
    sent: list = []
    client = RunClient(mode="live", cache_path=cache, ledger_path=ledger, prompt_sha="p",
                       inner=HcxClient(FAKE_KEY, min_interval=0, opener=_opener([429], sent)), max_billed=1)
    client.case_id = "c1"
    with pytest.raises(ModelError):
        client.complete("INTERP", "s", PAYLOAD, SCHEMA)
    assert (client.deferred, client.billed, client.last, client.stop_reason) == (1, 0, "deferred", None)
    assert not cache.exists()
    # a later run fills the deferred request; the 429 used none of the one billed request allowed
    sent2: list = []
    client2 = RunClient(mode="live", cache_path=cache, ledger_path=ledger, prompt_sha="p",
                        inner=HcxClient(FAKE_KEY, min_interval=0, opener=_opener(['{"answers": []}'], sent2)),
                        max_billed=1)
    client2.case_id = "c1"
    assert client2.complete("INTERP", "s", PAYLOAD, SCHEMA).text == '{"answers": []}'
    assert (client2.billed, client2.answered, len(sent2)) == (1, 1, 1)
    with pytest.raises(ModelError):  # the budget is used up: a new request is not sent
        client2.complete("INTERP", "s", {**PAYLOAD, "question": "other"}, SCHEMA)
    assert len(sent2) == 1 and client2.last == "stopped" and client2.stop_reason
    # replay: the cached reply comes back, a missing one is recorded, nothing is sent
    client3 = RunClient(mode="replay", cache_path=cache, ledger_path=ledger, prompt_sha="p")
    client3.case_id = "c2"
    assert client3.complete("INTERP", "s", PAYLOAD, SCHEMA).text == '{"answers": []}'
    with pytest.raises(ModelError):
        client3.complete("INTERP", "s", {**PAYLOAD, "question": "other"}, SCHEMA)
    assert (client3.cache_hits, client3.missing, client3.billed) == (1, 1, 0)
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in rows] == ["deferred", "ok", "missing"]
    assert rows[1]["request_sha256"] == request_key("INTERP", "p", PAYLOAD, SCHEMA) and rows[1]["points"] == 1
    assert FAKE_KEY not in ledger.read_text(encoding="utf-8") + cache.read_text(encoding="utf-8")


def test_run_client_retries_transient_errors_twice(tmp_path):
    sent: list = []
    client = RunClient(mode="live", cache_path=tmp_path / "cache.jsonl", ledger_path=tmp_path / "ledger.jsonl",
                       prompt_sha="p", inner=HcxClient(FAKE_KEY, min_interval=0, opener=_opener([500, 500, 500], sent)),
                       max_billed=10)
    with pytest.raises(ModelError) as err:
        client.complete("INTERP", "s", PAYLOAD, SCHEMA)
    assert err.value.error_class == "transient" and len(sent) == 3 and client.billed == 3
    assert not (tmp_path / "cache.jsonl").exists()  # a transient failure is not cached: a later run tries again


def test_run_client_stops_when_tokens_exceed_the_estimate(tmp_path):
    replies = [json.dumps({"n": k}) for k in range(10)]
    client = RunClient(mode="live", cache_path=tmp_path / "cache.jsonl", ledger_path=tmp_path / "ledger.jsonl",
                       prompt_sha="p", inner=HcxClient(FAKE_KEY, min_interval=0, opener=_opener(replies, [])),
                       max_billed=100, est_in=50)  # 100 tokens in per reply > 1.5 x 50
    for k in range(10):
        client.complete("INTERP", "s", {"points": [], "k": k}, SCHEMA)
    assert client.stop_reason and "tokens_in" in client.stop_reason
    with pytest.raises(ModelError):
        client.complete("INTERP", "s", {"points": [], "k": 99}, SCHEMA)


# ---- review 10/19 (Codex r1) findings 1 and 2


@pytest.mark.parametrize("bad", ["", "   ", "fake key", "fake-key\nX-Other: 1", "fake\x00key", "fake​key"])
def test_hcx_refuses_an_unusable_key_without_echoing_it(bad):
    with pytest.raises(ModelError) as err:
        HcxClient(bad, min_interval=0)
    assert err.value.error_class == "auth" and (not bad.strip() or bad.strip() not in str(err.value))
    assert HcxClient(" " + FAKE_KEY + "\n", min_interval=0, opener=lambda req, timeout: None)._key == FAKE_KEY


def test_hcx_hides_any_exception_while_sending(tmp_path):
    # finding 1: http.client refuses an invalid header with a message that holds the header value (the key)
    def opener(req, timeout):
        raise ValueError(f"Invalid header value {req.get_header('Authorization')!r}")

    with pytest.raises(ModelError) as err:
        HcxClient(FAKE_KEY, min_interval=0, opener=opener).complete("T2", "s", {}, {})
    e = err.value
    assert e.error_class == "other" and str(e) == "ValueError" and FAKE_KEY not in str(e)
    assert e.__cause__ is None and e.__suppress_context__ is True


def _ledger_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_run_client_counts_billed_requests_across_runs(tmp_path):
    # finding 2: a later run over the same folder starts from the billed requests already in the ledger
    ledger = tmp_path / "ledger.jsonl"
    _ledger_rows(ledger, [{"status": "error", "error_class": "transient"}] * 2 + [{"status": "deferred"},
                                                                                  {"status": "missing"}])
    sent: list = []
    client = RunClient(mode="live", cache_path=tmp_path / "cache.jsonl", ledger_path=ledger, prompt_sha="p",
                       inner=HcxClient(FAKE_KEY, min_interval=0, opener=_opener(['{"answers": []}'] * 3, sent)),
                       max_billed=3)
    assert client.billed_before == 2 and client.stop_reason is None
    client.complete("INTERP", "s", PAYLOAD, SCHEMA)
    with pytest.raises(ModelError):  # 2 earlier + 1 now = the cap of 3
        client.complete("INTERP", "s", {**PAYLOAD, "question": "other"}, SCHEMA)
    assert len(sent) == 1 and client.counts()["billed_total"] == 3 and "max-billed" in client.stop_reason
    with pytest.raises(ValueError):
        RunClient(mode="replay", cache_path=tmp_path / "c.jsonl", ledger_path=ledger, prompt_sha="p", max_billed=301)


@pytest.mark.parametrize("rows,word", [
    ([{"status": "error", "error_class": "other"}] * 3, "errors in a row"),
    ([{"status": "ok", "tokens_in": 100}] * 10, "tokens_in"),
])
def test_a_stopped_folder_stays_stopped_unless_resumed(tmp_path, rows, word):
    ledger = tmp_path / "ledger.jsonl"
    _ledger_rows(ledger, rows)
    sent: list = []

    def client(resume: bool) -> RunClient:
        return RunClient(mode="live", cache_path=tmp_path / "cache.jsonl", ledger_path=ledger, prompt_sha="p",
                         inner=HcxClient(FAKE_KEY, min_interval=0, opener=_opener(['{"answers": []}'], sent)),
                         max_billed=300, est_in=50, resume_after_stop=resume)

    stopped = client(False)
    assert stopped.stop_reason and word in stopped.stop_reason
    with pytest.raises(ModelError):
        stopped.complete("INTERP", "s", PAYLOAD, SCHEMA)
    assert sent == [] and stopped.last == "stopped"
    resumed = client(True)
    assert resumed.stop_reason is None
    assert resumed.complete("INTERP", "s", PAYLOAD, SCHEMA).text == '{"answers": []}' and len(sent) == 1


# ---------------------------------------------------------------- the run script (child process)


def _child_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k != "CLOVASTUDIO_API_KEY"}
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _set_file(tmp_path: Path) -> Path:
    path = tmp_path / "set.jsonl"
    path.write_text(json.dumps({"id": "x1", "template": "t", "question": "가상 질문"}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("extra,message", [([], "--allow-external"), (["--allow-external"], "CLOVASTUDIO_API_KEY")])
def test_run_script_live_needs_permission_and_key(tmp_path, extra, message):
    out = tmp_path / "out"
    done = subprocess.run([sys.executable, "-B", "-X", "utf8", str(SCRIPT), "--set", str(_set_file(tmp_path)),
                           "--mode", "live", "--out", str(out)] + extra, capture_output=True, text=True,
                          encoding="utf-8", env=_child_env(), timeout=120)
    assert done.returncode == 2 and message in done.stderr and "nothing was sent" in done.stderr
    assert not out.exists()


def test_run_script_refuses_an_unusable_key_and_a_large_cap(tmp_path):
    # findings 1 and 2: the value is never echoed; --max-billed above 300 is refused before anything runs
    out = tmp_path / "out"
    bad = "fake-key-with\nnewline"
    env = {**_child_env(), "CLOVASTUDIO_API_KEY": bad}
    base = [sys.executable, "-B", "-X", "utf8", str(SCRIPT), "--set", str(_set_file(tmp_path)), "--out", str(out)]
    done = subprocess.run(base + ["--mode", "live", "--allow-external"], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=120)
    assert done.returncode == 2 and "not a usable key" in done.stderr and "nothing was sent" in done.stderr
    assert "fake-key-with" not in done.stderr + done.stdout
    done = subprocess.run(base + ["--mode", "dry", "--max-billed", "301"], capture_output=True, text=True,
                          encoding="utf-8", env=_child_env(), timeout=120)
    assert done.returncode == 2 and "--max-billed must be 1..300" in done.stderr
    assert not out.exists()


def test_run_script_shows_only_the_class_of_an_exception(tmp_path):
    # finding 1: an exception around the run is shown by its class only, never its message (here a missing set file
    # whose name stands in for a secret the message would carry)
    missing = tmp_path / f"{FAKE_KEY}.jsonl"
    done = subprocess.run([sys.executable, "-B", "-X", "utf8", str(SCRIPT), "--set", str(missing), "--mode", "dry",
                           "--out", str(tmp_path / "out")], capture_output=True, text=True, encoding="utf-8",
                          env=_child_env(), timeout=120)
    assert done.returncode == 1 and "run stopped: FileNotFoundError" in done.stderr
    assert FAKE_KEY not in done.stdout + done.stderr and "Traceback" not in done.stderr
