"""Pure, daily-only personal planning. No account I/O and no order execution.

The ledger reserves existing positions and proposed buys together. Hypothetical
sales never release capacity. Manual stops describe a user's plan, not orders.
Legacy fixed-universe simulations are intentionally not called by this module.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, time, timezone
import math
import re
from zoneinfo import ZoneInfo

from backtest import _history
from holdings_plan import _num, _stamp, _fresh, _latest_weekday, _positions, _contract, _symbol
from portfolio_factors import factors


VERSION = 'personal-long-term-v1'
DEFAULTS = dict(accountSource='broker', allocationPct=100, targetReturnPct=50,
                maxDrawdownPct=30, templateId='long_term', allowProfitAdd=True, reserveCashPct=0)
SEMICONDUCTORS = frozenset(('NVDA', 'MRVL', 'AVGO', 'MU', 'SKHY', 'SKUU'))
LABELS = {'verify': '资料待核实', 'hold': '按条件持有', 'reduce': '优先减仓复核',
          'exit': '退出条件触发', 'candidate': '条件候选', 'conditional': '有条件计划',
          'blocked': '暂停新增风险', 'wait': '等待信号'}
TIMING = '完整日线收盘后评估；下一常规交易时段人工复核，价格超出区间则取消。非实时行情，不自动下单。'
FEE = .001  # A planning allowance; not a claim about the user's broker tariff.


def _settings(value):
    if not isinstance(value, dict) or set(value)-set(DEFAULTS):
        raise ValueError('计划设置包含未知字段或不是对象')
    result = {**DEFAULTS, **value}
    for name, lower, upper in [('allocationPct', 0, 100), ('reserveCashPct', 0, 100),
                               ('targetReturnPct', 0, 200), ('maxDrawdownPct', 1, 80)]:
        number = _num(result[name])
        if number is None or not lower <= number <= upper:
            raise ValueError(f'{name} 必须为 {lower}–{upper} 的有限数值')
        result[name] = number
    if result['accountSource'] not in ('broker', 'manual'):
        raise ValueError('未知账户来源')
    if result['templateId'] not in ('long_term', 'pullback', 'rotation'):
        raise ValueError('未知长线策略')
    if not isinstance(result['allowProfitAdd'], bool):
        raise ValueError('盈利加仓开关必须为布尔值')
    return result


def _check(label, passed, observed=None, required=None):
    return dict(label=label, passed=passed, observed=observed, required=required)


def _daily(quote, cutoff, expected, failures):
    reasons, rows, fs = [], [], []
    if not quote:
        return rows, fs, ['缺少该证券完整日线；核对行情代码并刷新。']
    if quote.get('quoteStatus', quote.get('quote_status', 'ready')) not in ('ready', 'available'):
        reasons.append('该证券报价过时或不可用；需刷新日线。')
    if _symbol(quote) in failures:
        reasons.append('该证券行情刷新失败或有数据警告。')
    try:
        rows = _history(quote.get('history'), cutoff)
        original = [r for r in quote['history'] if r['date'] <= cutoff]
        for row, raw in zip(rows, original):
            volume = _num(raw.get('volume'))
            if volume is None or volume < 0:
                raise ValueError('成交量缺失或无效')
            row['volume'] = volume
        if len(rows) < 200:
            reasons.append(f'当前只有 {len(rows)} 根完整日线，策略要求至少 200 根；不推算上市前历史。')
        if not rows or rows[-1]['date'] != expected:
            reasons.append('缺少最近已完成工作日日线；节假日需核对交易所日历。')
        if rows and quote.get('asOf') and quote['asOf'] != rows[-1]['date']:
            reasons.append('报价日期与最后完整日线日期不一致。')
        fs = factors(rows) if len(rows) >= 200 else []
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        rows, fs = [], []
        reasons.append('OHLC、日期或成交量无效；不能生成条件价位。')
    return rows, fs, reasons


def _sector(position, quote, symbol):
    explicit = position.get('sector') if position else None
    if explicit in ('semiconductor', 'other'):
        return explicit
    # A fund's issuer classification is not a look-through exposure calculation.
    if quote.get('quoteType') == 'ETF' or quote.get('securityKind') == 'fund':
        return 'unknown'
    supplied_class = quote.get('sectorClass')
    evidence = quote.get('metadataEvidence')
    if (supplied_class in ('semiconductor', 'other') and isinstance(evidence, dict)
            and evidence.get('source') and evidence.get('url') and evidence.get('checkedAt')):
        return supplied_class
    # An unselected manual field may use sourced metadata, not an unsourced hint.
    if explicit == 'unknown':
        return 'unknown'
    if symbol in SEMICONDUCTORS:
        return 'semiconductor'
    supplied = quote.get('sector')
    if supplied in ('semiconductor', 'other', 'unknown'):
        return supplied
    industry = quote.get('industry')
    if isinstance(industry, str) and industry.strip():
        return 'semiconductor' if 'semiconductor' in industry.lower() else 'other'
    # These legacy ordinary-stock identities have a known non-semiconductor role.
    return 'other' if symbol in ('GOOG', 'GOOGL', 'IBKR') else 'unknown'


def _unsupported_reason(quote, position, listing):
    """Known strategy exclusions differ from unresolved security identity."""
    if listing.get('supported') is False and listing.get('status') == 'unsupported' and listing.get('reason'):
        return str(listing['reason'])+' 当前普通股策略不适用，保留该合约记录。'
    if quote.get('securityKind') == 'shell':
        return '公开注册资料归类为壳公司/SPAC，当前经营性普通股策略不适用；保留研究记录。'
    kind = quote.get('quoteType', quote.get('instrumentType'))
    if kind == 'ETF' or quote.get('securityKind') == 'fund':
        name = 'ETF' if kind == 'ETF' else '基金'
        return f'已核实为{name}，当前普通股策略不适用；保留研究与持仓估值，不生成该策略的买入股数。'
    for record in (position or {}, listing, quote):
        currency = record.get('currency')
        if isinstance(currency, str) and currency and currency != 'USD':
            return f'币种为{currency}，当前策略仅支持USD普通股；不混用美元价格和风险预算。'
    quantity = _num(position.get('position')) if position else None
    if quantity is not None and quantity <= 0:
        return '当前策略仅管理普通股多头；该持仓方向不适用，仍保留全账户风险限制。'
    return None


def _rules(template):
    entries = {
        'long_term': ['收盘 > SMA50 > SMA200；严格突破此前 60 个交易日最高价（不含当日）。',
                      '成交量 ≥ 前 20 日均量的 1.2 倍；126 日收益高于 SPY。'],
        'pullback': ['收盘 > SMA50 > SMA200；最近 10 日曾触及各自 SMA50，且各日收盘均高于 SMA200。',
                     '最新收盘重新站上 SMA50 且高于前日最高价；成交量 ≥ 前 20 日均量；126 日收益高于 SPY。'],
        'rotation': ['每月首个常规交易时段，以前一已完成日线对全部可核实候选按 126 日收益排名，取前 3 名。',
                     '收盘 > SMA50 > SMA200、126 日收益为正；同分按代码及合约排序。完整股票池或月度日期未确认时不分配新仓。'],
    }
    return dict(label={'long_term': '长线趋势突破', 'pullback': '长线趋势回调', 'rotation': '月度动量轮动'}[template],
                entry=['仅 USD 普通股多头；SPY 收盘 > SMA200；股价 ≥ $10，前 20 日平均成交额 ≥ $2,000 万。', *entries[template]],
                exit=['连续两个完整收盘均低于各自 SMA50，下一常规时段复核退出；用户计划止损被触及时优先复核。',
                      '每持有 120 个交易日复核长期逻辑；未知入场日期不推算已经持有多久。',
                      '月度轮动只在完整月度排名确认后复核落榜退出；缺数据不会被当成落榜。'],
                sizing=['每笔风险比例 = min(0.75%, 最大回撤容忍度 / 40)，未平仓计划止损风险上限 = min(3%, 容忍度 / 10)。',
                        '风险、单股 25%、半导体 60% 以分配资金为基数；全部既有持仓仍占用容量。总名义仓位不超过分配资金及账户净值。',
                        '买入区间为信号收盘 ± 0.5ATR14，拟议初始止损为信号收盘 − 2ATR14；按上沿至止损的 2.5ATR 风险取整。',
                        '单次量不超过前 20 日均量的 0.1%；预留成交金额 0.1% 作为费用估计，实际费用需另核实。',
                        '按排名依次预留共享资金和风险；先复核减仓，拟议卖出所得不能提前用于买入。'], timing=TIMING)


def _signal(rows, fs, spy, spy_fs, template):
    if template == 'pullback' and len(rows) < 209:
        return [], False, 0., ['长线回调需要至少209根日线，最近10个观察日的SMA200必须全部完成预热。']
    bar, f = rows[-1], fs[-1]
    spy_by_date = {r['date']: i for i, r in enumerate(spy)}
    start = max(rows[0]['date'], spy[0]['date'])
    aligned = [r['date'] for r in rows if r['date'] >= start] == [r['date'] for r in spy if r['date'] >= start]
    if not aligned or len([r for r in rows if r['date'] >= start]) < 200:
        return [], False, 0., ['股票与 SPY 共同区间不足 200 根或交易日期不一致。']
    sf = spy_fs[spy_by_date[bar['date']]]
    checks = [_check('收盘价 ≥ $10', bar['close'] >= 10, bar['close'], 10),
              _check('前20日平均成交额 ≥ $2,000万', f['avgDollar20'] >= 20_000_000, f['avgDollar20'], 20_000_000),
              _check('收盘 > SMA50 > SMA200', bar['close'] > f['sma50'] > f['sma200'],
                     {'close': bar['close'], 'sma50': f['sma50'], 'sma200': f['sma200']}, '严格高于'),
              _check('SPY收盘 > SMA200', spy[-1]['close'] > sf['sma200'], spy[-1]['close'], sf['sma200'])]
    score = f['return126']-sf['return126']
    if template == 'long_term':
        high = max(r['high'] for r in rows[-61:-1])
        checks += [_check('收盘突破此前60日最高价', bar['close'] > high, bar['close'], high),
                   _check('成交量 ≥ 前20日均量 × 1.2', bar['volume'] >= f['avgVolume20']*1.2,
                          bar['volume'], f['avgVolume20']*1.2),
                   _check('126日收益 > SPY', score > 0, f['return126'], sf['return126'])]
    elif template == 'pullback':
        touched = any(rows[i]['low'] <= fs[i]['sma50'] for i in range(len(rows)-10, len(rows)))
        intact = all(rows[i]['close'] > fs[i]['sma200'] for i in range(len(rows)-10, len(rows)))
        checks += [_check('最近10日触及SMA50且收盘始终高于SMA200', touched and intact),
                   _check('收盘高于前日最高价', bar['close'] > rows[-2]['high'], bar['close'], rows[-2]['high']),
                   _check('成交量 ≥ 前20日均量', bar['volume'] >= f['avgVolume20'], bar['volume'], f['avgVolume20']),
                   _check('126日收益 > SPY', score > 0, f['return126'], sf['return126'])]
    else:
        score = f['return126']
        checks.append(_check('126日收益为正', score > 0, score, 0))
    return checks, all(c['passed'] for c in checks), score, []


def _universe(holdings, watchlists):
    positions = _positions(holdings)
    raw_positions = holdings.get('positions', [])
    raw_positions = raw_positions if isinstance(raw_positions, list) else []
    for position in positions:
        originals = [p for p in raw_positions if isinstance(p, dict) and
                     ((_contract(p) == position['_contract']) if position['_contract'] else
                      (_contract(p) is None and _symbol(p) == position['_symbol']))]
        if len({(_num(p.get('stop_price')), p.get('sector')) for p in originals}) > 1:
            position['_identity_conflict'] = True
    source = watchlists.get('instruments', []) if isinstance(watchlists, dict) else []
    registry = {}
    for index, value in enumerate(source if isinstance(source, list) else []):
        if not isinstance(value, dict):
            continue
        row = deepcopy(value)
        key = 'contract:'+str(row['contractId']) if row.get('contractId') is not None else 'unresolved:'+str(index)
        row['groupIds'] = list(dict.fromkeys(g for g in row.get('groupIds', []) if isinstance(g, str)))
        if key in registry:
            prior = registry[key]
            prior['groupIds'] = list(dict.fromkeys(prior['groupIds']+row['groupIds']))
            if any(prior.get(k) != row.get(k) for k in ('symbol', 'currency', 'quoteSymbol')):
                prior['_conflict'] = True
        else:
            registry[key] = row
    manual = holdings.get('source') == 'manual'
    listing_counts = Counter(_symbol(row) for row in registry.values())
    holding_counts = Counter(p['_symbol'] for p in positions)
    def matches(row, position):
        return ((_contract(row) and _contract(row) == position['_contract']) or
                (manual and _symbol(row) == position['_symbol'] and listing_counts[_symbol(row)] == 1
                 and holding_counts[position['_symbol']] == 1))
    output = []
    for p in positions:
        matched = [r for r in registry.values() if matches(r, p)]
        output.append(dict(key=p['_key'], symbol=p['_symbol'], contract=p['_contract'], position=p,
                           listing=matched[0] if len(matched) == 1 else {}, groups=list(dict.fromkeys(
                               g for r in matched for g in r['groupIds']))))
    for key, row in registry.items():
        if any(matches(row, p) for p in positions):
            continue
        output.append(dict(key=key, symbol=_symbol(row), contract=_contract(row), position=None,
                           listing=row, groups=row['groupIds']))
    counts = Counter(r['symbol'] for r in output)
    for row in output:
        row['ambiguous'] = counts[row['symbol']] > 1
    return output


def build_personal_plan(market, holdings, watchlists, settings, now=None, market_issue=None):
    """Return reviewable daily conditions; unknown capacity never becomes zero risk."""
    config = _settings(settings)
    if not isinstance(market, dict) or not isinstance(holdings, dict):
        raise ValueError('行情和持仓必须是快照对象')
    now = _stamp(now) if now is not None else datetime.now(timezone.utc)
    if now is None:
        raise ValueError('当前时间无效')
    ny = now.astimezone(ZoneInfo('America/New_York'))
    cutoff = (ny.date() if ny.hour >= 16 else ny.date()-timedelta(days=1)).isoformat()
    expected = _latest_weekday(cutoff)
    manual = holdings.get('source') == 'manual'
    source = 'manual' if manual else 'broker'
    config['accountSource'] = source
    account_time = holdings.get('asOf')
    times = holdings.get('retrievalTimes')
    account_times = [account_time, *(times.values() if isinstance(times, dict) else [])]
    account_fresh = manual or all(_fresh(t, now) for t in account_times)
    market_fresh = _fresh(market.get('generatedAt'), now)
    refresh = market.get('quoteRefresh') or {}
    alerts = [*(refresh.get('errors', []) if isinstance(refresh, dict) else []), *(market.get('marketWarnings') or [])]
    failures = {r.get('symbol') for r in alerts if isinstance(r, dict)}
    benchmark = market.get('benchmark') or {}
    benchmark = benchmark if isinstance(benchmark, dict) else {}
    spy, spy_fs, benchmark_issues = _daily(benchmark, cutoff, expected, failures)
    global_unknown = []
    positions_known = isinstance(holdings.get('positions'), list) and all(isinstance(p, dict) for p in holdings['positions'])
    if not positions_known:
        global_unknown.append('账户持仓列表缺失或格式无效，不能当作已确认的空仓账户。')
    if not account_fresh: global_unknown.append('券商账户来源缺失、超过24小时或时间无效，请手动同步账户。')
    if not market_fresh: global_unknown.append('日线快照生成时间缺失、超过24小时或无效，请刷新行情。')
    if market_issue: global_unknown.append(str(market_issue))
    if benchmark_issues: global_unknown.append('SPY基准未通过：'+'；'.join(benchmark_issues))
    summary = holdings.get('account_summary') or {}
    summary = summary if isinstance(summary, dict) else {}
    nav = _num(summary.get('net_liquidation'))
    if nav is None or nav <= 0 or summary.get('currency') != 'USD':
        nav = None
        global_unknown.append('正数USD账户净值未核实，不能确定资金和风险预算。')
    cash, available, excess = (_num(summary.get(k)) for k in ('total_cash_value', 'available_funds', 'excess_liquidity'))
    if cash is None or (not manual and any(v is None for v in (available, excess))):
        global_unknown.append('现金、可用资金或剩余流动性缺失，不能假设有融资或现金余量。')
    universe = _universe(holdings, watchlists)
    quotes = [q for q in market.get('stocks', []) if isinstance(q, dict)] if isinstance(market.get('stocks'), list) else []
    items, contexts = [], {}
    gross_sum, sector_sum, open_risk = 0., 0., 0.
    valuations_known, sectors_known, stops_known = positions_known, True, positions_known
    unknown_sectors, fund_exposures, missing_stops = [], [], []
    for entry in universe:
        symbol, p, listing = entry['symbol'], entry['position'], entry['listing']
        found = [q for q in quotes if _symbol(q) == symbol]
        quote = found[0] if len(found) == 1 else {}
        rows, fs, reasons = _daily(quote, cutoff, expected, failures)
        unsupported = _unsupported_reason(quote, p, listing)
        identity = bool(re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', symbol)) and not entry['ambiguous']
        identity = identity and not listing.get('_conflict') and not (p and p.get('_identity_conflict'))
        identity = identity and not unsupported and len(found) == 1 and quote.get('currency') == 'USD' and quote.get('quoteType', quote.get('instrumentType')) == 'EQUITY'
        if p:
            quantity, cost, price, value, stop = (_num(p.get(k)) for k in ('position', 'average_price', 'market_price', 'market_value', 'stop_price'))
            identity = identity and p.get('currency') == 'USD' and p.get('asset_class') == 'STK' and quantity is not None and quantity > 0
            valid_value = (quantity is not None and quantity > 0 and price is not None and price > 0 and value is not None and value > 0
                           and p.get('currency') == 'USD' and abs(value-quantity*price) <= max(.02, abs(value)*.001))
            if manual and p.get('valuationSource') == 'daily_close' and p.get('valuationAsOf') != expected:
                valid_value = False
                reasons.append('该持仓日线估值不是最近完整工作日，不能据旧估值分配全账户容量。')
            if manual and p.get('valuationSource') == 'manual_mark' and not _fresh(p.get('valuationAsOf'), now):
                valid_value = False
                reasons.append('该持仓回退到手工参考价且确认已超过24小时；请更新参考价或恢复完整日线。')
            if value is not None and p.get('currency') == 'USD': gross_sum += abs(value)
            valuations_known = valuations_known and valid_value
            if not valid_value: reasons.append('持仓数量、币种、价格或市值不完整/不一致；保留该持仓并暂停新增风险。')
            if not manual and _contract(quote) and entry['contract'] and _contract(quote) != entry['contract']:
                identity = False
            if not manual or not valid_value or stop is None or stop <= 0 or not identity:
                stops_known = False
                if manual and (stop is None or stop <= 0): missing_stops.append(symbol)
            else:
                open_risk += quantity*max(0., price-stop)
            holding = dict(quantity=quantity, averageCost=cost, marketPrice=price, marketValue=value, stopPrice=stop if manual else None)
        else:
            quantity = cost = price = value = stop = None
            holding = None
            identity = identity and listing.get('supported') is True and listing.get('currency') in (None, 'USD')
            if _contract(quote) and entry['contract'] and _contract(quote) != entry['contract']:
                identity = False
        if unsupported:
            reasons.insert(0, unsupported)
        elif not identity:
            reasons.append('合约/代码/币种/普通股身份或多头方向未唯一核实；不同合约不能混用价格。')
        sector = _sector(p, quote, symbol)
        if p:
            sectors_known = sectors_known and sector != 'unknown'
            if sector == 'semiconductor' and value is not None: sector_sum += abs(value)
            if sector == 'unknown':
                if quote.get('quoteType') == 'ETF' or quote.get('securityKind') == 'fund': fund_exposures.append(symbol)
                else: unknown_sectors.append(symbol)
        if sector == 'unknown' and not unsupported:
            reasons.append('行业资料尚未导入，当前仅观察，无法生成最终股数。')
        conditions, eligible, score = [], False, 0.
        if rows and fs and spy and spy_fs and not benchmark_issues:
            conditions, eligible, score, alignment = _signal(rows, fs, spy, spy_fs, config['templateId'])
            reasons.extend(alignment)
        level = None
        if not unsupported and fs and fs[-1]['atr14'] > 0:
            close, atr = rows[-1]['close'], fs[-1]['atr14']
            proposed = close-2*atr
            if proposed > 0:
                level = dict(referencePrice=close, lowerPrice=close-.5*atr, upperPrice=close+.5*atr,
                             proposedStop=proposed, riskPerShare=2.5*atr)
        if level is None and not unsupported and not (rows and len(rows) < 200):
            reasons.append('缺少有效ATR或正数拟议止损，不能计算入场股数。')
        if p:
            profitable = bool(cost and cost > 0 and price and price > cost and level and level['lowerPrice'] > cost)
            conditions.append(_check('盈利加仓：账户价、收盘及整个拟入场区间均高于成本', profitable,
                                     dict(cost=cost, accountPrice=price, lowerEntry=level['lowerPrice'] if level else None), '全部严格高于平均成本'))
            eligible = eligible and profitable and config['allowProfitAdd']
        item = dict(id=entry['key'], symbol=symbol, contractId=entry['contract'], origin='holding' if p else 'watchlist',
                    name=quote.get('name') or listing.get('name') or symbol, groupIds=entry['groups'], holding=holding,
                    status='verify' if reasons else 'hold' if p else 'wait', label='', rationale='',
                    conditions=conditions, blockReasons=list(dict.fromkeys(reasons)), marketAsOf=rows[-1]['date'] if rows else None,
                    entry=level, quantity=None, quantityStatus='unknown' if reasons else 'not_applicable',
                    theoreticalMaxQuantity=None, sizingLimits=[], reduction=None, nextReview=TIMING,
                    cancelConditions=['任何账户持仓、现金、止损或有效订单发生变化后，先更新账户再重算。',
                                      '最新完整日线、SPY趋势、合约身份或成交量不再通过时取消。',
                                      '下一常规时段价格不在入场区间，或费用/流动性使限制不足一股时取消。'],
                    warnings=['拟议止损是新计划参考线，不是现有有效券商订单；跳空可使亏损超过计划风险。'])
        if p:
            item['warnings'].append('入场日期与入场以来最高收盘未确认；不推算120日持有期或历史移动止损。')
            if p.get('valuationSource'):
                item['warnings'].append(f"账户估值来源：{p['valuationSource']}；估值日期：{p.get('valuationAsOf') or '未知'}；手工编辑日期不随行情刷新改变。")
        contexts[item['id']] = dict(position=p, identity=identity, validValue=valid_value if p else True,
                                    sector=sector, rows=rows, fs=fs, eligible=eligible, score=score,
                                    unsupported=unsupported, ownReasons=bool(reasons),
                                    metadataEvidence=quote.get('metadataEvidence'),
                                    dataReady=not reasons and account_fresh and market_fresh and not benchmark_issues and not market_issue)
        items.append(item)

    broker_gross = _num(summary.get('gross_position_value'))
    gross = max(gross_sum, broker_gross or 0.) if valuations_known else None
    if broker_gross is not None and broker_gross > gross_sum+max(.02, gross_sum*.001):
        global_unknown.append('账户总持仓市值高于已逐项核验的持仓，存在未覆盖风险。')
        stops_known = False
    if not valuations_known: global_unknown.append('存在未核实的全账户持仓估值/币种/方向，不能计算剩余容量。')
    if unknown_sectors:
        global_unknown.append('持仓行业待核实：'+ '、'.join(dict.fromkeys(unknown_sectors))+'；暂不能确认全账户半导体额度。')
    if fund_exposures:
        global_unknown.append('持仓基金 '+ '、'.join(dict.fromkeys(fund_exposures))+' 的底层行业敞口尚未纳入当前普通股策略风控；不能假设半导体敞口为零。')
    if not stops_known:
        if missing_stops:
            global_unknown.append('请在账户中补充计划止损：'+'、'.join(dict.fromkeys(missing_stops))+'；这是你的退出计划，无法从公开资料代填。')
        else:
            global_unknown.append('已有持仓身份、计划止损或实际订单风险口径未通过，未平仓风险不可按零处理。')
    open_risk_value = open_risk if stops_known and valuations_known else None
    allocated = nav*config['allocationPct']/100 if nav else None
    reserve = nav*config['reserveCashPct']/100 if nav else None
    trade_pct, open_pct = min(.75, config['maxDrawdownPct']/40), min(3., config['maxDrawdownPct']/10)
    trade_budget = allocated*trade_pct/100 if allocated is not None else None
    risk_cap = allocated*open_pct/100 if allocated is not None else None
    single_cap = allocated*.25 if allocated is not None else None
    sector_cap = allocated*.60 if allocated is not None else None
    remaining = max(0., allocated-gross) if allocated is not None and gross is not None else None
    over = max(0., gross-allocated) if allocated is not None and gross is not None else None
    if manual:
        cash_capacity = max(0., min([cash-reserve, *([available] if available is not None else [])])) if cash is not None and reserve is not None else None
    else:
        cash_capacity = max(0., min(cash-reserve, available, excess)) if None not in (cash, reserve, available, excess) else None
    hard_blocks = []
    if cash is not None and cash <= 0: hard_blocks.append('现金非正，禁止借款买入；先处理融资现金缺口。')
    if any(v is not None and v <= 0 for v in ((available,) if manual else (available, excess))):
        hard_blocks.append('可用资金或剩余流动性非正，暂停新增风险。')
    if allocated == 0: hard_blocks.append('策略分配为0%，保留所有持仓管理但不新增风险。')
    if remaining == 0: hard_blocks.append('全部既有持仓已占满或超过策略分配容量。')
    if cash_capacity == 0: hard_blocks.append('预留现金及账户资金限制后没有可用现金。')
    if risk_cap is not None and open_risk_value is not None and open_risk_value >= risk_cap:
        hard_blocks.append('已有计划止损风险达到或超过分配资金的风险上限。')

    # Full universe required for a rotation ranking. An excluded/unknown security
    # is explicit evidence that this month's complete ranking is not verified.
    if config['templateId'] == 'rotation':
        complete = bool(items) and all(contexts[r['id']]['dataReady'] for r in items)
        next_day = datetime.fromisoformat(expected).date()+timedelta(days=1)
        while next_day.weekday() >= 5: next_day += timedelta(days=1)
        monthly = next_day.month != datetime.fromisoformat(expected).month
        ranked = sorted((r for r in items if contexts[r['id']]['eligible']), key=lambda r: (-contexts[r['id']]['score'], r['symbol'], r['id']))
        selected = {r['id'] for r in ranked[:3]}
        for row in items:
            ctx = contexts[row['id']]
            row['conditions'] += [_check('完整候选池月度排名可核实', complete),
                                  _check('下一常规时段为新月首个工作日，仍需核对交易日历', monthly),
                                  _check('126日动量位列前三', row['id'] in selected if complete else None)]
            ctx['eligible'] = ctx['eligible'] and complete and monthly and row['id'] in selected
            ctx['rotationExit'] = complete and monthly and row['id'] not in selected
            ctx['rankingIncomplete'] = not complete
            if not complete: row['blockReasons'].append('完整月度候选池有缺失或不支持证券；排名未验证。')

    # One reduction ledger: a share sold to meet one cap also reduces the other
    # caps and the cash deficit. This ledger is never used by the buy pass.
    sale_gross, sale_sector, sale_risk = gross, sector_sum, open_risk_value
    deficit = max(0., -cash) if cash is not None else 0.
    for row in sorted((r for r in items if r['holding']), key=lambda r: (-max(0., (r['holding']['marketValue'] or 0)-(single_cap or 0)), r['id'])):
        ctx, h = contexts[row['id']], row['holding']
        if not ctx['dataReady'] or not ctx['validValue'] or allocated is None:
            continue
        q, price, value = h['quantity'], h['marketPrice'], h['marketValue']
        rows, fs = ctx['rows'], ctx['fs']
        trend_exit = len(rows) >= 2 and all(fs[i]['sma50'] is not None and rows[i]['close'] < fs[i]['sma50'] for i in (-2, -1))
        stop_exit = bool(manual and h['stopPrice'] and rows[-1]['close'] <= h['stopPrice'])
        row['conditions'] += [_check('退出复核：连续两个收盘低于各自SMA50', trend_exit,
                                     dict(previousClose=rows[-2]['close'], previousSma50=fs[-2]['sma50'],
                                          close=rows[-1]['close'], sma50=fs[-1]['sma50']), '连续两个收盘均严格低于'),
                              _check('退出复核：收盘触及用户计划止损', stop_exit if manual and h['stopPrice'] else None,
                                     rows[-1]['close'], h['stopPrice'])]
        exit_trigger = trend_exit or stop_exit or ctx.get('rotationExit', False)
        demands = [max(0., value-single_cap), deficit/(1-FEE)]
        if sale_gross is not None: demands.append(max(0., sale_gross-allocated))
        if ctx['sector'] == 'semiconductor': demands.append(max(0., sale_sector-sector_cap))
        risk_per_share = max(0., price-h['stopPrice']) if manual and h['stopPrice'] else None
        if sale_risk is not None and risk_cap is not None and risk_per_share:
            demands.append(max(0., sale_risk-risk_cap)/risk_per_share*price)
        to_sell = q if exit_trigger else min(q, math.ceil(max(demands)/price-1e-10))
        if to_sell <= 0: continue
        amount = to_sell*price
        if sale_gross is not None: sale_gross = max(0., sale_gross-amount)
        if ctx['sector'] == 'semiconductor': sale_sector = max(0., sale_sector-amount)
        if sale_risk is not None and risk_per_share: sale_risk = max(0., sale_risk-to_sell*risk_per_share)
        deficit = max(0., deficit-amount*(1-FEE))
        reason = ('连续两个收盘跌破SMA50' if trend_exit else '收盘触及用户计划止损' if stop_exit
                  else '完整月度排名落榜' if ctx.get('rotationExit') else '共享账户/分配限额、计划止损风险或现金缺口')
        row.update(status='exit' if exit_trigger else 'reduce', quantity=0, quantityStatus='zero',
                   reduction=dict(quantity=to_sell, referencePrice=price, reason=reason))
    if any(r['reduction'] for r in items):
        hard_blocks.append('先复核减仓/退出并更新账户；拟议卖出资金不会提前用于新增买入。')

    used_cash, used_risk, used_gross, used_sector = 0., 0., 0., 0.
    for row in sorted(items, key=lambda r: (-contexts[r['id']]['score'], r['symbol'], r['id'])):
        ctx, entry, h = contexts[row['id']], row['entry'], row['holding']
        if entry is None or allocated is None:
            continue
        price, distance = entry['upperPrice'], entry['riskPerShare']
        owned = h['marketValue'] if h and h['marketValue'] is not None else 0.
        basic = [('每笔风险预算', trade_budget/distance),
                 ('单股25%剩余额度', max(0., single_cap-owned)/price),
                 ('分配资金总额度', allocated/price),
                 ('成交量参与上限', ctx['fs'][-1]['avgVolume20']*.001)]
        row['theoreticalMaxQuantity'] = max(0, math.floor(min(v for _, v in basic)+1e-10))
        limits = [*basic,
                  ('全账户既有持仓及已预留后的分配余量', max(0., remaining-used_gross)/price if remaining is not None else None),
                  ('现金/保证金/预留现金余量', max(0., cash_capacity-used_cash)/(price*(1+FEE)) if cash_capacity is not None else None),
                  ('全账户计划止损风险余量', max(0., risk_cap-open_risk_value-used_risk)/distance if open_risk_value is not None else None)]
        if ctx['sector'] == 'semiconductor':
            limits.append(('半导体60%剩余额度', max(0., sector_cap-sector_sum-used_sector)/price if sectors_known else None))
        row['sizingLimits'] = [dict(label=label, quantity=max(0, math.floor(v+1e-10)) if v is not None else None) for label, v in limits]
        if row['reduction']:
            continue
        if row['blockReasons'] or global_unknown:
            row['blockReasons'] = list(dict.fromkeys(row['blockReasons']+global_unknown))
            row.update(status='verify' if row['blockReasons'] else 'blocked', quantity=None, quantityStatus='unknown')
        elif hard_blocks:
            row['blockReasons'].extend(hard_blocks)
            row.update(status='blocked', quantity=0, quantityStatus='zero')
        elif not ctx['eligible']:
            row.update(status='hold' if h else 'wait', quantity=0, quantityStatus='zero')
            if h and not config['allowProfitAdd']: row['blockReasons'].append('盈利加仓开关已关闭。')
        else:
            count = max(0, math.floor(min(v for _, v in limits if v is not None)+1e-10))
            row.update(status='conditional' if count else 'blocked', quantity=count,
                       quantityStatus='conditional' if count else 'zero')
            if not count: row['blockReasons'].append('共享预算或单项约束不足1股。')
            used_cash += count*price*(1+FEE)
            used_gross += count*price
            used_risk += count*distance
            if ctx['sector'] == 'semiconductor': used_sector += count*price
    for row in items:
        ctx = contexts[row['id']]
        required_bars = 209 if config['templateId'] == 'pullback' else 200
        available_bars = len(ctx['rows'])
        state = ('unsupported' if ctx['unsupported'] else
                 'insufficient_history' if 0 < available_bars < required_bars else
                 'needs_data' if ctx['ownReasons'] or not ctx['dataReady'] else
                 'ranking_incomplete' if ctx.get('rankingIncomplete') else
                 'account_required' if global_unknown else 'ready')
        row['verification'] = dict(state=state, requiredBars=required_bars, availableBars=available_bars)
        if isinstance(ctx['metadataEvidence'], dict): row['verification']['source'] = ctx['metadataEvidence']
        row['label'] = LABELS[row['status']]
        if row['status'] == 'verify':
            row['label'] = {'unsupported': '策略不适用',
                            'insufficient_history': f'历史不足 · {available_bars}/{required_bars}日',
                            'account_required': '待补充账户信息',
                            'ranking_incomplete': '候选池未完整'}.get(state, row['label'])
        row['rationale'] = (row['reduction']['reason'] if row['reduction'] else row['blockReasons'][0] if row['blockReasons']
                            else '已满足当前日线条件；股数只是共享预算内的条件计划，执行前需更新价格和账户。' if row['status'] == 'conditional'
                            else '当前未满足全部入场条件，继续观察完整日线及已有保护计划。')
    next_close = datetime.combine(ny.date(), time(16), tzinfo=ny.tzinfo)
    if next_close <= ny: next_close += timedelta(days=1)
    while next_close.weekday() >= 5: next_close += timedelta(days=1)
    expiries = [next_close.astimezone(timezone.utc)]
    for value in [market.get('generatedAt'), *(account_times if not manual else [])]:
        timestamp = _stamp(value)
        if timestamp: expiries.append(timestamp+timedelta(hours=24))
    warnings = ['所有价格来自已完成日线，股数仅为有条件的人工计划，不是订单或成交承诺。',
                '未知账户净值历史高点，真实回撤为未知；最大回撤目标只收紧预算，不保证损失上限。',
                '日线未完整调整拆股分红；账户成本、止损和行情口径发生变化时须核实。',
                '未接入交易所节假日/提前收盘日历；日期不确定时保守暂停。',
                '手工止损是用户的计划，不是已经生效的券商止损订单；系统无法得知手工账户是否另有交易。' if manual
                else '券商账户是手动读取的快照；未接入真实止损和订单记录，不能把缺失风险视为零。']
    return dict(version=VERSION, generatedAt=now.isoformat(), settings=config,
                freshness=dict(marketAsOf=spy[-1]['date'] if spy else None,
                               marketFresh=market_fresh and not benchmark_issues and not market_issue,
                               accountFresh=account_fresh, expiresAt=min(expiries).isoformat()),
                account=dict(source=source, currency=summary.get('currency'), asOf=account_time,
                             netLiquidation=nav, cash=cash, availableFunds=available, excessLiquidity=excess,
                             grossPositionValue=gross, openRiskValue=open_risk_value,
                             openRiskStatus='unknown' if open_risk_value is None else 'planned' if any(r['holding'] for r in items) else 'empty',
                             currentDrawdownPct=None),
                allocation=dict(allocationPct=config['allocationPct'], allocatedCapital=allocated,
                                reserveCashValue=reserve, committedCapital=gross, remainingCapital=remaining,
                                overAllocatedCapital=over, tradeRiskPct=trade_pct, tradeRiskBudget=trade_budget,
                                openRiskCapPct=open_pct, openRiskBudget=risk_cap,
                                annualTargetValue=allocated*config['targetReturnPct']/100 if allocated is not None else None,
                                drawdownBudgetValue=allocated*config['maxDrawdownPct']/100 if allocated is not None else None),
                summary=dict(headline='先复核已有风险，再按共享预算查看下一常规时段条件计划。',
                             holdingCount=sum(r['origin'] == 'holding' for r in items),
                             candidateCount=sum(r['origin'] == 'watchlist' for r in items),
                             conditionalCount=sum(r['status'] == 'conditional' for r in items),
                             blockedCount=sum(r['status'] in ('verify', 'blocked') for r in items),
                             plannedBuyValue=used_cash, plannedRiskValue=used_risk,
                             remainingCash=max(0., cash_capacity-used_cash) if cash_capacity is not None else None,
                             blockReasons=list(dict.fromkeys(global_unknown+hard_blocks))),
                items=items, rules=_rules(config['templateId']),
                validation=dict(status='unvalidated', targetReturnPct=config['targetReturnPct'], maxDrawdownPct=config['maxDrawdownPct'],
                                conclusion='当前长线规则、完整自选股票池和盈利加仓尚无同口径历史验证；不能判断收益目标可达。',
                                warnings=['旧固定五股回测不验证此计划，不能沿用其收益、胜率或回撤。',
                                          '年度目标只表达所需收益和研究评价，不提高杠杆、现金额度或风险限额。']),
                warnings=warnings)
