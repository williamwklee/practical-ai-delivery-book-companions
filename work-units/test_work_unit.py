import copy,json,unittest
from pathlib import Path
from work_unit import assess,definition_errors,import_payload
R=Path(__file__).parent
class WorkUnitTests(unittest.TestCase):
    def setUp(self):
        self.unit=json.loads((R/'research-example.json').read_text());self.facts=json.loads((R/'eligible-facts.json').read_text());self.config=json.loads((R/'jira-reference.json').read_text())
    def result(self):return assess(self.unit,self.facts,self.config,'2026-09-27T13:00:01Z')['outcome']
    def test_eligible(self):self.assertEqual(self.result(),'ELIGIBLE_FOR_CLAIM')
    def test_native_dependency(self):
        self.facts=json.loads((R/'dependent-facts.json').read_text());self.assertEqual(self.result(),'DEPENDENCY_OR_DECISION')
    def test_missing_required_input(self):
        self.facts=json.loads((R/'missing-input-facts.json').read_text());self.assertEqual(self.result(),'NEEDS_INPUT')
    def test_rejected_blocker_does_not_make_missing_input_available(self):
        self.facts['blockers']=[{'key':'fixture','status':'Rejected'}];self.facts['inputs_verified']['rooms']=False;self.assertEqual(self.result(),'NEEDS_INPUT')
    def test_missing_definition(self):
        self.unit=json.loads((R/'work-unit-template.json').read_text());self.assertEqual(self.result(),'NEEDS_INPUT')
    def test_missing_acceptance(self):self.unit['acceptance']=[];self.assertEqual(self.result(),'NEEDS_INPUT')
    def test_missing_dependency_declaration(self):del self.unit['dependencies'];self.assertEqual(self.result(),'NEEDS_INPUT')
    def test_declared_dependency_must_be_linked(self):self.unit['dependencies']=[{'key':'SAMPLE-INPUT-01','reason':'Required source'}];self.assertEqual(self.result(),'STOPPED')
    def test_to_do_never_approves_itself(self):self.facts['status']='To Do';self.assertEqual(self.result(),'DEFINITION_ONLY')
    def test_epic_not_claimable(self):self.facts['issue_type']='Epic';self.facts['hierarchy_level']=1;self.assertEqual(self.result(),'CONTAINER')
    def test_current_status_mapping(self):
        expected={'In Progress':'ALREADY_CLAIMED','In Review':'NEEDS_REVIEW','Awaiting Acceptance':'AWAITING_HUMAN','Blocked':'DEPENDENCY_OR_DECISION','Done':'TERMINAL','Rejected':'TERMINAL','Duplicate':'TERMINAL'}
        for status,outcome in expected.items():
            with self.subTest(status=status):self.facts['status']=status;self.assertEqual(self.result(),outcome)
    def test_rework_is_eligible_with_remaining_allowance(self):self.facts['status']='Rework Required';self.facts['next_action']='correct';self.assertEqual(self.result(),'ELIGIBLE_FOR_CLAIM')
    def test_rework_exhausted(self):self.facts['status']='Rework Required';self.facts['usage']['corrections']=2;self.assertEqual(self.result(),'STOPPED')
    def test_fake_approval_not_trusted(self):self.facts['authority']['verified']=False;self.assertEqual(self.result(),'STOPPED')
    def test_existing_owner(self):self.facts['owner']='another-run';self.assertEqual(self.result(),'STOPPED')
    def test_missing_stop_owner_and_resume_fields_fail_closed(self):
        for field in ['manual_stop','unresolved_execution_failure','resume_required','owner']:
            with self.subTest(field=field):
                original=copy.deepcopy(self.facts);del self.facts[field];self.assertEqual(self.result(),'STOPPED');self.facts=original
    def test_unresolved_provider_failure(self):self.facts['unresolved_execution_failure']=True;self.assertEqual(self.result(),'STOPPED')
    def test_dependency_query_incomplete(self):self.facts['blockers_complete']=False;self.assertEqual(self.result(),'STOPPED')
    def test_manual_stop(self):self.facts['manual_stop']=True;self.assertEqual(self.result(),'STOPPED')
    def test_stale_or_future_facts(self):
        original=copy.deepcopy(self.facts)
        for when in ['2026-09-27T12:00:00Z','2026-09-27T14:00:00Z']:
            with self.subTest(observed_at=when):
                self.facts=copy.deepcopy(original);self.facts['observed_at']=when;self.assertEqual(self.result(),'STOPPED')
    def test_unknown_usage(self):del self.facts['usage']['primary_launches'];self.assertEqual(self.result(),'STOPPED')
    def test_pending_reservation(self):self.facts['usage']['reserved_launches']=1;self.assertEqual(self.result(),'STOPPED')
    def test_exhausted_limits(self):
        for field in ['primary_launches','active_seconds','reviews','stalled_cycles']:
            with self.subTest(field=field):
                saved=copy.deepcopy(self.facts);self.facts['usage'][field]=self.unit['limits'][field];self.assertEqual(self.result(),'STOPPED');self.facts=saved
    def test_next_launch_must_fit(self):self.facts['usage']['active_seconds']=7100;self.assertEqual(self.result(),'STOPPED')
    def test_unknown_or_malformed_facts_fail_closed(self):
        for value in [None,{},[],{'manual_stop':False,'observed_at':'nonsense'}]:
            with self.subTest(facts=value):
                self.assertEqual(assess(copy.deepcopy(self.unit),copy.deepcopy(value),self.config,'2026-09-27T13:00:01Z')['outcome'],'STOPPED')
    def test_unknown_status(self):self.facts['status']='Imagined';self.assertEqual(self.result(),'STOPPED')
    def test_boolean_or_infinite_budget_rejected(self):
        original=copy.deepcopy(self.unit)
        for val in [True,float('inf'),-1]:
            with self.subTest(primary_launches=val):
                self.unit=copy.deepcopy(original);self.unit['limits']['primary_launches']=val;self.assertEqual(self.result(),'NEEDS_INPUT')
    def test_resume_requires_authority(self):self.facts['resume_required']=True;self.assertEqual(self.result(),'STOPPED')
    def test_exhausted_retry(self):self.facts['next_action']='retry';self.facts['usage']['transient_retries']=3;self.assertEqual(self.result(),'STOPPED')
    def test_missing_next_action(self):del self.facts['next_action'];self.assertEqual(self.result(),'STOPPED')
    def test_draft_payload_cannot_approve(self):
        p=import_payload(self.unit);self.assertEqual(p['issueType'],'Task');self.assertEqual(p['parent'],'DEV-70');self.assertNotIn('status',p);self.assertNotIn('transitionId',p);self.assertIn('grants no approval',p['description'])
    def test_read_only_and_no_counter_reset(self):
        before=copy.deepcopy(self.facts);r=assess(self.unit,self.facts,self.config,'2026-09-27T13:00:01Z');self.assertEqual(before,self.facts);self.assertEqual(r['service_mutations'],0);self.assertEqual(r['model_launches'],0)
    def test_live_mapping_matches_all_ten_reference_names(self):
        self.assertEqual(len(self.config['status_mapping']),10);self.assertEqual(self.config['status_mapping']['Awaiting Acceptance']['transition_id'],'2');self.assertEqual(self.config['status_mapping']['Done']['transition_id'],'41')

if __name__=='__main__':
    class Recorded(unittest.TextTestResult):
        def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.subcases=[]
        def addSubTest(self,test,subtest,err):
            self.subcases.append({'case':str(subtest),'passed':err is None});super().addSubTest(test,subtest,err)
    result=unittest.TextTestRunner(verbosity=2,resultclass=Recorded).run(unittest.defaultTestLoader.loadTestsFromTestCase(WorkUnitTests))
    (R/'test-results.json').write_text(json.dumps({'kind':'offline synthetic contract tests; no Jira transitions or model calls','tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful(),'subcases':result.subcases},indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
