from fastapi import APIRouter, Request, Body
from app.engine import evaluate
from app.errors import BusinessError
from app import store
from app.web import templates
from app.config import META
from app.modules.amendment import rules

router=APIRouter()

SAMPLES={
    'shift':{'label':'AMD-2026-11-A 窗整体后移','version':'v3','effective_at':'2026-10-12T00:00:00+08:00','subjects':'all','windows':{'V1':[6,12],'V2':[13,18]}},
    'deviation':{'label':'AMD-2026-11-B V2窗收紧','version':'v3','effective_at':'2026-10-12T00:00:00+08:00','subjects':'all','windows':{'V1':[6,12],'V2':[15,18]}},
}

def db():
    from app.main import DB_PATH
    return DB_PATH

@router.get('/amendment')
def page(request:Request):
    run_id=request.query_params.get('run_id')
    if run_id: state=store.get(db(),run_id)
    else: state=store.save(db(),evaluate({'dataset':request.query_params.get('dataset','dirty')}))
    selected=None
    relock_id=request.query_params.get('relock')
    if relock_id: selected=store.get_relock(db(),relock_id)
    return templates.TemplateResponse(request=request,name='amendment.html',
        context={'meta':META,'key':'amendment','state':state,'view':{'rows':[]},
                 'lock':store.get_lock(db()),'relocks':store.list_relocks(db()),'selected':selected,'samples':SAMPLES})

@router.post('/api/amendment')
def dashboard(payload:dict=Body(default={})):
    return {'formal_lock':store.get_lock(db()),'relocks':store.list_relocks(db())}

@router.post('/api/amendments')
def create(payload:dict=Body(...)):
    run_id=payload.get('run_id')
    if not isinstance(run_id,str) or not run_id: raise BusinessError('BAD_BASE','缺少基批 run_id')
    amendment=payload.get('amendment')
    base=store.get(db(),run_id)
    context,spec=rules.build_context(base['context'],amendment)
    candidate=store.save(db(),evaluate({'dataset':base['source']},snapshot={'bytes_b64':base['input_snapshot']['bytes_b64'],'context':context}))
    relock=rules.make_relock(base,candidate,amendment,spec)
    saved,created=store.save_relock(db(),relock)
    return {**saved,'deduplicated':not created}

@router.get('/api/amendments')
def list_all():
    return store.list_relocks(db())

@router.get('/api/amendments/{relock_id}')
def detail(relock_id:str):
    return store.get_relock(db(),relock_id)

@router.post('/api/amendments/{relock_id}/promote')
def promote(relock_id:str):
    def verify(relock,base,candidate,lock):
        if lock and lock['run_id']!=relock['base_run_id']:
            raise BusinessError('BASE_NOT_CURRENT','基批已非当前正式锁库，请基于现行锁库重新发起更正',409)
        rules.verify(relock,base,candidate)
    relock,lock,switched=store.promote(db(),relock_id,verify)
    return {'relock':relock,'formal_lock':lock,'switched':switched}
