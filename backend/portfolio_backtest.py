"""Deterministic research-only shared-cash daily portfolio simulator.

Only completed bars generate decisions. Open liquidation/trims precede buys;
intraday stops follow all morning buys. Close trailing updates apply tomorrow.
Prices are raw and no real-account or brokerage interface is imported here.
"""
from copy import deepcopy
from datetime import date
import math
from backtest import _date, _number
from strategy_rules import SYMBOLS, SEMICONDUCTORS, describe_rules, get_profile
from portfolio_factors import validate_histories, factors, signal_details, condition


MODE_LABELS = {'normal': '正常运行', 'reduced': '一级降风险（半档）',
               'paused': '二级暂停', 'recovery': '恢复观察（半档）',
               'terminated': '超过回撤目标，停止新交易'}


class _RiskState:
    """Close observations schedule state changes for the next session's open."""
    def __init__(self, target, equity):
        self.target = target
        self.global_peak = self.cycle_peak = equity
        self.mode, self.pending = 'normal', None
        self.started = 0
        self.events = []

    @property
    def multiplier(self):
        return .5 if self.mode in ('reduced', 'recovery') else 1.0

    def global_drawdown(self, equity):
        return max(0., (1-equity/self.global_peak)*100)

    def cycle_drawdown(self, equity):
        return max(0., (1-equity/self.cycle_peak)*100)

    def _schedule(self, mode, day, equity, label, threshold=None):
        self.pending = mode
        event = dict(date=day, type=mode, label=label,
                     description=f'{label}；下一交易日开盘生效', equity=equity,
                     drawdownPct=self.global_drawdown(equity) if mode == 'terminated' else self.cycle_drawdown(equity),
                     globalDrawdownPct=self.global_drawdown(equity))
        if threshold is not None:
            event['thresholdPct'] = threshold
        self.events.append(event)

    def at_open(self, index, day, equity):
        if self.pending:
            self.mode, self.pending = self.pending, None
            self.started = index
            if self.mode == 'recovery':
                self.cycle_peak = equity
            if self.mode == 'terminated':
                return 'drawdown_termination'
            if self.mode == 'paused':
                return 'risk_pause'
        return None

    def observe(self, index, day, equity, market_streak):
        self.global_peak = max(self.global_peak, equity)
        self.cycle_peak = max(self.cycle_peak, equity)
        if self.mode == 'terminated':
            return
        dd = self.cycle_drawdown(equity)
        if self.global_drawdown(equity) >= self.target-1e-10:
            self._schedule('terminated', day, equity, '超过总历史回撤目标，次日全部退出并终止', self.target)
        elif self.mode == 'paused':
            if index-self.started+1 >= 10 and market_streak >= 5:
                self._schedule('recovery', day, equity, '已暂停10个完整交易日且SPY连续5日强势，开始新一轮半档运行')
        elif dd >= self.target*2/3-1e-10:
            self._schedule('paused', day, equity, '二级暂停：次日全部退出，至少冷静10个交易日', self.target*2/3)
        elif self.mode in ('normal', 'recovery') and dd >= self.target/3-1e-10:
            self._schedule('reduced', day, equity, '一级降风险：次日现有及新仓限额减半', self.target/3)
        elif self.mode == 'reduced' and index-self.started+1 >= 5 and dd < self.target/6 and market_streak >= 5:
            self._schedule('normal', day, equity, '半档运行已满5日、回撤恢复且市场连续5日强势，恢复本档限额')
        elif self.mode == 'recovery' and index-self.started+1 >= 5 and dd < self.target/3:
            self._schedule('normal', day, equity, '恢复观察已满5日且未再触发风险阈值，恢复本档限额')


def _new_week(previous, current):
    return date.fromisoformat(previous).isocalendar()[:2] != date.fromisoformat(current).isocalendar()[:2]


