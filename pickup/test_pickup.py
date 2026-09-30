import copy
import json
import multiprocessing as mp
from pathlib import Path
import tempfile
import time
import unittest
from pickup import Queue, sha


def approval(key='BOOK-DEMO-1', now=1000, priority=1):
    return {'key': key, 'approval_id': 'authority-' + key,
            'authority_ref': 'fictional test approval; not real Jira authority',
            'definition_sha256': sha(b'definition'), 'request_sha256': sha(b'request'),
            'expires': now + 900, 'priority': priority}


def snapshot(key='BOOK-DEMO-1', now=1000, **changes):
    value = {'key': key, 'type': 'Task', 'status': 'Work Approved', 'owner': None,
             'definition_sha256': sha(b'definition'), 'read_at': now, 'blockers': []}
    value.update(changes)
    return value


def race_claim(db, owner, start, out):
    q = Queue(db); sid = q.reserve_scan(); start.wait(10)
    result = q.claim(sid, owner, [snapshot(now=time.time())])
    out.put(result)


def race_publish(db, token, owner, start, out):
    q = Queue(db); start.wait(10)
    try:
        r = q.begin_publication(token, 'publisher', snapshot(now=time.time(), status='In Progress', owner=owner))
        out.put({'published': True, 'sha': r['result_sha256']})
    except ValueError as e:
        out.put({'published': False, 'reason': str(e)})


class PickupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name) / 'queue.sqlite'
        self.now = 1000
        self.q = Queue.create(self.path, [approval()], publisher='publisher', clock=lambda: self.now)

    def tearDown(self): self.tmp.cleanup()

    def claim(self, **changes):
        return self.q.claim(self.q.reserve_scan(), 'worker-a', [snapshot(now=self.now, **changes)])

    def running(self):
        c = self.claim()['claim']; self.q.confirm_board_claim(c['token'], snapshot(now=self.now, status='In Progress', owner='worker-a'), 'fictional transition receipt'); return c

    def staged(self):
        c = self.running(); payload = b'one result'
        self.q.stage_result(c['token'], payload, {'result_sha256': sha(payload), 'request_sha256': sha(b'request'), 'review_sha256': sha(b'review'), 'disposition': 'READY_FOR_PUBLICATION', 'deterministic_checks_pass': True}); return c

    def test_fixed_queue_excludes_unknown(self):
        r = self.q.claim(self.q.reserve_scan(), 'a', [snapshot('OTHER')]); self.assertIsNone(r['claim'])

    def test_ineligible_statuses_and_container(self):
        for status in ['To Do', 'In Progress', 'In Review', 'Awaiting Acceptance', 'Done', 'Blocked']:
            with self.subTest(status=status): self.assertIsNone(self.claim(status=status)['claim'])
        for kind in ['Epic', 'Story', 'Unknown']:
            with self.subTest(kind=kind): self.assertIsNone(self.claim(type=kind)['claim'])

    def test_rework_is_eligible(self): self.assertIsNotNone(self.claim(status='Rework Required')['claim'])

    def test_blockers_must_be_done(self):
        for status in ['To Do', 'In Progress', 'Awaiting Acceptance', 'Rejected']:
            with self.subTest(status=status): self.assertIsNone(self.claim(blockers=[{'key':'P','status':status}])['claim'])
        self.assertIsNotNone(self.claim(blockers=[{'key':'P','status':'Done'}])['claim'])

    def test_owned_work_rejected(self): self.assertIsNone(self.claim(owner='someone')['claim'])

    def test_changed_definition_rejected(self): self.assertIsNone(self.claim(definition_sha256=sha(b'changed'))['claim'])

    def test_stale_and_future_snapshots_rejected(self):
        for read_at in [self.now - 16, self.now + 1]:
            with self.subTest(read_at=read_at): self.assertIsNone(self.claim(read_at=read_at)['claim'])

    def test_snapshot_must_follow_reservation(self):
        sid = self.q.reserve_scan(); self.now += 1
        self.assertIsNone(self.q.claim(sid, 'a', [snapshot(now=999.9)])['claim'])

    def test_scan_receipt_single_use(self):
        sid = self.q.reserve_scan(); self.q.claim(sid, 'a', [])
        with self.assertRaisesRegex(ValueError, 'scan_not_reserved'): self.q.claim(sid, 'a', [])

    def test_failed_and_abandoned_scans_count(self):
        for i in range(12):
            sid = self.q.reserve_scan()
            if i % 2: self.q.discard_scan(sid, 'synthetic service failure')
        with self.assertRaisesRegex(ValueError, 'scan_limit'): self.q.reserve_scan()
        self.assertEqual(len(Queue(self.path, clock=lambda:self.now).inspect()['scans']), 12)

    def test_expired_scan_consumed(self):
        sid = self.q.reserve_scan(); self.now += 16
        self.assertEqual(self.q.claim(sid,'a',[snapshot(now=self.now)])['reason'],'scan_expired')
        with self.assertRaises(ValueError): self.q.claim(sid,'a',[])

    def test_malformed_snapshots_consume_scan(self):
        for payload in [{}, [None], [snapshot(), snapshot()], [{'key':'X'}]]:
            with self.subTest(payload=payload):
                sid=self.q.reserve_scan();self.assertIsNone(self.q.claim(sid,'a',payload)['claim'])
                with self.assertRaises(ValueError):self.q.claim(sid,'a',[])

    def test_consumed_authority_survives_reopen(self):
        self.running(); self.now += 901; q=Queue(self.path,clock=lambda:self.now)
        r=q.claim(q.reserve_scan(),'new',[snapshot(now=self.now)]);self.assertIsNone(r['claim'])
        self.assertEqual(len(q.inspect()['claims']),1)

    def test_expired_authority_never_claimed(self):
        self.now+=901;self.assertIsNone(self.claim()['claim'])

    def test_board_claim_must_be_confirmed(self):
        c=self.claim()['claim']
        with self.assertRaises(ValueError):self.q.validate_launch(c['token'])
        with self.assertRaises(ValueError):self.q.confirm_board_claim(c['token'],snapshot(owner='worker-a'),'wrong status')
        with self.assertRaises(ValueError):self.q.confirm_board_claim(c['token'],snapshot(status='In Progress',owner='other'),'wrong owner')

    def test_late_worker_cannot_launch_or_stage(self):
        c=self.running();self.now+=901
        with self.assertRaisesRegex(ValueError,'stale_or_wrong_owner'):self.q.validate_launch(c['token'])
        proof={'result_sha256':sha(b'x'),'request_sha256':sha(b'request'),'review_sha256':sha(b'r'),'disposition':'READY_FOR_PUBLICATION','deterministic_checks_pass':True}
        with self.assertRaises(ValueError):self.q.stage_result(c['token'],b'x',proof)

    def test_bad_result_proof_rejected(self):
        c=self.running();p={'result_sha256':sha(b'x'),'request_sha256':sha(b'request'),'review_sha256':sha(b'r'),'disposition':'READY_FOR_PUBLICATION','deterministic_checks_pass':True}
        for key,value in [('result_sha256',sha(b'other')),('request_sha256',sha(b'other')),('review_sha256',''),('disposition','PASS'),('deterministic_checks_pass',False)]:
            with self.subTest(key=key):
                bad=dict(p);bad[key]=value
                with self.assertRaises(ValueError):self.q.stage_result(c['token'],b'x',bad)

    def test_only_designated_publisher(self):
        c=self.staged()
        with self.assertRaisesRegex(ValueError,'wrong_publisher'):self.q.begin_publication(c['token'],'worker-a',snapshot(status='In Progress',owner='worker-a'))

    def test_board_snapshot_cannot_predate_claim(self):
        c=self.claim()['claim'];self.now+=1
        with self.assertRaisesRegex(ValueError,'board_read_predates_claim'):self.q.confirm_board_claim(c['token'],snapshot(now=999.9,status='In Progress',owner='worker-a'),'old read')

    def test_publication_snapshot_cannot_predate_confirmation(self):
        c=self.staged();self.now+=1
        with self.assertRaisesRegex(ValueError,'publication_read_predates_board_claim'):self.q.begin_publication(c['token'],'publisher',snapshot(now=999.9,status='In Progress',owner='worker-a'))

    def test_expired_owner_cannot_publish_staged_result(self):
        c=self.staged();self.now+=901
        with self.assertRaisesRegex(ValueError,'stale_or_wrong_owner'):self.q.begin_publication(c['token'],'publisher',snapshot(now=self.now,status='In Progress',owner='worker-a'))

    def test_publish_rechecks_status_owner_definition_and_dependencies(self):
        c=self.staged()
        for changes in [{'status':'Blocked'},{'owner':'other'},{'definition_sha256':sha(b'changed')},{'blockers':[{'key':'P','status':'In Review'}]}]:
            with self.subTest(changes=changes):
                s=snapshot(status='In Progress',owner='worker-a');s.update(changes)
                with self.assertRaises(ValueError):self.q.begin_publication(c['token'],'publisher',s)

    def test_publication_once_and_replay_rejected(self):
        c=self.staged();s=snapshot(status='In Progress',owner='worker-a');dispatch=self.q.begin_publication(c['token'],'publisher',s)
        with self.assertRaises(ValueError):self.q.begin_publication(c['token'],'publisher',s)
        with self.assertRaises(ValueError):self.q.settle_publication(dispatch['dispatch_token'],'publisher',sha(b'wrong'),'bad readback')
        self.q.settle_publication(dispatch['dispatch_token'],'publisher',dispatch['result_sha256'],'fixture-file:/one/result')
        with self.assertRaises(ValueError):self.q.settle_publication(dispatch['dispatch_token'],'publisher',dispatch['result_sha256'],'repeat')
        self.assertEqual(self.q.inspect()['publications'][0]['state'],'verified')

    def test_crash_window_stays_unsettled(self):
        c=self.staged();self.q.begin_publication(c['token'],'publisher',snapshot(status='In Progress',owner='worker-a'))
        q=Queue(self.path,clock=lambda:self.now)
        self.assertEqual(q.inspect()['publications'][0]['state'],'unsettled')
        with self.assertRaises(ValueError):q.begin_publication(c['token'],'publisher',snapshot(status='In Progress',owner='worker-a'))

    def test_settlement_after_stop_records_effect_without_new_write(self):
        c=self.staged();d=self.q.begin_publication(c['token'],'publisher',snapshot(status='In Progress',owner='worker-a'));self.q.stop('manual')
        self.q.settle_publication(d['dispatch_token'],'publisher',d['result_sha256'],'fixture saved before stop')
        with self.assertRaises(ValueError):self.q.reserve_scan()

    def test_deadline_and_clock_rollback_remain_stopped(self):
        self.now=2300
        with self.assertRaisesRegex(ValueError,'deadline'):self.q.reserve_scan()
        self.now=1000
        with self.assertRaises(ValueError):self.q.reserve_scan()
        self.assertEqual(self.q.inspect()['policy']['stopped'],'queue_deadline')

    def test_creation_cannot_replace_existing_ledger(self):
        before=self.path.read_bytes()
        with self.assertRaises(FileExistsError):Queue.create(self.path,[approval()],publisher='p',clock=lambda:self.now)
        self.assertEqual(self.path.read_bytes(),before)

    def test_invalid_limits_and_duplicate_authority(self):
        for kw in [{'max_scans':0},{'max_claims':True},{'max_concurrent':3},{'seconds':float('nan')},{'freshness':31}]:
            with self.subTest(kw=kw):
                with self.assertRaises(ValueError):Queue.create(Path(self.tmp.name)/'invalid.sqlite',[approval()],publisher='p',clock=lambda:self.now,**kw)
        with self.assertRaises(ValueError):Queue.create(Path(self.tmp.name)/'duplicate.sqlite',[approval(),approval()],publisher='p',clock=lambda:self.now)

    def test_concurrency_and_claim_count_are_persistent(self):
        p=Path(self.tmp.name)/'multi.sqlite';q=Queue.create(p,[approval('A'),approval('B')],publisher='p',max_claims=1,max_concurrent=1,clock=lambda:self.now)
        r=q.claim(q.reserve_scan(),'a',[snapshot('A'),snapshot('B')]);self.assertIsNotNone(r['claim'])
        self.assertEqual(q.claim(q.reserve_scan(),'b',[snapshot('B')])['reason'],'claim_limit')
        self.assertEqual(len(Queue(p,clock=lambda:self.now).inspect()['claims']),1)

    def test_priority_and_concurrency(self):
        p=Path(self.tmp.name)/'priority.sqlite';q=Queue.create(p,[approval('A',priority=1),approval('B',priority=5)],publisher='p',max_claims=2,max_concurrent=1,clock=lambda:self.now)
        r=q.claim(q.reserve_scan(),'a',[snapshot('A'),snapshot('B')]);self.assertEqual(r['claim']['unit'],'B')
        self.assertEqual(q.claim(q.reserve_scan(),'b',[snapshot('A')])['reason'],'concurrency_limit')


