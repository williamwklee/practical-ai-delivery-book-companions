"""Import definitions, render Jira drafts and evaluate supplied snapshots. No network or writes.

This is a reference contract checker, not a dispatcher, authority authenticator or atomic lock.
The future controller must obtain facts independently from services and the durable ledger.
"""
import argparse, json, math
from datetime import datetime, timezone
from pathlib import Path

def instant(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None: raise ValueError('timezone_required')
    return d

def integer(value, minimum=0):
    return type(value) is int and value >= minimum

def nonempty(value):
    return isinstance(value, str) and bool(value.strip())

def definition_errors(unit):
    if not isinstance(unit, dict): return ['definition must be an object']
    errors = []
    if type(unit.get('schema_version')) is not int or unit.get('schema_version') != 1: errors.append('schema_version must be 1')
    for field in ['unit_id', 'title', 'original_intent', 'outcome', 'scope_boundary', 'stop_rule', 'resume_rule']:
        if not nonempty(unit.get(field)): errors.append('missing ' + field)
    for group, fields in [('roles', ['producer', 'reviewer', 'publisher', 'human_approver']),
                          ('destinations', ['confluence_parent', 'visual_inventory', 'github_repository'])]:
        value = unit.get(group)
        if not isinstance(value, dict) or any(not nonempty(value.get(f)) for f in fields):
            errors.append('incomplete ' + group)
    dependencies = unit.get('dependencies')
    if not isinstance(dependencies, list) or any(not isinstance(d, dict) or not nonempty(d.get('key')) or not nonempty(d.get('reason')) for d in dependencies):
        errors.append('dependencies must be an explicit list of key/reason records, possibly empty')
    for collection, fields in [('inputs', ['id', 'location', 'version', 'check']),
                               ('acceptance', ['id', 'criterion', 'method', 'evidence'])]:
        values = unit.get(collection)
        if not isinstance(values, list) or not values: errors.append('missing ' + collection); continue
        ids = []
        for item in values:
            if not isinstance(item, dict) or any(not nonempty(item.get(k)) for k in fields):
                errors.append('incomplete ' + collection); continue
            ids.append(item['id'])
        if len(set(ids)) != len(ids): errors.append('duplicate ' + collection + ' id')
    limits = unit.get('limits', {})
    if not isinstance(limits, dict): return errors + ['limits must be an object']
    ceilings = {'primary_launches': 10, 'active_seconds': 7200, 'reviews': 3,
                'corrections': 2, 'transient_retries': 3, 'stalled_cycles': 2}
    for k, ceiling in ceilings.items():
        if not integer(limits.get(k), 1 if k in ['primary_launches', 'active_seconds', 'reviews', 'stalled_cycles'] else 0) or limits[k] > ceiling:
            errors.append('invalid finite limit ' + k)
    return errors

def assess(unit, facts, config, now):
    """Return advice only; trusted facts are supplied separately from editable ticket text."""
    def result(outcome, reasons): return {'outcome': outcome, 'reasons': reasons, 'service_mutations': 0, 'model_launches': 0}
    if not isinstance(facts, dict): return result('STOPPED', ['facts unavailable'])
    if facts.get('manual_stop') is True: return result('STOPPED', ['manual stop; preserve ledger and ownership'])
    if facts.get('manual_stop') is not False or facts.get('unresolved_execution_failure') is not False or type(facts.get('resume_required')) is not bool:
        return result('STOPPED', ['stop/failure/resume state unknown or unresolved; read the existing run record'])
    errors = definition_errors(unit)
    if errors: return result('NEEDS_INPUT', errors)
    try:
        age = (instant(now) - instant(facts['observed_at'])).total_seconds()
        if not 0 <= age <= 300: return result('STOPPED', ['facts stale or from the future; refresh before deciding'])
        if facts.get('issue_type') == 'Epic' or facts.get('hierarchy_level', 1) > 0:
            return result('CONTAINER', ['never claim an Epic or container'])
        if facts.get('issue_type') not in ['Task', 'Story', 'Feature', 'Bug', 'Subtask']:
            return result('STOPPED', ['unknown work type'])
        status = facts['status']
        outcomes = {'To Do':'DEFINITION_ONLY', 'In Progress':'ALREADY_CLAIMED', 'In Review':'NEEDS_REVIEW',
                    'Awaiting Acceptance':'AWAITING_HUMAN', 'Blocked':'DEPENDENCY_OR_DECISION',
                    'Done':'TERMINAL', 'Rejected':'TERMINAL', 'Duplicate':'TERMINAL'}
        if status in outcomes: return result(outcomes[status], ['current status is ' + status])
        if status not in config['claimable_statuses']: return result('STOPPED', ['unknown status'])
        authority = facts['authority']
        if authority.get('verified') is not True or not nonempty(authority.get('record')) or not nonempty(authority.get('human')):
            return result('STOPPED', ['approval provenance not verified independently'])
        if facts.get('ownership_checked') is not True or 'owner' not in facts or facts['owner'] is not None:
            return result('STOPPED', ['ownership unknown or already held'])
        if facts.get('blockers_complete') is not True: return result('STOPPED', ['dependency query incomplete'])
        blockers = facts['blockers']
        if not isinstance(blockers, list): return result('STOPPED', ['invalid blocker data'])
        linked_keys = {b.get('key') for b in blockers if isinstance(b, dict)}
        absent_links = [d['key'] for d in unit['dependencies'] if d['key'] not in linked_keys]
        if absent_links: return result('STOPPED', ['declared dependency lacks a verified native link: ' + ', '.join(absent_links)])
        unfinished = [b.get('key', 'unknown') for b in blockers if not isinstance(b, dict) or b.get('status') not in config['terminal_dependency_statuses']]
        if unfinished: return result('DEPENDENCY_OR_DECISION', ['unfinished native blockers: ' + ', '.join(unfinished)])
        available = facts['inputs_verified']
        if not isinstance(available, dict): return result('NEEDS_INPUT', ['input evidence unavailable'])
        missing = [i['id'] for i in unit['inputs'] if available.get(i['id']) is not True]
        if missing: return result('NEEDS_INPUT', ['required input not verified: ' + ', '.join(missing)])
        usage = facts['usage']; limits = unit['limits']
        for k in ['primary_launches', 'active_seconds', 'reviews', 'corrections', 'transient_retries', 'stalled_cycles']:
            if not integer(usage.get(k)): return result('STOPPED', ['missing or invalid durable usage: ' + k])
            if usage[k] > limits[k]: return result('STOPPED', ['limit already exceeded: ' + k])
        if not integer(usage.get('reserved_launches')) or not integer(usage.get('reserved_seconds')):
            return result('STOPPED', ['pending usage unknown'])
        if usage['reserved_launches'] or usage['reserved_seconds']:
            return result('STOPPED', ['unsettled reservation; reconcile without resetting'])
        for k in ['primary_launches', 'active_seconds', 'reviews', 'stalled_cycles']:
            if usage[k] >= limits[k]: return result('STOPPED', ['finite limit exhausted: ' + k])
        if status == 'Rework Required' and usage['corrections'] >= limits['corrections']:
            return result('STOPPED', ['correction allowance exhausted'])
        action = facts.get('next_action')
        allowed = {'Work Approved': ['produce', 'retry'], 'Rework Required': ['correct', 'retry']}
        if action not in allowed[status]:
            return result('STOPPED', ['next action is missing or inconsistent with current status'])
        if action == 'retry' and usage['transient_retries'] >= limits['transient_retries']:
            return result('STOPPED', ['transient retry allowance exhausted'])
        if not integer(facts.get('next_launch_seconds'), 1) or facts['next_launch_seconds'] > limits['active_seconds'] - usage['active_seconds']:
            return result('STOPPED', ['next launch does not fit remaining active time'])
        if facts.get('resume_required') is True and facts.get('resume_authority_verified') is not True:
            return result('STOPPED', ['substantive/manual stop needs recorded resume authority'])
        if not nonempty(facts.get('ledger_id')): return result('STOPPED', ['stable ledger identifier missing'])
        return result('ELIGIBLE_FOR_CLAIM', ['definition, current approval, ownership, dependencies, inputs and remaining limits checked; claim is still a separate operation'])
    except (KeyError, TypeError, ValueError, AttributeError):
        return result('STOPPED', ['malformed or missing required facts; obtain complete evidence'])

def render(unit):
    errors = definition_errors(unit)
    if errors: raise ValueError('; '.join(errors))
    sections = [('# ' + unit['title']), ('\nUnit: ' + unit['unit_id']),
                '\nDRAFT DEFINITION. This text grants no approval, claim or acceptance.']
    for k in ['original_intent','outcome','scope_boundary','inputs','dependencies','acceptance','roles','limits','destinations','stop_rule','resume_rule']:
        value = unit[k]
        sections += ['\n## ' + k.replace('_',' ').title(), value if isinstance(value,str) else '```json\n'+json.dumps(value,indent=2)+'\n```']
    sections += ['\n## Execution record', 'Record approval source, native Blocks links, fresh status/owner, run/attempt/candidate version, persistent ledger use/reservations, full independent report/corrections, Confluence page/attachment versions, inventory IDs and immutable companion commit. Do not copy fixture authority into a real ticket.']
    return '\n'.join(sections) + '\n'

def import_payload(unit):
    return {'cloudId':'176e5060-a7a7-427d-8ff4-5f133825dccf','projectKey':'DEV','issueType':'Task',
            'parent':'DEV-70','summary':unit['title'],'description':render(unit),'contentFormat':'markdown',
            'labels':['book-work-unit-draft']}

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['check','render','import-payload'])
    parser.add_argument('definition');parser.add_argument('--facts');parser.add_argument('--now')
    args=parser.parse_args();unit=json.loads(Path(args.definition).read_text())
    if args.command=='render':print(render(unit),end='')
    elif args.command=='import-payload':print(json.dumps(import_payload(unit),indent=2))
    else:
        if not args.facts:parser.error('--facts is required; authority is not inferred from ticket text')
        config=json.loads((Path(__file__).parent/'jira-reference.json').read_text())
        print(json.dumps(assess(unit,json.loads(Path(args.facts).read_text()),config,args.now or datetime.now(timezone.utc).isoformat()),indent=2))
