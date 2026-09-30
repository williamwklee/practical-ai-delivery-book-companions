"""Six explicit fictional recovery cases; never a command to reset real state."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time
from unittest.mock import patch

from recover import Recovery, encoded, current_claim
from supervisor import launch, recover_result
from test_recovery import Fixture
from upstream.pickup import sha


def denied(fn):
    try:fn()
    except (ValueError,ConnectionError,PermissionError) as e:return type(e).__name__+': '+str(e)
    raise AssertionError('expected refusal or injected failure')


def public(value):
    if isinstance(value,bytes):return value.decode()
    if isinstance(value,list):return [public(v) for v in value]
    if isinstance(value,dict):
        out={}
        for k,v in value.items():
            if k in ('record','result','details','hashes') and isinstance(v,str) and v.startswith(('{','[')):
                try:v=json.loads(v)
                except ValueError:pass
            out[k]=public(v)
        return out
    return value


def run(root):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=False);cases=[]
    for name in ['cli_exit','service_outage','lost_context','expired_ownership','file_before_record','consumed_authority_budget']:
        folder=root/name;folder.mkdir();f=Fixture(folder);steps=[]
        if name=='cli_exit':
            prompt=folder/'prompt.txt';prompt.write_text('Synthetic recovery case, no model call.')
            launch(f.q,f.b,f.owner,f.token,f.snapshot(),'producer','draft',prompt,folder/'attempt',
                [sys.executable,str(Path(__file__).parent/'upstream/fixture_worker.py'),'failure'],
                'codex','fixture-model',{},synthetic=True,timeout=2)
            end=time.monotonic()+6
            while True:
                result=recover_result(f.b,f.owner,folder/'attempt')
                if result['state']!='still_running':break
                if time.monotonic()>=end:raise AssertionError('fixture exceeded wait')
                time.sleep(.05)
            assert result['state']=='unsettled';steps.append({'recovered_original_attempt':result})
            stop=next(x for x in f.b.snapshot()['stops'] if x['active'])
            steps.append({'resume_refused_before_reconciliation':denied(lambda:f.b.resume(stop['id'],f.owner,'fixture:inspect'))})
            rid=result['attempt_id'];f.b.reconcile(rid,f.owner,{'process_ended':True,'effects_checked':True,
                'reference':'fixture:original adapter exit/no-survivor receipt and no publication'})
            ev='fixture:failed process reconciled; operator permits next admitted attempt'
            refusal=denied(lambda:f.b.resume(stop['id'],f.owner,ev))
            assert refusal=='Denied: missing_stale_consumed_or_mismatched_authority'
            steps.append({'resume_without_authority_refused_after_reconciliation':refusal})
            payload={'stop_id':stop['id'],'evidence':ev,'new_plan':None}
            g=f.b.register_authority(f.owner,f.unit,'resume',payload,'SYNTHETIC:William-decision',
                'Fictional explicit recovery decision, not real authority',f.now+20)
            f.b.resume(stop['id'],f.owner,ev,g);steps.append({'consumed_model_slots_after_resume':f.used(),
                'stop_cleared_under_fictional_registered_decision':True,
                'further_launches_after_resume':0,'next_action':'close fixture without another launch',
                'failed_result_stop_test':'test_supervisor.test_cli_exit_failure_retains_partial_evidence_and_requires_manual_resolution'})
        elif name=='service_outage':
            f.stage(service=True)
            observed={'target':'fixture:confluence-page-1','version':3,'read_at':f.now,'receipt':'fixture:authoritative read'}
            first=f.r.service_intent(f.token,f.snapshot(),observed);first.pop('payload')
            steps.append({'first_intent_before_simulated_outage':first})
            f.reopen();steps.append({'unknown_state_refused':denied(lambda:f.r.service_intent(f.token,f.snapshot(),None,resumed=True))})
            retry=f.r.service_intent(f.token,f.snapshot(),observed,resumed=True);retry.pop('payload')
            service={'version':3,'writes':0}
            def apply(intent):
                if intent['expected_version']!=service['version']:return 'version conflict'
                service['version']+=1;service['writes']+=1;return 'saved'
            applied=apply(retry);late=apply(first);assert (applied,late,service['writes'])==('saved','version conflict',1)
            steps.append({'same_conditional_version_retry':retry,'retry_result':applied,'delayed_first_result':late,'simulated_service':service})
            saved=dict(observed,version=service['version'],operation_marker=retry['operation_marker'],result_sha256=retry['result_sha256'])
            steps.append({'saved_readback':f.r.observe_service(f.unit,saved)})
        elif name=='lost_context':
            f.r.checkpoint(f.unit,f.request,b'first incomplete draft',encoded({'verdict':'FAIL','findings':['Missing source']}))
            f.stage();f.reopen();contexts=f.r.restore(f.unit)
            assert len(contexts)==2 and b'Missing source' in contexts[0]['review']
            steps.append({'all_restored_context_versions':contexts})
            steps.append({'published_restored_final_candidate':f.r.publish_file(f.token,f.snapshot())})
        elif name=='expired_ownership':
            f.stage();f.now+=31;old=f.token
            steps.append({'expired_refusal':denied(lambda:f.r.publish_file(old,f.snapshot()))})
            ev='fixture:previous worker ended and effects checked';payload={'expires':f.now+20,'evidence':ev}
            steps.append({'automatic_renewal_refusal':denied(lambda:f.r.renew_claim(f.unit,f.snapshot(),f.now+20,ev,None))})
            g=f.authority('renew_claim',payload);f.token=f.r.renew_claim(f.unit,f.snapshot(),f.now+20,ev,g)
            steps.append({'old_token_refusal':denied(lambda:current_claim(f.q,old,f.snapshot()))})
            steps.append({'same_claim_publication':f.r.publish_file(f.token,f.snapshot())})
        elif name=='file_before_record':
            f.stage()
            def outage():raise ConnectionError('fixture:record unavailable after file saved')
            steps.append({'lost_reply':denied(lambda:f.r.publish_file(f.token,f.snapshot(),after_write=outage))})
            before=f.target.stat().st_mtime_ns;f.reopen();f.now+=120
            steps.append({'reconcile_after_expiry':f.r.inspect_file(f.unit),'idempotent_second_inspection':f.r.inspect_file(f.unit),
                          'file_unchanged':f.target.stat().st_mtime_ns==before})
        else:
            for _ in range(3):
                r=f.b.reserve(f.unit,f.owner,'producer','draft','synthetic:request')
                f.b.settle(r['id'],f.owner,{'process_ended':True,'terminal_valid':True,'exit_code':0,
                  'result_sha256':sha(b'synthetic result'),'binding':'synthetic:request','observed_tokens':1})
            f.stage();assert f.r.publish_file(f.token,f.snapshot())=='match'
            assert f.q.inspect()['claims'][0]['state']=='delivered'
            steps.append({'original_claim_completed_before_replay':True,'active_claims':0})
            f.reopen();steps.append({'model_budget_refusal':denied(lambda:f.b.reserve(f.unit,f.owner,'producer','draft','renamed'))})
            r=f.q.claim(f.q.reserve_scan(),'different',[f.snapshot(status='Work Approved',owner=None)])
            assert r['claim'] is None and r['reason']=='nothing_eligible'
            assert r['details'][f.unit]=='authority_consumed'
            steps.append({'authority_replay':r,'model_slots_still_used':f.used()})
        # Close each synthetic fixture, including completed effects. No reset.
        if not f.q.inspect()['policy']['stopped']:f.q.stop('fixture finished; no further work')
        f.b.stop('@all',f.owner,'manual','fixture qualification complete')
        record=public({'case':name,'synthetic':True,'actual_model_calls':0,'real_service_writes':0,
                       'steps':steps,'final_state':f.r.inspect()})
        out=root/(name+'.json');out.write_text(json.dumps(record,separators=(',',':'))+'\n')
        cases.append({'case':name,'file':out.name,'sha256':sha(out.read_bytes())})
    index={'cases':cases,'count':6,'actual_fixture_workers':1,'actual_adapter_wrapper_processes':1,
           'model_processes':0,'real_service_writes':0,'authority':'fictional decisions, not actual William approval'}
    (root/'index.json').write_text(json.dumps(index,indent=2));return index


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    print(json.dumps(run(parser.parse_args().output),indent=2))
