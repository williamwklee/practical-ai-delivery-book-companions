"""Bounded offline exercise of the existing qualified pickup component.
No model process, service client, Jira approval or live dispatcher is created.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,time
def digest(b):return hashlib.sha256(b).hexdigest()
def run(module,source,out):
 out=Path(out);out.mkdir(parents=True,exist_ok=False)
 spec=importlib.util.spec_from_file_location('qualified_pickup',module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 data=Path(source).read_bytes();request=digest(data);now=time.time()
 definition={'key':'FICTIONAL-RESEARCH-1','purpose':'Explain whether one wet month establishes a wetter local climate','source_pack_sha256':request,'scope':'OFFLINE illustrative research pickup only'}
 definition_hash=digest(json.dumps(definition,sort_keys=True).encode())
 approval={'key':definition['key'],'approval_id':'fixture-only-research-approval','authority_ref':'SYNTHETIC fixture, not approval for a real Jira task','definition_sha256':definition_hash,'request_sha256':request,'expires':now+120,'priority':1}
 q=m.Queue.create(out/'private-queue.sqlite',[approval],publisher='fixture-publisher',max_scans=3,max_claims=1,max_concurrent=1,seconds=120,lease_seconds=100,freshness=15)
 def snapshot(blocker,status='Work Approved',owner=None):
  return {'key':definition['key'],'type':'Task','status':status,'owner':owner,'definition_sha256':definition_hash,'read_at':time.time(),'blockers':[{'key':'FICTIONAL-SOURCE-PACK','status':blocker}]}
 scan=q.reserve_scan();blocked=q.claim(scan,'fixture-research-worker',[snapshot('In Progress')])
 assert blocked['claim'] is None and blocked['details'][definition['key']]=='unfinished_or_unknown_dependency'
 # Source file really exists and hashes are checked locally; task statuses below are still fixtures.
 assert digest(Path(source).read_bytes())==request
 scan=q.reserve_scan();chosen=q.claim(scan,'fixture-research-worker',[snapshot('Done')]);assert chosen['claim']['unit']==definition['key']
 claim=chosen['claim'];q.confirm_board_claim(claim['token'],snapshot('Done','In Progress',claim['owner']),'OFFLINE FIXTURE: simulated Jira transition/readback; no service write')
 launch=q.validate_launch(claim['token'])
 q.stop('offline research pickup demonstration complete; no AI or service writes admitted')
 stopped=None
 try:q.reserve_scan()
 except ValueError as e:stopped=str(e)
 assert stopped and stopped.startswith('queue_stopped:')
 state=q.inspect();assert len(state['scans'])==2 and len(state['claims'])==1
 proof={'scope':'fresh local fixture demonstration using unchanged accepted pickup.py; no live Jira reads/claims, no unattended research run','synthetic_service_statuses':True,'new_AI_processes':0,'real_service_writes':0,'component_sha256':digest(Path(module).read_bytes()),'source_pack_sha256':request,'definition':definition,'blocked_first_scan':blocked,'second_scan_selected':claim['unit'],'local_claim_owners':1,'fixture_launch_authorisation':launch,'launch_performed':False,'stop_refusal':stopped,'scans_used':2,'scan_limit':3,'claim_limit':1,'time_limit_seconds':120,'state':state}
 (out/'pickup-result.json').write_text(json.dumps(proof,indent=2))
 return {k:proof[k] for k in ['scope','new_AI_processes','real_service_writes','second_scan_selected','stop_refusal','scans_used']}
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--module',default=str(Path(__file__).parent/'reference/pickup.py'));p.add_argument('--source',default=str(Path(__file__).parent/'reference/source-pack.json'));p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(run(a.module,a.source,a.output)))

