"""Independent manual account and settings; never writes the broker snapshot."""
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from risk import build_account_risk
from watchlists import safe_quote_symbol

_write_lock = RLock()
CORE_SYMBOLS = ('NVDA', 'SPCX', 'MRVL', 'AVGO', 'SKHY', 'MU', 'GOOG')


class PersonalSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    accountSource: Literal['broker', 'manual'] = 'broker'
    allocationPct: float = Field(100, ge=0, le=100)
    targetReturnPct: float = Field(50, ge=0, le=200)
    maxDrawdownPct: float = Field(30, ge=1, le=80)
    templateId: Literal['long_term', 'pullback', 'rotation'] = 'long_term'
    allowProfitAdd: bool = True
    reserveCashPct: float = Field(0, ge=0, le=100)


class ManualPosition(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    symbol: str
    quantity: float = Field(gt=0)
    averageCost: float = Field(gt=0)
    markPrice: float | None = Field(None, gt=0)
    stopPrice: float | None = Field(None, gt=0)
    sector: Literal['semiconductor', 'other', 'unknown'] = 'unknown'

    @field_validator('symbol')
    @classmethod
    def valid_symbol(cls, value):
        if not safe_quote_symbol(value):
            raise ValueError('股票代码需为大写 USD 行情代码，例如 AAPL 或 BRK-B')
        return value


class ManualAccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    cash: float
    positions: list[ManualPosition] = Field(max_length=500)

    @model_validator(mode='after')
    def unique_symbols(self):
        symbols = [p.symbol for p in self.positions]
        if len(symbols) != len(set(symbols)):
            raise ValueError('同一股票请合并为一行，并填写加权平均成本')
        return self


def atomic_json_write(path, value):
    """A private temporary file prevents partial reads and concurrent collisions."""
    path = Path(path)
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', prefix=path.name+'.',
                                    suffix='.tmp', dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _read(path, default):
    try:
        value = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise ValueError('本机账户或设置文件无法读取，请核对后重新保存') from exc
    if not isinstance(value, dict):
        raise ValueError('本机账户或设置文件格式无效')
    return value


def load_settings(data_dir):
    return PersonalSettings.model_validate(_read(Path(data_dir)/'personal-settings.json', {})).model_dump()


def save_settings(data_dir, value):
    parsed = value if isinstance(value, PersonalSettings) else PersonalSettings.model_validate(value)
    result = parsed.model_dump()
    atomic_json_write(Path(data_dir)/'personal-settings.json', result)
    return result


def load_manual_account(data_dir):
    saved = _read(Path(data_dir)/'manual-account.json', {'cash': 0, 'positions': [], 'updatedAt': None})
    fields = {key: value for key, value in saved.items() if key != 'updatedAt'}
    value = ManualAccountInput.model_validate(fields).model_dump()
    stamp = saved.get('updatedAt')
    if stamp is not None and not isinstance(stamp, str):
        raise ValueError('手填账户的保存时间无效')
    return {**value, 'updatedAt': stamp}


def save_manual_account(data_dir, value, *, now=None):
    parsed = value if isinstance(value, ManualAccountInput) else ManualAccountInput.model_validate(value)
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise ValueError('账户保存时间必须包含时区')
    result = {**parsed.model_dump(), 'updatedAt': stamp.astimezone(timezone.utc).isoformat()}
    atomic_json_write(Path(data_dir)/'manual-account.json', result)
    return result


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (ValueError, OverflowError):
        return None


def _daily_price(symbol, market, now):
    """Require an unambiguous USD quote and its matching completed OHLC bar."""
    candidates = [s for s in market.get('stocks', []) if isinstance(s, dict) and s.get('symbol') == symbol]
    if len(candidates) != 1:
        return None
    stock = candidates[0]
    if (stock.get('currency') != 'USD' or stock.get('quoteStatus') == 'stale'
            or stock.get('quoteType') not in ('EQUITY', 'ETF')):
        return None
    if any(isinstance(e, dict) and e.get('symbol') == symbol
           for e in (market.get('quoteRefresh') or {}).get('errors', [])):
        return None
    try:
        day = date.fromisoformat(stock['asOf'])
        local = now.astimezone(ZoneInfo('America/New_York'))
        cutoff = local.date() if local.hour >= 16 else local.date()-timedelta(days=1)
        if day > cutoff:
            return None
        bar = stock['history'][-1]
        if bar['date'] != stock['asOf']:
            return None
        values = [_finite(bar.get(k)) for k in ('open', 'high', 'low', 'close')]
        if any(v is None or v <= 0 for v in values):
            return None
        opening, high, low, close = values
        if low > min(opening, close) or high < max(opening, close) or low > high:
            return None
        if _finite(stock.get('close')) != close:
            return None
        return close, stock['asOf']
    except (KeyError, ValueError, TypeError, IndexError):
        return None


def selected_holdings(data_dir, market, settings=None, *, now=None):
    """Return a selected-source snapshot without updating its confirmation time."""
    settings = settings or load_settings(data_dir)
    source = PersonalSettings.model_validate(settings).accountSource
    if source == 'broker':
        return _read(Path(data_dir)/'holdings.json', {'source': 'IBKR', 'asOf': None,
                     'account_summary': {}, 'positions': [], 'balances': []})
    manual = load_manual_account(data_dir)
    now = now or datetime.now(timezone.utc)
    positions = []
    for row in manual['positions']:
        quote = _daily_price(row['symbol'], market, now)
        price = quote[0] if quote else row['markPrice']
        valuation_source = 'daily_close' if quote else 'manual_mark' if price is not None else 'missing'
        valuation_asof = quote[1] if quote else manual['updatedAt'] if price is not None else None
        quantity, cost = row['quantity'], row['averageCost']
        positions.append({'contract_description': row['symbol'], 'position': quantity,
            'asset_class': 'STK', 'currency': 'USD', 'average_price': cost, 'market_price': price,
            'market_value': _finite(price*quantity) if price is not None else None,
            'unrealized_pnl': _finite((price-cost)*quantity) if price is not None else None,
            'stop_price': row['stopPrice'], 'sector': row['sector'], 'source': 'manual',
            'valuationSource': valuation_source, 'valuationAsOf': valuation_asof,
            'stopSource': 'manual_plan' if row['stopPrice'] is not None else 'unknown'})
    gross = (_finite(sum(p['market_value'] for p in positions))
             if all(p['market_value'] is not None for p in positions) else None)
    cash = manual['cash']
    nav = _finite(cash+gross) if gross is not None else None
    summary = {'currency': 'USD', 'net_liquidation': nav, 'total_cash_value': cash,
        'gross_position_value': gross, 'available_funds': cash,
        'leverage': _finite(gross/nav) if nav is not None and nav > 0 and gross is not None else None,
        'high_water_mark': None}
    return {'source': 'manual', 'asOf': manual['updatedAt'], 'manualUpdatedAt': manual['updatedAt'],
        'valuationAsOf': min((p['valuationAsOf'] for p in positions if p['valuationAsOf']), default=None),
        'account_summary': summary, 'positions': positions,
        'balances': [{'currency': 'USD', 'cash_balance': cash, 'settled_cash': None, 'exchange_rate': 1}],
        'valuationComplete': gross is not None}


def build_account_view(raw_holdings):
    """Expose the selected account using the existing dashboard's seven-stock view."""
    risk = build_account_risk(raw_holdings)
    nav, positions = risk['netLiquidation'], []
    for raw in raw_holdings.get('positions') or []:
        symbol = raw.get('contract_description')
        if symbol not in CORE_SYMBOLS:
            continue
        quantity, average, value, pnl = [_finite(raw.get(k)) for k in
                                       ('position', 'average_price', 'market_value', 'unrealized_pnl')]
        cost = _finite(abs(quantity*average)) if quantity is not None and average is not None else None
        positions.append({'symbol': symbol, 'quantity': quantity, 'averagePrice': average,
            'marketPrice': _finite(raw.get('market_price')), 'marketValue': value, 'pnl': pnl,
            'returnPct': _finite(pnl/cost*100) if pnl is not None and cost is not None and cost > 0 else None,
            'weightPct': _finite(value/nav*100) if value is not None and nav is not None and nav > 0 else None})
    positions.sort(key=lambda row: CORE_SYMBOLS.index(row['symbol']))
    def total(key):
        return _finite(sum(p[key] for p in positions)) if all(p[key] is not None for p in positions) else None
    portfolio = {'asOf': raw_holdings.get('asOf') or '未提供',
        'source': 'manual' if raw_holdings.get('source') == 'manual' else 'IBKR',
        'netLiquidation': nav, 'cash': risk['cash'], 'leverage': risk['leverage'],
        'sevenMarketValue': total('marketValue'), 'sevenPnl': total('pnl'), 'positions': positions}
    return {'portfolio': portfolio, 'accountRisk': risk}
