"""Plan 19 stages 4-5 (reduced scope, user 10/21): the model input layers.

1. a name found through its dot-free form is looked up by that key (the same candidates as the spaced form);
2. sense order by the question's positive explicit basis and calc conditions (a negated one raises nothing), spelling
   variants (spacing, footnote mark) as one sense, omitted senses recorded with the reason and kept locally;
3. context rules of the segment layer: a request word that is also a word of another lexicon class is a request only
   in a request position; a joiner particle marks a list only between two nominals;
4. the prompt rules and the shrink order;
5. the input version label.

Synthetic catalog (made-up "가상" names and table IDs, plus a few general words the rules read); hand-made candidate
rows for the order rules; the real catalog only for the read-only regression on development questions (skipped when
the files are absent). IDs are found by their words, never fixed. No network, no model.
"""

from __future__ import annotations

import json

import pytest
from catalog_synth import write_inputs, write_raw

from claview_a import paths
from claview_a.catalog.layer import load_layer
from claview_a.catalog.occurrences import occurrences_of
from claview_a.interpret import decompose_payload as DP
from claview_a.interpret import lexicon as LX
from claview_a.interpret.conditions import find_conditions
from claview_a.interpret.decompose_payload import build_payload, build_senses, prompt_text, spelling_key
from claview_a.interpret.decompose_provider import versions
from claview_a.interpret.decompose_verify import verify
from claview_a.interpret.mentions import build_mentions
from claview_a.interpret.segments import build_segments
from claview_a.interpret.text import normalize_text

ITEMS = [("m1", "가상요소", None, None, None)]
MONEY = ["가상통화(말잔 원계열)", "가상통화(말잔 계절조정계열)", "가상통화(평잔 원계열)", "가상통화(평잔 계절조정계열)",
         "가상통화(Old series)", "가상통화 (광의)"]
TABLES = [
    ("T_A", "가상대출금", "Synthetic Loans", [
        ("REG", "지역코드별", "REGION", [("r1", "가상서울", None, None, None), ("r2", "가상부산", None, None, None)]),
        ("IND", "업종코드별", "INDUSTRY", [("i1", "제조업", None, None, None), ("i2", "건설업", None, None, None)]),
        ("ACC", "계정항목별", "ACC", [("a1", "금과 SDRs", None, None, None), ("a2", "가상갑 가상병", None, None, None)]),
    ]),
    *[(f"T_M{n}", title, f"Synthetic Money {n}", [("ACC", "계정항목별", "ACC", ITEMS)])
      for n, title in enumerate(MONEY, start=1)],
]


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan19_stage45")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


class Q:
    """One question read up to the payload, with helpers that find IDs by their words."""

    def __init__(self, question: str, layer):
        self.text = normalize_text(question)
        self.conditions = find_conditions(self.text, layer)
        self.segments = build_segments(self.text, self.conditions, layer)
        self.mentions = build_mentions(self.text, self.conditions, self.segments, layer)
        self.payload, self.local = build_payload(self.text, self.conditions, self.segments, self.mentions, layer)

    def seg(self, quote: str, nth: int = 0) -> dict:
        return [x for x in self.segments if x["quote"] == quote][nth]

    def s(self, quote: str, nth: int = 0) -> str:
        return self.seg(quote, nth)["segment_id"]

    def kind(self, quote: str) -> str:
        return self.seg(quote)["kind"]

    def name_mention(self, quote: str) -> dict:
        """The name mention written exactly over one segment with that quote."""
        sid = self.s(quote)
        lo, hi = self.seg(quote)["span"]
        spans = self.local["mention_spans"]
        (m,) = [m for m in self.mentions if m["kind"] == "name" and m["segment_ids"] == [sid]
                and (not spans or tuple(spans[m["mention_id"]]) == (lo, hi))]
        return m

    def markers(self) -> list[tuple[str, str]]:
        """(quote, marker) of the marker mentions, read by their raw spans."""
        raw = self.text["raw"]
        spans = self.local["mention_spans"]
        return [(raw[slice(*spans[m["mention_id"]])], m["marker"]) for m in self.mentions if m["kind"] == "marker"]

    def requests(self) -> list[str]:
        raw = self.text["raw"]
        spans = self.local["mention_spans"]
        return [raw[slice(*spans[m["mention_id"]])] for m in self.mentions if m["kind"] == "request"]

    def kept_names(self, quote: str) -> list[tuple[str, str]]:
        m = self.name_mention(quote)
        return [(self.local["senses"][z]["name"], self.local["senses"][z]["role"])
                for z in self.local["mention_kept"][m["mention_id"]]]

    def z(self, name: str, role: str) -> str:
        (z,) = {r["sense_id"] for r in self.payload["candidate_summary"] if r["name"] == name and r["role"] == role}
        return z


