from pathlib import Path
import json
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from recover import Recovery, encoded, current_claim
from upstream.pickup import Queue, sha
from upstream.controller import Controller, Denied, policy


class Fixture:
    """Explicit synthetic authority; no Jira/Confluence/model service calls."""
    def __init__(self, root, *, resumed_writes=2, lease=30, launches=3):
        self.root=Path(root).resolve();self.now=time.time();self.owner='fictional-coordinator';self.unit='FICTIONAL-1'
        self.request=encoded({'work':'Compare three fictional venues for a workshop'})
        self.candidate=encoded({'choice':'Venue A','summary':'Fictional checked result'})
        self.review=encoded({'verdict':'PASS','findings':[]})
        self.approval={'key':self.unit,'approval_id':'fixture:approval','authority_ref':'SYNTHETIC, not William authority',
            'definition_sha256':sha(b'definition'),'request_sha256':sha(self.request),'expires':self.now+100,'priority':1}
        self.q=Queue.create(self.root/'queue.sqlite',[self.approval],publisher='fixture-publisher',
                            seconds=110,lease_seconds=lease,clock=lambda:self.now)
        self.b=Controller.create(self.root/'budget.sqlite','SYNTHETIC',self.owner,policy(self.now+110,launches=launches),
            {self.unit:{'parent':'@all','policy':policy(self.now+100,launches=launches)}},'fixture:budget',clock=lambda:self.now)
        self.b.start_cycle(self.unit,self.owner)
        claim=self.q.claim(self.q.reserve_scan(),self.owner,[self.snapshot(status='Work Approved',owner=None)])['claim']
        self.token=claim['token'];self.q.confirm_board_claim(self.token,self.snapshot(),'fixture:board receipt')
        self.r=Recovery.install(self.q,self.b,'fixture-publisher',maximum_resumed_writes=resumed_writes)
        self.target=self.root/'result.json'

    def snapshot(self,**changes):
        s={'key':self.unit,'type':'Task','status':'In Progress','owner':self.owner,
           'definition_sha256':sha(b'definition'),'read_at':self.now,'blockers':[]};s.update(changes);return s

    def stage(self, service=False):
        self.q.stage_result(self.token,self.candidate,{'result_sha256':sha(self.candidate),
            'request_sha256':sha(self.request),'review_sha256':sha(self.review),
            'disposition':'READY_FOR_PUBLICATION','deterministic_checks_pass':True})
        self.r.checkpoint(self.unit,self.request,self.candidate,self.review)
        if service:self.r.prepare_service(self.unit,'fixture:confluence-page-1',3,'fixture:atomic compare-and-set')
        else:self.r.prepare_file(self.unit,self.target)

    def reopen(self):
        self.q=Queue(self.root/'queue.sqlite',clock=lambda:self.now)
        self.b=Controller(self.root/'budget.sqlite',clock=lambda:self.now)
        self.r=Recovery(self.q,self.b,'fixture-publisher')

    def authority(self,operation,payload):
        return self.r.register_authority(self.unit,operation,payload,'SYNTHETIC: explicit fixture decision',self.now+10)

    def used(self):return self.b.snapshot()['scopes'][0]['used']['launches']


class RecoveryTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.f=Fixture(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def outage(self):raise ConnectionError('fictional record service unavailable after file write')

    def test_existing_file_after_record_outage_reconciles_without_duplicate(self):
        f=self.f;f.stage()
        with self.assertRaises(ConnectionError):f.r.publish_file(f.token,f.snapshot(),after_write=self.outage)
        self.assertEqual(f.target.read_bytes(),f.candidate);before=f.target.stat().st_mtime_ns
        f.reopen();self.assertEqual(f.q.inspect()['publications'][0]['state'],'unsettled')
        self.assertEqual(f.r.inspect_file(f.unit),'match');self.assertEqual(f.r.inspect_file(f.unit),'match')
        self.assertEqual(f.q.inspect()['publications'][0]['state'],'verified')
        self.assertEqual(f.target.stat().st_mtime_ns,before)
        self.assertEqual(f.r.inspect()['effects'][0]['writes'],1)
        with self.assertRaises(ValueError):f.r.publish_file(f.token,f.snapshot())

    def test_known_absence_resumes_once_with_current_claim(self):
        f=self.f;f.stage()
        with patch.object(Recovery,'_write_exclusive',side_effect=ConnectionError('fixture:before write')):
            with self.assertRaises(ConnectionError):f.r.publish_file(f.token,f.snapshot())
        f.reopen();self.assertEqual(f.r.resume_file(f.token,f.snapshot(),'fixture:local destination checked absent'),'match')
        self.assertEqual(f.r.inspect()['policy']['used'],1)
        self.assertEqual(f.r.inspect()['effects'][0]['writes'],2)

    def test_uncertain_read_does_not_resend(self):
        f=self.f;f.stage()
        with patch.object(Recovery,'_write_exclusive',side_effect=ConnectionError('fixture:outage')):
            with self.assertRaises(ConnectionError):f.r.publish_file(f.token,f.snapshot())
        with patch.object(Path,'exists',side_effect=OSError('fixture:inspection unavailable')):
            with self.assertRaisesRegex(ValueError,'uncertain'):f.r.resume_file(f.token,f.snapshot(),'fixture:network returned')
        self.assertEqual(f.r.inspect()['policy']['used'],0);self.assertFalse(f.target.exists())

    def test_partial_or_changed_file_is_never_overwritten(self):
        f=self.f;f.stage()
        def partial(target,payload):Path(target).write_bytes(payload[:7]);raise OSError('fixture:interruption')
        with patch.object(Recovery,'_write_exclusive',side_effect=partial):
            with self.assertRaises(OSError):f.r.publish_file(f.token,f.snapshot())
        f.reopen();self.assertEqual(f.r.inspect_file(f.unit),'conflict')
        with self.assertRaisesRegex(ValueError,'conflicting'):f.r.resume_file(f.token,f.snapshot(),'fixture:unchecked')
        self.assertEqual(f.target.read_bytes(),f.candidate[:7])

    def test_context_restores_every_review_including_unresolved_findings(self):
        f=self.f;bad=encoded({'verdict':'FAIL','findings':['Missing source']})
        f.r.checkpoint(f.unit,f.request,b'first draft',bad);f.stage();f.reopen();rows=f.r.restore(f.unit)
        self.assertEqual(len(rows),2);self.assertEqual(rows[0]['review'],bad);self.assertEqual(rows[1]['candidate'],f.candidate)
        with self.assertRaisesRegex(ValueError,'original_request'):f.r.checkpoint(f.unit,b'changed request',b'x',b'y')

    def test_corrupt_context_is_refused(self):
        f=self.f;f.stage()
        with sqlite3.connect(f.q.path) as c:c.execute("UPDATE recovery_context SET candidate=?",(b'changed',))
        with self.assertRaisesRegex(ValueError,'corrupt'):f.r.restore(f.unit)

    def test_expired_claim_requires_bound_renewal_and_old_token_stays_invalid(self):
        f=self.f;f.stage();f.now+=31
        with self.assertRaisesRegex(ValueError,'owner'):f.r.publish_file(f.token,f.snapshot())
        evidence='fixture:old process ended and effects checked';payload={'expires':f.now+20,'evidence':evidence}
        with self.assertRaisesRegex(ValueError,'authority'):f.r.renew_claim(f.unit,f.snapshot(),f.now+20,evidence,None)
        grant=f.authority('renew_claim',payload);old=f.token
        f.token=f.r.renew_claim(f.unit,f.snapshot(),f.now+20,evidence,grant)
        with self.assertRaisesRegex(ValueError,'owner'):current_claim(f.q,old,f.snapshot())
        with self.assertRaisesRegex(ValueError,'authority'):f.r.renew_claim(f.unit,f.snapshot(),f.now+20,evidence,grant)
        self.assertEqual(f.r.publish_file(f.token,f.snapshot()),'match');self.assertEqual(len(f.q.inspect()['claims']),1)

    def test_renewal_does_not_override_changed_owner_or_approved_window(self):
        f=self.f;f.stage();f.now+=31;ev='fixture:checked';g=f.authority('renew_claim',{'expires':f.now+20,'evidence':ev})
        with self.assertRaisesRegex(ValueError,'same_owner'):f.r.renew_claim(f.unit,f.snapshot(owner='other'),f.now+20,ev,g)
        with self.assertRaisesRegex(ValueError,'window'):f.r.renew_claim(f.unit,f.snapshot(),f.now+120,ev,g)

    def test_existing_effect_can_be_recorded_after_expiry_without_new_write(self):
        f=self.f;f.stage()
        with self.assertRaises(ConnectionError):f.r.publish_file(f.token,f.snapshot(),after_write=self.outage)
        f.now+=120;self.assertEqual(f.r.inspect_file(f.unit),'match')
        self.assertEqual(f.q.inspect()['publications'][0]['state'],'verified')

    def test_permission_error_needs_human_resume_authority(self):
        f=self.f;f.stage()
        with patch.object(Recovery,'_write_exclusive',side_effect=PermissionError('fixture')):
            with self.assertRaises(PermissionError):f.r.publish_file(f.token,f.snapshot())
        ev='fixture:permission corrected by operator'
        with self.assertRaisesRegex(ValueError,'authority'):f.r.resume_file(f.token,f.snapshot(),ev)
        g=f.authority('resume_write',{'evidence':ev})
        self.assertEqual(f.r.resume_file(f.token,f.snapshot(),ev,grant=g),'match')
        self.assertEqual(f.r.inspect()['grants'][0]['used'],1)

    def test_resumed_write_rechecks_status_definition_dependencies_owner_and_freshness(self):
        f=self.f;f.stage()
        with patch.object(Recovery,'_write_exclusive',side_effect=ConnectionError('fixture')):
            with self.assertRaises(ConnectionError):f.r.publish_file(f.token,f.snapshot())
        for change in [{'status':'Done'},{'owner':'another'},{'definition_sha256':sha(b'x')},
                       {'blockers':[{'key':'P','status':'In Review'}]},{'read_at':f.now-20}]:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):f.r.resume_file(f.token,f.snapshot(**change),'fixture:resolved')
        self.assertEqual(f.r.inspect()['policy']['used'],0)

    def test_recovery_count_survives_restart_and_stops_retrying(self):
        f=self.f;f.stage()
        with patch.object(Recovery,'_write_exclusive',side_effect=ConnectionError('fixture:persistent outage')):
            with self.assertRaises(ConnectionError):f.r.publish_file(f.token,f.snapshot())
            for _ in range(2):
                f.reopen()
                with self.assertRaises(ConnectionError):f.r.resume_file(f.token,f.snapshot(),'fixture:inspection found local absence')
            f.reopen()
            with self.assertRaisesRegex(ValueError,'recovery_write_limit'):f.r.resume_file(f.token,f.snapshot(),'fixture:again')
        self.assertEqual(f.r.inspect()['policy']['used'],2);self.assertEqual(f.r.inspect()['effects'][0]['writes'],3)

    def test_budget_pending_stop_and_deadline_prevent_new_effects(self):
        f=self.f;f.stage();r=f.b.reserve(f.unit,f.owner,'producer','draft','fixture:pending')
        with self.assertRaisesRegex(ValueError,'reconcile'):f.r.publish_file(f.token,f.snapshot())
        f.b.reconcile(r['id'],f.owner,{'process_started':False,'effects_checked':True,'reference':'fixture:no launch'})
        f.b.stop(f.unit,f.owner,'manual','fixture:operator stop')
        with self.assertRaisesRegex(ValueError,'stopped'):f.r.publish_file(f.token,f.snapshot())
        self.assertEqual(f.used(),1);self.assertFalse(f.target.exists())

    def test_consumed_model_budget_and_approval_never_replenish(self):
        f=self.f
        for _ in range(3):
            r=f.b.reserve(f.unit,f.owner,'producer','draft','fixture')
            f.b.settle(r['id'],f.owner,{'process_ended':True,'terminal_valid':True,'exit_code':0,
              'result_sha256':sha(b'output'),'binding':'fixture','observed_tokens':1})
        f.stage();self.assertEqual(f.r.publish_file(f.token,f.snapshot()),'match')
        self.assertEqual(f.q.inspect()['claims'][0]['state'],'delivered')
        f.reopen()
        with self.assertRaisesRegex(Denied,'launches_exhausted'):f.b.reserve(f.unit,f.owner,'producer','draft','renamed')
        self.assertEqual(f.used(),3)
        new=f.q.claim(f.q.reserve_scan(),'new-worker',[f.snapshot(status='Work Approved',owner=None)])
        self.assertIsNone(new['claim']);self.assertEqual(len(f.q.inspect()['claims']),1)
        self.assertEqual(new['reason'],'nothing_eligible')
        self.assertEqual(new['details'][f.unit],'authority_consumed')

    def test_copied_queue_or_reinstalled_journal_does_not_reset(self):
        f=self.f;new=f.root/'copy.sqlite';shutil.copyfile(f.q.path,new)
        with self.assertRaisesRegex(ValueError,'original_stores'):Recovery(Queue(new,clock=lambda:f.now),f.b,'fixture-publisher')
        with self.assertRaisesRegex(ValueError,'already_installed'):Recovery.install(f.q,f.b,'fixture-publisher')

    def test_symlink_destination_is_refused(self):
        f=self.f;f.stage();f.target.symlink_to(f.root/'elsewhere')
        self.assertEqual(f.r.inspect_file(f.unit),'conflict')

    def service_read(self,version=3,**changes):
        o={'target':'fixture:confluence-page-1','version':version,'read_at':self.f.now,'receipt':'fixture:authoritative service read'}
        o.update(changes);return o

    def test_service_lost_reply_reconciles_existing_result_without_resend(self):
        f=self.f;f.stage(service=True)
        intent=f.r.service_intent(f.token,f.snapshot(),self.service_read())
        # Simulated service applied the exact conditional update, then reply lost.
        f.reopen();o=self.service_read(4,operation_marker=intent['operation_marker'],result_sha256=intent['result_sha256'])
        self.assertEqual(f.r.observe_service(f.unit,o),'match')
        self.assertEqual(f.r.observe_service(f.unit,o),'match')
        self.assertEqual(f.r.inspect()['services'][0]['writes'],1)
        with self.assertRaises(ValueError):f.r.service_intent(f.token,f.snapshot(),self.service_read(),resumed=True)

    def test_conditional_retry_cannot_apply_twice_if_first_write_arrives_late(self):
        f=self.f;f.stage(service=True)
        first=f.r.service_intent(f.token,f.snapshot(),self.service_read())
        f.reopen();retry=f.r.service_intent(f.token,f.snapshot(),self.service_read(),resumed=True)
        service={'version':3,'applied':0}
        def apply(intent):
            if service['version']!=intent['expected_version']:return 'version conflict'
            service.update(version=4,applied=service['applied']+1);return 'saved'
        self.assertEqual(apply(retry),'saved');self.assertEqual(apply(first),'version conflict')
        self.assertEqual(service['applied'],1)
        self.assertEqual(f.r.inspect()['policy']['used'],1)
        self.assertEqual(f.r.observe_service(f.unit,self.service_read(4,operation_marker=retry['operation_marker'],result_sha256=retry['result_sha256'])),'match')

    def test_changed_or_unknown_service_state_never_retries(self):
        f=self.f;f.stage(service=True);f.r.service_intent(f.token,f.snapshot(),self.service_read())
        for o in [None,self.service_read(4),self.service_read(3,read_at=f.now-20),self.service_read(3,target='wrong')]:
            with self.assertRaises(ValueError):f.r.service_intent(f.token,f.snapshot(),o,resumed=True)
        self.assertEqual(f.r.inspect()['policy']['used'],0)
        self.assertEqual(f.r.observe_service(f.unit,self.service_read(4,result_sha256=sha(b'other'),operation_marker='wrong')),'conflict')
        with self.assertRaisesRegex(ValueError,'manual'):f.r.service_intent(f.token,f.snapshot(),self.service_read(),resumed=True)

    def test_service_retries_share_finite_recovery_budget(self):
        f=self.f;f.stage(service=True);f.r.service_intent(f.token,f.snapshot(),self.service_read())
        for _ in range(2):f.reopen();f.r.service_intent(f.token,f.snapshot(),self.service_read(),resumed=True)
        f.reopen()
        with self.assertRaisesRegex(ValueError,'recovery_write_limit'):f.r.service_intent(f.token,f.snapshot(),self.service_read(),resumed=True)
        self.assertEqual(f.r.inspect()['policy']['used'],2)

    def test_service_recovery_checks_current_ticket_before_dispatch(self):
        f=self.f;f.stage(service=True);f.r.service_intent(f.token,f.snapshot(),self.service_read())
        for changes in [{'status':'Blocked'},{'owner':'new'},{'definition_sha256':sha(b'new definition')},
                        {'blockers':[{'key':'X','status':'In Review'}]}]:
            with self.assertRaises(ValueError):f.r.service_intent(f.token,f.snapshot(**changes),self.service_read(),resumed=True)
        self.assertEqual(f.r.inspect()['services'][0]['writes'],1)


if __name__=='__main__':unittest.main(verbosity=2)
