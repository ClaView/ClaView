  // 서버 응답만 사용한다. 후보 확인은 원래 실행과 연결된 별도 사용자 동작이다.
  var stageIds = ['question', 'table', 'conditions', 'fetch', 'chart'];
  function exampleForQuery(query){
    return CV.EXAMPLES.find(function(s){return s.q===(query||'').trim();});
  }
  function exampleNotice(s){
    var notice=el('aside',{class:'example-notice',role:'note'});
    notice.append(el('strong',{text:s.number+'번 · '+s.note.badge+' — 현재 구현 범위'}),el('span',{text:s.note.text}));
    return notice;
  }
  function refreshExampleHint(){
    if(!D.exampleHint)return;
    var selected=exampleForQuery(D.input.value);
    D.exampleHint.classList.toggle('has-selection',!!selected);
    D.exampleHint.textContent=selected?selected.number+'번 질문을 입력했습니다. 수정한 뒤 ‘실행’을 눌러 주세요. 아직 조회하지 않았습니다.':
      '실행 버튼을 누르면 조회합니다. Enter는 줄바꿈입니다.';
    document.querySelectorAll('[data-example-id]').forEach(function(b){b.setAttribute('aria-pressed',String(!!selected&&b.dataset.exampleId===selected.id));});
    D.exampleNote.replaceChildren();D.exampleNote.hidden=!(selected&&selected.note);
    if(selected&&selected.note)D.exampleNote.append(exampleNotice(selected));
  }
  function selectExample(s){
    // 숫자/정답을 불러오거나 run()을 호출하지 않는다. 실제 실행은 form 제출에서만 한다.
    if(S.running){CV.toast('현재 실행이 끝나면 예시를 선택해 주세요.');return;}
    D.input.value=s.q;grow();refreshExampleHint();
    if(!D.input.disabled){D.input.focus({preventScroll:true});D.input.setSelectionRange(s.q.length,s.q.length);}
  }
  function filterExampleItems(groups,term,groupId){
    // 예시 목록만 검색한다. 질문 해석이나 자동 표 선택 규칙과는 무관하다.
    term=String(term||'').trim().toLocaleLowerCase().replace(/\s+/g,' ');
    return groups.flatMap(function(g,index){return g.items.map(function(s){return {groupId:String(index),groupTitle:g.title,example:s};});})
      .filter(function(row){return (groupId==='all'||row.groupId===groupId)&&
        (!term||[row.example.id,row.example.number,row.example.label,row.example.q,row.groupTitle].join(' ').toLocaleLowerCase().replace(/\s+/g,' ').includes(term));});
  }
  function mountExampleLibrary(){
    var library=el('section',{id:'exampleLibrary',class:'example-library','aria-label':'골든셋 예시 질문 22개'});
    library.hidden=true;
    var selectedGroup='all',grid=el('div',{id:'exampleGrid',class:'example-grid'}),cards=[],filters=[];
    var search=el('input',{id:'exampleSearch',type:'search',class:'example-search',placeholder:'예: 물가, 대출금, GDP, 04',
      'aria-label':'예시 질문 검색','aria-controls':'exampleGrid'});
    var count=el('p',{class:'example-count',role:'status','aria-live':'polite'});
    var noMatches=el('p',{class:'example-no-matches',text:'일치하는 예시가 없어요. 검색어나 유형을 바꿔 보세요.'});
    var filterBar=el('div',{class:'example-filters',role:'group','aria-label':'예시 질문 유형'});
    function filterButton(id,label,total){
      var b=el('button',{type:'button',class:'example-filter','data-example-group':id,'aria-pressed':String(id===selectedGroup),
        onclick:function(){selectedGroup=id;applyFilters();}},label,el('span',{class:'example-filter-count',text:String(total)}));
      filters.push(b);filterBar.append(b);
    }
    filterButton('all','전체',22);
    var shortGroups=['한 지표 추이','여러 지표 비교','구성 비교','규모·증감','변화 시점','수치표'];
    CV.EXAMPLE_GROUPS.forEach(function(g,index){
      filterButton(String(index),shortGroups[index]||g.title,g.items.length);
      g.items.forEach(function(s){
        var button=el('button',{type:'button',class:'example-item','data-example-id':s.id,'aria-pressed':'false',title:s.q,
          'aria-label':s.number+'번 '+s.label.replace(/^\d+\s*·\s*/, '')+'. 질문 입력: '+s.q,onclick:function(){selectExample(s);}});
        button.append(el('span',{class:'example-card-heading'},el('span',{class:'example-number',text:s.number}),
          el('strong',{text:s.label.replace(/^\d+\s*·\s*/, '')})),el('span',{class:'example-card-type',text:shortGroups[index]||g.title}));
        if(s.note)button.append(el('span',{class:'example-badge',text:s.note.badge}));
        grid.append(button);cards.push({id:s.id,node:button});
      });
    });
    var reset=el('button',{type:'button',class:'example-reset',text:'초기화',onclick:function(){search.value='';selectedGroup='all';applyFilters();search.focus();}});
    function applyFilters(){
      var matches=filterExampleItems(CV.EXAMPLE_GROUPS,search.value,selectedGroup),ids=new Set(matches.map(function(r){return r.example.id;}));
      cards.forEach(function(c){c.node.hidden=!ids.has(c.id);});
      filters.forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.exampleGroup===selectedGroup));});
      count.textContent='전체 22개 중 '+matches.length+'개';noMatches.hidden=matches.length>0;
      reset.disabled=!search.value&&selectedGroup==='all';
    }
    search.addEventListener('input',applyFilters);
    library.append(el('div',{class:'example-search-row'},el('label',{for:'exampleSearch',class:'example-search-label'},'예시 찾기',search),reset),
      filterBar,count,grid,noMatches);applyFilters();
    var toggle=el('button',{id:'examplesToggle',class:'example-action',type:'button','aria-expanded':'false','aria-controls':'exampleLibrary',text:'예시 질문 보기 · 22개'});
    toggle.addEventListener('click',function(){
      library.hidden=!library.hidden;toggle.setAttribute('aria-expanded',String(!library.hidden));
      toggle.textContent=library.hidden?'예시 질문 보기 · 22개':'예시 질문 접기 · 22개';
    });
    // 사용자 첫 화면에는 예시 선택만 둔다. 개발용 검수 기록은 /review22 주소에 보존한다.
    D.empty.append(el('div',{class:'example-actions'},toggle),
      el('p',{class:'example-help',text:'예시 선택은 입력만 합니다. 내용을 수정한 뒤 실행하세요.'}),library);
    if(CV.REVIEW_MODE){
      var savedView=CV.SAVED_VIEW||{title:'저장된 실행 기록',description:'왼쪽 이력에서 결과를 선택하세요. 새 API 호출은 없습니다.'};
      var banner=el('aside',{class:'review-intro',role:'note'},el('strong',{text:savedView.title}),
        el('span',{text:savedView.description}));
      D.empty.prepend(banner);
      if(CV.SAVED_VIEW){
        D.empty.querySelector('h1').textContent=savedView.title;
        D.empty.querySelector('p').textContent='왼쪽 이력에서 보려는 결과를 선택하세요. 비교 그래프로 바로 이동합니다.';
        toggle.hidden=true;
        D.empty.querySelector('.example-help').hidden=true;
      }
    }
    D.exampleHint=el('p',{id:'exampleInputHint',class:'example-input-hint',role:'status','aria-live':'polite'});
    D.exampleNote=el('div',{id:'exampleInputNote'});
    D.composer.querySelector('.box').after(D.exampleHint,D.exampleNote);
    D.input.setAttribute('aria-describedby','exampleInputHint exampleInputNote');
    D.input.addEventListener('input',refreshExampleHint);
    D.sendBtn.textContent='실행';D.sendBtn.setAttribute('aria-label','질문 실행');D.sendBtn.title='입력한 질문으로 새 조회 시작';
    // 동일한 form을 본문 밖 하단에 유지한다. 예시 목록을 스크롤해도 입력창은 보인다.
    CV.syncComposerPlacement=function(cv){
      var home=!CV.SAVED_VIEW&&!(cv&&cv.msgs.length);
      document.querySelector('.main').classList.toggle('home-mode',home);
      D.input.rows=1;grow();
    };
    CV.syncComposerPlacement(active());
    refreshExampleHint();
  }
  function confirmationEl(m) {
    var spec=m.confirmation, box=el('section',{class:'confirmation-box','aria-label':'후보 표 확인'});
    box.append(el('h3',{text:'어떤 통계표로 조회할까요?'}), el('p',{text:'아래는 데이터를 가져올 통계표 후보입니다. 표를 확인하면 항목과 기간을 정해 조회합니다. 같은 주기·단위의 여러 항목은 한 그래프에 함께 표시합니다.'}));
    var draft=m.confirmationDraft||(m.confirmationDraft={choices:{},interpretation_confirmed:false});
    var form=el('form'), controls=[];
    if(spec.conditions.length) box.append(el('p',{class:'stage-note',text:'질문에서 읽은 조건: '+spec.conditions.map(function(c){return c.quote;}).join(' · ')}));
    if(spec.requires_interpretation_ack) box.append(el('p',{class:'confirmation-warning',text:'질문 해석 확인이 필요합니다. 아래의 요구와 조건이 의도에 맞는지 확인해 주세요.'}));
    if(spec.recovery_note) box.append(el('p',{class:'confirmation-warning',text:CV.ux.text(spec.recovery_note)}));
    spec.groups.forEach(function(g,gi){
      var field=el('fieldset'); field.append(el('legend',{text:'확인할 내용 '+(gi+1)+': '+(g.quote||g.indicator)}));
      field.append(el('p',{class:'confirmation-reason',text:CV.ux.confirmationReason(spec,g)}));
      var grid=el('div',{class:'candidate-grid'});
      g.options.forEach(function(option){
        var label=el('label',{class:'candidate-option'}), description=el('div',{class:'candidate-description'});
        var radio=el('input',{type:'radio',name:'candidate-'+m.runId+'-'+g.requirement_id,value:option.option_id,
          'aria-label':option.tables.map(function(t){return t.table_name+' '+t.table_id;}).join(' + ')});
        radio.checked=draft.choices[g.requirement_id]===option.option_id; controls.push(radio);
        option.tables.forEach(function(t){
          description.append(el('strong',{text:t.table_name}));
          var facts=el('dl',{class:'candidate-facts'});
          function fact(k,v){facts.append(el('div',{},el('dt',{text:k}),el('dd',{text:v})));}
          fact('구분 기준',t.different_axes.length?t.different_axes.join(' · '):'차이 정보 미제공 · 표 이름과 수록 항목 참고');
          fact('주기',t.frequencies.map(function(f){return {Y:'연간',H:'반기',Q:'분기',M:'월별'}[f]||f;}).join(' · ')||'미제공');
          fact('수록 기간',t.coverage.map(function(c){return ({Y:'연간',H:'반기',Q:'분기',M:'월별'}[c.frequency]||c.frequency)+' '+CV.ux.period(c.start,c.frequency)+' ~ '+CV.ux.period(c.end,c.frequency);}).join(' / ')||'미제공');
          fact('단위',t.units.join(' · ')||'항목에 따라 다름');
          if(t.example_items.length)fact('수록 항목 예',t.example_items.join(' · '));description.append(facts);
        });
        label.append(radio,description); grid.append(label);
        radio.addEventListener('change',function(){draft.choices[g.requirement_id]=option.option_id; saveStore(); refresh();});
      });
      field.append(grid);
      field.append(el('details',{class:'technical-detail'},el('summary',{text:'표 코드·분류와 자동 선택 진단 보기'}),
        el('pre',{text:JSON.stringify({상태:g.a_status,진단:g.reasons,질문해석:spec.interpretation_reasons,
          후보:g.options.map(function(o){return {후보:o.option_id,표:o.tables.map(function(t){return {이름:t.table_name,표코드:t.table_id,분류:t.path,검색근거:t.search_note};})};})},null,2)})));
      form.append(field);
    });
    if(spec.requires_interpretation_ack){
      var ack=el('input',{type:'checkbox'}), ackLabel=el('label',{class:'interpretation-check'});
      ack.checked=!!draft.interpretation_confirmed; controls.push(ack);
      ackLabel.append(ack,el('span',{text:'위 질문 해석과 내가 선택한 표로 조회하는 데 동의합니다.'}));
      ack.addEventListener('change',function(){draft.interpretation_confirmed=ack.checked; saveStore(); refresh();}); form.append(ackLabel);
    }
    var button=el('button',{type:'submit',class:'continue-button',text:'선택한 표로 계속 실행'}), hint=el('p',{class:'stage-note'});
    function refresh(){
      var used=!!m.resumedJobId, busy=!!S.running;
      controls.forEach(function(c){c.disabled=used||busy;});
      var complete=spec.groups.every(function(g){return !!draft.choices[g.requirement_id];})&&(!spec.requires_interpretation_ack||draft.interpretation_confirmed);
      button.disabled=used||busy||!complete;
      hint.textContent=used?'선택을 전달했습니다. 아래 이어 실행 결과를 확인해 주세요.':busy?'현재 실행이 끝나면 선택할 수 있습니다.':
        complete?'버튼을 누르면 선택한 표로 실제 조회를 시작합니다.':'각 요구의 후보를 선택해 주세요.'+(spec.requires_interpretation_ack?' 질문 해석 확인도 필요합니다.':'');
    }
    form.addEventListener('submit',function(e){
      e.preventDefault(); if(button.disabled)return;
      resumeRun(m,{choices:spec.groups.map(function(g){return {requirement_id:g.requirement_id,option_id:draft.choices[g.requirement_id]};}),interpretation_confirmed:!!draft.interpretation_confirmed});
    });
    form.append(button,hint); box.append(form); refresh(); return box;
  }
  function fillAi(body,m,cv){
    body.innerHTML='';
    if(isRecoverable(m)||m.recoveryState){body.append(recoveryEl(m,cv));}
    if(isPreviousStop(m)){body.append(previousStopEl(m));return;}
    var complete=hasComparisonResult(m);
    var questionExample=exampleForQuery(m.inputText);
    if(questionExample&&questionExample.note)body.append(exampleNotice(questionExample));
    if(questionExample&&questionExample.note&&m.kind==='result'&&m.cases&&!m.cases.some(function(c){return c.analysisVersion;}))body.append(el('p',{class:'stage-note',text:'이 이력은 C 보완 전 저장 결과입니다. 최신 계산·누적 화면은 22건 검수 결과에서 확인하거나 새로 실행해 주세요.'}));
    if(m.parentRunId) body.append(el('p',{class:'stage-note',text:m.resumeKind==='b_conditions'?(m.kind==='result'?'수정한 조회 조건으로 이어 실행한 결과입니다. 원래 질문과 이전 기록은 위에 남아 있습니다.':'수정한 조회 조건을 전달했습니다. 아래에서 진행 상태를 확인하세요.'):'선택한 표로 이어 실행합니다. 아래에서 조회 조건·데이터 조회·그래프 결과를 확인하세요.'}));
    else if(m.resumedJobId) body.append(el('p',{class:'stage-note',text:'이 확인에서 선택한 표를 전달했습니다. 아래에 이어 실행 결과가 있습니다.'}));
    var progress=CV.ux.simplifyProgress(stepsEl(m)); progress.querySelector('details.steps').open=!complete&&!m.resumedJobId;
    if(complete){
      progress.classList.add('completed-progress');
      var count=m.cases.reduce(function(n,c){return n+c.series.length;},0);
      var resultLabel=m.savedRecord?'저장 자료':'조회 완료';
      body.append(el('p',{class:'comparison-ready',role:'status',text:m.cases.length===1?
        resultLabel+' · '+count+'개 항목의 결과를 아래에서 확인하세요.':
        resultLabel+' · '+count+'개 항목을 주기·단위에 따라 '+m.cases.length+'개 결과로 나누었습니다.'}));
      if(m.savedRecord)body.append(el('p',{class:'stage-note',text:CV.SAVED_VIEW?CV.SAVED_VIEW.description:'저장된 자료입니다. 지금 새 API를 호출하지 않습니다.'}));
    }
    if(m.resumedJobId){
      var previous=el('details',{class:'resolved-checkpoint'},el('summary',{text:'확인을 마친 이전 단계 보기'}));
      previous.append(progress);body.append(previous);
      if(m.confirmation)previous.append(confirmationEl(m));
      if(m.conditionEditor)previous.append(conditionEditorEl(m));
      if(m.answer)previous.append(el('p',{class:'stage-note',text:CV.ux.text(m.answer)}));
      if(m.finishedAt)previous.append(el('p',{class:'stage-note',text:'이전 단계: '+new Date(m.finishedAt).toLocaleString('ko-KR')}));
      return;
    }else body.append(progress);
    if(complete){
      m.cardStates=m.cardStates||{};
      m.cases.forEach(function(c){
        var r=CV.createResult(c,m.cardStates[c.id],{onChange:function(s){m.cardStates[c.id]=s; saveStore();}});
        results.push(r); var slot=el('div',{class:'result'}); body.append(slot); CV.layout.renderResult(slot,r,m,{conv:cv,fresh:false});
      });
    }
    if(m.confirmation&&!m.resumedJobId)body.append(confirmationEl(m));
    if(m.conditionEditor&&!m.resumedJobId)body.append(conditionEditorEl(m));
    if(m.answer){
      var answer=el('div',{class:'answer',role:'status'});
      if(m.savedRecord&&!complete)answer.append(el('p',{class:'stage-note',text:'당시 실행의 안내입니다. 이 저장 화면에서는 조건 수정이나 재실행을 하지 않습니다. 새 질문은 실제 실행 화면에서 진행하세요.'}));
      answer.append(el('p',{text:CV.ux.text(m.answer)}));body.append(answer);
    }
    if(m.callCounts||m.jobId||CV.ux.text(m.answer)!==(m.answer||''))body.append(el('details',{class:'technical-detail'},el('summary',{text:'실행 기록 자세히 보기'}),
      el('pre',{text:JSON.stringify({실행번호:m.runId||m.jobId,호출횟수:m.callCounts||'이전 기록에는 별도 집계 없음',원래안내:m.answer},null,2)})));
    if(m.finishedAt)body.append(el('p',{class:'stage-note',text:'실행: '+new Date(m.finishedAt).toLocaleString('ko-KR')+' · 이력을 열어도 API를 다시 호출하지 않습니다.'}));
  }
  async function readJson(url,options){
    var response;
    try{response=await fetch(url,options);}catch(_){var e=new Error('실행 서버에 연결하지 못했습니다. 서버 실행 여부(docker compose ps)와 화면 주소(기본 http://localhost:10300/)를 확인해 주세요.');e.network=true;throw e;}
    if(!(response.headers.get('Content-Type')||'').includes('application/json'))throw new Error('이 화면은 실행 서버에서 열어야 합니다. 기본 주소 http://localhost:10300/ 에서 확인해 주세요.');
    var data=await response.json(); if(!response.ok){var error=new Error(data.error||'실행 요청을 처리하지 못했습니다.');error.status=response.status;throw error;} return data;
  }
  function newMessage(text){return {role:'ai',kind:'run',inputText:text,mode:'live',flowVersion:comparisonFlowVersion,execution:{mode:'live',stages:stageIds.map(function(id){
    return {id:id,status:'pending',summary:'서버 실행을 기다립니다.'};})},answer:'실행을 준비하고 있습니다.'};}
  async function resumeRun(original,confirmation){
    if(S.running||original.resumedJobId)return;
    var cv=active(),m=newMessage(original.inputText); m.parentRunId=original.runId; cv.msgs.push(m); cv.ts=Date.now();
    await executeMessage(m,cv,'/api/resume',{job_id:original.jobId,confirmation:confirmation},original);
  }
  async function run(text){
    if(S.running)return;
    if(location.protocol==='file:'){CV.toast('실행 서버 주소(기본 http://localhost:10300/)에서 열어 주세요.');return;}
    if(text.length>2000||/\bnv-[a-z0-9]{16,}|\bBearer\s+|api[_ ]?key\s*[=:]|[a-z0-9+/]{32,}={0,2}/i.test(text)){
      CV.toast('질문은 2,000자 이내로 입력하고, 인증키는 붙여 넣지 마세요.');return;
    }
    var cv=active();if(!cv){cv={id:uid(),title:text.slice(0,30),ts:Date.now(),msgs:[]};S.convs.push(cv);S.active=cv.id;}
    cv.ts=Date.now();var m=newMessage(text);cv.msgs.push({role:'user',text:text},m);await executeMessage(m,cv,'/api/query',{query:text});
  }
  function isRecoverable(m){
    return !!((m.recoveryState||m.loadedFromHistory||m.kind==='stopped')&&m.jobId&&!m.savedRecord&&!m.finishedAt&&!m.confirmation&&!m.conditionEditor&&!m.resumedJobId&&
      !m.jobSettled&&m.recoveryState!=='expired');
  }
  function recoveryEl(m,cv){
    var expired=m.recoveryState==='expired',box=el('aside',{class:'recovery-box',role:'status'});
    box.append(el('strong',{text:expired?'이전 실행 상태를 더 이상 확인할 수 없습니다.':'이전 실행의 진행 상태를 확인할 수 있습니다.'}),
      el('p',{text:expired?'서버가 다시 시작됐거나 보관된 작업이 없어졌습니다. 새 조회를 자동으로 시작하지 않았습니다. 질문을 다시 입력해 직접 실행할 수 있습니다.':
        '화면 연결이 끊겨도 서버 작업은 계속될 수 있습니다. 아래 버튼은 기존 작업만 확인하며 질문을 다시 제출하거나 API를 중복 호출하지 않습니다.'}));
    if(m.recoveryNote)box.append(el('p',{text:m.recoveryNote}));
    var check=el('button',{type:'button',class:'example-action',text:'진행 상태 다시 확인',disabled:expired||!!S.running,onclick:function(){recoverMessage(m,cv);}});
    box.append(check);return box;
  }
  function updateLiveProgress(m){
    var last=D.thread.lastElementChild;if(!last||!last._body)return;
    var body=last._body,old=body.querySelector('.progress-area');if(!old)return;
    var opened=Array.from(old.querySelectorAll('details')).map(function(d){return d.open;}),fresh=CV.ux.simplifyProgress(stepsEl(m));
    Array.from(fresh.querySelectorAll('details')).forEach(function(d,i){d.open=!!opened[i];});old.replaceWith(fresh);
    var answer=body.querySelector('.answer p');if(answer)answer.textContent=CV.ux.text(m.answer);
  }
  function receiveResult(m,result){
    m.execution.stages=result.stages;m.cases=result.ui_cases||[];m.kind=m.cases.length?'result':'stopped';
    m.finishedAt=result.finished_at;m.runId=result.run_id;m.confirmation=result.confirmation;m.userConfirmation=result.user_confirmation;
    m.conditionEditor=result.condition_editor;m.resumeKind=result.resume_kind||m.resumeKind;
    m.answer=result.message;m.callCounts=result.calls;m.jobSettled=true;
    delete m.recoveryState;delete m.recoveryNote;saveStore();
  }
  async function pollExistingJob(m,info,first){
    var deadline=Date.now()+15*60*1000;
    while(Date.now()<deadline){
      var job=first||await readJson('/api/jobs/'+m.jobId,{headers:{'X-Claview-Token':info.token}});first=null;
      (job.events||[]).forEach(function(event){if(event.type==='stage'){var idx=stageIds.indexOf(event.stage.id);if(idx>=0)m.execution.stages[idx]=event.stage;}});
      var running=m.execution.stages.find(function(s){return s.status==='running';});m.answer=running?running.summary:'다음 단계의 결과를 기다립니다.';updateLiveProgress(m);
      if(job.done){
        if(!job.result){m.jobSettled=true;throw new Error(job.error||'완료 결과를 받지 못했습니다.');}
        receiveResult(m,job.result);return;
      }
      await new Promise(function(resolve){setTimeout(resolve,800);});
    }
    throw new Error('진행 상태 확인 시간이 지났습니다. 서버 작업을 취소한 것은 아닙니다. 새로 실행하기 전에 기존 작업 상태를 확인해 주세요.');
  }
  function connectionFailure(m,error){
    m.kind='stopped';m.answer=error.message||'연결 상태를 확인하지 못했습니다.';
    if(m.jobId&&!m.jobSettled){
      m.recoveryState=(error.status===404||error.expired)?'expired':'disconnected';
      m.recoveryNote=m.recoveryState==='expired'?'남아 있는 완료 그래프나 다른 이력은 그대로 보존됩니다.':m.answer;
    }else if(m.submissionUncertain){
      m.answer+=' 요청이 서버에 전달됐는지 확인되지 않아 자동으로 재제출하지 않았습니다. 서버 상태를 확인한 뒤 새 실행 여부를 결정해 주세요.';
    }
    m.execution.stages.forEach(function(s){if(s.status==='running'||s.status==='pending'){s.status='unavailable';s.summary='완료 여부를 확인할 수 없습니다.';}});saveStore();
  }
  async function recoverMessage(m,cv){
    // 복구 경로에는 POST가 없다. 작업 번호·서버 세대가 일치하는 기존 작업만 읽는다.
    if(S.running||!isRecoverable(m))return;
    S.running=m;D.sendBtn.disabled=true;m.recoveryNote='기존 작업의 상태를 확인하고 있습니다.';renderThread();renderHist();
    try{
      var info=await readJson('/api/info'),instance=String(info.server_pid||'');
      if(m.serverInstance&&instance!==m.serverInstance){var expired=new Error('서버가 다시 시작되어 이전 작업을 이어 확인할 수 없습니다.');expired.expired=true;throw expired;}
      var job=await readJson('/api/jobs/'+m.jobId,{headers:{'X-Claview-Token':info.token}});
      if(job.resumed_job_id){
        var source=m,child=cv.msgs.find(function(x){return x.jobId===job.resumed_job_id;});
        if(!child){child=newMessage(source.inputText);child.jobId=job.resumed_job_id;child.serverInstance=instance;child.parentRunId=source.runId;cv.msgs.push(child);}
        source.resumedJobId=child.jobId;delete source.recoveryState;delete source.recoveryNote;m=child;S.running=m;job=null;
      }
      m.serverInstance=instance;m.kind='run';m.loadedFromHistory=false;m.recoveryState='checking';saveStore();renderThread();
      await pollExistingJob(m,info,job);
    }catch(error){connectionFailure(m,error);}
    finally{S.running=null;D.sendBtn.disabled=location.protocol==='file:';saveStore();renderThread();renderHist();}
  }
  var connectionUi=null,checkingConnection=false;
  async function checkConnection(recover){
    if(checkingConnection||!connectionUi)return;
    checkingConnection=true;connectionUi.button.disabled=true;
    try{
      var info=await readJson('/api/info');connectionUi.root.dataset.state='online';
      connectionUi.status.textContent=(!info.hcx_ready||!info.kosis_ready)?'서버 연결됨 · 조회에 필요한 인증 설정을 확인해 주세요.':info.busy?'서버 연결됨 · 조회 처리 중':'서버 연결됨 · 질문할 수 있습니다.';
      if(recover&&!S.running){var cv=active(),pending=cv&&cv.msgs.slice().reverse().find(isRecoverable);if(pending)await recoverMessage(pending,cv);}
    }catch(_){connectionUi.root.dataset.state='offline';connectionUi.status.textContent='서버 연결 끊김 · 기존 결과는 유지됩니다. 서버 실행 후 연결 확인을 눌러 주세요.';}
    finally{checkingConnection=false;connectionUi.button.disabled=false;}
  }
  function mountConnection(){
    var root=el('div',{class:'connection-bar','data-state':'checking'}),status=el('span',{role:'status','aria-live':'polite',text:'서버 연결 확인 중'}),button=el('button',{type:'button',class:'example-action',text:'연결 확인',onclick:function(){checkConnection(true);}});
    root.append(el('span',{class:'connection-dot','aria-hidden':'true'}),status,button);D.scroller.before(root);connectionUi={root:root,status:status,button:button};
    checkConnection(true);setInterval(function(){if(!document.hidden)checkConnection(false);},30000);
  }
  async function executeMessage(m,cv,path,payload,source){
    S.running=m;D.sendBtn.disabled=true;saveStore();renderThread();renderHist();closeSide();
    if(source)requestAnimationFrame(function(){scrollToMsg(D.thread.lastElementChild);});
    try{
      var info=await readJson('/api/info');
      if(path==='/api/resume'&&!info.confirmation_resume)throw new Error('서버가 이전 버전입니다. 최신 서버로 다시 시작해 주세요.');
      if(path==='/api/conditions'&&!info.b_condition_resume)throw new Error('조회 조건 수정 기능을 반영한 서버를 다시 실행해 주세요.');
      m.serverInstance=String(info.server_pid||'');
      var headers={'Content-Type':'application/json','X-Claview-Token':info.token};
      // 응답이 유실되면 자동 재제출하지 않는다. 중복 유료 호출을 방지한다.
      m.submissionUncertain=true;
      var receipt=await readJson(path,{method:'POST',headers:headers,body:JSON.stringify(payload)});m.jobId=receipt.job_id;m.submissionUncertain=false;
      if(source){
        source.resumedJobId=m.jobId;saveStore();renderThread();
        requestAnimationFrame(function(){scrollToMsg(D.thread.lastElementChild);});
      }
      await pollExistingJob(m,info);
    }catch(error){
      if(error.status)m.submissionUncertain=false;
      connectionFailure(m,error);
      if(source&&path==='/api/conditions'&&!source.resumedJobId)source.conditionError=m.answer;
    }finally{
      S.running=null;D.sendBtn.disabled=location.protocol==='file:';renderThread();renderHist();
      if(source)requestAnimationFrame(function(){
        if(path==='/api/conditions'&&source.conditionError&&!source.resumedJobId){
          var editors=D.thread.querySelectorAll('.b-editor');
          if(editors.length){editors[editors.length-1].scrollIntoView({block:'start'});return;}
        }
        scrollToCurrentOutcome(cv);
      });
    }
  }
  // 파일 미리보기에서는 실행 주소를 안내한다. 잘못된 file:// API 요청을 보내지 않는다.
  var originalAppInit=CV.app.init;
  CV.app.init=function(){
    originalAppInit();
    mountExampleLibrary();
    if(CV.SAVED_VIEW){
      var savedHint=el('aside',{class:'file-server-hint',role:'note'});
      savedHint.append(el('strong',{text:'저장 자료 보기 · 이 화면에서는 새 조회를 실행하지 않습니다.'}),el('a',{href:'/',text:'새 질문 실행 화면으로 이동'}));
      D.composer.before(savedHint);D.input.disabled=true;D.sendBtn.disabled=true;
      D.exampleHint.textContent='저장된 결과를 보는 화면입니다. 새 질문은 위의 실제 실행 화면에서 입력해 주세요.';
      D.input.placeholder='새 질문은 실제 실행 화면에서 입력해 주세요.';
      return;
    }
    if(location.protocol!=='file:'){mountConnection();return;}
    var serverHint=el('aside',{class:'file-server-hint',role:'alert'});
    serverHint.append(el('strong',{text:'질문 실행은 서버 주소에서 가능합니다.'}),el('a',{href:'/',text:'질문 실행 화면 열기'}));
    D.composer.before(serverHint);D.input.disabled=true;D.sendBtn.disabled=true;
  };
