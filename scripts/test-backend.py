"""Run every backend test in the runtime that owns its dependencies."""
from pathlib import Path
import subprocess,sys,unittest

ROOT=Path(__file__).resolve().parent.parent
BACKEND=ROOT/'backend'
sys.path.insert(0,str(BACKEND))

def cases(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from cases(item)
        else:yield item

def main():
    all_tests=unittest.defaultTestLoader.discover(str(BACKEND))
    application=unittest.TestSuite(t for t in cases(all_tests) if type(t).__module__!='test_financials')
    result=unittest.TextTestRunner(verbosity=2).run(application)
    python=ROOT/'.venv-research/bin/python'
    if not python.exists():
        print('Missing .venv-research; install backend/requirements-research.txt.',file=sys.stderr)
        return 1
    research=subprocess.run([str(python),'-m','unittest','discover','-p','test_financials.py','-v'],cwd=BACKEND)
    return 0 if result.wasSuccessful() and research.returncode==0 else 1

if __name__=='__main__':raise SystemExit(main())
