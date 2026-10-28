"""Current A extraction/diagnosis -> installed shared resolver; no copied B rules."""

import copy

import pytest

from test_plan15_settle import P, layer
from test_plan15_step5 import simple
from claview_a_legacy import period, period_core
from claview_a_legacy.agent.graph import run_outer
from claview_a_legacy.contract.check import check_analysis
from claview_a_legacy.development import runtime_versions
from claview_a_legacy.interpret.condition_fields import period_diagnosis, condition_fields
from claview_a_legacy.interpret.decompose_provider import versions
from claview_a_legacy.interpret.pipeline import interpret_question

META = {"periods": {"M": {"start": "200001", "end": "202608"}}}
AS_OF = "2026-10-20"


def analysis_of(text, layer):
    return interpret_question("가상대출금 " + text + " 보여줘", layer,
                              decomposition={"mode": "rules", "as_of": AS_OF})


def resolve(analysis, **kwargs):
    return period_core.resolve_period_for_table(
        analysis["conditions"], META, AS_OF, inferred=[],
        condition_fields=analysis["decomposition"]["condition_fields"], **kwargs)


def test_installed_dependency_contract_policy_and_provenance_are_pinned():
    identity = period.package_identity()
    assert identity["package"] == "0.2.0"
    assert identity["contract"] == "period-contract/2026-10-21.2"
    assert identity["policy_sha256"] == "745d4d2f49d76707f185050b1e68fe447828276e7658de84477a7f392e68c0cb"
    assert "resolver.py" in identity["files"] and "period_policy.json" in identity["files"]
    assert "dependency/claview_period" in runtime_versions()
    assert "dependency/claview_period" in versions()


def test_unreviewed_dependency_version_is_an_error_not_a_rules_fallback(monkeypatch):
    monkeypatch.setattr(period_core, "__version__", "unreviewed")
    with pytest.raises(RuntimeError, match="pinned dependency"):
        period_diagnosis({}, AS_OF)


@pytest.mark.parametrize("text,start,end", [
    ("최근 3개월", "202606", "202608"),
    ("지난 분기", "202604", "202606"),
    ("지난달", "202607", "202607"),
    ("최근 6개월 월별로", "202603", "202608"),
])
def test_four_current_a_shapes_are_supported_and_keep_b_table_anchor(layer, text, start, end):
    analysis = analysis_of(text, layer)
    before = copy.deepcopy(analysis)
    conditions = [c for c in analysis["conditions"] if c["kind"] == "period"]
    assert conditions and all(c["attrs"]["extract_status"] == "unresolved" for c in conditions)
    fields = analysis["decomposition"]["condition_fields"]
    assert all(fields[c["id"]]["period_diagnosis"] == "ok" for c in conditions)
    assert all(fields[c["id"]]["applied"] is None for c in conditions)
    assert check_analysis(analysis) == []
    baseline = interpret_question("가상대출금 " + text + " 보여줘", layer)
    assert {k: v for k, v in analysis.items() if k != "decomposition"} == baseline
    result = resolve(analysis)
    assert period_core.check_result(result) == []
    assert result["status"] == "ok" and result["frequency"] == "M"
    assert result["applied"] == {"frequency": "M", "start": start, "end": end}
    assert result["anchor"]["period"] == "202608"  # real metadata, not the diagnostic probe / request date
    assert analysis == before


def test_model_path_gets_the_same_diagnosis_without_extra_calls_or_probe_leak(layer):
    calls = []
    def source(payload, schema):
        calls.append(payload)
        return simple(P(payload))
    state = run_outer("가상대출금 최근 3개월 보여줘", "router", layer=layer, as_of=AS_OF,
                      request_id="shared_period_fake", decomposition={"mode": "gpt", "execution_mode": "fake",
                                                                      "proposal_source": source})
    analysis = state["analysis"]
    assert len(calls) == 1 and analysis["decomposition"]["applied_source"] == "model"
    assert state["as_of"] == AS_OF
    assert resolve(analysis)["applied"] == {"frequency": "M", "start": "202606", "end": "202608"}
    assert all(f["applied"] is None for f in analysis["decomposition"]["condition_fields"].values())


def test_valid_period_is_not_confirmed_when_its_attribution_is_unsettled(layer):
    analysis = analysis_of("최근 3개월", layer)
    cond, = [c for c in analysis["conditions"] if c["kind"] == "period"]
    r = {"id": "r1", "condition_ids": [cond["id"]], "ambiguous_condition_ids": []}
    fields = condition_fields([cond], {"requirements": [r]}, {}, as_of=AS_OF)
    assert fields[cond["id"]]["confirmed"] is True
    for interpretation, judgement in ((None, {}),
        ({"requirements": [{**r, "ambiguous_condition_ids": [cond["id"]]}]}, {}),
        ({"requirements": [r]}, {"failed_sources": [{"condition_ids": [cond["id"]]}]})):
        fields = condition_fields([cond], interpretation, judgement, as_of=AS_OF)
        assert fields[cond["id"]]["period_diagnosis"] == "ok"
        assert fields[cond["id"]]["confirmed"] is False


@pytest.mark.parametrize("kind,expected", [("unsupported", "unsupported"), ("invalid", "malformed"),
                                          ("attrs_type", "malformed"), ("backward", "malformed")])
def test_malformed_and_unsupported_do_not_become_default_periods(layer, kind, expected):
    analysis = analysis_of("2024년", layer)
    cond, = [c for c in analysis["conditions"] if c["kind"] == "period"]
    attrs = cond["attrs"]
    if kind == "unsupported": attrs["start_expr"] = {"text": "언젠가"}
    if kind == "invalid": attrs["invalid"] = True
    if kind == "attrs_type": attrs = [1]
    if kind == "backward":
        attrs = {**attrs, "form": "range", "start_expr": {"year": "2024", "sub_unit": "M", "sub_value": "6"},
                 "end_expr": {"year": "2024", "sub_unit": "M", "sub_value": "1"}}
    assert period_diagnosis(attrs, AS_OF) == expected
    cond["attrs"] = attrs
    analysis["decomposition"]["condition_fields"][cond["id"]]["period_diagnosis"] = expected
    result = resolve(analysis)
    assert result["status"] == ("unresolved" if expected == "unsupported" else "invalid_input")
    assert result["applied"] is None and result["periods"] is None


def test_probe_coverage_is_never_interpreted_as_unsupported(layer):
    analysis = analysis_of("2100년부터", layer)
    cond, = [c for c in analysis["conditions"] if c["kind"] == "period"]
    assert period_diagnosis(cond["attrs"], AS_OF) == "ok"


@pytest.mark.parametrize("assignment", [[{"start": "2024-04", "end": "2024-09"}],
    [{"start": "2024-01", "end": "2024-03"}, {"start": "2024-10", "end": "2024-12"}],
    [{"start": "2024-01", "end": "2024-03"}, {"start": "2024-04", "end": "2024-12"}]])
def test_a_condition_fields_do_not_bypass_one_assignment_contract(layer, assignment):
    analysis = analysis_of("2024년", layer)
    before = copy.deepcopy(assignment)
    result = resolve(analysis, assignment=assignment)
    if len(assignment) == 1:
        assert result["applied"] == {"frequency": "M", "start": "202404", "end": "202409"}
    else:
        assert result["status"] == "invalid_input" and result["issue"]["code"] == "invalid_assignment"
        assert result["applied"] is None
    assert assignment == before
