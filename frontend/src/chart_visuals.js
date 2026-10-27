/* 값·항목 순서는 그대로 두고, 원래 항목 순번에 색/선 모양을 고정한다.
 * 체크 해제로 앞 항목을 숨겨도 남은 항목의 색·선이 바뀌지 않는다. */
CV.visualStyle = function (index) {
  var patterns = [
    { dashStyle: 'Solid', dash: [], name: '실선', marker: 'circle' },
    { dashStyle: 'Dash', dash: [8, 6], name: '파선', marker: 'square' },
    { dashStyle: 'ShortDot', dash: [2, 2], name: '점선', marker: 'diamond' },
    { dashStyle: 'DashDot', dash: [8, 6, 2, 6], name: '일점쇄선', marker: 'triangle' }
  ];
  return patterns[index % patterns.length];
};
CV.visualSwatch = function (index, line) {
  var ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', '26'); svg.setAttribute('height', '14');
  svg.setAttribute('viewBox', '0 0 26 14'); svg.setAttribute('aria-hidden', 'true');
  var mark = document.createElementNS(ns, line ? 'path' : 'rect');
  var color = 'var(--s' + (index % 14 + 1) + ')';
  if (line) {
    mark.setAttribute('d', 'M0 7 H26'); mark.setAttribute('fill', 'none');
    mark.setAttribute('stroke', color); mark.setAttribute('stroke-width', '2');
    mark.setAttribute('stroke-dasharray', CV.visualStyle(index).dash.join(','));
  } else {
    mark.setAttribute('x', '2'); mark.setAttribute('y', '2');
    mark.setAttribute('width', '22'); mark.setAttribute('height', '10');
    mark.setAttribute('fill', color);
  }
  svg.appendChild(mark); return svg;
};
