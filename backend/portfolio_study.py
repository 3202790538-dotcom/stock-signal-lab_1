"""Chronological, train-only research for complete portfolio trading rules."""
import math
from backtest import _history
from strategy_rules import SYMBOLS, VERSION, get_profile, candidate_parameters, describe_rules

CAPITAL = 10000.0


def _target(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 200:
        raise ValueError('收益目标需要 0–200 范围内的有限数值')
    return float(value)


def prepare_history(stocks, spy_history):
    if not isinstance(stocks, list) or not isinstance(spy_history, list) or len(spy_history) < 350:
        raise ValueError('需要至少 350 根 SPY 历史日线，请先刷新行情')
    by_symbol = {s.get('symbol'): s for s in stocks if isinstance(s, dict)}
    histories = {}
    for symbol in SYMBOLS + ['SPY']:
        source = spy_history if symbol == 'SPY' else by_symbol.get(symbol, {}).get('history')
        if not isinstance(source, list) or len(source) < 350:
            raise ValueError(f'{symbol} 需要至少 350 根完整历史日线')
        validated = _history(source, None)
        rows = []
        for row, original in zip(validated, source):
            volume = original.get('volume')
            if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume < 0:
                raise ValueError(f"{symbol} {row['date']} 成交量缺失或无效")
            rows.append({**row, 'volume': volume})
        histories[symbol] = rows
    if len({rows[-1]['date'] for rows in histories.values()}) != 1:
        raise ValueError('五只股票与 SPY 的最新交易日不一致，请刷新完整行情；不会静默截掉更新的日期')
    start = max(rows[0]['date'] for rows in histories.values())
    end = min(rows[-1]['date'] for rows in histories.values())
    histories = {s: [r for r in rows if start <= r['date'] <= end] for s, rows in histories.items()}
    dates = [r['date'] for r in histories['SPY']]
    if len(dates) < 350 or any([r['date'] for r in rows] != dates for rows in histories.values()):
        raise ValueError('五只股票与 SPY 需至少 350 根日期一致的日线，缺失日期不会补造')
    excluded = [{'symbol': s, 'bars': len(by_symbol.get(s, {}).get('history') or []),
                 'reason': '历史不足，未纳入固定五股主研究；不混用较早同名代码历史。'}
                for s in ('SPCX', 'SKHY') if s in by_symbol]
    return {s: histories[s] for s in SYMBOLS}, histories['SPY'], dates, excluded


def rank_candidate(metrics, annual, drawdown):
    ret, dd = metrics['annualReturnPct'], metrics['maxDrawdownPct']
    if dd <= drawdown and ret >= annual: return (0, dd, -ret)
    if dd <= drawdown: return (1, max(0, annual-ret), dd)
    return (2, dd-drawdown, max(0, annual-ret))


def _meets(metrics, annual, dd):
    return metrics['annualReturnPct'] >= annual and metrics['maxDrawdownPct'] <= dd


def curve_metrics(curve, initial=CAPITAL):
    peak = initial
    dd = 0.0
    for row in curve:
        peak = max(peak, row['equity'])
        dd = max(dd, (peak-row['equity'])/peak)
    ratio = curve[-1]['equity']/initial
    return {'startDate': curve[0]['date'], 'endDate': curve[-1]['date'], 'sessions': len(curve),
            'totalReturnPct': (ratio-1)*100, 'annualReturnPct': (ratio**(252/len(curve))-1)*100,
            'maxDrawdownPct': dd*100, 'finalEquity': curve[-1]['equity']}


def buy_hold(histories, start, end, label):
    rows = [[r for r in h if start <= r['date'] <= end] for h in histories]
    budget = CAPITAL/len(rows)
    fee, slip = .0005, .0005
    quantities = [budget/(r[0]['open']*(1+slip)*(1+fee)) for r in rows]
    costs = sum(q*r[0]['open']*(slip + (1+slip)*fee) for q, r in zip(quantities, rows))
    curve = [{'date': row['date'], 'equity': sum(q*r[i]['close'] for q, r in zip(quantities, rows))}
             for i, row in enumerate(rows[0])]
    metrics = {**curve_metrics(curve), 'label': label, 'closedTradeCount': 0,
               'winRatePct': None, 'payoffRatio': None, 'profitFactor': None, 'totalCosts': costs,
               'averageCashPct': 0, 'turnoverPct': 100/(1+fee), 'riskEventCount': 0,
               'openPositionCount': len(rows), 'valuation': 'mark_to_market_without_exit_costs'}
    return metrics, curve


def build_portfolio_study(stocks, spy_history, template_id='breakout', risk_profile='aggressive', target_return_pct=50):
    annual = _target(target_return_pct)
    profile = get_profile(risk_profile)
    parameters = candidate_parameters(template_id)
    histories, spy, dates, excluded = prepare_history(stocks, spy_history)
    from portfolio_backtest import run_portfolio
    tradable = dates[200:]
    split = int(len(tradable)*.7)
    train_start, train_end = tradable[0], tradable[split-1]
    valid_start, valid_end = tradable[split], tradable[-1]
    # Do not even pass validation prices into the training runs.
    train_histories = {s: [r for r in rows if r['date'] <= train_end] for s, rows in histories.items()}
    train_spy = [r for r in spy if r['date'] <= train_end]
    runs = []
    for params in parameters:
        run = run_portfolio(train_histories, train_spy, template_id=template_id, profile_id=risk_profile,
                            parameters=params, start_date=train_start, end_date=train_end)
        runs.append((params, run))
    selected, training_run = min(runs, key=lambda item: rank_candidate(item[1]['metrics'], annual, profile['drawdownPct']))
    def simulate(start, fee=5, slip=5):
        return run_portfolio(histories, spy, template_id=template_id, profile_id=risk_profile,
                             parameters=selected, start_date=start, end_date=valid_end, fee_bps=fee, slippage_bps=slip)
    validation_run = simulate(valid_start)
    stress = simulate(valid_start, 10, 10)
    simulation = simulate(train_start)
    training = {**training_run['metrics'], 'meetsTargets': _meets(training_run['metrics'], annual, profile['drawdownPct'])}
    validation = {**validation_run['metrics'], 'meetsTargets': _meets(validation_run['metrics'], annual, profile['drawdownPct'])}
    spy_metrics, spy_curve = buy_hold([spy], valid_start, valid_end, 'SPY 全额买入持有')
    basket_metrics, basket_curve = buy_hold(list(histories.values()), valid_start, valid_end, '五股等额买入持有')
    if [r['date'] for r in validation_run['curve']] != [r['date'] for r in spy_curve]:
        raise ValueError('策略和基准日期不一致，不能比较')
    warnings = [
        '原始价格探索回测，未处理拆股和分红；不具备实盘就绪认证。',
        '年化收益目标不是承诺，回撤规则也不是保证损失上限；跳空和延迟成交会放大亏损。',
        '前 200 根日线预热，前 70% 可交易日训练、后 30% 验证。反复查看并修改目标会污染验证段，它不是封存样本外结果。',
        '固定当前五只股票，存在股票池选择和幸存者偏差；约两年原始行情覆盖不足完整市场周期。',
        '开盘后检查并以开盘附近成交为日线近似，尚未验证分钟级撮合、排队、停牌和真实流动性。',
        '双边手续费和滑点各 5 bps，现金不计息，未计税及最低佣金；所有模拟从独立的 $10,000 现金开始。',
        '基准全额投入并允许分数股；策略整数股且受仓位/风险限制。两者统一期末估值，均不扣未发生的平仓费。',
        '当前模拟状态使用选定参数回放完整区间（包含训练段），不能当作验证成绩，也不是实际 IBKR 持仓。',
    ]
    if validation['closedTradeCount'] < 30:
        warnings.append('验证期完成交易少于 30 笔，胜率和盈亏比的样本不充分。')
    if not training['meetsTargets']:
        conclusion = '训练段没有候选同时达到收益和回撤目标；展示规则下较接近的候选，当前策略未达标。'
    elif not validation['meetsTargets']:
        conclusion = '训练段达标，后段历史验证未同时达标。新增完整规则未通过目标验证，不能沿用旧策略收益。'
    else:
        conclusion = '训练和后段历史验证达到设定目标，仅为模拟观察候选；原始行情、短样本与成交近似仍不支持盈利保证。'
    return {'status': 'research_only', 'version': VERSION, 'asOf': valid_end, 'templateId': template_id,
            'riskProfile': risk_profile, 'targets': {'annualReturnPct': annual, 'maxDrawdownPct': profile['drawdownPct']},
            'parameters': selected, 'rules': describe_rules(template_id, risk_profile, selected),
            'candidateCount': len(runs), 'trainingQualifiedCount': sum(_meets(r['metrics'], annual, profile['drawdownPct']) for _, r in runs),
            'training': training, 'validation': validation, 'benchmarks': {'spy': spy_metrics, 'basket': basket_metrics},
            'costStress': stress['metrics'], 'equityCurve': [
                {'date': r['date'], 'strategy': r['equity'], 'spy': spy_curve[i]['equity'], 'basket': basket_curve[i]['equity']}
                for i, r in enumerate(validation_run['curve'])],
            'validationDetails': {k: validation_run[k] for k in ('trades', 'openPositions', 'events', 'decisions', 'state')},
            'simulation': simulation, 'excluded': excluded, 'warnings': warnings, 'conclusion': conclusion,
            'sample': {'historyStartDate': dates[0], 'historyEndDate': dates[-1], 'historyBars': len(dates),
                       'warmupBars': 200, 'trainingFraction': .7, 'validationFraction': .3,
                       'validationType': 'retrospective_chronological_split_not_pristine_oos'}}
