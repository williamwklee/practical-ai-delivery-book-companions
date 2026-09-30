import json, tempfile, threading, time, unittest
from pathlib import Path
import sys
from unittest.mock import patch
import adapter
from adapter import run, parse_events, digest

ROOT = Path(__file__).resolve().parent
RESULTS = []

class Qualification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='book-adapter-test-')
        self.root = Path(self.tmp.name)
        self.prompt = self.root/'prompt.txt'
        self.prompt.write_text('Synthetic public venue selection fixture.\n')
    def tearDown(self):self.tmp.cleanup()
    def invoke(self, case, provider='codex', **kw):
        defaults={'timeout':1.2,'first_output_timeout':.35,'idle_timeout':.4,'grace':.12,'max_output_bytes':100_000}
        defaults.update(kw)
        record=run([sys.executable,str(ROOT/'fixture_worker.py'),case],str(self.prompt),str(self.root/case),provider,
                   'claude-opus-5' if provider=='claude' else 'gpt-6-astra','producer','DEV-74',synthetic=True,**defaults)
        RESULTS.append({'case':case,**record})
        return record
    def test_codex_complete_and_eof(self):
        self.assertTrue(self.invoke('codex_ok')['success'])
        self.assertTrue(self.invoke('stdin_eof')['success'])
    def test_claude_continuation_and_thinking_separation(self):
        r=self.invoke('claude_ok','claude');self.assertTrue(r['success'])
        final=(self.root/'claude_ok/final.txt').read_text()
        self.assertEqual(final,'First part.\nContinuation.')
        self.assertNotIn('PRIVATE_SYNTHETIC_THINKING',json.dumps(r)+final)
    def test_failure_private_diagnostics(self):
        r=self.invoke('failure');self.assertFalse(r['success']);self.assertEqual(r['exit_code'],7)
        self.assertNotIn('FAKE_SECRET_MUST_STAY_PRIVATE',json.dumps(r))
        self.assertEqual((self.root/'failure/private/stderr.bin').stat().st_mode & 0o777,0o600)
    def test_incomplete_malformed_and_empty(self):
        for case in ['truncated','malformed','empty']:
            with self.subTest(case=case):self.assertFalse(self.invoke(case)['success'])
    def test_silent_input_wait(self):
        r=self.invoke('stdin_wait');self.assertEqual(r['stop_reason'],'no_output_limit');self.assertFalse(r['success'])
    def test_idle_after_first_event(self):
        r=self.invoke('idle');self.assertEqual(r['stop_reason'],'idle_output_limit')
    def test_elapsed_even_when_output_continues(self):
        r=self.invoke('progress_forever',timeout=.65);self.assertEqual(r['stop_reason'],'elapsed_limit')
    def test_flood(self):
        r=self.invoke('flood',max_output_bytes=4096);self.assertEqual(r['stop_reason'],'output_bytes_limit')
        self.assertLessEqual(sum(r['captured_bytes'].values()),4096)
    def test_continuously_ready_flood_cannot_starve_elapsed(self):
        # A real unsleeping flood keeps the pipe readable. Force legal short
        # reads so the byte cap cannot mask elapsed checking on a fast host.
        original_read=adapter.os.read
        with patch.object(adapter.os,'read',side_effect=lambda fd,n:original_read(fd,min(n,64))):
            r=self.invoke('flood',timeout=.15,first_output_timeout=.15,
                          idle_timeout=.15,max_output_bytes=8_000_000)
        self.assertEqual(r['stop_reason'],'elapsed_limit')
        self.assertLess(sum(r['observed_bytes'].values()),8_000_000)
        self.assertEqual(r['live_process_group_survivors'],[])
    def test_continuously_ready_flood_cannot_starve_cancel(self):
        original_read=adapter.os.read;cancel=self.root/'STOP'
        timer=threading.Timer(.15,lambda:cancel.touch());timer.start()
        try:
            with patch.object(adapter.os,'read',side_effect=lambda fd,n:original_read(fd,min(n,64))):
                r=self.invoke('flood',cancel_path=str(cancel),timeout=2,
                              first_output_timeout=1,idle_timeout=1,max_output_bytes=8_000_000)
        finally:timer.join()
        self.assertEqual(r['stop_reason'],'cancelled')
        self.assertLess(sum(r['observed_bytes'].values()),8_000_000)
        self.assertEqual(r['live_process_group_survivors'],[])
    def test_process_inspection_failure_remains_unknown(self):
        # Real process still terminates; only inspection is fault-injected.
        with patch.object(adapter,'group_members',side_effect=RuntimeError('seeded inspection failure')):
            r=self.invoke('codex_ok')
        self.assertFalse(r['success'])
        self.assertEqual(r['live_process_group_survivors'],['inspection_failed'])
        self.assertEqual(r['stop_reason'],'process_control_error')
    def test_cancel_stubborn_group(self):
        cancel=self.root/'cancel'
        timer=threading.Timer(.3,lambda:cancel.touch());timer.start()
        try:r=self.invoke('child_ignores_term',cancel_path=str(cancel),timeout=3,first_output_timeout=1,idle_timeout=2)
        finally:timer.join()
        self.assertEqual(r['stop_reason'],'cancelled');self.assertIn('SIGKILL',r['termination_signals'])
        self.assertEqual(r['live_process_group_survivors'],[])
    def test_subscription_missing_reservation_rejected_before_launch(self):
        with self.assertRaisesRegex(ValueError,'durable_launch_reservation_required'):
            run(['/does/not/exist'],str(self.prompt),str(self.root/'must-not-exist'),'codex','gpt-6-astra','producer','DEV-74')
        self.assertFalse((self.root/'must-not-exist').exists())
    def test_reuse_rejected(self):
        self.invoke('codex_ok')
        with self.assertRaises(FileExistsError):self.invoke('codex_ok')
    def test_reservation_cannot_launch_again_in_new_directory(self):
        argv=['/does/not/exist']
        receipt=self.root/'reservation.json'
        context={k:'fixture-only' for k in ['run_id','attempt_id','candidate_version',
                 'candidate_sha256','authenticated_principal','cli_version','effort','profile_sha256']}
        receipt.write_text(json.dumps({'reserved':True,'mode':'consumer_subscription','unit':'DEV-74',
            'run_id':context['run_id'],'argv_sha256':digest(json.dumps(argv).encode()),
            'prompt_sha256':digest(self.prompt.read_bytes()),'expires_unix':time.time()+5}))
        a=run(argv,str(self.prompt),str(self.root/'first'),'codex','gpt-6-astra','producer','DEV-74',
              context=context,launch_receipt=str(receipt))
        self.assertFalse(a['success'])
        with self.assertRaises(FileExistsError):
            run(argv,str(self.prompt),str(self.root/'second'),'codex','gpt-6-astra','producer','DEV-74',
                context=context,launch_receipt=str(receipt))
        self.assertFalse((self.root/'second').exists())
    def test_cached_tokens_cannot_exceed_input(self):
        e=b'{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":2,"output_tokens":1}}'
        self.assertFalse(parse_events(e,'codex','gpt-6-astra',True)['usage_valid'])
    def test_nested_malformed_objects_are_classified(self):
        for provider,event in [('codex',{'type':'item.completed','item':None}),
             ('claude',{'type':'assistant','message':None}),
             ('claude',{'type':'assistant','message':{'model':[], 'content':[]}}),
             ('claude',{'type':'assistant','message':{'model':'claude-opus-5','content':[None]}})]:
            with self.subTest(event=event):
                self.assertGreater(parse_events(json.dumps(event).encode(),provider,'claude-opus-5',True)['malformed_lines'],0)
    def test_launch_failure_has_durable_record(self):
        result=run(['/does/not/exist'],str(self.prompt),str(self.root/'failed-launch'),
                   'codex','gpt-6-astra','producer','DEV-74',synthetic=True)
        self.assertFalse(result['success']);self.assertEqual(result['process_error_type'],'FileNotFoundError')
        self.assertEqual(json.loads((self.root/'failed-launch/run.json').read_text()),result)
        self.assertFalse(result['hard_token_or_dollar_cap_claimed'])
        RESULTS.append({'case':'failed_launch',**result})
    def test_claude_usage_requires_finite_integer(self):
        for number in [float('inf'),1.5,True]:
            e={'type':'result','subtype':'success','is_error':False,'modelUsage':{'claude-opus-5':
               {'inputTokens':number,'outputTokens':1,'cacheReadInputTokens':0,'cacheCreationInputTokens':0}}}
            self.assertFalse(parse_events(json.dumps(e).encode(),'claude','claude-opus-5',True)['usage_valid'])
    def test_record_identity_is_complete_and_unique(self):
        a=self.invoke('codex_ok');b=self.invoke('stdin_eof')
        for field in ['run_id','attempt_id']:
            self.assertNotEqual(a[field],b[field])
        for field in ['candidate_sha256','authenticated_principal','cli_version','effort','ended_utc']:
            self.assertTrue(a[field])

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Qualification)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    (ROOT/'offline-qualification.json').write_text(json.dumps({'kind':'synthetic process tests; no live model calls',
        'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'success':result.wasSuccessful(),'records':RESULTS},indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
