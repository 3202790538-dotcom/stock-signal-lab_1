#!/usr/bin/env python3
"""Read-only publication guard. Reports paths/categories, never matched values.

Default: working-tree publication candidates; --staged: all index blobs;
--history: all locally reachable commits. This complements manual review and
does not claim to recognize every possible private value in arbitrary prose.
"""
import argparse
import fnmatch
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ACCOUNT_ID = re.compile(r'\b(?:DU|U)[0-9]{6,}\b')
PRIVATE_PATH = re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/')
PRIVATE_KEY = re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----')
TOKEN = re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|sk-(?:proj-)?[A-Za-z0-9_-]{25,})\b')
PRIVATE_FILES = ('holdings*.json', 'manual-account*.json', 'personal-settings*.json',
                 'research-settings*.json', 'ibkr-watchlists*.json', 'account-export*', 'flex-query*')
BINARY_SUFFIXES = {'.png','.jpg','.jpeg','.webp','.heic','.gif','.pdf','.xlsx','.xls','.zip','.tar','.tgz','.gz',
                   '.csv','.tsv','.xml','.parquet','.feather','.pickle','.pkl','.h5','.hdf5'}


def git(root, *args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE)


def private_tokens(root):
    tokens=set()
    # Only identifier fields are read; portfolio amounts are never logged.
    def visit(value):
        if isinstance(value,dict):
            for key,item in value.items():
                normalized=re.sub(r'[^a-z]','',key.lower())
                if isinstance(item,str) and (normalized in {'secemail','email','accountid','accountnumber','accountidentifier','accountname'}):
                    if len(item)>=6: tokens.add(item)
                elif isinstance(item,(dict,list)): visit(item)
        elif isinstance(value,list):
            for item in value:visit(item)
    for name in ('research-settings.json','holdings.json','manual-account.json'):
        try:visit(json.loads((root/'backend/data'/name).read_text()))
        except (OSError,ValueError):pass
    return tokens


def path_problem(name, historical=False):
    path=PurePosixPath(name);parts=path.parts;base=path.name.lower()
    if name.startswith(('backend/data/','private/','local/','screenshots/','captures/')):
        return 'local private data directory'
    if not historical and name.startswith('docs/superpowers/'):
        return 'private development session notes'
    if any(fnmatch.fnmatch(base,pattern) for pattern in PRIVATE_FILES):return 'account/settings export'
    if base.startswith(('.env','.dev.vars')) and not base.endswith(('.example','.sample')):return 'local environment'
    if path.suffix.lower() in {'.log','.pid','.db','.sqlite','.sqlite3','.pem','.key','.p12','.pfx','.bak','.backup'}:
        return 'log, credential, database or backup'
    if path.suffix.lower() in BINARY_SUFFIXES:return 'binary/document/archive requires separate privacy review'
    if any(p in {'.git','.venv','.venv-research','node_modules','dist','.next'} for p in parts):return 'local generated directory'
    return None


def content_problems(name, body, tokens):
    try:text=body.decode('utf-8')
    except UnicodeError:return ['unreviewed binary content']
    problems=[]
    for label,pattern in [('broker account identifier',ACCOUNT_ID),('personal machine path',PRIVATE_PATH),
                          ('private key',PRIVATE_KEY),('credential token',TOKEN)]:
        if pattern.search(text):problems.append(label)
    if any(token in text for token in tokens):problems.append('local private identifier')
    if name.endswith('.json'):
        try:
            payload=json.loads(text)
            if isinstance(payload,dict) and ('account_summary' in payload or
                    {'cash','positions','updatedAt'}<=payload.keys() or
                    {'positions','balances','asOf'}<=payload.keys() or
                    isinstance(payload.get('portfolio'),dict) and 'positions' in payload['portfolio']):
                problems.append('personal account snapshot structure')
        except ValueError:pass
    return problems


def candidates(root):
    return sorted(set(name for name in git(root,'ls-files','-z','--cached','--others','--exclude-standard').decode().split('\0') if name))


def scan(root, mode='working'):
    tokens=private_tokens(root);issues=[];count=0;seen=set()
    def inspect(name, body, context='', historical=False):
        nonlocal count
        count+=1
        reasons=content_problems(name,body,tokens)
        issue=path_problem(name,historical)
        if issue:reasons.append(issue)
        for reason in reasons:issues.append((context+name,reason))
    if mode=='history':
        commits=git(root,'rev-list','--all').decode().splitlines()
        for commit in commits:
            metadata=git(root,'show','-s','--format=%B%n%an%n%ae%n%cn%n%ce',commit)
            inspect('(commit metadata)',metadata,commit[:12]+':',True)
            for row in git(root,'ls-tree','-r','-z',commit).split(b'\0'):
                if not row:continue
                header,name=row.split(b'\t',1);mode_bits,kind,oid=header.decode().split();name=name.decode()
                if kind!='blob':issues.append((name,'unreviewed submodule'));continue
                if (oid,name) in seen:continue
                seen.add((oid,name))
                inspect(name,git(root,'cat-file','blob',oid),commit[:12]+':',True)
                if mode_bits=='120000':issues.append((name,'symlink requires review'))
    elif mode=='staged':
        for row in git(root,'ls-files','--stage','-z').split(b'\0'):
            if not row:continue
            header,name=row.split(b'\t',1);mode_bits,oid,stage=header.decode().split();name=name.decode()
            if stage!='0':issues.append((name,'unmerged index'));continue
            inspect(name,git(root,'cat-file','blob',oid))
            if mode_bits=='120000':issues.append((name,'symlink requires review'))
    else:
        for name in candidates(root):
            file=root/name
            if file.is_symlink():issues.append((name,'symlink requires review'));continue
            if file.is_file():inspect(name,file.read_bytes())
    return issues,count


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    modes=parser.add_mutually_exclusive_group();modes.add_argument('--staged',action='store_true');modes.add_argument('--history',action='store_true')
    args=parser.parse_args();mode='history' if args.history else 'staged' if args.staged else 'working'
    try:issues,count=scan(args.root,mode)
    except (OSError,subprocess.CalledProcessError,ValueError):
        print('Privacy check could not finish; publication blocked.',file=sys.stderr);return 2
    for name,reason in issues:
        for token in private_tokens(args.root):name=name.replace(token,'[private]')
        print(f'BLOCKED: {name}: {reason}')
    print(f'Privacy check ({mode}): {count} files/records checked; {len(issues)} findings.')
    return 1 if issues else 0


if __name__=='__main__':raise SystemExit(main())
