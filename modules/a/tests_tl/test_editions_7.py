"""Plan 56 order 7 (bundle 6, policy ⑦): the representative first, and the three edition judgments (a relation entry,
the preservation evidence, the compatibility) before an edition replaces or is stitched to it. Synthetic views: no
table of the catalog, no question of an evaluation set."""

from types import SimpleNamespace

import pytest

from claview_a import periods as PR

AS_OF = "2026-10-22"


def view(table_id, *periods, units=("십억원",)):
    return SimpleNamespace(table_id=table_id, periods=list(periods), table_units=list(units))


def conditions(periods=(), frequencies=("월별",)):
    return PR.read_quotes(list(periods), list(frequencies))["conditions"]


def select(rep, views, periods, usable=None, relations=None, unknown=()):
    ids = [rep, *[t for t in views if t != rep]]
    usable = {t: "representative_checked" if t == rep else "same_value_names" for t in ids} if usable is None \
        else usable
    return PR.final_tables(rep, ids, usable, views, conditions(periods), AS_OF, relations=relations, unknown=unknown)


def spans(got):
    return {t["table_id"]: [(p["start"], p["end"]) for p in t["period"]] for t in got["tables"]}


OLD, NEW = "OLD", "NEW"


def editions(old_units=("십억원",), new_units=("십억원",)):
    return {OLD: view(OLD, ("M", "1990.01", "2004.09"), units=old_units),
            NEW: view(NEW, ("M", "2003.10", "2026.08"), units=new_units)}


# ---------------------------------------------------------------- the representative first


def test_a_representative_that_covers_stays_against_a_later_sibling():
    got = select(OLD, editions(), ("2000년부터 2002년까지",))
    assert got["status"] == "selected" and [t["table_id"] for t in got["tables"]] == [OLD]
    assert "relation_status" not in got["tables"][0]


def test_a_later_sibling_replaces_only_a_representative_that_does_not_cover():
    got = select(OLD, editions(), ("2020년부터 2022년까지",))
    assert [t["table_id"] for t in got["tables"]] == [NEW]
    assert got["tables"][0]["relation_status"] == "unverified"  # no relation entry: allowed on the evidence


def test_the_overlap_goes_to_the_representative_and_the_other_edition_takes_the_rest():
    got = select(OLD, editions(), ("2000년부터 2010년까지",))
    assert got["detail"] == "stitched"
    assert spans(got) == {NEW: [("2004-10", "2010-12")], OLD: [("2000-01", "2004-09")]}
    got = select(NEW, editions(), ("2000년부터 2010년까지",))
    assert spans(got) == {NEW: [("2003-10", "2010-12")], OLD: [("2000-01", "2003-09")]}
    assert [t["table_id"] for t in got["tables"]] == [NEW, OLD]  # shown newest first, as before


def test_four_tables_needed_is_the_stitch_limit():
    views = {f"T{i}": view(f"T{i}", ("M", f"{2000 + 2 * i}.01", f"{2001 + 2 * i}.12")) for i in range(4)}
    got = select("T3", views, ("2000년부터 2007년까지",))
    assert got["status"] == "failed" and got["reason"] == "stitch_limit_exceeded"


# ---------------------------------------------------------------- the three judgments


def test_the_judgments_are_recorded_per_table_apart():
    got = select(OLD, editions(), ("2000년부터 2010년까지",))
    j = got["edition_judgments"][NEW]
    assert j["relation"] is None and j["evidence"] == "sufficient" and j["compatibility"] == "no_conflict_found"
    assert j["relation_status"] == "unverified" and j["units"] == {"representative": ["십억원"], "table": ["십억원"]}


def test_a_confirmed_incompatibility_blocks_the_stitch():
    got = select(OLD, editions(new_units=("백만원",)), ("2000년부터 2010년까지",))
    assert got["status"] == "failed" and got["reason"] == "edition_relation_conflict"
    assert got["edition_judgments"][NEW]["relation_status"] == "conflict"


@pytest.mark.parametrize("case", ["unit_unknown", "basis_unknown", "content_unverified"])
def test_insufficient_evidence_makes_no_automatic_stitch(case):
    views = editions(new_units=() if case == "unit_unknown" else ("십억원",))
    usable = {OLD: "representative_checked"} if case == "content_unverified" else None
    got = select(OLD, views, ("2000년부터 2010년까지",), usable=usable,
                 unknown=(NEW,) if case == "basis_unknown" else ())
    assert got["status"] == "failed" and got["tables"] == []
    # a table that fails the content check never competes (as before), so its reason is not an edition one
    assert got["reason"] == ("period_not_covered" if case == "content_unverified" else "edition_evidence_insufficient")
    assert case in got["edition_judgments"][NEW]["evidence_why"]


def test_insufficient_evidence_never_blocks_a_representative_that_covers():
    got = select(OLD, editions(new_units=()), ("2000년부터 2002년까지",))
    assert [t["table_id"] for t in got["tables"]] == [OLD]


def test_a_relation_entry_makes_the_status_verified_and_a_forbidding_one_blocks():
    allow = [{"tables": [OLD, NEW], "allows": True}]
    got = select(OLD, editions(), ("2000년부터 2010년까지",), relations=allow)
    assert {t["table_id"]: t.get("relation_status") for t in got["tables"]} == {NEW: "verified", OLD: None}
    got = select(OLD, editions(), ("2000년부터 2010년까지",), relations=[{"tables": [NEW, OLD], "allows": False}])
    assert got["reason"] == "edition_relation_conflict"


def test_the_relation_file_starts_empty_with_its_schema():
    assert PR.EDITION_RELATIONS["relations"] == []
    for word in ("series", "items", "basis", "frequency", "period", "source", "allows_replacement", "allows_stitch"):
        assert word in PR.EDITION_RELATIONS["about"]


def test_a_past_shortfall_keeps_its_reason_apart_from_the_edition_reasons():
    got = select(NEW, {NEW: view(NEW, ("M", "2003.10", "2026.08"))}, ("1990년부터 2010년까지",))
    assert got["reason"] == "period_not_covered" and got["detail"] == "past_shortfall"
