"""Snapshot-based account risk; no broker margin or liquidation simulation.

IBKR securities excess liquidity = Equity with Loan Value - maintenance margin:
https://www.ibkrguides.com/ibkrdesktop/available-for-trading.htm
IBKR margin cushion = excess liquidity / net liquidation value:
https://www.interactivebrokers.com/campus/glossary-terms/margin-alerts/

Scenarios hold current maintenance margin fixed, change only covered stock
values, and leave other assets and currencies unchanged. They are hypothetical
account arithmetic, not IBKR's future margin requirements or liquidation line.
"""
import math


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _ratio(numerator, denominator):
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return _number(numerator / denominator)


def _percent(numerator, denominator):
    ratio = _ratio(numerator, denominator)
    return _number(ratio * 100) if ratio is not None else None


def _difference(left, right):
    return _number(left - right) if left is not None and right is not None else None


def _plus(left, right):
    return _number(left + right) if left is not None and right is not None else None


def build_account_risk(snapshot: dict) -> dict:
    """Normalize IBKR metrics while retaining missingness and reported values."""
    summary = snapshot.get('account_summary') or {}
    currency = summary.get('currency') or None
    fields = {
        'netLiquidation': 'net_liquidation',
        'equityWithLoanValue': 'equity_with_loan_value',
        'initialMargin': 'initial_margin',
        'maintenanceMargin': 'maintenance_margin',
        'availableFunds': 'available_funds',
        'excessLiquidity': 'excess_liquidity',
        'buyingPower': 'buying_power',
        'grossPositionValue': 'gross_position_value',
        'cash': 'total_cash_value',
        'leverage': 'leverage',
    }
    result = {key: _number(summary.get(source)) for key, source in fields.items()}
    result.update({'asOf': snapshot.get('asOf'), 'currency': currency})
    nav, equity = result['netLiquidation'], result['equityWithLoanValue']
    computed_excess = _difference(equity, result['maintenanceMargin'])
    computed_available = _difference(equity, result['initialMargin'])
    result['reconciliation'] = {
        'computedExcess': computed_excess,
        'difference': _difference(result['excessLiquidity'], computed_excess),
        'computedAvailable': computed_available,
        'availableDifference': _difference(result['availableFunds'], computed_available),
    }
    for key, computed in [('excessLiquidity', computed_excess),
                          ('availableFunds', computed_available)]:
        source = 'broker' if result[key] is not None else 'missing'
        if source == 'missing' and computed is not None:
            result[key], source = computed, 'derived'
        result[f'{key}Source'] = source
    if nav is None or nav <= 0:
        result['leverage'] = None
    elif result['leverage'] is None:
        result['leverage'] = _ratio(result['grossPositionValue'], nav)
    result['cushionPct'] = _percent(result['excessLiquidity'], nav)
    result['maintenanceUsagePct'] = _percent(result['maintenanceMargin'], equity)
    result['cashDebtRatioPct'] = _percent(
        max(-result['cash'], 0) if result['cash'] is not None else None, nav)
    warnings = []

    # BASE is an account aggregate, not an extra currency borrowing.
    raw_balances = snapshot.get('balances')
    if isinstance(raw_balances, dict):
        raw_balances = raw_balances.get('balances')
    result['cashBalancesAvailable'] = isinstance(raw_balances, list)
    currency_balances = {}
    for row in raw_balances or []:
        if not isinstance(row, dict):
            continue
        ccy = row.get('currency')
        if ccy and ccy != 'BASE':
            currency_balances[ccy] = row
    exchange_rates = {currency: 1.0} if currency else {}
    balances = []
    for ccy, row in currency_balances.items():
        rate = 1.0 if ccy == currency else _number(row.get('exchange_rate'))
        if rate is not None and rate <= 0:
            rate = None
        if rate is not None:
            exchange_rates[ccy] = rate
        cash, settled = _number(row.get('cash_balance')), _number(row.get('settled_cash'))
        debit = max(-settled, 0) if settled is not None else None
        balances.append({
            'currency': ccy, 'cash': cash, 'settledCash': settled,
            'debit': debit, 'currentDebit': max(-cash, 0) if cash is not None else None,
            'exchangeRate': rate,
            'baseDebit': _number(debit * rate) if debit is not None and rate is not None else None,
        })
    result['cashBalances'] = balances
    result['debtByCurrencyAvailable'] = bool(balances) and all(
        balance['debit'] is not None for balance in balances)
    if not result['debtByCurrencyAvailable']:
        warnings.append('缺少完整的各币种已结算现金，不能确认全部融资计息本金。')

    raw_positions = snapshot.get('positions')
    if isinstance(raw_positions, dict):
        raw_positions = raw_positions.get('positions')
    positions_available = isinstance(raw_positions, list)
    result['positionsAvailable'] = positions_available
    positions, omitted = [], []
    exposure, covered_gross, covered_count = 0.0, 0.0, 0
    for row in raw_positions or []:
        if not isinstance(row, dict):
            continue
        symbol = row.get('contract_description') or '未知证券'
        asset_class, ccy = row.get('asset_class'), row.get('currency')
        market_value = _number(row.get('market_value'))
        rate = exchange_rates.get(ccy)
        base_value = _number(market_value * rate) if market_value is not None and rate is not None else None
        covered = asset_class == 'STK' and base_value is not None
        positions.append({
            'symbol': symbol, 'assetClass': asset_class, 'currency': ccy,
            'quantity': _number(row.get('position')), 'marketValue': market_value,
            'baseMarketValue': base_value, 'marketPrice': _number(row.get('market_price')),
            'pnl': _number(row.get('unrealized_pnl')), 'weightPct': _percent(base_value, nav),
            'covered': covered,
        })
        if covered:
            exposure += base_value
            covered_gross += abs(base_value)
            covered_count += 1
        else:
            reason = ('非股票资产未作线性价格压力推算' if asset_class != 'STK'
                      else '缺少市值或对账户基币的有效汇率')
            omitted.append({'symbol': symbol, 'assetClass': asset_class,
                            'currency': ccy, 'reason': reason})
    result['allPositions'], result['omittedPositions'] = positions, omitted
    # Separate connector reads are not atomic. Show, rather than conceal, drift.
    all_stock_values = positions_available and all(
        position['assetClass'] == 'STK' and position['baseMarketValue'] is not None
        for position in positions)
    gross_from_positions = _number(sum(abs(p['baseMarketValue']) for p in positions)) if all_stock_values else None
    gross_difference = _difference(gross_from_positions, result['grossPositionValue'])
    result['reconciliation'].update({
        'positionsGrossValue': gross_from_positions,
        'grossValueDifference': gross_difference,
    })
    if gross_difference is not None and abs(gross_difference) > 1:
        warnings.append('逐项持仓市值合计与账户总持仓价值有差额，分次读取可能存在价格或时间差；压力损益采用逐项持仓，不将两者强行配平。')
    result['stockExposure'] = _number(exposure) if positions_available else None
    result['stressCoveragePct'] = _percent(covered_gross, result['grossPositionValue']) if positions_available else None
    if not positions_available:
        warnings.append('缺少完整账户持仓，无法计算股票压力情景。')
    if omitted:
        warnings.append(f'{len(omitted)} 项持仓未纳入股票压力计算，其价格风险与保证金变化未估计。')
    if nav is not None and nav <= 0:
        warnings.append('账户净资产不为正，比率与杠杆不作正常解释。')
    for difference_key in ['difference', 'availableDifference']:
        diff = result['reconciliation'][difference_key]
        if diff is not None and abs(diff) > 1:
            warnings.append('券商直接返回值与证券账户简单公式存在差额；保留券商值，可能存在分段口径或快照时间差。')
            break
    result['warnings'] = warnings
    result['scenarios'] = []
    if covered_count:
        for shock in [-5, -10, -20, -30]:
            pnl = _number(exposure * shock / 100)
            scenario_nav = _plus(nav, pnl)
            scenario_gross = _plus(result['grossPositionValue'], _number(covered_gross * shock / 100))
            scenario_excess = _plus(result['excessLiquidity'], pnl)
            result['scenarios'].append({
                'shockPct': shock, 'pnl': pnl, 'netLiquidation': scenario_nav,
                'equityWithLoanValue': _plus(equity, pnl),
                'excessLiquidity': scenario_excess,
                'cushionPct': _percent(scenario_excess, scenario_nav),
                'leverage': _ratio(scenario_gross, scenario_nav),
            })
    return result


def estimate_interest(principal, annual_rate_pct, days, basis=360):
    """Simple estimate with explicit rate and day basis, not IBKR tier pricing."""
    values = [_number(value) for value in [principal, annual_rate_pct, days]]
    if any(value is None or value < 0 for value in values) or basis not in (360, 365):
        raise ValueError('本金、年利率和天数须为有限非负数；年基准须为360或365。')
    principal, rate, days = values
    result = _number(principal * (rate / 100) * (days / basis))
    if result is None:
        raise ValueError('利息估算超出可计算范围。')
    return result