class ActualProcessTests(unittest.TestCase):
    def test_real_process_contention(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'race.sqlite';now=time.time();q=Queue.create(path,[approval(now=now)],publisher='publisher',max_concurrent=2,max_claims=2)
            ctx=mp.get_context('spawn');start=ctx.Event();out=ctx.Queue()
            workers=[ctx.Process(target=race_claim,args=(str(path),'worker-'+str(i),start,out)) for i in range(2)]
            for p in workers:p.start()
            start.set();results=[out.get(timeout=20) for p in workers]
            for p in workers:p.join(20);self.assertEqual(p.exitcode,0)
            wins=[r['claim'] for r in results if r['claim']];self.assertEqual(len(wins),1)
            win=wins[0];s=snapshot(now=time.time(),status='In Progress',owner=win['owner']);q.confirm_board_claim(win['token'],s,'fictional board claim')
            data=b'one result';q.stage_result(win['token'],data,{'result_sha256':sha(data),'request_sha256':sha(b'request'),'review_sha256':sha(b'review'),'disposition':'READY_FOR_PUBLICATION','deterministic_checks_pass':True})
            start=ctx.Event();out=ctx.Queue();workers=[ctx.Process(target=race_publish,args=(str(path),win['token'],win['owner'],start,out)) for _ in range(2)]
            for p in workers:p.start()
            start.set();results=[out.get(timeout=20) for p in workers]
            for p in workers:p.join(20);self.assertEqual(p.exitcode,0)
            self.assertEqual(sum(r['published'] for r in results),1)
            self.assertEqual(len(q.inspect()['claims']),1);self.assertEqual(len(q.inspect()['publications']),1)


if __name__=='__main__':unittest.main(verbosity=2)
