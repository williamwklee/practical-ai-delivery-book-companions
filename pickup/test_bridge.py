from pathlib import Path
import json
import shutil
import tempfile
import unittest
from pickup import Queue, sha
from runner_bridge import stage_runner_output
from test_pickup import approval, snapshot


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source';shutil.copytree(Path(__file__).parent/'reference-evidence',self.source)
        ctx=json.loads((self.source/'context.json').read_text());a=approval();a['request_sha256']=ctx['request_sha256']
        self.q=Queue.create(self.root/'queue.sqlite',[a],publisher='publisher',clock=lambda:1000)
        self.claim=self.q.claim(self.q.reserve_scan(),'a',[snapshot()])['claim']
        self.q.confirm_board_claim(self.claim['token'],snapshot(status='In Progress',owner='a'),'fixture transition')

    def tearDown(self):self.tmp.cleanup()

    def test_actual_qualified_runner_output_is_bound_and_staged(self):
        p=stage_runner_output(self.q,self.claim['token'],self.source)
        self.assertEqual(self.q.inspect()['publications'][0]['result_sha'],p['result_sha256'])

    def test_stopped_runner_is_not_publishable(self):
        p=self.source/'state.json';s=json.loads(p.read_text());s['disposition']='STOP';p.write_text(json.dumps(s))
        with self.assertRaisesRegex(ValueError,'runner_not_ready'):stage_runner_output(self.q,self.claim['token'],self.source)

    def test_modified_candidate_is_rejected(self):
        p=self.source/'candidate-0.json';s=json.loads(p.read_text());s['summary']='changed';p.write_text(json.dumps(s))
        with self.assertRaisesRegex(ValueError,'candidate_changed'):stage_runner_output(self.q,self.claim['token'],self.source)

    def test_missing_review_is_rejected(self):
        (self.source/'events.json').write_text('[]')
        with self.assertRaisesRegex(ValueError,'review_missing'):stage_runner_output(self.q,self.claim['token'],self.source)

    def test_changed_request_is_rejected(self):
        p=self.source/'context.json';s=json.loads(p.read_text());s['spec']['original_request']='changed';p.write_text(json.dumps(s))
        with self.assertRaisesRegex(ValueError,'request_changed'):stage_runner_output(self.q,self.claim['token'],self.source)

    def test_checks_catch_wrong_facts_even_with_rebound_hash(self):
        p=self.source/'candidate-0.json';s=json.loads(p.read_text());s['comparison'][0]['seats']+=1;p.write_text(json.dumps(s))
        h=sha(json.dumps(s,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
        p=self.source/'state.json';s=json.loads(p.read_text());s['candidate_sha256']=h;p.write_text(json.dumps(s))
        p=self.source/'events.json';s=json.loads(p.read_text())
        for e in s:
            if e.get('message')=='Checker and exact-data checks preserved.':e['review']['candidate_sha256']=h
        p.write_text(json.dumps(s))
        with self.assertRaisesRegex(ValueError,'source_mismatch'):stage_runner_output(self.q,self.claim['token'],self.source)


if __name__=='__main__':unittest.main(verbosity=2)
