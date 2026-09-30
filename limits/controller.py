"""Trusted single-coordinator limit controller. No model or service client.

An operator registers authority only after verifying its external source.
Names and references stored here do not authenticate William or any AI.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import math
import os
import sqlite3
import time
import uuid


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Denied(ValueError):
    """The rejection and any stop have already been committed."""


COUNTERS = ('launches', 'cycles', 'corrections', 'reviews', 'retries')
ROLES = {'producer': None, 'checker': 'reviews', 'correction': 'corrections',
         'retry': 'retries', 'helper': None}
REASONS = {'product_warning', 'observed_usage', 'limit', 'no_progress',
           'transient', 'missing_evidence', 'out_of_authority', 'manual', 'clock_rollback'}


def policy(deadline, launches=8, cycles=3, corrections=2, reviews=3,
           retries=1, no_progress=2, observed_tokens=None, actions=('draft', 'review')):
    return dict(deadline=deadline, launches=launches, cycles=cycles,
                corrections=corrections, reviews=reviews, retries=retries,
                no_progress=no_progress, observed_tokens=observed_tokens, actions=list(actions))


def validate_policy(p):
    if set(p) != set(COUNTERS) | {'deadline', 'no_progress', 'observed_tokens', 'actions'}:
        raise ValueError('policy_shape')
    if any(type(p[k]) is not int or p[k] < 0 for k in COUNTERS):
        raise ValueError('finite_nonnegative_counts_required')
    if type(p['no_progress']) is not int or p['no_progress'] < 1:
        raise ValueError('finite_progress_limit_required')
    if type(p['deadline']) not in (int, float) or not math.isfinite(p['deadline']):
        raise ValueError('finite_deadline_required')
    n = p['observed_tokens']
    if n is not None and (type(n) is not int or n < 1):
        raise ValueError('invalid_observed_threshold')
    if not isinstance(p['actions'], list) or not p['actions'] or any(
            not isinstance(x, str) or not x for x in p['actions']) or len(set(p['actions'])) != len(p['actions']):
        raise ValueError('explicit_actions_required')


class Controller:
    def __init__(self, path, clock=time.time):
        self.path = Path(path).resolve()
        self.clock = clock
        if not self.path.is_file():
            raise ValueError('registered_existing_store_required')
        with sqlite3.connect(self.path) as c:
            meta = json.loads(c.execute('SELECT value FROM meta').fetchone()[0])
        if meta['canonical_path'] != str(self.path):
            raise ValueError('copied_store_not_authoritative')

    @classmethod
    def create(cls, path, campaign, owner, overall, units, authority_ref, clock=time.time):
        """Trusted bootstrap only. All unit/child IDs are registered once here."""
        path = Path(path).resolve()
        validate_policy(overall)
        now = clock()
        if not campaign or not owner or not authority_ref or overall['deadline'] <= now:
            raise ValueError('initial_authority_and_future_deadline_required')
        policies = {'@all': overall}
        for unit, entry in units.items():
            validate_policy(entry['policy'])
            if unit == '@all' or not unit or entry['parent'] not in policies:
                raise ValueError('ordered_registered_parent_required')
            if not set(entry['policy']['actions']) <= set(policies[entry['parent']]['actions']):
                raise ValueError('child_scope_exceeds_parent')
            policies[unit] = entry['policy']
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        c = sqlite3.connect(path)
        with c:
            c.executescript('''
                CREATE TABLE meta(value TEXT NOT NULL);
                CREATE TABLE scopes(id TEXT PRIMARY KEY,parent TEXT,policy TEXT,used TEXT,plan TEXT);
                CREATE TABLE attempts(id TEXT PRIMARY KEY,unit TEXT,role TEXT,purpose TEXT,action TEXT,
                    state TEXT,deadline REAL,binding TEXT,result TEXT);
                CREATE TABLE stops(id TEXT PRIMARY KEY,scope TEXT,reason TEXT,active INTEGER,record TEXT);
                CREATE TABLE grants(id TEXT PRIMARY KEY,scope TEXT,operation TEXT,payload TEXT,
                    expires REAL,used INTEGER,record TEXT);
                CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,record TEXT);
            ''')
            c.execute('INSERT INTO meta VALUES(?)', (encode(dict(campaign=campaign,
                      owner_sha=digest(owner), canonical_path=str(path), high_water=now)),))
            for unit, p in policies.items():
                used = {k: 0 for k in COUNTERS}
                used.update(observed_tokens=0, unknown_usage_attempts=0,
                            progress_fingerprint=None, repeated_findings=0, retry_pending=None)
                parent = None if unit == '@all' else units[unit]['parent']
                c.execute('INSERT INTO scopes VALUES(?,?,?,?,?)',
                          (unit, parent, encode(p), encode(used), 'initial'))
            c.execute('INSERT INTO events(kind,record) VALUES(?,?)', ('bootstrap', encode({
                'at': now, 'campaign': campaign, 'authority_ref': authority_ref,
                'policies': policies, 'parents': {k: v['parent'] for k, v in units.items()}})))
        c.close()
        return cls(path, clock)

    @contextmanager
    def _tx(self, owner):
        c = sqlite3.connect(self.path, timeout=3)
        c.row_factory = sqlite3.Row
        try:
            c.execute('BEGIN IMMEDIATE')
            m = json.loads(c.execute('SELECT value FROM meta').fetchone()[0])
            now = self.clock()
            if type(now) not in (int, float) or not math.isfinite(now):
                raise ValueError('clock_invalid')
            if m['canonical_path'] != str(self.path):
                raise ValueError('store_identity_changed')
            if digest(owner) != m['owner_sha']:
                self._stop(c, '@all', 'out_of_authority', m['high_water'], 'wrong owner')
                raise Denied('wrong_owner')
            if now < m['high_water']:
                self._stop(c, '@all', 'clock_rollback', m['high_water'], 'clock moved backwards')
                raise Denied('clock_rollback')
            m['high_water'] = now
            c.execute('UPDATE meta SET value=?', (encode(m),))
            yield c, now
            c.commit()
        except Denied:
            c.commit()
            raise
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def _event(self, c, kind, record):
        c.execute('INSERT INTO events(kind,record) VALUES(?,?)', (kind, encode(record)))

    def _scope(self, c, unit):
        r = c.execute('SELECT * FROM scopes WHERE id=?', (unit,)).fetchone()
        if r is None:
            raise ValueError('unregistered_scope')
        d = dict(r)
        d['policy'], d['used'] = json.loads(d['policy']), json.loads(d['used'])
        return d

    def _chain(self, c, unit):
        chain = []
        while unit is not None:
            r = self._scope(c, unit)
            chain.append(r)
            unit = r['parent']
        return chain

    def _save_used(self, c, scope):
        c.execute('UPDATE scopes SET used=? WHERE id=?', (encode(scope['used']), scope['id']))

    def _stop(self, c, scope, reason, now, detail, partial=None, counter=None, attempt_id=None):
        if reason not in REASONS:
            raise ValueError('unknown_stop_reason')
        if reason == 'limit' and counter not in set(COUNTERS) | {'deadline'}:
            raise ValueError('structured_exhausted_counter_required')
        sid = uuid.uuid4().hex
        attempts = [dict(x) for x in c.execute('SELECT id,unit,role,state FROM attempts')]
        record = dict(id=sid, scope=scope, reason=reason, at=now, detail=detail,
                      partial=partial or [], attempts=attempts, counter=counter, attempt_id=attempt_id,
                      used=self._scope(c, scope)['used'],
                      policy_hashes={r['id']: digest(r['policy']) for r in self._chain(c, scope)},
                      resume_class='automatic_with_evidence' if reason == 'transient' else 'William_authority')
        c.execute('INSERT INTO stops VALUES(?,?,?,1,?)', (sid, scope, reason, encode(record)))
        self._event(c, 'stop', record)
        return sid

    def _ready(self, c, unit, now):
        if unit == '@all' or not c.execute('SELECT 1 FROM scopes WHERE id=?', (unit,)).fetchone():
            self._stop(c, '@all', 'out_of_authority', now, 'unregistered work unit')
            raise Denied('unregistered_work_unit')
        chain = self._chain(c, unit)
        for r in chain:
            if c.execute('SELECT 1 FROM stops WHERE scope=? AND active=1', (r['id'],)).fetchone():
                raise Denied('stopped:' + r['id'])
            if now >= r['policy']['deadline']:
                self._stop(c, r['id'], 'limit', now, 'deadline exhausted', counter='deadline')
                raise Denied('deadline_exhausted')
        if c.execute("SELECT 1 FROM attempts WHERE state IN ('reserved','launching','unsettled')").fetchone():
            self._stop(c, '@all', 'missing_evidence', now, 'prior attempt not settled')
            raise Denied('unsettled_attempt')
        return chain

    def start_cycle(self, unit, owner):
        with self._tx(owner) as (c, now):
            chain = self._ready(c, unit, now)
            for r in chain:
                if r['used']['cycles'] >= r['policy']['cycles']:
                    self._stop(c, r['id'], 'limit', now, 'cycles exhausted', counter='cycles')
                    raise Denied('cycles_exhausted')
            for r in chain:
                r['used']['cycles'] += 1
                self._save_used(c, r)
            self._event(c, 'cycle_started', dict(unit=unit, at=now,
                        cumulative={r['id']: r['used'] for r in chain}))

    def reserve(self, unit, owner, role, action, binding):
        """Reserve before starting a process; every reservation remains consumed."""
        with self._tx(owner) as (c, now):
            chain = self._ready(c, unit, now)
            if role not in ROLES or not isinstance(binding, str) or not binding:
                self._stop(c, unit, 'out_of_authority', now, 'invalid role or missing request binding')
                raise Denied('invalid_launch')
            if chain[0]['used']['cycles'] < 1:
                raise Denied('start_cycle_first')
            pending = chain[0]['used']['retry_pending']
            if (pending is not None) != (role == 'retry'):
                raise Denied('recorded_retry_required' if pending else 'retry_not_authorised')
            purpose = pending if role == 'retry' else role
            for r in chain:
                if action not in r['policy']['actions']:
                    self._stop(c, r['id'], 'out_of_authority', now, 'operation outside scope: ' + str(action))
                    raise Denied('outside_scope')
                checks = ['launches'] + ([ROLES[purpose]] if ROLES[purpose] else [])
                if role == 'retry':
                    checks.append('retries')
                for key in checks:
                    if r['used'][key] >= r['policy'][key]:
                        self._stop(c, r['id'], 'limit', now, key + ' exhausted', counter=key)
                        raise Denied(key + '_exhausted')
                threshold = r['policy']['observed_tokens']
                if threshold is not None and r['used']['observed_tokens'] >= threshold:
                    self._stop(c, r['id'], 'observed_usage', now, 'reported threshold reached')
                    raise Denied('observed_threshold')
            rid = uuid.uuid4().hex
            deadline = min(r['policy']['deadline'] for r in chain)
            c.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,NULL)',
                      (rid, unit, role, purpose, action, 'reserved', deadline, binding))
            for r in chain:
                r['used']['launches'] += 1
                if ROLES[purpose]:
                    r['used'][ROLES[purpose]] += 1
                if role == 'retry':
                    r['used']['retries'] += 1
                if r['id'] == unit:
                    r['used']['retry_pending'] = None
                self._save_used(c, r)
            record = dict(id=rid, unit=unit, role=role, purpose=purpose, action=action, deadline=deadline,
                          binding=binding, at=now, cumulative={r['id']: r['used'] for r in chain})
            self._event(c, 'reserved', record)
            return record

    def begin_launch(self, rid, owner):
        """Persist launch uncertainty immediately before calling the adapter."""
        with self._tx(owner) as (c, now):
            row = c.execute('SELECT * FROM attempts WHERE id=?', (rid,)).fetchone()
            if row is None or row['state'] != 'reserved':
                raise Denied('not_reserved')
            for r in self._chain(c, row['unit']):
                if c.execute('SELECT 1 FROM stops WHERE scope=? AND active=1', (r['id'],)).fetchone():
                    raise Denied('stopped_before_launch')
                if now >= min(row['deadline'], r['policy']['deadline']):
                    self._stop(c, r['id'], 'limit', now, 'deadline exhausted before launch', counter='deadline', attempt_id=rid)
                    raise Denied('deadline_exhausted_before_launch')
                counters = ['launches'] + ([ROLES[row['purpose']]] if ROLES[row['purpose']] else [])
                if row['role'] == 'retry': counters.append('retries')
                for key in counters:
                    if r['used'][key] > r['policy'][key]:
                        self._stop(c, r['id'], 'limit', now, 'adopted limit below reserved use', counter=key, attempt_id=rid)
                        raise Denied('amended_limit_blocks_reserved_attempt')
            c.execute("UPDATE attempts SET state='launching' WHERE id=?", (rid,))
            self._event(c, 'launch_boundary', dict(at=now, id=rid, unit=row['unit'], process_started='not yet known'))

    def abandon_before_launch(self, rid, owner, detail, partial=None, failure='missing_evidence'):
        """Trusted launcher proved its process call was never entered. Slot stays used."""
        if failure not in ('transient', 'out_of_authority', 'missing_evidence'):
            raise ValueError('invalid_prelaunch_failure_class')
        with self._tx(owner) as (c, now):
            row = c.execute('SELECT * FROM attempts WHERE id=?', (rid,)).fetchone()
            if row is None or row['state'] != 'reserved':
                raise Denied('cannot_attest_no_launch_after_launch_boundary')
            result = dict(outcome='abandoned_before_launch', process_started=False,
                          failure=failure, observed_tokens=0, detail=detail, partial=partial or [])
            c.execute("UPDATE attempts SET state='abandoned_before_launch',result=? WHERE id=?", (encode(result), rid))
            self._event(c, 'abandoned_before_launch', dict(at=now, id=rid, unit=row['unit'], result=result,
                                                         consumed_launch_slot_preserved=True))
            self._stop(c, row['unit'], failure, now, 'known failure before process call', partial, attempt_id=rid)

    def settle(self, rid, owner, result):
        """Trusted process adapter calls once. Optional usage may be unknown."""
        with self._tx(owner) as (c, now):
            row = c.execute('SELECT * FROM attempts WHERE id=?', (rid,)).fetchone()
            if row is None or row['state'] not in ('reserved', 'launching'):
                raise Denied('not_reserved')
            required = {'process_ended', 'terminal_valid', 'exit_code', 'result_sha256', 'binding'}
            evidence = isinstance(result, dict) and required <= set(result)
            evidence = evidence and result['process_ended'] is True and type(result['exit_code']) is int
            evidence = evidence and type(result['terminal_valid']) is bool and result['binding'] == row['binding']
            fingerprint = result.get('result_sha256') if isinstance(result, dict) else None
            evidence = evidence and isinstance(fingerprint, str) and len(fingerprint) == 64 and all(
                x in '0123456789abcdef' for x in fingerprint)
            reason = result.get('failure') if isinstance(result, dict) else None
            if reason is not None and reason not in REASONS - {'limit', 'observed_usage', 'clock_rollback'}:
                raise ValueError('unknown_failure')
            if not evidence or (not result['terminal_valid'] and reason not in ('transient', 'product_warning', 'manual')):
                c.execute("UPDATE attempts SET state='unsettled',result=? WHERE id=?", (encode(result), rid))
                self._stop(c, row['unit'], 'missing_evidence', now, 'mandatory process/result evidence missing',
                           result.get('partial', []) if isinstance(result, dict) else [], attempt_id=rid)
                return 'unsettled'
            usage = result.get('observed_tokens')
            if usage is not None and (type(usage) is not int or usage < 0):
                raise ValueError('invalid_optional_usage')
            if result['exit_code'] != 0 and reason is None:
                reason = 'missing_evidence'
            if row['purpose'] == 'checker' and not reason:
                f = result.get('findings_fingerprint')
                if f is not None and (not isinstance(f, str) or len(f) != 64):
                    raise ValueError('invalid_findings_fingerprint')
                if 'findings_fingerprint' not in result:
                    reason = 'missing_evidence'
            c.execute("UPDATE attempts SET state='settled',result=? WHERE id=?", (encode(result), rid))
            for r in self._chain(c, row['unit']):
                if usage is None:
                    r['used']['unknown_usage_attempts'] += 1
                else:
                    r['used']['observed_tokens'] += usage
                # No-progress concerns one unit's repeated findings, not unrelated siblings.
                if r['id'] == row['unit'] and row['purpose'] == 'checker' and not reason:
                    f = result['findings_fingerprint']
                    previous = r['used']['progress_fingerprint']
                    r['used']['repeated_findings'] = (r['used']['repeated_findings'] + 1 if f == previous else 1) if f else 0
                    r['used']['progress_fingerprint'] = f
                    if r['used']['repeated_findings'] >= r['policy']['no_progress']:
                        reason = 'no_progress'
                self._save_used(c, r)
                threshold = r['policy']['observed_tokens']
                if threshold is not None and r['used']['observed_tokens'] >= threshold:
                    self._stop(c, r['id'], 'observed_usage', now, 'reported threshold reached')
            if reason:
                manual_already_recorded = reason == 'manual' and any(
                    c.execute("SELECT 1 FROM stops WHERE scope=? AND reason='manual' AND active=1", (r['id'],)).fetchone()
                    for r in self._chain(c, row['unit']))
                if manual_already_recorded:
                    self._event(c, 'manual_cancellation_observed', dict(at=now, id=rid, unit=row['unit'], result=result))
                else:
                    self._stop(c, row['unit'], reason, now, 'process outcome: ' + reason, result.get('partial', []), attempt_id=rid)
            if now >= row['deadline']:
                self._stop(c, row['unit'], 'limit', now, 'attempt deadline exceeded', counter='deadline', attempt_id=rid)
            self._event(c, 'settled', dict(at=now, id=rid, unit=row['unit'], result=result))
            return 'settled'

    def stop(self, scope, owner, reason, detail, partial=None, counter=None):
        with self._tx(owner) as (c, now):
            self._scope(c, scope)
            return self._stop(c, scope, reason, now, detail, partial, counter=counter)

    def register_authority(self, owner, scope, operation, payload, reference, decision, expires):
        """Trusted registrar: verify William's real instruction BEFORE calling.

        A fixture reference is permitted in a fixture database only. This method
        cannot determine whether a Confluence/Jira record actually exists.
        """
        with self._tx(owner) as (c, now):
            r = self._scope(c, scope)
            if operation not in ('amend', 'resume') or not reference or not decision or expires <= now:
                raise ValueError('explicit_fresh_authority_required')
            g = uuid.uuid4().hex
            record = dict(id=g, scope=scope, operation=operation, payload=payload,
                          reference=reference, decision=decision, at=now, expires=expires,
                          old_policy_sha256=digest(r['policy']), source_verification='trusted registrar obligation')
            c.execute('INSERT INTO grants VALUES(?,?,?,?,?,0,?)',
                      (g, scope, operation, encode(payload), expires, encode(record)))
            self._event(c, 'authority_registered', record)
            return g

    def _grant(self, c, gid, scope, operation, payload, now):
        row = c.execute('SELECT * FROM grants WHERE id=?', (gid,)).fetchone()
        r = self._scope(c, scope)
        if row is None or row['used'] or row['scope'] != scope or row['operation'] != operation or row['expires'] <= now or row['payload'] != encode(payload) or json.loads(row['record'])['old_policy_sha256'] != digest(r['policy']):
            self._event(c, 'authority_rejected', dict(at=now, scope=scope, operation=operation))
            raise Denied('missing_stale_consumed_or_mismatched_authority')
        c.execute('UPDATE grants SET used=1 WHERE id=?', (gid,))
        return json.loads(row['record'])

    def amend(self, scope, owner, new_policy, grant_id):
        validate_policy(new_policy)
        with self._tx(owner) as (c, now):
            r = self._scope(c, scope)
            authority = self._grant(c, grant_id, scope, 'amend', new_policy, now)
            c.execute('UPDATE scopes SET policy=? WHERE id=?', (encode(new_policy), scope))
            self._event(c, 'amendment', dict(at=now, scope=scope, authority=authority,
                old_policy=r['policy'], new_policy=new_policy,
                deadline_effect_seconds=new_policy['deadline'] - r['policy']['deadline'],
                cumulative=r['used'], attempts=[dict(x) for x in c.execute('SELECT id,unit,role,state FROM attempts')]))

    def reconcile(self, rid, owner, evidence):
        """Record observed existing effects; does not launch or grant resumption."""
        with self._tx(owner) as (c, now):
            row = c.execute('SELECT * FROM attempts WHERE id=?', (rid,)).fetchone()
            if row is None or row['state'] not in ('reserved', 'launching', 'unsettled') or not isinstance(evidence, dict) or (evidence.get('process_ended') is not True and evidence.get('process_started') is not False) or evidence.get('effects_checked') is not True or not evidence.get('reference'):
                raise Denied('reconciliation_evidence_required')
            c.execute("UPDATE attempts SET state='reconciled' WHERE id=?", (rid,))
            self._event(c, 'reconciled', dict(at=now, id=rid, unit=row['unit'], evidence=evidence,
                                           original_result=row['result'], optional_usage='unknown unless previously settled'))
            # Never overwrite the original partial result or invent usage.
            for r in self._chain(c, row['unit']):
                r['used']['unknown_usage_attempts'] += 1
                self._save_used(c, r)

    def resume(self, stop_id, owner, evidence, grant_id=None, new_plan=None):
        with self._tx(owner) as (c, now):
            stop = c.execute('SELECT * FROM stops WHERE id=? AND active=1', (stop_id,)).fetchone()
            if stop is None or not isinstance(evidence, str) or not evidence.strip():
                raise Denied('active_stop_and_resolution_evidence_required')
            if c.execute("SELECT 1 FROM attempts WHERE state IN ('reserved','launching','unsettled')").fetchone():
                raise Denied('reconcile_before_resume')
            r = self._scope(c, stop['scope'])
            original = json.loads(stop['record'])
            exhausted = original.get('counter')
            if stop['reason'] == 'limit' and exhausted in COUNTERS and r['used'][exhausted] >= r['policy'][exhausted]:
                raise Denied('limits_still_exhausted')
            for s in self._chain(c, stop['scope']):
                if now >= s['policy']['deadline'] or s['used']['launches'] >= s['policy']['launches']:
                    raise Denied('limits_still_exhausted')
                threshold = s['policy']['observed_tokens']
                if threshold is not None and s['used']['observed_tokens'] >= threshold:
                    raise Denied('observed_threshold_still_exhausted')
            payload = dict(stop_id=stop_id, evidence=evidence, new_plan=new_plan)
            if stop['reason'] == 'transient':
                if stop['scope'] == '@all':
                    raise Denied('transient_resume_requires_work_unit')
                prior = c.execute('SELECT * FROM attempts WHERE id=? AND unit=?', (original.get('attempt_id'), stop['scope'])).fetchone()
                if prior is None or prior['state'] not in ('settled', 'abandoned_before_launch') or json.loads(prior['result']).get('failure') != 'transient':
                    raise Denied('resolved_transient_attempt_required')
                for s in self._chain(c, stop['scope']):
                    if s['used']['retries'] >= s['policy']['retries']:
                        raise Denied('retries_exhausted')
            if stop['reason'] == 'transient' and grant_id is None:
                if original['policy_hashes'] != {s['id']: digest(s['policy']) for s in self._chain(c, stop['scope'])}:
                    raise Denied('automatic_resume_requires_unchanged_limits')
                authority = 'automatic: resolved transient, same owner, unchanged policy'
            else:
                if stop['reason'] == 'no_progress' and (not new_plan or new_plan == r['plan']):
                    raise Denied('changed_plan_required')
                authority = self._grant(c, grant_id, stop['scope'], 'resume', payload, now)
            if stop['reason'] == 'transient':
                r['used']['retry_pending'] = prior['purpose']
                self._save_used(c, r)
            if new_plan:
                c.execute('UPDATE scopes SET plan=? WHERE id=?', (new_plan, stop['scope']))
            c.execute('UPDATE stops SET active=0 WHERE id=?', (stop_id,))
            self._event(c, 'resumed', dict(at=now, scope=stop['scope'], payload=payload, authority=authority,
                                         cumulative=r['used']))

    def snapshot(self):
        with sqlite3.connect(self.path) as c:
            c.row_factory = sqlite3.Row
            return {'scopes': [self._scope(c, x[0]) for x in c.execute('SELECT id FROM scopes')],
                    'attempts': [dict(x) for x in c.execute('SELECT * FROM attempts')],
                    'stops': [dict(x) for x in c.execute('SELECT * FROM stops')],
                    'events': [dict(x) for x in c.execute('SELECT * FROM events ORDER BY seq')]}
