"""Isolated research dependency environment; never expose the SEC contact."""
from contextlib import redirect_stdout
import io,json,sys
from pathlib import Path
from financials import build_company_research

def main():
    symbol=sys.argv[1];folder=Path(sys.argv[2])
    settings=json.loads((folder/'research-settings.json').read_text())
    email=settings.get('secEmail','').strip()
    if '@' not in email or '\n' in email or '\r' in email:
        raise ValueError('SEC contact is required')
    market=json.loads((folder/'dashboard.json').read_text())
    prices={s['symbol']:{'price':s['close'],'priceAsOf':s['asOf'],'currency':s.get('currency','USD'),'source':'Yahoo Finance daily close'} for s in market.get('stocks',[])}
    with redirect_stdout(io.StringIO()):
        result=build_company_research(symbol,'Northstar Research '+email,prices)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__}),file=sys.stderr)
        raise SystemExit(1)
