"""The golden scorer's ask check (.15, user 2026-10-28 19:51 UTC): split statistics may each ask the axis they need;
one statistic asking the same axis twice, a missing value or an extra axis is still wrong. Made-up tables only."""

from __future__ import annotations

from tl_eval import score as S

TITLES = {"DT_A1": "가상물가지수(기본분류)", "DT_A2": "가상물가지수(품목별)", "DT_B1": "가상지표(원계열)"}
EXPECTED = {"axis": "classification", "values": {frozenset({"기본분류"}): {"DT_A1"}, frozenset({"품목별"}): {"DT_A2"}}}


def ask(sid, options, axis="classification"):
    return (sid, {"axis": axis, "options": [{"value": v, "table": t} for v, t in options]})


BOTH = [("기본분류", "DT_A1"), ("품목별", "DT_A2")]


def score(asks):
    return S._score_ask(asks, EXPECTED, lambda o: o["table"], TITLES)


def test_one_statistic_asking_once_is_right():
    assert score([ask("s1", BOTH)])[0] is True


def test_split_statistics_each_asking_the_axis_are_right():
    ok, problems = score([ask("s1", BOTH), ask("s2", BOTH)])
    assert ok is True and problems == []


def test_the_same_statistic_asking_twice_is_wrong():
    ok, problems = score([ask("s1", BOTH), ask("s1", BOTH)])
    assert ok is False and "more than once" in problems[0]


def test_a_split_ask_missing_a_value_is_wrong():
    ok, problems = score([ask("s1", BOTH), ask("s2", [("기본분류", "DT_A1")])])
    assert ok is False and any(p.startswith("s2: values") for p in problems)


def test_no_ask_or_an_extra_axis_is_wrong():
    assert score([])[0] is False
    assert score([ask("s1", BOTH), ask("s2", [("원계열", "DT_B1"), ("원계열", "DT_B1")], axis="seasonal")])[0] is False
