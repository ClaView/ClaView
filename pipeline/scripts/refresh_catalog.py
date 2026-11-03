"""Collect KOSIS metadata into a new directory; never update runtime data in place."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

TYPES = ("TBL", "PRD", "ITM", "UNIT", "CMMT")
OPTIONAL = {"UNIT", "CMMT"}
ROOT = "https://kosis.kr/openapi/"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def utc() -> str:
    return datetime.now(UTC).isoformat()


class StopCollection(Exception):
    pass


def parse_metadata_json(data: bytes) -> tuple[object, int]:
    """Repair only invalid JSON backslash escapes; retain raw response bytes separately."""
    source = data.decode("utf-8-sig")
    normalized = 0
    while True:
        try:
            return json.loads(source), normalized
        except json.JSONDecodeError as exc:
            if exc.msg != "Invalid \\escape" or source[exc.pos:exc.pos + 1] != "\\":
                raise
            source = source[:exc.pos] + "\\" + source[exc.pos:]
            normalized += 1


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StopCollection("redirect_refused")


class Collector:
    def __init__(self, output: Path, key: str, cap: int, delay: float,
                 reuse: Path | None = None):
        self.output, self.key, self.cap, self.delay = output, key, cap, delay
        self.count = 0
        self.sequence = 0
        self.reused_count = 0
        self.normalized_escapes = 0
        self.normalized_responses = 0
        self.reuse = reuse
        self.reuse_entries = None
        self.reuse_ledger_sha256 = None
        self.opener = build_opener(NoRedirect())
        (output / "responses").mkdir()

    def load_reuse(self) -> None:
        """Validate all saved evidence before issuing any new network request."""
        if self.reuse_entries is not None:
            return
        entries = {}
        if self.reuse is not None:
            try:
                root = self.reuse.resolve(strict=True)
                ledger = (root / "requests.jsonl").read_bytes()
                responses = (root / "responses").resolve(strict=True)
                if responses.parent != root:
                    raise StopCollection("invalid_reuse_path")
                for line in ledger.decode("utf-8-sig").splitlines():
                    row = json.loads(line)
                    kind, identifier = row["kind"], row["identifier"]
                    if kind not in ("LIST", *TYPES) or not isinstance(identifier, str):
                        raise StopCollection("invalid_reuse_parameters")
                    expected = {"method": "getList" if kind == "LIST" else "getMeta",
                                "format": "json", "jsonVD": "Y"}
                    expected.update({"vwCd": "MT_OTITLE", "parentListId": identifier}
                                    if kind == "LIST" else
                                    {"type": kind, "orgId": "301", "tblId": identifier})
                    if row["parameters"] != expected:
                        raise StopCollection("invalid_reuse_parameters")
                    if row["status"] not in {"ok", "empty", "invalid_json"}:
                        raise StopCollection("invalid_reuse_status")
                    relative = Path(row["file"])
                    source = (root / relative).resolve(strict=True)
                    if relative.is_absolute() or source.parent != responses:
                        raise StopCollection("invalid_reuse_path")
                    data = source.read_bytes()
                    if len(data) > 8_000_000 or len(data) != row["bytes"] or digest(data) != row["sha256"]:
                        raise StopCollection("invalid_reuse_response_digest")
                    identity = (kind, identifier)
                    if identity in entries:
                        raise StopCollection("duplicate_reuse_request")
                    entries[identity] = {**row, "source_path": source}
                self.reuse_ledger_sha256 = digest(ledger)
            except (OSError, KeyError, TypeError, ValueError, AttributeError):
                raise StopCollection("invalid_reuse_evidence") from None
        self.reuse_entries = entries

    def get(self, kind: str, identifier: str, params: dict) -> object:
        self.load_reuse()
        # Only these two read-only metadata endpoints are reachable.
        endpoint = "statisticsList.do" if kind == "LIST" else "statisticsData.do"
        safe = {"method": "getList" if kind == "LIST" else "getMeta",
                "format": "json", "jsonVD": "Y", **params}
        query = urlencode({**safe, "apiKey": self.key})
        cached = self.reuse_entries.get((kind, identifier))
        if cached is not None and cached["parameters"] != safe:
            raise StopCollection("reuse_request_parameter_mismatch")
        if cached is None and self.count >= self.cap:
            raise StopCollection("request_cap")
        self.sequence += 1
        record = {"sequence": self.sequence, "started_at": utc(), "kind": kind,
                  "identifier": identifier, "parameters": safe,
                  "source": "reuse" if cached is not None else "network"}
        try:
            if cached is not None:
                data = cached["source_path"].read_bytes()
                if len(data) != cached["bytes"] or digest(data) != cached["sha256"]:
                    raise StopCollection("invalid_reuse_response_digest")
                self.reused_count += 1
                record.update(reused_sequence=cached["sequence"],
                              original_started_at=cached["started_at"])
            else:
                self.count += 1
                req = Request(ROOT + endpoint + "?" + query,
                              headers={"User-Agent": "ClaView-MetadataRefresh/1.0"})
                with self.opener.open(req, timeout=30) as response:
                    data = response.read(8_000_001)
                    if len(data) > 8_000_000:
                        raise StopCollection("response_size_limit")
            if self.key.encode() in data or query.encode() in data:
                raise StopCollection("credential_echo_refused")
            name = f"{self.sequence:04d}_{kind}_{identifier}.json"
            (self.output / "responses" / name).write_bytes(data)
            record.update(file="responses/" + name, sha256=digest(data), bytes=len(data))
            value, normalized = parse_metadata_json(data)
            record["normalized_escapes"] = normalized
            self.normalized_escapes += normalized
            self.normalized_responses += bool(normalized)
            error = value if isinstance(value, dict) else None
            if isinstance(value, list) and value and isinstance(value[0], dict) and "err" in value[0]:
                error = value[0]
            if isinstance(error, dict) and "err" in error:
                code = str(error["err"])
                record["api_error"] = code
                if code == "30" and (kind in OPTIONAL or kind == "LIST"):
                    record["status"] = "empty"
                    return []
                raise StopCollection("api_error_" + code if code.isdigit() else "api_error")
            if not isinstance(value, list):
                raise StopCollection("not_a_list")
            if any(not isinstance(row, dict) for row in value):
                raise StopCollection("invalid_response_row")
            if not value and kind not in OPTIONAL and kind != "LIST":
                raise StopCollection("empty_required_section")
            record["status"] = "ok" if value else "empty"
            return value
        except HTTPError as exc:
            record["status"] = f"http_{exc.code}"
            raise StopCollection(record["status"]) from None
        except (URLError, TimeoutError, OSError):
            record["status"] = "transport_failure"
            raise StopCollection("transport_failure") from None
        except (UnicodeError, ValueError):
            record["status"] = "invalid_json"
            raise StopCollection("invalid_json") from None
        except StopCollection as exc:
            record["status"] = str(exc)
            raise
        finally:
            record["ended_at"] = utc()
            with (self.output / "requests.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            if cached is None:
                time.sleep(self.delay)

    def listing(self) -> list[dict]:
        queue = [("301", ["301"], ["한국은행"])]
        seen, found = set(), []
        while queue:
            parent, ids, names = queue.pop(0)
            if parent in seen:
                raise StopCollection("duplicate_list_node")
            seen.add(parent)
            rows = self.get("LIST", parent, {"vwCd": "MT_OTITLE", "parentListId": parent})
            for row in rows:
                if row.get("TBL_ID"):
                    if str(row.get("ORG_ID")) != "301":
                        raise StopCollection("unexpected_organization")
                    if not isinstance(row.get("TBL_NM"), str) or not row["TBL_NM"].strip():
                        raise StopCollection("missing_table_name")
                    found.append({"row": row, "path_ids": ids, "path_names": names})
                elif row.get("LIST_ID") and row.get("LIST_NM"):
                    child = str(row["LIST_ID"])
                    queue.append((child, ids + [child], names + [row["LIST_NM"]]))
                else:
                    raise StopCollection("unrecognized_list_row")
            if len(seen) % 25 == 0:
                print(json.dumps({"list_nodes": len(seen), "tables_seen": len(found)}), flush=True)
        write_json(self.output / "listing.json", found)
        return found


def build_documents(listing: list[dict], old: dict, date: str) -> tuple[list[dict], list[str]]:
    by_id = {}
    for item in listing:
        key = str(item["row"]["ORG_ID"]) + ":" + item["row"]["TBL_ID"]
        by_id.setdefault(key, []).append(item)
    previous = {d["doc_id"]: d for d in old["documents"]}
    # Preserve stable ordering and an existing official path where still available.
    order = [k for k in previous if k in by_id] + sorted(set(by_id) - set(previous))
    documents = []
    for key in order:
        routes = by_id[key]
        old_path = previous.get(key, {}).get("path_ids")
        route = next((r for r in routes if " > ".join(r["path_ids"]) == old_path), routes[0])
        row, names, ids = route["row"], route["path_names"], route["path_ids"]
        title = row["TBL_NM"]
        path = " > ".join(names)
        documents.append({
            "doc_id": key, "org_id": str(row["ORG_ID"]), "table_id": row["TBL_ID"],
            "title": title, "category": names[1] if len(names) > 1 else names[0],
            "path": path, "path_ids": " > ".join(ids), "catalog_class": names[-1],
            "catalog_snapshot_date": date, "record_source": "KOSIS statisticsList MT_OTITLE",
            "path_indicates_legacy": "(구)" in path,
            "search_text": f"작성기관: 한국은행\n공식 표명: {title}\n공식 목록 경로: {path}\n표 ID: {row['TBL_ID']}",
            "metadata_level": "official_metadata", "api_verified": False,
            "metadata_api_verified": True, "official_updated_at": row.get("SEND_DE"),
        })
    return documents, sorted(set(previous) - set(by_id))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-requests", type=int, default=2500)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--reuse", type=Path,
                        help="Reuse verified raw responses from a previous staging directory.")
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.max_requests <= 3000 or args.delay < 0.2:
        print("invalid_output_or_limits", file=sys.stderr)
        return 2
    key = os.environ.get("KOSIS_API_KEY", "").strip()
    if not key:
        print("kosis_key_unavailable", file=sys.stderr)
        return 2
    args.output.mkdir(parents=True)
    old_bytes = args.catalog.read_bytes()
    old = json.loads(old_bytes.decode("utf-8-sig"))
    manifest = {"schema_version": 1, "started_at": utc(), "status": "collecting",
                "old_catalog_sha256": digest(old_bytes), "request_cap": args.max_requests,
                "model_calls": 0, "numeric_data_calls": 0, "retries": 0,
                "normalization_policy": "invalid_backslash_escape_only_raw_bytes_preserved",
                "scope": "KOSIS MT_OTITLE 301 list and TBL/PRD/ITM/UNIT/CMMT metadata"}
    client = Collector(args.output, key, args.max_requests, args.delay, args.reuse)
    try:
        listing = client.listing()
        date = manifest["started_at"][:10]
        docs, removed = build_documents(listing, old, date)
        manifest.update(listed_tables=len(docs), missing_from_current_list=removed)
        if removed:
            # Never silently discard historical tables from the service catalog.
            raise StopCollection("old_tables_missing_from_list_review_required")
        if not args.list_only:
            with (args.output / "boK_meta_raw.jsonl").open("w", encoding="utf-8") as handle:
                for i, doc in enumerate(docs):
                    record = {"tblId": doc["table_id"], "orgId": doc["org_id"],
                              "name": doc["title"], "cat": doc["catalog_class"],
                              "meta": {}, "status": {}, "collected_at": utc()}
                    for kind in TYPES:
                        result = client.get(kind, doc["table_id"],
                                            {"type": kind, "orgId": doc["org_id"], "tblId": doc["table_id"]})
                        record["meta"][kind] = result
                        record["status"][kind] = "ok" if result else "empty"
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    if (i + 1) % 10 == 0:
                        print(json.dumps({"metadata_tables": i + 1, "requests": client.count}), flush=True)
            write_json(args.output / "search_catalog.json", {
                "schema_version": "1.0", "source_date": date,
                "warning": "Official catalog and metadata refreshed; numeric observations were not fetched.",
                "documents": docs})
        manifest["status"] = "list_only" if args.list_only else "collected_not_applied"
    except StopCollection as exc:
        manifest.update(status="stopped", reason=str(exc))
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        manifest.update(status="stopped", reason="invalid_response_or_local_io")
    finally:
        manifest.update(ended_at=utc(), requests=client.count, network_requests=client.count,
                        reused_responses=client.reused_count, responses_processed=client.sequence,
                        normalized_escapes=client.normalized_escapes,
                        normalized_responses=client.normalized_responses,
                        reuse_ledger_sha256=client.reuse_ledger_sha256)
        write_json(args.output / "collection_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    return 0 if manifest["status"] != "stopped" else 1


if __name__ == "__main__":
    raise SystemExit(main())
