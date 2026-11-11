"""Train-only parameter selection for a transparent, long-only paper strategy.

This is a retrospective study of today's watchlist, not a promise or a live
execution system. A later historical block is evaluated only after selection.
"""
import math

from backtest import run_backtest

SYMBOLS=['NVDA','MRVL','AVGO','MU','GOOG']
LOOKBACKS=(20,40,60)
HOLDING_DAYS=(10,20,40)
EXPOSURES=(.25,.5,.75,1.0)
CAPITAL=10000.0
FEE_BPS=5
SLIPPAGE_BPS=5


def _target(value,name,minimum,maximum):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not minimum<=value<=maximum:
        raise ValueError(f'{name}需要 {minimum}–{maximum} 范围内的有限数值')
    return float(value)


def _curve_metrics(curve):
    if not curve:raise ValueError('缺少可比较的日净值')
    values=[row['equity'] for row in curve]
    if any(not math.isfinite(v) or v<=0 for v in values):raise ValueError('日净值包含无效值')
    peak=CAPITAL;drawdown=0.0
    for value in values:
        peak=max(peak,value)
        drawdown=max(drawdown,1-value/peak)
    ratio=values[-1]/CAPITAL
    annual=(ratio**(252/len(values))-1)*100
    if not math.isfinite(annual):raise ValueError('样本年化收益无法有效计算')
    return {'startDate':curve[0]['date'],'endDate':curve[-1]['date'],'sessions':len(curve),
            'totalReturnPct':(ratio-1)*100,'annualReturnPct':annual,
            'maxDrawdownPct':drawdown*100,'finalEquity':values[-1]}


def _aggregate(results,exposure):
    if not results:raise ValueError('没有可汇总的股票回测')
    dates=[row['date'] for row in results[0]['curve']]
    if any([row['date'] for row in result['curve']]!=dates for result in results):
        raise ValueError('股票日净值日期不一致，不能静默补齐或平均')
    weight=exposure/len(results)
    curve=[]
    for i,day in enumerate(dates):
        equity=CAPITAL*(1-exposure)+sum(result['curve'][i]['equity']*weight for result in results)
        benchmark=sum(result['curve'][i]['benchmark'] for result in results)/len(results)
        curve.append({'date':day,'equity':equity,'benchmark':benchmark})
    pnl=[trade['netPnl']*weight for result in results for trade in result['trades']]
    winners=[v for v in pnl if v>1e-8];losers=[v for v in pnl if v< -1e-8]
    metrics=_curve_metrics(curve)
    metrics.update({'closedTradeCount':len(pnl),'tradeCount':len(pnl),'wins':len(winners),'losses':len(losers),
                    'winRatePct':len(winners)/len(pnl)*100 if pnl else None,
                    'payoffRatio':(sum(winners)/len(winners))/abs(sum(losers)/len(losers)) if winners and losers else None,
                    'profitFactor':sum(winners)/abs(sum(losers)) if losers else None,
                    'totalCosts':sum(result['metrics']['totalCosts']*weight for result in results),
                    'openPositionCount':sum(result['openPosition'] is not None for result in results)})
    return {'curve':curve,'metrics':metrics}


def _prepare(stocks):
    if not isinstance(stocks,list):raise ValueError('股票历史格式不正确')
    by_symbol={stock.get('symbol'):stock for stock in stocks if isinstance(stock,dict)}
    if any(symbol not in by_symbol for symbol in SYMBOLS):
        raise ValueError('需要 NVDA、MRVL、AVGO、MU、GOOG 五只股票的完整历史')
    histories={symbol:by_symbol[symbol].get('history') for symbol in SYMBOLS}
    if any(not isinstance(rows,list) or len(rows)<350 for rows in histories.values()):
        raise ValueError('五只研究股票各需至少 350 根完整日线，当前样本不足')
    try:
        start=max(rows[0]['date'] for rows in histories.values())
        end=min(rows[-1]['date'] for rows in histories.values())
        histories={symbol:[row for row in rows if start<=row['date']<=end] for symbol,rows in histories.items()}
        dates=[row['date'] for row in histories[SYMBOLS[0]]]
        if len(dates)<350 or any([row['date'] for row in rows]!=dates for rows in histories.values()):
            raise ValueError('五只股票需至少 350 根日期一致的日线；缺失交易日不会补造')
    except (TypeError,KeyError,IndexError):
        raise ValueError('股票日线缺少有效日期') from None
    excluded=[{'symbol':symbol,'bars':len(by_symbol.get(symbol,{}).get('history') or []),
               'reason':'当前上市历史较短，未纳入长期训练与后段验证；待形成足够历史后单独研究。'}
              for symbol in ('SPCX','SKHY') if symbol in by_symbol]
    return histories,dates,excluded


