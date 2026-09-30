import concurrent.futures,json,tempfile,unittest
from pathlib import Path
from control import RunLedger

class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'ledger.sqlite'
        self.ledger=RunLedger.create(self.path,'DEV-74',2,60)
    def tearDown(self):self.temp.cleanup()
    def test_only_one_pending_attempt_is_admitted(self):
        def reserve(_):
            try:return self.ledger.reserve('DEV-74','fixture')[0]
            except ValueError:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(reserve,[1,2]))
        self.assertEqual(sum(v is not None for v in result),1)
    def test_two_runs_exhaust_even_after_reopen(self):
        for _ in range(2):
            rid,_=self.ledger.reserve('DEV-74','fixture');self.ledger.settle(rid,{'success':True})
        with self.assertRaisesRegex(ValueError,'exhausted'):RunLedger(self.path).reserve('DEV-74','fixture')
        self.assertEqual(len(self.ledger.snapshot()['runs']),2)
    def test_failure_stops_next_launch_and_survives_reopen(self):
        rid,_=self.ledger.reserve('DEV-74','fixture');self.ledger.settle(rid,{'success':False,'reason':'quota_or_auth_failure'})
        with self.assertRaisesRegex(ValueError,'stopped'):RunLedger(self.path).reserve('DEV-74','fixture')
    def test_crash_keeps_pending_reservation(self):
        self.ledger.reserve('DEV-74','fixture')
        with self.assertRaisesRegex(ValueError,'not_completed'):RunLedger(self.path).reserve('DEV-74','fixture')
    def test_creation_cannot_reset_existing_campaign(self):
        with self.assertRaises(FileExistsError):RunLedger.create(self.path,'DEV-74',2,60)
    def test_wrong_unit_cannot_borrow_other_allowance(self):
        with self.assertRaisesRegex(ValueError,'wrong_unit'):self.ledger.reserve('DEV-75','fixture')
    def test_duplicate_settlement_rejected(self):
        rid,_=self.ledger.reserve('DEV-74','fixture');self.ledger.settle(rid,{'success':True})
        with self.assertRaisesRegex(ValueError,'not_pending'):self.ledger.settle(rid,{'success':True})

if __name__=='__main__':
    class Recorded(unittest.TextTestResult):
        def startTest(self,test):
            self.names=getattr(self,'names',[])+[test.id()];super().startTest(test)
    result=unittest.TextTestRunner(verbosity=2,resultclass=Recorded).run(unittest.defaultTestLoader.loadTestsFromTestCase(ControlTests))
    (Path(__file__).parent/'control-tests.json').write_text(json.dumps({'tests':result.names,'count':result.testsRun,
        'success':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors),'model_runs':0},indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
