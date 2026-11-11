"""One-position, daily-bar breakout experiment with explicit cash accounting.

Signals use only completed bars. Prices are raw OHLC: this module does not
adjust splits, dividends or other corporate actions and makes no claims about
future performance. Fractional shares let position sizing spend a fixed budget
without leverage; fills remain a simple slippage model, not a liquidity model.
"""
from datetime import date
import math


def _number(value, name, minimum, maximum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} 必须是有限数值')
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f'{name} 必须是有限数值') from None
    if (not math.isfinite(value) or value < minimum
            or (maximum is not None and value > maximum)
            or (integer and not value.is_integer())):
        bound = f'{minimum}–{maximum}' if maximum is not None else f'≥ {minimum}'
        raise ValueError(f'{name} 必须为{bound}范围内的' + ('整数' if integer else '有限数值'))
    return int(value) if integer else value


def _date(value, name):
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError(f'{name} 必须是有效的 YYYY-MM-DD 日期') from None
    return value


def _history(history, end_date):
    if not isinstance(history, list):
        raise ValueError('历史行情必须是日线列表')
    result = []
    previous = None
    for row in history:
        if not isinstance(row, dict):
            raise ValueError('历史行情包含无效日线')
        day = _date(row.get('date'), '日线日期')
        if day is None:
            raise ValueError('日线缺少日期')
        # A request ending in the past never uses later OHLC or return values.
        if end_date is not None and day > end_date:
            continue
        if previous is not None and day <= previous:
            raise ValueError(f'{day} 日线日期重复或未按时间升序排列')
        previous = day
        prices = {}
        for key in ('open', 'high', 'low', 'close'):
            value = _number(row.get(key), f'{day} 的 {key}', 0)
            if value <= 0:
                raise ValueError(f'{day} 的 {key} 必须为正数')
            prices[key] = value
        if (prices['high'] < max(prices['open'], prices['close'], prices['low'])
                or prices['low'] > min(prices['open'], prices['close'], prices['high'])):
            raise ValueError(f'{day} 的 OHLC 不一致；请修复行情源后重试')
        result.append({'date': day, **prices})
    return result


def _entry(budget, bar, fee, slip):
    price = bar['open'] * (1 + slip)
    quantity = budget / (price * (1 + fee))
    commission = quantity * price * fee
    return {
        'entryDate': bar['date'], 'entryReferencePrice': bar['open'],
        'entryPrice': price, 'quantity': quantity, 'entryCapital': budget,
        'entryFee': commission, 'entrySlippage': quantity * (price - bar['open']),
        # Exact budget sizing leaves zero cash and never creates borrowing.
        'cashAfterEntry': 0.0,
    }


def _exit(position, bar, fee, slip):
    price = bar['close'] * (1 - slip)
    gross = position['quantity'] * price
    commission = gross * fee
    proceeds = gross - commission
    pnl = proceeds - position['entryCapital']
    slippage = position['quantity'] * (bar['close'] - price)
    return {
        **position, 'exitDate': bar['date'], 'exitReferencePrice': bar['close'],
        'exitPrice': price, 'exitFee': commission, 'exitSlippage': slippage,
        'exitProceeds': proceeds, 'netPnl': pnl,
        'returnPct': pnl / position['entryCapital'] * 100,
        'totalCosts': position['entryFee'] + position['entrySlippage'] + commission + slippage,
    }


