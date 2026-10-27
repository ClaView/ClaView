/* 계산 원자료와 근거는 숨기지 않는다. 사용자 선택은 계산 기준/공식 분모를 바꾸지 않는다. */
(function(){
  var CV=window.CV;
  function node(tag,text){var e=document.createElement(tag);if(text!=null)e.textContent=text;return e;}
  function rate(value){return value==null?null:Number(value).toLocaleString('ko-KR',{maximumFractionDigits:6});}
  function grid(headers,rows){
    var table=node('table');table.className='dt';
    var head=node('tr');headers.forEach(function(x){head.append(node('th',x));});
    var thead=node('thead');thead.append(head);table.append(thead);
    var body=node('tbody');rows.forEach(function(row){var tr=node('tr');row.forEach(function(x){tr.append(node('td',x==null?'계산 불가':String(x)));});body.append(tr);});table.append(body);
    var wrap=node('div');wrap.className='dt-wrap';wrap.append(table);return wrap;
  }
  CV.currentData=function(c,kind){
    // Highcharts의 percent 자동 재정규화를 사용하지 않는다. 공식 전체로 계산한 %를 normal로 쌓는다.
    if(kind!=='percent_stacked'||!c.percentSeries)return c;
    return Object.assign({},c,{series:c.percentSeries,unit:'% (공식 M2 전체 대비)',maxDecimals:2});
  };
  var original=CV.createResult;
  CV.createResult=function(c,saved,hooks){
    var r=original(c,saved,hooks);
    r.parts.analysis=function(){
      var a=c.calculation,composition=c.composition;
      if(!a&&!composition)return node('span');
      var details=node('details');details.className='analysis-evidence';
      details.append(node('summary',a?'계산 기준·원지수 보기':'합산 근거·공식 전체 대조 보기'));
      if(a){
        details.append(node('p',a.policy),node('p','계산식: '+a.formula),node('p','원지수 단위: '+a.source_unit+' · 계산 결과: % · 0 이하 기준값/결측은 계산하지 않습니다.'));
        details.append(node('p','계산값은 아래 근거 표에서 소수 6자리까지 표시하고, CSV에는 계산 정밀도를 보존합니다.'));
        details.append(grid(['항목','표시 시점','기준 시점','현재 지수','기준 지수','계산값 (%)','상태'],a.rows.map(function(x){return[x.name,x.period,x.baseline_period,x.current_value,x.baseline_value,rate(x.value),x.issue||'계산 완료'];})));
      }else{
        details.append(node('p',composition.reason),node('p','각 항목 ÷ 해당 시점 공식 M2 전체 × 100. 일부 항목을 숨기면 보이는 막대는 전체보다 작아집니다. 남은 항목으로 100%를 다시 만들지 않습니다.'),node('p','합계 대조 허용 오차: '+composition.tolerance+' 십억원. 공개 값의 반올림 차이만 허용합니다.'));
        details.append(grid(['시점','14개 구성 합계','공식 M2 전체','차이 (십억원)','허용 오차'],composition.checks.map(function(x){return[x.period,x.component_sum,x.official_total,x.difference,x.tolerance];})));
        var link=node('a','KOSIS 원표 · M2 상품별 구성내역');link.href=composition.source_url;link.target='_blank';link.rel='noopener';details.append(link);
        details.append(node('p','공식 전체 조회 시각: '+(composition.total_provenance.fetched_at||'저장 자료의 조회 시각 참고')));
      }
      return details;
    };
    return r;
  };
})();
