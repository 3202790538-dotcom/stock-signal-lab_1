"""Descriptive research from completed daily bars, with explicit sample limits.

Returns are simple price returns, not total returns. VaR/ES describe historical
one-day losses; signal changes and subsequent returns are not strategy results.
All portfolio weights refer to the supplied positions divided by account NAV.
"""
from math import isfinite

import numpy as np
import pandas as pd

RETURN_WINDOWS = (5, 20, 60, 120, 252)
SEMICONDUCTORS = {'NVDA', 'MRVL', 'AVGO', 'SKHY', 'MU'}
PORTFOLIO_SYMBOLS = {'NVDA', 'SPCX', 'MRVL', 'AVGO', 'SKHY', 'MU', 'GOOG'}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if isfinite(value) else None


def _frame(history):
    """Keep dates of invalid closes as gaps rather than creating false returns."""
    if not history:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'], dtype=float)
    frame = pd.DataFrame(history)
    if 'date' not in frame:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'], dtype=float)
    frame['date'] = pd.to_datetime(frame['date'], errors='coerce', utc=True).dt.normalize()
    frame = frame.dropna(subset=['date']).drop_duplicates('date', keep='last').set_index('date').sort_index()
    for name in ['open', 'high', 'low', 'close', 'volume']:
        if name not in frame:
            frame[name] = np.nan
        frame[name] = pd.to_numeric(frame[name], errors='coerce').replace([np.inf, -np.inf], np.nan)
    frame.loc[frame['close'] <= 0, 'close'] = np.nan
    frame.loc[frame['volume'] < 0, 'volume'] = np.nan
    return frame


def _window_return(close, days):
    if len(close) <= days or close.iloc[-days-1:].isna().any():
        return None
    return _number(close.iloc[-1] / close.iloc[-days-1] - 1)


def _window_extreme(frame, days, field, operation):
    if len(frame) < days:
        return None
    values = frame[field].iloc[-days:]
    if values.isna().any() or (values <= 0).any():
        return None
    return _number(getattr(values, operation)())


def _long_trend(close):
    result = {'status': 'insufficient', 'label': '长期样本不足',
              'description': '需要完整的200个交易日收盘价，暂不判断长期均线结构。',
              'priceVsMa200': None, 'ma50VsMa200': None, 'ma200': None,
              'bars': int(close.count()), 'requiredBars': 200}
    if len(close) < 200 or close.iloc[-200:].isna().any():
        return result
    price = float(close.iloc[-1])
    ma50 = float(close.iloc[-50:].mean())
    ma200 = float(close.iloc[-200:].mean())
    if price > ma50 > ma200:
        status, label = 'bullish', '长期多头排列'
    elif price > ma200:
        status, label = 'above', '高于200日均线'
    elif price < ma50 < ma200:
        status, label = 'bearish', '长期空头排列'
    else:
        status, label = 'below', '低于200日均线'
    result.update({'status': status, 'label': label,
                   'description': '比较收盘价与200日均线、50日与200日均线的位置；不是未来收益预测。',
                   'priceVsMa200': price / ma200 - 1,
                   'ma50VsMa200': ma50 / ma200 - 1, 'ma200': ma200})
    return result


def _signals(close):
    ma20 = close.rolling(20, min_periods=20).mean()
    ma50 = close.rolling(50, min_periods=50).mean()
    events = []
    previous = None
    for i in range(49, len(close)):
        price, m20, m50 = close.iloc[i], ma20.iloc[i], ma50.iloc[i]
        if not np.isfinite([price, m20, m50]).all():
            previous = None
            continue
        status = 'strong' if price > m20 > m50 else 'recovering' if price > m20 or price > m50 else 'weak'
        if previous is not None and status != previous:
            event = {'date': close.index[i].strftime('%Y-%m-%d'), 'status': status,
                     'close': float(price), 'return5': None, 'return20': None}
            for horizon in [5, 20]:
                if i + horizon < len(close) and close.iloc[i:i+horizon+1].notna().all():
                    event[f'return{horizon}'] = _number(close.iloc[i+horizon] / price - 1)
            events.append(event)
        previous = status
    return events[-6:]


