"""One bounded, trusted coordinator for a fixed queue of approved work.

Workers have no direct Jira/Confluence/GitHub publication credentials. All
cooperating workers share this local database; copying it is outside the
contract. Jira read/transition is NOT the atomic ownership mechanism.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import math
import os
import secrets
import sqlite3
import time


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


class Queue:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        require(self.path.is_file(), 'existing_queue_required')
        self.clock = clock

    @classmethod
    def create(cls, path, approvals, *, publisher, max_scans=12, max_claims=2,
               max_concurrent=1, seconds=1200, lease_seconds=900, freshness=15,
               clock=time.time):
        require(type(max_scans) is int and 1 <= max_scans <= 100, 'scan_limit')
        require(type(max_claims) is int and 1 <= max_claims <= 16, 'claim_limit')
        require(type(max_concurrent) is int and 1 <= max_concurrent <= max_claims, 'concurrency_limit')
        require(number(seconds, 1, 7200) and number(lease_seconds, 1, seconds), 'time_limit')
        require(number(freshness, 1, 30), 'freshness_limit')
        require(isinstance(publisher, str) and bool(publisher.strip()), 'publisher_required')
        require(isinstance(approvals, list) and 1 <= len(approvals) <= 16, 'fixed_queue_required')
        keys, identifiers = set(), set()
        for a in approvals:
            require(set(a) == {'key', 'approval_id', 'authority_ref', 'definition_sha256',
                               'request_sha256', 'expires', 'priority'}, 'approval_schema')
            require(all(isinstance(a[k], str) and a[k].strip() for k in
                        ('key', 'approval_id', 'authority_ref')), 'approval_identity')
            require(digest(a['definition_sha256']) and digest(a['request_sha256']), 'approval_hash')
            require(number(a['expires'], clock(), clock() + 7200), 'approval_expiry')
            require(type(a['priority']) is int and 0 <= a['priority'] <= 100, 'priority')
            require(a['key'] not in keys and a['approval_id'] not in identifiers, 'duplicate_approval')
            keys.add(a['key']); identifiers.add(a['approval_id'])
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        now = clock()
        c = sqlite3.connect(str(p))
        try:
            with c:
                c.executescript('''
                CREATE TABLE policy(publisher TEXT, max_scans INTEGER, max_claims INTEGER,
                    max_concurrent INTEGER, deadline REAL, lease_seconds REAL,
                    freshness REAL, last_time REAL, stopped TEXT);
                CREATE TABLE approvals(unit TEXT PRIMARY KEY, approval_id TEXT UNIQUE,
                    authority_ref TEXT, definition_sha TEXT, request_sha TEXT,
                    expires REAL, priority INTEGER);
                CREATE TABLE scans(id TEXT PRIMARY KEY, reserved REAL, expires REAL,
                    state TEXT, reason TEXT);
                CREATE TABLE claims(id TEXT PRIMARY KEY, unit TEXT UNIQUE, owner TEXT,
                    token TEXT UNIQUE, expires REAL, state TEXT, board_receipt TEXT,
                    created REAL, board_confirmed REAL);
                CREATE TABLE publications(unit TEXT PRIMARY KEY, claim_id TEXT UNIQUE,
                    result_sha TEXT, payload BLOB, review_sha TEXT, state TEXT,
                    dispatch_token TEXT UNIQUE, receipt TEXT);
                CREATE TABLE events(n INTEGER PRIMARY KEY AUTOINCREMENT, at REAL,
                    action TEXT, unit TEXT, details TEXT);
                ''')
                c.execute('INSERT INTO policy VALUES(?,?,?,?,?,?,?,?,NULL)',
                          (publisher, max_scans, max_claims, max_concurrent, now + seconds,
                           lease_seconds, freshness, now))
                c.executemany('INSERT INTO approvals VALUES(?,?,?,?,?,?,?)',
                              [(a['key'], a['approval_id'], a['authority_ref'],
                                a['definition_sha256'], a['request_sha256'],
                                a['expires'], a['priority']) for a in approvals])
        finally:
            c.close()
        return cls(p, clock)

    @contextmanager
    def transaction(self, administrative=False):
        c = sqlite3.connect(str(self.path), timeout=5)
        c.row_factory = sqlite3.Row
        try:
            c.execute('BEGIN IMMEDIATE')
            p = dict(c.execute('SELECT * FROM policy').fetchone())
            now = self.clock()
            if not number(now, 0, 10**12) or now < p['last_time']:
                c.execute('UPDATE policy SET stopped=COALESCE(stopped,?)', ('clock_moved_backwards',))
                c.commit()
                raise ValueError('clock_moved_backwards')
            c.execute('UPDATE policy SET last_time=?', (now,))
            if not administrative:
                require(not p['stopped'], 'queue_stopped:' + str(p['stopped']))
                if now >= p['deadline']:
                    c.execute('UPDATE policy SET stopped=?', ('queue_deadline',))
                    c.commit()
                    raise ValueError('queue_deadline')
                c.execute("UPDATE claims SET state='expired' WHERE expires<=? AND state IN ('claimed','running')", (now,))
            yield c, p, now
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def event(c, now, action, unit=None, **details):
        c.execute('INSERT INTO events(at,action,unit,details) VALUES(?,?,?,?)',
                  (now, action, unit, json.dumps(details, sort_keys=True)))

    def reserve_scan(self):
        """Call BEFORE reading the service. Failed or abandoned reads still count."""
        with self.transaction() as (c, p, now):
            require(c.execute('SELECT count(*) FROM scans').fetchone()[0] < p['max_scans'], 'scan_limit')
            sid = secrets.token_hex(16)
            c.execute('INSERT INTO scans VALUES(?,?,?,?,NULL)',
                      (sid, now, min(now + p['freshness'], p['deadline']), 'reserved'))
            self.event(c, now, 'scan_reserved', scan=sid)
            return sid

    def discard_scan(self, sid, reason):
        with self.transaction(administrative=True) as (c, p, now):
            require(isinstance(reason, str) and reason.strip(), 'reason_required')
            changed = c.execute("UPDATE scans SET state='failed',reason=? WHERE id=? AND state='reserved'",
                                (reason, sid)).rowcount
            require(changed == 1, 'scan_not_reserved')
            self.event(c, now, 'scan_failed', scan=sid, reason=reason)

    @staticmethod
    def snapshot_reason(s, a, p, now, status):
        if not isinstance(s, dict) or set(s) != {'key', 'type', 'status', 'owner',
                                                'definition_sha256', 'read_at', 'blockers'}:
            return 'snapshot_schema'
        if s['key'] != a['unit'] or s['type'] != 'Task':
            return 'wrong_unit_or_container'
        if s['status'] not in status:
            return 'ineligible_status'
        if s['definition_sha256'] != a['definition_sha']:
            return 'definition_changed'
        if not number(s['read_at'], now - p['freshness'], now):
            return 'stale_or_future_snapshot'
        if not isinstance(s['blockers'], list) or any(
                not isinstance(b, dict) or set(b) != {'key', 'status'} or
                not isinstance(b['key'], str) or b['status'] != 'Done' for b in s['blockers']):
            return 'unfinished_or_unknown_dependency'
        return None

    def claim(self, sid, owner, snapshots):
        """Snapshot authority is the trusted connector, never worker assertions.

        One scan receipt is consumed even when no candidate is eligible.
        Unknown/unregistered items cannot enlarge the fixed reference queue.
        """
        require(isinstance(owner, str) and owner.strip(), 'owner_required')
        with self.transaction() as (c, p, now):
            scan = c.execute('SELECT * FROM scans WHERE id=?', (sid,)).fetchone()
            require(scan is not None and scan['state'] == 'reserved', 'scan_not_reserved')
            c.execute("UPDATE scans SET state='used' WHERE id=?", (sid,))
            if now >= scan['expires']:
                return {'claim': None, 'reason': 'scan_expired'}
            if not isinstance(snapshots, list) or len(snapshots) > 100:
                return {'claim': None, 'reason': 'invalid_snapshot_list'}
            by_key = {}
            for s in snapshots:
                if not isinstance(s, dict) or not isinstance(s.get('key'), str) or s['key'] in by_key:
                    return {'claim': None, 'reason': 'invalid_or_duplicate_snapshot'}
                by_key[s['key']] = s
            if c.execute('SELECT count(*) FROM claims').fetchone()[0] >= p['max_claims']:
                return {'claim': None, 'reason': 'claim_limit'}
            if c.execute("SELECT count(*) FROM claims WHERE state IN ('claimed','running','publishing')").fetchone()[0] >= p['max_concurrent']:
                return {'claim': None, 'reason': 'concurrency_limit'}
            reasons = {}
            for a in c.execute('SELECT * FROM approvals ORDER BY priority DESC,unit').fetchall():
                unit = a['unit']
                if c.execute('SELECT 1 FROM claims WHERE unit=?', (unit,)).fetchone():
                    reasons[unit] = 'authority_consumed'; continue
                if now >= a['expires']:
                    reasons[unit] = 'authority_expired'; continue
                s = by_key.get(unit)
                reason = self.snapshot_reason(s, a, p, now, {'Work Approved', 'Rework Required'})
                if not reason and s['read_at'] < scan['reserved']:
                    reason = 'snapshot_predates_scan'
                if not reason and s['owner'] is not None:
                    reason = 'already_owned'
                if reason:
                    reasons[unit] = reason; continue
                token, cid = secrets.token_hex(32), secrets.token_hex(16)
                expiry = min(now + p['lease_seconds'], p['deadline'], a['expires'])
                c.execute('INSERT INTO claims VALUES(?,?,?,?,?,?,NULL,?,NULL)',
                          (cid, unit, owner, token, expiry, 'claimed', now))
                self.event(c, now, 'claimed', unit, claim=cid, owner=owner,
                           approval=a['approval_id'], scan=sid, expires=expiry)
                return {'claim': {'id': cid, 'unit': unit, 'owner': owner,
                                  'token': token, 'expires': expiry}, 'reason': None}
            self.event(c, now, 'nothing_eligible', reasons=reasons, scan=sid)
            return {'claim': None, 'reason': 'nothing_eligible', 'details': reasons}

    @staticmethod
    def owned(c, now, token, states):
        r = c.execute('SELECT * FROM claims WHERE token=?', (token,)).fetchone()
        require(r is not None and r['state'] in states and now < r['expires'], 'stale_or_wrong_owner')
        return r

    def confirm_board_claim(self, token, snapshot, transition_receipt):
        """Trusted coordinator supplies a fresh read AFTER its Jira transition."""
        require(isinstance(transition_receipt, str) and transition_receipt.strip(), 'transition_receipt_required')
        with self.transaction() as (c, p, now):
            r = self.owned(c, now, token, {'claimed'})
            a = c.execute('SELECT * FROM approvals WHERE unit=?', (r['unit'],)).fetchone()
            require(self.snapshot_reason(snapshot, a, p, now, {'In Progress'}) is None, 'board_claim_not_verified')
            require(snapshot['read_at'] >= r['created'], 'board_read_predates_claim')
            require(snapshot['owner'] == r['owner'], 'board_owner_mismatch')
            c.execute("UPDATE claims SET state='running',board_receipt=?,board_confirmed=? WHERE id=?",
                      (transition_receipt, now, r['id']))
            self.event(c, now, 'board_claim_verified', r['unit'], receipt=transition_receipt)

    def validate_launch(self, token):
        with self.transaction() as (c, p, now):
            r = self.owned(c, now, token, {'running'})
            return {'unit': r['unit'], 'expires': min(p['deadline'], r['expires'])}

    def stage_result(self, token, payload, proof):
        """Trusted adapter supplies bound runner evidence; no model verdict alone."""
        require(isinstance(payload, bytes) and 0 < len(payload) <= 1_000_000, 'payload_size')
        require(isinstance(proof, dict) and set(proof) == {
            'result_sha256', 'request_sha256', 'review_sha256',
            'disposition', 'deterministic_checks_pass'}, 'result_proof_schema')
        require(proof['result_sha256'] == sha(payload) and digest(proof['review_sha256']) and
                proof['disposition'] == 'READY_FOR_PUBLICATION' and
                proof['deterministic_checks_pass'] is True, 'result_not_verified')
        with self.transaction() as (c, p, now):
            r = self.owned(c, now, token, {'running'})
            a = c.execute('SELECT * FROM approvals WHERE unit=?', (r['unit'],)).fetchone()
            require(proof['request_sha256'] == a['request_sha'], 'wrong_request')
            require(not c.execute('SELECT 1 FROM publications WHERE unit=?', (r['unit'],)).fetchone(), 'result_slot_consumed')
            c.execute('INSERT INTO publications VALUES(?,?,?,?,?,?,NULL,NULL)',
                      (r['unit'], r['id'], sha(payload), payload, proof['review_sha256'], 'staged'))
            self.event(c, now, 'result_staged', r['unit'], result_sha=sha(payload))

    def begin_publication(self, token, publisher, snapshot):
        """Reserve once BEFORE external write. No automatic resend after uncertainty."""
        with self.transaction() as (c, p, now):
            require(publisher == p['publisher'], 'wrong_publisher')
            r = self.owned(c, now, token, {'running'})
            a = c.execute('SELECT * FROM approvals WHERE unit=?', (r['unit'],)).fetchone()
            require(self.snapshot_reason(snapshot, a, p, now, {'In Progress'}) is None and
                    snapshot['owner'] == r['owner'], 'publication_status_or_owner_changed')
            require(snapshot['read_at'] >= r['board_confirmed'], 'publication_read_predates_board_claim')
            result = c.execute('SELECT * FROM publications WHERE unit=?', (r['unit'],)).fetchone()
            require(result is not None and result['state'] == 'staged', 'publication_slot_consumed')
            dispatch = secrets.token_hex(32)
            c.execute("UPDATE publications SET state='unsettled',dispatch_token=? WHERE unit=?", (dispatch, r['unit']))
            c.execute("UPDATE claims SET state='publishing' WHERE id=?", (r['id'],))
            self.event(c, now, 'publication_reserved', r['unit'], result_sha=result['result_sha'])
            return {'dispatch_token': dispatch, 'unit': r['unit'],
                    'result_sha256': result['result_sha'], 'payload': bytes(result['payload'])}

    def settle_publication(self, dispatch_token, publisher, saved_hash, receipt):
        # Settlement only records already observed effects. It is allowed after
        # cancellation/deadline; it never authorises another external write.
        require(isinstance(receipt, str) and receipt.strip(), 'saved_receipt_required')
        with self.transaction(administrative=True) as (c, p, now):
            require(publisher == p['publisher'], 'wrong_publisher')
            r = c.execute('SELECT * FROM publications WHERE dispatch_token=?', (dispatch_token,)).fetchone()
            require(r is not None and r['state'] == 'unsettled', 'dispatch_not_pending')
            require(saved_hash == r['result_sha'], 'saved_bytes_differ')
            c.execute("UPDATE publications SET state='verified',receipt=? WHERE unit=?", (receipt, r['unit']))
            c.execute("UPDATE claims SET state='delivered' WHERE id=?", (r['claim_id'],))
            self.event(c, now, 'publication_verified', r['unit'], receipt=receipt)

    def stop(self, reason):
        require(isinstance(reason, str) and reason.strip(), 'reason_required')
        with self.transaction(administrative=True) as (c, p, now):
            require(not p['stopped'], 'already_stopped')
            c.execute('UPDATE policy SET stopped=?', (reason,))
            self.event(c, now, 'stopped', reason=reason)

    def inspect(self):
        c = sqlite3.connect(str(self.path)); c.row_factory = sqlite3.Row
        try:
            return {'policy': dict(c.execute('SELECT * FROM policy').fetchone()),
                    'scans': [dict(x) for x in c.execute('SELECT * FROM scans')],
                    'claims': [dict(x) for x in c.execute('SELECT id,unit,owner,expires,state,board_receipt,created,board_confirmed FROM claims')],
                    'publications': [dict(x) for x in c.execute('SELECT unit,claim_id,result_sha,review_sha,state,receipt FROM publications')],
                    'events': [dict(x) for x in c.execute('SELECT * FROM events ORDER BY n')],
                    'human_acceptance': 'not granted by this controller'}
        finally:
            c.close()