def run_portfolio(histories, spy_history, template_id='breakout', profile_id='aggressive',
                  parameters=None, start_date=None, end_date=None, initial_capital=10000,
                  fee_bps=5, slippage_bps=5):
    """All percentages use 0–100 units; quantities are whole shares, long only."""
    rules = describe_rules(template_id, profile_id, parameters)
    params, profile = rules['parameters'], get_profile(profile_id)
    initial_capital = _number(initial_capital, '初始资金', 0, 1e12)
    if initial_capital <= 0:
        raise ValueError('初始资金必须大于0')
    fee = _number(fee_bps, '手续费bps', 0, 100)/10000
    slip = _number(slippage_bps, '滑点bps', 0, 100)/10000
    start_date, end_date = _date(start_date, '开始日期'), _date(end_date, '结束日期')
    if start_date and end_date and start_date > end_date:
        raise ValueError('开始日期不能晚于结束日期')
    if not isinstance(histories, dict) or any(s not in SYMBOLS for s in histories):
        raise ValueError('只支持固定研究股票池')
    data, spy = validate_histories(histories, spy_history, end_date)
    eligible = [i for i in range(200, len(spy)) if start_date is None or spy[i]['date'] >= start_date]
    if not eligible:
        raise ValueError('至少需要200根预热日线及1个交易日，且窗口内必须有行情')
    if start_date and spy[eligible[0]]['date'] != start_date and start_date <= spy[199]['date']:
        raise ValueError('开始日期之前需要200根预热日线')
    fs = {s: factors(rows) for s, rows in data.items()}
    spy_fs = factors(spy)
    symbols = sorted(data)
    cash, positions, trades, curve, decisions = initial_capital, {}, [], [], []
    exited, planned = {}, {}
    costs, turnover, cash_sum = 0., 0., 0.
    risk = _RiskState(profile['drawdownPct'], initial_capital)
    market_streak = 0
    for j in range(199, eligible[0]):
        market_streak = market_streak+1 if spy[j]['close'] > spy_fs[j]['sma200'] else 0

    def decision(day, symbol, status, code, reason, conditions):
        return dict(date=day, symbol=symbol, status=status, reasonCode=code, reason=reason,
                    conditions=deepcopy(conditions))

    def equity(index, field):
        return cash + sum(p['quantity']*data[s][index][field] for s,p in positions.items())

    def sell(symbol, quantity, reference, index, reason):
        nonlocal cash, costs, turnover
        p = positions[symbol]
        price, day = reference*(1-slip), spy[index]['date']
        commission, slippage = quantity*price*fee, quantity*(reference-price)
        proceeds = quantity*price-commission
        allocated_entry_fee = p['entryFee']*quantity/p['initialQuantity']
        p['realizedPnl'] += proceeds-quantity*p['entryPrice']-allocated_entry_fee
        p['quantity'] -= quantity
        cash += proceeds
        costs += commission+slippage
        turnover += quantity*price
        p['fills'].append(dict(date=day, side='sell', quantity=quantity, referencePrice=reference,
                               price=price, fee=commission, slippage=slippage, reason=reason))
        if p['quantity'] == 0:
            p.update(exitDate=day, exitReason=reason, unrealizedPnl=0., netPnl=p['realizedPnl'])
            trades.append(p)
            del positions[symbol]
            exited[symbol] = index
            planned.pop(symbol, None)

    def trim(index):
        """Only reduce; each step marks other holdings at raw open prices."""
        def trim_group(selected, cap, risk_measure=False):
            if not selected:
                return
            e = equity(index, 'open')
            values = {s: positions[s]['quantity']*(positions[s]['initialRiskPerShare'] if risk_measure else data[s][index]['open'])
                      for s in selected}
            total = sum(values.values())
            limit = max(0, e)*profile[cap]*risk.multiplier/100
            if total <= limit+1e-8:
                return
            ratio = max(0., limit/total)
            targets = {s: math.floor(positions[s]['quantity']*ratio) for s in selected}
            codes = {'singleCapPct':'single_cap', 'sectorCapPct':'sector_cap', 'grossCapPct':'gross_cap', 'openRiskCapPct':'open_risk_cap'}
            for s in sorted(selected):
                quantity = positions[s]['quantity']-targets[s]
                if quantity:
                    sell(s, quantity, data[s][index]['open'], index, codes[cap])
        for s in sorted(list(positions)):
            trim_group([s], 'singleCapPct')
        trim_group([s for s in positions if s in SEMICONDUCTORS], 'sectorCapPct')
        trim_group(list(positions), 'grossCapPct')
        trim_group(list(positions), 'openRiskCapPct', True)

    def observations(index):
        found = {}
        for s in symbols:
            conditions, ranked, score = signal_details(data[s], fs[s], spy, spy_fs, index, template_id, params)
            found[s] = dict(conditions=conditions, ranked=ranked, score=score)
        ranking = sorted((s for s in symbols if found[s]['ranked']), key=lambda s: (-found[s]['score'], s))
        return found, ranking

    def evaluate(index, trade_index=None, latest=False):
        """Explain every symbol, including non-signals; buys are executed separately."""
        obs, ranking = observations(index)
        # Future exchange holidays are unknown. Latest weekly signals are conditional.
        weekly = _new_week(spy[index]['date'], spy[trade_index]['date']) if trade_index is not None else None
        selected = set(ranking[:params['topK']]) if template_id == 'rotation' else set(ranking)
        results = []
        for s in sorted(symbols, key=lambda s: (-obs[s]['score'], s)):
            c = obs[s]['conditions']
            code, reason, status = 'candidate', '满足收盘条件；下一交易日开盘仍须通过跳空、现金及额度检查', 'candidate'
            effective_mode = (risk.pending or risk.mode) if latest else risk.mode
            prospective_index = trade_index if trade_index is not None else index+1
            if s in positions:
                code, reason, status = 'already_held', '已有模拟持仓，不追加买入', 'held'
                if s in planned:
                    reason = f'已安排下一交易日开盘退出：{planned[s]}'
                if effective_mode in ('paused', 'terminated'):
                    reason = '组合风控：下一交易日开盘清仓或继续暂停'
            elif effective_mode in ('paused', 'terminated'):
                code, reason, status = 'risk_pause', MODE_LABELS[effective_mode], 'blocked'
            elif not c[-1]['passed']:
                code, reason, status = 'market_filter', 'SPY未高于SMA200，暂停新买入', 'blocked'
            elif s in exited and prospective_index-exited[s] <= 5:
                code, reason, status = 'cooldown', '完全退出后需间隔5个完整交易日', 'blocked'
            elif template_id == 'rotation' and weekly is False:
                code, reason, status = 'weekly_schedule', '等待下周第一个有完整行情交易日；届时使用前一交易日排名', 'waiting'
            elif s not in selected:
                code, reason, status = ('outside_top_k', '不在本周前K名', 'rejected') if template_id == 'rotation' else ('conditions_not_met', '未满足：'+ '、'.join(x['label'] for x in c if not x['passed']), 'rejected')
            elif not all(x['passed'] for x in c):
                code, reason, status = 'conditions_not_met', '未满足：'+'、'.join(x['label'] for x in c if not x['passed']), 'rejected'
            if latest and template_id == 'rotation' and status == 'candidate':
                code, reason, status = 'weekly_conditional', '若下一交易日为新一周首个完整行情日则按此排名检查买入；否则等待下次周度评估。尚未确定未来交易日历或成交', 'conditional'
            results.append(decision(spy[index]['date'], s, status, code, reason, c))
        return results, selected, weekly

    for i in eligible:
        day, prior = spy[i]['date'], i-1
        liquidation = risk.at_open(i, day, equity(i, 'open'))
        obs, ranking = observations(prior)
        weekly = _new_week(spy[prior]['date'], day)
        top = set(ranking[:params['topK']]) if template_id == 'rotation' else set()
        # Scheduled exits and open gaps happen before any new funds are spent.
        for s in sorted(list(positions)):
            reason = liquidation or planned.get(s)
            if not reason and template_id == 'rotation' and weekly and s not in top:
                reason = 'rotation_rank'
            if not reason and data[s][i]['open'] <= positions[s]['stop']:
                reason = 'open_stop'
            if reason:
                sell(s, positions[s]['quantity'], data[s][i]['open'], i, reason)
        trim(i)
        candidates, _, _ = evaluate(prior, i)
        for record in candidates:
            s, bar, f = record['symbol'], data[record['symbol']][i], fs[record['symbol']][prior]
            if record['status'] != 'candidate':
                decisions.append(record)
                continue
            reference, signal_close = bar['open'], data[s][prior]['close']
            gap = abs(reference-signal_close)
            record['conditions'].append(condition('开盘与信号收盘差 ≤ 0.5 ATR14', gap, .5*f['atr14'], gap <= .5*f['atr14']+1e-10))
            if gap > .5*f['atr14']+1e-10:
                record.update(status='skipped', reasonCode='entry_gap', reason='开盘跳空超过0.5倍信号日ATR14，跳过')
                decisions.append(record)
                continue
            price, distance = reference*(1+slip), 2*f['atr14']
            stop = price-distance
            if stop <= 0 or distance <= 0:
                record.update(status='skipped', reasonCode='invalid_stop', reason='初始止损必须为正且ATR必须大于0')
                decisions.append(record)
                continue
            if len(positions) >= profile['maxPositions']:
                record.update(status='skipped', reasonCode='position_count', reason='已达到同时持仓数量上限')
                decisions.append(record)
                continue
            e = equity(i, 'open')
            gross = sum(p['quantity']*data[t][i]['open'] for t,p in positions.items())
            sector = sum(p['quantity']*data[t][i]['open'] for t,p in positions.items() if t in SEMICONDUCTORS)
            open_risk = sum(p['quantity']*p['initialRiskPerShare'] for p in positions.values())
            def cap(key):
                return max(0., e)*profile[key]*risk.multiplier/100
            limits = [('每笔初始风险', cap('tradeRiskPct')/distance), ('单股额度', cap('singleCapPct')/price),
                      ('总仓位额度', max(0., cap('grossCapPct')-gross)/price),
                      ('未平仓初始风险额度', max(0., cap('openRiskCapPct')-open_risk)/distance),
                      ('可用现金（含买入费）', cash/(price*(1+fee))), ('成交量参与上限', f['avgVolume20']*.001)]
            if s in SEMICONDUCTORS:
                limits.append(('半导体行业额度', max(0., cap('sectorCapPct')-sector)/price))
            limiting = min(limits, key=lambda x: x[1])
            quantity = max(0, math.floor(limiting[1]+1e-10))
            sizing = dict(equity=e, riskBudget=cap('tradeRiskPct'),
                          limits=[dict(label=label, shares=max(0, math.floor(value+1e-10))) for label,value in limits],
                          binding=limiting[0], quantity=quantity)
            record['sizing'] = sizing
            if quantity == 0:
                record.update(status='skipped', reasonCode='insufficient_capacity', reason=f'{limiting[0]}不足1股，保留现金')
                decisions.append(record)
                continue
            commission, slippage = quantity*price*fee, quantity*(price-reference)
            cash -= quantity*price+commission
            # No rounding of the ledger; only absorb sub-cent machine error.
            if cash < -1e-7:
                raise ArithmeticError('共享现金账本出现借款')
            cash = max(0., cash)
            costs += commission+slippage
            turnover += quantity*price
            positions[s] = dict(id=f'{s}-{day}', symbol=s, strategy=template_id, ruleVersion=rules['version'],
                                signalDate=spy[prior]['date'], entryDate=day, entryPrice=price,
                                initialQuantity=quantity, quantity=quantity, initialStop=stop, stop=stop,
                                initialRiskPerShare=distance, entryFee=commission, entrySlippage=slippage,
                                realizedPnl=0., unrealizedPnl=0., netPnl=0., exitDate=None, exitReason=None,
                                conditions=record['conditions'], sizing=sizing,
                                fills=[dict(date=day, side='buy', quantity=quantity, referencePrice=reference,
                                            price=price, fee=commission, slippage=slippage, reason='entry')],
                                stopHistory=[], _entryIndex=i, _highestClose=0.)
            record.update(status='entered', reasonCode='entry', reason=f'按次日开盘模型买入{quantity}股；最限制条件：{limiting[0]}', executionDate=day)
            decisions.append(record)
        # Intraday proceeds are unavailable to the completed morning buy pass.
        for s in sorted(list(positions)):
            p, bar = positions[s], data[s][i]
            if bar['open'] <= p['stop']:
                # A very small ATR plus entry slippage can put a new stop above
                # the raw open; the same gap convention still applies.
                sell(s, p['quantity'], bar['open'], i, 'open_stop')
            elif bar['low'] <= p['stop']:
                sell(s, p['quantity'], p['stop'], i, 'intraday_stop')
        # Closing marks and trailing stops never affect same-day executions.
        for s,p in positions.items():
            close, f = data[s][i]['close'], fs[s][i]
            p['_highestClose'] = max(p['_highestClose'], close)
            new_stop = max(p['stop'], p['_highestClose']-3*f['atr14'])
            if new_stop > p['stop']:
                p['stopHistory'].append(dict(date=day, previous=p['stop'], stop=new_stop,
                                              reason='收盘最高价减3ATR，下一交易日起生效'))
                p['stop'] = new_stop
            average = f['sma20'] if template_id == 'breakout' else f['sma50']
            if close < average:
                planned[s] = 'trend_exit'
            elif i-p['_entryIndex']+1 >= params['holdingDays']:
                planned[s] = 'holding_limit'
        e = equity(i, 'close')
        market_was_strong = market_streak > 0
        market_streak = market_streak+1 if spy[i]['close'] > spy_fs[i]['sma200'] else 0
        risk.observe(i, day, e, market_streak)
        if market_was_strong != (market_streak > 0):
            strong = market_streak > 0
            risk.events.append(dict(date=day, type='market_resume' if strong else 'market_pause',
                                    label='市场恢复开仓条件' if strong else '市场转弱暂停新买入',
                                    description='SPY收盘重新高于SMA200；次日仍须通过组合风控及个股条件' if strong else 'SPY收盘不高于SMA200；次日暂停新买入，持仓继续执行个股退出',
                                    equity=e, drawdownPct=risk.cycle_drawdown(e)))
        gross = e-cash
        cash_sum += cash/e*100 if e else 100
        curve.append(dict(date=day, equity=e, cash=cash, grossExposure=gross,
                          drawdownPct=risk.global_drawdown(e)))
    latest, _, _ = evaluate(eligible[-1], latest=True)
    last = eligible[-1]
    for s,p in positions.items():
        remaining_fee = p['entryFee']*p['quantity']/p['initialQuantity']
        p['unrealizedPnl'] = p['quantity']*(data[s][last]['close']-p['entryPrice'])-remaining_fee
        p['netPnl'] = p['realizedPnl']+p['unrealizedPnl']
        p.update(markDate=spy[last]['date'], markPrice=data[s][last]['close'],
                 marketValue=p['quantity']*data[s][last]['close'], plannedExit=planned.get(s),
                 valuation='mark_to_market_without_exit_costs')
    for p in trades+list(positions.values()):
        p.pop('_entryIndex', None)
        p.pop('_highestClose', None)
    winners = [p['netPnl'] for p in trades if p['netPnl'] > 1e-8]
    losers = [p['netPnl'] for p in trades if p['netPnl'] < -1e-8]
    final, count = curve[-1]['equity'], len(trades)
    metrics = dict(startDate=curve[0]['date'], endDate=curve[-1]['date'], sessions=len(curve),
                   totalReturnPct=(final/initial_capital-1)*100,
                   annualReturnPct=((final/initial_capital)**(252/len(curve))-1)*100,
                   maxDrawdownPct=max(r['drawdownPct'] for r in curve), finalEquity=final,
                   closedTradeCount=count, winRatePct=len(winners)/count*100 if count else None,
                   payoffRatio=(sum(winners)/len(winners))/abs(sum(losers)/len(losers)) if winners and losers else None,
                   profitFactor=sum(winners)/abs(sum(losers)) if losers else None,
                   totalCosts=costs, averageCashPct=cash_sum/len(curve),
                   turnoverPct=turnover/initial_capital*100, riskEventCount=len(risk.events),
                   openPositionCount=len(positions))
    return dict(metrics=metrics, curve=curve, trades=trades, openPositions=list(positions.values()),
                events=risk.events, decisions=decisions, latestSignals=latest,
                state=dict(mode=risk.mode, label=MODE_LABELS[risk.mode] + ('；市场转弱暂停新买入' if not market_streak else ''), equity=final, cash=cash,
                           cycleDrawdownPct=risk.cycle_drawdown(final), globalDrawdownPct=risk.global_drawdown(final),
                           nextMode=risk.pending, marketAllowsEntry=market_streak > 0,
                           riskMultiplier=risk.multiplier, cyclePeak=risk.cycle_peak, globalPeak=risk.global_peak))
