# ruff: noqa: E501  (user-facing Korean text: ruff counts full-width characters as two columns)
"""A's pending question -> the confirmation card of the UI -> A's ID-only answer.

A (claview_a) owns the question: its text, its options and their effects live in the
server-held A state (`state["control"]["pending_question"]`). The browser only sees the
public question (labels, plus table facts read from the official metadata here) and
sends back option IDs. It cannot change the question, the tables or the state.

The card keeps the shape the UI already renders (groups -> options -> tables), so one
A question becomes one group; a table-less option (for example a reading choice) is
shown as a single labelled entry.
"""

from __future__ import annotations

FREQ_ORDER = ("Y", "H", "Q", "M")


def _table_index(store):
    index = {}
    for key, record in store.records.items():
        org_id, _, table_id = key.partition(":")
        index.setdefault(table_id, (org_id, record.get("name", table_id)))
    return index


def table_names(store):
    """table_id -> table name from the official metadata."""
    return {table_id: name for table_id, (_, name) in _table_index(store).items()}


def _table_card(store, index, table_id, differences):
    org_id, name = index.get(table_id, ("", table_id))
    card = {
        "org_id": org_id,
        "table_id": table_id,
        "table_name": name,
        "path": "",
        "frequencies": [],
        "coverage": [],
        "units": [],
        "different_axes": [],
        "example_items": [],
        "search_note": "A가 제시한 후보",
    }
    doc = store.catalog.get(f"{org_id}:{table_id}")
    if doc:
        card["path"] = doc.get("path", "")
    try:
        meta = store.get(org_id, table_id)
    except Exception:  # noqa: BLE001 - facts are optional; the candidate itself stays selectable
        return card
    freqs = [f for f in FREQ_ORDER if f in meta["periods"]]
    card["frequencies"] = freqs
    card["coverage"] = [
        {"frequency": f, "start": meta["periods"][f]["start"], "end": meta["periods"][f]["end"]}
        for f in freqs
    ]
    card["units"] = list(meta.get("units") or [])
    diff = next((d for d in differences if table_id in d.get("table_ids", [])), {})
    card["different_axes"] = list(diff.get("only_axes", []))
    card["example_items"] = (
        list(diff.get("only_values", []))[:6]
        or list(dict.fromkeys(i["name"] for i in meta["items"].values()))[:6]
    )
    return card


def _label_card(label):
    return {
        "org_id": "",
        "table_id": "",
        "table_name": label,
        "path": "",
        "frequencies": [],
        "coverage": [],
        "units": [],
        "different_axes": [],
        "example_items": [],
        "search_note": "A가 제시한 해석 후보",
    }


def make_confirmation(a_state, store):
    """The UI card for A's pending question, or None when A is not waiting."""
    pending = (a_state.get("control") or {}).get("pending_question")
    if not pending:
        return None
    index = _table_index(store)
    requirement_ids = pending.get("requirement_ids") or []
    selections = [
        s for s in a_state.get("selections") or [] if s.get("requirement_id") in requirement_ids
    ]
    differences = [d for s in selections for d in s.get("differences") or []]
    quotes = {
        r["id"]: r.get("quote", "")
        for r in ((a_state.get("analysis") or {}).get("interpretation") or {}).get("requirements")
        or []
    }
    options = []
    for option in pending["options"]:
        effect = option.get("effect") or {}
        if effect.get("kind") == "pick_assignment" and effect.get("tables"):
            tables = [_table_card(store, index, t, differences) for t in effect["tables"]]
        else:
            tables = [_label_card(option["label"])]
        options.append(
            {"option_id": option["option_id"], "label": option["label"], "tables": tables}
        )
    quote = " · ".join(q for q in (quotes.get(r) for r in requirement_ids) if q)
    group = {
        "requirement_id": pending["question_id"],
        "quote": quote or pending.get("text", ""),
        "indicator": pending.get("text", ""),
        "breakdown": [],
        "a_status": "question",
        "reasons": [pending.get("dimension", "")],
        "options": options,
    }
    return {
        "requires_interpretation_ack": False,
        "recovery_note": "",
        "interpretation_reasons": [],
        "conditions": [],
        "groups": [group],
        "question_text": pending.get("text", ""),
        "dimension": pending.get("dimension", ""),
    }


def validate_confirmation(paused, payload):
    """Check a browser choice against the server-held question; return A's answer dict.

    Raises ValueError with a user-facing message. No model or KOSIS call happens here.
    """
    a_state = paused.get("a_state")
    confirmation = paused.get("confirmation")
    if paused.get("status") != "needs_confirmation" or not a_state or not confirmation:
        raise ValueError("이 실행은 A의 확인 질문을 기다리는 상태가 아닙니다.")
    if not isinstance(payload, dict) or set(payload) != {"choices", "interpretation_confirmed"}:
        raise ValueError("선택 내용이 올바르지 않습니다.")
    if not isinstance(payload["interpretation_confirmed"], bool):
        raise ValueError("선택 내용이 올바르지 않습니다.")
    choices = payload["choices"]
    group = confirmation["groups"][0]
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("후보를 하나 선택해 주세요.")
    choice = choices[0]
    if not isinstance(choice, dict) or set(choice) != {"requirement_id", "option_id"}:
        raise ValueError("선택 형식이 올바르지 않습니다.")
    if choice["requirement_id"] != group["requirement_id"]:
        raise ValueError("확인할 질문이 원래 질문과 다릅니다.")
    option = next((o for o in group["options"] if o["option_id"] == choice["option_id"]), None)
    if option is None:
        raise ValueError("제시된 후보 중에서 선택해 주세요.")
    return {
        "request_id": a_state["request_id"],
        "question_version": a_state["question_version"],
        "question_id": group["requirement_id"],
        "option_id": option["option_id"],
    }
