"""저장된 자료도 실제 실행 화면과 같은 비교 UI로 만든다. 실행 원본은 수정하지 않는다."""
import copy
import hashlib
import json

from dependencies import HERE, UI_ROOT


def saved_page(html, conversations, *, key, title, description, cards=None):
    conversations = copy.deepcopy(conversations)
    for conversation in conversations:
        for message in conversation["msgs"]:
            if message.get("role") == "ai":
                message["savedRecord"] = True
    data = json.dumps(conversations, ensure_ascii=False).replace("<", "\\u003c")
    assert html.count("function seed() { return []; }") == 1
    html = html.replace("function seed() { return []; }", "function seed() { return " + data + "; }")
    digest = hashlib.sha256(data.encode()).hexdigest()[:16]
    html = html.replace("claview-abc-live-v1", f"claview-{key}-comparison-v2-{digest}")
    html = html.replace("CV.REVIEW_MODE = false;", "CV.REVIEW_MODE = true;")
    view = {"title": title, "description": description, "count": len(conversations)}
    html = html.replace("<title>ClaView · 실제 질문 연결 · Highcharts</title>", "<title>ClaView · " + title.replace("<", "&lt;").replace(">", "&gt;") + "</title>")
    html = html.replace("CV.SAVED_VIEW = null;", "CV.SAVED_VIEW = " + json.dumps(view, ensure_ascii=False).replace("<", "\\u003c") + ";")
    html = html.replace('통계표와 조회 조건을 확인한 뒤 그래프를 볼 수 있습니다. <a href="/sample" target="_blank" rel="noopener">저장 자료 보기</a>',
                        '저장 자료 보기 · 새 조회 없음 · <a href="http://127.0.0.1:8775/">실제 질문 실행 화면으로 이동</a>')
    if cards is not None:
        html = html.replace("DATA: []", "DATA: " + json.dumps(cards, ensure_ascii=False).replace("<", "\\u003c"), 1)
    return html


def build_sample_page():
    source = (UI_ROOT / "ClaView_시안A_인라인카드형_Highcharts.html").read_text(encoding="utf-8")
    cards, _ = json.JSONDecoder().raw_decode(source[source.index("DATA: ") + 6:])
    conversations = []
    for index, card in enumerate(cards):
        message = {"role": "ai", "kind": "result", "mode": "sample", "caseId": card["id"],
                   "inputText": card["query"], "cases": [card],
                   "answer": "저장 샘플의 비교 화면입니다. 지금 A·B 실행이나 새 KOSIS 조회를 한 결과는 아닙니다."}
        conversations.append({"id": card["id"], "title": card["id"] + " · " + card["query"],
                              "ts": 1792512000000 - index * 1000,
                              "msgs": [{"role": "user", "text": card["query"]}, message]})
    html = saved_page((HERE / "dist/index.html").read_text(encoding="utf-8"), conversations,
                      key="samples", title=f"저장 샘플 {len(cards)}건 · 같은 비교 화면",
                      description="원본 시안의 저장 자료입니다. 결측 시험 자료는 합성 자료로 표시합니다. 새 API 호출은 없습니다.", cards=cards)
    (HERE / "dist/sample.html").write_text(html, encoding="utf-8")
    (UI_ROOT / "ClaView_시안A_인라인카드형_Highcharts_최신.html").write_text(html, encoding="utf-8")
    return conversations
