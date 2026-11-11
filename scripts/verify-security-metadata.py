#!/usr/bin/env python3
"""Resolve public company SIC and fund types; prints aggregate counts only."""
from contextlib import redirect_stdout, redirect_stderr
import argparse
import io
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from security_metadata import verify_metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('data_dir', nargs='?', default=str(ROOT/'backend/data'))
    args = parser.parse_args()
    folder = Path(args.data_dir)
    settings = json.loads((folder/'research-settings.json').read_text())
    email = settings.get('secEmail', '').strip()
    if '@' not in email or '\n' in email or '\r' in email:
        raise ValueError('SEC contact is required')
    market = json.loads((folder/'dashboard.json').read_text())
    from edgar import Company, set_identity
    set_identity('Northstar Research '+email)
    last_call = 0.

    def fetch_sec(symbol):
        nonlocal last_call
        elapsed = time.monotonic()-last_call
        if elapsed < .5: time.sleep(.5-elapsed)
        last_call = time.monotonic()
        # Prevent library diagnostics from exposing configured contact headers.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            entity = Company(symbol)
            data = entity.data
            forms = data.filings.data.column('form').to_pylist() if data.filings is not None else []
            return {'cik':entity.cik, 'name':entity.name, 'tickers':entity.tickers,
                    'sic':entity.sic, 'sicDescription':entity.industry,
                    'entityType':data.entity_type, 'recentForms':forms}

    result = verify_metadata(market, folder, fetch_sec=fetch_sec)
    print(json.dumps(result['summary'], ensure_ascii=False))


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
