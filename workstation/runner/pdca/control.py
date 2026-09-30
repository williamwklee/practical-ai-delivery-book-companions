"""Durable local-run limit for the consumer CLI qualification, not API billing."""
from pathlib import Path
import json,os,sqlite3,time,uuid

def atomic_json(path,data):
    path=Path(path);tmp=path.with_name(path.name+'.pending')
    fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as out:json.dump(data,out,indent=2)
    os.replace(str(tmp),str(path))

class RunLedger:
    def __init__(self,path):
        self.path=Path(path)
        if not self.path.is_file():raise ValueError('existing_registered_ledger_required')
    @classmethod
    def create(cls,path,unit,maximum_runs,wall_seconds):
        if not isinstance(maximum_runs,int) or isinstance(maximum_runs,bool) or not 1<=maximum_runs<=10:
            raise ValueError('invalid_run_limit')
        if not 1<=wall_seconds<=7200:raise ValueError('invalid_wall_limit')
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        con=sqlite3.connect(str(path))
        with con:
            con.execute('CREATE TABLE campaign(unit TEXT, maximum_runs INTEGER, deadline REAL, stopped INTEGER)')
            con.execute('INSERT INTO campaign VALUES(?,?,?,0)',(unit,maximum_runs,time.time()+wall_seconds))
            con.execute('CREATE TABLE runs(id TEXT PRIMARY KEY, provider TEXT, state TEXT, result TEXT)')
        con.close();return cls(path)
    def reserve(self,unit,provider):
        con=sqlite3.connect(str(self.path),timeout=2)
        try:
            con.execute('BEGIN IMMEDIATE')
            owner,maximum,deadline,stopped=con.execute('SELECT * FROM campaign').fetchone()
            rows=con.execute('SELECT state FROM runs').fetchall()
            if owner!=unit:raise ValueError('wrong_unit')
            if stopped or time.time()>=deadline:raise ValueError('campaign_stopped')
            if len(rows)>=maximum:raise ValueError('run_limit_exhausted')
            if any(r[0]!='completed' for r in rows):raise ValueError('previous_attempt_not_completed')
            rid=uuid.uuid4().hex
            con.execute('INSERT INTO runs VALUES(?,?,?,NULL)',(rid,provider,'reserved'))
            con.commit();return rid,deadline
        except Exception:con.rollback();raise
        finally:con.close()
    def settle(self,rid,result):
        con=sqlite3.connect(str(self.path),timeout=2)
        try:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT state FROM runs WHERE id=?',(rid,)).fetchone()
            if row is None or row[0]!='reserved':raise ValueError('not_pending')
            success=result.get('success') is True
            con.execute('UPDATE runs SET state=?,result=? WHERE id=?',
                        ('completed' if success else 'stopped',json.dumps(result),rid))
            if not success:con.execute('UPDATE campaign SET stopped=1')
            con.commit()
        except Exception:con.rollback();raise
        finally:con.close()
    def snapshot(self):
        con=sqlite3.connect(str(self.path));con.row_factory=sqlite3.Row
        result={'campaign':dict(con.execute('SELECT * FROM campaign').fetchone()),
                'runs':[dict(r) for r in con.execute('SELECT id,provider,state FROM runs ORDER BY rowid')]}
        con.close();return result
