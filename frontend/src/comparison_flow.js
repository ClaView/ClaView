  // 화면 흐름만 정리한다. 검색·조건 검증·실제 API 호출 계약은 바꾸지 않는다.
  var comparisonFlowVersion = 'comparison-flow-20261021-v2';
  function hasComparisonResult(m){
    return m.kind==='result'&&Array.isArray(m.cases)&&m.cases.length>0;
  }
  function isPreviousStop(m){
    return m.role==='ai'&&m.kind==='stopped'&&!m.savedRecord&&!m.resumedJobId&&!m.confirmation&&!m.conditionEditor&&(!m.jobId||m.finishedAt||m.jobSettled)&&
      (!m.flowVersion||m.loadedFromHistory===true);
  }
  function historyBadge(cv){
    var messages=cv.msgs.filter(function(m){return m.role==='ai';}),last=messages[messages.length-1];
    if(!last)return null;
    if(last.recoveryState==='expired')return el('span',{class:'history-badge previous',text:'상태 확인 불가'});
    if(last.jobId&&!last.finishedAt&&!last.confirmation&&!last.conditionEditor&&!last.resumedJobId&&!last.savedRecord&&!last.jobSettled)return el('span',{class:'history-badge waiting',text:'진행 상태 확인'});
    if(isPreviousStop(last))return el('span',{class:'history-badge previous',text:'이전 중단 기록'});
    if(hasComparisonResult(last))return el('span',{class:'history-badge complete',text:last.mode==='sample'?
      (last.cases.some(function(c){return c.synthetic;})?'합성·결측 시험':'저장 샘플'):'조회 완료'});
    if(last.confirmation||last.conditionEditor)return el('span',{class:'history-badge waiting',text:'확인 대기'});
    if(last.kind==='run')return el('span',{class:'history-badge waiting',text:'실행 중'});
    if(last.kind==='stopped')return el('span',{class:'history-badge previous',text:'중단 기록'});
    return null;
  }
  function putQuestionBack(m){
    if(S.running){CV.toast('현재 실행이 끝난 뒤 질문을 입력해 주세요.');return;}
    D.input.value=m.inputText||'';grow();refreshExampleHint();
    D.input.focus({preventScroll:true});
    CV.toast('질문만 입력했습니다. 실행을 눌러야 새 조회가 시작됩니다.');
  }
  function previousStopEl(m){
    var box=el('aside',{class:'previous-stop',role:'note','data-previous-stop':'true'});
    box.append(el('strong',{text:'이전 실행의 중단 기록입니다.'}),
      el('p',{text:'이 기록에는 완료된 비교 그래프가 없습니다. 지금 새로 실행한 결과가 아니며, 이력을 열어도 다시 조회하지 않습니다.'}));
    if(!m.finishedAt)box.append(el('p',{text:'창을 닫을 당시 서버 작업이 끝났는지는 확인되지 않았습니다. 중복 실행 전에 현재 상태를 확인해 주세요.'}));
    if(m.inputText)box.append(el('button',{type:'button',class:'example-action',text:'이 질문 다시 입력',onclick:function(){putQuestionBack(m);}}));
    var details=el('details',{class:'previous-stop-detail'},el('summary',{text:'당시 중단 내용 확인'}));
    var progress=stepsEl(m);progress.querySelector('details.steps').open=false;
    details.append(progress);
    if(m.answer)details.append(el('p',{text:m.answer}));
    if(m.finishedAt)details.append(el('p',{class:'stage-note',text:'당시 실행: '+new Date(m.finishedAt).toLocaleString('ko-KR')}));
    box.append(details);return box;
  }
  function scrollToCurrentOutcome(cv){
    if(!cv||!D.thread.lastElementChild){scrollEnd(true);return;}
    // 조회가 끝나면 긴 처리 로그 대신 마지막 답변의 비교 결과로 이동한다.
    var last=D.thread.lastElementChild;
    var target=last.querySelector('.result')||last.querySelector('.confirmation-box,.b-editor,.previous-stop')||last;
    scrollToMsg(target);
  }
