"""Personal plans use synthetic snapshots only; no brokerage or network access."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import unittest


NOW = datetime(2026, 11, 10, 14, tzinfo=timezone.utc)


def bars(step=.3, count=260, end=date(2026, 11, 9)):
    days, day = [], end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return [dict(date=d, open=100+i*step-.1, high=100+i*step+.2,
                 low=100+i*step-.2, close=100+i*step,
                 volume=2_000_000 if i == count-1 else 1_000_000)
            for i, d in enumerate(reversed(days))]


def fixture(held=('AAA',), candidates=('BBB', 'CCC')):
    names = list(dict.fromkeys([*held, *candidates]))
    rows = bars()
    price = rows[-1]['close']
    market = dict(generatedAt=NOW.isoformat(), marketAsOf='2026-11-09',
                  stocks=[dict(symbol=s, contractId=str(i+1), name=s, currency='USD',
                               quoteType='EQUITY', quoteStatus='ready', asOf='2026-11-09',
                               sector='other', history=deepcopy(rows)) for i, s in enumerate(names)],
                  benchmark=dict(symbol='SPY', quoteStatus='ready', asOf='2026-11-09',
                                 history=bars(.05)), quoteRefresh={'errors': []}, marketWarnings=[])
    holdings = dict(source='manual', asOf=NOW.isoformat(), account_summary=dict(
        currency='USD', net_liquidation=100_000, total_cash_value=90_000,
        available_funds=90_000, excess_liquidity=90_000,
        gross_position_value=price*10*len(held)), positions=[
            dict(contract_id=str(names.index(s)+1), symbol=s, contract_description=s,
                 position=10, average_price=100, market_price=price, market_value=price*10,
                 currency='USD', asset_class='STK', stop_price=price-5, sector='other') for s in held])
    watchlists = dict(instruments=[dict(contractId=str(names.index(s)+1), symbol=s,
                         quoteSymbol=s, currency='USD', supported=True, status='ready', groupIds=['g1'])
                         for s in candidates])
    return market, holdings, watchlists


def build(market, holdings, watchlists, **settings):
    assert importlib.util.find_spec('personal_plan') is not None, 'personal plan engine is not implemented'
    from personal_plan import build_personal_plan
    return build_personal_plan(market, holdings, watchlists, settings, now=NOW)


def item(result, symbol):
    return next(row for row in result['items'] if row['symbol'] == symbol)


class PersonalPlanTests(unittest.TestCase):
    def test_zero_allocation_preserves_all_holdings_and_disallows_new_risk(self):
        market, holdings, watchlists = fixture(('AAA', 'DDD'))
        result = build(market, holdings, watchlists, allocationPct=0)
        self.assertEqual({x['symbol'] for x in result['items'] if x['origin'] == 'holding'}, {'AAA', 'DDD'})
        self.assertEqual(result['allocation']['allocatedCapital'], 0)
        self.assertGreater(result['allocation']['overAllocatedCapital'], 0)
        self.assertTrue(all(x['quantity'] in (0, None) for x in result['items']))

    def test_unknown_stops_do_not_become_zero_risk_or_order_quantity(self):
        market, holdings, watchlists = fixture()
        holdings['positions'][0].pop('stop_price')
        result = build(market, holdings, watchlists)
        self.assertIsNone(result['account']['openRiskValue'])
        self.assertIsNone(result['account']['currentDrawdownPct'])
        self.assertIsNone(item(result, 'BBB')['quantity'])
        self.assertGreater(item(result, 'BBB')['theoreticalMaxQuantity'], 0)

    def test_manual_stops_are_planned_risk_and_sizing_uses_worst_entry_price(self):
        result = build(*fixture())
        self.assertEqual(result['account']['openRiskStatus'], 'planned')
        self.assertEqual(result['account']['openRiskValue'], 50)
        candidate = item(result, 'BBB')
        self.assertEqual(candidate['quantityStatus'], 'conditional')
        self.assertGreater(candidate['quantity'], 0)
        self.assertAlmostEqual(candidate['entry']['riskPerShare'],
                               candidate['entry']['upperPrice']-candidate['entry']['proposedStop'])
        self.assertTrue(any('券商' in warning for warning in result['warnings']))

    def test_broker_snapshot_does_not_certify_manual_stop_field(self):
        market, holdings, watchlists = fixture()
        holdings['source'] = 'IBKR'
        result = build(market, holdings, watchlists)
        self.assertIsNone(result['account']['openRiskValue'])
        self.assertIsNone(item(result, 'BBB')['quantity'])

    def test_shared_cash_is_reserved_only_once(self):
        market, holdings, watchlists = fixture(held=())
        holdings['account_summary'].update(total_cash_value=1000, available_funds=1000, excess_liquidity=1000)
        result = build(market, holdings, watchlists)
        self.assertLessEqual(result['summary']['plannedBuyValue'], 1000)
        self.assertGreater(result['summary']['plannedBuyValue'], 0)
        self.assertGreaterEqual(result['summary']['remainingCash'], 0)
        self.assertEqual(sum(x['quantity'] or 0 for x in result['items']), 5)

    def test_lower_drawdown_reduces_budget_and_higher_return_never_increases_it(self):
        snapshots = fixture(held=())
        low = build(*snapshots, maxDrawdownPct=1)
        high = build(*snapshots, maxDrawdownPct=30)
        ambitious = build(*snapshots, maxDrawdownPct=1, targetReturnPct=200)
        self.assertEqual(low['allocation']['tradeRiskBudget'], 25)
        self.assertEqual(low['allocation']['openRiskBudget'], 100)
        self.assertLess(item(low, 'BBB')['quantity'], item(high, 'BBB')['quantity'])
        self.assertEqual([x['quantity'] for x in low['items']], [x['quantity'] for x in ambitious['items']])
        self.assertEqual(ambitious['validation']['status'], 'unvalidated')

    def test_negative_cash_reduction_is_shared_and_sale_proceeds_do_not_fund_buys(self):
        market, holdings, watchlists = fixture(('AAA', 'DDD'))
        holdings['account_summary'].update(total_cash_value=-100, available_funds=10_000, excess_liquidity=10_000)
        result = build(market, holdings, watchlists)
        reductions = [x['reduction']['quantity'] or 0 for x in result['items'] if x['reduction']]
        self.assertEqual(sum(reductions), 1)
        self.assertEqual(result['summary']['plannedBuyValue'], 0)
        self.assertTrue(all(x['quantity'] in (0, None) for x in result['items']))

    def test_one_bad_symbol_does_not_block_valid_other_symbol(self):
        market, holdings, watchlists = fixture(held=())
        market['stocks'][1]['history'][-1]['volume'] = None
        market['quoteRefresh']['errors'].append(dict(symbol='CCC', message='bad quote'))
        result = build(market, holdings, watchlists)
        self.assertGreater(item(result, 'BBB')['quantity'], 0)
        self.assertIsNone(item(result, 'CCC')['quantity'])

    def test_benchmark_missing_latest_bar_blocks_all_entries(self):
        market, holdings, watchlists = fixture(held=())
        market['benchmark']['history'].pop()
        result = build(market, holdings, watchlists)
        self.assertEqual(result['summary']['plannedBuyValue'], 0)
        self.assertTrue(result['summary']['blockReasons'])

    def test_watchlist_contract_duplicates_merge_groups_but_keep_distinct_contracts(self):
        market, holdings, watchlists = fixture(held=(), candidates=('BBB',))
        same = deepcopy(watchlists['instruments'][0]); same['groupIds'] = ['g2']
        different = {**same, 'contractId': 'another'}
        watchlists['instruments'] += [same, different]
        result = build(market, holdings, watchlists)
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(set(result['items'][0]['groupIds']), {'g1', 'g2'})
        self.assertTrue(all(x['quantity'] is None for x in result['items']))

    def test_held_contract_is_not_duplicated_as_a_new_position(self):
        market, holdings, watchlists = fixture(candidates=('AAA', 'BBB'))
        result = build(market, holdings, watchlists)
        self.assertEqual(sum(x['symbol'] == 'AAA' for x in result['items']), 1)
        self.assertEqual(item(result, 'AAA')['origin'], 'holding')

    def test_non_usd_unknown_value_and_short_positions_remain_and_block_capacity(self):
        for mutation in ('currency', 'value', 'short'):
            with self.subTest(mutation=mutation):
                market, holdings, watchlists = fixture()
                if mutation == 'currency': holdings['positions'][0]['currency'] = 'EUR'
                if mutation == 'value': holdings['positions'][0].update(market_value=None, market_price=None)
                if mutation == 'short': holdings['positions'][0]['position'] = -10
                result = build(market, holdings, watchlists)
                self.assertEqual(result['summary']['holdingCount'], 1)
                self.assertIsNone(item(result, 'BBB')['quantity'])

    def test_loss_at_either_price_source_or_proposed_entry_prevents_adds(self):
        for cost, broker in ((200, 177.7), (175, 170), (177.6, 177.7)):
            with self.subTest(cost=cost, broker=broker):
                market, holdings, watchlists = fixture()
                holdings['positions'][0].update(average_price=cost, market_price=broker, market_value=broker*10)
                result = build(market, holdings, watchlists)
                self.assertIn(item(result, 'AAA')['quantity'], (None, 0))

    def test_cash_reserve_is_for_account_and_not_released_by_lower_allocation(self):
        market, holdings, watchlists = fixture(held=())
        holdings['account_summary'].update(total_cash_value=20_000, available_funds=20_000, excess_liquidity=20_000)
        result = build(market, holdings, watchlists, allocationPct=50, reserveCashPct=20)
        self.assertEqual(result['allocation']['reserveCashValue'], 20_000)
        self.assertEqual(result['summary']['plannedBuyValue'], 0)

    def test_strict_settings_reject_invalid_numbers_and_unknown_fields(self):
        for settings in ({'allocationPct': -1}, {'allocationPct': 101}, {'allocationPct': True},
                         {'maxDrawdownPct': 0}, {'targetReturnPct': float('nan')},
                         {'allowProfitAdd': 1}, {'templateId': 'breakout'}, {'unexpected': 1}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                build(*fixture(), **settings)

    def test_inputs_not_mutated_and_output_is_finite_json(self):
        snapshots = fixture()
        original = deepcopy(snapshots)
        result = build(*snapshots)
        self.assertEqual(snapshots, original)
        json.dumps(result, allow_nan=False)

    def test_old_manual_edit_does_not_stale_current_daily_valuation(self):
        market, holdings, watchlists = fixture()
        holdings['asOf'] = '2025-01-01T00:00:00Z'
        result = build(market, holdings, watchlists)
        self.assertGreater(item(result, 'BBB')['quantity'], 0)
        holdings['source'] = 'IBKR'
        self.assertEqual(build(market, holdings, watchlists)['summary']['plannedBuyValue'], 0)

    def test_manual_cash_account_does_not_need_invented_margin_metrics(self):
        market, holdings, watchlists = fixture()
        holdings['account_summary'].pop('excess_liquidity')
        result = build(market, holdings, watchlists)
        self.assertIsNone(result['account']['excessLiquidity'])
        self.assertIsNotNone(item(result, 'BBB')['quantity'])
        self.assertGreater(item(result, 'BBB')['quantity'], 0)

    def test_two_completed_closes_below_fifty_day_average_exit_but_one_does_not(self):
        market, holdings, watchlists = fixture()
        holdings['positions'][0]['stop_price'] = 90
        for index in (-2, -1):
            row = market['stocks'][0]['history'][index]
            row.update(open=150, high=151, low=149, close=150)
        result = build(market, holdings, watchlists)
        self.assertEqual(item(result, 'AAA')['status'], 'exit')
        self.assertEqual(item(result, 'AAA')['reduction']['quantity'], 10)
        self.assertEqual(result['summary']['plannedBuyValue'], 0)
        market['stocks'][0]['history'][-2] = bars()[-2]
        self.assertIsNone(item(build(market, holdings, watchlists), 'AAA')['reduction'])

    def test_exactly_two_hundred_bars_can_confirm_two_close_fifty_day_exit(self):
        market, holdings, watchlists = fixture(candidates=())
        market['stocks'][0]['history'] = bars(count=200)
        for row in market['stocks'][0]['history'][-2:]:
            row.update(open=120, high=121, low=119, close=120)
        holdings['positions'][0].update(market_price=120, market_value=1200, stop_price=90)
        holdings['account_summary']['gross_position_value'] = 1200
        result = build(market, holdings, watchlists)
        self.assertEqual(item(result, 'AAA')['status'], 'exit')
        self.assertEqual(item(result, 'AAA')['reduction']['quantity'], 10)

    def test_long_term_breakout_uses_sixty_days_including_high_older_than_twenty(self):
        market, holdings, watchlists = fixture(held=(), candidates=('BBB',))
        market['stocks'][0]['history'][-45]['high'] = 190
        result = build(market, holdings, watchlists)
        self.assertEqual(item(result, 'BBB')['quantity'], 0)
        self.assertEqual(item(result, 'BBB')['status'], 'wait')

    def test_pullback_requires_actual_fifty_day_touch(self):
        market, holdings, watchlists = fixture(held=(), candidates=('BBB',))
        self.assertEqual(item(build(market, holdings, watchlists, templateId='pullback'), 'BBB')['quantity'], 0)
        market['stocks'][0]['history'][-3]['low'] = 160
        self.assertGreater(item(build(market, holdings, watchlists, templateId='pullback'), 'BBB')['quantity'], 0)

    def test_rotation_waits_for_month_boundary_and_complete_ranking(self):
        market, holdings, watchlists = fixture(held=())
        self.assertEqual(build(market, holdings, watchlists, templateId='rotation')['summary']['plannedBuyValue'], 0)
        from personal_plan import build_personal_plan
        month_start = datetime(2026, 11, 2, 14, tzinfo=timezone.utc)
        market['generatedAt'] = month_start.isoformat()
        for quote in market['stocks']:
            quote.update(history=bars(end=date(2026, 10, 30)), asOf='2026-10-30')
        market['benchmark'].update(history=bars(.05, end=date(2026, 10, 30)), asOf='2026-10-30')
        result = build_personal_plan(market, holdings, watchlists, {'templateId': 'rotation'}, now=month_start)
        self.assertGreater(result['summary']['plannedBuyValue'], 0)
        market['stocks'][1]['history'] = []
        result = build_personal_plan(market, holdings, watchlists, {'templateId': 'rotation'}, now=month_start)
        self.assertEqual(result['summary']['plannedBuyValue'], 0)

    def test_manual_symbol_does_not_hide_two_distinct_watchlist_contracts(self):
        market, holdings, watchlists = fixture(candidates=('AAA',))
        holdings['positions'][0].pop('contract_id')
        watchlists['instruments'].append({**watchlists['instruments'][0], 'contractId': 'another'})
        result = build(market, holdings, watchlists)
        self.assertEqual(len(result['items']), 3)
        self.assertTrue(all(x['quantity'] is None for x in result['items']))

    def test_conflicting_stops_on_duplicate_contract_do_not_certify_account_risk(self):
        market, holdings, watchlists = fixture()
        duplicate = {**holdings['positions'][0], 'stop_price': 100}
        holdings['positions'].append(duplicate)
        holdings['account_summary']['gross_position_value'] *= 2
        result = build(market, holdings, watchlists)
        self.assertEqual(result['summary']['holdingCount'], 1)
        self.assertEqual(item(result, 'AAA')['holding']['quantity'], 20)
        self.assertIsNone(result['account']['openRiskValue'])
        self.assertIsNone(item(result, 'BBB')['quantity'])

    def test_aggregate_market_date_from_unrelated_stale_symbol_does_not_block(self):
        market, holdings, watchlists = fixture(held=())
        market['marketAsOf'] = '2026-10-01'
        result = build(market, holdings, watchlists)
        self.assertGreater(item(result, 'BBB')['quantity'], 0)

    def test_missing_benchmark_does_not_crash_or_create_quantity(self):
        market, holdings, watchlists = fixture()
        market['benchmark'] = None
        result = build(market, holdings, watchlists)
        self.assertEqual(result['summary']['plannedBuyValue'], 0)
        self.assertFalse(result['freshness']['marketFresh'])

    def test_unknown_sector_does_not_grant_semiconductor_capacity(self):
        market, holdings, watchlists = fixture()
        holdings['positions'][0]['sector'] = 'unknown'
        result = build(market, holdings, watchlists)
        self.assertIsNone(item(result, 'BBB')['quantity'])

    def test_sourced_sector_fills_an_unspecified_manual_sector_without_changing_the_account(self):
        market, holdings, watchlists = fixture()
        holdings['positions'][0]['sector'] = 'unknown'
        market['stocks'][0].update(sectorClass='other', metadataEvidence={
            'source': 'SEC submissions', 'url': 'https://data.sec.gov/submissions/CIK0000000001.json',
            'checkedAt': NOW.isoformat(), 'sic': '7372'})
        result = build(market, holdings, watchlists)
        self.assertIsNotNone(item(result, 'BBB')['quantity'])
        self.assertGreater(item(result, 'BBB')['quantity'], 0)
        self.assertEqual(holdings['positions'][0]['sector'], 'unknown')

    def test_unsourced_sector_hint_does_not_certify_an_unknown_manual_holding(self):
        market, holdings, watchlists = fixture()
        holdings['positions'][0]['sector'] = 'unknown'
        market['stocks'][0]['sectorClass'] = 'other'
        result = build(market, holdings, watchlists)
        self.assertIsNone(item(result, 'BBB')['quantity'])

    def test_known_fund_is_strategy_inapplicable_instead_of_unresolved_identity(self):
        for fields in ({'quoteType': 'ETF'}, {'securityKind': 'fund'}):
            with self.subTest(fields=fields):
                market, holdings, watchlists = fixture(held=(), candidates=('BBB',))
                market['stocks'][0].update(fields)
                row = item(build(market, holdings, watchlists), 'BBB')
                self.assertEqual(row.get('verification', {}).get('state'), 'unsupported')
                self.assertIsNone(row['quantity'])
                self.assertIn('策略不适用', row['label'])
                self.assertFalse(any('身份或多头方向未唯一核实' in reason for reason in row['blockReasons']))

    def test_unsupported_holdings_keep_valuation_but_have_no_strategy_entry_or_sizing(self):
        for fields in ({'quoteType': 'ETF'}, {'securityKind': 'fund'},
                       {'securityKind': 'shell'}, {'currency': 'HKD'}):
            with self.subTest(fields=fields):
                market, holdings, watchlists = fixture()
                market['stocks'][0].update(fields)
                result = build(market, holdings, watchlists)
                row = item(result, 'AAA')
                self.assertIsNone(row['entry'])
                self.assertIsNone(row['theoreticalMaxQuantity'])
                self.assertEqual(row['sizingLimits'], [])
                self.assertIsNone(row['quantity'])
                self.assertEqual(row['holding']['quantity'], 10)
                self.assertEqual(row['holding']['marketValue'], holdings['positions'][0]['market_value'])
                self.assertIsNone(result['account']['openRiskValue'])
                self.assertIsNone(item(result, 'BBB')['quantity'])

    def test_verified_shell_company_is_excluded_even_with_enough_history(self):
        market, holdings, watchlists = fixture(held=(), candidates=('BBB',))
        market['stocks'][0]['securityKind'] = 'shell'
        row = item(build(market, holdings, watchlists), 'BBB')
        self.assertEqual(row.get('verification', {}).get('state'), 'unsupported')
        self.assertIsNone(row['quantity'])
        self.assertTrue(any('壳公司' in reason for reason in row['blockReasons']))

    def test_known_non_usd_security_does_not_look_like_an_unresolved_contract(self):
        market, holdings, watchlists = fixture(held=(), candidates=('BBB',))
        market['stocks'][0]['currency'] = 'HKD'
        watchlists['instruments'][0]['currency'] = 'HKD'
        row = item(build(market, holdings, watchlists), 'BBB')
        self.assertEqual(row.get('verification', {}).get('state'), 'unsupported')
        self.assertIsNone(row['quantity'])
        self.assertTrue(any('HKD' in reason for reason in row['blockReasons']))

    def test_imported_out_of_scope_contract_keeps_its_verified_exclusion_without_quote(self):
        market, holdings, watchlists = fixture(held=(), candidates=('BBB',))
        market['stocks'] = []
        watchlists['instruments'][0].update(symbol='USD.CNH', quoteSymbol=None, currency=None,
            supported=False, status='unsupported', reason='外汇合约已导入；当前 USD 股票日线模块不支持此合约。')
        row = item(build(market, holdings, watchlists), 'USD.CNH')
        self.assertEqual(row.get('verification', {}).get('state'), 'unsupported')
        self.assertIsNone(row['quantity'])
        self.assertIn('外汇', row['rationale'])

    def test_short_valid_history_reports_observed_bars_without_synthesizing_or_relaxing_rules(self):
        market, holdings, watchlists = fixture(held=(), candidates=('BBB',))
        market['stocks'][0]['history'] = bars(count=105)
        row = item(build(market, holdings, watchlists), 'BBB')
        self.assertEqual(row.get('verification', {}).get('state'), 'insufficient_history')
        self.assertEqual(row['verification']['availableBars'], 105)
        self.assertEqual(row['verification']['requiredBars'], 200)
        self.assertIsNone(row['entry'])
        self.assertIsNone(row['quantity'])

    def test_complete_security_reports_account_gate_separately_from_its_own_data(self):
        market, holdings, watchlists = fixture()
        holdings['positions'][0].pop('stop_price')
        row = item(build(market, holdings, watchlists), 'BBB')
        self.assertEqual(row.get('verification', {}).get('state'), 'account_required')
        self.assertIn('账户', row['label'])
        self.assertIsNone(row['quantity'])
        self.assertTrue(any('AAA' in reason and '止损' in reason for reason in row['blockReasons']))

    def test_missing_and_malformed_histories_are_data_errors_not_known_short_listings(self):
        for history in (None, [], [dict(date='2026-11-09', close=100)]):
            with self.subTest(history=history):
                market, holdings, watchlists = fixture(held=(), candidates=('BBB',))
                market['stocks'][0]['history'] = history
                row = item(build(market, holdings, watchlists), 'BBB')
                self.assertEqual(row.get('verification', {}).get('state'), 'needs_data')
                self.assertIsNone(row['quantity'])

    def test_reduced_drawdown_budget_trims_existing_stop_risk_on_one_shared_ledger(self):
        market, holdings, watchlists = fixture(('AAA', 'DDD'))
        for position in holdings['positions']: position['stop_price'] = 100
        result = build(market, holdings, watchlists, maxDrawdownPct=1)
        sold = sum(r['reduction']['quantity'] for r in result['items'] if r['reduction'])
        self.assertEqual(sold, 19)
        self.assertEqual(result['summary']['plannedBuyValue'], 0)

    def test_stale_fallback_manual_valuation_retains_position_but_not_risk_capacity(self):
        market, holdings, watchlists = fixture()
        holdings['positions'][0].update(valuationSource='manual_mark', valuationAsOf='2025-01-01T00:00:00Z')
        result = build(market, holdings, watchlists)
        self.assertEqual(result['summary']['holdingCount'], 1)
        self.assertIsNone(item(result, 'BBB')['quantity'])

    def test_stale_daily_valuation_in_a_holding_does_not_certify_account_capacity(self):
        market, holdings, watchlists = fixture()
        holdings['positions'][0].update(valuationSource='daily_close', valuationAsOf='2026-11-06')
        result = build(market, holdings, watchlists)
        self.assertIsNone(item(result, 'BBB')['quantity'])

    def test_missing_or_invalid_positions_are_unknown_not_an_empty_confirmed_account(self):
        for missing in ('absent', None, {}, [None]):
            with self.subTest(missing=missing):
                market, holdings, watchlists = fixture(held=())
                if missing == 'absent': holdings.pop('positions')
                else: holdings['positions'] = missing
                result = build(market, holdings, watchlists)
                self.assertIsNone(result['account']['openRiskValue'])
                self.assertEqual(result['summary']['plannedBuyValue'], 0)

    def test_pullback_needs_sma200_for_all_ten_observations_and_isolates_short_ticker(self):
        market, holdings, watchlists = fixture(held=())
        market['stocks'][0]['history'] = bars(count=200)
        market['stocks'][1]['history'][-3]['low'] = 160
        result = build(market, holdings, watchlists, templateId='pullback')
        self.assertIsNone(item(result, 'BBB')['quantity'])
        self.assertGreater(item(result, 'CCC')['quantity'], 0)


if __name__ == '__main__':
    unittest.main()
