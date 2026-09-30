"""Finite, offline adverse cases. William/authority records here are fixtures."""
from pathlib import Path
import copy
import hashlib
import json
import shutil
import tempfile
import unittest

from controller import Controller, Denied, policy


class Clock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now


class Cases(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'registered.sqlite'
        self.clock = Clock()
        self.owner = 'fixture-coordinator'
        self.c = self.make()

    def tearDown(self): self.temp.cleanup()

    def make(self, overall=None, unit=None):
        return Controller.create(self.path, 'FICTIONAL-CAMPAIGN', self.owner,
            overall or policy(1100, launches=12, cycles=8, reviews=8),
            {'A': {'parent': '@all', 'policy': unit or policy(1090)},
             'A-child': {'parent': 'A', 'policy': unit or policy(1090)},
             'B': {'parent': '@all', 'policy': unit or policy(1090)}},
            'fixture:operator-approved-plan', self.clock)

    def rebuild(self, **kwargs):
        # A new temporary fixture, never an operational allowance reset.
        self.path = Path(self.temp.name) / ('fixture-' + str(len(list(Path(self.temp.name).iterdir()))) + '.sqlite')
        self.c = self.make(**kwargs)

    def scope(self, unit='A'):
        return next(x for x in self.c.snapshot()['scopes'] if x['id'] == unit)

    def active(self, reason=None):
        return [x for x in self.c.snapshot()['stops'] if x['active'] and (reason is None or x['reason'] == reason)]

    def launch(self, role='producer', unit='A', action='draft'):
        if self.scope(unit)['used']['cycles'] == 0:
            self.c.start_cycle(unit, self.owner)
        return self.c.reserve(unit, self.owner, role, action, 'fixture-request-sha')

    def result(self, **overrides):
        d = dict(process_ended=True, terminal_valid=True, exit_code=0,
                 result_sha256=hashlib.sha256(b'fixture output').hexdigest(),
                 binding='fixture-request-sha', observed_tokens=10, findings_fingerprint=None)
        d.update(overrides)
        return d

    def finish(self, r=None, **overrides):
        r = r or self.launch()
        self.c.settle(r['id'], self.owner, self.result(**overrides))
        return r

    def authority(self, scope, operation, payload):
        return self.c.register_authority(self.owner, scope, operation, payload,
            'fixture:William-decision', 'Synthetic test decision, not actual William approval', 1200)

    def amend(self, scope='A', **changes):
        p = copy.deepcopy(self.scope(scope)['policy']); p.update(changes)
        self.c.amend(scope, self.owner, p, self.authority(scope, 'amend', p))

    def human_resume(self, stop, evidence='fixture:resolved-condition', new_plan=None):
        payload = dict(stop_id=stop['id'], evidence=evidence, new_plan=new_plan)
        grant = self.authority(stop['scope'], 'resume', payload)
        self.c.resume(stop['id'], self.owner, evidence, grant, new_plan)
        return grant

    def test_product_warning_persists_and_requires_authority(self):
        self.finish(failure='product_warning', exit_code=7, terminal_valid=False,
                    partial=['private/partial-result.txt'])
        self.c = Controller(self.path, self.clock)
        stop = self.active('product_warning')[0]
        self.assertEqual(json.loads(stop['record'])['partial'], ['private/partial-result.txt'])
        with self.assertRaises(Denied): self.launch()
        with self.assertRaisesRegex(Denied, 'authority'): self.c.resume(stop['id'], self.owner, 'fixture:cleared')
        self.human_resume(stop)
        self.finish()
        self.assertEqual(self.scope()['used']['launches'], 2)

    def test_observed_threshold_counts_overshoot_without_provider_cap(self):
        self.rebuild(unit=policy(1090, observed_tokens=5))
        self.finish(observed_tokens=10)
        self.assertEqual(self.scope()['used']['observed_tokens'], 10)
        stop = self.active('observed_usage')[0]
        with self.assertRaisesRegex(Denied, 'threshold'): self.human_resume(stop)
        self.amend(observed_tokens=30)
        self.human_resume(stop)
        self.finish(observed_tokens=2)
        self.assertEqual(self.scope()['used']['observed_tokens'], 12)

    def test_optional_unknown_usage_is_explicit_and_not_fatal(self):
        self.rebuild(unit=policy(1090, observed_tokens=5))
        self.finish(observed_tokens=None)
        self.finish(observed_tokens=None)
        self.assertEqual(self.scope()['used']['unknown_usage_attempts'], 2)
        self.assertEqual(self.scope()['used']['observed_tokens'], 0)
        self.assertFalse(self.active())

    def test_all_roles_child_and_sibling_charge_overall_once(self):
        self.finish()
        self.finish(self.launch('checker'), observed_tokens=None)
        self.finish(self.launch('correction'))
        self.finish(self.launch('helper', 'A-child'))
        self.finish(self.launch('producer', 'B'))
        self.assertEqual(self.scope()['used']['launches'], 4)
        self.assertEqual(self.scope('A-child')['used']['launches'], 1)
        self.assertEqual(self.scope('B')['used']['launches'], 1)
        self.assertEqual(self.scope('@all')['used']['launches'], 5)
        self.assertEqual(self.scope()['used']['reviews'], 1)
        self.assertEqual(self.scope()['used']['corrections'], 1)

    def test_unit_launch_exhaustion_survives_reopen(self):
        self.rebuild(unit=policy(1090, launches=1))
        self.finish()
        self.c = Controller(self.path, self.clock)
        with self.assertRaisesRegex(Denied, 'launches_exhausted'): self.launch()
        self.assertEqual(self.scope()['used']['launches'], 1)
        with self.assertRaisesRegex(Denied, 'still_exhausted'): self.human_resume(self.active('limit')[0])

    def test_overall_blocks_a_different_unit_and_child(self):
        self.rebuild(overall=policy(1100, launches=1, cycles=8))
        self.finish()
        with self.assertRaisesRegex(Denied, 'launches_exhausted'): self.launch(unit='B')
        self.assertEqual(self.active('limit')[0]['scope'], '@all')
        with self.assertRaises(Denied): self.launch(unit='A-child')

    def test_fixed_deadline_does_not_restart(self):
        self.finish()
        self.clock.now = 1090
        self.c = Controller(self.path, self.clock)
        with self.assertRaisesRegex(Denied, 'deadline_exhausted'): self.launch()
        self.assertEqual(self.scope()['policy']['deadline'], 1090)

    def test_attempt_deadline_is_minimum_of_unit_and_overall(self):
        self.rebuild(overall=policy(1080))
        r = self.launch()
        self.assertEqual(r['deadline'], 1080)
        self.clock.now = 1081
        self.finish(r)
        self.assertTrue(self.active('limit'))

    def test_cycle_limit_persists_and_cannot_resume_unchanged(self):
        self.rebuild(unit=policy(1090, cycles=1))
        self.c.start_cycle('A', self.owner)
        with self.assertRaisesRegex(Denied, 'cycles_exhausted'): self.c.start_cycle('A', self.owner)
        with self.assertRaisesRegex(Denied, 'still_exhausted'): self.human_resume(self.active()[0])

    def test_correction_and_review_limits(self):
        for role, key in [('correction', 'corrections'), ('checker', 'reviews')]:
            with self.subTest(role=role):
                self.rebuild(unit=policy(1090, **{key: 1}))
                self.finish(self.launch(role))
                with self.assertRaisesRegex(Denied, key + '_exhausted'): self.launch(role)
                self.assertEqual(self.scope()['used'][key], 1)
                stop = self.active('limit')[0]
                self.assertEqual(json.loads(stop['record'])['counter'], key)
                with self.assertRaisesRegex(Denied, 'still_exhausted'): self.human_resume(stop)

    def test_free_text_limit_cannot_replace_structured_counter(self):
        with self.assertRaisesRegex(ValueError, 'structured_exhausted_counter'):
            self.c.stop('A', self.owner, 'limit', 'review allowance exhausted')
        self.assertFalse(self.active())

    def test_campaign_manual_stop_resumes_without_inventing_retry_entitlement(self):
        sid = self.c.stop('@all', self.owner, 'manual', 'fixture:pause campaign')
        self.human_resume(next(s for s in self.active() if s['id'] == sid))
        self.finish()
        self.assertFalse(self.active())
        self.assertIsNone(self.scope('@all')['used']['retry_pending'])
        self.assertEqual(self.scope()['used']['launches'], 1)

    def test_campaign_transient_cannot_create_unusable_retry_entitlement(self):
        sid = self.c.stop('@all', self.owner, 'transient', 'fixture:invalid root classification')
        with self.assertRaisesRegex(Denied, 'requires_work_unit'):
            self.c.resume(sid, self.owner, 'fixture:resolved')
        self.assertTrue(self.active('transient'))
        self.assertTrue(all(s['used']['retry_pending'] is None for s in self.c.snapshot()['scopes']))

    def test_structured_retry_limit_survives_reopen_and_refuses_unchanged_resume(self):
        self.rebuild(overall=policy(1100, retries=2), unit=policy(1090, retries=2))
        self.finish(failure='transient', exit_code=8, terminal_valid=False)
        self.c.resume(self.active('transient')[0]['id'], self.owner, 'fixture:first cause resolved')
        self.finish(self.launch('retry'), failure='transient', exit_code=8, terminal_valid=False)
        self.c.resume(self.active('transient')[0]['id'], self.owner, 'fixture:second cause resolved')
        # A real authorised decrease reaches the reserve guard without editing SQLite.
        self.amend(retries=1)
        self.c = Controller(self.path, self.clock)
        with self.assertRaisesRegex(Denied, 'retries_exhausted'): self.launch('retry')
        stop = self.active('limit')[0]
        self.assertEqual(json.loads(stop['record'])['counter'], 'retries')
        with self.assertRaisesRegex(Denied, 'limits_still_exhausted'): self.human_resume(stop)
        self.assertEqual((self.scope()['used']['launches'], self.scope()['used']['retries']), (2, 1))

    def test_no_launch_attestation_is_refused_after_boundary(self):
        r = self.launch(); self.c.begin_launch(r['id'], self.owner)
        with self.assertRaisesRegex(Denied, 'after_launch_boundary'):
            self.c.abandon_before_launch(r['id'], self.owner, 'fixture:invalid claim')
        with self.assertRaisesRegex(Denied, 'unsettled_attempt'): self.launch()
        self.assertEqual(self.c.snapshot()['attempts'][0]['state'], 'launching')

    def test_repeated_findings_require_changed_plan_and_authority(self):
        for _ in range(2): self.finish(self.launch('checker'), findings_fingerprint='a' * 64)
        stop = self.active('no_progress')[0]
        with self.assertRaisesRegex(Denied, 'changed_plan'): self.human_resume(stop)
        with self.assertRaisesRegex(Denied, 'authority'): self.c.resume(stop['id'], self.owner, 'new method', new_plan='source-first')
        self.human_resume(stop, new_plan='source-first')
        self.finish(self.launch('checker'), findings_fingerprint=None)
        self.assertEqual(self.scope()['used']['reviews'], 3)
        self.assertEqual(self.scope()['used']['repeated_findings'], 0)

    def test_transient_can_resume_but_retry_is_counted_and_exhausts(self):
        self.finish(failure='transient', exit_code=8, terminal_valid=False)
        stop = self.active('transient')[0]
        self.c.resume(stop['id'], self.owner, 'fixture:temporary cause cleared')
        with self.assertRaisesRegex(Denied, 'recorded_retry'): self.launch('producer')
        self.finish(self.launch('retry'), failure='transient', exit_code=8, terminal_valid=False)
        with self.assertRaisesRegex(Denied, 'retries_exhausted'):
            self.c.resume(self.active('transient')[0]['id'], self.owner, 'fixture:cleared again')
        self.assertEqual(self.scope()['used']['launches'], 2)
        self.assertEqual(self.scope()['used']['retries'], 1)

    def test_checker_retry_also_consumes_review_allowance(self):
        self.finish(self.launch('checker'), failure='transient', exit_code=8, terminal_valid=False)
        self.c.resume(self.active()[0]['id'], self.owner, 'fixture:cleared')
        self.finish(self.launch('retry'))
        self.assertEqual(self.scope()['used']['reviews'], 2)
        self.assertEqual(self.scope()['used']['retries'], 1)

    def test_automatic_resume_refuses_changed_limits(self):
        self.finish(failure='transient', exit_code=8, terminal_valid=False)
        stop = self.active()[0]
        self.amend(launches=9)
        with self.assertRaisesRegex(Denied, 'unchanged_limits'): self.c.resume(stop['id'], self.owner, 'fixture:cleared')
        self.human_resume(stop)
        self.finish(self.launch('retry'))

    def test_retry_without_a_failed_attempt_is_refused(self):
        with self.assertRaisesRegex(Denied, 'retry_not_authorised'): self.launch('retry')
        self.assertEqual(self.scope()['used']['launches'], 0)

    def test_crash_reservation_cannot_be_renamed_away(self):
        self.launch()
        self.c = Controller(self.path, self.clock)
        with self.assertRaisesRegex(Denied, 'unsettled_attempt'): self.launch(unit='B')
        self.assertEqual(len(self.c.snapshot()['attempts']), 1)
        self.assertEqual(self.scope('@all')['used']['launches'], 1)

    def test_missing_evidence_stays_unsettled_until_reconciled_and_authorised(self):
        r = self.launch()
        self.assertEqual(self.c.settle(r['id'], self.owner, {'partial': ['partial.txt']}), 'unsettled')
        stop = self.active('missing_evidence')[0]
        with self.assertRaisesRegex(Denied, 'reconcile_before_resume'): self.human_resume(stop)
        with self.assertRaises(Denied): self.c.reconcile(r['id'], self.owner, {})
        self.c.reconcile(r['id'], self.owner, dict(process_ended=True, effects_checked=True, reference='fixture:inspection'))
        self.assertEqual(self.scope()['used']['unknown_usage_attempts'], 1)
        with self.assertRaisesRegex(Denied, 'authority'): self.c.resume(stop['id'], self.owner, 'fixture:reconciled')
        self.human_resume(stop)
        self.finish()
        self.assertEqual(self.scope()['used']['launches'], 2)
        self.assertIn('partial.txt', self.c.snapshot()['attempts'][0]['result'])

    def test_wrong_result_binding_is_mandatory_evidence_failure(self):
        r = self.launch()
        self.c.settle(r['id'], self.owner, self.result(binding='different-request'))
        self.assertEqual(self.c.snapshot()['attempts'][0]['state'], 'unsettled')

    def test_out_of_scope_action_stops_before_consumption(self):
        with self.assertRaisesRegex(Denied, 'outside_scope'): self.launch(action='publish')
        self.assertEqual(self.scope()['used']['launches'], 0)
        self.assertTrue(self.active('out_of_authority'))

    def test_unregistered_unit_and_wrong_owner_stop_campaign(self):
        with self.assertRaises(Denied): self.c.start_cycle('NEW-TICKET', self.owner)
        self.assertEqual(self.active()[0]['scope'], '@all')
        self.rebuild()
        with self.assertRaisesRegex(Denied, 'wrong_owner'): self.c.start_cycle('A', 'other-worker')
        self.assertEqual(self.active()[0]['scope'], '@all')

    def test_manual_stop_requires_one_use_bound_authority(self):
        sid = self.c.stop('A', self.owner, 'manual', 'fixture:user stop')
        self.c = Controller(self.path, self.clock)
        with self.assertRaises(Denied): self.launch()
        grant = self.human_resume(self.active()[0])
        with self.assertRaises(Denied): self.c.resume(sid, self.owner, 'fixture:resolved-condition', grant)
        self.finish()

    def test_authorised_limit_amendment_preserves_consumed_and_pending(self):
        r = self.launch()
        before = copy.deepcopy(self.scope()['policy'])
        self.amend(launches=10, deadline=1110)
        event = [json.loads(x['record']) for x in self.c.snapshot()['events'] if x['kind'] == 'amendment'][0]
        self.assertEqual(event['old_policy'], before)
        self.assertEqual(event['new_policy']['launches'], 10)
        self.assertEqual(event['deadline_effect_seconds'], 20)
        self.assertEqual(event['cumulative']['launches'], 1)
        self.assertEqual(event['attempts'][0]['state'], 'reserved')
        self.assertEqual(event['attempts'][0]['id'], r['id'])
        self.assertEqual(self.c.snapshot()['attempts'][0]['deadline'], 1090)

    def test_missing_stale_and_mismatched_authority_cannot_amend(self):
        original = copy.deepcopy(self.scope()['policy'])
        new = dict(original, launches=9)
        with self.assertRaises(Denied): self.c.amend('A', self.owner, new, None)
        grant = self.authority('A', 'amend', new)
        with self.assertRaises(Denied): self.c.amend('A', self.owner, dict(new, launches=10), grant)
        self.assertEqual(self.scope()['policy'], original)
        self.c.amend('A', self.owner, new, grant)
        with self.assertRaises(Denied): self.c.amend('A', self.owner, new, grant)

    def test_authority_expiry_and_stale_policy(self):
        new = dict(self.scope()['policy'], launches=9)
        grant = self.authority('A', 'amend', new)
        self.amend(deadline=1110)
        with self.assertRaises(Denied): self.c.amend('A', self.owner, new, grant)
        self.rebuild()
        grant = self.c.register_authority(self.owner, 'A', 'amend', new, 'fixture:ref', 'fixture decision', 1001)
        self.clock.now = 1002
        with self.assertRaises(Denied): self.c.amend('A', self.owner, new, grant)

    def test_scope_change_requires_bound_authority_and_resumption(self):
        with self.assertRaises(Denied): self.launch(action='export')
        stop = self.active()[0]
        self.amend(actions=['draft', 'review', 'export'])
        self.amend('@all', actions=['draft', 'review', 'export'])
        self.human_resume(stop)
        self.finish(self.launch(action='export'))

    def test_parent_amendment_does_not_increase_child_or_overall_caps(self):
        self.amend(launches=100, deadline=2000)
        self.assertEqual(self.scope('@all')['policy']['launches'], 12)
        self.assertEqual(self.scope('A-child')['policy']['launches'], 8)
        r = self.launch()
        self.assertEqual(r['deadline'], 1100)

    def test_copy_and_recreation_are_refused(self):
        self.finish()
        copied = Path(self.temp.name) / 'new-folder.sqlite'
        shutil.copy2(self.path, copied)
        with self.assertRaisesRegex(ValueError, 'copied_store'): Controller(copied, self.clock)
        with self.assertRaises(FileExistsError): self.make()
        with self.assertRaisesRegex(ValueError, 'existing_store'): Controller(Path(self.temp.name) / 'missing.sqlite', self.clock)

    def test_clock_rollback_stops_and_does_not_extend_deadline(self):
        self.finish()
        self.clock.now -= 1
        with self.assertRaisesRegex(Denied, 'clock_rollback'): self.launch()
        self.assertEqual(self.scope()['policy']['deadline'], 1090)
        self.assertTrue(self.active('clock_rollback'))

    def test_duplicate_settlement_is_rejected(self):
        r = self.finish()
        with self.assertRaisesRegex(Denied, 'not_reserved'): self.c.settle(r['id'], self.owner, self.result())
        self.assertEqual(self.scope()['used']['observed_tokens'], 10)


if __name__ == '__main__': unittest.main(verbosity=2)