def _runs(histories,lookback,holding,start,end,fee=FEE_BPS,slippage=SLIPPAGE_BPS):
    return [run_backtest(histories[symbol],lookback=lookback,holding_days=holding,
                         initial_capital=CAPITAL,fee_bps=fee,slippage_bps=slippage,
                         start_date=start,end_date=end) for symbol in SYMBOLS]


def _meets(metrics,annual,drawdown):
    return metrics['annualReturnPct']>=annual and metrics['maxDrawdownPct']<=drawdown


def _rank(candidate,annual,drawdown):
    metrics=candidate['metrics'];ret=metrics['annualReturnPct'];dd=metrics['maxDrawdownPct']
    # Training drawdown is a selection constraint, not an enforceable future cap.
    if _meets(metrics,annual,drawdown):return (0,dd,-ret)
    if dd<=drawdown:return (1,max(0,annual-ret),dd)
    return (2,dd-drawdown,max(0,annual-ret))


def _with_symbols(aggregate,results):
    return {**aggregate['metrics'],'perSymbol':[
        {'symbol':symbol,'totalReturnPct':result['metrics']['totalReturnPct'],
         'benchmarkReturnPct':result['metrics']['benchmarkReturnPct'],
         'closedTradeCount':result['metrics']['closedTradeCount'],'winRatePct':result['metrics']['winRatePct']}
        for symbol,result in zip(SYMBOLS,results)]}


