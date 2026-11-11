"""Public, sourced security identities and coarse concentration classifications.

SEC SIC describes the issuer, not a prediction or a look-through fund exposure.
No account values are read or changed. Sector tags already entered by the user
remain the planner's responsibility; this module only enriches public quotes.
"""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile

CACHE_NAME = 'security-metadata.json'
MAX_AGE_DAYS = 30
SEMI_SIC = {'3674', '3559'}  # SIC 3559 includes equipment: conservatively reserve semiconductor capacity.
FUND_FORMS = {'N-2', 'N-2/A', 'N-CSR', 'N-CSRS', 'N-CEN', 'NPORT-P', 'N-PORT'}


def _now(now=None):
    return now or datetime.now(timezone.utc)


def _identity(quote):
    return {key: quote.get(key) for key in ('symbol', 'name', 'currency', 'quoteType')}


def _base(quote, now):
    symbol = quote.get('symbol')
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9-]{0,14}', symbol):
        raise ValueError('invalid public quote symbol')
    return dict(symbol=symbol, name=quote.get('name'), quoteIdentity=_identity(quote),
                status='verified', sectorClass='unknown', securityKind='unknown', industry='',
                lastAttemptAt=_now(now).isoformat(), lastError=None)


def sec_record(quote, payload, now=None):
    """Require SEC's current ticker membership; never identify an issuer by name alone."""
    result = _base(quote, now)
    symbol = result['symbol']
    tickers = payload.get('tickers')
    normalize = lambda value: str(value).strip().upper().replace('.', '-')
    if not isinstance(tickers, list) or symbol not in {normalize(t) for t in tickers}:
        raise ValueError('SEC ticker does not match requested security')
    cik = payload.get('cik')
    if isinstance(cik, bool) or not str(cik).isdigit() or int(cik) <= 0:
        raise ValueError('SEC CIK is absent or invalid')
    sic = str(payload.get('sic') or '').strip()
    industry = str(payload.get('sicDescription') or '').strip()
    forms = payload.get('recentForms') or []
    fund = payload.get('entityType') == 'investment' or bool(FUND_FORMS.intersection(forms))
    kind = 'fund' if fund else 'shell' if sic == '6770' else 'equity' if re.fullmatch(r'[1-9][0-9]{3}', sic) and sic != '9999' else 'unknown'
    sector = ('semiconductor' if sic in SEMI_SIC or 'semiconductor' in industry.lower() else 'other') if kind == 'equity' and industry else 'unknown'
    result.update(cik=int(cik), name=str(payload.get('name') or quote.get('name')),
                  sic=sic or None, industry=industry, sectorClass=sector, securityKind=kind,
                  evidence={'source':'SEC EDGAR submissions (EdgarTools)',
                            'url':f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json',
                            'checkedAt':_now(now).isoformat(), 'sic':sic or None,
                            'tickers':tickers, 'entityType':payload.get('entityType'),
                            'fundForms':sorted(FUND_FORMS.intersection(forms))})
    return result


def fund_record(quote, now=None):
    """A fund type is known; its constituent semiconductor exposure remains unknown."""
    if quote.get('quoteType') not in ('ETF', 'MUTUALFUND') or quote.get('quoteStatus') not in ('ready', 'available'):
        raise ValueError('fund quote identity is not verified')
    result = _base(quote, now)
    result.update(securityKind='fund', industry='ETF / Fund', evidence={
        'source':'Yahoo Finance chart security type',
        'url':f"https://query1.finance.yahoo.com/v8/finance/chart/{quote['symbol']}?range=2y&interval=1d&includePrePost=false",
        'checkedAt':_now(now).isoformat(), 'quoteType':quote['quoteType'], 'priceAsOf':quote.get('asOf')})
    return result


def load_metadata(data_dir):
    try:
        payload = json.loads((Path(data_dir)/CACHE_NAME).read_text())
        records = payload.get('records') if isinstance(payload, dict) else None
        return records if isinstance(records, dict) else {}
    except (OSError, ValueError):
        return {}


def save_metadata(data_dir, records):
    directory = Path(data_dir); directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=directory, prefix='metadata-', suffix='.tmp', delete=False) as file:
        json.dump({'schemaVersion':1, 'records':records}, file, ensure_ascii=False, allow_nan=False)
        temporary = Path(file.name)
    temporary.replace(directory/CACHE_NAME)


def _usable(record, quote, now):
    if not isinstance(record, dict) or record.get('status') != 'verified' or record.get('quoteIdentity') != _identity(quote):
        return False
    if record.get('sectorClass') not in ('semiconductor', 'other', 'unknown') or record.get('securityKind') not in ('equity', 'fund', 'shell', 'unknown'):
        return False
    evidence = record.get('evidence')
    if not isinstance(evidence, dict) or not evidence.get('url') or not evidence.get('source'):
        return False
    try:
        checked = datetime.fromisoformat(evidence['checkedAt'].replace('Z', '+00:00'))
        age = (_now(now)-checked).total_seconds()
        return 0 <= age <= MAX_AGE_DAYS*86400
    except (KeyError, AttributeError, TypeError, ValueError):
        return False


def enrich_market(market, data_dir, now=None):
    """Copy just the envelope and quotes, preserving OHLC arrays and display themes."""
    records = load_metadata(data_dir)
    stocks = []
    for quote in market.get('stocks', []):
        row = dict(quote)
        record = records.get(quote.get('symbol'))
        if _usable(record, quote, now):
            row.update(sectorClass=record['sectorClass'], securityKind=record['securityKind'],
                       industry=record.get('industry') or '', metadataEvidence=dict(record['evidence']))
            if record.get('lastError'): row['metadataRefreshError'] = record['lastError']
        stocks.append(row)
    return {**market, 'stocks':stocks}


def verify_metadata(market, data_dir, fetch_sec, now=None):
    """Refresh public metadata; provider failure cannot change a verified issuer."""
    records = load_metadata(data_dir)
    checked = _now(now)
    summary = Counter()
    for quote in market.get('stocks', []):
        symbol = quote.get('symbol')
        try:
            if quote.get('currency') != 'USD' or quote.get('quoteStatus') not in ('ready', 'available'):
                raise ValueError('quote identity is not current')
            if quote.get('quoteType') in ('ETF', 'MUTUALFUND'):
                record = fund_record(quote, checked)
            elif quote.get('quoteType') == 'EQUITY':
                record = sec_record(quote, fetch_sec(symbol), checked)
            else:
                raise ValueError('security type not verified')
            records[symbol] = record
            summary['verified'] += 1
            summary[record['securityKind']] += 1
            if record['sectorClass'] != 'unknown': summary['classified'] += 1
        except Exception as exc:
            old = records.get(symbol)
            error = '公开资料源暂未完成核验（'+type(exc).__name__+'）；未自动推断行业。'
            if _usable(old, quote, checked):
                records[symbol] = {**old, 'lastAttemptAt':checked.isoformat(), 'lastError':error}
                summary['retained'] += 1
            else:
                records[symbol] = {**_base(quote, checked), 'status':'error', 'lastError':error}
                summary['failed'] += 1
        # Persist each completed item so a process timeout preserves progress.
        save_metadata(data_dir, records)
    return {'records':records, 'summary':dict(summary)}