def _research(frame):
    close = frame['close']
    recent = close.iloc[-252:]
    valid = recent.dropna()
    declines = recent / recent.cummax() - 1
    daily_returns = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).dropna().iloc[-252:]
    risk = {'historicalVar95': None, 'historicalEs95': None, 'observations': len(daily_returns),
            'minimumObservations': 60, 'window': 252, 'horizonDays': 1,
            'description': '最多252个历史日收益的5%分位数和左尾均值；以非负损失比例表示，不预测未来最大损失。'}
    if len(daily_returns) >= 60:
        quantile = float(daily_returns.quantile(.05))
        tail = daily_returns[daily_returns <= quantile]
        risk['historicalVar95'] = max(0.0, -quantile)
        risk['historicalEs95'] = max(0.0, -float(tail.mean()))
    levels = {'rangeBars': len(frame.iloc[-252:])}
    for days in (20, 60, 252):
        levels[f'low{days}'] = _window_extreme(frame, days, 'low', 'min')
        levels[f'high{days}'] = _window_extreme(frame, days, 'high', 'max')
    high = levels['high252']
    latest = _number(close.iloc[-1]) if len(close) else None
    levels['distanceHigh252'] = latest / high - 1 if latest is not None and high is not None else None
    volume_ratio = None
    if len(frame) >= 21:
        volumes = frame['volume'].iloc[-21:]
        baseline = volumes.iloc[:-1].mean()
        if volumes.notna().all() and baseline > 0:
            volume_ratio = _number(volumes.iloc[-1] / baseline)
    return {'returns': {f'd{days}': _window_return(close, days) for days in RETURN_WINDOWS},
            'longTrend': _long_trend(close),
            'drawdown': {'current': _number(declines.iloc[-1]) if len(declines) else None,
                         'max': _number(declines.min()) if len(valid) else None,
                         'bars': len(recent), 'validBars': len(valid), 'window': 252},
            'levels': levels, 'volumeRatio': volume_ratio, 'risk': risk,
            'signalHistory': _signals(close),
            'signalHistoryNote': '最近6次可计算的趋势状态切换；后续5/20日为价格收益，仅完整窗口展示，非策略回测，不含交易成本。'}


def _correlations(frames):
    symbols = list(frames)
    n = len(symbols)
    values = [[None] * n for _ in range(n)]
    observations = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            # The common interval start matters too: a two-day move must not be
            # paired with another stock's one-day return when one quote is absent.
            pair = pd.concat([frames[symbols[i]]['close'], frames[symbols[j]]['close']], axis=1)
            pair.columns = ['a', 'b']
            returns = pair.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).dropna().iloc[-126:]
            count = len(returns)
            observations[i][j] = observations[j][i] = count
            if count < 60 or returns['a'].std() <= 1e-12 or returns['b'].std() <= 1e-12:
                continue
            correlation = _number(returns['a'].corr(returns['b']))
            values[i][j] = values[j][i] = correlation
    return {'symbols': symbols, 'values': values, 'observations': observations,
            'minObservations': 60, 'window': 126,
            'description': '每对股票最多126个相同起止日期的日收益；至少60个样本，缺失不补零，恒定序列不计算相关性。'}


def _concentration(portfolio):
    nav = _number(portfolio.get('netLiquidation'))
    supplied = portfolio.get('positions')
    available = isinstance(supplied, list)
    positions = supplied if available else []
    # A missing market value is unknown exposure, never a zero-sized position.
    amounts = [_number(p.get('marketValue')) for p in positions]
    semi_amounts = [_number(p.get('marketValue')) for p in positions if p.get('symbol') in SEMICONDUCTORS]
    valid_nav = nav is not None and nav > 0
    complete = available and all(value is not None for value in amounts)
    semis_complete = available and all(value is not None for value in semi_amounts)
    # These are exposure weights, so short positions also consume concentration.
    absolute = [abs(value) for value in amounts] if complete else []
    return {'top3Pct': _number(sum(sorted(absolute, reverse=True)[:3]) / nav * 100) if valid_nav and complete else None,
            'grossWeightPct': _number(sum(absolute) / nav * 100) if valid_nav and complete else None,
            'semiconductorPct': _number(sum(abs(value) for value in semi_amounts) / nav * 100) if valid_nav and semis_complete else None,
            'positionCount': len(positions) if available else None,
            'scope': 'suppliedPositions', 'denominator': 'accountNetLiquidation'}


def enrich_research(result: dict) -> dict:
    """Enrich dashboard in place and return the same dict; does not fetch data."""
    frames = {}
    for item in result.get('stocks', []):
        frame = _frame(item.get('history') or [])
        item['research'] = _research(frame)
        frames[item['symbol']] = frame
    result['portfolioAnalytics'] = {
        'correlation': _correlations({symbol: frame for symbol, frame in frames.items() if symbol in PORTFOLIO_SYMBOLS}),
        'concentration': _concentration(result.get('portfolio') or {}),
        'notes': ['集中度仅覆盖传入的关注持仓，以账户净资产为分母；融资时合计可超过100%，不是全账户持仓归一化。',
                  '相关性是历史日价格收益的共同变化，样本短或缺失会显示空值；高相关不代表未来必然同步。',
                  '未对现金流、分红、拆股或汇率重新建模；指标沿用输入行情的价格口径。']}
    return result
