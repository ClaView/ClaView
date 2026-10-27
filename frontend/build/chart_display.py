"""생성 화면에만 적용한다. 원본 시안과 조회·계산 로직은 수정하지 않는다."""
from paths import SRC as HERE, VENDOR, sha

ACCESSIBILITY = VENDOR / "highcharts-accessibility-11.4.1.js"
ACCESSIBILITY_SHA256 = "d63ab0804f0cf21ecb51b37e45f2020d1b7feaa3dab01e0e66ca675373ec9882"


def enhance(html):
    def replace(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise RuntimeError(f"표시 보완 위치 변경: {old[:65]}")
        html = html.replace(old, new, 1)

    if sha(ACCESSIBILITY) != ACCESSIBILITY_SHA256:
        raise RuntimeError("Highcharts 접근성 모듈 버전/해시 불일치")
    module = ACCESSIBILITY.read_text(encoding="utf-8")
    # Core와 같은 11.4.1 공식 모듈을 내장한다. 페이지를 볼 때 CDN 호출은 없다.
    replace("/* Highcharts adapter", module + "\n/* Highcharts adapter")
    replace("  function renderChartInto", (HERE / "chart_visuals.js").read_text(encoding="utf-8") + "\n  function renderChartInto")
    replace("i <= 12; i++) pal.push", "i <= 14; i++) pal.push")
    replace("</style>", """
:root{--s13:#087f8c;--s14:#875339;}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--s13:#5eead4;--s14:#e9bda5;}}
:root[data-theme="dark"]{--s13:#5eead4;--s14:#e9bda5;}
.visual-swatch{display:inline-flex;align-items:center;flex-shrink:0;width:26px;height:14px;}
.highcharts-container:focus-visible{outline:2px solid var(--accent);outline-offset:3px;}
</style>""")
    replace("var common = { labels: axis", "var common = { description:c.tableName+' · '+c.start+' ~ '+c.end+' · '+c.freqName, labels: axis")
    # series index는 선택된 부분집합이 아닌 원래 카드에서 찾는다.
    # 작은 그래프/전체 그래프 두 곳에 동일한 원칙을 적용한다.
    if html.count("name: s.name, color: colorOf(s), data:") != 2:
        raise RuntimeError("항목 선 모양 지정 위치 변경")
    html = html.replace("name: s.name, color: colorOf(s), data:", "name: s.name, color: colorOf(s), pattern: CV.visualStyle(c.series.indexOf(s)), data:")
    replace("var sw = el('span', { class: 'sw', style: 'background:var(--s' + ((i % 12) + 1) + ')' });",
            "var sw = el('span', { class:'visual-swatch', 'data-series-index':i }); sw.append(CV.visualSwatch(i,st.kind==='line'||st.kind==='small_multiples'&&c.smallType!=='bar'));")
    replace("      if (serCount) serCount.textContent", """      serBox.querySelectorAll('.visual-swatch').forEach(function(sw){
        sw.replaceChildren(CV.visualSwatch(Number(sw.dataset.seriesIndex),st.kind==='line'||st.kind==='small_multiples'&&c.smallType!=='bar'));
      });
      if (serCount) serCount.textContent""")
    replace("class: 'stage-note', role: 'note'", "class: 'stage-note', role: 'note', 'aria-live':'polite'")
    replace("같은 시점의 선 높이와 시간에 따른 방향을 비교하세요.", "항목별 색·선 모양을 확인하고 같은 시점의 선 높이와 시간에 따른 방향을 비교하세요.")
    replace("horizontal: '선택한 한 시점에서 항목별 막대 길이를 비교하세요. 시점 선택을 바꾸면 그 시점 값으로 바뀝니다.',",
            "horizontal: '선택한 한 시점에서 항목별 막대 길이를 비교하세요.'+(c.periods.length>1?' 시점 선택을 바꾸면 그 시점 값으로 바뀝니다.':' 이 결과는 한 시점의 값입니다.'),")
    replace("guide.textContent = (guides[st.kind] || '')", "guide.textContent = (guides[st.kind] || '') + (st.kind==='table'?'':' 키보드: Tab으로 차트에 들어가 방향키로 값을 살피고, Tab으로 다음 영역에 이동할 수 있습니다.')")
    replace("(c.derived?' 계산값은 %입니다. 0선 위는 상승, 아래는 하락입니다.':'')",
            "(c.derived?(st.kind==='horizontal'?' 계산값은 %입니다. 0선 오른쪽은 상승, 왼쪽은 하락입니다.':' 계산값은 %입니다. 양수는 상승, 음수는 하락입니다.'):'')")
    replace("title: { text: null }, gridLineColor: th.grid, maxPadding: 0.18,",
            "title: { text: null }, gridLineColor: th.grid, maxPadding: 0.18, plotLines:cfg.derived?[{value:0,width:1,color:th.text2,zIndex:3}]:[],")
    replace("      // 원본 시안 B와 같은 독립 실행 번들. 별도 접근성 모듈 도입은 후속 범위다.\n      accessibility: { enabled: false }", """      // 색·마우스만으로 정보를 전달하지 않는다. 원래 시점·값·단위를 읽는다.
      accessibility: {
        enabled:true,
        description:escapeText(cfg.description+' · '+cfg.unit+' · 수치표와 CSV도 함께 제공합니다.'),
        keyboardNavigation:{enabled:true,seriesNavigation:{mode:'normal',pointNavigationEnabledThreshold:false}},
        series:{pointDescriptionEnabledThreshold:false},
        point:{descriptionFormatter:function(p){
          var label=cfg.type==='horizontal'?cfg.cats[p.index]:cfg.full[p.index];
          var name=cfg.type==='horizontal'?cfg.periodLabel:p.series.name;
          return label+' · '+name+' · '+(p.y==null?'자료 없음':fmt(p.y)+' '+cfg.unit);
        }},
        screenReaderSection:{beforeChartFormat:'<div>{chartLongdesc}</div>',afterChartFormat:'{endOfChartMarker}'}
      }""")
    replace("Highcharts.setOptions({ lang: { thousandsSep: ',' } });", """Highcharts.setOptions({lang:{thousandsSep:',',accessibility:{
      defaultChartTitle:'통계 차트',chartContainerLabel:'{title}. 방향키로 항목과 시점을 살펴보세요.',
      svgContainerLabel:'통계 차트',credits:'Highcharts 차트',
      screenReaderSection:{beforeRegionLabel:'통계 차트 설명',afterRegionLabel:'통계 차트 끝',endOfChartMarker:'차트 끝'}
    }}});""")
    # 내장 범례는 합산값을 임의로 바꾸지 않는 설명용이다. 조작은 실제 연결된 체크박스에서 한다.
    replace("enabled: !!cfg.legend, align: 'left', itemStyle: style(th.text, 12),",
            "enabled: !!cfg.legend, accessibility:{enabled:false}, align: 'left', itemStyle: style(th.text, 12),")
    replace("series: cfg.series.map(function (s) { return { name: s.name, data: s.data, color: s.color }; })", """series: cfg.series.map(function (s) { return {
          name:s.name,data:s.data,color:s.color,
          dashStyle:cfg.type==='line'&&s.pattern?s.pattern.dashStyle:'Solid',
          marker:{symbol:s.pattern?s.pattern.marker:'circle'}
        }; })""")
    replace("color: s.color, name: s.name, width: width", "color: s.color, name: s.name, width: width, dash:cfg.type==='line'&&s.pattern?s.pattern.dash:[]")
    replace("context.beginPath(); context.moveTo(item.x, h + item.y);", "context.setLineDash(item.dash); context.beginPath(); context.moveTo(item.x, h + item.y);")
    replace("context.fillStyle = th.text;\n            context.fillText", "context.setLineDash([]); context.fillStyle = th.text;\n            context.fillText")
    return html