def run_backtest(history: list[dict], lookback=20, holding_days=10, fee_bps=5,
                 slippage_bps=5, initial_capital=10000, start_date=None,
                 end_date=None) -> dict:
    """Run a chronological simulation; percent-suffixed fields use 0–100 units.

    The first possible signal needs lookback prior bars. Its next open (index
    lookback+1) starts both the strategy and benchmark sample. Earlier bars may
    warm up a selected start date, including the preceding close's signal.
    An open strategy position is valued at raw final close without inventing an
    exit. The benchmark explicitly assumes liquidation at the final close.
    """
    lookback = _number(lookback, '回看交易日', 5, 120, integer=True)
    holding_days = _number(holding_days, '持有交易日', 1, 60, integer=True)
    fee_bps = _number(fee_bps, '单边手续费 bps', 0, 100)
    slippage_bps = _number(slippage_bps, '单边滑点 bps', 0, 100)
    initial_capital = _number(initial_capital, '初始资金', 0, 1_000_000_000_000)
    if initial_capital <= 0:
        raise ValueError('初始资金必须大于 0')
    start_date, end_date = _date(start_date, '开始日期'), _date(end_date, '结束日期')
    if start_date and end_date and start_date > end_date:
        raise ValueError('开始日期不能晚于结束日期')
    data = _history(history, end_date)
    eligible = [i for i in range(lookback + 1, len(data))
                if start_date is None or data[i]['date'] >= start_date]
    if not eligible:
        raise ValueError(f'所选窗口没有可交易日；至少需要 {lookback + 2} 根有效日线（含信号与次日开盘）')
    first, last = eligible[0], eligible[-1]
    fee, slip = fee_bps / 10000, slippage_bps / 10000
    benchmark_position = _entry(initial_capital, data[first], fee, slip)
    benchmark = _exit(benchmark_position, data[last], fee, slip)
    benchmark['valuation'] = 'hypothetical_liquidation_at_last_close'
    benchmark['sessions'] = len(eligible)
    cash, position, entry_index = initial_capital, None, None
    trades, curve = [], []
    exposure, total_costs = 0, 0.0
    peak, max_drawdown = initial_capital, 0.0
    for i in eligible:
        bar = data[i]
        # The preceding session is fully known before this session's open.
        signal_index = i - 1
        if position is None:
            prior_high = max(row['high'] for row in data[signal_index - lookback:signal_index])
            if data[signal_index]['close'] > prior_high:
                position = _entry(cash, bar, fee, slip)
                position['signalDate'] = data[signal_index]['date']
                position['breakoutLevel'] = prior_high
                entry_index, cash = i, 0.0
                total_costs += position['entryFee'] + position['entrySlippage']
        if position is not None:
            exposure += 1
            if i == entry_index + holding_days - 1:
                closed = _exit(position, bar, fee, slip)
                closed['sessions'] = holding_days
                trades.append(closed)
                cash = closed['exitProceeds']
                total_costs += closed['exitFee'] + closed['exitSlippage']
                position, entry_index = None, None
        equity = cash + (position['quantity'] * bar['close'] if position else 0)
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1)
        benchmark_equity = (benchmark['exitProceeds'] if i == last
                            else benchmark_position['quantity'] * bar['close'])
        curve.append({'date': bar['date'], 'equity': equity, 'benchmark': benchmark_equity})
    open_position = None
    if position:
        market_value = position['quantity'] * data[last]['close']
        open_position = {
            **position, 'markDate': data[last]['date'], 'markPrice': data[last]['close'],
            'marketValue': market_value, 'unrealizedPnl': market_value - position['entryCapital'],
            'returnPct': (market_value / position['entryCapital'] - 1) * 100,
            'sessions': last - entry_index + 1,
            'remainingSessions': holding_days - (last - entry_index + 1),
            'totalCosts': position['entryFee'] + position['entrySlippage'],
            'valuation': 'mark_to_market_without_exit_costs',
        }
    winners = [trade['netPnl'] for trade in trades if trade['netPnl'] > 1e-8]
    losers = [trade['netPnl'] for trade in trades if trade['netPnl'] < -1e-8]
    count = len(trades)
    final = curve[-1]['equity']
    strategy_return = (final / initial_capital - 1) * 100
    benchmark_return = benchmark['returnPct']
    warnings = [
        '输入为未复权 OHLC；未建模拆股、分红及其他公司行动，跨公司行动的结果可能失真。',
        '仅为历史规则试验；样本约两年且为当前股票池，存在选择偏差，不能据少量交易判断统计显著性或未来收益。',
        '假设可买入碎股、全额投入且无融资；不含税费、汇率、市场冲击、交易暂停或真实成交限制。',
        '买入持有基准在同一窗口首个可交易日开盘买入，并于最后收盘假设清仓，使用相同双边费用及滑点。',
    ]
    if open_position:
        warnings.append('策略期末仍有未完成持仓：按收盘市值计入净值，未扣假设卖出成本，未计入胜率或平仓交易统计。')
    return {
        'parameters': {'lookback': lookback, 'holdingDays': holding_days,
                       'feeBps': fee_bps, 'slippageBps': slippage_bps,
                       'initialCapital': initial_capital, 'startDate': start_date, 'endDate': end_date},
        'sample': {'startDate': data[first]['date'], 'endDate': data[last]['date'],
                   'bars': len(eligible), 'warmupBars': first,
                   'historyStartDate': data[0]['date'], 'historyEndDate': data[-1]['date']},
        'metrics': {'totalReturnPct': strategy_return, 'benchmarkReturnPct': benchmark_return,
                    'excessReturnPct': strategy_return - benchmark_return,
                    'maxDrawdownPct': max_drawdown * 100,
                    'closedTradeCount': count, 'wins': len(winners), 'losses': len(losers),
                    'breakeven': count - len(winners) - len(losers),
                    'winRatePct': len(winners) / count * 100 if count else None,
                    'payoffRatio': ((sum(winners) / len(winners)) / abs(sum(losers) / len(losers))
                                    if winners and losers else None),
                    'profitFactor': sum(winners) / abs(sum(losers)) if losers and count else None,
                    'exposureFraction': exposure / len(eligible),
                    'finalEquity': final, 'totalCosts': total_costs},
        'curve': curve, 'trades': trades, 'openPosition': open_position,
        'benchmark': benchmark, 'warnings': warnings,
        'rules': [
            f'收盘价严格高于此前 {lookback} 个交易日最高价（不含信号当日）时触发；次一交易日开盘买入。',
            f'一次仅持一笔多头，全额投入；入场日计为第 1 天，第 {holding_days} 个交易日收盘卖出。',
            f'每侧手续费 {fee_bps:g} bps、滑点 {slippage_bps:g} bps；买价上调、卖价下调，费用按实际成交额计算。',
            '持仓期间忽略其他信号；退出当日收盘可生成下一日新信号，未到退出日的持仓不强制卖出。',
            '净值每日收盘计价，包含买入费用与滑点；平仓后含卖出费用。最大回撤包含相对初始资金的首日损失。',
            '胜率分母为全部已平仓交易（含持平）；盈亏比为平均盈利 / 平均亏损绝对值，利润因子为盈利合计 / 亏损绝对值。缺少相应样本时显示 —。',
        ],
    }
