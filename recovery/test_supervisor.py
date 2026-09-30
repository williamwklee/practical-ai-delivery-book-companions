from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from supervisor import launch, recover_result
from test_recovery import Fixture
from upstream.controller import Denied


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.f=Fixture(self.temp.name)
        self.prompt=self.f.root/'prompt.txt';self.prompt.write_text('Synthetic process fixture only.')

    def tearDown(self):self.temp.cleanup()

    def start(self,case='codex_ok',folder='attempt',**kwargs):
        f=self.f
        return launch(f.q,f.b,f.owner,f.token,f.snapshot(),'producer','draft',self.prompt,f.root/folder,
            [sys.executable,str(Path(__file__).parent/'upstream/fixture_worker.py'),case],
            'codex','fixture-model',{},synthetic=True,timeout=2,**kwargs)

    def finish(self,folder='attempt'):
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            r=recover_result(self.f.b,self.f.owner,self.f.root/folder)
            if r['state']!='still_running':return r
            time.sleep(.05)
        self.fail('bounded fixture did not finish')

    def test_restart_observes_same_process_and_settles_once(self):
        self.start();self.f.reopen();r=self.finish()
        self.assertEqual(r['state'],'settled');self.assertEqual(self.f.used(),1)
        self.assertEqual(self.finish()['state'],'already_settled');self.assertEqual(self.f.used(),1)

    def test_surviving_process_blocks_new_model_launch(self):
        self.start('idle')
        self.assertEqual(recover_result(self.f.b,self.f.owner,self.f.root/'attempt')['state'],'still_running')
        with self.assertRaisesRegex(Denied,'unsettled_attempt'):self.start(folder='renamed')
        result=self.finish();self.assertEqual(result['state'],'unsettled')
        self.assertEqual(result['process_record']['live_process_group_survivors'],[])
        self.assertEqual(self.f.used(),1)

    def test_cli_exit_failure_retains_partial_evidence_and_requires_manual_resolution(self):
        self.start('failure');r=self.finish();self.assertEqual(r['state'],'unsettled')
        self.assertTrue((self.f.root/'attempt/run/run.json').exists())
        self.assertTrue(any(x['active'] and x['reason']=='missing_evidence' for x in self.f.b.snapshot()['stops']))

    def test_missing_process_receipt_is_not_assumed_safe(self):
        self.start();self.finish();p=self.f.root/'attempt/process.json';p.unlink()
        # A settled result is idempotently inspectable. A genuinely pending case
        # has a distinct fixture, not a reset of its consumed slot.
        self.assertEqual(self.finish()['state'],'already_settled')
        self.start('idle',folder='pending');(self.f.root/'pending/process.json').rename(self.f.root/'retained-marker.json')
        with self.assertRaisesRegex(ValueError,'receipt_missing'):recover_result(self.f.b,self.f.owner,self.f.root/'pending')
        (self.f.root/'retained-marker.json').rename(self.f.root/'pending/process.json')
        self.finish('pending')

    def test_actual_parent_exit_leaves_bounded_adapter_recoverable(self):
        f=self.f
        # A child coordinator opens the SAME stores and exits immediately after
        # durable launch, while its adapter continues under the original timeout.
        spec={'queue':str(f.q.path),'budget':str(f.b.path),'token':f.token,'owner':f.owner,
              'snapshot':f.snapshot(),'prompt':str(self.prompt),'folder':str(f.root/'attempt')}
        p=f.root/'private-parent.json';p.write_text(json.dumps(spec));p.chmod(0o600)
        code="""import json,os,sys
from pathlib import Path
from supervisor import launch
from upstream.pickup import Queue
from upstream.controller import Controller
d=json.loads(Path(sys.argv[1]).read_text())
launch(Queue(d['queue']),Controller(d['budget']),d['owner'],d['token'],d['snapshot'],'producer','draft',d['prompt'],d['folder'],[sys.executable,'-c',\"import time,json;time.sleep(.4);print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Synthetic recovered result'}}));print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))\"],'codex','fixture-model',{},synthetic=True,timeout=2)
os._exit(17)
"""
        proc=subprocess.run([sys.executable,'-c',code,str(p)],cwd=Path(__file__).parent,capture_output=True,timeout=5)
        self.assertEqual(proc.returncode,17,proc.stderr.decode())
        # Advance the synthetic clock past the actual child's recorded high water.
        f.now=time.time();f.reopen();r=self.finish()
        self.assertEqual(r['state'],'settled');self.assertEqual(f.used(),1)
        self.assertEqual(r['process_record']['live_process_group_survivors'],[])


if __name__=='__main__':unittest.main(verbosity=2)
