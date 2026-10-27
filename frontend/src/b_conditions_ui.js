  // B에서 멈춘 실행에만 표시한다. 공식 메타에서 선택하고 확인 버튼을 눌러야 조회한다.
  function conditionEditorEl(m){
    var spec=m.conditionEditor,box=el('section',{class:'confirmation-box b-editor','aria-label':'B 조회 조건 수정'});
    box.append(el('h3',{text:'조회 조건을 수정하고 이어서 실행'}),el('p',{text:CV.ux.text(spec.reason)}),
      el('p',{class:'stage-note',text:'선택한 통계표는 유지합니다. 원래 질문과 다른 기간·항목을 선택하면 아래 수정 조건을 우선합니다. 확인 버튼을 누르기 전에는 조회하지 않습니다.'}));
    if(m.conditionError)box.append(el('p',{class:'condition-error',role:'alert',text:m.conditionError}));
    var draft=m.conditionDraft||(m.conditionDraft={acknowledged:false,tables:spec.tables.map(function(t){
      return {table_key:t.table_key,period:Object.assign({frequency:'',start:'',end:''},t.draft.period||{}),
        selections:t.draft.selections.length?JSON.parse(JSON.stringify(t.draft.selections)):[{item_id:'',dimensions:{}}]};
    })});
    var form=el('form'),controls=[], rowLists=[], summaries=[];
    function changed(){draft.acknowledged=false;ack.checked=false;m.conditionError='';saveStore();refresh();}
    function field(label,control){var lab=el('label',{class:'condition-field'});lab.append(el('span',{text:label}),control);return lab;}
    function choose(label,values,selected,setter){
      var select=el('select',{'aria-label':label}),wrap=el('div',{class:'condition-choice'});
      function fill(term){
        select.textContent='';select.append(el('option',{value:'',text:'선택해 주세요'}));
        values.filter(function(v){return v.code===selected||!term||(v.label+' '+v.code).toLowerCase().includes(term.toLowerCase());})
          .forEach(function(v){select.append(el('option',{value:v.code,text:v.label+' ['+v.code+']'}));});
        select.value=selected;
      }
      if(!selected&&values.length===1){selected=values[0].code;setter(selected);}
      fill('');controls.push(select);
      if(values.length>12){var search=el('input',{type:'search',placeholder:'이름·상위 분류·코드로 찾기','aria-label':label+' 검색'});controls.push(search);search.addEventListener('input',function(){fill(search.value);});wrap.append(search);}
      select.addEventListener('change',function(){selected=select.value;setter(selected);changed();});
      wrap.append(select);return field(label,wrap);
    }
    spec.tables.forEach(function(t,i){
      var d=draft.tables[i],group=el('fieldset'),rows=el('div',{class:'condition-rows'});
      group.append(el('legend',{text:t.table_name}),el('details',{class:'technical-detail'},el('summary',{text:'통계표 코드 보기'}),el('span',{text:t.table_key})));
      if(t.draft_notice)group.append(el('p',{class:'confirmation-warning',text:t.draft_notice}));
      var freq=el('select',{'aria-label':t.table_name+' 주기'}),start=el('input',{type:'text',inputmode:'numeric','aria-label':t.table_name+' 시작 기간'}),end=el('input',{type:'text',inputmode:'numeric','aria-label':t.table_name+' 종료 기간'});
      freq.append(el('option',{value:'',text:'주기를 선택하세요'}));
      Object.keys(t.periods).forEach(function(f){freq.append(el('option',{value:f,text:{Y:'연간',H:'반기',Q:'분기',M:'월별'}[f]}));});
      freq.value=d.period.frequency;start.value=d.period.start;end.value=d.period.end;controls.push(freq,start,end);
      var bounds=el('p',{class:'stage-note'}),format=el('p',{class:'stage-note'});
      function periodInfo(){
        var f=d.period.frequency,b=t.periods[f];bounds.textContent=b?'저장 메타 수록 범위: '+b.start+' ~ '+b.end:'선택한 주기의 수록 범위를 안내합니다.';
        var example={Y:'2024',M:'202401 (2024년 1월)',Q:'202401 (2024년 1분기)',H:'202401 (2024년 상반기)'}[f]||'주기를 먼저 선택하세요';
        format.textContent='입력 예: '+example+' · 표시할 기간을 입력하세요. 전월·전년 비교용 이전 기간은 필요한 경우 서버가 함께 조회합니다.';
        start.placeholder=f==='Y'?'YYYY':'YYYY01';end.placeholder=start.placeholder;
      }
      freq.addEventListener('change',function(){d.period={frequency:freq.value,start:'',end:''};start.value=end.value='';periodInfo();changed();});
      start.addEventListener('input',function(){d.period.start=start.value.trim();changed();});
      end.addEventListener('input',function(){d.period.end=end.value.trim();changed();});
      group.append(el('div',{class:'condition-period'},field('주기',freq),field('시작 기간',start),field('종료 기간',end)),bounds,format);periodInfo();
      function renderRows(){
        rows.textContent='';d.selections.forEach(function(r,j){
          var row=el('div',{class:'condition-row'});row.append(el('strong',{text:'조회 항목 '+(j+1)}));
          row.append(choose(t.table_name+' 항목 '+(j+1),Object.keys(t.items).map(function(code){return {code:code,label:t.items[code].name+(t.items[code].unit?' · '+t.items[code].unit:'')};}),r.item_id,function(v){r.item_id=v;}));
          Object.keys(t.dimensions).forEach(function(axis){var dim=t.dimensions[axis];row.append(choose(dim.name+' '+(j+1),dim.values,r.dimensions[axis]||'',function(v){r.dimensions[axis]=v;}));});
          var remove=el('button',{type:'button',class:'example-action',text:'이 항목 삭제','aria-label':t.table_name+' 항목 '+(j+1)+' 삭제'});controls.push(remove);
          remove.addEventListener('click',function(){d.selections.splice(j,1);renderRows();changed();});row.append(remove);rows.append(row);
        });
      }
      renderRows();rowLists.push(rows);
      var add=el('button',{type:'button',class:'example-action',text:'조회 항목 추가'});controls.push(add);
      add.addEventListener('click',function(){d.selections.push({item_id:'',dimensions:{}});renderRows();changed();});
      var clear=el('button',{type:'button',class:'example-action',text:'선택 비우기'});controls.push(clear);
      clear.addEventListener('click',function(){d.selections=[{item_id:'',dimensions:{}}];renderRows();changed();});
      var summary=el('p',{class:'stage-note'});summaries.push(summary);group.append(rows,el('div',{class:'example-actions'},add,clear),summary);form.append(group);
    });
    var ack=el('input',{type:'checkbox','aria-label':'수정한 기간과 항목 확인'});ack.checked=draft.acknowledged;controls.push(ack);
    ack.addEventListener('change',function(){draft.acknowledged=ack.checked;saveStore();refresh();});
    form.append(el('label',{class:'interpretation-check'},ack,el('span',{text:'수정한 기간·항목을 확인했습니다. 원질문보다 이 조건을 우선하여 조회합니다.'})));
    var button=el('button',{type:'submit',class:'continue-button',text:'수정한 조건으로 조회·그래프 이어서 실행'}),hint=el('p',{class:'stage-note',role:'status'});
    function refresh(){
      var locked=!!m.resumedJobId||!!S.running,total=0,complete=true;
      draft.tables.forEach(function(t,i){
        total+=t.selections.length;
        var p=t.period,meta=spec.tables[i];
        complete=complete&&!!meta.periods[p.frequency]&&!!p.start&&!!p.end&&t.selections.length>0&&t.selections.every(function(r){return !!r.item_id&&Object.keys(meta.dimensions).every(function(a){return !!r.dimensions[a];});});
        summaries[i].textContent='수정 조건: '+(p.start||'미정')+' ~ '+(p.end||'미정')+' · 항목 '+t.selections.length+'개';
      });
      controls.forEach(function(c){c.disabled=locked;});
      button.disabled=locked||!complete||total>spec.max_selections||!draft.acknowledged;
      hint.textContent=m.resumedJobId?'조건을 전달했습니다. 아래 이어 실행 결과를 확인하세요.':S.running?'실행 중입니다. 중복 조회하지 않습니다.':total>spec.max_selections?'현재 '+total+'개입니다. 30개 이내로 줄여 주세요.':!complete?'모든 표의 기간과 항목·분류를 선택해 주세요.':'총 '+total+'개 항목. 버튼을 누르면 조건을 검사한 뒤 조회합니다. 질문·표 분석은 반복하지 않습니다.';
    }
    form.addEventListener('submit',function(e){e.preventDefault();if(button.disabled)return;resumeConditions(m,JSON.parse(JSON.stringify(draft)));});
    form.append(button,hint);box.append(form);refresh();return box;
  }
  async function resumeConditions(original,conditions){
    if(S.running||original.resumedJobId)return;
    var cv=active(),m=newMessage(original.inputText);m.parentRunId=original.runId;m.resumeKind='b_conditions';cv.msgs.push(m);cv.ts=Date.now();
    await executeMessage(m,cv,'/api/conditions',{job_id:original.jobId,conditions:conditions},original);
  }
