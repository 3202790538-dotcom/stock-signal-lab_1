"""Past-only daily factors and strict OHLCV calendar validation."""
from backtest import _date, _history, _number


def validate_histories(histories, spy_history, end_date=None):
    if not isinstance(histories, dict) or not histories:
        raise ValueError('股票日线不能为空')
    def validate(rows):
        prices = _history(rows, end_date)
        source = [r for r in rows if end_date is None or r['date'] <= end_date]
        for clean, row in zip(prices, source):
            clean['volume'] = _number(row.get('volume'), f"{clean['date']} volume", 0)
        return prices
    spy = validate(spy_history)
    stocks = {symbol: validate(history) for symbol, history in histories.items()}
    dates = [r['date'] for r in spy]
    for symbol, rows in stocks.items():
        if [r['date'] for r in rows] != dates:
            raise ValueError(f'{symbol} 与 SPY 日期未对齐；禁止填补缺失行情')
    return stocks, spy


def factors(rows):
    output = []
    true_ranges = []
    for i, bar in enumerate(rows):
        previous = rows[i-1]['close'] if i else bar['close']
        true_ranges.append(max(bar['high']-bar['low'], abs(bar['high']-previous), abs(bar['low']-previous)))
        item = {}
        for window in (20, 50, 200):
            item[f'sma{window}'] = (sum(r['close'] for r in rows[i-window+1:i+1])/window
                                      if i >= window-1 else None)
        item['atr14'] = sum(true_ranges[i-13:i+1])/14 if i >= 13 else None
        item['avgVolume20'] = sum(r['volume'] for r in rows[i-20:i])/20 if i >= 20 else None
        item['avgDollar20'] = sum(r['volume']*r['close'] for r in rows[i-20:i])/20 if i >= 20 else None
        for window in (63, 126):
            item[f'return{window}'] = (bar['close']/rows[i-window]['close']-1)*100 if i >= window else None
        output.append(item)
    return output


def condition(label, value, threshold, passed):
    return {'label': label, 'value': value, 'threshold': threshold, 'passed': bool(passed)}


def signal_details(rows, fs, spy, spy_fs, i, template, parameters):
    """Return conditions, technical eligibility, rank; market is separate."""
    bar, f = rows[i], fs[i]
    c = [condition('收盘价 ≥ 10 美元', bar['close'], 10, bar['close'] >= 10),
         condition('前20日平均成交额 ≥ 2000万美元', f['avgDollar20'], 20_000_000, f['avgDollar20'] >= 20_000_000),
         condition('收盘价 > SMA50', bar['close'], f['sma50'], bar['close'] > f['sma50']),
         condition('SMA50 > SMA200', f['sma50'], f['sma200'], f['sma50'] > f['sma200'])]
    if template == 'breakout':
        high = max(r['high'] for r in rows[i-parameters['lookback']:i])
        c += [condition('收盘突破此前N日最高价', bar['close'], high, bar['close'] > high),
              condition('成交量 ≥ 前20日均量 × 1.2', bar['volume'], f['avgVolume20']*1.2, bar['volume'] >= f['avgVolume20']*1.2)]
    elif template == 'pullback':
        window = range(i-parameters['pullbackDays']+1, i+1)
        touched = any(rows[j]['low'] <= fs[j]['sma20'] for j in window)
        intact = all(rows[j]['close'] > fs[j]['sma50'] for j in window)
        c += [condition('最近P日最低价曾触及SMA20', '是' if touched else '否', '是', touched),
              condition('最近P日每个收盘 > 当日SMA50', '是' if intact else '否', '是', intact),
              condition('信号收盘 > SMA20', bar['close'], f['sma20'], bar['close'] > f['sma20']),
              condition('信号收盘 > 前日最高价', bar['close'], rows[i-1]['high'], bar['close'] > rows[i-1]['high']),
              condition('成交量 ≥ 前20日均量', bar['volume'], f['avgVolume20'], bar['volume'] >= f['avgVolume20'])]
    if template != 'rotation':
        rank = f['return63'] - spy_fs[i]['return63']
        c.append(condition('股票R63 > SPY R63', f['return63'], spy_fs[i]['return63'], rank > 0))
        rank_eligible = all(x['passed'] for x in c)
    else:
        rank = f[f"return{parameters['momentumDays']}"]
        c.append(condition('M日收益 > 0', rank, 0, rank > 0))
        # Common price/liquidity constraints restrict purchases, not the weekly ranking.
        rank_eligible = all(x['passed'] for x in c[2:])
    market = spy[i]['close'] > spy_fs[i]['sma200']
    c.append(condition('SPY收盘 > SMA200', spy[i]['close'], spy_fs[i]['sma200'], market))
    return c, rank_eligible, rank
