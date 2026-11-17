import hashlib, json
from copy import deepcopy
from app.coerce import instant
from app.errors import BusinessError
from app.config import RULE_VERSION

DISPOSITIONS=('维持','重判','新增偏离')

def validate(amendment,context):
    if not isinstance(amendment,dict): raise BusinessError('BAD_AMENDMENT','更正内容须为对象')
    version=amendment.get('version')
    if not isinstance(version,str) or not version.strip(): raise BusinessError('BAD_AMENDMENT','更正须指定新版本号 version')
    version=version.strip()
    if version in context['plans']: raise BusinessError('AMENDMENT_OVERWRITES_VERSION','更正不得改写已生效方案版本，须以新版本号生效')
    windows=amendment.get('windows')
    if not isinstance(windows,dict) or not windows: raise BusinessError('BAD_AMENDMENT','更正须给出新版本访视窗 windows')
    if set(windows)!=set(context['required_visits']): raise BusinessError('AMENDMENT_BAD_WINDOW','新版本窗口须覆盖全部规定访视')
    parsed={}
    for visit,bounds in windows.items():
        if not isinstance(bounds,list) or len(bounds)!=2: raise BusinessError('AMENDMENT_BAD_WINDOW','窗口须为[起,止)两日值')
        start,end=bounds
        if isinstance(start,bool) or isinstance(end,bool) or not isinstance(start,int) or not isinstance(end,int) or not 0<=start<end:
            raise BusinessError('AMENDMENT_BAD_WINDOW','窗口须为半开区间且 0≤起<止')
        parsed[visit]=[start,end]
    if 'effective_at' not in amendment: raise BusinessError('BAD_AMENDMENT','更正须给出生效时刻 effective_at')
    t=instant(amendment['effective_at'])
    subjects=amendment.get('subjects','all')
    if subjects=='all': targets=sorted(context['subjects'])
    elif isinstance(subjects,list) and subjects and all(isinstance(s,str) for s in subjects):
        if any(s not in context['subjects'] for s in subjects): raise BusinessError('UNKNOWN_SUBJECT','受试者未入组')
        targets=sorted(set(subjects))
    else: raise BusinessError('BAD_AMENDMENT','subjects 须为 all 或受试者编号列表')
    label=amendment.get('label') or version
    if not isinstance(label,str): raise BusinessError('BAD_AMENDMENT','label 须为文本')
    return {'version':version,'windows':parsed,'effective_at':amendment['effective_at'],'_t':t,'targets':targets,'label':label}

def build_context(context,amendment):
    """按受试者生效窗截断拼接：生效时刻前维持旧版，起按新版。只允许新增版本号，不做全量追溯。"""
    spec=validate(amendment,context);t=spec['_t'];ctx=deepcopy(context)
    ctx['plans'][spec['version']]=deepcopy(spec['windows'])
    for subject in spec['targets']:
        wins=ctx['assignments'].get(subject,[])
        containing=[w for w in wins if instant(w['start'])<=t<instant(w['end'])]
        if len(containing)!=1: raise BusinessError('AMENDMENT_EFFECTIVE_RANGE','更正生效时刻不在受试者方案生效窗内')
        cur=containing[0]
        far_end=max((w for w in wins if instant(w['end'])>t),key=lambda w:instant(w['end']))['end']
        merged=[w for w in wins if instant(w['end'])<=t]
        if instant(cur['start'])<t: merged.append({'version':cur['version'],'start':cur['start'],'end':spec['effective_at']})
        merged.append({'version':spec['version'],'start':spec['effective_at'],'end':far_end})
        merged.sort(key=lambda w:instant(w['start']))
        ctx['assignments'][subject]=merged
    return ctx,spec

def _index(run):
    dev={(d['subject'],d['visit']):d for d in run['deviations']}
    comp={(c['subject'],c['visit']):c for c in run['completed']}
    sel={s['primary_id']:s for s in run['selected_results']}
    return dev,comp,sel

def _outcome(dev,comp,sel,subject,visit):
    if (subject,visit) in dev:
        d=dev[(subject,visit)]
        return {'status':'偏离','code':d['code'],'record':d.get('id','')}
    if (subject,visit) in comp:
        c=comp[(subject,visit)];s=sel.get(c['id'])
        return {'status':'完成','protocol':c['selected_protocol'],'window':[c['start_day'],c['end_day']],'day':c['day'],
                'record':c['id'],'selected':s['id'] if s else '','retest':s['code'] if s else '',
                'revision':s['revision'] if s else '','value_mg_dl':s['value_mg_dl'] if s else ''}
    return {'status':'未知'}

def classify(before,after):
    if before==after: return '维持'
    if after['status']=='偏离' and before.get('status')!='偏离': return '新增偏离'
    return '重判'

