"""Publication checks use disposable repositories and synthetic identifiers."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/check-privacy.py'


class PrivacyGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
        self.git('init','-q')
        self.git('config','user.email','tester@example.invalid')
        self.git('config','user.name','Test')

    def tearDown(self): self.temp.cleanup()
    def git(self,*args): return subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True)
    def write(self,name,text):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    def check(self,*args):
        return subprocess.run([sys.executable,str(SCRIPT),'--root',str(self.root),*args],capture_output=True,text=True)

    def test_ignored_local_data_stays_local_but_forced_staging_is_blocked(self):
        self.write('.gitignore','backend/data/\n');self.write('backend/data/holdings.json','{}')
        self.write('app.py','cash = 10000\npositions = []\n')
        self.assertEqual(self.check().returncode,0)
        self.git('add','-f','backend/data/holdings.json')
        self.assertEqual(self.check('--staged').returncode,1)

    def test_private_identifiers_in_renamed_source_are_blocked_without_printing_them(self):
        email='private-person'+'@mail.invalid';account='U'+'987654321'
        self.write('.gitignore','backend/data/\n')
        self.write('backend/data/research-settings.json','{"secEmail":"'+email+'"}')
        self.write('notes.txt',email+' '+account)
        result=self.check()
        self.assertEqual(result.returncode,1)
        self.assertNotIn(email,result.stdout+result.stderr)
        self.assertNotIn(account,result.stdout+result.stderr)

    def test_deleted_secret_is_still_detected_in_history(self):
        self.write('notes.txt','account '+'DU'+'987654321')
        self.git('add','.');self.git('commit','-qm','fixture')
        self.git('rm','notes.txt');self.git('commit','-qm','remove fixture')
        self.assertEqual(self.check('--history').returncode,1)

    def test_private_json_renamed_and_screenshot_are_blocked(self):
        self.write('dump.json','{"cash":100,"positions":[],"updatedAt":"2026-01-01"}')
        self.assertEqual(self.check().returncode,1)
        (self.root/'dump.json').unlink()
        self.write('preview-account.png','not a real image')
        self.assertEqual(self.check().returncode,1)

    def test_normal_code_and_examples_are_allowed(self):
        self.write('test_case.py','email = "tester@example.com"\naccount = {"cash":10000,"positions":[]}\n')
        self.write('README.md','Set NORTHSTAR_PYTHON to your own Python interpreter.\n')
        self.git('add','.')
        self.assertEqual(self.check('--staged').returncode,0)

    def test_export_omits_ignored_accounts_and_old_git_history(self):
        from zipfile import ZipFile
        self.write('old.txt','account '+'U'+'987654321')
        self.git('add','.');self.git('commit','-qm','old fixture')
        self.git('rm','old.txt');self.git('commit','-qm','remove fixture')
        self.write('.gitignore','backend/data/\noutputs/\n')
        self.write('backend/data/manual-account.json','{"cash":100,"positions":[]}')
        self.write('app.py','cash = None\n')
        export=SCRIPT.with_name('export-github-source.py')
        result=subprocess.run([sys.executable,str(export),'--root',str(self.root)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        with ZipFile(self.root/'outputs/northstar-github-source.zip') as archive:
            self.assertEqual(set(archive.namelist()),{'stock-signal-lab/.gitignore','stock-signal-lab/app.py'})

    def test_symlink_cannot_smuggle_ignored_file_into_export(self):
        self.write('.gitignore','backend/data/\n');self.write('backend/data/hidden.txt','private')
        (self.root/'public.txt').symlink_to('backend/data/hidden.txt')
        self.assertEqual(self.check().returncode,1)


if __name__=='__main__': unittest.main()
