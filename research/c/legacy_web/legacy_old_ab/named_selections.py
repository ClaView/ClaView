"""Complete explicit, unambiguous same-axis lists from official metadata.

The model chooses the table/item/other axes. Only an axis listing 2+ unique exact
names can be completed; exclusions, duplicate names and several varying axes
are not guessed. Rebuilt rows pass the existing B validator before any fetch.
"""
import copy
import re
import unicodedata
from collections import defaultdict

from claview.contracts import Config,StageFailure
from claview.stages import canonicalize_selections,find_relations


def clean(value):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',value))


def exact_named_codes(query,values):
    text=clean(query);by_name=defaultdict(list)
    for code,value in values.items():
        name=clean(re.sub(r'\s*\d+\)\s*$','',value['name']))
        if len(name)>=2 and not name.endswith('기준'):by_name[name].append(code)
    spans=[]
    for name,codes in by_name.items():
        for match in re.finditer(re.escape(name),text):
            spans.append((match.start(),match.end(),codes))
    # A specifically named child is not also a request for its parent substring.
    spans=[s for s in spans if not any(t[0]<=s[0] and t[1]>=s[1] and t[1]-t[0]>s[1]-s[0] for t in spans)]
    if any(len(s[2])!=1 for s in spans):return []
    return list(dict.fromkeys(s[2][0] for s in sorted(spans)))


def complete_named_selections(query,b_result,store):
    result=copy.deepcopy(b_result)
    if store is None or re.search('제외|뺀|빼고|아닌|말고',query):return result,[]
    grouped=defaultdict(list)
    for row in result['selections']:grouped[(row['org_id'],row['table_id'])].append(row)
    all_rows=[];notices=[]
    for key,rows in grouped.items():
        table=store.get(*key)
        named={a:exact_named_codes(query,d['values']) for a,d in table['dimensions'].items()}
        axes=[a for a,codes in named.items() if len(codes)>=2]
        if len(axes)!=1:
            all_rows.extend(rows);continue
        axis=axes[0];values=table['dimensions'][axis]['values']
        # A category heading (e.g. 기업대출) preceding two named children is context,
        # not an additional series to inject into a child comparison.
        targets=[c for c in named[axis] if sum(c in [p['code'] for p in values[t]['path'][:-1]] for t in named[axis] if t!=c)<2]
        chosen={r['dimensions'][axis]['code'] for r in rows}
        if set(targets).issubset(chosen):all_rows.extend(rows);continue
        fixed={(r['item_id'],tuple((a,v['code']) for a,v in sorted(r['dimensions'].items()) if a!=axis),
                tuple(sorted(r['period'].items()))) for r in rows}
        if len(fixed)!=1 or not all(c in targets or any(c in [p['code'] for p in values[t]['path'][:-1]] for t in targets) for c in chosen):
            raise StageFailure('named_conditions_require_confirmation','needs_confirmation')
        base=rows[0]
        raw={'selections':[{'item_id':base['item_id'],'period':base['period'],
                           'dimensions':{a:(t if a==axis else v['code']) for a,v in base['dimensions'].items()}} for t in targets]}
        canonical=canonicalize_selections(raw,table,Config())
        all_rows.extend(canonical)
        notices.append({'table':':'.join(key),'axis':axis,'before':sorted(chosen),'after':targets,
                        'message':'질문에 명시한 '+', '.join(values[t]['name'] for t in targets)+'을 공식 코드로 모두 선택했습니다.'})
    if not notices:return result,[]
    if len(all_rows)>30:raise StageFailure('selection_count_requires_confirmation','needs_confirmation')
    for index,row in enumerate(all_rows,1):row['series_id']=f'S{index:03d}'
    relations=[]
    for key in grouped:
        relations.extend(find_relations([r for r in all_rows if (r['org_id'],r['table_id'])==key],store.get(*key)))
    result.update(selections=all_rows,relations=relations)
    result.pop('frequency_alternatives',None) # Old alternatives refer to the model's incomplete row set.
    result.setdefault('period_notices',[]).extend({'message':n['message']} for n in notices)
    return result,notices
