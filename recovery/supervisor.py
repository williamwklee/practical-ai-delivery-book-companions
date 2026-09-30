"""Restartable wrapper around the unchanged DEV-78 bounded process adapter.

The adapter runs in its own process so its finite timeout survives loss of the
coordinator. A missing receipt or uncertain process never grants a new launch.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import time

from recover import current_claim, encoded
from upstream.pickup import require, sha

_LOCAL_HANDLES = {}  # Reap children while this coordinator lives; never authority.


def atomic(path, value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.pending')
    with tmp.open('x') as f:
        os.chmod(tmp,0o600);json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def birth(pid):
    r=subprocess.run(['ps','-p',str(pid),'-o','lstart=','-o','stat='],capture_output=True,text=True,timeout=3)
    if r.returncode not in (0,1):raise ValueError('process_inspection_failed')
    text=r.stdout.strip()
    if not text:return None
    fields=text.split()
    if len(fields)<6:raise ValueError('process_identity_unreadable')
    return {'started':' '.join(fields[:5]),'state':fields[5]}


def launch(queue, budget, owner, token, snapshot, role, action, prompt, folder,
           argv, provider, model, context, *, env=None, synthetic=False, timeout=480,
           first_output_timeout=60, idle_timeout=300):
    claim=current_claim(queue,token,snapshot,('running',));unit=claim['unit']
    folder=Path(folder).resolve();prompt=Path(prompt).resolve()
    require(0<timeout<=480,'finite_process_timeout')
    require(0<first_output_timeout<=480 and 0<idle_timeout<=480,'finite_output_timeouts')
    binding=sha(encoded({'unit':unit,'claim':claim['id'],'prompt':sha(prompt.read_bytes()),
                        'argv':argv,'provider':provider,'model':model,'context':context,'role':role}))
    reserved=budget.reserve(unit,owner,role,action,binding)
    try:
        folder.mkdir(mode=0o700,parents=True,exist_ok=False)
        remaining=min(timeout,reserved['deadline']-time.time(),claim['expires']-time.time())
        require(remaining>0,'launch_window_expired')
        receipt=folder/'reservation.json'
        atomic(receipt,{'reserved':True,'mode':'consumer_subscription','unit':unit,'run_id':reserved['id'],
                       'argv_sha256':sha(json.dumps(argv).encode()),'prompt_sha256':sha(prompt.read_bytes()),
                       'expires_unix':time.time()+remaining})
        spec={'argv':argv,'prompt_path':str(prompt),'run_dir':str(folder/'run'),'provider':provider,
              'model':model,'role':role,'unit':unit,'timeout':remaining,'first_output_timeout':min(first_output_timeout,remaining),
              'idle_timeout':min(idle_timeout,remaining),'max_output_bytes':2_000_000,'grace':.2,
              'cancel_path':str(budget.path.with_suffix('.STOP')),'synthetic':synthetic,
              'launch_receipt':str(receipt),'context':dict(context,run_id=reserved['id'])}
        atomic(folder/'manifest.json',spec)
        atomic(folder/'binding.json',{'attempt_id':reserved['id'],'binding':binding,'claim_id':claim['id'],
              'unit':unit,'prompt_sha256':sha(prompt.read_bytes()),'folder':str(folder),'provider':provider})
    except Exception as e:
        budget.abandon_before_launch(reserved['id'],owner,type(e).__name__,[str(folder)])
        raise
    current_claim(queue,token,snapshot,('running',))
    budget.begin_launch(reserved['id'],owner)
    # The crash gap before process.json is deliberately uncertain, never "no process".
    with (folder/'supervisor.stdout').open('xb') as out,(folder/'supervisor.stderr').open('xb') as err:
        os.chmod(folder/'supervisor.stdout',0o600);os.chmod(folder/'supervisor.stderr',0o600)
        proc=subprocess.Popen([sys.executable,str(Path(__file__).parent/'upstream/adapter.py'),str(folder/'manifest.json')],
                              stdout=out,stderr=err,env=env,start_new_session=True)
    _LOCAL_HANDLES[proc.pid]=proc
    identity=birth(proc.pid)
    atomic(folder/'process.json',{'pid':proc.pid,'identity':identity,'attempt_id':reserved['id'],
                                 'supervisor':'existing bounded adapter; own process group'})
    # Popen need not survive in memory. Receipt + final adapter files survive restart.
    return {'attempt_id':reserved['id'],'folder':str(folder),'pid':proc.pid}


def recover_result(budget, owner, folder, *, findings_classifier=None):
    """Inspect and settle the original attempt, without launching or service writes."""
    folder=Path(folder).resolve()
    b=json.loads((folder/'binding.json').read_text());require(b['folder']==str(folder),'attempt_folder_moved')
    attempts={x['id']:x for x in budget.snapshot()['attempts']}
    attempt=attempts.get(b['attempt_id'])
    require(attempt is not None and attempt['binding']==b['binding'] and attempt['unit']==b['unit'], 'attempt_binding_changed')
    if attempt['state']=='settled':return {'state':'already_settled','attempt_id':attempt['id']}
    require(attempt['state'] in ('reserved','launching'),'attempt_needs_manual_reconciliation')
    marker=folder/'process.json'
    require(marker.is_file(),'process_receipt_missing_manual_inspection_required')
    p=json.loads(marker.read_text());require(p['attempt_id']==attempt['id'],'wrong_process_receipt')
    handle=_LOCAL_HANDLES.get(p['pid'])
    if handle is not None and handle.poll() is not None:_LOCAL_HANDLES.pop(p['pid'])
    identity=birth(p['pid'])
    if identity and p['identity'] and identity['started']==p['identity']['started'] and not identity['state'].startswith('Z'):
        return {'state':'still_running','attempt_id':attempt['id']}
    # A dead or reused PID is insufficient. Require the original adapter's final
    # terminal/exit/group/binding evidence; otherwise unknown descendants may exist.
    rp=folder/'run/run.json';fp=folder/'run/final.txt'
    require(rp.is_file() and fp.is_file(),'terminal_evidence_missing_manual_inspection_required')
    r=json.loads(rp.read_text());final=fp.read_bytes()
    require(r['run_id']==attempt['id'] and r['unit']==b['unit'] and
            r['prompt_sha256']==b['prompt_sha256'] and r['final_sha256']==sha(final),'process_result_binding_mismatch')
    parsed=r['result'];valid=bool(parsed['terminal_event_valid'] and not parsed['reported_failure'] and
                                 parsed['malformed_lines']==0 and parsed['model_event_valid'] and final.strip())
    usage=None
    if parsed.get('usage_valid') and isinstance(parsed.get('usage'),dict):
        u=parsed['usage']
        usage=u['input_tokens']+u['output_tokens'] if b['provider']=='codex' else sum(
            x[k] for x in u.values() for k in ['inputTokens','outputTokens','cacheReadInputTokens','cacheCreationInputTokens'])
    failure='manual' if r['stop_reason']=='cancelled' else None
    if failure is None and (not valid or r['exit_code']!=0 or r['live_process_group_survivors']):failure='missing_evidence'
    result={'process_ended':r['exit_code'] is not None and not r['live_process_group_survivors'],
            'terminal_valid':valid,'exit_code':r['exit_code'],'result_sha256':sha(final),'binding':b['binding'],
            'observed_tokens':usage,'failure':failure,'partial':[str(rp),str(fp)]}
    if attempt['purpose']=='checker' and not failure and findings_classifier is not None:
        result['findings_fingerprint']=findings_classifier(final.decode(),r)
    state=budget.settle(attempt['id'],owner,result)
    return {'state':state,'attempt_id':attempt['id'],'result':result,'process_record':r}
