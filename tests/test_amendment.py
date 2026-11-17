from app.modules.amendment import rules
from app.modules.amendment.handler import SAMPLES
from app.engine import evaluate
from app.errors import BusinessError
from app import main, store
from fastapi.testclient import TestClient
from copy import deepcopy
import json, sqlite3, pytest

@pytest.fixture
def clean():
    return evaluate({'dataset':'clean'})
@pytest.fixture
def dirty():
    return evaluate({'dataset':'dirty'})
def amended(base,sample):
    context,spec=rules.build_context(base['context'],deepcopy(sample))
    return evaluate({'dataset':base['source']},snapshot={'bytes_b64':base['input_snapshot']['bytes_b64'],'context':context}),spec
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'test.sqlite'));return TestClient(main.app)
def lock_clean(c):
    run=c.post('/api/runs',json={'dataset':'clean'}).json();rid=run['manifest']['run_id']
    assert c.post(f'/api/runs/{rid}/lock').status_code==200
    return rid

def test_build_context_truncates_assignment(clean):
    ctx,spec=rules.build_context(clean['context'],deepcopy(SAMPLES['shift']))
    assert ctx['plans']['v3']=={'V1':[6,12],'V2':[13,18]}
    assert [(w['version'],w['start'],w['end']) for w in ctx['assignments']['00002']]==[
        ('v1','2026-01-01T00:00:00+08:00','2026-10-12T00:00:00+08:00'),
        ('v3','2026-10-12T00:00:00+08:00','2027-01-01T00:00:00+08:00')]
    # 受试者00001先生效v2，更正在v2窗内截断，v1窗不动
    assert [(w['version'],w['start'],w['end']) for w in ctx['assignments']['00001']]==[
        ('v1','2026-01-01T00:00:00+08:00','2026-10-10T00:00:00+08:00'),
        ('v2','2026-10-10T00:00:00+08:00','2026-10-12T00:00:00+08:00'),
        ('v3','2026-10-12T00:00:00+08:00','2027-01-01T00:00:00+08:00')]
def test_amendment_drops_superseded_windows(clean):
    # 生效时刻落在v1窗内时，其后的v2窗被更正取代，不产生重叠
    amendment={'version':'v3','effective_at':'2026-10-05T00:00:00+08:00','subjects':['00001'],'windows':{'V1':[6,12],'V2':[13,18]}}
    ctx,_=rules.build_context(clean['context'],amendment)
    wins=ctx['assignments']['00001']
    assert [w['version'] for w in wins]==['v1','v3']
    assert wins[0]['end']==wins[1]['start']=='2026-10-05T00:00:00+08:00'
def test_overwrite_existing_version_rejected(clean):
    with pytest.raises(BusinessError) as e:
        rules.build_context(clean['context'],{'version':'v1','effective_at':'2026-10-12T00:00:00+08:00','windows':{'V1':[6,12],'V2':[13,18]}})
    assert e.value.code=='AMENDMENT_OVERWRITES_VERSION'
def test_bad_window_rejected(clean):
    with pytest.raises(BusinessError) as e:
        rules.build_context(clean['context'],{'version':'v3','effective_at':'2026-10-12T00:00:00+08:00','windows':{'V1':[6,12]}})
    assert e.value.code=='AMENDMENT_BAD_WINDOW'
    with pytest.raises(BusinessError) as e:
        rules.build_context(clean['context'],{'version':'v3','effective_at':'2026-10-12T00:00:00+08:00','windows':{'V1':[12,6],'V2':[13,18]}})
    assert e.value.code=='AMENDMENT_BAD_WINDOW'
def test_effective_outside_assignment_rejected(clean):
    with pytest.raises(BusinessError) as e:
        rules.build_context(clean['context'],{'version':'v3','effective_at':'2028-01-01T00:00:00+08:00','windows':{'V1':[6,12],'V2':[13,18]}})
    assert e.value.code=='AMENDMENT_EFFECTIVE_RANGE'
def test_unknown_subject_rejected(clean):
    with pytest.raises(BusinessError) as e:
        rules.build_context(clean['context'],{'version':'v3','effective_at':'2026-10-12T00:00:00+08:00','subjects':['99999'],'windows':{'V1':[6,12],'V2':[13,18]}})
    assert e.value.code=='UNKNOWN_SUBJECT'
