"""KOSIS가 단위를 비워 보내는 표에 단위 보완표의 단위를 채우는 규칙을 본다."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from claview_b.contracts import Config, StageFailure
from claview_b.metadata import MetadataStore, load_unit_supplement, selected_unit
from claview_b.stages import build_b_context, model_table

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "301:DT_501Y006"  # 픽스처에서 메타데이터 단위가 "%"인 표


def write_supplement(path: Path, tables: dict) -> Path:
    path.write_text(json.dumps({"tables": tables}, ensure_ascii=False), encoding="utf-8")
    return path


def entry(unit="지수"):
    return {"unit": unit, "basis": "조회한 계열 전건에서 API 단위 공란 확인", "source": "시험"}


def store_without_units(tmp_path, supplement):
    """DT_501Y006의 메타데이터 단위를 지운 저장소(KOSIS가 단위를 비워 보내는 표 모양)."""
    rows = []
    for line in (FIXTURES / "metadata.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["tblId"] == "DT_501Y006":
            row["meta"]["UNIT"] = []
            for item in row["meta"]["ITM"]:
                item["UNIT_NM"] = None
        rows.append(json.dumps(row, ensure_ascii=False))
    metadata = tmp_path / "metadata.jsonl"
    metadata.write_text("\n".join(rows), encoding="utf-8")
    shutil.copy(FIXTURES / "catalog.json", tmp_path / "catalog.json")
    path = write_supplement(tmp_path / "supp.json", supplement)
    return MetadataStore(tmp_path / "catalog.json", metadata, path)


def first_selection(store):
    table = store.get("301", "DT_501Y006")
    item = next(iter(table["items"]))
    dims = {a: next(iter(d["values"])) for a, d in table["dimensions"].items()}
    return table, item, dims


def test_a_table_without_any_unit_gets_the_supplement_unit(tmp_path):
    store = store_without_units(tmp_path, {KEY: entry()})
    table, item, dims = first_selection(store)
    assert table["units"] == []
    assert selected_unit(table, item, dims) == "지수"


def test_without_a_supplement_entry_the_unit_stays_missing(tmp_path):
    store = store_without_units(tmp_path, {})
    table, item, dims = first_selection(store)
    assert selected_unit(table, item, dims) is None


def test_a_unit_from_the_metadata_is_never_replaced(tmp_path):
    store = MetadataStore(
        FIXTURES / "catalog.json",
        FIXTURES / "metadata.jsonl",
        write_supplement(tmp_path / "supp.json", {KEY: entry()}),
    )
    table, item, dims = first_selection(store)
    assert table["units"] == ["%"]
    assert selected_unit(table, item, dims) == "%"


def test_the_default_file_next_to_the_data_folder_is_read_when_present(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "mappings" / "runtime").mkdir(parents=True)
    shutil.copy(FIXTURES / "metadata.jsonl", tmp_path / "raw" / "boK_meta_raw.jsonl")
    shutil.copy(FIXTURES / "catalog.json", tmp_path / "catalog.json")
    write_supplement(tmp_path / "mappings" / "runtime" / "unit_supplement.json", {KEY: entry()})
    store = MetadataStore(tmp_path / "catalog.json", tmp_path / "raw" / "boK_meta_raw.jsonl")
    assert list(store.unit_supplement) == [KEY]


def test_a_missing_file_means_no_supplement(tmp_path):
    assert load_unit_supplement(tmp_path / "none.json") == {}
    assert load_unit_supplement(None) == {}


@pytest.mark.parametrize(
    "tables",
    [
        {KEY: {"unit": "", "basis": "근거"}},
        {KEY: {"unit": "지수", "basis": ""}},
        {KEY: {"unit": "지수"}},
        {"DT_501Y006": entry()},
    ],
)
def test_a_malformed_supplement_stops_loudly(tmp_path, tables):
    with pytest.raises(StageFailure) as caught:
        load_unit_supplement(write_supplement(tmp_path / "s.json", tables))
    assert caught.value.code == "invalid_unit_supplement"


def test_the_shipped_supplement_lists_only_checked_index_tables():
    root = Path(__file__).resolve().parents[3]
    tables = load_unit_supplement(root / "data" / "mappings" / "runtime" / "unit_supplement.json")
    assert set(tables) == {
        "301:DT_511Y002",
        "301:DT_512Y007",
        "301:DT_512Y008",
        "301:DT_514Y001",
        "301:DT_514Y002",
        "301:DT_514Y003",
    }
    assert {e["unit"] for e in tables.values()} == {"지수"}


class Client:
    """저장 응답 대신 단위가 빈 KOSIS 응답 한 줄을 돌려주는 시험용 클라이언트."""

    def __init__(self, unit_nm):
        self.unit_nm = unit_nm

    def fetch(self, params, trace):
        from claview_b.fetch import _RESPONSE_PRD_SE

        row = {
            "ORG_ID": params["orgId"],
            "TBL_ID": params["tblId"],
            "ITM_ID": params["itmId"],
            "PRD_SE": _RESPONSE_PRD_SE[params["prdSe"]],
            "PRD_DE": params["startPrdDe"],
            "DT": "1.5",
            "UNIT_NM": self.unit_nm,
        }
        for axis, code in params.items():
            if axis.startswith("objL"):
                row["C" + axis[-1]] = code
        return [row]


def one_series_result(store):
    from claview_b.contracts import Config
    from claview_b.stages import canonicalize_selections

    table, item, dims = first_selection(store)
    freq, bounds = next(iter(table["periods"].items()))
    period = {"frequency": freq, "start": bounds["start"], "end": bounds["start"]}
    raw = {"selections": [{"item_id": item, "dimensions": dims, "period": period}]}
    selection = canonicalize_selections(raw, table, Config())[0]
    selection["series_id"] = "S1"
    return {"selections": [selection]}


def test_the_result_says_the_unit_came_from_the_supplement(tmp_path):
    from claview_b.contracts import Trace
    from claview_b.fetch import fetch_data

    store = store_without_units(tmp_path, {KEY: entry()})
    result = fetch_data(one_series_result(store), store, Client(None), Trace())
    series = result["result"]["series"][0]
    assert series["unit"] == "지수"
    assert series["provenance"]["unit_source"] == "supplement"


def test_a_unit_the_api_sends_is_not_marked_as_supplemented(tmp_path):
    from claview_b.contracts import Trace
    from claview_b.fetch import fetch_data

    store = store_without_units(tmp_path, {})
    result = fetch_data(one_series_result(store), store, Client("%"), Trace())
    series = result["result"]["series"][0]
    assert series["unit"] == "%"
    assert "unit_source" not in series["provenance"]


def test_the_model_never_sees_the_unit_supplement(tmp_path):
    """보완 단위는 모델 답 뒤에 채운다. 모델 입력은 보완표가 없을 때와 같아야 한다(HCX 입력 불변, 단위 힌트 없음)."""
    with_supplement = store_without_units(tmp_path, {KEY: entry()})
    (tmp_path / "plain").mkdir()
    plain = store_without_units(tmp_path / "plain", {})
    table = with_supplement.get("301", "DT_501Y006")
    assert "unit_supplement" in table
    query = "2024년 지표 추이"
    seen = model_table(build_b_context(query, table, Config()), table)
    bare = plain.get("301", "DT_501Y006")
    assert "unit_supplement" not in bare
    assert seen == model_table(build_b_context(query, bare, Config()), bare)
    assert "unit_supplement" not in json.dumps(seen, ensure_ascii=False)
