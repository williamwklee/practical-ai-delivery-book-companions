"""Normalize a complete trusted Jira MCP read, without credentials or HTTP code."""
import json
from pickup import require, sha


def normalize(issue, *, read_at, owner):
    """Request summary, description, status, issuetype, parent and issuelinks.

    owner comes from the coordinator's verified claim receipt, not assignee.
    This helper does not perform a read or prove the caller's identity.
    """
    require(isinstance(issue, dict) and isinstance(issue.get('key'), str), 'jira_key_missing')
    f=issue.get('fields');require(isinstance(f, dict), 'jira_fields_missing')
    require(all(k in f for k in ['summary','description','status','issuetype','parent','issuelinks']), 'incomplete_jira_read')
    require(isinstance(f['summary'], str) and isinstance(f['description'], str) and
            isinstance(f['issuelinks'], list), 'jira_field_types')
    kind=f['issuetype'];require(isinstance(kind, dict) and isinstance(kind.get('id'), str) and isinstance(kind.get('name'), str), 'issue_type_missing')
    status=f['status'];require(isinstance(status, dict) and isinstance(status.get('name'), str), 'status_missing')
    parent=f['parent'];require(parent is None or isinstance(parent, dict) and isinstance(parent.get('key'), str), 'parent_missing')
    blockers=[]
    for link in f['issuelinks']:
        require(isinstance(link, dict) and isinstance(link.get('type'), dict) and isinstance(link['type'].get('name'), str), 'link_type_missing')
        if link['type']['name']!='Blocks':continue
        require(('inwardIssue' in link) != ('outwardIssue' in link), 'ambiguous_link')
        if 'inwardIssue' not in link:continue
        b=link['inwardIssue'];require(isinstance(b, dict) and isinstance(b.get('key'), str), 'blocker_missing')
        bs=b.get('fields',{}).get('status',{}).get('name');require(isinstance(bs, str), 'blocker_status_missing')
        blockers.append({'key':b['key'],'status':bs})
    require(len({b['key'] for b in blockers})==len(blockers), 'duplicate_blocker')
    definition={'key':issue['key'],'summary':f['summary'],'description':f['description'],
                'type':{'id':kind['id'],'name':kind['name']},'parent':parent['key'] if parent else None,
                'blocker_keys':sorted(b['key'] for b in blockers)}
    fingerprint=sha(json.dumps(definition,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
    return {'key':issue['key'],'type':kind['name'],'status':status['name'],'owner':owner,
            'definition_sha256':fingerprint,'read_at':read_at,'blockers':sorted(blockers,key=lambda b:b['key'])}