def test_diff_maintained_and_readjudicated(clean):
    candidate,spec=amended(clean,SAMPLES['shift'])
    rows,summary,changes=rules.diff_runs(clean,candidate)
    # 唯一锁库矩阵：每受试者×访视恰一条，来源可溯
    assert len(rows)==40 and len({(r['subject'],r['visit']) for r in rows})==40
    assert summary=={'维持':20,'重判':20,'新增偏离':0}
    v1=next(r for r in rows if r['subject']=='00002' and r['visit']=='V1')
    assert v1['disposition']=='维持' and v1['source']==clean['manifest']['run_id']
    v2=next(r for r in rows if r['subject']=='00002' and r['visit']=='V2')
    assert v2['disposition']=='重判' and v2['source']==candidate['manifest']['run_id']
    assert v2['before']['protocol']=='v1' and v2['after']['protocol']=='v3'
    assert v2['after']['retest']=='PRIMARY_SELECTED'
    assert changes=={'新增隔离':[],'解除隔离':[]}
def test_diff_new_deviation(clean):
    candidate,spec=amended(clean,SAMPLES['deviation'])
    rows,summary,changes=rules.diff_runs(clean,candidate)
    assert summary=={'维持':20,'重判':0,'新增偏离':20}
    row=next(r for r in rows if r['subject']=='00002' and r['visit']=='V2')
    assert row['before']['status']=='完成' and row['after']=={'status':'偏离','code':'WINDOW_EARLY','record':'00002-V2'}
    assert not rules.gate(clean,candidate,rows,changes)['pass']
def test_retest_cannot_fill_missing_after_amendment(dirty):
    candidate,spec=amended(dirty,SAMPLES['shift'])
    assert any(d['subject']=='00020' and d['visit']=='V2' and d['code']=='MISSING_PRIMARY' for d in candidate['deviations'])
    rows,summary,changes=rules.diff_runs(dirty,candidate)
    # 更正后00001-V1按v3窗重判为窗内，偏离消除；但漏访仍缺，复检不能填补
    fixed=next(r for r in rows if r['subject']=='00001' and r['visit']=='V1')
    assert fixed['disposition']=='重判' and fixed['before']['code']=='WINDOW_LATE' and fixed['after']['status']=='完成'
    missing=next(r for r in rows if r['subject']=='00020' and r['visit']=='V2')
    assert missing['disposition']=='维持' and missing['after']['code']=='MISSING_PRIMARY'
    assert not rules.gate(dirty,candidate,rows,changes)['pass']
def test_gate_passes_clean_candidate(clean):
    candidate,spec=amended(clean,SAMPLES['shift'])
    rows,summary,changes=rules.diff_runs(clean,candidate)
    g=rules.gate(clean,candidate,rows,changes)
    assert g['pass'] and all(c['pass'] for c in g['checks'])

def test_create_relock_idempotent(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);rid=lock_clean(c)
    body={'run_id':rid,'amendment':SAMPLES['shift']}
    a=c.post('/api/amendments',json=body).json();b=c.post('/api/amendments',json=body).json()
    assert a['id']==b['id'] and b['deduplicated'] is True
    assert len(c.get('/api/amendments').json())==1
    assert a['summary']=={'维持':20,'重判':20,'新增偏离':0} and a['gate']['pass']
def test_promote_switches_formal_lock(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);rid=lock_clean(c)
    relock=c.post('/api/amendments',json={'run_id':rid,'amendment':SAMPLES['shift']}).json()
    promoted=c.post(f"/api/amendments/{relock['id']}/promote")
    assert promoted.status_code==200 and promoted.json()['switched'] is True
    lock=promoted.json()['formal_lock']
    assert lock['run_id']==relock['candidate_run_id'] and lock['relock_id']==relock['id']
    again=c.post(f"/api/amendments/{relock['id']}/promote").json()
    assert again['switched'] is False and again['formal_lock']['run_id']==lock['run_id']
def test_failed_promote_keeps_old_lock(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);rid=lock_clean(c)
    relock=c.post('/api/amendments',json={'run_id':rid,'amendment':SAMPLES['deviation']}).json()
    assert relock['summary']['新增偏离']==20 and not relock['gate']['pass']
    response=c.post(f"/api/amendments/{relock['id']}/promote")
    assert response.status_code==422 and response.json()['code']=='GATE_BLOCKED'
    assert c.post('/api/amendment',json={}).json()['formal_lock']['run_id']==rid
