"""Start the intentionally local application, and leave it available for the user."""
from pathlib import Path
import json, os, shutil, subprocess, time, urllib.request, webbrowser

ROOT=Path(__file__).resolve().parent.parent
PYTHON=Path(os.environ.get('NORTHSTAR_PYTHON') or next(
    (str(path) for path in (ROOT/'.venv/bin/python', Path.home()/'Documents/GitHub/qlib/.venv/bin/python') if path.exists()),
    str(ROOT/'.venv/bin/python')))

def ready(url):
    try:
        with urllib.request.urlopen(url,timeout=2) as r:return r.status==200
    except Exception:return False

def app_ready(url):
    try:
        with urllib.request.urlopen(url,timeout=2) as r:return json.load(r).get('service')=='northstar-local'
    except Exception:return False

def start():
    data=ROOT/'backend/data';data.mkdir(exist_ok=True)
    npm=shutil.which('npm')
    if not npm:
        for directory in ['/usr/local/bin','/opt/homebrew/bin','/usr/bin']:
            if (Path(directory)/'npm').exists():
                os.environ['PATH']=directory+os.pathsep+os.environ.get('PATH','');npm=str(Path(directory)/'npm');break
    if not PYTHON.exists() or not npm:
        raise SystemExit('缺少本项目需要的 Python 或 Node 环境，请在 Codex 中检查。')
    if not app_ready('http://127.0.0.1:18765/api/health'):
        with (data/'server.log').open('ab') as log:
            subprocess.Popen([str(PYTHON),'backend/server.py'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    if not app_ready('http://127.0.0.1:5173/api/health'):
        with (data/'frontend.log').open('ab') as log:
            subprocess.Popen([npm,'run','dev','--','--host','127.0.0.1'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    for _ in range(45):
        if app_ready('http://127.0.0.1:18765/api/health') and app_ready('http://127.0.0.1:5173/api/health') and ready('http://127.0.0.1:5173'):
            webbrowser.open('http://127.0.0.1:5173');print('北极星已打开：http://127.0.0.1:5173');return
        time.sleep(1)
    raise SystemExit('启动尚未完成，请查看 backend/data/frontend.log 和 server.log。')

if __name__=='__main__':start()
