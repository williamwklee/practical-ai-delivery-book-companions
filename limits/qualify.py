"""Run eight finite fictional adverse-case groups; no AI or shared-tool calls."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time

from controller import Controller, Denied, policy
from adapter_bridge import run_guarded


def qualification(output):
    root = Path(output).resolve(); root.mkdir(parents=True, exist_ok=False, mode=0o700)
    groups = []
    for name in ['product_warning', 'observed_usage', 'limit', 'no_progress',
                 'transient', 'missing_evidence', 'out_of_authority', 'manual']:
        folder = root / name; folder.mkdir()
        now = time.time(); owner = 'FICTIONAL-COORDINATOR'
        p = policy(now + 60, launches=1 if name == 'limit' else 6, reviews=4,
                   observed_tokens=5 if name == 'observed_usage' else None,
                   actions=['draft', 'review'])
        c = Controller.create(folder / 'private.sqlite', 'FICTIONAL-CAMPAIGN', owner,
            policy(now + 70, launches=12, cycles=8, reviews=8),
            {'DEMO': {'parent': '@all', 'policy': p}}, 'fixture:initial approval, not real authority')
        c.start_cycle('DEMO', owner)

        def complete(failure=None, role='producer', findings=None, optional_usage=10):
            r = c.reserve('DEMO', owner, role, 'draft', 'fixture:request')
            result = dict(process_ended=True, terminal_valid=failure is None, exit_code=0 if failure is None else 7,
                          result_sha256=hashlib.sha256(b'fixture output').hexdigest(), binding='fixture:request',
                          failure=failure, observed_tokens=optional_usage, findings_fingerprint=findings,
                          partial=['fictional-partial.txt'])
            c.settle(r['id'], owner, result)
            return r

        def deny(call):
            try: call()
            except Denied as e: return str(e)
            raise AssertionError('expected refusal')

        if name == 'product_warning': complete('product_warning')
        elif name == 'observed_usage': complete()
        elif name == 'limit':
            complete()
            deny(lambda: c.reserve('DEMO', owner, 'producer', 'draft', 'renamed-request'))
        elif name == 'no_progress':
            complete(role='checker', findings='a' * 64)
            complete(role='checker', findings='a' * 64)
        elif name == 'transient': complete('transient')
        elif name == 'missing_evidence':
            pending = c.reserve('DEMO', owner, 'producer', 'draft', 'fixture:request')
            c.settle(pending['id'], owner, {'partial': ['fictional-partial.txt']})
        elif name == 'out_of_authority': deny(lambda: c.reserve('DEMO', owner, 'producer', 'publish', 'fixture:request'))
        else: c.stop('DEMO', owner, 'manual', 'fixture:user pressed stop', ['fictional-partial.txt'])

        stopped = c.snapshot()
        c = Controller(folder / 'private.sqlite')
        refusal = deny(lambda: c.reserve('DEMO', owner, 'producer', 'draft', 'renamed-request'))
        stop = next(s for s in stopped['stops'] if s['active'])
        evidence = 'fixture:condition inspected and resolved'
        if name == 'missing_evidence':
            deny(lambda: c.resume(stop['id'], owner, evidence))
            c.reconcile(pending['id'], owner, dict(process_ended=True, effects_checked=True, reference='fixture:retained-evidence inspection'))
        if name in ('observed_usage', 'limit'):
            replacement = dict(p, launches=6, observed_tokens=30 if name == 'observed_usage' else None)
            deny(lambda: c.amend('DEMO', owner, replacement, None))
            grant = c.register_authority(owner, 'DEMO', 'amend', replacement,
                'fixture:William-amendment', 'SYNTHETIC decision: raise the fixture limit', now + 60)
            c.amend('DEMO', owner, replacement, grant)
        plan = 'fixture:changed-source-checking-method' if name == 'no_progress' else None
        payload = dict(stop_id=stop['id'], evidence=evidence, new_plan=plan)
        if name == 'transient':
            c.resume(stop['id'], owner, evidence)
            complete('transient', role='retry')
            last_stop = next(s for s in c.snapshot()['stops'] if s['active'])
            retry_refusal = deny(lambda: c.resume(last_stop['id'], owner, evidence))
        else:
            deny(lambda: c.resume(stop['id'], owner, evidence, new_plan=plan))
            grant = c.register_authority(owner, 'DEMO', 'resume', payload,
                'fixture:William-resumption', 'SYNTHETIC decision: resume this exact fixture stop', now + 60)
            c.resume(stop['id'], owner, evidence, grant, plan)
            complete(optional_usage=None)
            retry_refusal = None
        after_resume = c.snapshot()
        c.stop('@all', owner, 'manual', 'fixture qualification finished; no more work permitted')
        proof = dict(group=name, scope='synthetic controller decisions, no model or service process',
                     refused_after_reopen=refusal, stopped=stopped, after_resume=after_resume,
                     retry_exhaustion_refusal=retry_refusal, final_stopped=c.snapshot())
        (folder / 'evidence.json').write_text(json.dumps(proof, indent=2))
        groups.append(dict(group=name, file=name + '/evidence.json',
                           sha256=hashlib.sha256((folder / 'evidence.json').read_bytes()).hexdigest()))

    physical = root / 'physical-adapter'; physical.mkdir()
    c = Controller.create(physical / 'private.sqlite', 'SYNTHETIC-PROCESS', 'fixture-owner', policy(time.time() + 60),
        {'DEMO': {'parent': '@all', 'policy': policy(time.time() + 50, launches=1)}}, 'fixture:local process only')
    c.start_cycle('DEMO', 'fixture-owner')
    prompt = physical / 'prompt.txt'; prompt.write_text('Synthetic fixture only. No AI call.')
    kwargs = dict(controller=c, unit='DEMO', owner='fixture-owner', role='producer', action='draft',
        prompt=prompt, argv=[sys.executable, str(Path(__file__).parent / 'upstream/fixture_worker.py'), 'codex_ok'],
        provider='codex', model='fixture-model', context={}, synthetic=True,
        claim_deadline=time.time() + 20, timeout=3, first_output_timeout=1, idle_timeout=1)
    result = run_guarded(folder=physical / 'first', **kwargs)
    try: run_guarded(folder=physical / 'renamed-second', **kwargs)
    except Denied as e: refusal = str(e)
    else: raise AssertionError('second process was admitted')
    if (physical / 'renamed-second').exists(): raise AssertionError('denial occurred after creating the launch directory')
    actual = dict(scope='one actual local Python fixture process, not an AI call',
                  first=result, second_refusal=refusal, second_folder_created=False,
                  actual_subprocesses=1, new_AI_processes=0, ledger=c.snapshot())
    (physical / 'evidence.json').write_text(json.dumps(actual, indent=2))
    index = dict(groups=groups, count=8, actual_fixture_subprocesses=1, new_AI_processes=0,
                 real_quota_exhausted=False, real_service_writes=0,
                 physical_evidence='physical-adapter/evidence.json',
                 authority_records='fictional test inputs, not William decisions')
    (root / 'index.json').write_text(json.dumps(index, indent=2))
    return index


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', required=True)
    print(json.dumps(qualification(p.parse_args().output), indent=2))