def test_promote_requires_current_base(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);rid=lock_clean(c)
    first=c.post('/api/amendments',json={'run_id':rid,'amendment':SAMPLES['shift']}).json()
    assert c.post(f"/api/amendments/{first['id']}/promote").status_code==200
    stale=c.post('/api/amendments',json={'run_id':rid,'amendment':dict(SAMPLES['shift'],label='AMD-2026-11-C')}).json()
    response=c.post(f"/api/amendments/{stale['id']}/promote")
    assert response.status_code==409 and response.json()['code']=='BASE_NOT_CURRENT'
def test_promote_tampered_candidate_rejected(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);rid=lock_clean(c)
    relock=c.post('/api/amendments',json={'run_id':rid,'amendment':SAMPLES['shift']}).json()
    path=str(tmp_path/'test.sqlite')
    with sqlite3.connect(path) as con:
        raw=con.execute('SELECT result FROM runs WHERE id=?',(relock['candidate_run_id'],)).fetchone()[0]
        tampered=json.loads(raw);tampered['input_snapshot']['bytes_b64']='AAAA'+tampered['input_snapshot']['bytes_b64'][4:]
        con.execute('UPDATE runs SET result=? WHERE id=?',(json.dumps(tampered,ensure_ascii=False),relock['candidate_run_id']))
    response=c.post(f"/api/amendments/{relock['id']}/promote")
    assert response.status_code==409 and response.json()['code']=='SOURCE_UNKNOWN'
    assert c.post('/api/amendment',json={}).json()['formal_lock']['run_id']==rid
def test_promote_tampered_diff_rejected(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);rid=lock_clean(c)
    relock=c.post('/api/amendments',json={'run_id':rid,'amendment':SAMPLES['shift']}).json()
    path=str(tmp_path/'test.sqlite')
    with sqlite3.connect(path) as con:
        raw=con.execute('SELECT result FROM runs WHERE id=?',(relock['candidate_run_id'],)).fetchone()[0]
        tampered=json.loads(raw);tampered['deviations'].append({'subject':'00002','visit':'V2','id':'00002-V2','code':'WINDOW_LATE'})
        con.execute('UPDATE runs SET result=? WHERE id=?',(json.dumps(tampered,ensure_ascii=False),relock['candidate_run_id']))
    response=c.post(f"/api/amendments/{relock['id']}/promote")
    assert response.status_code==409 and response.json()['code']=='VERIFY_MISMATCH'
def test_initial_lock_rules(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    dirty_run=c.post('/api/runs',json={'dataset':'dirty'}).json()
    assert c.post(f"/api/runs/{dirty_run['manifest']['run_id']}/lock").status_code==422
    rid=lock_clean(c)
    assert c.post(f'/api/runs/{rid}/lock').json()['created'] is False
    other=c.post('/api/runs',json={'dataset':'legacy'}).json()
    if other['balance']['status']=='可锁库':
        response=c.post(f"/api/runs/{other['manifest']['run_id']}/lock")
        assert response.status_code==409 and response.json()['code']=='LOCK_EXISTS'
def test_amendment_page_and_dashboard(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);rid=lock_clean(c)
    relock=c.post('/api/amendments',json={'run_id':rid,'amendment':SAMPLES['shift']}).json()
    page=c.get(f"/amendment?run_id={rid}&relock={relock['id']}")
    assert page.status_code==200 and '前后差异' in page.text and '维持' in page.text and '放行闸门' in page.text
    assert c.get('/amendment').status_code==200
    dashboard=c.post('/api/amendment',json={}).json()
    assert dashboard['formal_lock']['run_id']==rid and len(dashboard['relocks'])==1
def test_amendment_on_dirty_base_blocked(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    dirty_run=c.post('/api/runs',json={'dataset':'dirty'}).json();rid=dirty_run['manifest']['run_id']
    relock=c.post('/api/amendments',json={'run_id':rid,'amendment':SAMPLES['shift']}).json()
    assert not relock['gate']['pass']
    assert c.post(f"/api/amendments/{relock['id']}/promote").status_code==422
    assert c.post('/api/amendment',json={}).json()['formal_lock'] is None
