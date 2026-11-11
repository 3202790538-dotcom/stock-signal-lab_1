"""Conditional research for existing IBKR positions; never executes orders.

This consumes supplied snapshots only. It deliberately has no original entry,
stop, or equity-high-water state and therefore never sizes an additional buy.
The fixed-universe portfolio simulator remains independent of this module.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import math
import re
from zoneinfo import ZoneInfo

from backtest import _history
from portfolio_factors import factors, signal_details, validate_histories
from strategy_rules import TEMPLATES, get_profile

VERSION = 'holdings-plan-v1.0'
SEMICONDUCTORS = frozenset(('NVDA', 'MRVL', 'AVGO', 'MU', 'SKHY', 'SKUU'))
CORE = frozenset(('NVDA', 'MRVL', 'AVGO', 'MU', 'GOOG', 'SPY'))
TIMING = '已完成日线确认后，下一个常规交易时段复核实时价格、持仓与订单；本页不下单。'
UNKNOWN_RISK = '原始入场日期、初始止损、入场以来最高价和真实订单记录未确认；不能推算原有移动止损、持有期退出或未平仓风险。'
LABELS = {'verify': '先核实数据', 'review_exit': '复核退出条件', 'review_reduce': '复核减仓',
          'hold': '条件持有', 'watch_add': '观察盈利加仓'}
ISSUE_TEXT = {
    'account_stale': ('账户快照或其中一项来源已超过 24 小时', '请人工重新读取并同步 IBKR 持仓、账户指标和余额；刷新行情不会更新账户。'),
    'account_timestamp_invalid': ('账户来源时间缺失、无效或晚于当前时间', '核对账户快照及各项读取时间，必要时人工重新同步 IBKR；本页没有账户同步按钮。'),
    'market_stale': ('行情快照生成已超过 24 小时', '刷新行情并等待完成；只刷新计划会重算旧数据。'),
    'market_timestamp_invalid': ('行情生成时间缺失、无效或晚于当前时间', '刷新行情，确认返回了有效生成时间后再查看计划。'),
    'latest_bar_missing': ('基准最新完整日线未就绪，或日线日期未一致', '刷新行情并核对 SPY 日线日期；生成时间新不能替代日线。工作日检查未接交易所日历，节假日需另行核实。'),
    'market_refresh_issue': ('行情刷新尚未完成或刷新失败', '若正在刷新请等待完成；若失败请重新刷新行情并检查结果，旧价位暂只供核对。'),
    'core_quote_issue': ('核心行情存在刷新错误或数据警告', '刷新行情并查看核心股票与 SPY 的具体报错；完整数据恢复前保持暂停。'),
    'benchmark_quote_stale': ('SPY 报价状态未通过', '刷新行情并确认基准报价恢复可用；缓存报价不能证明当前市场条件。'),
    'position_quote_issue': ('该持仓行情刷新失败、过时或存在数据警告', '刷新行情并查看该证券报错；仍失败时核对代码、证券类型及行情来源。'),
    'position_date_mismatch': ('该持仓、SPY 与快照的日线日期未一致', '刷新行情并核对各自最后完整日线日期；不要把较早收盘当作最新信号。'),
    'position_quote_missing': ('没有找到该持仓对应的行情', '核对券商合约与行情代码后刷新行情；本页保留持仓，不会用其他证券的价格替代。'),
    'identity_mismatch': ('合约、代码、币种、资产类别或持仓方向未通过核对', '人工核对 IBKR 合约与行情身份；本模板仅支持匹配的 USD 普通股票多头。'),
    'broker_value_inconsistent': ('券商价格、数量或市值缺失或未通过一致性检查', '人工核对并重新同步同一账户快照中的持仓与估值；刷新公开行情不能修复券商字段。'),
    'history_missing': ('缺少可用的完整历史日线', '核对证券代码与行情来源后刷新行情；仍无历史时仅作账户风险复核。'),
    'history_insufficient': ('与 SPY 可对齐的完整日线不足 200 根', '等待累积足够真实历史，或核对行情是否下载完整；刷新不能补出上市前的数据。'),
    'history_invalid': ('历史价格或日线结构未通过校验', '刷新并核对原始日线；在价格、日期和字段恢复有效前不使用模板信号。'),
    'history_volume_invalid': ('历史成交量缺失或无效', '刷新并核对日线成交量；不能把缺失成交量当成零成交或当成放量信号。'),
    'history_calendar_mismatch': ('持仓与 SPY 的历史交易日期未对齐', '核对缺失日期及行情来源后刷新；不补造缺失交易日或静默截掉最新日期。'),
    'etf_risk_only': ('ETF 仅作账户风险复核，不适用普通股票模板', '人工核对产品与杠杆风险；刷新行情不会解除证券类型限制，也不会开启普通股规则加仓。'),
    'instrument_unverified': ('证券类型尚未确认，暂仅作账户风险复核', '先核实证券类型、合约与行情元数据；确认普通股票身份前不套用股票加仓规则。'),
}


def _issue(code):
    label, action = ISSUE_TEXT[code]
    return dict(code=code, label=label, action=action)


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _stamp(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def _fresh(value, now):
    timestamp = _stamp(value)
    return timestamp is not None and 0 <= (now-timestamp).total_seconds() <= 86400


def _latest_weekday(cutoff):
    """Fail closed on missing weekday bars; holidays need separate verification."""
    day = date.fromisoformat(cutoff)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def _contract(row):
    value = row.get('contract_id', row.get('contractId', row.get('conid')))
    return str(value) if value is not None and not isinstance(value, (dict, list, bool)) else None


def _symbol(row):
    value = row.get('symbol') or row.get('contract_description') or ''
    return value.strip().upper() if isinstance(value, str) else ''


def _positions(holdings):
    """Merge known identical contracts; never merge different contracts by ticker."""
    grouped = {}
    source = holdings.get('positions', [])
    for index, raw in enumerate(source if isinstance(source, list) else []):
        if not isinstance(raw, dict):
            continue
        quantity = _num(raw.get('position'))
        if quantity == 0:
            continue
        symbol, contract = _symbol(raw), _contract(raw)
        currency, asset = raw.get('currency'), raw.get('asset_class')
        safe_symbol = bool(re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', symbol))
        key = ('contract:'+contract if contract else 'symbol:'+symbol
               if safe_symbol and currency == 'USD' and asset == 'STK' else 'unknown:'+str(index))
        record = dict(raw, _key=key, _symbol=symbol or '待核实', _contract=contract,
                      _identity_conflict=False)
        if key not in grouped:
            grouped[key] = record
            continue
        previous = grouped[key]
        # Same ticker in different currencies/classes is never silently netted.
        if any(previous.get(k) != record.get(k) for k in ('currency', 'asset_class', '_symbol')):
            record['_key'] = key+':conflict:'+str(index)
            record['_identity_conflict'] = previous['_identity_conflict'] = True
            grouped[record['_key']] = record
            continue
        old_quantity = _num(previous.get('position'))
        if quantity is None or old_quantity is None or quantity*old_quantity < 0:
            record['_key'] = key+':conflict:'+str(index)
            record['_identity_conflict'] = previous['_identity_conflict'] = True
            grouped[record['_key']] = record
            continue
        total = quantity+old_quantity
        cost, old_cost = _num(record.get('average_price')), _num(previous.get('average_price'))
        previous['average_price'] = ((cost*quantity+old_cost*old_quantity)/total
                                     if cost is not None and old_cost is not None and total else None)
        previous['position'] = total
        for field in ('market_value', 'unrealized_pnl'):
            left, right = _num(previous.get(field)), _num(record.get(field))
            previous[field] = left+right if left is not None and right is not None else None
        if _num(previous.get('market_price')) != _num(record.get('market_price')):
            previous['_identity_conflict'] = True
    return list(grouped.values())


def _history_context(stock, benchmark, cutoff):
    """Preserve valid OHLC levels even if volume, calendar, or sample gates fail."""
    rows, technical, warnings, issue_codes = [], None, [], []
    try:
        rows = _history(stock.get('history', []), cutoff)
    except (ValueError, TypeError, KeyError):
        warnings.append('历史价格无效，不能计算技术价位。')
        issue_codes.append('history_invalid')
    level = {'close': None, 'sma20': None, 'sma50': None, 'sma200': None, 'atr14': None, 'priorHigh': None}
    if rows:
        level['close'] = rows[-1]['close']
        for window in (20, 50, 200):
            if len(rows) >= window:
                level[f'sma{window}'] = sum(r['close'] for r in rows[-window:])/window
        if len(rows) >= 15:
            trs = [max(r['high']-r['low'], abs(r['high']-rows[i-1]['close']),
                       abs(r['low']-rows[i-1]['close'])) for i, r in enumerate(rows) if i > 0]
            level['atr14'] = sum(trs[-14:])/14
        if len(rows) >= 21:
            level['priorHigh'] = max(r['high'] for r in rows[-21:-1])
    history_issue = 'history_invalid'
    try:
        sources = stock.get('history', [])
        spy_sources = benchmark.get('history', [])
        if not isinstance(sources, list) or not isinstance(spy_sources, list):
            raise ValueError('日线必须为列表。')
        # Different listing dates are allowed; missing days within the shared period are not.
        stock_rows = [r for r in sources if isinstance(r, dict) and isinstance(r.get('date'), str) and r['date'] <= cutoff]
        spy_rows = [r for r in spy_sources if isinstance(r, dict) and isinstance(r.get('date'), str) and r['date'] <= cutoff]
        if len(stock_rows) < 200 or len(spy_rows) < 200:
            history_issue = 'history_insufficient' if stock_rows and spy_rows else 'history_missing'
            raise ValueError('不足 200 根完整日线，仅保留可计算价位。')
        if stock_rows[-1]['date'] != spy_rows[-1]['date']:
            history_issue = 'history_calendar_mismatch'
            raise ValueError('持仓与 SPY 最新已完成日线不一致。')
        start = max(stock_rows[0]['date'], spy_rows[0]['date'])
        stock_rows = [r for r in stock_rows if r['date'] >= start]
        spy_rows = [r for r in spy_rows if r['date'] >= start]
        if len(stock_rows) < 200 or len(spy_rows) < 200:
            history_issue = 'history_insufficient'
            raise ValueError('与 SPY 对齐后不足 200 根完整日线。')
        if any(_num(r.get('volume')) is None or r['volume'] < 0 for r in stock_rows+spy_rows):
            history_issue = 'history_volume_invalid'
        elif [r['date'] for r in stock_rows] != [r['date'] for r in spy_rows]:
            history_issue = 'history_calendar_mismatch'
        stocks, spy = validate_histories({'position': stock_rows}, spy_rows)
        validated = stocks['position']
        technical = (validated, factors(validated), spy, factors(spy))
    except (ValueError, TypeError, KeyError, IndexError):
        warnings.append('完整日线不足、成交量缺失或与 SPY 日期未对齐；不生成模板买入信号。')
        issue_codes.append(history_issue)
    return rows, level, technical, warnings, [_issue(code) for code in dict.fromkeys(issue_codes)]


def _check(label, passed, observed=None, required=None):
    return dict(label=label, passed=passed, observed=observed, required=required)


def build_holdings_plan(market, holdings, template_id='breakout', risk_profile='aggressive',
                        allow_profit_add=True, now=None, market_issue=None):
    if not isinstance(template_id, str) or template_id not in TEMPLATES:
        raise ValueError('未知策略模板')
    profile = get_profile(risk_profile)
    if not isinstance(allow_profit_add, bool):
        raise ValueError('盈利加仓开关必须是布尔值')
    if not isinstance(market, dict) or not isinstance(holdings, dict):
        raise ValueError('行情和持仓必须是快照对象')
    now = _stamp(now) if now is not None else datetime.now(timezone.utc)
    if now is None:
        raise ValueError('当前时间无效')
    local = now.astimezone(ZoneInfo('America/New_York'))
    cutoff = (local.date() if local.hour >= 16 else local.date()-timedelta(days=1)).isoformat()
    parameters = deepcopy(TEMPLATES[template_id]['defaultParameters'])
    positions = _positions(holdings)
    account_asof, market_asof = holdings.get('asOf'), market.get('marketAsOf')
    account_fresh = _fresh(account_asof, now)
    retrieval = holdings.get('retrievalTimes')
    if isinstance(retrieval, dict):
        account_fresh = account_fresh and all(_fresh(v, now) for v in retrieval.values())
    account_stamps = [account_asof, *(retrieval.values() if isinstance(retrieval, dict) else [])]
    effective_asof = min(_stamp(v) for v in account_stamps).isoformat() if account_fresh else None
    market_fresh = _fresh(market.get('generatedAt'), now)
    summary = holdings.get('account_summary') or {}
    summary = summary if isinstance(summary, dict) else {}
    nav = _num(summary.get('net_liquidation'))
    nav = nav if nav is not None and nav > 0 and summary.get('currency') == 'USD' else None
    cash, available, excess = (_num(summary.get(k)) for k in ('total_cash_value', 'available_funds', 'excess_liquidity'))
    values = []
    semi = 0.0
    known_values = True
    for position in positions:
        value, price, qty = (_num(position.get(k)) for k in ('market_value', 'market_price', 'position'))
        if value is None and price is not None and qty is not None:
            value = price*qty
        if position.get('currency') != 'USD' or value is None:
            known_values = False
            continue
        values.append(abs(value))
        if position['_symbol'] in SEMICONDUCTORS:
            semi += abs(value)
    broker_gross = _num(summary.get('gross_position_value'))
    gross = max(broker_gross or 0, sum(values)) if broker_gross is not None or known_values else None
    gross_cap = nav*profile['grossCapPct']/100 if nav else None
    sector_cap = nav*profile['sectorCapPct']/100 if nav else None
    gross_excess = max(0, gross-gross_cap) if gross is not None and gross_cap is not None else None
    sector_excess = max(0, semi-sector_cap) if sector_cap is not None else None
    benchmark = market.get('benchmark') or {}
    benchmark = benchmark if isinstance(benchmark, dict) else {}
    try:
        benchmark_rows = _history(benchmark.get('history', []), cutoff)
        benchmark_date = benchmark_rows[-1]['date'] if benchmark_rows else None
        daily_recent = (benchmark_date == market_asof and benchmark_date == _latest_weekday(cutoff)
                        and (not benchmark.get('asOf') or benchmark.get('asOf') == benchmark_date))
    except (ValueError, TypeError, KeyError):
        daily_recent = False
    stocks = [s for s in market.get('stocks', []) if isinstance(s, dict)] if isinstance(market.get('stocks'), list) else []
    refresh = market.get('quoteRefresh') or {}
    errors = refresh.get('errors', []) if isinstance(refresh, dict) else []
    errors = [r for r in errors if isinstance(r, dict)] if isinstance(errors, list) else []
    warnings = market.get('marketWarnings', [])
    warnings = [r for r in warnings if isinstance(r, dict)] if isinstance(warnings, list) else []
    global_errors = bool(market_issue) or not daily_recent or any(r.get('symbol') in CORE for r in errors+warnings)
    global_errors = global_errors or benchmark.get('quoteStatus', benchmark.get('quote_status', 'ready')) not in ('ready', 'available')
    # Explanation metadata follows existing gates; it never participates in a decision.
    data_issues = []
    if not daily_recent:
        data_issues.append(_issue('latest_bar_missing'))
    if market_issue:
        data_issues.append(_issue('market_refresh_issue'))
    if not account_fresh:
        invalid = any(_stamp(v) is None or _stamp(v) > now for v in account_stamps)
        data_issues.append(_issue('account_timestamp_invalid' if invalid else 'account_stale'))
    if not market_fresh:
        stamp = _stamp(market.get('generatedAt'))
        data_issues.append(_issue('market_timestamp_invalid' if stamp is None or stamp > now else 'market_stale'))
    if any(r.get('symbol') in CORE for r in errors+warnings):
        data_issues.append(_issue('core_quote_issue'))
    if benchmark.get('quoteStatus', benchmark.get('quote_status', 'ready')) not in ('ready', 'available'):
        data_issues.append(_issue('benchmark_quote_stale'))
    general_blocks = []
    if not account_fresh: general_blocks.append('账户快照时间缺失或超过 24 小时，需手动同步 IBKR。')
    if not market_fresh: general_blocks.append('行情生成时间缺失或超过 24 小时，需刷新行情。')
    if not daily_recent: general_blocks.append('最近完整工作日缺少对应基准日线，先核实行情与交易所日历；刷新时间不能代替日线时间，节假日可能保守暂停。')
    if global_errors: general_blocks.append('行情存在刷新错误或核心数据问题，先核实数据。')
    if nav is None: general_blocks.append('USD 账户净值未确认。')
    if cash is None or cash <= 0: general_blocks.append('账户现金非正或未确认，禁止加仓；融资购买力不能替代现金。')
    if available is None or available <= 0: general_blocks.append('可用资金非正或未确认，禁止加仓。')
    if excess is None or excess <= 0: general_blocks.append('剩余流动性非正或未确认，禁止加仓。')
    if gross is None or gross_cap is None or gross >= gross_cap: general_blocks.append('总名义市值已达上限或无法确认，禁止加仓。')
    if not known_values: general_blocks.append('存在币种或估值未确认的持仓，不能确认全账户风险余量。')
    if not allow_profit_add: general_blocks.append('盈利加仓已关闭。')
    account = dict(currency=summary.get('currency'), netLiquidation=nav, cash=cash,
                   availableFunds=available, excessLiquidity=excess, grossPositionValue=gross,
                   grossExposurePct=gross/nav*100 if gross is not None and nav else None,
                   semiconductorValue=semi if known_values else None,
                   semiconductorExposurePct=semi/nav*100 if nav and known_values else None,
                   grossCapValue=gross_cap, grossExcessValue=gross_excess,
                   semiconductorCapValue=sector_cap, semiconductorExcessValue=sector_excess if known_values else None,
                   openRiskConfirmed=False, asOf=account_asof, effectiveAsOf=effective_asof, fresh=account_fresh)
    items = []
    for position in positions:
        symbol, contract = position['_symbol'], position['_contract']
        candidates = [s for s in stocks if contract and _contract(s) == contract]
        if not candidates:
            candidates = [s for s in stocks if _symbol(s) == symbol]
        stock = candidates[0] if len(candidates) == 1 else {}
        currency, asset = position.get('currency'), position.get('asset_class')
        quantity, cost, price, value, pnl = (_num(position.get(k)) for k in
                                           ('position', 'average_price', 'market_price', 'market_value', 'unrealized_pnl'))
        if value is None and quantity is not None and price is not None:
            value = quantity*price
        if pnl is None and None not in (quantity, price, cost):
            pnl = quantity*(price-cost)
        quote_type = stock.get('quoteType', stock.get('instrumentType', 'UNKNOWN'))
        instrument = ('leveraged_etf_2x' if symbol == 'SKUU' else 'etf' if quote_type == 'ETF'
                      else 'stock' if asset == 'STK' and quote_type == 'EQUITY' else 'unverified')
        identity_ok = (not position['_identity_conflict'] and bool(re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', symbol))
                       and currency == 'USD' and asset == 'STK' and quantity is not None and quantity > 0)
        matching = (bool(stock) and len(candidates) == 1 and stock.get('currency') == 'USD'
                    and _symbol(stock) == symbol and (not _contract(stock) or not contract or _contract(stock) == contract))
        broker_valid = identity_ok and price is not None and price > 0 and value is not None and value > 0
        broker_valid = broker_valid and abs(value-price*quantity) <= max(.02, abs(value)*.001)
        rows, level, technical, item_warnings, history_issues = _history_context(stock, benchmark, cutoff)
        asof = rows[-1]['date'] if rows else None
        stock_status = stock.get('quoteStatus', stock.get('quote_status', 'ready'))
        quote_bad = stock_status not in ('ready', 'available') or any(r.get('symbol') == symbol for r in errors+warnings)
        date_bad = bool(rows) and (asof != market_asof or (stock.get('asOf') and asof != stock.get('asOf'))
                                  or (benchmark.get('asOf') and asof != benchmark.get('asOf')))
        verified = account_fresh and market_fresh and not global_errors and not quote_bad and not date_bad
        technical_ok = bool(verified and identity_ok and matching and instrument == 'stock' and technical)
        signal_checks, entry_ok, market_ok = [], False, False
        if technical_ok:
            history, fs, spy, spy_fs = technical
            details, eligible, rank = signal_details(history, fs, spy, spy_fs, len(history)-1, template_id, parameters)
            signal_checks = [_check(d['label'], d['passed'], d['value'], d['threshold']) for d in details]
            # Rotation's ranking eligibility omits liquidity; additional buying still requires it.
            entry_ok = eligible and all(d['passed'] for d in details[:-1])
            market_ok = details[-1]['passed']
        cost_valid = cost is not None and cost > 0
        profitable = bool(cost_valid and level['close'] is not None and price is not None
                          and level['close'] > cost and price > cost)
        single_cap = nav*profile['singleCapPct']/100 if nav else None
        single_excess = max(0, value-single_cap) if value is not None and single_cap is not None else None
        single_room = bool(single_cap is not None and value is not None and value < single_cap)
        semi_group = symbol in SEMICONDUCTORS
        sector_room = bool(known_values and sector_cap is not None and (not semi_group or semi < sector_cap))
        add_candidate = bool(not general_blocks and technical_ok and broker_valid and profitable
                             and entry_ok and market_ok and single_room and sector_room
                             and template_id != 'rotation')
        risk_trigger = bool((single_excess or 0) > 0 or (gross_excess or 0) > 0
                            or (semi_group and (sector_excess or 0) > 0) or (cash is not None and cash < 0))
        reduce_ready = bool(verified and broker_valid and nav and risk_trigger and (matching or not stock))
        exit_ma = level['sma20'] if template_id == 'breakout' else level['sma50']
        exit_trigger = bool(technical_ok and exit_ma is not None and level['close'] < exit_ma)
        reduce_quantity = None
        if reduce_ready:
            portions = [single_excess or 0]
            if gross and gross_excess:
                portions.append(value*gross_excess/gross)
            if semi_group and semi and sector_excess:
                portions.append(value*sector_excess/semi)
            amount = max(portions)
            if amount > 0:
                reduce_quantity = min(quantity, math.ceil(amount/price))
        if not verified or not identity_ok or (stock and not matching) or not broker_valid:
            kind = 'verify'
        elif exit_trigger:
            kind = 'review_exit'
        elif reduce_ready:
            kind = 'review_reduce'
        elif not technical_ok:
            kind = 'verify'
        elif add_candidate:
            kind = 'watch_add'
        else:
            kind = 'hold'
        if not verified:
            item_warnings.append('数据新鲜度、报价状态或日线日期未通过；价位仅保留作历史参考，所有行动触发暂停。')
        if not identity_ok or not matching:
            item_warnings.append('合约、代码、币种、资产类别或多头数量未完整匹配，需人工核实。')
        if not broker_valid:
            item_warnings.append('券商价格、数量和市值未通过一致性核验。')
        if instrument == 'leveraged_etf_2x':
            item_warnings.append('SKUU 为 2 倍 ETF；按券商名义市值计入半导体暴露，未折算为等效标的风险，不套用普通股票自动加仓规则。')
        elif instrument == 'etf':
            item_warnings.append('ETF 仅做账户风险复核，不套用普通股票模板加仓。')
        item_warnings += [UNKNOWN_RISK,
                          '拟议风险参考线来自当前收盘减 2ATR，不是原有止损，也不能保证止损成交或最大损失。']
        if template_id == 'rotation':
            item_warnings.append('仅展示现有持仓的趋势条件；未构建完整候选池周排名，不据此宣称入选或落榜。')
        checks = [_check('账户与行情均在 24 小时内且无数据阻断', verified,
                         {'accountFresh': account_fresh, 'marketFresh': market_fresh}, True),
                  _check('匹配 USD 股票多头合约', identity_ok and matching, currency, 'USD / STK / 多头'),
                  _check('券商价格与持仓市值一致', broker_valid, price, '数量 × 券商价格匹配市值'),
                  _check('完成日线收盘高于成本（禁止摊低成本）', bool(cost_valid and level['close'] is not None and level['close'] > cost), level['close'], cost),
                  _check('券商价格高于成本（禁止摊低成本）', bool(cost_valid and price is not None and price > cost), price, cost),
                  _check('账户现金为正', cash is not None and cash > 0, cash, '> 0'),
                  _check('可用资金与剩余流动性为正', bool(available is not None and excess is not None and available > 0 and excess > 0),
                         {'availableFunds': available, 'excessLiquidity': excess}, '> 0'),
                  _check('总名义市值未达上限', gross is not None and gross_cap is not None and gross < gross_cap, account['grossExposurePct'], profile['grossCapPct']),
                  _check('单股名义市值未达上限', single_room, value/nav*100 if value is not None and nav else None, profile['singleCapPct']),
                  _check('半导体名义市值未达上限', sector_room, account['semiconductorExposurePct'], profile['sectorCapPct']),
                  _check('至少 200 根完整并与 SPY 对齐的股票日线', technical_ok, len(rows), 200),
                  *signal_checks,
                  _check('真实初始止损与全账户未平仓风险已核实', None, None, '需核实原始成交与有效止损订单')]
        if template_id == 'rotation':
            checks.append(_check('完整候选池周排名已确认', None, None, '每周首个交易时段核实完整候选池及入选名单'))
        levels = [dict(label=label, value=level[key], explanation='已完成日线计算；数据日期见本项日线时间。')
                  for key, label in [('close', '已完成日线收盘'), ('sma20', 'SMA20'), ('sma50', 'SMA50'),
                                     ('sma200', 'SMA200'), ('atr14', 'ATR14'), ('priorHigh', '此前 20 日最高价（不含当日）')]]
        proposed = level['close']-2*level['atr14'] if level['close'] is not None and level['atr14'] is not None else None
        levels.append(dict(label='参考防守价（收盘 − 2ATR）', value=max(0, proposed) if proposed is not None else None,
                           explanation='新的讨论参考线，并非已有止损；需结合原始成交、风险预算和有效订单核实。'))
        add_timing = ('每周首个常规交易时段，先确认上一交易日收盘及完整候选池排名；' if template_id == 'rotation' else '')+TIMING
        hold_trigger = bool(technical_ok and not exit_trigger and not reduce_ready and not add_candidate)
        scenarios = [
            dict(id='hold', title='条件持有', triggered=hold_trigger,
                 conditions=['已完成收盘未跌破当前模板退出均线；账户限额未要求减仓。',
                             '核实已有保护订单，每次日线更新后复查；未知初始止损不能当成不存在止损。'],
                 timing='每个完整交易日收盘后更新条件；'+TIMING,
                 referencePrice=exit_ma, quantity=None, quantityNote='持有观察不产生新增订单数量。'),
            dict(id='add', title='仅盈利趋势加仓', triggered=add_candidate,
                 conditions=['完成日线收盘与券商价格均严格高于平均成本；亏损仓位禁止摊低成本。',
                             '模板全部入场条件通过，SPY 收盘高于 SMA200；行情、合约和成交量均有效。',
                             '现金、可用资金与剩余流动性均为正；总仓、单股、半导体及真实未平仓风险仍有余量。',
                             '先核实原始止损、真实订单和账户风险；实时价与信号收盘偏离超过 0.5ATR 则跳过；重新计算股数。'
                             ] + (['仅当前持仓的趋势筛选，不代表完整股票池周排名通过。'] if template_id == 'rotation' else []),
                 timing=add_timing, referencePrice=level['close'], quantity=None,
                 quantityNote='原始止损与全账户未平仓风险未确认，不能给出可执行加仓股数；当前只展示条件候选。'),
            dict(id='reduce', title='账户限额减仓复核', triggered=reduce_ready,
                 conditions=['单股、总名义市值或半导体名义市值超过本档上限，或账户现金为负。',
                             '只以新鲜 USD 券商价格和实际股数评估；成交前复核账户、实时价格与有效订单。'],
                 timing=TIMING, referencePrice=price if broker_valid else None, quantity=reduce_quantity,
                 quantityNote='数量为单股超额与总仓/行业按市值比例分担超额的较大值，向上取整且不超过持仓；各项是独立示例，不能相加当成清仓或成交指令。'),
            dict(id='exit', title='若采用当前规则的退出复核', triggered=exit_trigger,
                 conditions=['已完成日线收盘跌破 '+('SMA20。' if template_id == 'breakout' else 'SMA50。'),
                             '这是当前模板的退出复核条件，需确认适用于原有头寸；不冒充实际历史策略或已有止损。'],
                 timing=TIMING, referencePrice=exit_ma, quantity=None,
                 quantityNote='原始交易计划与有效止损未知，不预设全仓卖出数量。')]
        # A failed source/identity gate suppresses all triggered guidance and quantities.
        if not verified or not identity_ok or not broker_valid or (stock and not matching):
            for scenario in scenarios:
                scenario['triggered'] = False
                scenario['quantity'] = None
        explanation = {'verify': '先补齐新鲜行情、合约或风险资料；保留历史价位供核对。',
                       'review_exit': '完成日线已触及当前模板退出条件，先确认原有交易计划与有效订单。',
                       'review_reduce': '账户融资或名义市值限额需要优先复核，当前不新增风险。',
                       'watch_add': '盈利与趋势条件通过，仍需核实原有止损和账户未平仓风险后才可讨论股数。',
                       'hold': '当前未触发模板退出或限额减仓；继续按完成日线和已有保护订单复核。'}
        data_kind = 'verify' if not verified or not identity_ok or (stock and not matching) or not broker_valid else 'ready' if technical_ok else 'risk_only'
        item_issues = deepcopy(data_issues)
        if quote_bad:
            item_issues.append(_issue('position_quote_issue'))
        if date_bad:
            item_issues.append(_issue('position_date_mismatch'))
        if not identity_ok or (stock and not matching) or len(candidates) > 1:
            item_issues.append(_issue('identity_mismatch'))
        if not candidates:
            item_issues.append(_issue('position_quote_missing'))
        if identity_ok and not broker_valid:
            item_issues.append(_issue('broker_value_inconsistent'))
        if instrument in ('leveraged_etf_2x', 'etf'):
            item_issues.append(_issue('etf_risk_only'))
        elif stock and instrument == 'unverified':
            item_issues.append(_issue('instrument_unverified'))
        item_issues.extend(history_issues)
        items.append(dict(identity=dict(key=position['_key'], symbol=symbol, contractId=contract,
                                        name=stock.get('name') or symbol, currency=currency, assetClass=asset,
                                        instrumentType=instrument, quantity=quantity, averageCost=cost,
                                        brokerPrice=price, brokerMarketValue=value, unrealizedPnl=pnl,
                                        weightPct=value/nav*100 if value is not None and nav and currency == 'USD' else None,
                                        semiGroup=semi_group),
                          decision=dict(kind=kind, label=LABELS[kind], timing=TIMING, summary=explanation[kind]),
                          checks=checks, levels=levels, scenarios=scenarios, warnings=item_warnings,
                          dataStatus=dict(kind=data_kind, label={'ready':'完整日线可用', 'risk_only':'仅账户风险与部分价位', 'verify':'数据待核实'}[data_kind],
                                          bars=len(rows), marketAsOf=asof, accountFresh=account_fresh,
                                          marketFresh=market_fresh, technicalEligible=technical_ok, issues=item_issues)))
    counts = {kind: sum(x['decision']['kind'] == kind for x in items) for kind in LABELS}
    return dict(version=VERSION, generatedAt=now.isoformat(), accountAsOf=account_asof, marketAsOf=market_asof,
                expectedMarketAsOf=_latest_weekday(cutoff), dataIssues=data_issues,
                settings=dict(templateId=template_id, templateLabel=TEMPLATES[template_id]['label'],
                              riskProfile=risk_profile, riskLabel=profile['label'], allowProfitAdd=allow_profit_add,
                              parameters=parameters, caps={k: profile[k] for k in ('grossCapPct', 'singleCapPct', 'sectorCapPct', 'tradeRiskPct', 'openRiskCapPct')}),
                account=account, summary=dict(positionCount=len(items), counts=counts, addBlocked=True,
                    addBlockReasons=general_blocks+['原始止损与全账户未平仓风险未确认，加仓股数暂不生成。'],
                    headline='按真实持仓逐项复核；盈利加仓须先通过账户与原有订单核验。'),
                items=items, warnings=[
                    '本页是现有持仓的条件管理研究，使用模板默认参数；不是历史最优策略证明，也不保证未来盈利。',
                    '账户仓位、成本和净值仅随手动 IBKR 同步更新；日线行情刷新不会更新账户。',
                    '所有暴露比例为券商名义市值 / 账户净值；超过 100% 代表名义风险敞口高于净值。SKUU 未按 2 倍折算。',
                    '未知账户净值历史高点，不能推断账户历史回撤或以回测回撤阈值直接要求全部清仓。',
                    '未接入交易所日历：按纽约常规收盘时间及工作日保守检查最新日线，节假日或提前收盘无法确认时暂停候选；不预测未来交易日期。',
                    '原始行情未完整处理拆股分红；发生公司行动时先核对券商成本、合约与价格口径。'],
                sources=[dict(name='IBKR 手动账户快照', asOf=account_asof, detail='真实股数、成本、券商估值与融资字段；本函数不连接券商或下单。'),
                         dict(name='已完成公开日线', asOf=market_asof, detail='仅使用已完成日线；均线与 ATR 价位不是实时成交价。'),
                         dict(name='行情快照生成时间', asOf=market.get('generatedAt'), detail='超过 24 小时或报价错误时暂停行动触发。')])
