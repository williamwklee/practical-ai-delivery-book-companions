"""Bounded, single-process CLI adapter for the macOS book reference.

No dispatcher, implicit retry, resume, credential provisioning or spending approval.
Uses ordinary consumer CLI sign-ins. The wrapper bounds local processes,
not hidden provider requests, subscription quota or a hard token/dollar amount.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone


def digest(data):
    return hashlib.sha256(data).hexdigest()


def group_members(pgid):
    """Return this session's live group members; zombies cannot execute."""
    p = subprocess.run(['ps', '-axo', 'pid=,pgid=,stat='],
                       capture_output=True, text=True, timeout=3)
    if p.returncode:
        raise RuntimeError('process_group_inspection_failed')
    rows = []
    for line in p.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and int(fields[1]) == pgid and not fields[2].startswith('Z'):
            rows.append(int(fields[0]))
    return rows


def stop_group(proc, grace):
    signals = []
    for sig, wait in [(signal.SIGTERM, grace), (signal.SIGKILL, 2.0)]:
        try:
            os.killpg(proc.pid, sig)
            signals.append(signal.Signals(sig).name)
        except ProcessLookupError:
            break
        until = time.monotonic() + wait
        while time.monotonic() < until:
            proc.poll()  # reap the direct child before checking group existence
            if not group_members(proc.pid):
                return signals, []
            time.sleep(.03)
    proc.poll()
    return signals, group_members(proc.pid)


def parse_events(raw, provider, expected_model, synthetic):
    events, invalid, messages, models = [], 0, [], set()
    for line in raw.splitlines():
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('not_object')
            events.append(event)
        except (ValueError, UnicodeDecodeError):
            invalid += 1
    terminal = []
    usage = None
    failed = False
    for e in events:
        if provider == 'codex':
            item = e.get('item', {})
            if not isinstance(item, dict):
                invalid += 1
                continue
            if e.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                if isinstance(item.get('text'), str):
                    messages.append(item['text'])
                else:
                    invalid += 1
            if e.get('type') == 'turn.completed':
                terminal.append(e)
                usage = e.get('usage')
            if e.get('type') in ['turn.failed', 'error']:
                failed = True
        elif provider == 'claude':
            if e.get('type') == 'assistant':
                msg = e.get('message', {})
                if (not isinstance(msg, dict) or not isinstance(msg.get('content', []), list)
                        or not isinstance(msg.get('model', ''), str)):
                    invalid += 1
                    continue
                if msg.get('model'):
                    models.add(msg['model'])
                for block in msg.get('content', []):
                    if not isinstance(block, dict):
                        invalid += 1
                    elif block.get('type') == 'text':
                        if isinstance(block.get('text'), str):
                            messages.append(block['text'])
                        else:
                            invalid += 1
            if e.get('type') == 'result':
                terminal.append(e)
                usage = e.get('modelUsage')
                failed |= e.get('subtype') != 'success' or e.get('is_error') is not False
        else:
            raise ValueError('unsupported_provider')
    # A terminal result must actually be last, not followed by a partial second turn.
    complete = len(terminal) == 1 and bool(events) and events[-1] is terminal[0]
    if provider == 'claude':
        usage_ok = isinstance(usage, dict) and bool(usage) and all(
            isinstance(v, dict) and all(isinstance(v.get(k), int) and
                                       not isinstance(v[k], bool) and v[k] >= 0
                                       for k in ['inputTokens', 'outputTokens',
                                                 'cacheReadInputTokens', 'cacheCreationInputTokens'])
            for v in usage.values())
        model_ok = models == {expected_model}
    else:
        usage_ok = isinstance(usage, dict) and all(
            isinstance(usage.get(k), int) and not isinstance(usage[k], bool) and usage[k] >= 0
            for k in ['input_tokens', 'cached_input_tokens', 'output_tokens'])
        usage_ok = usage_ok and usage['cached_input_tokens'] <= usage['input_tokens']
        # Codex JSONL lacks server model identity. Record the requested model and this limitation.
        model_ok = True
    return {'event_count': len(events), 'malformed_lines': invalid,
            'terminal_event_valid': complete, 'reported_failure': failed,
            'usage_valid': bool(usage_ok), 'usage': usage if usage_ok else None,
            'reported_models': sorted(models), 'requested_model': expected_model,
            'model_event_valid': model_ok, 'final_text': '\n'.join(messages),
            'synthetic': synthetic}


