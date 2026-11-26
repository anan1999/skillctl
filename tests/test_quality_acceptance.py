import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
import skill_quality as q
from skillctl import fingerprint
from skill_dashboard import Dashboard,make_server
import threading,urllib.request,urllib.error

class QualityAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.suite=json.loads(Path('quality/pilot.json').read_text(encoding='utf-8'))
        self.dirs={}
        for name in {c['skill'] for c in self.suite['cases']}:
            p=self.root/'skills'/name;p.mkdir(parents=True)
            (p/'SKILL.md').write_text('---\nname: '+name+'\ndescription: Synthetic evaluation fixture\n---\n# Workflow\nRead only.\n',encoding='utf-8')
            (p/'reference.txt').write_text('reference v1',encoding='utf-8');self.dirs[name]=p
        self.plan=q.prepare(self.suite,self.dirs,'fixture-model')
        self.responses=[dict(id=c['id'],model='fixture-model',status='completed',answer=json.dumps({x['path']:x['value'] for x in c['checks']}),elapsed_seconds=0.1) for c in self.suite['cases']]
    def tearDown(self):self.tmp.cleanup()
    def assess(self,r,n='failure-triage'):return q.assess(r,n,fingerprint(self.dirs[n]))
    def test_perfect_baseline_and_private_path_exclusion(self):
        r=q.grade(self.plan,self.responses)
        for n in self.dirs:
            a=self.assess(r,n);self.assertEqual(a['status'],'passed');self.assertEqual((a['passed'],a['total']),(4,4))
        self.assertNotIn(str(self.root),json.dumps(r));self.assertEqual(r['mode'],'text-simulation')
    def test_expected_answers_not_in_model_task(self):
        for job in self.plan['jobs']:
            self.assertNotIn('"checks"',job['context']);self.assertIn('Synthetic evaluation fixture',job['context'])
        self.assertEqual(len(self.plan['jobs']),12)
    def test_wrong_and_boolean_type_mismatch_fail(self):
        for value in (99,True):
            bad=copy.deepcopy(self.responses);a=json.loads(bad[0]['answer']);a['first_error_line']=value;bad[0]['answer']=json.dumps(a)
            self.assertEqual(self.assess(q.grade(self.plan,bad))['status'],'failed')
    def test_missing_duplicate_wrong_model_truncated_are_not_pass(self):
        for variant in ('missing','duplicate','model','truncated','malformed','empty'):
            bad=copy.deepcopy(self.responses)
            if variant=='missing':bad=bad[1:]
            elif variant=='duplicate':bad.append(bad[0])
            elif variant=='model':bad[0]['model']='other'
            elif variant=='truncated':bad[0]['status']='truncated'
            elif variant=='malformed':bad[0]['answer']='not json'
            elif variant=='empty':bad=[]
            self.assertNotEqual(self.assess(q.grade(self.plan,bad))['status'],'passed',variant)
    def test_changed_reference_is_stale(self):
        r=q.grade(self.plan,self.responses);(self.dirs['failure-triage']/'reference.txt').write_text('changed')
        self.assertEqual(self.assess(r)['status'],'stale')
        self.assertEqual(q.assess(r,'not-tested',{} )['status'],'untested')
        self.assertNotEqual(q.assess(r,'failure-triage',None)['status'],'passed')
    def test_report_forgery_and_empty_suite_fail_closed(self):
        r=q.grade(self.plan,self.responses);r['skills']['failure-triage']['cases']=[]
        self.assertNotEqual(self.assess(r)['status'],'passed')
        with self.assertRaises(ValueError):q.prepare({**self.suite,'cases':[]},self.dirs,'fixture-model')
        bad=copy.deepcopy(self.suite);bad['cases'].append(bad['cases'][0])
        with self.assertRaises(ValueError):q.prepare(bad,self.dirs,'fixture-model')
    def test_http_quality_is_readonly_authenticated_and_bound(self):
        report=self.root/'report.json';report.write_text(json.dumps(q.grade(self.plan,self.responses)),encoding='utf-8')
        app=Dashboard(self.root/'skills',[('fixture',self.root/'skills')],self.root/'state',quality_report=report)
        server=make_server(app);threading.Thread(target=server.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(base+'/api/quality?target=0&name=failure-triage')
            self.assertEqual(e.exception.code,403)
            req=urllib.request.Request(base+'/api/quality?target=0&name=failure-triage',headers={'X-Skillctl-Token':app.secret})
            with urllib.request.urlopen(req) as r:self.assertEqual(json.load(r)['status'],'passed')
            report.write_text('{bad',encoding='utf-8')
            with urllib.request.urlopen(req) as r:self.assertEqual(json.load(r)['status'],'error')
        finally:server.shutdown();server.server_close()

if __name__ == '__main__': unittest.main(verbosity=2)
