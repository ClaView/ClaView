"""원본 시안 A는 그대로 두고 실제 질문 실행용 HTML을 별도 생성한다."""
import json
import re
from pathlib import Path
from paths import DIST, SRC as HERE, UI_ROOT, sha
from example_questions import example_groups

SOURCE = UI_ROOT / "ClaView_시안A_인라인카드형_Highcharts.html"


def build():
    html = SOURCE.read_text(encoding="utf-8")
    original_hash = sha(SOURCE)

    def replace(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise RuntimeError(f"원본 변경 감지: 교체 위치 {old[:45]!r}")
        html = html.replace(old, new, 1)

    # 실제 실행 화면에는 정답 샘플 숫자나 키워드→정답 연결을 넣지 않는다.
    html, count = re.subn(r"  var CV = \(window\.CV = \{ DATA: .*?\n",
                         "  var CV = (window.CV = { DATA: [], adapters: {}, layout: {}, adapter: null });\n", html)
    assert count == 1
    a, b = html.index("  /* ---------- engine ---------- */"), html.index("/* 진행 과정 표시 전용 코드.")
    groups = json.dumps(example_groups(), ensure_ascii=False).replace("<", "\\u003c")
    html = html[:a] + "  CV.EXAMPLE_GROUPS = " + groups + ";\n" + """
  CV.REVIEW_MODE = false;
  CV.SAVED_VIEW = null;
  CV.DISPLAY_VERSION = 'comparison-flow-20261021-v2';
  CV.EXAMPLES = CV.EXAMPLE_GROUPS.flatMap(function(g){return g.items;});
  CV.SAMPLES = [];
  CV.STATE_SAMPLES = [];
""" + html[b:]
    # 첫 화면에는 질문 입력과 접힌 전체 예시 버튼만 둔다. 고정된 4개 카드는 생성하지 않는다.
    replace("CV.SAMPLES.forEach(function (s) { D.chips.append(el('button', { type: 'button', class: 'chip', onclick: function () { run(s.q); } }, s.label)); });", "")
    replace("      if (D.stateChips) CV.STATE_SAMPLES.forEach(function (s) { D.stateChips.append(el('button', { type: 'button', class: 'chip ghost', onclick: function () { run(s.q); } }, s.label)); });\n", "")
    replace('<div id="chips" class="chips"></div><div id="stateChips" class="chips"></div>', '')
    # 예시 클릭은 입력만 한다. 엔터도 줄바꿈으로 남겨 실행 버튼과 구분한다.
    replace("      D.input.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); D.composer.requestSubmit(); } });\n", "")
    replace("if (v) { D.input.value = ''; grow(); run(v); }", "if (v) { D.input.value = ''; grow(); refreshExampleHint(); run(v); }")
    replace("claview-mock-a-highcharts-v1", "claview-abc-live-v1")
    # 새로고침 때 기존 대화를 복원한다. 저장 자료 페이지는 원래처럼 목록에서 선택한다.
    replace("      S.active = null;", "      S.active = !CV.SAVED_VIEW && st && S.convs.some(function(c){return c.id===st.active;}) ? st.active : null;")
    replace("S.active = null; renderThread(); renderHist(); closeSide();", "S.active = null; saveStore(); renderThread(); renderHist(); closeSide();")
    replace("  var KIND_NAME = { stacked: '누적막대', percent_stacked: '비중 누적' };",
            "  var KIND_NAME = { line:'선그래프', bar:'묶음막대', dual_axis:'이중 축', table:'수치표', horizontal:'한 시점 가로막대', small_multiples:'항목별 나눠 보기', stacked: '누적막대', percent_stacked: '100% 누적막대' };")
    a, b = html.index("  function seed() {"), html.index("  function groupOf(")
    html = html[:a] + "  function seed() { return []; }\n" + html[b:]
    a, b = html.index("  function fillAi("), html.index("\n})();\n\n/* Highcharts adapter")
    html = html[:a] + (HERE / "b_conditions_ui.js").read_text(encoding="utf-8") + (HERE / "comparison_flow.js").read_text(encoding="utf-8") + (HERE / "live_app.js").read_text(encoding="utf-8") + html[b:]
    replace("    if (us.length) scrollToMsg(us[us.length - 1]); else scrollEnd(true);",
            "    scrollToCurrentOutcome(cv);")
    replace("    D.empty.hidden = !!(cv && cv.msgs.length);", "    D.empty.hidden = !!(cv && cv.msgs.length);\n    if(CV.syncComposerPlacement)CV.syncComposerPlacement(cv);")
    replace("} }, cv.title),\n          el('button'", "} }, el('span',{class:'history-title',text:cv.title}), historyBadge(cv)),\n          el('button'")
    replace("이전 모의 진행이 중지된 기록입니다. 실제 조회 중지 기록은 아닙니다.",
            "화면을 닫아 실행 결과를 받지 못한 기록입니다. 서버 작업 완료 여부는 확인되지 않았습니다.")
    replace("cv.msgs.forEach(function (m) { if (m.kind === 'run')", "cv.msgs.forEach(function (m) { m.loadedFromHistory=true; if (m.kind === 'run')")
    replace("저장된 샘플을 보여줍니다. 실제 A·B 실행 및 새 KOSIS 조회는 연결되어 있지 않습니다.",
            '통계표와 조회 조건을 확인한 뒤 그래프를 볼 수 있습니다. <a href="/sample" target="_blank" rel="noopener">저장 자료 보기</a>')
    replace('시안 A · 인라인 카드형', '한국은행 통계 · KOSIS')
    replace("한국은행 통계를 자연어로 물어보면 표와 기간을 찾아 차트로 보여드립니다.",
            "궁금한 통계와 기간을 적어 주세요. 예시를 골라 시작해도 좋아요.")
    replace("개가 저장되어 있습니다.", "개를 조회했습니다.")
    replace("    if (freq === 'Q') return", "    if (freq === 'H') return p.slice(0, 4) + '년 ' + Number(p.slice(4)) + '반기';\n    if (freq === 'Q') return")
    # 원본 시안의 데이터/CSV를 보존하되 100% 선택 시에는 공식 전체 기준 %를 사용한다.
    replace("    box.innerHTML = '';\n    var sel = c.series.filter", "    box.innerHTML = '';\n    c = CV.currentData(c, st.kind);\n    var sel = c.series.filter")
    replace("var common = { labels: axis, full: c.labels, unit: c.unit, decimals: c.maxDecimals, theme: th };",
            "var common = { labels: axis, full: c.labels, unit: c.unit, decimals: c.maxDecimals, theme: th, percent: st.kind==='percent_stacked', highlights:(c.highlights||[]).map(function(p){return c.periods.indexOf(p);}), derived:!!c.derived };")
    replace("type: st.kind === 'bar' ? 'bar' : 'line', small: false, legend: false",
            "type: ['stacked','percent_stacked'].indexOf(st.kind)>=0?'stacked':st.kind === 'bar' ? 'bar' : 'line', small: false, legend: ['stacked','percent_stacked'].indexOf(st.kind)>=0")
    replace("chart: Object.assign({}, base.chart, { type: cfg.type === 'bar' ? 'column' : 'line' }),",
            "chart: Object.assign({}, base.chart, { type: cfg.type === 'bar' || cfg.type === 'stacked' ? 'column' : 'line' }),")
    replace("categories: cfg.labels, lineColor: th.grid, tickColor: th.grid,",
            "categories: cfg.labels, lineColor: th.grid, tickColor: th.grid,\n          plotBands:(cfg.highlights||[]).filter(function(i){return i>=0;}).map(function(i){return {from:i-.4,to:i+.4,color:th.grid,label:{text:'최대 변화',style:style(th.text2,10)}};}),")
    replace("          gridLineColor: th.grid,\n          labels: {",
            "          gridLineColor: th.grid, min:cfg.percent?0:undefined,max:cfg.percent?100:undefined,\n          plotLines:cfg.derived?[{value:0,width:1,color:th.text2,zIndex:3}]:[],\n          labels: {")
    replace("            animation: false, connectNulls: false,\n            marker:",
            "            animation: false, connectNulls: false, stacking:cfg.type==='stacked'?'normal':undefined,\n            events:{legendItemClick:function(){return false;}},\n            marker:")
    replace("      else csvBox.append(CV.buildTable(c, sel, true));",
            "      else {var displayed=CV.currentData(c,st.kind);csvBox.append(CV.buildTable(displayed,displayed.series.filter(function(s){return ids.indexOf(s.id)>=0;}),true));}")
    replace("'.csv', CV.csv(c, ids));", "'.csv', CV.csv(CV.currentData(c,st.kind), ids));")
    replace("subtitle: c.start + ' ~ ' + c.end + ' · 단위: ' + c.unit,", "subtitle: c.start + ' ~ ' + c.end + ' · 단위: ' + CV.currentData(c,st.kind).unit,")
    replace("if (!cfg.small && cfg.type !== 'horizontal') {", "if (!cfg.small && cfg.type !== 'horizontal' && !cfg.legend) {")
    replace("g.fillText(meta.title, pad, pad + 12 * S);", "g.fillText(meta.title, pad, pad + 12 * S, W-pad*2);")
    replace("g.fillText(meta.subtitle, pad, pad + 34 * S);", "g.fillText(meta.subtitle, pad, pad + 34 * S, W-pad*2);")
    replace("g.fillText(meta.footer, pad, H - pad / 2 - 2 * S);", "g.fillText(meta.footer, pad, H - pad / 2 - 2 * S, W-pad*2);")
    replace("c.series.length + '개를 조회했습니다.'", "c.series.length + (c.derived?'개를 계산했습니다.':'개를 조회했습니다.')")
    replace("    var b = r.parts.banners(); if (b) slot.append(b);", "    var b = r.parts.banners(); if (b) slot.append(b);\n    slot.append(r.parts.analysis());")
    replace("  CV.app.init();", (HERE / 'analysis_ui.js').read_text(encoding='utf-8') + "\n" + (HERE / 'service_ux.js').read_text(encoding='utf-8') + "\n  CV.app.init();")
    # 이중 축: 단위가 다른 두 지표를 왼쪽·오른쪽 축에 나눠 그린다. 값은 바꾸지 않고 축만 나눈다.
    replace("    var host3 = el('div', { class: 'main-chart' }); box.append(host3);",
            """    if (st.kind === 'dual_axis' && c.axes) {
      var hostD = el('div', { class: 'main-chart' }); box.append(hostD);
      mk(hostD, { type: 'dual', small: false, legend: false, axes: c.axes, series: sel.map(function (s) {
        return { name: s.name, color: colorOf(s), unit: s.unit, axis: s.axis || 0, data: s.values.map(function (v) { return v == null ? null : +v; }) };
      }) });
      return handle;
    }
    var host3 = el('div', { class: 'main-chart' }); box.append(host3);""")
    # 항목별 나눠 보기에서도 각 항목의 단위를 쓴다(이중 축 카드는 항목마다 단위가 다르다).
    replace("mk(host, { type: c.smallType === 'bar' ? 'bar' : 'line', small: true, title: s.name, series:",
            "mk(host, { type: c.smallType === 'bar' ? 'bar' : 'line', small: true, title: s.name, unit: s.unit || c.unit, series:")
    replace("""    } else {
      // 선·묶음막대·항목별 나눠 보기: 같은 cfg를 Highcharts 설정으로 옮긴다.""",
            """    } else if (cfg.type === 'dual') {
      // 이중 축: 계열마다 단위·축 번호를 받는다. 막대(column)는 뒤에, 선은 앞에 그린다.
      options = Object.assign(base, {
        xAxis: {
          categories: cfg.labels, lineColor: th.grid, tickColor: th.grid,
          labels: { style: style(th.text2, 11), rotation: 0, autoRotation: false, overflow: 'justify' },
          tickInterval: Math.ceil(cfg.labels.length / 6)
        },
        yAxis: cfg.axes.map(function (axis, i) {
          return {
            title: {
              text: axis.unit, align: 'high', rotation: 0, y: -14, x: i ? 4 : -4,
              textAlign: i ? 'right' : 'left', style: style(th.text2, 11)
            },
            opposite: axis.side === 'right', gridLineColor: th.grid, gridLineWidth: i ? 0 : 1,
            labels: {
              style: style(th.text2, 11),
              formatter: function () { return CV.num(this.value, Math.abs(this.value) < 100 ? 1 : 0); }
            }
          };
        }),
        tooltip: {
          shared: true, useHTML: false,
          formatter: function () {
            var point = this.points && this.points[0];
            if (!point) return false;
            var index = point.point.index;
            var lines = [escapeText(cfg.full[index])];
            cfg.series.forEach(function (s) {
              var value = s.data[index];
              lines.push('<span style="color:' + s.color + '">●</span> ' + escapeText(s.name) +
                ': <b>' + (value == null ? '자료 없음' : fmt(value) + ' ' + escapeText(s.unit)) + '</b>');
            });
            return lines.join('<br/>');
          }
        },
        accessibility: Object.assign({}, base.accessibility, {
          point: { descriptionFormatter: function (p) {
            var s = cfg.series[p.series.index] || { unit: '' };
            return cfg.full[p.index] + ' · ' + p.series.name + ' · ' + (p.y == null ? '자료 없음' : fmt(p.y) + ' ' + s.unit);
          } }
        }),
        plotOptions: {
          series: { animation: false, connectNulls: false, marker: { enabled: cfg.labels.length <= 30, radius: 3 } },
          column: { pointPadding: 0.08, groupPadding: 0.1, borderWidth: 0 }
        },
        series: cfg.series.map(function (s) {
          var draw = cfg.axes[s.axis].draw === 'column' ? 'column' : 'line';
          return { name: s.name, type: draw, yAxis: s.axis, color: s.color, data: s.data, zIndex: draw === 'line' ? 3 : 1 };
        })
      });
    } else {
      // 선·묶음막대·항목별 나눠 보기: 같은 cfg를 Highcharts 설정으로 옮긴다.""")
    # 그래프를 바꿀 때 읽는 법도 함께 바뀐다. 데이터 자체는 바꾸지 않는다.
    replace("    r.setKind = function (k) { st.kind = k; changed(); };",
            "    r.setKind = function (k) { if (c.kinds.indexOf(k) < 0) return; st.kind = k; changed(); };")
    replace("    var adapter = CV.adapter;\n    var st =",
            """    var adapter = CV.adapter;
    // 이전 화면의 자동 나눠 보기·일부 선택 상태는 이번 표시 버전에서 한 번 초기화한다.
    if(saved&&saved.displayVersion!==CV.DISPLAY_VERSION)saved=null;
    if(c.defaultView==='small_multiples'&&!c.chartPolicyVersion){
      var togetherKind=(c.smallType==='bar'||/막대/.test(c.query))?'bar':'line';
      if(c.kinds.indexOf(togetherKind)<0)togetherKind=c.kinds.indexOf('line')>=0?'line':'table';
      c=Object.assign({},c,{defaultView:togetherKind,recommend:'같은 주기·단위의 항목을 함께 비교합니다. 복잡하면 항목별 나눠 보기를 선택할 수 있습니다.'});
    }
    var st =""")
    replace("    if (!saved && c.series.length > 30) st.checked = new Set(c.series.slice(0, 8).map(function (s) { return s.id; }));", "    // 요청된 항목은 기본적으로 모두 표시한다. 조회 범위 제한은 기존 B에서 검증한다.")
    replace("{ kind: st.kind, checked: Array.from(st.checked), period: st.period, csvAll: st.csvAll }", "{ kind: st.kind, checked: Array.from(st.checked), period: st.period, csvAll: st.csvAll, displayVersion: CV.DISPLAY_VERSION }")
    replace("    r.setAll = function (on) { st.checked = new Set(on ? c.series.map(function (s) { return s.id; }) : []); changed(); };",
            """    r.setAll = function (on) { st.checked = new Set(on ? c.series.map(function (s) { return s.id; }) : []); changed(); };
    r.selectOnly = function (id) { if(!c.series.some(function(s){return s.id===id;}))return; st.checked=new Set([id]); changed(); };
    r.parts.compareButton = function () {
      var together=['line','bar','stacked','percent_stacked'];
      var kind=together.indexOf(c.defaultView)>=0?c.defaultView:c.kinds.find(function(k){return together.indexOf(k)>=0;});
      if(c.series.length<2||!kind)return null;
      return el('button',{type:'button',class:'btn compare-all','aria-label':'전체 항목 함께 비교',onclick:function(){
        st.checked=new Set(c.series.map(function(s){return s.id;}));st.kind=kind;changed();
      }},'전체 항목 함께 비교');
    };""")
    replace("r.parts.typeSwitch('segmented'), r.parts.pngButton()", "r.parts.typeSwitch('segmented'), r.parts.compareButton(), r.parts.pngButton()")
    # aria 속성은 HTML boolean 속성이 아니므로 초기 선택 상태도 문자열로 전달한다.
    replace("'aria-pressed': k === st.kind,", "'aria-pressed': String(k === st.kind),")
    replace("    var r = { c: c, st: st, parts: {} };",
            """    var r = { c: c, st: st, parts: {} };
    var guide = el('p', {class: 'stage-note', role: 'note'});
    function updateGuide() {
      var guides = {
        line: '같은 시점의 선 높이와 시간에 따른 방향을 비교하세요. 끊어진 구간은 값이 없는 구간입니다.',
        bar: '같은 시점에 나란히 놓인 막대 높이를 비교하세요. 0과 자료 없음은 다릅니다.',
        small_multiples: '항목별 작은 그래프에서 흐름을 보세요. 크기를 비교하기 전 각 세로축 눈금을 확인하세요.',
        horizontal: '선택한 한 시점에서 항목별 막대 길이를 비교하세요. 시점 선택을 바꾸면 그 시점 값으로 바뀝니다.',
        table: '시점별 값과 단위를 확인하세요. 빠진 값은 0으로 채우지 않습니다. 원래 소수 자릿수는 CSV에서 확인하세요.'
      };
      guides.stacked='막대 전체 높이는 M2 규모, 색별 높이는 상품별 금액입니다. 항목을 숨기면 보이는 합계는 전체가 아닙니다.';
      guides.percent_stacked='해당 시점의 공식 M2 전체 대비 비중입니다. 항목을 숨겨도 분모를 바꾸지 않습니다. CSV도 현재 비중값(%)으로 저장합니다.';
      guides.dual_axis='단위가 서로 다른 두 지표를 왼쪽·오른쪽 축에 나눠 그렸습니다. 축마다 눈금이 달라 막대와 선의 높이를 직접 비교하지 말고, 각 축의 단위를 확인하며 방향을 비교하세요.';
      guide.textContent = (guides[st.kind] || '') + (c.derived?' 계산값은 %입니다. 0선 위는 상승, 아래는 하락입니다.':'') + ' 단위: ' + CV.currentData(c,st.kind).unit + ' · 주기: '+c.freqName;
    }
    r.parts.guide = function () { updateGuide(); return guide; };""")
    replace("    function changed(what) {\n      persist();",
            "    function changed(what) {\n      updateGuide(); persist();")
    replace("r.parts.blockedNote(), r.parts.seriesList(), r.parts.chart()),",
            "r.parts.blockedNote(), r.parts.guide(), r.parts.seriesList(), r.parts.chart()),")
    replace("<title>ClaView 시안 A · Highcharts</title>",
            "<title>ClaView · 실제 질문 연결 · Highcharts</title>")
    replace('</style>', (HERE / 'examples.css').read_text(encoding='utf-8') + '\n' + (HERE / 'service_ux.css').read_text(encoding='utf-8') + '\n</style>')
    replace("</style>", """
.disclaim a {color:var(--accent);text-underline-offset:3px;}
.confirmation-box{margin:18px 0;padding:18px;border:1px solid var(--accent);border-radius:14px;background:var(--surface);overflow-wrap:anywhere;}
.confirmation-box h3{margin:0 0 10px;}.confirmation-box fieldset{border:1px solid var(--line);border-radius:10px;margin:16px 0;padding:12px;min-width:0;}
.confirmation-box legend{padding:0 6px;max-width:100%;}.candidate-option{display:flex;align-items:flex-start;gap:10px;padding:12px;margin:8px 0;border:1px solid var(--line);border-radius:10px;cursor:pointer;}
.candidate-option:has(input:checked){border-color:var(--accent);background:var(--hover);}.candidate-option input{margin-top:4px;flex-shrink:0;accent-color:var(--accent);}
.candidate-option .stage-note{margin:4px 0;padding:0;border:0;font-size:12px;line-height:1.5;}.candidate-code{display:block;color:var(--text2);font-size:12px;margin-top:3px;}
.interpretation-check{display:flex;align-items:flex-start;gap:8px;margin:14px 0;}.confirmation-warning{color:#d3a84e;}
.continue-button{padding:11px 16px;background:var(--accent);color:var(--accent-ink);border:0;border-radius:8px;cursor:pointer;font-weight:600;}
.continue-button:disabled{opacity:.45;cursor:not-allowed;}.file-server-hint{padding:12px 18px;border:1px solid var(--accent);border-radius:10px;}
.file-server-hint a{display:block;color:var(--accent);margin-top:6px;}
</style>""")
    from chart_display import enhance
    html = enhance(html)
    # 앱/차트 스크립트를 구문 검사할 수 있도록 빌드 산출물로 함께 보관한다.
    dist = DIST
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(html, encoding="utf-8")
    for i, script in enumerate(re.findall(r"<script[^>]*>([\s\S]*?)</script>", html)):
        (dist / f"script-{i}.js").write_text(script, encoding="utf-8")
    (dist / "manifest.json").write_text(json.dumps(
        {"source": str(SOURCE), "source_sha256": original_hash,
         "generated_sha256": sha(dist / "index.html"), "original_unchanged": sha(SOURCE) == original_hash},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("Generated:", dist / "index.html")


# The review and saved-sample pages (saved_pages, report_*) were C's local review tools; they are kept in
# research/c/legacy_web and are not part of the service screen.


if __name__ == "__main__":
    build()
