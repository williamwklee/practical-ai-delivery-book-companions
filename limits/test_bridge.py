from pathlib import Path
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from controller import Controller, Denied, policy
from adapter_bridge import run_guarded, manual_stop


class BridgeCases(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.owner = 'fixture-owner'
        self.c = Controller.create(self.root / 'controller.sqlite', 'fixture', self.owner,
            policy(time.time() + 60, launches=3),
            {'FIXTURE': {'parent': '@all', 'policy': policy(time.time() + 50, launches=1)}},
            'fixture:initial-authority')
        self.c.start_cycle('FIXTURE', self.owner)
        self.prompt = self.root / 'prompt.txt'; self.prompt.write_text('Synthetic demonstration only.')

    def tearDown(self): self.temp.cleanup()

    def run_case(self, name='codex_ok', folder='attempt', **changes):
        args = dict(controller=self.c, unit='FIXTURE', owner=self.owner, role='producer', action='draft',
                    prompt=self.prompt, folder=self.root / folder,
                    argv=[sys.executable, str(Path(__file__).parent / 'upstream/fixture_worker.py'), name],
                    provider='codex', model='fixture-model', context={}, synthetic=True,
                    claim_deadline=time.time() + 20, timeout=3, first_output_timeout=1, idle_timeout=1)
        args.update(changes)
        return run_guarded(**args)

    def test_actual_subprocess_then_budget_refusal_before_second_process(self):
        result = self.run_case()
        self.assertEqual(result['settlement_state'], 'settled')
        self.assertTrue(result['process_record']['success'])
        self.assertEqual(result['result']['observed_tokens'], 110)
        with self.assertRaisesRegex(Denied, 'launches_exhausted'): self.run_case(folder='renamed-attempt')
        self.assertFalse((self.root / 'renamed-attempt').exists())
        self.assertEqual(len(self.c.snapshot()['attempts']), 1)

    def test_missing_terminal_stops_and_keeps_partial_result(self):
        result = self.run_case('truncated')
        self.assertEqual(result['settlement_state'], 'unsettled')
        self.assertIn('PASS', (self.root / 'attempt/run/final.txt').read_text())
        self.assertTrue(any(x['reason'] == 'missing_evidence' and x['active'] for x in self.c.snapshot()['stops']))

    def test_optional_usage_missing_does_not_invent_model_failure(self):
        code = "import json;print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Synthetic complete output'}}));print(json.dumps({'type':'turn.completed'}))"
        result = self.run_case(argv=[sys.executable, '-c', code])
        self.assertFalse(result['process_record']['success'])  # historical adapter requires usage
        self.assertTrue(result['result']['terminal_valid'])
        self.assertIsNone(result['result']['observed_tokens'])
        self.assertEqual(result['settlement_state'], 'settled')
        self.assertFalse(self.c.snapshot()['stops'])

    def test_actual_timeout_keeps_reservation_and_stops(self):
        result = self.run_case('progress_forever', timeout=.5, first_output_timeout=.5, idle_timeout=.5)
        self.assertEqual(result['process_record']['stop_reason'], 'elapsed_limit')
        self.assertEqual(result['process_record']['live_process_group_survivors'], [])
        self.assertEqual(result['settlement_state'], 'unsettled')

    def test_manual_stop_signals_running_process_and_persists(self):
        timer = threading.Timer(.15, lambda: manual_stop(self.c, 'FIXTURE', self.owner, 'fixture:user stop'))
        timer.start()
        try: result = self.run_case('progress_forever')
        finally: timer.join(2)
        self.assertEqual(result['process_record']['stop_reason'], 'cancelled')
        self.assertEqual(result['process_record']['live_process_group_survivors'], [])
        self.assertTrue(self.c.path.with_suffix('.STOP').is_file())
        self.assertTrue(any(x['active'] and x['reason'] == 'manual' for x in self.c.snapshot()['stops']))

    def test_expired_claim_refuses_before_reserving(self):
        with self.assertRaisesRegex(ValueError, 'expired_claim'): self.run_case(claim_deadline=time.time() - 1)
        self.assertFalse(self.c.snapshot()['attempts'])

    def test_existing_directory_is_consumed_without_a_false_process_end(self):
        (self.root / 'attempt').mkdir()
        with self.assertRaises(FileExistsError): self.run_case()
        state = self.c.snapshot(); attempt = state['attempts'][0]
        self.assertEqual(attempt['state'], 'abandoned_before_launch')
        outcome = json.loads(attempt['result'])
        self.assertIs(outcome['process_started'], False)
        self.assertNotIn('process_ended', outcome)
        self.assertEqual(next(s for s in state['scopes'] if s['id'] == 'FIXTURE')['used']['launches'], 1)
        self.assertFalse((self.root / 'attempt/run').exists())
        stop = next(s for s in state['stops'] if s['active'])
        with self.assertRaisesRegex(Denied, 'limits_still_exhausted'):
            self.c.resume(stop['id'], self.owner, 'fixture:folder problem resolved')

    def test_prelaunch_failure_can_resume_within_unchanged_remaining_allowance(self):
        # Distinct, explicitly admitted fixture with room for one charged retry.
        self.c = Controller.create(self.root / 'retry-fixture.sqlite', 'fixture2', self.owner,
            policy(time.time() + 60), {'FIXTURE': {'parent': '@all', 'policy': policy(time.time() + 50, launches=2)}},
            'fixture:bounded retry demonstration')
        self.c.start_cycle('FIXTURE', self.owner)
        (self.root / 'attempt').mkdir()
        with self.assertRaises(FileExistsError): self.run_case()
        stop = next(s for s in self.c.snapshot()['stops'] if s['active'])
        self.c.resume(stop['id'], self.owner, 'fixture:use a verified new output folder')
        result = self.run_case(folder='verified-new-folder', role='retry')
        self.assertTrue(result['process_record']['success'])
        used = next(s for s in self.c.snapshot()['scopes'] if s['id'] == 'FIXTURE')['used']
        self.assertEqual((used['launches'], used['retries']), (2, 1))

    def test_permission_failure_requires_authority_even_with_retry_room(self):
        self.c = Controller.create(self.root / 'permission-fixture.sqlite', 'permission-fixture', self.owner,
            policy(time.time() + 60), {'FIXTURE': {'parent': '@all', 'policy': policy(time.time() + 50, launches=2)}},
            'fixture:permission failure demonstration')
        self.c.start_cycle('FIXTURE', self.owner)
        with patch.object(Path, 'mkdir', side_effect=PermissionError('fixture:denied')):
            with self.assertRaises(PermissionError): self.run_case()
        state = self.c.snapshot(); stop = next(s for s in state['stops'] if s['active'])
        self.assertEqual(stop['reason'], 'out_of_authority')
        self.assertIs(json.loads(state['attempts'][0]['result'])['process_started'], False)
        with self.assertRaisesRegex(Denied, 'authority'):
            self.c.resume(stop['id'], self.owner, 'fixture:permissions corrected')

    def test_checker_without_classification_stops_instead_of_assuming_no_findings(self):
        result = self.run_case(role='checker')
        self.assertNotIn('findings_fingerprint', result['result'])
        self.assertTrue(any(s['active'] and s['reason'] == 'missing_evidence' for s in self.c.snapshot()['stops']))

    def test_classifier_reads_completed_output_and_repeated_findings_stop(self):
        self.c = Controller.create(self.root / 'classification-fixture.sqlite', 'classification-fixture', self.owner,
            policy(time.time() + 60), {'FIXTURE': {'parent': '@all', 'policy': policy(time.time() + 50, launches=2)}},
            'fixture:post-result classification demonstration')
        self.c.start_cycle('FIXTURE', self.owner)
        seen = []
        def classify(final, record):
            self.assertTrue(final.strip())
            self.assertEqual(record['exit_code'], 0)
            seen.append(record['run_id'])
            return 'a' * 64  # Explicit synthetic finding, not an AI judgement.
        self.run_case(role='checker', findings_classifier=classify)
        self.run_case(role='checker', folder='review-two', findings_classifier=classify)
        self.assertEqual(len(set(seen)), 2)
        self.assertTrue(any(s['active'] and s['reason'] == 'no_progress' for s in self.c.snapshot()['stops']))


if __name__ == '__main__': unittest.main(verbosity=2)
