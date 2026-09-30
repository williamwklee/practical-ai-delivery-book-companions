"""Admission and settlement around the unchanged, qualified CLI process adapter.

No service credentials, login, model selection or retry policy is provisioned.
The caller supplies its already-authorised consumer profile and current claim.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import time

from controller import digest

_spec = importlib.util.spec_from_file_location('qualified_dev78_adapter', Path(__file__).parent / 'upstream/adapter.py')
_adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_adapter)


def observed_usage(record, provider):
    parsed = record.get('result', {})
    usage = parsed.get('usage')
    if parsed.get('usage_valid') is not True or not isinstance(usage, dict):
        return None
    if provider == 'codex':
        # Cached input is a subset of input, not an extra quantity.
        return usage['input_tokens'] + usage['output_tokens']
    return sum(v[k] for v in usage.values() for k in
               ['inputTokens', 'outputTokens', 'cacheReadInputTokens', 'cacheCreationInputTokens'])


def manual_stop(controller, scope, owner, detail):
    """Persist the decision, then signal this campaign's running adapter.

    A failed signal-file write leaves the persisted stop in force. The adapter's
    independent finite timeout still applies. Local termination is not proof
    that remote provider work ceased immediately.
    """
    stop_id = controller.stop(scope, owner, 'manual', detail)
    signal = controller.path.with_suffix('.STOP')
    with signal.open('w') as f:
        f.write(stop_id + '\n')
    signal.chmod(0o600)
    return stop_id


def run_guarded(controller, unit, owner, role, action, prompt, folder, argv,
                provider, model, context, *, synthetic=False, env=None,
                claim_deadline, cancel_path=None, timeout=30, first_output_timeout=10,
                idle_timeout=15, findings_classifier=None):
    """Trusted coordinator call. claim_deadline comes from Queue.validate_launch.

    Synthetic=True is only for local fault injection; never use it to represent
    an actual consumer CLI process. This bridge produces no shared-tool writes.
    """
    prompt = Path(prompt).resolve()
    folder = Path(folder).resolve()
    signal = controller.path.with_suffix('.STOP')
    if cancel_path is not None and Path(cancel_path).resolve() != signal:
        raise ValueError('campaign_cancel_path_required')
    cancel_path = str(signal)
    now = time.time()
    if claim_deadline <= now or (cancel_path and Path(cancel_path).exists()):
        controller.stop(unit, owner, 'manual', 'expired external claim or manual stop before launch')
        raise ValueError('expired_claim_or_manual_stop')
    request_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()
    binding = digest({'unit': unit, 'role': role, 'action': action, 'prompt': request_hash,
                      'argv': argv, 'provider': provider, 'model': model, 'context': context,
                      'claim_deadline': claim_deadline, 'synthetic': synthetic})
    reservation = controller.reserve(unit, owner, role, action, binding)
    # A known failure before the adapter call has a truthful, consumed outcome.
    try:
        folder.mkdir(parents=True, exist_ok=False, mode=0o700)
        deadline = min(reservation['deadline'], claim_deadline, time.time() + timeout)
        ctx = dict(context, run_id=reservation['id'])
        receipt = {'reserved': True, 'mode': 'consumer_subscription', 'unit': unit,
                   'run_id': reservation['id'], 'argv_sha256': hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
                   'prompt_sha256': request_hash, 'expires_unix': deadline}
        receipt_path = folder / 'reservation.json'
        with receipt_path.open('x') as f: json.dump(receipt, f, indent=2)
        receipt_path.chmod(0o600)
        remaining = deadline - time.time()
        if remaining <= 0:
            controller.stop(unit, owner, 'limit', 'launch deadline exceeded', [str(receipt_path)], counter='deadline')
            raise ValueError('expired_before_process')
        controller.begin_launch(reservation['id'], owner)
    except Exception as error:
        failure = ('transient' if isinstance(error, FileExistsError) else
                   'out_of_authority' if isinstance(error, PermissionError) else 'missing_evidence')
        controller.abandon_before_launch(reservation['id'], owner, type(error).__name__, [str(folder)], failure)
        raise
    # From this boundary a crash is uncertain until actual process evidence is reconciled.
    record = _adapter.run(argv, str(prompt), str(folder / 'run'), provider, model, role, unit,
        timeout=min(timeout, remaining), first_output_timeout=min(first_output_timeout, remaining),
        idle_timeout=min(idle_timeout, remaining), max_output_bytes=2_000_000, grace=.2,
        cancel_path=cancel_path, env=env, synthetic=synthetic,
        launch_receipt=str(receipt_path), context=ctx)
    final = (folder / 'run/final.txt').read_bytes()
    parsed = record['result']
    valid = (record['run_id'] == reservation['id'] and record['unit'] == unit and
             record['prompt_sha256'] == request_hash and record['final_sha256'] == hashlib.sha256(final).hexdigest() and
             parsed['terminal_event_valid'] and not parsed['reported_failure'] and
             parsed['malformed_lines'] == 0 and parsed['model_event_valid'] and bool(final.strip()))
    failure = None
    if record['stop_reason'] == 'cancelled':
        failure = 'manual'
    elif not valid or record['exit_code'] != 0 or record['live_process_group_survivors']:
        # Never infer a retryable failure from arbitrary stderr or a guessed code.
        failure = 'missing_evidence'
    result = dict(process_ended=record['exit_code'] is not None and not record['live_process_group_survivors'],
                  terminal_valid=bool(valid), exit_code=record['exit_code'],
                  result_sha256=hashlib.sha256(final).hexdigest(), binding=binding,
                  observed_tokens=observed_usage(record, provider), failure=failure,
                  partial=[str(folder / 'run/run.json'), str(folder / 'run/final.txt')],
                  synthetic=synthetic, native_adapter_success=record['success'],
                  optional_usage_does_not_gate_evidence=True)
    # Classify the actual completed review, never a pre-launch guess. None means
    # verified no findings; an absent classifier is missing mandatory evidence.
    # A classifier exception leaves the attempt uncertain and blocks new work.
    if reservation['purpose'] == 'checker' and failure is None and findings_classifier is not None:
        result['findings_fingerprint'] = findings_classifier(final.decode('utf-8'), record)
    state = controller.settle(reservation['id'], owner, result)
    return dict(reservation=reservation, settlement_state=state, process_record=record, result=result)
