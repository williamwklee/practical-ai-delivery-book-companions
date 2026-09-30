"""Read the existing DEV-78 runner's durable output; do not build another runner."""
from pathlib import Path
import json
from pickup import require, sha


def stage_runner_output(queue, claim_token, attempt):
    """Trusted coordinator operation, not a worker-supplied PASS flag.

    This verifies stored bindings and the exact fixture source facts. The
    producer/controller must preserve the actual native CLI process receipts;
    this helper does not authenticate arbitrary untrusted folders.
    """
    root = Path(attempt)
    state = json.loads((root / 'state.json').read_text())
    context = json.loads((root / 'context.json').read_text())
    events = json.loads((root / 'events.json').read_text())
    require(state['disposition'] == 'READY_FOR_PUBLICATION', 'runner_not_ready')
    canonical = lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    require(sha(canonical(context['spec'])) == context['request_sha256'], 'request_changed')
    files = sorted(root.glob('candidate-*.json'), key=lambda f: int(f.stem.split('-')[1]))
    require(bool(files), 'candidate_missing')
    candidate = json.loads(files[-1].read_text())
    payload = canonical(candidate)
    require(sha(payload) == state['candidate_sha256'], 'candidate_changed')
    checks = [e for e in events if e.get('message') == 'Checker and exact-data checks preserved.']
    require(bool(checks), 'review_missing')
    final = checks[-1]
    review = final['review']
    require(review['candidate_sha256'] == sha(payload) and review['verdict'] == 'PASS'
            and review['findings'] == [] and final['deterministic_findings'] == [], 'review_not_pass')
    spec = context['spec']; constraints = spec['constraints']; sources = spec['sources']
    require(set(candidate) == {'choice', 'comparison', 'summary'} and
            isinstance(candidate['summary'], str) and candidate['summary'].strip(), 'candidate_shape')
    require(isinstance(candidate['comparison'], list), 'comparison_missing')
    seen, eligible = set(), []
    for row in candidate['comparison']:
        require(set(row) == {'source', 'seats', 'step_free', 'price_gbp', 'meets_request'}, 'comparison_shape')
        k = row['source']; require(k in sources and k not in seen, 'source_missing_or_duplicate'); seen.add(k)
        source = sources[k]
        require(type(row['seats']) is int and type(row['price_gbp']) is int and
                type(row['step_free']) is bool and type(row['meets_request']) is bool, 'comparison_types')
        require(all(row[x] == source[x] for x in ['seats', 'step_free', 'price_gbp']), 'source_mismatch')
        meets = source['seats'] >= constraints['minimum_seats'] and source['price_gbp'] <= constraints['maximum_price_gbp'] and (not constraints['step_free_required'] or source['step_free'])
        require(row['meets_request'] == meets, 'eligibility_mismatch')
        if meets: eligible.append(source['name'])
    require(seen == set(sources) and candidate['choice'] in eligible, 'ineligible_candidate')
    proof = {'result_sha256': sha(payload), 'request_sha256': context['request_sha256'],
             'review_sha256': sha(canonical(review)), 'disposition': 'READY_FOR_PUBLICATION',
             'deterministic_checks_pass': True}
    queue.stage_result(claim_token, payload, proof)
    return proof