def build_strategy_study(stocks:list,target_return_pct=50,max_drawdown_pct=30)->dict:
    annual=_target(target_return_pct,'目标年化收益',0,200)
    drawdown=_target(max_drawdown_pct,'最大回撤目标',1,80)
    histories,dates,excluded=_prepare(stocks)
    # All parameter candidates start after the same 60 prior bars + signal bar.
    tradable=dates[61:];split=int(len(tradable)*.7)
    train_start,train_end=tradable[0],tradable[split-1]
    validation_start,validation_end=tradable[split],tradable[-1]
    candidates=[];training_runs={}
    for lookback in LOOKBACKS:
        for holding in HOLDING_DAYS:
            results=_runs(histories,lookback,holding,train_start,train_end)
            training_runs[(lookback,holding)]=results
            for exposure in EXPOSURES:
                aggregate=_aggregate(results,exposure)
                candidates.append({'lookback':lookback,'holdingDays':holding,'exposure':exposure,
                                   'metrics':aggregate['metrics']})
    chosen=min(candidates,key=lambda candidate:_rank(candidate,annual,drawdown))
    lookback,holding,exposure=chosen['lookback'],chosen['holdingDays'],chosen['exposure']
    train_results=training_runs[(lookback,holding)]
    training=_with_symbols(_aggregate(train_results,exposure),train_results)
    training['meetsTargets']=_meets(training,annual,drawdown)
    # Validation data is first passed to the engine after selection is complete.
    validation_results=_runs(histories,lookback,holding,validation_start,validation_end)
    aggregate=_aggregate(validation_results,exposure)
    validation=_with_symbols(aggregate,validation_results)
    validation['meetsTargets']=_meets(validation,annual,drawdown)
    benchmark=_curve_metrics([{'date':row['date'],'equity':row['benchmark']} for row in aggregate['curve']])
    benchmark['label']='五只股票等额买入持有（100% 初始投入）'
    benchmark['exposurePct']=100
    default_results=_runs(histories,20,10,validation_start,validation_end)
    baseline=_with_symbols(_aggregate(default_results,1),default_results)
    baseline['parameters']={'lookback':20,'holdingDays':10,'exposurePct':100}
    stress_results=_runs(histories,lookback,holding,validation_start,validation_end,10,10)
    cost_stress={**_aggregate(stress_results,exposure)['metrics'],'feeBps':10,'slippageBps':10}
    if not training['meetsTargets']:
        conclusion='训练段未找到同时达到收益与回撤目标的候选。展示较接近目标的研究参数，不应据此启动实盘。'
    elif not validation['meetsTargets']:
        conclusion='训练段达标，但后段历史验证未同时达到目标；当前方案未通过验证，只适合继续研究。'
    else:
        conclusion='训练和后段历史验证均达到设定目标，但样本较短且已被观察；仅列为模拟观察候选，不能保证未来盈利。'
    warnings=[
        '收益目标和回撤目标是历史筛选条件，不是盈利承诺，也不是实盘损失上限。年化按 252 个交易日换算，短窗口年化可能夸大可持续收益。',
        '先固定 36 组参数，仅在前 70% 可交易日选择；后 30% 只验证选定参数。用户反复调整目标、观察结果会污染验证段；它不是封存的、全新样本外证据。',
        '训练、验证分别从 10,000 美元现金开始，验证不承接训练持仓；窗口之前的历史仅供指标预热。',
        '采用当前五只科技股，存在股票池选择及幸存者偏差，约两年样本无法覆盖完整市场周期；SPCX、SKHY 历史短，排除于主研究。',
        '价格为未复权 OHLC，未建模拆股、分红、税费、停牌或市场冲击；跨公司行动的结果可能失真。',
        '资金比例指初始模拟本金中投入五个等额独立子账户的比例，其余现金不计息；子账户内部复利、互不调拨，不进行持续再平衡，持仓权重可漂移。',
        '每侧手续费及滑点各 5 bps；没有融资、做空或实盘订单。胜率按已完成交易统计；不同股票同期交易并非独立样本。',
        '买入持有基准为五股等额、100% 初始投入，在相同窗口最后收盘假设清仓；策略未到持有期末的头寸仅按收盘估值，未扣假设平仓成本。',
        '每股收益表展示各子账户全额使用自身资金的收益；总组合收益另计现金保留比例，不能把每股回撤简单平均。',
    ]
    if validation['closedTradeCount']<30:warnings.append('后段验证平仓交易少于 30 笔，胜率和盈亏比的统计不确定性很高。')
    return {'status':'research_only','asOf':validation_end,
            'targets':{'annualReturnPct':annual,'maxDrawdownPct':drawdown},
            'parameters':{'lookback':lookback,'holdingDays':holding,'exposurePct':exposure*100,'symbols':SYMBOLS[:],
                          'feeBps':FEE_BPS,'slippageBps':SLIPPAGE_BPS},
            'training':training,'validation':validation,'benchmark':benchmark,'baseline':baseline,'costStress':cost_stress,
            'equityCurve':[{'date':row['date'],'strategy':row['equity'],'benchmark':row['benchmark']} for row in aggregate['curve']],
            'candidateCount':len(candidates),'trainingQualifiedCount':sum(_meets(c['metrics'],annual,drawdown) for c in candidates),
            'selectionRule':'仅用训练段：优先同时达标且回撤较低；否则优先回撤不超目标且收益最接近目标；均超回撤时选择超出最少者。',
            'sample':{'historyStartDate':dates[0],'historyEndDate':dates[-1],'historyBars':len(dates),'warmupBars':61,
                      'trainingFraction':.7,'validationFraction':.3,'validationType':'retrospective_chronological_split_not_pristine_oos'},
            'rules':[f'收盘突破此前 {lookback} 个交易日最高价，次日开盘买入。',
                     f'每只股票独立持有 {holding} 个交易日（入场日为第 1 天），到期收盘退出；不重叠加仓。',
                     f'初始模拟资金 {exposure*100:g}% 等分为五个子账户，其余留现金，无融资。',
                     '每日只使用已完成且完整的日线；成交计入双边费用和滑点。'],
            'excluded':excluded,'warnings':warnings,'conclusion':conclusion,
            'sources':[{'title':'SEC Investor Bulletin: Performance Claims',
                        'url':'https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-47',
                        'purpose':'回测是假设结果，历史表现不能证明未来可实现收益。'}]}
