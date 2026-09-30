"""Recovery journal in the original DEV-79 queue; original DEV-80 budget retained.

One trusted coordinator, local file effects and a fixed-object conditional-write
handoff. No credentials, remote client, or universal exactly-once remote IO.
"""
from pathlib import Path
import hashlib
import json
import os
import secrets
import sqlite3

from upstream.pickup import Queue, require, sha
from upstream.controller import Controller


def encoded(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def current_claim(queue, token, snapshot, states=('running', 'publishing')):
    """Trusted connector snapshot, checked immediately before a new effect."""
    with queue.transaction() as (c, p, now):
        claim = queue.owned(c, now, token, set(states))
        approval = c.execute('SELECT * FROM approvals WHERE unit=?', (claim['unit'],)).fetchone()
        require(now < approval['expires'], 'approval_expired')
        require(queue.snapshot_reason(snapshot, approval, p, now, {'In Progress'}) is None,
                'current_status_definition_or_dependency_invalid')
        require(snapshot['owner'] == claim['owner'] and snapshot['read_at'] >= claim['board_confirmed'],
                'current_owner_or_read_invalid')
        return dict(claim)


class Recovery:
    @classmethod
    def install(cls, queue, budget, publisher, maximum_resumed_writes=2):
        require(type(maximum_resumed_writes) is int and 0 <= maximum_resumed_writes <= 6, 'finite_recovery_limit')
        with queue.transaction(administrative=True) as (c, p, now):
            require(publisher == p['publisher'], 'wrong_publisher')
            require(not c.execute("SELECT 1 FROM sqlite_master WHERE name='recovery_policy'").fetchone(),
                    'recovery_already_installed')
            # No executescript: keep this migration inside the same transaction.
            for sql in [
                'CREATE TABLE recovery_policy(queue_path TEXT,budget_path TEXT,maximum INTEGER,used INTEGER)',
                'CREATE TABLE recovery_context(unit TEXT,sequence INTEGER,request BLOB,candidate BLOB,review BLOB,hashes TEXT,PRIMARY KEY(unit,sequence))',
                'CREATE TABLE recovery_effect(unit TEXT PRIMARY KEY,target TEXT UNIQUE,state TEXT,writes INTEGER,last_observation TEXT)',
                'CREATE TABLE recovery_service(unit TEXT PRIMARY KEY,target TEXT,prior_version INTEGER,marker TEXT,profile TEXT,writes INTEGER,last_version INTEGER)',
                'CREATE TABLE recovery_grants(id TEXT PRIMARY KEY,unit TEXT,operation TEXT,payload TEXT,fingerprint TEXT,expires REAL,used INTEGER,reference TEXT)',
            ]: c.execute(sql)
            c.execute('INSERT INTO recovery_policy VALUES(?,?,?,0)',
                      (str(queue.path.resolve()), str(budget.path.resolve()), maximum_resumed_writes))
            queue.event(c, now, 'recovery_installed', maximum=maximum_resumed_writes,
                        queue_path=str(queue.path.resolve()), budget_path=str(budget.path.resolve()))
        return cls(queue, budget, publisher)

    def __init__(self, queue, budget, publisher):
        self.queue, self.budget, self.publisher = queue, budget, publisher
        with queue.transaction(administrative=True) as (c, p, now):
            r = c.execute('SELECT * FROM recovery_policy').fetchone()
            require(r is not None and r['queue_path'] == str(queue.path.resolve()) and
                    r['budget_path'] == str(budget.path.resolve()), 'registered_original_stores_required')
            require(publisher == p['publisher'], 'wrong_publisher')

    def checkpoint(self, unit, request, candidate, review):
        """Append exact bytes including unresolved findings; never confer publication."""
        require(all(isinstance(x, bytes) and 0 < len(x) <= 1_000_000 for x in (request,candidate,review)),
                'bounded_context_bytes_required')
        with self.queue.transaction(administrative=True) as (c, p, now):
            a = c.execute('SELECT * FROM approvals WHERE unit=?', (unit,)).fetchone()
            require(a is not None and sha(request) == a['request_sha'], 'original_request_required')
            sequence = c.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM recovery_context WHERE unit=?', (unit,)).fetchone()[0]
            hashes = {'request':sha(request),'candidate':sha(candidate),'review':sha(review)}
            c.execute('INSERT INTO recovery_context VALUES(?,?,?,?,?,?)',
                      (unit, sequence, request, candidate, review, json.dumps(hashes)))
            self.queue.event(c, now, 'context_checkpoint', unit, sequence=sequence, hashes=hashes)
        return sequence

    def restore(self, unit):
        with self.queue.transaction(administrative=True) as (c, p, now):
            rows = [dict(r) for r in c.execute('SELECT * FROM recovery_context WHERE unit=? ORDER BY sequence', (unit,))]
            require(bool(rows), 'context_not_recorded')
            for r in rows:
                hashes = json.loads(r['hashes'])
                require(all(sha(bytes(r[k])) == hashes[k] for k in ['request','candidate','review']), 'context_corrupt')
            return rows

    def prepare_file(self, unit, target):
        """Bind a fixed local destination before reserving the first publication."""
        target = Path(target).absolute()
        require(target.parent.resolve() == target.parent and not target.is_symlink(), 'canonical_non_symlink_target')
        context = self.restore(unit)[-1]
        with self.queue.transaction() as (c, p, now):
            r = c.execute('SELECT * FROM publications WHERE unit=?', (unit,)).fetchone()
            require(r is not None and r['state'] == 'staged', 'staged_verified_result_required')
            require(sha(context['candidate']) == r['result_sha'] and sha(context['review']) == r['review_sha'],
                    'context_does_not_match_verified_delivery')
            c.execute('INSERT INTO recovery_effect VALUES(?,?,?,0,NULL)', (unit,str(target),'prepared'))
            self.queue.event(c, now, 'effect_prepared', unit, target=str(target), result_sha=r['result_sha'])

    def _effect(self, unit):
        with self.queue.transaction(administrative=True) as (c, p, now):
            e = c.execute('SELECT * FROM recovery_effect WHERE unit=?', (unit,)).fetchone()
            r = c.execute('SELECT * FROM publications WHERE unit=?', (unit,)).fetchone()
            require(e is not None and r is not None, 'registered_effect_required')
            return dict(e), dict(r)

    def prepare_service(self, unit, target, prior_version, conditional_profile):
        """Trusted connector contract: a stable object with atomic version checking.

        No create-by-title, append/email/send operation, or unverified retry-safe
        claim is supported. The operator qualifies the named connector profile.
        """
        require(isinstance(target,str) and target.strip() and type(prior_version) is int and prior_version>=1,
                'fixed_existing_object_and_version_required')
        require(isinstance(conditional_profile,str) and conditional_profile.strip(),'qualified_conditional_profile_required')
        context=self.restore(unit)[-1]
        with self.queue.transaction() as (c,p,now):
            r=c.execute('SELECT * FROM publications WHERE unit=?',(unit,)).fetchone()
            require(r is not None and r['state']=='staged' and sha(context['candidate'])==r['result_sha'] and
                    sha(context['review'])==r['review_sha'],'verified_service_payload_required')
            marker=sha(encoded({'unit':unit,'target':target,'prior_version':prior_version,'payload':r['result_sha']}))
            c.execute('INSERT INTO recovery_effect VALUES(?,?,?,0,NULL)',(unit,'service:'+target,'prepared'))
            c.execute('INSERT INTO recovery_service VALUES(?,?,?,?,?,0,?)',
                      (unit,target,prior_version,marker,conditional_profile,prior_version))
            self.queue.event(c,now,'conditional_service_prepared',unit,target=target,version=prior_version,marker=marker)

    def service_intent(self, token, snapshot, observation, *, resumed=False):
        """Reserve one conditional update, then return its immutable write contract.

        Trusted connector observation: target, version, read_at, receipt. The
        connector must enforce expected_version atomically on the actual write.
        Unknown or changed state never permits blind resend.
        """
        claim=current_claim(self.queue,token,snapshot);unit=claim['unit'];self._budget_allows_effect(unit)
        e,r=self._effect(unit)
        require(r['state']==('unsettled' if resumed else 'staged'),'publication_state_not_eligible')
        with self.queue.transaction() as (c,p,now):
            service=c.execute('SELECT * FROM recovery_service WHERE unit=?',(unit,)).fetchone()
            require(service is not None,'registered_service_required')
            require(isinstance(observation,dict) and observation.get('target')==service['target'] and
                    type(observation.get('version')) is int and
                    observation.get('version')==service['prior_version'] and
                    type(observation.get('read_at')) in (int,float) and now-p['freshness']<=observation['read_at']<=now and
                    isinstance(observation.get('receipt'),str) and bool(observation['receipt'].strip()),
                    'fresh_unchanged_authoritative_service_read_required')
            require(e['state'] in (('uncertain',) if resumed else ('prepared',)),'service_stop_requires_manual_handling')
            if resumed:
                rp=c.execute('SELECT * FROM recovery_policy').fetchone()
                require(rp['used']<rp['maximum'],'recovery_write_limit')
                c.execute('UPDATE recovery_policy SET used=used+1')
                self.queue.event(c,now,'conditional_retry_reserved',unit,observation=observation)
        if not resumed:self.queue.begin_publication(token,self.publisher,snapshot)
        # Re-check after the reservation. A failed check leaves the original
        # consumed publication/allowance uncertain; it never creates a new slot.
        current_claim(self.queue,token,snapshot);self._budget_allows_effect(unit)
        with self.queue.transaction() as (c,p,now):
            c.execute("UPDATE recovery_effect SET state='uncertain',writes=writes+1 WHERE unit=?",(unit,))
            c.execute('UPDATE recovery_service SET writes=writes+1 WHERE unit=?',(unit,))
            self.queue.event(c,now,'conditional_write_dispatched',unit,target=service['target'],expected_version=service['prior_version'])
        return {'unit':unit,'target':service['target'],'expected_version':service['prior_version'],
                'operation_marker':service['marker'],'payload':bytes(r['payload']),'result_sha256':r['result_sha'],
                'qualified_profile':service['profile']}

    def observe_service(self, unit, observation):
        """Record a trusted fresh read of an existing effect. Never perform a write."""
        e,r=self._effect(unit)
        with self.queue.transaction(administrative=True) as (c,p,now):
            s=c.execute('SELECT * FROM recovery_service WHERE unit=?',(unit,)).fetchone()
            require(s is not None and isinstance(observation,dict) and
                    observation.get('target')==s['target'] and type(observation.get('version')) is int and
                    type(observation.get('read_at')) in (int,float) and now-p['freshness']<=observation['read_at']<=now and
                    isinstance(observation.get('receipt'),str) and bool(observation['receipt'].strip()),
                    'fresh_service_observation_required')
            matched=(observation['version']==s['prior_version']+1 and
                     observation.get('operation_marker')==s['marker'] and observation.get('result_sha256')==r['result_sha'])
            state='match' if matched else 'unchanged' if observation['version']==s['prior_version'] else 'conflict'
            c.execute('UPDATE recovery_service SET last_version=? WHERE unit=?',(observation['version'],unit))
            self.queue.event(c,now,'service_observed',unit,observation=observation,state=state,no_new_service_write=True)
        if state=='match':
            require(r['state'] in ('unsettled','verified'),'service_effect_not_reserved')
            if r['state']=='unsettled':self.queue.settle_publication(r['dispatch_token'],self.publisher,r['result_sha'],observation['receipt'])
            with self.queue.transaction(administrative=True) as (c,p,now):
                c.execute("UPDATE recovery_effect SET state='verified',last_observation='match' WHERE unit=?",(unit,))
        elif state=='conflict':self.mark_manual(unit,'service content/version changed; do not overwrite')
        return state

    def _budget_allows_effect(self, unit):
        s = self.budget.snapshot(); by_id = {r['id']:r for r in s['scopes']}
        require(unit in by_id, 'budget_unit_not_registered')
        chain=[]; k=unit
        while k is not None: chain.append(k); k=by_id[k]['parent']
        now=self.budget.clock()
        require(all(now < by_id[k]['policy']['deadline'] for k in chain), 'budget_deadline')
        require(not any(x['active'] and x['scope'] in chain for x in s['stops']), 'budget_stopped')
        require(not any(x['state'] in ('reserved','launching','unsettled') for x in s['attempts']), 'reconcile_process_first')
        # Used model slots are never decremented. Publication is not another CLI.

    @staticmethod
    def _write_exclusive(target, payload):
        # A fixed path and O_EXCL prevent a second local file creation. A partial
        # file is preserved as a conflict; it is never overwritten automatically.
        with open(target, 'xb') as f:
            os.chmod(target, 0o600); f.write(payload); f.flush(); os.fsync(f.fileno())

    def _attempt_write(self, unit, token, snapshot, *, after_write=None):
        current_claim(self.queue,token,snapshot)
        self._budget_allows_effect(unit)
        e, r = self._effect(unit)
        with self.queue.transaction() as (c, p, now):
            c.execute("UPDATE recovery_effect SET state='uncertain',writes=writes+1 WHERE unit=?", (unit,))
            self.queue.event(c, now, 'effect_write_started', unit)
        try:
            self._write_exclusive(e['target'], bytes(r['payload']))
            if after_write: after_write()  # Fault-injection boundary; no production retry.
        except PermissionError:
            self.mark_manual(unit, 'permission failure; verified authority needed')
            raise
        except Exception:
            # Preserve uncertainty even if the caller or service lost the reply.
            raise
        return self.inspect_file(unit)

    def publish_file(self, token, snapshot, *, after_write=None):
        claim = current_claim(self.queue, token, snapshot, ('running',))
        self._budget_allows_effect(claim['unit'])
        e, r = self._effect(claim['unit']); require(e['state']=='prepared', 'effect_already_started')
        self.queue.begin_publication(token, self.publisher, snapshot)
        return self._attempt_write(claim['unit'], token, snapshot, after_write=after_write)

    def inspect_file(self, unit):
        """Reconcile existing bytes, including after expiry/stop; never write output."""
        e, r = self._effect(unit);require(not e['target'].startswith('service:'),'use_service_observation');p=Path(e['target'])
        try:
            if p.is_symlink(): observation='conflict'
            elif not p.exists(): observation='absent'
            elif not p.is_file(): observation='conflict'
            else: observation='match' if sha(p.read_bytes())==r['result_sha'] else 'conflict'
        except OSError: observation='unknown'
        if observation=='match':
            require(r['state'] in ('unsettled','verified'), 'effect_before_publication_reservation')
            if r['state']=='unsettled':
                self.queue.settle_publication(r['dispatch_token'], self.publisher, r['result_sha'], 'file:'+str(p))
        with self.queue.transaction(administrative=True) as (c, policy, now):
            state='verified' if observation=='match' else 'manual' if observation=='conflict' else e['state']
            c.execute('UPDATE recovery_effect SET state=?,last_observation=? WHERE unit=?', (state, observation, unit))
            self.queue.event(c, now, 'effect_observed', unit, observation=observation, no_new_output=True)
        return observation

    def _fingerprint(self, c, unit):
        claim=c.execute('SELECT id,owner,expires,state,board_confirmed FROM claims WHERE unit=?',(unit,)).fetchone()
        effect=c.execute('SELECT target,state,writes,last_observation FROM recovery_effect WHERE unit=?',(unit,)).fetchone()
        return sha(encoded({'claim':dict(claim) if claim else None,'effect':dict(effect) if effect else None}))

    def register_authority(self, unit, operation, payload, reference, expires):
        """Trusted registrar must verify the real external decision beforehand."""
        require(operation in ('renew_claim','resume_write') and isinstance(reference,str) and reference.strip(), 'authority_required')
        with self.queue.transaction(administrative=True) as (c,p,now):
            require(now < expires <= p['deadline'], 'finite_authority_expiry')
            gid=secrets.token_hex(16)
            c.execute('INSERT INTO recovery_grants VALUES(?,?,?,?,?,?,0,?)',
                      (gid,unit,operation,encoded(payload).decode(),self._fingerprint(c,unit),expires,reference))
            self.queue.event(c,now,'recovery_authority_registered',unit,grant=gid,operation=operation,reference=reference)
            return gid

    def _consume(self,c,now,gid,unit,operation,payload):
        g=c.execute('SELECT * FROM recovery_grants WHERE id=?',(gid,)).fetchone()
        require(g is not None and not g['used'] and now<g['expires'] and g['unit']==unit and
                g['operation']==operation and g['payload']==encoded(payload).decode() and
                g['fingerprint']==self._fingerprint(c,unit),'missing_consumed_or_mismatched_authority')
        c.execute('UPDATE recovery_grants SET used=1 WHERE id=?',(gid,))
        self.queue.event(c,now,'recovery_authority_consumed',unit,grant=gid)

    def renew_claim(self, unit, snapshot, new_expiry, evidence, grant):
        """Explicit lease amendment, never a new claim or restored process budget."""
        self._budget_allows_effect(unit)
        payload={'expires':new_expiry,'evidence':evidence}
        require(isinstance(evidence,str) and evidence.strip(), 'process_and_effect_inspection_reference_required')
        with self.queue.transaction(administrative=True) as (c,p,now):
            require(not p['stopped'] and now<p['deadline'],'queue_stopped_or_expired')
            a=c.execute('SELECT * FROM approvals WHERE unit=?',(unit,)).fetchone()
            claim=c.execute('SELECT * FROM claims WHERE unit=?',(unit,)).fetchone()
            require(claim is not None and claim['state'] in ('running','expired','publishing'), 'renewable_claim_required')
            require(self.queue.snapshot_reason(snapshot,a,p,now,{'In Progress'}) is None and
                    snapshot['owner']==claim['owner'], 'fresh_same_owner_and_status_required')
            require(now < new_expiry <= min(p['deadline'],a['expires']), 'renewal_cannot_extend_approved_window')
            self._consume(c,now,grant,unit,'renew_claim',payload)
            token=secrets.token_hex(32)
            publication=c.execute('SELECT state FROM publications WHERE unit=?',(unit,)).fetchone()
            state='publishing' if publication and publication['state']=='unsettled' else 'running'
            c.execute('UPDATE claims SET token=?,expires=?,state=?,board_confirmed=? WHERE id=?',
                      (token,new_expiry,state,now,claim['id']))
            self.queue.event(c,now,'claim_renewed',unit,claim=claim['id'],old_expiry=claim['expires'],
                             new_expiry=new_expiry,old_token_sha=sha(claim['token'].encode()),evidence=evidence)
            return token

    def resume_file(self, token, snapshot, evidence, *, grant=None):
        claim=current_claim(self.queue,token,snapshot);unit=claim['unit']
        self._budget_allows_effect(unit)
        e,r=self._effect(unit)
        require(e['state']!='verified' and r['state']=='unsettled','no_pending_effect')
        require(isinstance(evidence,str) and evidence.strip(),'resolution_evidence_required')
        # Re-read bytes before deciding. A completed effect is settled, not resent.
        observation=self.inspect_file(unit)
        if observation=='match':return 'match'
        require(observation=='absent','uncertain_or_conflicting_effect_requires_manual_decision')
        e,r=self._effect(unit)
        with self.queue.transaction() as (c,p,now):
            rp=c.execute('SELECT * FROM recovery_policy').fetchone()
            require(rp['used']<rp['maximum'],'recovery_write_limit')
            if e['state']=='manual':
                self._consume(c,now,grant,unit,'resume_write',{'evidence':evidence})
            elif grant is not None:
                self._consume(c,now,grant,unit,'resume_write',{'evidence':evidence})
            c.execute('UPDATE recovery_policy SET used=used+1')
            self.queue.event(c,now,'recovery_write_reserved',unit,evidence=evidence,automatic=grant is None)
        # Single coordinator only: no worker or concurrent process may bypass this
        # admission. An interrupted reservation stays consumed even before IO.
        return self._attempt_write(unit, token, snapshot)

    def mark_manual(self, unit, reason):
        require(isinstance(reason,str) and reason.strip(),'stop_reason_required')
        with self.queue.transaction(administrative=True) as (c,p,now):
            changed=c.execute("UPDATE recovery_effect SET state='manual' WHERE unit=? AND state!='verified'",(unit,)).rowcount
            require(changed==1,'pending_effect_required')
            self.queue.event(c,now,'recovery_manual_stop',unit,reason=reason)

    def inspect(self):
        with self.queue.transaction(administrative=True) as (c,p,now):
            return {'policy':dict(c.execute('SELECT * FROM recovery_policy').fetchone()),
                    'effects':[dict(x) for x in c.execute('SELECT * FROM recovery_effect')],
                    'services':[dict(x) for x in c.execute('SELECT * FROM recovery_service')],
                    'grants':[dict(x) for x in c.execute('SELECT id,unit,operation,expires,used,reference FROM recovery_grants')],
                    'context_versions':[dict(x) for x in c.execute('SELECT unit,sequence,hashes FROM recovery_context')],
                    'queue':self.queue.inspect(), 'budget':self.budget.snapshot()}
