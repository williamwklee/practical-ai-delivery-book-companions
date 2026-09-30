import copy
import unittest
from jira_snapshot import normalize


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.issue={'key':'DEV-TEST','fields':{'summary':'Example','description':'Do the specified work',
            'status':{'name':'Work Approved'},'issuetype':{'id':'10371','name':'Task'},'parent':{'key':'DEV-70'},
            'issuelinks':[{'type':{'name':'Blocks'},'inwardIssue':{'key':'PRE','fields':{'status':{'name':'Done'}}}}]}}

    def test_native_inward_is_prerequisite(self):
        x=normalize(self.issue,read_at=1000,owner=None);self.assertEqual(x['blockers'],[{'key':'PRE','status':'Done'}])

    def test_definition_changes_invalidate_approval(self):
        original=normalize(self.issue,read_at=1000,owner=None)['definition_sha256']
        for key,val in [('summary','Changed'),('description','Changed'),('parent',{'key':'OTHER'}),('issuelinks',[])]:
            with self.subTest(key=key):
                issue=copy.deepcopy(self.issue);issue['fields'][key]=val
                self.assertNotEqual(normalize(issue,read_at=1000,owner=None)['definition_sha256'],original)

    def test_status_change_does_not_change_definition(self):
        old=normalize(self.issue,read_at=1000,owner=None)['definition_sha256'];self.issue['fields']['status']['name']='In Progress'
        self.assertEqual(normalize(self.issue,read_at=1001,owner='worker')['definition_sha256'],old)

    def test_incomplete_dependency_read_fails_closed(self):
        del self.issue['fields']['issuelinks'][0]['inwardIssue']['fields']
        with self.assertRaisesRegex(ValueError,'blocker_status_missing'):normalize(self.issue,read_at=1000,owner=None)

    def test_missing_required_fields_fail_closed(self):
        for key in ['description','parent','issuelinks','issuetype']:
            with self.subTest(key=key):
                issue=copy.deepcopy(self.issue);del issue['fields'][key]
                with self.assertRaisesRegex(ValueError,'incomplete_jira_read'):normalize(issue,read_at=1000,owner=None)


if __name__=='__main__':unittest.main(verbosity=2)
