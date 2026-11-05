"""Metadata cards and validation of server-held frequency alignment choices."""

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


def _label_card(label, note="A가 제시한 해석 후보"):
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
        "search_note": note,
    }


def make_alignment_confirmation(choice):
    """The card for the frequency question of `claview_b.align` (same shape as A's card)."""
    options = [
        {
            "option_id": option["option_id"],
            "label": option["label"],
            "tables": [_label_card(option["label"], "주기를 맞추는 방법")],
        }
        for option in choice["options"]
    ]
    group = {
        "requirement_id": choice["question_id"],
        "quote": " · ".join(f"{s['label']}" for s in choice["series"]),
        "indicator": choice["text"],
        "breakdown": [],
        "a_status": "question",
        "reasons": [choice["question_id"]],
        "options": options,
    }
    return {
        "kind": "alignment",
        "requires_interpretation_ack": False,
        "recovery_note": "",
        "interpretation_reasons": [],
        "conditions": [],
        "groups": [group],
        "question_text": choice["text"],
        "dimension": choice["question_id"],
    }


def validate_confirmation(paused, payload):
    """Check a browser choice against the server-held question; return A's answer dict.

    Raises ValueError with a user-facing message. No model or KOSIS call happens here.
    """
    confirmation = paused.get("confirmation")
    if paused.get("status") != "needs_confirmation" or not confirmation or confirmation.get("kind") != "alignment":
        raise ValueError("이 실행은 주기 맞춤 선택을 기다리는 상태가 아닙니다.")
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
    if confirmation.get("kind") == "alignment":
        # 주기 맞춤 질문: A의 질문이 아니므로 A에 답을 넘기지 않는다.
        return {
            "kind": "alignment",
            "question_id": group["requirement_id"],
            "option_id": option["option_id"],
        }
    raise ValueError("지원하지 않는 확인 형식입니다.")
