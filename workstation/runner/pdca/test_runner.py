"""Offline control tests. Fake responses are never represented as AI evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from runner import Runner, canonical, digest, object_only

SPEC = json.loads((Path(__file__).parent / 'room-request.json').read_text())
GOOD = {'choice': 'Oak', 'comparison': [
    {'source': 'S1', 'seats': 24, 'step_free': True, 'price_gbp': 120, 'meets_request': True},
    {'source': 'S2', 'seats': 30, 'step_free': False, 'price_gbp': 90, 'meets_request': False},
    {'source': 'S3', 'seats': 16, 'step_free': True, 'price_gbp': 85, 'meets_request': False}],
    'summary': 'Fictional data: Oak fits; Birch lacks step-free access; Elm has fewer than 20 seats. No booking.'}
BAD = copy.deepcopy(GOOD)
BAD['choice'] = 'Birch'
BAD['summary'] = 'Fictional test seed: choose Birch for its lower price. This intentionally omits the access requirement.'
FINDING = {'id': 'F1', 'evidence': ['S2'], 'reason': 'Birch is not step-free.'}
DISPOSITION = {'id': 'F1', 'action': 'applied', 'evidence': ['S1', 'S2'], 'reason': 'Recommend Oak; Birch fails access.'}


class Fake:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []
    def __call__(self, provider, prompt, folder, candidate_hash, deadline):
        self.calls.append((provider, prompt.read_text(), candidate_hash))
        answer = self.answers.pop(0)
        if callable(answer):
            answer = answer(candidate_hash)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


def review(verdict='PASS', findings=None):
    return lambda h: {'candidate_sha256': h, 'verdict': verdict, 'findings': findings or []}


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'attempt'
    def tearDown(self):
        self.tmp.cleanup()
    def runner(self, answers, **kwargs):
        self.fake = Fake(answers)
        return Runner(self.root, copy.deepcopy(SPEC), self.fake, **kwargs)
    def test_pass_uses_distinct_providers_and_full_request(self):
        r = self.runner([{'candidate': GOOD, 'dispositions': []}, review()])
        result = r.execute()
        self.assertEqual(result['disposition'], 'READY_FOR_PUBLICATION')
        self.assertEqual([c[0] for c in self.fake.calls], ['codex', 'claude'])
        self.assertTrue(all(SPEC['original_request'] in c[1] for c in self.fake.calls))
        self.assertEqual(json.loads((self.root/'state.json').read_text()), result)
    def test_supported_correction_rechecked(self):
        r = self.runner([review('CORRECT', [FINDING]), {'candidate': GOOD, 'dispositions': [DISPOSITION]}, review()])
        self.assertEqual(r.execute(BAD)['disposition'], 'READY_FOR_PUBLICATION')
        self.assertEqual([c[0] for c in self.fake.calls], ['claude', 'codex', 'claude'])
        self.assertNotEqual(self.fake.calls[0][2], self.fake.calls[2][2])
    def test_no_progress_stops_before_third_call(self):
        r = self.runner([review('CORRECT', [FINDING]), {'candidate': BAD, 'dispositions': [DISPOSITION]}])
        self.assertEqual(r.execute(BAD)['reason'], 'no_progress_unchanged_candidate')
        self.assertEqual(len(self.fake.calls), 2)
    def test_call_limit_checked_before_launch(self):
        r = self.runner([{'candidate': GOOD, 'dispositions': []}], max_calls=1)
        self.assertEqual(r.execute()['reason'], 'call_limit')
        self.assertEqual(len(self.fake.calls), 1)
    def test_manual_stop_before_first_invocation(self):
        r = self.runner([])
        (self.root/'STOP').touch()
        self.assertEqual(r.execute()['reason'], 'manual_stop')
        self.assertEqual(self.fake.calls, [])
    def test_elapsed_limit_before_invocation(self):
        clock = [0.0]
        r = self.runner([], clock=lambda: clock[0], wall_seconds=1)
        clock[0] = 2.0
        self.assertEqual(r.execute()['reason'], 'elapsed_limit')
        self.assertEqual(self.fake.calls, [])
    def test_wrong_hash_rejected(self):
        r = self.runner([{'candidate_sha256': 'wrong', 'verdict': 'PASS', 'findings': []}])
        self.assertEqual(r.execute(GOOD)['reason'], 'invalid_or_wrong_candidate_review')
    def test_pass_cannot_override_contradictory_source(self):
        r = self.runner([review()])
        self.assertEqual(r.execute(BAD)['reason'], 'checker_pass_conflicts_with_evidence')
    def test_no_implicit_retry_after_cli_error(self):
        r = self.runner([ValueError('cli_failed:quota')])
        self.assertEqual(r.execute()['disposition'], 'STOP')
        self.assertEqual(len(self.fake.calls), 1)
    def test_claim_cannot_be_reopened(self):
        self.runner([])
        with self.assertRaises(FileExistsError):
            self.runner([])
    def test_unresolved_finding_rejected(self):
        r = self.runner([review('CORRECT', [FINDING]), {'candidate': GOOD, 'dispositions': []}])
        self.assertEqual(r.execute(BAD)['reason'], 'unresolved_findings')
    def test_correction_limit(self):
        r = self.runner([review('CORRECT', [FINDING])], max_corrections=0)
        self.assertEqual(r.execute(BAD)['reason'], 'correction_limit')
        self.assertEqual(len(self.fake.calls), 1)
    def test_unfounded_citation_rejected(self):
        f = dict(FINDING, evidence=['missing'])
        r = self.runner([review('CORRECT', [f])])
        self.assertEqual(r.execute(BAD)['reason'], 'unsupported_review_finding')
    def test_malformed_json_never_repaired_by_guess(self):
        r = self.runner(['here is an answer {broken'])
        self.assertEqual(r.execute()['disposition'], 'STOP')
        with self.assertRaises(ValueError):
            object_only('{"a": 1, "a": 2}')
    def test_missing_inputs_fail_before_any_launch(self):
        with self.assertRaises(ValueError):
            Runner(self.root, {}, Fake([]))
        self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()
