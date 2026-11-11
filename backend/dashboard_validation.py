"""Validate the cached dashboard's required display contract without coercion."""
from datetime import date, datetime
import math
from watchlists import safe_quote_symbol


def _number(value, nullable=False):
    if value is None:
        return nullable
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _strings(record, keys):
    return isinstance(record, dict) and all(isinstance(record.get(key), str) for key in keys)


def _numbers(record, keys, nullable=False):
    return isinstance(record, dict) and all(key in record and _number(record[key], nullable) for key in keys)


def _date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _records(value, validate):
    return isinstance(value, list) and all(isinstance(row, dict) and validate(row) for row in value)


def _bar(row):
    return (_date(row.get('date')) and _numbers(row, ('open', 'high', 'low', 'close'))
            and all(row[key] > 0 for key in ('open', 'high', 'low', 'close'))
            and _numbers(row, ('volume',), nullable=True))


def _stock(stock):
    return (safe_quote_symbol(stock.get('symbol'))
            and _strings(stock, ('name', 'sector', 'status', 'observation')) and _date(stock.get('asOf'))
            and _numbers(stock, ('close', 'changePct')) and stock['close'] > 0
            and _numbers(stock, ('ret20', 'ret60', 'ma20', 'ma50', 'ma200', 'rsi', 'volatility', 'atr', 'relative20'), nullable=True)
            and type(stock.get('bars')) is int and stock['bars'] > 0
            and bool(stock.get('history')) and _records(stock['history'], _bar)
            and _records(stock.get('signals'), lambda row: _strings(row, ('id', 'label', 'tone', 'description'))))


def _fundamental(row):
    return (_strings(row, ('symbol', 'asOf', 'summary', 'risk', 'source'))
            and ('metrics' not in row or _records(row['metrics'], lambda item: _strings(item, ('label', 'value'))))
            and ('event' not in row or _strings(row['event'], ('date', 'label', 'source')))
            and ('annualEps' not in row or _number(row['annualEps'])))


def valid_dashboard(data):
    """Fail closed on incomplete snapshots; optional unavailable metrics stay null."""
    if not _strings(data, ('generatedAt', 'marketAsOf', 'marketSource', 'engine')):
        return False
    try:
        if datetime.fromisoformat(data['generatedAt'].replace('Z', '+00:00')).tzinfo is None:
            return False
    except ValueError:
        return False
    if not _date(data['marketAsOf']) or not data.get('stocks') or not _records(data['stocks'], _stock):
        return False
    benchmark = data.get('benchmark')
    if (not _numbers(benchmark, ('close', 'changePct')) or benchmark['close'] <= 0
            or not _date(benchmark.get('asOf')) or not benchmark.get('history')
            or not _records(benchmark['history'], _bar)):
        return False
    portfolio = data.get('portfolio')
    if (not _strings(portfolio, ('asOf',))
            or not _numbers(portfolio, ('netLiquidation', 'cash', 'leverage', 'sevenMarketValue', 'sevenPnl'), nullable=True)
            or not _records(portfolio.get('positions'), lambda row: safe_quote_symbol(row.get('symbol'))
                            and _numbers(row, ('quantity', 'averagePrice', 'marketPrice', 'marketValue', 'pnl', 'returnPct', 'weightPct'), nullable=True))):
        return False
    if (not _records(data.get('fundamentals'), _fundamental)
            or not _records(data.get('rules'), lambda row: _strings(row, ('name', 'description')))
            or not _records(data.get('sources'), lambda row: _strings(row, ('name', 'status', 'asOf', 'detail')))):
        return False
    if 'marketWarnings' in data and not _records(data['marketWarnings'], lambda row:
            _strings(row, ('symbol', 'date')) and isinstance(row.get('missingFields'), list)
            and all(isinstance(field, str) for field in row['missingFields'])):
        return False
    if 'quoteRefresh' in data:
        refresh = data['quoteRefresh']
        if not isinstance(refresh, dict) or not _records(refresh.get('errors'), lambda row: _strings(row, ('symbol', 'message'))):
            return False
    return True
