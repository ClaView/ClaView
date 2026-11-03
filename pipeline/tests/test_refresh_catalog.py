"""Offline review probes. Synthetic catalogs, credentials and responses only."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from pipeline.scripts import refresh_catalog as mod

SOURCE = Path(mod.__file__)
TEST_ROOT = Path(__file__).parent
FAKE_KEY = "offline-only-synthetic-key"


class Response:
    def __init__(self, payload):
        self.data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, amount):
        return self.data[:amount]


class Opener:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req)
        return Response(next(self.payloads))


class CollectorReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root = Path(self.temp.name)
        self.sleep = patch.object(mod.time, "sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.temp.cleanup()

    def client(self, responses, cap=20):
        out = self.root / "direct"
        out.mkdir()
        client = mod.Collector(out, FAKE_KEY, cap, 0.2)
        client.opener = Opener(responses)
        return client

    def run_main(self, responses, extra=(), old_docs=()):
        old = self.root / "synthetic_old.json"
        old.write_text(json.dumps({"documents": list(old_docs)}), encoding="utf-8")
        output = self.root / "output"
        fake = Opener(responses)
        argv = [str(SOURCE), "--catalog", str(old), "--output", str(output), *extra]
        error, code = None, None
        with (
            patch.object(mod.os, "environ", {"KOSIS_API_KEY": FAKE_KEY}),
            patch.object(mod.sys, "argv", argv),
            patch.object(mod, "build_opener", return_value=fake),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            try:
                code = mod.main()
            except Exception as exc:
                error = type(exc).__name__
        return code, error, output, fake

    def test_metadata_endpoint_and_no_credentials_in_ledger(self):
        c = self.client([[{"LIST_ID": "child", "LIST_NM": "category"}], [{"TBL_ID": "T1"}]])
        c.get("LIST", "301", {"vwCd": "MT_OTITLE", "parentListId": "301"})
        c.get("TBL", "T1", {"type": "TBL", "orgId": "301", "tblId": "T1"})
        urls = [urlparse(r.full_url) for r in c.opener.requests]
        self.assertEqual([u.path for u in urls], ["/openapi/statisticsList.do", "/openapi/statisticsData.do"])
        self.assertEqual([parse_qs(u.query)["method"] for u in urls], [["getList"], ["getMeta"]])
        for file in c.output.rglob("*"):
            if file.is_file():
                self.assertNotIn(FAKE_KEY, file.read_text(encoding="utf-8"))

    def test_optional_no_data_is_empty_but_required_stops(self):
        c = self.client([{"err": "30"}, {"err": "30"}])
        self.assertEqual(c.get("UNIT", "T1", {}), [])
        with self.assertRaisesRegex(mod.StopCollection, "api_error_30"):
            c.get("TBL", "T1", {})

    def test_cap_stop_records_stopped_and_no_catalog(self):
        code, error, output, fake = self.run_main(
            [[{"ORG_ID": "301", "TBL_ID": "T1", "TBL_NM": "A"}]], ["--max-requests", "1"]
        )
        self.assertEqual((code, error), (1, None))
        self.assertEqual(
            json.loads((output / "collection_manifest.json").read_text(encoding="utf-8"))["reason"], "request_cap"
        )
        self.assertFalse((output / "search_catalog.json").exists())

    def test_missing_old_table_stops(self):
        code, error, output, _ = self.run_main(
            [[{"ORG_ID": "301", "TBL_ID": "T1", "TBL_NM": "A"}]], old_docs=[{"doc_id": "301:OLD"}]
        )
        self.assertEqual((code, error), (1, None))
        self.assertEqual(
            json.loads((output / "collection_manifest.json").read_text(encoding="utf-8"))["reason"],
            "old_tables_missing_from_list_review_required",
        )

    def test_credential_echo_is_not_persisted(self):
        c = self.client([[{"echo": FAKE_KEY}]])
        with self.assertRaisesRegex(mod.StopCollection, "credential_echo_refused"):
            c.get("TBL", "T1", {})
        self.assertEqual(list((c.output / "responses").iterdir()), [])
        self.assertNotIn(FAKE_KEY, (c.output / "requests.jsonl").read_text(encoding="utf-8"))

    def test_malformed_list_stops_cleanly(self):
        code, error, output, _ = self.run_main([["not-an-object"]])
        self.assertEqual((code, error), (1, None))
        self.assertEqual(
            json.loads((output / "collection_manifest.json").read_text(encoding="utf-8"))["reason"],
            "invalid_response_row",
        )

    def test_missing_title_stops_cleanly(self):
        code, error, output, _ = self.run_main([[{"ORG_ID": "301", "TBL_ID": "T1"}]])
        self.assertEqual((code, error), (1, None))
        self.assertEqual(
            json.loads((output / "collection_manifest.json").read_text(encoding="utf-8"))["reason"],
            "missing_table_name",
        )

    def test_non_object_metadata_stops_cleanly(self):
        code, error, output, _ = self.run_main(
            [[{"ORG_ID": "301", "TBL_ID": "T1", "TBL_NM": "A"}], ["bad"], ["bad"], ["bad"], [], []]
        )
        self.assertEqual((code, error), (1, None))
        self.assertEqual(
            json.loads((output / "collection_manifest.json").read_text(encoding="utf-8"))["reason"],
            "invalid_response_row",
        )
        self.assertFalse((output / "search_catalog.json").exists())

    def test_empty_listing_branch_does_not_discard_old_table_guard(self):
        rows = [{"LIST_ID": "EMPTY", "LIST_NM": "empty category"}, {"ORG_ID": "301", "TBL_ID": "T1", "TBL_NM": "A"}]
        code, error, output, fake = self.run_main([rows, {"err": "30"}], ["--list-only"], [{"doc_id": "301:T1"}])
        self.assertEqual((code, error), (0, None))
        self.assertEqual(len(fake.requests), 2)
        manifest = json.loads((output / "collection_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "list_only")
        self.assertEqual(manifest["missing_from_current_list"], [])
        self.assertFalse((output / "search_catalog.json").exists())
        self.assertEqual(
            json.loads((output / "requests.jsonl").read_text(encoding="utf-8").splitlines()[-1])["status"], "empty"
        )

    def test_complete_staging_preserves_old_catalog_and_numeric_unverified(self):
        rows = [{"ORG_ID": "301", "TBL_ID": "T1", "TBL_NM": "A"}]
        metadata = [
            [{"ORG_ID": "301", "TBL_ID": "T1", "TBL_NM": "A"}],
            [{"PRD_SE": "년", "STRT_PRD_DE": "2020", "END_PRD_DE": "2025"}],
            [{"ITM_ID": "I1", "ITM_NM": "item", "OBJ_ID": "ITEM"}],
            [],
            [],
        ]
        old_docs = [{"doc_id": "301:T1", "path_ids": "301"}]
        code, error, output, fake = self.run_main([rows, *metadata], old_docs=old_docs)
        self.assertEqual((code, error), (0, None))
        self.assertEqual(len(fake.requests), 6)
        self.assertEqual(
            json.loads((self.root / "synthetic_old.json").read_text(encoding="utf-8")), {"documents": old_docs}
        )
        final = json.loads((output / "search_catalog.json").read_text(encoding="utf-8"))
        self.assertFalse(final["documents"][0]["api_verified"])
        self.assertTrue(final["documents"][0]["metadata_api_verified"])
        manifest = json.loads((output / "collection_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "collected_not_applied")
        self.assertEqual((manifest["model_calls"], manifest["numeric_data_calls"]), (0, 0))

    def test_existing_route_and_order_are_preserved(self):
        row = {"ORG_ID": "301", "TBL_ID": "T1", "TBL_NM": "official name"}
        listing = [
            {"row": row, "path_ids": ["301", "B"], "path_names": ["한국은행", "B"]},
            {"row": row, "path_ids": ["301", "A"], "path_names": ["한국은행", "A"]},
        ]
        docs, removed = mod.build_documents(
            listing, {"documents": [{"doc_id": "301:T1", "path_ids": "301 > A"}]}, "2026-11-03"
        )
        self.assertEqual(removed, [])
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["path_ids"], "301 > A")
        self.assertEqual(docs[0]["title"], "official name")

    def test_redirect_is_refused(self):
        with self.assertRaisesRegex(mod.StopCollection, "redirect_refused"):
            mod.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://invalid.example")


if __name__ == "__main__":
    unittest.main(verbosity=2)
