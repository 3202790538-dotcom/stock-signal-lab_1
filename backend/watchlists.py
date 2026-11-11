"""Public display metadata derived from the private, user-imported IBKR lists.

Contract identity and list membership are independent of the public quote
universe. This module never contacts the broker or changes its source snapshot.
"""
from pathlib import Path
import json
import math
import re


def safe_quote_symbol(value):
    """Only identifiers safe in provider URLs, cache names, and Qlib paths."""
    return isinstance(value, str) and re.fullmatch(r'[A-Z][A-Z0-9-]{0,14}', value) is not None


def _text(value, fallback=''):
    return str(value).strip()[:200] if value is not None else fallback


def _counts(instruments):
    counts = dict.fromkeys(('ready', 'stale', 'unavailable', 'unsupported', 'pending'), 0)
    for item in instruments:
        counts[item['status']] += 1
    return counts


def load_registry(data_dir):
    """Read the authorized snapshot and return only the display contract."""
    try:
        snapshot = json.loads((Path(data_dir) / 'ibkr-watchlists.json').read_text())
        if not isinstance(snapshot, dict):
            snapshot = {}
    except (OSError, ValueError):
        snapshot = {}
    resolved = snapshot.get('resolvedContracts') or {}
    if not isinstance(resolved, dict):
        resolved = {}
    groups, by_contract = [], {}
    for raw_group in snapshot.get('lists') or []:
        if not isinstance(raw_group, dict):
            continue
        group_id = _text(raw_group.get('id'))
        if not group_id:
            continue
        group = {'id': group_id, 'name': _text(raw_group.get('name'), group_id),
                 'count': 0, 'contractIds': [], 'symbols': []}
        for raw in raw_group.get('instruments') or []:
            if not isinstance(raw, dict):
                continue
            contract_id = _text(raw.get('contract_id_ex'))
            if not contract_id:
                continue
            if contract_id not in by_contract:
                details = resolved.get(contract_id) or {}
                if not isinstance(details, dict):
                    details = {}
                symbol = _text(details.get('symbol') or raw.get('contract_description')).upper()
                security_type = _text(details.get('securityType')).upper()
                country = _text(details.get('country')).upper()
                currency = _text(details.get('currency')).upper() or None
                market = _text(details.get('exchange'))
                reason = None
                if security_type in ('CASH', 'FX') or market == 'IDEALPRO':
                    reason = '外汇合约已导入；当前 USD 股票日线模块不支持此合约。'
                elif country not in ('', 'US') or currency not in (None, 'USD') or market == 'TSEJ':
                    reason = '海外市场合约已导入；当前模块仅接入可核验的 USD 日线。'
                elif security_type not in ('', 'STK', 'ETF'):
                    reason = '该证券类型已导入，当前股票日线模块尚不支持。'
                elif not safe_quote_symbol(symbol):
                    reason = '证券代码尚未映射到可核验的 USD 行情源。'
                supported = reason is None
                by_contract[contract_id] = {
                    'contractId': contract_id, 'symbol': symbol, 'quoteSymbol': symbol if supported else None,
                    'name': _text(details.get('name'), symbol), 'market': market or '待核验',
                    'currency': currency, 'supported': supported,
                    'status': 'pending' if supported else 'unsupported', 'reason': reason,
                    'asOf': None, 'groupIds': []}
            item = by_contract[contract_id]
            if group_id not in item['groupIds']:
                item['groupIds'].append(group_id)
            if contract_id not in group['contractIds']:
                group['contractIds'].append(contract_id)
                group['symbols'].append(item['symbol'])
        group['count'] = len(group['contractIds'])
        groups.append(group)
    instruments = list(by_contract.values())
    return {'source': 'IBKR', 'importedAt': snapshot.get('importedAt'),
            'uniqueCount': len(instruments), 'groups': groups, 'instruments': instruments,
            'counts': _counts(instruments)}


def quote_symbols(data_dir, base_symbols):
    """Stable quote order, independent of the original holdings/strategy scope."""
    imported = [i['quoteSymbol'] for i in load_registry(data_dir)['instruments'] if i['supported']]
    try:
        snapshot = json.loads((Path(data_dir) / 'holdings.json').read_text())
        positions = snapshot.get('positions', []) if isinstance(snapshot, dict) else []
    except (OSError, ValueError):
        positions = []
    held = []
    for item in positions if isinstance(positions, list) else []:
        if not isinstance(item, dict):
            continue
        quantity = item.get('position')
        if (isinstance(quantity, (int, float)) and not isinstance(quantity, bool)
                and math.isfinite(quantity) and quantity != 0
                and item.get('currency') == 'USD' and item.get('asset_class') == 'STK'):
            held.append(item.get('contract_description'))
    try:
        manual = json.loads((Path(data_dir) / 'manual-account.json').read_text())
        rows = manual.get('positions', []) if isinstance(manual, dict) else []
    except (OSError, ValueError):
        rows = []
    manual_symbols = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        quantity = row.get('quantity')
        if (isinstance(quantity, (int, float)) and not isinstance(quantity, bool)
                and math.isfinite(quantity) and quantity > 0):
            manual_symbols.append(row.get('symbol'))
    return list(dict.fromkeys(s for s in [*base_symbols, *imported, *held, *manual_symbols] if safe_quote_symbol(s)))


def watchlist_status(data_dir, stocks, errors=None):
    """Join imported contract membership to real quote availability, no I/O writes."""
    registry = load_registry(data_dir)
    by_symbol = {s.get('symbol'): s for s in stocks if isinstance(s, dict)}
    failures = {e.get('symbol'): e for e in errors or [] if isinstance(e, dict)}
    for item in registry['instruments']:
        if not item['supported']:
            continue
        quote = by_symbol.get(item['quoteSymbol'])
        error = failures.get(item['quoteSymbol'])
        if quote:
            item.update(name=quote.get('name') or item['name'], market=quote.get('exchange') or item['market'],
                        currency=quote.get('currency') or item['currency'], asOf=quote.get('asOf'))
            stale = quote.get('quoteStatus') == 'stale' or bool(error)
            item['status'] = 'stale' if stale else 'ready'
            item['reason'] = ((error or {}).get('message') or quote.get('quoteError') or '当前显示上次有效行情。') if stale else None
        elif error:
            item.update(status='unavailable', reason=error.get('message') or '尚未取得有效日线。')
    registry['counts'] = _counts(registry['instruments'])
    return registry