def R(*sids, z=None, sq=None) -> dict:
    return {"segment_ids": list(sids), "subquote": sq, "sense_id": z}


def req(rid="r1", indicator=None) -> dict:
    return {"id": rid, "indicator": indicator, "targets": [], "basis_refs": [], "context": None, "breakdowns": []}


def reading(reqs, request=(), unused=()) -> dict:
    return {"requirements": list(reqs), "condition_bindings": [], "relations": [], "polarity_links": [],
            "request_refs": list(request), "unused_refs": list(unused)}


# ---------------------------------------------------------------- 1. middle-dot names: candidates by the span's key


def test_a_name_written_with_a_middle_dot_gets_the_candidates_of_the_spaced_form(layer):
    dotted = Q("가상대출금 가상갑·가상병 보여줘", layer)
    spaced = Q("가상대출금 가상갑 가상병 보여줘", layer)
    # precondition: the written form itself is no key of the occurrence layer (the defect of review finding 11)
    assert occurrences_of(layer).lookup("가상갑·가상병") == []
    got = dotted.name_mention("가상갑·가상병")["candidates"]
    want = spaced.name_mention("가상갑 가상병")["candidates"]
    assert got == want != []
    # the payload names the senses instead of an empty row
    rows = [r for r in dotted.payload["candidate_summary"]
            if r["mention_id"] == dotted.name_mention("가상갑·가상병")["mention_id"]]
    assert rows and all(r["sense_id"] is not None for r in rows)
    # the segments stay as they were: one name segment, the raw question restored
    assert dotted.kind("가상갑·가상병") == "name"
    assert "".join(s["quote"] for s in dotted.segments) == dotted.text["raw"]


# ---------------------------------------------------------------- 2. request words in context


@pytest.mark.parametrize("question, word", [
    ("가상대출금 한 사람당 보여줘", "한"),  # a quantity before a noun
    ("가상대출금을 함께 나눠 보여줘", "함께"),  # simultaneity before a divide word
])
def test_a_shared_request_word_out_of_a_request_position_is_a_word(layer, question, word):
    q = Q(question, layer)
    assert word in {w for w in LX.REQUEST_WORDS} and q.kind(word) == "word"
    assert all(word not in r for r in q.requests())
    assert q.kind("보여줘") == "request"


@pytest.mark.parametrize("question, phrase", [
    ("가상대출금 보여줘", "보여줘"),
    ("가상대출금 알려줘", "알려줘"),
    ("가상대출금을 함께 보여줘", "함께 보여줘"),  # a request position: the request word after it
    ("가상대출금을 한 그래프로 보여줘", "한 그래프로 보여줘"),  # a run of request words judged from its end
])
def test_request_words_in_a_request_position_stay_requests(layer, question, phrase):
    q = Q(question, layer)
    assert phrase in q.requests()
    assert all(q.kind(w) == "request" for w in phrase.split())


def test_negation_and_exclusion_markers_are_unaffected(layer):
    q = Q("가상서울 말고 가상부산 가상대출금", layer)
    assert ("말고", "negation") in q.markers() and q.kind("말고") == "marker"
    x = Q("가상서울 제외하고 가상대출금 보여줘", layer)
    assert ("제외하고", "exclusion") in x.markers() and x.kind("제외하고") == "marker"