def run(argv, prompt_path, run_dir, provider, model, role, unit, *,
        timeout=30.0, first_output_timeout=10.0, idle_timeout=15.0,
        max_output_bytes=2_000_000, grace=.5, cancel_path=None, env=None,
        synthetic=False, launch_receipt=None, context=None):
    context = dict(context or {})
    run_id = context.get('run_id', 'run-' + uuid.uuid4().hex)
    attempt_id = context.get('attempt_id', 'attempt-' + uuid.uuid4().hex)
    if not (0 < timeout <= 600 and 0 < first_output_timeout <= timeout and
            0 < idle_timeout <= timeout and 0 < max_output_bytes <= 8_000_000 and 0 < grace <= 5):
        raise ValueError('invalid_finite_limits')
    if not argv or provider not in ['codex', 'claude']:
        raise ValueError('invalid_launch')
    if not synthetic and not launch_receipt:
        raise ValueError('durable_launch_reservation_required')
    required = ['run_id','attempt_id','candidate_version','candidate_sha256',
                'authenticated_principal','cli_version','effort','profile_sha256']
    if not synthetic and any(not isinstance(context.get(k), str) or not context[k] for k in required):
        raise ValueError('live_run_identity_required')
    if cancel_path and Path(cancel_path).exists():
        raise ValueError('cancelled_before_launch')
    if not synthetic:
        admission=json.loads(Path(launch_receipt).read_text())
        if not (admission.get('reserved') is True and admission.get('mode')=='consumer_subscription'
                and admission.get('unit')==unit and admission.get('run_id')==run_id
                and admission.get('argv_sha256')==digest(json.dumps(argv).encode())
                and admission.get('prompt_sha256')==digest(Path(prompt_path).read_bytes())
                and admission.get('expires_unix',0)>time.time()):
            raise ValueError('launch_reservation_mismatch')
        # A reservation is consumed once even if a caller chooses another output
        # directory. A crash keeps it consumed; the caller must reconcile it.
        fd = os.open(str(launch_receipt) + '.consumed',
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        timeout = min(timeout, admission['expires_unix'] - time.time())
        if timeout <= 0:
            raise ValueError('launch_reservation_expired')
        first_output_timeout = min(first_output_timeout, timeout)
        idle_timeout = min(idle_timeout, timeout)
    root = Path(run_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)  # no silent resume/reset
    private = root / 'private'
    private.mkdir(mode=0o700)
    prompt = Path(prompt_path).read_bytes()
    if len(prompt) > 250_000:
        raise ValueError('prompt_too_large')
    started_utc = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    stream_data = {'stdout': bytearray(), 'stderr': bytearray()}
    raw_bytes = {'stdout': 0, 'stderr': 0}
    stop_reason, signals, survivors = None, [], []
    last_output, first_seen = start, False
    # Regular-file stdin guarantees EOF and avoids an unbounded pipe write.
    proc = None
    process_error = None
    try:
        with Path(prompt_path).open('rb') as stdin:
            proc = subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env, cwd=str(root),
                                    start_new_session=True)
            selector = selectors.DefaultSelector()
            for key, pipe in [('stdout', proc.stdout), ('stderr', proc.stderr)]:
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, key)
            try:
                while True:
                    now = time.monotonic()
                    if cancel_path and Path(cancel_path).exists():
                        stop_reason = 'cancelled'
                    elif now - start >= timeout:
                        stop_reason = 'elapsed_limit'
                    elif not first_seen and now - start >= first_output_timeout:
                        stop_reason = 'no_output_limit'
                    elif first_seen and now - last_output >= idle_timeout:
                        stop_reason = 'idle_output_limit'
                    if stop_reason:
                        break
                    for key, _ in selector.select(.05):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        raw_bytes[key.data] += len(chunk)
                        left = max(0, max_output_bytes - sum(len(v) for v in stream_data.values()))
                        stream_data[key.data].extend(chunk[:left])
                        if key.data == 'stdout':
                            first_seen, last_output = True, time.monotonic()
                        if sum(raw_bytes.values()) > max_output_bytes:
                            stop_reason = 'output_bytes_limit'
                            break
                    if stop_reason or (proc.poll() is not None and not selector.get_map()):
                        break
            finally:
                selector.close()
                # Includes children holding pipes after their direct parent exits.
                signals, survivors = stop_group(proc, grace)
                proc.wait(timeout=3)
                proc.stdout.close()
                proc.stderr.close()
    except Exception as error:
        # Preserve partial observations even when process creation/cleanup fails.
        process_error = type(error).__name__
        stop_reason = stop_reason or 'process_control_error'
        if proc is not None:
            try:
                extra, survivors = stop_group(proc, grace)
                signals.extend(extra)
            except Exception:
                survivors = ['inspection_failed']
    for key, value in stream_data.items():
        file = private / (key + '.bin')
        file.write_bytes(value)
        file.chmod(0o600)
    parsed = parse_events(bytes(stream_data['stdout']), provider, model, synthetic)
    success = (stop_reason is None and proc is not None and proc.returncode == 0 and not survivors and
               parsed['terminal_event_valid'] and not parsed['reported_failure'] and
               parsed['malformed_lines'] == 0 and parsed['usage_valid'] and
               parsed['model_event_valid'] and bool(parsed['final_text'].strip()))
    final = parsed.pop('final_text')
    # Raw stderr, headers, argv/env and thinking are never published in run.json.
    record = {'schema_version': 3, 'unit': unit, 'run_id': run_id, 'attempt_id': attempt_id,
              'candidate_version': context.get('candidate_version','C1 consumer'),
              'candidate_sha256': context.get('candidate_sha256',digest(Path(__file__).read_bytes())),
              'candidate_hash_scope':context.get('candidate_hash_scope','adapter.py bytes'),
              'authenticated_principal': context.get('authenticated_principal','local fixture; no provider authentication'),
              'cli_version': context.get('cli_version','not applicable: synthetic worker'),
              'effort': context.get('effort','not applicable: synthetic worker'),
              'profile_sha256': context.get('profile_sha256'),
              'role': role, 'executable': Path(argv[0]).name,
              'argv_sha256': digest(json.dumps(argv).encode()),
              'prompt_sha256': digest(prompt), 'started_utc': started_utc,
              'ended_utc': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': round(time.monotonic() - start, 3),
              'limits': {'elapsed_seconds': timeout, 'first_output_seconds': first_output_timeout,
                         'idle_output_seconds': idle_timeout, 'output_bytes': max_output_bytes,
                         'termination_grace_seconds': grace, 'automatic_retries': 0},
              'exit_code': proc.returncode if proc is not None else None, 'stop_reason': stop_reason,
              'process_error_type': process_error,
              'termination_signals': signals, 'live_process_group_survivors': survivors,
              'captured_bytes': {k: len(v) for k, v in stream_data.items()},
              'observed_bytes': raw_bytes,
              'stderr_sha256': digest(bytes(stream_data['stderr'])),
              'diagnostic': stop_reason or ('completed' if success else 'incomplete_or_failed_result'),
              'result': parsed, 'server_model_evidence': ('reported in Claude assistant event' if provider=='claude' else 'not emitted by Codex JSONL; requested model recorded'),
              'subscription_quota_remaining':None,'hard_token_or_dollar_cap_claimed':False,
              'qualification_mode': 'synthetic fixture' if synthetic else 'live consumer CLI with subscription sign-in',
              'success': success, 'final_sha256': digest(final.encode())}
    (root / 'final.txt').write_text(final)
    (root / 'run.json').write_text(json.dumps(record, indent=2))
    return record


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('spec', help='JSON launch manifest. Never store secrets in it.')
    a = p.parse_args()
    result = run(**json.loads(Path(a.spec).read_text()))
    print(json.dumps({k: result[k] for k in ['run_id', 'success', 'exit_code', 'stop_reason']}))
    raise SystemExit(0 if result['success'] else 1)
