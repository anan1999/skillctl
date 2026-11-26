"""Run only the installed log excerpt helper against synthetic files."""
import argparse,hashlib,json,subprocess,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--skill-dir',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
helper=args.skill_dir/'scripts/first_error.py'
before=hashlib.sha256(helper.read_bytes()).hexdigest()
results=[]
with tempfile.TemporaryDirectory(prefix='skillctl-quality-helper-') as folder:
    p=Path(folder)/'sample.log'
    cases=[('first-error','INFO boot\nWARN cache miss\nERROR bad config\nFATAL shutdown\n','3: ERROR bad config',4),('large-log','INFO normal\n'*100000+'ERROR connection refused\n'+'INFO normal\n'*100,'100001: ERROR connection refused',11),('no-error','INFO startup\nINFO stopped\n','No ERROR, FATAL, Traceback, or Exception marker found.',1)]
    for name,log,expected,max_lines in cases:
        p.write_text(log,encoding='utf-8')
        r=subprocess.run([sys.executable,'-B',str(helper),str(p)],capture_output=True,text=True,encoding='utf-8',timeout=20)
        passed=r.returncode==0 and expected in r.stdout and len(r.stdout.splitlines())<=max_lines
        results.append(dict(id=name,passed=passed,expected=expected,actual_excerpt=r.stdout,returncode=r.returncode))
assert before==hashlib.sha256(helper.read_bytes()).hexdigest()
record=dict(mode='local-helper-synthetic-files',skill='failure-triage',helper_sha256=before,created_at=datetime.now(timezone.utc).isoformat(),passed=all(r['passed'] for r in results),cases=results)
args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(record,ensure_ascii=False))
sys.exit(0 if record['passed'] else 1)