def diff_runs(base,candidate):
    """逐受试者×访视对比基批与候选批，形成唯一锁库矩阵：每格一条结论，标注处置与来源批次。"""
    context=base['context'];bd,bc,bs=_index(base);cd,cc,cs=_index(candidate)
    base_id=base['manifest']['run_id'];cand_id=candidate['manifest']['run_id'];rows=[]
    for subject in sorted(context['subjects']):
        for visit in context['required_visits']:
            before=_outcome(bd,bc,bs,subject,visit);after=_outcome(cd,cc,cs,subject,visit)
            disposition=classify(before,after)
            rows.append({'subject':subject,'visit':visit,'disposition':disposition,'before':before,'after':after,
                         'source':base_id if disposition=='维持' else cand_id})
    summary={k:0 for k in DISPOSITIONS}
    for r in rows: summary[r['disposition']]+=1
    bq={(q['id'],q['code']) for q in base['quarantine']};cq={(q['id'],q['code']) for q in candidate['quarantine']}
    quarantine_changes={'新增隔离':[{'id':i,'code':c} for i,c in sorted(cq-bq)],'解除隔离':[{'id':i,'code':c} for i,c in sorted(bq-cq)]}
    return rows,summary,quarantine_changes

def gate(base,candidate,rows,quarantine_changes):
    """放行闸门：候选批无阻断、影响全部处置、来源可追溯，三者齐备才放行。"""
    balance=candidate['balance'];base_id=base['manifest']['run_id'];cand_id=candidate['manifest']['run_id']
    expected=len(base['context']['subjects'])*len(base['context']['required_visits'])
    unhandled=balance['blocked']+len(quarantine_changes['新增隔离'])
    complete=len(rows)==expected and all(r['disposition'] in DISPOSITIONS and r['before']['status']!='未知' and r['after']['status']!='未知' for r in rows)
    sources=candidate['input_snapshot']['bytes_b64']==base['input_snapshot']['bytes_b64'] and all(r['source'] in {base_id,cand_id} for r in rows)
    checks=[
        {'key':'row_balanced','label':'候选批行守恒','pass':bool(balance['row_balanced']),'detail':f"输入 {balance['input']} 行，采纳 {balance['accepted']}，隔离 {balance['quarantined']}"},
        {'key':'no_unhandled','label':'无未处理影响','pass':unhandled==0,'detail':f"阻断项 {balance['blocked']}，新增隔离 {len(quarantine_changes['新增隔离'])}"},
        {'key':'diff_complete','label':'影响全部处置','pass':complete,'detail':f"{len(rows)}/{expected} 单元已分类"},
        {'key':'sources_known','label':'来源可追溯','pass':sources,'detail':'候选批与基批同源' if sources else '存在来源不明记录'},
    ]
    return {'pass':all(c['pass'] for c in checks),'checks':checks}

def relock_id(base_run_id,amendment):
    payload=json.dumps(amendment,ensure_ascii=False,sort_keys=True)
    return 'RL'+hashlib.sha256((base_run_id+payload+RULE_VERSION).encode()).hexdigest()[:14]

def make_relock(base,candidate,amendment,spec):
    rows,summary,quarantine_changes=diff_runs(base,candidate)
    return {'id':relock_id(base['manifest']['run_id'],amendment),
            'base_run_id':base['manifest']['run_id'],'candidate_run_id':candidate['manifest']['run_id'],
            'amendment':amendment,'label':spec['label'],'version':spec['version'],'effective_at':spec['effective_at'],'targets':spec['targets'],
            'rows':rows,'summary':summary,'quarantine_changes':quarantine_changes,
            'gate':gate(base,candidate,rows,quarantine_changes),'status':'待放行'}

def verify(relock,base,candidate):
    """放行前重算核对：同源、候选批摘要可复现、差异与决议一致、闸门通过。任一失败即抛错，旧版锁库保留。"""
    from app.engine import evaluate
    if candidate['input_snapshot']['bytes_b64']!=base['input_snapshot']['bytes_b64']:
        raise BusinessError('SOURCE_UNKNOWN','候选批来源不明：与基批原始字节不一致',409)
    ctx,_=build_context(base['input_snapshot']['context'],relock['amendment'])
    fresh=evaluate({'dataset':base['source']},snapshot={'bytes_b64':base['input_snapshot']['bytes_b64'],'context':ctx})
    if fresh['manifest']['sha256']!=candidate['manifest']['sha256']:
        raise BusinessError('SOURCE_UNKNOWN','候选批来源不明：按更正重算摘要不一致',409)
    rows,summary,quarantine_changes=diff_runs(base,candidate)
    if rows!=relock['rows'] or summary!=relock['summary'] or quarantine_changes!=relock['quarantine_changes']:
        raise BusinessError('VERIFY_MISMATCH','核对失败：重算差异与决议不一致',409)
    g=gate(base,candidate,rows,quarantine_changes)
    if not g['pass']:
        raise BusinessError('GATE_BLOCKED','存在未处理影响或来源不明，不能放行',422)
    return g
