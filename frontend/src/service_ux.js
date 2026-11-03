/* 실제 조회·계산 규칙은 그대로 두고 설명과 표시 조작만 보완한다. */
(function(){
  var CV=window.CV,el=CV.el;
  function text(value){
    return String(value||'').replace(/ · 실제 호출:.*$/,'')
      .replace(/ · HCX \d+회\s*\/\s*KOSIS \d+회/g,'')
      .replace(/공유된 A 코드로/g,'입력하신').replace(/기존 B가/g,'프로그램이')
      .replace(/B의 조건 선택/g,'조회 조건 확정').replace(/실제 파이프라인 실행 기록/g,'실제 조회 진행')
      .replace(/as_of=/g,'조회 기준일: ')
      .replace(/서버가 전달한 단계별 상태만 표시합니다. 누락된 단계는 완료로 처리하지 않습니다./g,'각 단계의 실제 진행 상태입니다. 완료된 단계만 체크합니다.')
      .replace(/앞 단계 미확정 또는 지정된 검수 범위 때문에 실행하지 않았습니다./g,'앞 단계가 미확정이거나 이번 확인 범위에 포함되지 않아 실행하지 않았습니다.')
      .replace(/HCX 미호출/g,'추가 모델 호출 없음').replace(/HCX/g,'분석 모델')
      .replace(/B 조건/g,'조회 조건').replace(/B가/g,'프로그램이').replace(/B에서/g,'조회 조건 단계에서')
      .replace(/A에서/g,'통계표 선택 단계에서').replace(/A의/g,'표 선택의')
      .replace(/A·B/g,'질문·조건 분석').replace(/B 모델/g,'조건 분석 모델')
      .replace(/ui_cases/g,'표시 자료');
  }
  function period(p,f){
    p=String(p||'');if(!p)return '미제공';
    if(p.length===4)return p+'년';
    if(p.length===6)return p.slice(0,4)+'년 '+Number(p.slice(4))+({M:'월',Q:'분기',H:'반기'}[f]||'');
    return p;
  }
  CV.ux={text:text,period:period,
    confirmationReason:function(spec,g){
      if(spec.kind==='alignment')return '주기 확인 필요: 함께 요청한 지표의 주기가 달라 한 그래프로 맞추려면 값을 바꿔야 합니다. 방법을 고르면 이미 조회한 값으로 바로 이어서 표시하며, 원래 값은 보존됩니다.';
      if(spec.requires_interpretation_ack)return '질문 확인 필요: 프로그램이 읽은 대상이나 조건을 아직 확정하지 못했습니다. 아래 해석이 의도와 맞는지 확인해 주세요. 질문이 반드시 잘못됐다는 뜻은 아닙니다.';
      return g.options.length>1?'표 확인 필요: 관련 통계표 후보가 '+g.options.length+'개 남았지만, 자동 선택 기준을 통과한 표를 확정하지 못했습니다. 아래 표의 기준·주기·수록 기간을 비교해 골라 주세요. 모든 후보가 같은 뜻이라는 의미는 아닙니다.':
        '표 확인 필요: 후보는 찾았지만 질문의 조건과 충분히 일치하는지 자동으로 확정하지 못했습니다. 후보가 하나여도 정답으로 단정하지 않고 확인받습니다.';
    },
    simplifyProgress:function(root){
      // 화면 문구만 바꾼다. 서버의 status·이벤트·원본 이력은 바꾸지 않는다.
      var walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),nodes=[],n;
      while((n=walker.nextNode()))nodes.push(n);
      nodes.forEach(function(n){n.nodeValue=text(n.nodeValue);});return root;
    }
  };
  // 증감 계산용으로 조회한 기준 시점(c.baseline)은 차트 앞쪽에 흐리게 덧그린다.
  // 표·CSV·요약의 값 목록은 요청한 기간만 쓰므로 여기서는 차트 설정만 보탠다.
  var baselineOf=new WeakMap();
  function registerBaseline(c){
    if(!c.baseline||!c.labels||baselineOf.has(c.labels))return;
    var b=c.baseline,values={};
    c.series.forEach(function(s){values[s.name]=(b.values[s.id]||[]).map(function(v){return v==null?null:+v;});});
    baselineOf.set(c.labels,{axis:b.periods.map(function(p){return CV.axisLabel(c.freq,p);}),
      full:b.labels.map(function(l){return l+' (비교 기준)';}),values:values});
  }
  function fadeBaseline(host,count,small){
    var chart=(window.Highcharts&&Highcharts.charts||[]).filter(function(h){return h&&h.renderTo===host;})[0];
    if(!chart)return;
    var th=CV.theme();
    chart.series.forEach(function(s){
      var faded=Highcharts.color(s.color).setOpacity(0.35).get('rgba');
      s.update({zoneAxis:'x',zones:[{value:count-0.5,color:faded,dashStyle:'Dot'},{}]},false);
    });
    chart.xAxis[0].addPlotBand({from:-0.5,to:count-0.5,id:'baseline-band',className:'baseline-band',
      color:Highcharts.color(th.text2).setOpacity(0.08).get('rgba'),
      label:small?undefined:{text:'비교 기준',style:{color:th.text2,fontSize:'10px',fontFamily:th.font},verticalAlign:'top',y:12}});
    chart.redraw(false);
  }
  function wrapAdapter(){
    var adapter=CV.adapter;
    if(!adapter||adapter.baselineWrapped)return;
    adapter.baselineWrapped=true;
    var create=adapter.create;
    adapter.create=function(host,cfg){
      var b=cfg.type!=='horizontal'&&cfg.full&&baselineOf.get(cfg.full);
      if(!b)return create.call(adapter,host,cfg);
      var count=b.axis.length;
      var next=Object.assign({},cfg,{labels:b.axis.concat(cfg.labels),full:b.full.concat(cfg.full),
        series:cfg.series.map(function(s){
          return Object.assign({},s,{data:(b.values[s.name]||new Array(count).fill(null)).concat(s.data)});})});
      var handle=create.call(adapter,host,next);
      fadeBaseline(host,count,!!cfg.small);
      return handle;
    };
  }
  var original=CV.createResult;
  CV.createResult=function(c,saved,hooks){
    registerBaseline(c);wrapAdapter();
    var summaryUnit,shownCount;
    var r=original(c,saved,{onChange:function(state){
      if(summaryUnit)summaryUnit.textContent=CV.currentData(c,state.kind).unit;
      if(shownCount)shownCount.textContent=state.checked.length+' / '+c.series.length+'개 표시';
      if(hooks&&hooks.onChange)hooks.onChange(state);
    }});
    function periodInfoBlock(rows){
      // B가 알려 준 기간 결정: 요청한 기간, 실제로 읽은 기간, 표에 없는 기간, 계산용 기준 기간, 결정 근거.
      var wrap=el('section',{class:'period-info','aria-label':'기간 안내'});
      rows.forEach(function(row){
        var box=el('div',{class:'period-info-table'});
        if(rows.length>1)box.append(el('h4',{text:row.tableName}));
        var list=el('dl',{class:'period-info-list'});
        function line(label,text){list.append(el('div',{},el('dt',{text:label}),el('dd',{text:text})));}
        line('요청한 기간',row.requested.join(', ')||'기간 미지정');
        line('조회한 기간',(row.fetched.join(', ')||'조회한 기간 없음')+' · '+row.freqName);
        if(row.missing.length)line('표에 없는 기간',row.missing.join(', '));
        if(row.substituted)line('대체 조회','요청한 기간이 표에 없어 조회 가능한 기간으로 바꿔 조회했습니다.');
        row.extra.forEach(function(x){line('계산용 기준 기간',x.range+' · '+x.purpose+(c.baseline?' (차트에 흐리게 표시, 표·내려받기 파일에는 넣지 않음)':' (화면에는 표시하지 않음)'));});
        var src=row.sources||{},how=[];
        if(src.period)how.push('기간: '+src.period);
        if(src.frequency)how.push('주기: '+src.frequency);
        if(how.length)line('결정 근거',how.join(' · '));
        if(row.alternatives&&row.alternatives.length)line('다른 주기',row.alternatives.join(', ')+'도 가능하지만 자동으로 조회하지 않았습니다.');
        box.append(list);wrap.append(box);
      });
      return wrap;
    }
    r.parts.summary=function(){
      var box=el('section',{class:'result-overview','aria-label':'조회 결과 요약'});
      box.append(el('h3',{text:c.tableName}));
      var facts=el('dl',{class:'result-facts'});
      function fact(label,value){var dd=el('dd',{text:value});facts.append(el('div',{},el('dt',{text:label}),dd));return dd;}
      fact('실제 표시 기간',c.start+' ~ '+c.end);fact('주기',c.freqName);
      shownCount=fact('항목',r.st.checked.size+' / '+c.series.length+'개 표시');
      summaryUnit=fact('단위',CV.currentData(c,r.st.kind).unit);
      fact('출처',(c.org||'제공 기관')+' · KOSIS');
      var fetched=new Date(c.fetchedAt),when=Number.isNaN(fetched.getTime())?(c.fetchedAt||'미제공'):fetched.toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' (한국 시간)';
      fact('자료 조회 시각',when);box.append(facts);
      if(c.periodInfo&&c.periodInfo.length){box.append(periodInfoBlock(c.periodInfo));}
      else if(c.requestedPeriod){var p=c.requestedPeriod;
        box.append(el('p',{class:'requested-period',text:'요청 표시 기간: '+period(p.start,c.freq)+' ~ '+period(p.end,c.freq)+' · 실제 표시 범위는 위 기간을 확인하세요.'}));}
      if(c.synthetic)box.append(el('p',{class:'confirmation-warning',text:'결측 표시 시험용 합성 자료입니다. 실제 통계로 해석하지 마세요.'}));
      if(c.derived)box.append(el('p',{class:'stage-note',text:'조회한 원지수로 계산한 결과입니다. 아래에서 계산 기준과 원값을 확인할 수 있습니다.'}));
      box.append(el('p',{class:'res-reco',text:c.recommend}));return box;
    };
    var banners=r.parts.banners;
    r.parts.banners=function(){var box=banners();return box?CV.ux.simplifyProgress(box):null;};
    var seriesList=r.parts.seriesList;
    r.parts.seriesList=function(){
      var box=seriesList(),list=box.querySelector('.ser-list'),rows=Array.from(list.children);
      var count=el('span',{class:'series-match-count',role:'status','aria-live':'polite'});
      rows.forEach(function(row,i){
        var s=c.series[i],wrap=el('div',{class:'series-choice'});
        row.before(wrap);wrap.append(row,el('button',{type:'button',class:'series-only',text:'이 항목만',
          'aria-label':s.name+' 항목만 보기',onclick:function(){r.selectOnly(s.id);}}));
      });
      if(c.series.length>4){
        var search=el('input',{type:'search',class:'series-search',placeholder:'표시 항목 이름으로 찾기','aria-label':'표시 항목 검색'});
        function filter(){var term=search.value.trim().toLowerCase(),matches=0;
          rows.forEach(function(row,i){var on=(c.series[i].name+' '+c.series[i].full).toLowerCase().includes(term);row.parentElement.hidden=!on;if(on)matches++;});
          count.textContent=matches+'개 항목 이름 일치 · 검색만으로 그래프 선택은 바뀌지 않습니다.';
        }
        search.addEventListener('input',filter);box.querySelector('.ser-head').after(el('div',{class:'series-search-row'},search,count));filter();
      }
      return box;
    };
    return r;
  };
})();
