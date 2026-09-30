"""Reproducible LOCAL demonstration; creates no Jira or service approval.

Reuse the qualified DEV-78 output without launching any new AI process.
"""
from pathlib import Path
import argparse
import json
import multiprocessing as mp
import time
from pickup import Queue, sha
from runner_bridge import stage_runner_output
from test_pickup import race_claim, race_publish, snapshot


def run(output, source):
    output=Path(output); output.mkdir(parents=True, exist_ok=False)
    source=Path(source)
    context=json.loads((source/'context.json').read_text()); now=time.time()
    a={'key':'BOOK-DEMO-1','approval_id':'fictional-demonstration-only',
       'authority_ref':'local fixture, not authority to act on a real Jira ticket',
       'definition_sha256':sha(b'definition'),'request_sha256':context['request_sha256'],
       'expires':now+600,'priority':1}
    q=Queue.create(output/'private-queue.sqlite',[a],publisher='publisher',seconds=600,lease_seconds=500,max_claims=2,max_concurrent=2)
    ctx=mp.get_context('spawn'); start=ctx.Event(); out=ctx.Queue()
    workers=[ctx.Process(target=race_claim,args=(str(q.path),'worker-'+str(i),start,out)) for i in range(2)]
    for p in workers:p.start()
    start.set(); results=[out.get(timeout=20) for p in workers]
    for p in workers:
        p.join(20)
        if p.exitcode!=0:raise RuntimeError('contender_failed')
    wins=[r['claim'] for r in results if r['claim']]
    if len(wins)!=1:raise RuntimeError('not_one_owner')
    win=wins[0]
    q.confirm_board_claim(win['token'],snapshot(now=time.time(),status='In Progress',owner=win['owner']), 'LOCAL FIXTURE: simulated board transition, not a Jira write')
    proof=stage_runner_output(q,win['token'],source)
    dispatch=q.begin_publication(win['token'],'publisher',snapshot(now=time.time(),status='In Progress',owner=win['owner']))
    target=output/'accepted-result.json'
    with target.open('xb') as f:f.write(dispatch['payload'])
    readback=target.read_bytes()
    if sha(readback)!=dispatch['result_sha256']:raise RuntimeError('readback_mismatch')
    q.settle_publication(dispatch['dispatch_token'],'publisher',sha(readback),'LOCAL FILE: accepted-result.json; exact byte readback')
    late=[]
    for token in [win['token'],'not-the-owner']:
        try:q.begin_publication(token,'publisher',snapshot(now=time.time(),status='In Progress',owner=win['owner']))
        except ValueError as e:late.append(str(e))
    if len(late)!=2:raise RuntimeError('replay_not_rejected')
    q.stop('local qualification complete; no further scan or work admitted')
    evidence={'scope':'local deterministic process contention and filesystem publication, not live Jira claim or end-to-end connector integration',
              'actual_competing_processes':2,'process_exit_codes':[p.exitcode for p in workers],
              'winning_owners':len(wins),'exact_result_file_sha256':sha(readback),
              'rejected_repeat_or_wrong_owner':late,'runner_proof':proof,
              'source_evidence':'DEV-78 qualified live-pass, reused; no fresh AI generation or new review claimed',
              'new_AI_processes':0,'source_files':{p.name:sha(p.read_bytes()) for p in sorted(source.glob('*.json'))},
              'state':q.inspect()}
    (output/'evidence.json').write_text(json.dumps(evidence,indent=2))
    return evidence


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--runner-evidence',default=str(Path(__file__).parent/'reference-evidence'))
    a=p.parse_args();print(json.dumps(run(a.output,a.runner_evidence),indent=2))
