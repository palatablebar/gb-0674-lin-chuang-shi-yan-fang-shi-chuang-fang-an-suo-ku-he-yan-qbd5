import sqlite3, json
from datetime import datetime, timezone
from pathlib import Path
from app.errors import BusinessError

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    con.execute("CREATE TABLE IF NOT EXISTS relocks (id TEXT PRIMARY KEY, base_run_id TEXT NOT NULL, candidate_run_id TEXT NOT NULL, amendment TEXT NOT NULL, result TEXT NOT NULL, status TEXT NOT NULL DEFAULT '待放行', created_at TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS formal_lock (id INTEGER PRIMARY KEY CHECK(id=1), run_id TEXT NOT NULL, relock_id TEXT, locked_at TEXT NOT NULL)")
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    return result

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)

def _relock_from(row):
    result=json.loads(row['result']);result.update(status=row['status'],created_at=row['created_at'])
    return result

def save_relock(path, relock):
    """同一更正重试不重复：按内容摘要幂等写入，返回(决议, 是否新建)。"""
    created_at=datetime.now(timezone.utc).isoformat()
    with connect(path) as con:
        cur=con.execute('INSERT OR IGNORE INTO relocks(id,base_run_id,candidate_run_id,amendment,result,created_at) VALUES(?,?,?,?,?,?)',
            (relock['id'],relock['base_run_id'],relock['candidate_run_id'],json.dumps(relock['amendment'],ensure_ascii=False),json.dumps(relock,ensure_ascii=False),created_at))
    return get_relock(path,relock['id']),cur.rowcount==1

def get_relock(path, relock_id):
    with connect(path) as con: row=con.execute('SELECT * FROM relocks WHERE id=?',(relock_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','更正决议不存在',404)
    return _relock_from(row)

def list_relocks(path):
    with connect(path) as con:
        rows=con.execute('SELECT * FROM relocks ORDER BY rowid DESC LIMIT 100').fetchall()
    return [{'id':r['id'],'base_run_id':r['base_run_id'],'candidate_run_id':r['candidate_run_id'],'status':r['status'],
             'created_at':r['created_at'],'label':json.loads(r['result']).get('label',''),
             'version':json.loads(r['result']).get('version',''),'summary':json.loads(r['result']).get('summary',{})} for r in rows]

def get_lock(path):
    with connect(path) as con: row=con.execute('SELECT * FROM formal_lock WHERE id=1').fetchone()
    return dict(row) if row else None

def lock_initial(path, run_id):
    """首次正式锁库：仅当尚无锁库记录。已有锁库时同批幂等返回，异批拒绝。"""
    locked_at=datetime.now(timezone.utc).isoformat()
    with connect(path) as con:
        existing=con.execute('SELECT * FROM formal_lock WHERE id=1').fetchone()
        if existing:
            if existing['run_id']==run_id: return dict(existing),False
            raise BusinessError('LOCK_EXISTS','已存在正式锁库，方案变更请经方案更正重锁',409)
        con.execute('INSERT INTO formal_lock(id,run_id,relock_id,locked_at) VALUES(1,?,NULL,?)',(run_id,locked_at))
    return get_lock(path),True

def promote(path, relock_id, verify_fn):
    """核对完整才切换正式锁库；任一失败抛错回滚，旧版保留。重复放行幂等。"""
    with connect(path) as con:
        row=con.execute('SELECT * FROM relocks WHERE id=?',(relock_id,)).fetchone()
        if not row: raise BusinessError('NOT_FOUND','更正决议不存在',404)
        relock=_relock_from(row)
        lock=con.execute('SELECT * FROM formal_lock WHERE id=1').fetchone()
        if lock and lock['relock_id']==relock_id:
            return relock,dict(lock),False
        def load_run(run_id):
            r=con.execute('SELECT result FROM runs WHERE id=?',(run_id,)).fetchone()
            if not r: raise BusinessError('NOT_FOUND','批次不存在',404)
            return json.loads(r['result'])
        verify_fn(relock,load_run(relock['base_run_id']),load_run(relock['candidate_run_id']),dict(lock) if lock else None)
        locked_at=datetime.now(timezone.utc).isoformat()
        con.execute("UPDATE relocks SET status='已放行' WHERE id=?",(relock_id,))
        con.execute('INSERT INTO formal_lock(id,run_id,relock_id,locked_at) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET run_id=excluded.run_id,relock_id=excluded.relock_id,locked_at=excluded.locked_at',
            (relock['candidate_run_id'],relock_id,locked_at))
    return get_relock(path,relock_id),get_lock(path),True
