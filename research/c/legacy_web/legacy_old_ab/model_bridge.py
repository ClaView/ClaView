"""Keep a multi-table B call focused on its confirmed requirement; deduplicate identical rows."""
import copy
import json


def table_requirements(analysis,selected,confirmation=None):
    reqs={r['id']:r.get('quote','') for r in analysis.get('interpretation',{}).get('requirements',[])}
    records=(confirmation or {}).get('choices') or selected.get('selections',[])
    result={}
    for choice in records:
        quote=reqs.get(choice['requirement_id'],'')
        for table in choice.get('tables',[]):
            key=table['org_id']+':'+table['table_id']
            if quote and quote not in result.setdefault(key,[]):result[key].append(quote)
    return result


class ScopedModel:
    def __init__(self,model,requirements=None,conditions=None):
        self.model=model;self.requirements=requirements or {};self.conditions=conditions or []

    def complete(self,task,system,context,schema,max_tokens,trace):
        key=task.removeprefix('B:');quotes=self.requirements.get(key,[])
        if len(self.requirements)>1 and quotes:
            context=copy.deepcopy(context)
            # Periods are still fixed by the original shared B code from the full query.
            context['query']=' / '.join(quotes)
            context['common_conditions']=self.conditions
            context['selected_tables']=[t for t in context['selected_tables'] if t['org_id']+':'+t['table_id']==key]
            system+='\n여러 표 중 현재 표가 담당하는 요구만 선택합니다. 다른 표가 담당할 지표를 이 표의 비슷한 항목으로 대신 고르지 않습니다. 공통 기간·주기 조건은 common_conditions를 따릅니다.'
            trace.add('model_requirement_scoped',table=key,query=context['query'],conditions=self.conditions)
        output=self.model.complete(task,system,context,schema,max_tokens,trace)
        rows=output.get('selections') if isinstance(output,dict) else None
        if not isinstance(rows,list):return output
        seen=set();unique=[]
        for row in rows:
            key=json.dumps(row,sort_keys=True,ensure_ascii=False)
            if key not in seen:seen.add(key);unique.append(row)
        if len(unique)!=len(rows):
            trace.add('identical_model_rows_removed',before=copy.deepcopy(output),removed=len(rows)-len(unique))
            output={**output,'selections':unique}
        return output
