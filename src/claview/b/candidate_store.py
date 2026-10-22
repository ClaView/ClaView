"""Single durable authority for candidate sets and downstream execution attempts."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from .contracts import AWAITING_USER, Json, StageFailure


class RequestContext(TypedDict):
    session_id: str
    request_id: str
    query_revision: int


class Evidence(TypedDict):
    query_quote: str
    source_field: str
    state: Literal["match", "conflict", "unknown"]
    reason: str
    value: object
    displayable: bool


class Candidate(TypedDict):
    doc_id: str
    org_id: str
    table_id: str
    table_name: str
    paths: list[Json]
    retrieval_signals: list[Json]
    evidence: list[Evidence]
    conflicts: list[Evidence]
    unknowns: list[Evidence]
    summary: Json
    rank_signals: Json


class CandidateSet(TypedDict):
    schema_version: str
    candidate_set_id: str
    context: RequestContext
    original_query: str
    query_hash: str
    snapshots: Json
    candidate_pool: list[Candidate]
    display_order: list[str]
    diagnostics: Json
    created_at: float
    expires_at: float
    status: str
    selection: list[str]
    a_outcome: Json | None
    trace: Json
    a1_state: NotRequired[Json]
    a2_state: NotRequired[Json]
    selection_decision: NotRequired[Json]
    selection_origin: NotRequired[str]
    a_answers: NotRequired[list[Json]]


def validate_context(context: Json) -> RequestContext:
    if not isinstance(context, dict) or any(
        not isinstance(context.get(k), str) or not context[k].strip() or len(context[k]) > 200
        for k in ("session_id", "request_id")
    ):
        raise StageFailure("invalid_context")
    revision = context.get("query_revision")
    if type(revision) is not int or revision < 1:
        raise StageFailure("invalid_query_revision")
    return {k: context[k] for k in ("session_id", "request_id", "query_revision")}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class CandidateStore:
    """Use one connection per operation; never hold a transaction during B calls."""

    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_sets (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    session_id TEXT, request_id TEXT, revision INTEGER, query_hash TEXT,
                    PRIMARY KEY (session_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS b_attempts (
                    id TEXT PRIMARY KEY, candidate_set_id TEXT NOT NULL,
                    status TEXT NOT NULL, started_at REAL NOT NULL, lease_until REAL NOT NULL,
                    outcome TEXT, trace TEXT
                );
                CREATE INDEX IF NOT EXISTS b_set ON b_attempts(candidate_set_id, started_at);
                CREATE TABLE IF NOT EXISTS a_agent_runs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, set_id TEXT, failure TEXT
                );
                CREATE TABLE IF NOT EXISTS a_clarifications (
                    parent_id TEXT PRIMARY KEY, answer TEXT NOT NULL, child_id TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except StageFailure:
            # Lifecycle transitions (expiry, indeterminate) survive a rejected action.
            db.commit()
            raise
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, bundle: CandidateSet, *, agent_run=None, clarification=None):
        context = validate_context(bundle["context"])
        with self.transaction() as db:
            if clarification:
                parent_id, answer, parent_context = clarification
                parent = self._load(db, parent_id, parent_context, active=True)
                link = db.execute(
                    "SELECT * FROM a_clarifications WHERE parent_id=?", (parent_id,)
                ).fetchone()
                if link:
                    if link["answer"] != answer:
                        raise StageFailure("clarification_answer_conflict", "unresolved")
                    return self._load(
                        db, link["child_id"], context, bundle["snapshots"], active=True
                    )
                if parent["status"] != AWAITING_USER or context != {
                    **parent_context,
                    "query_revision": parent_context["query_revision"] + 1,
                }:
                    raise StageFailure("invalid_clarification_revision", "unresolved")
            if agent_run:
                row = db.execute(
                    "SELECT state FROM a_agent_runs WHERE id=?", (agent_run,)
                ).fetchone()
                if row is None or row["state"] != "running":
                    raise StageFailure("agent_run_not_active", "unresolved")
            current = db.execute(
                "SELECT * FROM requests WHERE session_id=? AND request_id=?",
                (context["session_id"], context["request_id"]),
            ).fetchone()
            if current:
                if current["revision"] > context["query_revision"]:
                    raise StageFailure("stale_query", "unresolved")
                if (
                    current["revision"] == context["query_revision"]
                    and current["query_hash"] != bundle["query_hash"]
                ):
                    raise StageFailure("query_revision_conflict", "unresolved")
            db.execute(
                "INSERT OR REPLACE INTO requests VALUES (?,?,?,?)",
                (
                    context["session_id"],
                    context["request_id"],
                    context["query_revision"],
                    bundle["query_hash"],
                ),
            )
            db.execute(
                "INSERT INTO candidate_sets VALUES (?,?)",
                (bundle["candidate_set_id"], encode(bundle)),
            )
            if clarification:
                db.execute(
                    "INSERT INTO a_clarifications VALUES (?,?,?)",
                    (parent_id, answer, bundle["candidate_set_id"]),
                )
            if agent_run:
                db.execute(
                    "UPDATE a_agent_runs SET state='complete',set_id=? WHERE id=?",
                    (bundle["candidate_set_id"], agent_run),
                )
                answers = bundle.get("agent_state", {}).get("answers", [])
                if answers:
                    last = answers[-1]
                    db.execute(
                        "INSERT INTO a_clarifications VALUES (?,?,?)",
                        (last["parent_set_id"], last["choice"], bundle["candidate_set_id"]),
                    )

    def clarified_result(self, parent_id, answer, context, snapshots):
        with self.transaction() as db:
            self._load(db, parent_id, context)
            link = db.execute(
                "SELECT * FROM a_clarifications WHERE parent_id=?", (parent_id,)
            ).fetchone()
            if link is None:
                return None
            if answer != link["answer"]:
                raise StageFailure("clarification_answer_conflict", "unresolved")
            child_context = {**context, "query_revision": context["query_revision"] + 1}
            return self._load(db, link["child_id"], child_context, snapshots, active=True)

    def claim_agent(self, run_id, context, query_hash):
        """Claim once before model work. Publishing candidates completes the claim atomically."""
        with self.transaction() as db:
            current = db.execute(
                "SELECT revision,query_hash FROM requests WHERE session_id=? AND request_id=?",
                (context["session_id"], context["request_id"]),
            ).fetchone()
            if current is None or current["revision"] != context["query_revision"]:
                raise StageFailure("stale_query", "unresolved")
            if current["query_hash"] != query_hash:
                raise StageFailure("query_revision_conflict", "unresolved")
            row = db.execute("SELECT * FROM a_agent_runs WHERE id=?", (run_id,)).fetchone()
            if row:
                if row["state"] == "complete":
                    return row["set_id"]
                raise StageFailure(
                    row["failure"] or "agent_run_in_progress_or_indeterminate", "unresolved"
                )
            db.execute("INSERT INTO a_agent_runs VALUES (?, 'running', NULL, NULL)", (run_id,))
        return None

    def fail_agent(self, run_id, code):
        with self.transaction() as db:
            db.execute(
                "UPDATE a_agent_runs SET state='failed',failure=? WHERE id=? AND state='running'",
                (code, run_id),
            )

    def _save(self, db, bundle):
        db.execute(
            "UPDATE candidate_sets SET body=? WHERE id=?",
            (encode(bundle), bundle["candidate_set_id"]),
        )

    def register_request(self, context, query_hash):
        context = validate_context(context)
        with self.transaction() as db:
            current = db.execute(
                "SELECT * FROM requests WHERE session_id=? AND request_id=?",
                (context["session_id"], context["request_id"]),
            ).fetchone()
            if current:
                if current["revision"] > context["query_revision"]:
                    raise StageFailure("stale_query", "unresolved")
                if (
                    current["revision"] == context["query_revision"]
                    and current["query_hash"] != query_hash
                ):
                    raise StageFailure("query_revision_conflict", "unresolved")
            db.execute(
                "INSERT OR REPLACE INTO requests VALUES (?,?,?,?)",
                (
                    context["session_id"],
                    context["request_id"],
                    context["query_revision"],
                    query_hash,
                ),
            )

    def _load(self, db, set_id, context, snapshots=None, active=False):
        context = validate_context(context)
        row = db.execute("SELECT body FROM candidate_sets WHERE id=?", (set_id,)).fetchone()
        if row is None:
            raise StageFailure("candidate_set_missing", "unresolved")
        bundle = json.loads(row["body"])
        if bundle["context"] != context:
            raise StageFailure("context_mismatch", "unresolved")
        if active:
            current = db.execute(
                "SELECT revision FROM requests WHERE session_id=? AND request_id=?",
                (context["session_id"], context["request_id"]),
            ).fetchone()
            if current["revision"] != context["query_revision"]:
                raise StageFailure("stale_query", "unresolved")
        mismatch = snapshots is not None and snapshots != bundle["snapshots"]
        if bundle["status"] == AWAITING_USER:
            if mismatch or self.clock() >= bundle["expires_at"]:
                bundle["status"] = "expired"
                bundle["expiry_reason"] = (
                    "snapshot_mismatch" if mismatch else "candidate_set_expired"
                )
                self._save(db, bundle)
        if active:
            if bundle["status"] == "expired":
                raise StageFailure(bundle["expiry_reason"], "unresolved")
            if mismatch:
                raise StageFailure("snapshot_mismatch", "unresolved")
        return bundle

    def get(self, set_id, context, snapshots=None, active=False):
        with self.transaction() as db:
            bundle = self._load(db, set_id, context, snapshots, active)
            bundle["b_attempts"] = self._attempts(db, set_id)
            return bundle

    def confirm(self, set_id, selection, context, snapshots, validator, *, origin="user"):
        with self.transaction() as db:
            bundle = self._load(db, set_id, context, snapshots, active=True)
            if bundle.get("agent_state", {}).get("clarification"):
                raise StageFailure("clarification_required", "unresolved")
            ids = normalize_selection(selection, bundle["display_order"])
            if bundle["status"] == "confirmed":
                if ids != bundle["selection"]:
                    raise StageFailure("selection_conflict", "unresolved")
                return bundle["a_outcome"]
            result = validator(bundle, ids)
            bundle.update(status="confirmed", selection=ids, a_outcome=result)
            bundle["selection_origin"] = origin
            decision = bundle.get("selection_decision")
            if decision:
                bundle["selection_comparison"] = {
                    "proposed_ids": decision["proposed_ids"],
                    "selected_ids": ids,
                    "same_set": set(decision["proposed_ids"]) == set(ids),
                    "origin": origin,
                    "interpretation": "comparison_only_not_correctness",
                }
            bundle["confirmed_at"] = self.clock()
            self._save(db, bundle)
            return result

    def _attempts(self, db, set_id):
        db.execute(
            "UPDATE b_attempts SET status='indeterminate' "
            "WHERE candidate_set_id=? AND status='running' AND lease_until<=?",
            (set_id, self.clock()),
        )
        rows = db.execute(
            "SELECT * FROM b_attempts WHERE candidate_set_id=? ORDER BY rowid", (set_id,)
        ).fetchall()
        return [
            {
                **dict(row),
                "outcome": json.loads(row["outcome"]) if row["outcome"] else None,
                "trace": json.loads(row["trace"]) if row["trace"] else None,
            }
            for row in rows
        ]

    def begin_b(self, set_id, context, snapshots, retry=False, lease_seconds=300):
        with self.transaction() as db:
            bundle = self._load(db, set_id, context, snapshots, active=True)
            if bundle["status"] != "confirmed":
                raise StageFailure("a_not_confirmed", "unresolved")
            attempts = self._attempts(db, set_id)
            if attempts:
                last = attempts[-1]
                if last["status"] in {"succeeded", "needs_confirmation"}:
                    return bundle, None, last["outcome"]
                if last["status"] == "running":
                    raise StageFailure("b_in_progress", "unresolved")
                if not retry:
                    code = (
                        "b_indeterminate"
                        if last["status"] == "indeterminate"
                        else "b_retry_required"
                    )
                    raise StageFailure(code, "unresolved")
            attempt_id = uuid.uuid4().hex
            now = self.clock()
            db.execute(
                "INSERT INTO b_attempts VALUES (?,?,?,?,?,?,?)",
                (attempt_id, set_id, "running", now, now + lease_seconds, None, None),
            )
            return bundle, attempt_id, None

    def finish_b(self, attempt_id, result, trace, indeterminate=False):
        with self.transaction() as db:
            status = (
                "indeterminate"
                if indeterminate
                else "needs_confirmation"
                if result["status"] == "needs_confirmation"
                else ("succeeded" if result["status"] == "ok" else "failed")
            )
            changed = db.execute(
                "UPDATE b_attempts SET status=?, outcome=?, trace=? "
                "WHERE id=? AND status='running' AND lease_until>?",
                (status, encode(result), encode(trace), attempt_id, self.clock()),
            ).rowcount
            if not changed:
                raise StageFailure("b_attempt_superseded", "unresolved")


def normalize_selection(selection, order):
    if not isinstance(selection, list) or not selection:
        raise StageFailure("invalid_selection")
    ids = set()
    for value in selection:
        if type(value) is int or (isinstance(value, str) and value.isascii() and value.isdigit()):
            number = int(value)
            if not 1 <= number <= len(order):
                raise StageFailure("invalid_selection")
            key = order[number - 1]
        elif isinstance(value, str) and ":" in value:
            key = value
        else:
            raise StageFailure("invalid_selection")
        if key not in order:
            raise StageFailure("selection_outside_candidates")
        ids.add(key)
    return [key for key in order if key in ids]
