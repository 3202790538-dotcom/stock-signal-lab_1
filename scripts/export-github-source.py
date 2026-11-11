#!/usr/bin/env python3
"""Build a checked source archive without Git history or ignored local files."""
import argparse
import importlib.util
from pathlib import Path
import sys
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('privacy_guard',ROOT/'scripts/check-privacy.py')
guard=importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();root=args.root.resolve()
    output=(args.output or root/'outputs/northstar-github-source.zip').resolve()
    issues,count=guard.scan(root)
    if issues:
        print(f'Publication blocked: {len(issues)} privacy findings. Run scripts/check-privacy.py.',file=sys.stderr)
        return 1
    # Use exactly the inspected bytes. Never follow links or copy ignored data.
    sources={}
    for name in guard.candidates(root):
        path=root/name
        if path.is_symlink():raise ValueError('symlink appeared during export')
        if not path.is_file():continue
        body=path.read_bytes()
        if guard.path_problem(name) or guard.content_problems(name,body,guard.private_tokens(root)):
            raise ValueError('source changed during export')
        sources[name]=body
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix(output.suffix+'.tmp')
    try:
        with ZipFile(temporary,'w',compression=ZIP_DEFLATED) as archive:
            for name,body in sources.items():
                info=ZipInfo.from_file(root/name,arcname='stock-signal-lab/'+name)
                archive.writestr(info,body,compress_type=ZIP_DEFLATED)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'Exported {len(sources)} reviewed source files, without .git history or local account data.')
    print(output)
    return 0


if __name__=='__main__':raise SystemExit(main())