def test_a_demoted_word_set_aside_as_a_request_is_kept_as_unresolved(layer):
    """The plan's handling table: a demoted word put into the request refs is the existing non-fatal
    unused_unresolved finding (never dropped silently)."""
    q = Q("가상대출금 한 사람당 보여줘", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    p = {"primary": reading([req("r1", ind)], request=[R(q.s("한")), R(q.s("보여줘"))],
                            unused=[{"ref": R(q.s("사람당")), "reason": "unattached"}]),
         "alternative": None, "ambiguities": []}
    out = verify(p, q.payload, q.local, q.text, q.conditions, q.segments, q.mentions)
    assert out["outcome"] == "accept"
    found = [it for it in out["issues"] if it["code"] == "unused_unresolved" and it["segment_ids"] == [q.s("한")]]
    assert found and found[0]["fatal"] is False and found[0]["detail"] == "request"


# ---------------------------------------------------------------- 3. joiner particles in context


def test_a_joiner_between_two_names_is_a_list_mark(layer):
    q = Q("제조업하고 건설업 보여줘", layer)
    assert ("하고", "list") in q.markers()
    sid = q.s("하고")
    assert {s["id"]: s["marker"] for s in q.payload["segments"]}[sid] == "list"
    both = Q("제조업과 건설업을 비교해줘", layer)
    assert ("과", "list") in both.markers()


@pytest.mark.parametrize("question", [
    "제조업을 비교하고 싶어",  # a predicate stem before it, a request word after it
    "제조업을 비교하고 건설업도 보여줘",  # a clause link, not a list
    "제조업과 비교해줘",  # a display word after it
])
def test_a_joiner_after_a_predicate_or_before_no_nominal_marks_no_list(layer, question):
    q = Q(question, layer)
    assert [m for m in q.markers() if m[1] == "list"] == []
    assert all(s["marker"] != "list" for s in q.payload["segments"])
    assert "".join(s["quote"] for s in q.segments) == question


def test_a_joiner_inside_a_catalog_name_keeps_the_whole_name(layer):
    q = Q("금과 SDRs 보여줘", layer)
    assert q.kind("금과 SDRs") == "name"
    lo, hi = q.seg("금과 SDRs")["span"]
    spans = q.local["mention_spans"]
    assert [m for m in q.mentions if m["kind"] == "marker" and lo <= spans[m["mention_id"]][0] < hi] == []
    assert q.name_mention("금과 SDRs")["candidates"]


# ---------------------------------------------------------------- 4. sense order, spelling variants, omitted senses


def cand(table: str, name: str, role: str = "indicator", path: str = "lexical") -> dict:
    return {"table_id": table, "obj_id": "ITEM", "value_id": f"{table}_ITEM", "value_name": name, "role": role,
            "path": path, "type": "item_row"}


def mention(cands: list[dict], mid: str = "m1") -> dict:
    return {"mention_id": mid, "kind": "name", "segment_ids": ["s1"], "condition_ids": [], "marker": None,
            "candidates": cands, "overlaps": []}


def cond(cid: str, axis: str, value: str, polarity: str = "+", kind: str = "basis", span=(10, 12)) -> dict:
    return {"id": cid, "kind": kind, "quote": value, "value": value, "span": list(span),
            "attrs": {"axis": axis, "value": value, "polarity": polarity}, "status": "candidate"}


def names_kept(table: dict, mid: str = "m1") -> list[str]:
    return [table["senses"][z]["name"] for z in table["mention_kept"][mid]]


MONEY_ROWS = [cand(f"T{n}", name) for n, name in enumerate(MONEY, start=1)] + [
    cand("T1", "가상통화", "table_name", "exact")]


def test_without_conditions_the_order_is_the_policy_order(monkeypatch):
    table = build_senses([mention(MONEY_ROWS)])
    # by role, then path, more rows and the name (the order before plan 19)
    assert names_kept(table) == ["가상통화 (광의)", "가상통화(Old series)", "가상통화(말잔 계절조정계열)",
                                 "가상통화(말잔 원계열)", "가상통화(평잔 계절조정계열)"]
    assert table["sense_compat"] == {} and table["question_meanings"] == []
    assert build_senses([mention(MONEY_ROWS)], []) == table


def test_senses_carrying_the_asked_basis_rank_first_at_their_role():
    asked = [cond("B1", "seasonal", "원계열"), cond("B2", "stock", "평잔", span=(14, 16))]
    table = build_senses([mention(MONEY_ROWS)], asked)
    kept = names_kept(table)
    assert kept[0] == "가상통화(평잔 원계열)"  # both asked values
    assert kept[1:3] == ["가상통화(말잔 원계열)", "가상통화(평잔 계절조정계열)"]  # one asked value, one other value
    z = table["mention_kept"]["m1"][0]
    assert table["sense_compat"][z] == {"matched": ["seasonal:원계열", "stock:평잔"], "conflicting": []}
    # the role stays first: the table name sense is never put before an indicator sense
    roles = [table["senses"][z]["role"] for z in table["mention_senses"]["m1"]]
    assert roles == sorted(roles, key=lambda r: DP.POLICY["senses"]["role_order"].index(r))
    assert table["question_meanings"] == ["seasonal:원계열", "stock:평잔"]


@pytest.mark.parametrize("negated", [
    [cond("B1", "stock", "평잔", polarity="-")],  # a negated basis word
    [cond("B1", "stock", "평잔"), {"id": "E1", "kind": "exclude", "quote": "평잔 제외", "value": "평잔", "span": [5, 20],
                                  "attrs": {"text": "평잔"}, "status": "candidate"}],  # inside an exclusion
])
def test_a_negated_or_excluded_condition_raises_nothing(negated):
    base = build_senses([mention(MONEY_ROWS)])
    table = build_senses([mention(MONEY_ROWS)], negated)
    assert names_kept(table) == names_kept(base)
    assert "가상통화(평잔 원계열)" not in names_kept(table)
    assert table["question_meanings"] == []


@pytest.mark.parametrize("polarity, first", [("+", "가상대출금(가상유형별)"), ("-", "가상대출금(가나다)")])
def test_a_classification_word_raises_only_when_asked_positively(monkeypatch, polarity, first):
    monkeypatch.setitem(DP.LIMITS, "senses_per_mention", 1)
    rows = [cand("T1", "가상대출금(가상유형별)"), cand("T2", "가상대출금(가나다)")]
    table = build_senses([mention(rows)], [cond("B1", "classification", "가상유형별", polarity)])
    assert names_kept(table) == [first]


def test_spelling_variants_are_one_sense_and_keep_every_row():
    rows = [cand("T1", "가상통화 (광의)"), cand("T2", "가상통화( 광의 )"), cand("T3", "가상통화(광의) 1)"),
            cand("T4", "가상통화(광의)"), cand("T5", "가상통화"), cand("T6", "가상통화(전년동기비)"),
            cand("T7", "가상항목(1)"), cand("T8", "가상항목(2)"), cand("T9", "가상통화 (광의)", "target")]
    table = build_senses([mention(rows)])
    by_role: dict[tuple, dict] = {}
    for z, s in table["senses"].items():
        by_role[(spelling_key(s["name"]), s["role"])] = s
    merged = by_role[(spelling_key("가상통화(광의)"), "indicator")]
    assert sorted(r["table_id"] for r in merged["rows"]) == ["T1", "T2", "T3", "T4"]
    assert merged["names"] == sorted({"가상통화 (광의)", "가상통화( 광의 )", "가상통화(광의) 1)", "가상통화(광의)"})
    # brackets, their contents and other marks are kept apart; roles are never merged
    assert len(table["senses"]) == 6
    assert spelling_key("가상통화") != spelling_key("가상통화(광의)") != spelling_key("가상통화(전년동기비)")
    assert spelling_key("가상항목(1)") != spelling_key("가상항목(2)")
    assert by_role[(spelling_key("가상통화 (광의)"), "target")]["rows"] == [rows[-1]]


def test_omitted_senses_are_recorded_and_kept_locally(monkeypatch):
    monkeypatch.setitem(DP.LIMITS, "senses_per_mention", 2)
    table = build_senses([mention(MONEY_ROWS)])
    kept, every = table["mention_kept"]["m1"], table["mention_senses"]["m1"]
    assert len(kept) == 2 and len(every) == 7
    assert [o["sense_id"] for o in table["omitted_senses"]] == every[2:]
    assert all(o["reason"] == "senses_per_mention" and o["mention_id"] == "m1" for o in table["omitted_senses"])
    rows = [r for s in table["senses"].values() for r in s["rows"]]
    assert all(c in rows for c in MONEY_ROWS)  # not sending is no deletion


def test_the_payload_orders_the_senses_by_the_questions_conditions(layer):
    plain = Q("가상통화 보여줘", layer)
    assert ("가상통화(평잔 원계열)", "indicator") not in plain.kept_names("가상통화")
    omitted = {(o["name"], o["role"]) for o in plain.local["omitted_senses"]}
    assert ("가상통화(평잔 원계열)", "indicator") in omitted
    asked = Q("가상통화 원계열 평잔 보여줘", layer)
    assert {c["kind"] for c in asked.conditions} >= {"basis"}
    assert asked.kept_names("가상통화")[0] == ("가상통화(평잔 원계열)", "indicator")
    assert asked.payload["truncation"]["senses_omitted"] is True
    negated = Q("가상통화 평잔이 아닌 것 보여줘", layer)
    assert ("가상통화(평잔 원계열)", "indicator") not in negated.kept_names("가상통화")
    # the payload shape is unchanged (the order facts stay local)
    assert set(asked.payload) == {"raw", "segments", "conditions", "candidate_summary", "relations", "truncation",
                                  "limits"}
    text = json.dumps(asked.payload, ensure_ascii=False)
    assert "sense_compat" not in text and "omitted_senses" not in text


def test_the_sense_table_is_deterministic(layer):
    a, b = Q("가상통화 원계열 평잔 보여줘", layer), Q("가상통화 원계열 평잔 보여줘", layer)
    assert a.payload == b.payload and a.local["senses"] == b.local["senses"]
    assert a.local["omitted_senses"] == b.local["omitted_senses"]


# ---------------------------------------------------------------- 5. prompt, shrink order, versions


def test_the_prompt_states_the_requirement_rules_with_placeholders():
    text = prompt_text()
    assert "가상" not in text
    for phrase in ("서로 다른 지표", "같은 지표를 여러 분류값", "측정 기준으로 각각", "표가 몇 개일지로 요구 수를 정하지 않는다",
                   "거르기와 나눔", "이름 안의 개념", "sense_id를 null", "공백 조각까지 포함", "참조를 여러 개로",
                   "name_content일 때만", "빠뜨리지 않고", "잠정"):
        assert phrase in text, phrase
    assert "하나의 이름으로 읽는다" not in text  # the old line (a name with middle dots or particles is one name)


def test_condition_attributes_are_the_last_shrink_step():
    steps = DP.POLICY["shrink_steps"]["steps"]
    assert steps[0] != "condition_attrs" and steps[-1] == "condition_attrs"
    assert sorted(steps) == ["candidate_paths", "condition_attrs", "relations"]


def test_the_input_version_and_the_input_layers_are_recorded():
    v = versions()
    assert v["input_version"] == DP.INPUT_VERSION
    for name in ("interpret/segments.py", "interpret/mentions.py", "interpret/tokens.py", "data/prompts/decompose.md"):
        assert name in v


# ---------------------------------------------------------------- read-only regression on development questions

REAL = paths.CATALOG_SEARCH.exists() and paths.CATALOG_META_RAW.exists() and paths.QUESTIONS_417.exists()


@pytest.fixture(scope="module")
def real():
    return load_layer(include_external_synonyms=False)


def _development(question: str) -> str:
    rows = [r for r in json.loads(paths.QUESTIONS_417.read_text(encoding="utf8")) if r["question"] == question]
    assert rows and all(r["dataset"] == "development" for r in rows)  # never golden or the new questions
    return question


@pytest.mark.skipif(not REAL, reason="catalog raw files or question file absent")
def test_real_dot_name_gets_the_candidates_of_the_spaced_form(real):
    question = _development("차주당 가계대출 잔액을 연령별·지역별로 보여줘")
    dotted, spaced = Q(question, real), Q(question.replace("·", " "), real)
    got = dotted.name_mention("연령별·지역별")["candidates"]
    assert got == spaced.name_mention("연령별 지역별")["candidates"] != []


@pytest.mark.skipif(not REAL, reason="catalog raw files or question file absent")
@pytest.mark.parametrize("question, quote, meanings", [
    ("2001년부터 2010년까지 M2 말잔(원계열)을 보여줘", "M2", {"stock:말잔", "seasonal:원계열"}),
    ("예금은행 산업별대출금을 지역별 용도별로 비교해줘.", "산업별대출금",
     {"classification:지역별", "classification:용도별"}),
])
def test_real_senses_carrying_the_asked_conditions_are_sent(real, question, quote, meanings):
    q = Q(_development(question), real)
    assert set(q.local["question_meanings"]) >= meanings
    kept = q.local["mention_kept"][q.name_mention(quote)["mention_id"]]
    compat = q.local["sense_compat"]
    assert any(meanings <= set(compat.get(z, {}).get("matched", [])) for z in kept)
