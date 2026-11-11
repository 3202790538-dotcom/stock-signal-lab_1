"""Actual-position management gates; deterministic local data and no account I/O."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import unittest

NOW = datetime(2026, 11, 10, 14, tzinfo=timezone.utc)
SYMBOLS = ['NVDA', 'AVGO', 'FMAC', 'GOOG', 'IBKR', 'MRVL', 'MU', 'SKHY', 'SKUU', 'SPCX']


def bars(count=220, step=.3):
    days = []
    day = date(2026, 11, 9)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return [dict(date=day, open=100+i*step-.1, high=100+i*step+.1,
                 low=100+i*step-.2, close=100+i*step,
                 volume=2_000_000 if i == count-1 else 1_000_000)
            for i, day in enumerate(reversed(days))]


def fixture(symbols=('NVDA',)):
    holdings = {'asOf': NOW.isoformat(), 'account_summary': {
        'currency': 'USD', 'net_liquidation': 100_000, 'total_cash_value': 80_000,
        'available_funds': 80_000, 'excess_liquidity': 80_000,
        'gross_position_value': len(symbols)*1657}, 'positions': [
        dict(contract_id=i+1, contract_description=s, position=10, currency='USD',
             asset_class='STK', average_price=100, market_price=165.7,
             market_value=1657, unrealized_pnl=657) for i, s in enumerate(symbols)]}
    market = {'generatedAt': NOW.isoformat(), 'marketAsOf': '2026-11-09',
              'stocks': [dict(symbol=s, currency='USD', quoteType='EQUITY', quoteStatus='ready',
                              asOf='2026-11-09', history=bars()) for s in symbols],
              'benchmark': dict(symbol='SPY', asOf='2026-11-09', quoteStatus='ready',
                                history=bars(step=.05)), 'quoteRefresh': {'errors': []},
              'marketWarnings': []}
    return market, holdings


def build(market, holdings, **kwargs):
    # A missing implementation produces an explicit assertion failure for the RED run.
    assert importlib.util.find_spec('holdings_plan') is not None, 'holdings plan backend is not implemented'
    from holdings_plan import build_holdings_plan
    return build_holdings_plan(market, holdings, now=NOW, **kwargs)


def scenario(item, name):
    return next(row for row in item['scenarios'] if row['id'] == name)


class HoldingsPlanTests(unittest.TestCase):
    def test_missing_latest_daily_and_refresh_failure_explain_verify_without_blame_on_fresh_account(self):
        from holdings_plan import build_holdings_plan
        market, holdings = fixture()
        later = datetime(2026, 11, 11, 1, 23, tzinfo=timezone.utc)
        market['generatedAt'] = later.isoformat()
        result = build_holdings_plan(market, holdings, now=later, market_issue='行情刷新失败')
        self.assertEqual(result.get('expectedMarketAsOf'), '2026-11-10')
        self.assertEqual({x['code'] for x in result.get('dataIssues', [])},
                         {'latest_bar_missing', 'market_refresh_issue'})
        item = result['items'][0]
        self.assertEqual(item['decision']['kind'], 'verify')
        self.assertTrue(item['dataStatus']['accountFresh'])
        self.assertEqual({x['code'] for x in item['dataStatus'].get('issues', [])},
                         {'latest_bar_missing', 'market_refresh_issue'})
        self.assertTrue(all(x['label'] and x['action'] for x in result['dataIssues']))

    def test_data_age_and_quote_reasons_match_the_gate_that_failed(self):
        for cause, code in [('account', 'account_stale'), ('generation', 'market_stale'),
                            ('account_missing', 'account_timestamp_invalid'),
                            ('generation_missing', 'market_timestamp_invalid'),
                            ('quote', 'position_quote_issue'), ('core', 'core_quote_issue')]:
            with self.subTest(cause=cause):
                market, holdings = fixture()
                if cause == 'account': holdings['asOf'] = '2026-11-08T00:00:00Z'
                if cause == 'generation': market['generatedAt'] = '2026-11-08T00:00:00Z'
                if cause == 'account_missing': holdings.pop('asOf')
                if cause == 'generation_missing': market.pop('generatedAt')
                if cause == 'quote': market['stocks'][0]['quoteStatus'] = 'stale'
                if cause == 'core': market['quoteRefresh']['errors'] = [{'symbol': 'SPY'}]
                result = build(market, holdings)
                item = result['items'][0]
                self.assertEqual(item['decision']['kind'], 'verify')
                self.assertIn(code, {x['code'] for x in item['dataStatus'].get('issues', [])})

    def test_history_reasons_distinguish_absence_sample_volume_and_calendar(self):
        for cause, code in [('missing', 'history_missing'), ('sample', 'history_insufficient'),
                            ('volume', 'history_volume_invalid'), ('calendar', 'history_calendar_mismatch'),
                            ('invalid', 'history_invalid')]:
            with self.subTest(cause=cause):
                market, holdings = fixture()
                rows = market['stocks'][0]['history']
                if cause == 'missing': rows.clear()
                if cause == 'sample': del rows[:-30]
                if cause == 'volume': rows[30]['volume'] = None
                if cause == 'calendar': rows.pop(30)
                if cause == 'invalid': rows[30]['low'] = rows[30]['high']+1
                item = build(market, holdings)['items'][0]
                self.assertFalse(item['dataStatus']['technicalEligible'])
                self.assertIn(code, {x['code'] for x in item['dataStatus'].get('issues', [])})

    def test_identity_broker_and_etf_explanations_preserve_existing_scope(self):
        for cause, code, data_kind in [('identity', 'identity_mismatch', 'verify'),
                                       ('value', 'broker_value_inconsistent', 'verify'),
                                       ('etf', 'etf_risk_only', 'risk_only'),
                                       ('unknown', 'instrument_unverified', 'risk_only')]:
            with self.subTest(cause=cause):
                market, holdings = fixture()
                if cause == 'identity': market['stocks'][0]['contract_id'] = 999
                if cause == 'value': holdings['positions'][0]['market_value'] = 100
                if cause == 'etf': market['stocks'][0]['quoteType'] = 'ETF'
                if cause == 'unknown': market['stocks'][0]['quoteType'] = 'UNKNOWN'
                item = build(market, holdings)['items'][0]
                self.assertEqual(item['dataStatus']['kind'], data_kind)
                self.assertIn(code, {x['code'] for x in item['dataStatus'].get('issues', [])})
                self.assertFalse(scenario(item, 'add')['triggered'])

    def test_unknown_real_stops_are_only_sizing_gaps_not_data_failures(self):
        market, holdings = fixture()
        result = build(market, holdings)
        self.assertEqual(result.get('dataIssues'), [])
        item = result['items'][0]
        self.assertEqual(item['dataStatus'].get('issues'), [])
        self.assertEqual(item['decision']['kind'], 'watch_add')
        self.assertIsNone(scenario(item, 'add')['quantity'])

    def test_all_actual_holdings_survive_without_watchlist_candidates(self):
        market, holdings = fixture(SYMBOLS)
        market['stocks'].append(dict(symbol='AAPL', history=bars()))
        result = build(market, holdings)
        self.assertEqual({x['identity']['symbol'] for x in result['items']}, set(SYMBOLS))
        self.assertEqual(result['summary']['positionCount'], 10)
        self.assertTrue(all({s['id'] for s in item['scenarios']} == {'hold', 'add', 'reduce', 'exit'} for item in result['items']))

    def test_profitable_candidate_requires_both_price_sources_above_cost_and_unknown_stops_prevent_sizing(self):
        market, holdings = fixture()
        result = build(market, holdings)
        item = result['items'][0]
        self.assertEqual(item['decision']['kind'], 'watch_add')
        self.assertIsNone(scenario(item, 'add')['quantity'])
        self.assertFalse(result['account']['openRiskConfirmed'])
        for cost, broker in [(170, 165.7), (160, 155)]:
            holdings['positions'][0].update(average_price=cost, market_price=broker, market_value=broker*10)
            item = build(market, holdings)['items'][0]
            self.assertNotEqual(item['decision']['kind'], 'watch_add')
            self.assertFalse(scenario(item, 'add')['triggered'])

    def test_account_financing_or_exposure_limits_block_every_add(self):
        for change in ({'total_cash_value': -1}, {'available_funds': 0}, {'excess_liquidity': 0},
                       {'gross_position_value': 110_000}, {'net_liquidation': 1000}):
            with self.subTest(change=change):
                market, holdings = fixture()
                holdings['account_summary'].update(change)
                result = build(market, holdings)
                self.assertTrue(result['summary']['addBlocked'])
                self.assertFalse(scenario(result['items'][0], 'add')['triggered'])

    def test_semiconductor_cap_includes_new_names_and_two_x_etf_nominal_value(self):
        market, holdings = fixture(('NVDA', 'MRVL', 'AVGO', 'MU', 'SKHY', 'SKUU', 'GOOG'))
        holdings['account_summary'].update(net_liquidation=16_000)
        result = build(market, holdings)
        self.assertAlmostEqual(result['account']['semiconductorValue'], 9942)
        self.assertAlmostEqual(result['account']['semiconductorExposurePct'], 62.1375)
        skuu = next(x for x in result['items'] if x['identity']['symbol'] == 'SKUU')
        self.assertEqual(skuu['identity']['instrumentType'], 'leveraged_etf_2x')
        self.assertFalse(scenario(skuu, 'add')['triggered'])
        self.assertEqual(skuu['dataStatus']['kind'], 'risk_only')

    def test_missing_short_or_invalid_volume_history_cannot_generate_entry(self):
        for history in ([], bars(30), [{**row, 'volume': None} for row in bars()]):
            market, holdings = fixture()
            market['stocks'][0]['history'] = history
            item = build(market, holdings)['items'][0]
            self.assertFalse(item['dataStatus']['technicalEligible'])
            self.assertFalse(scenario(item, 'add')['triggered'])
        market, holdings = fixture()
        market['stocks'] = []
        self.assertEqual(len(build(market, holdings)['items']), 1)

    def test_stale_sources_errors_or_calendar_mismatch_keep_levels_but_block_actions(self):
        for mutation in ('account', 'market', 'status', 'core', 'date', 'issue'):
            with self.subTest(mutation=mutation):
                market, holdings = fixture()
                kw = {}
                if mutation == 'account': holdings['asOf'] = '2026-11-08T00:00:00Z'
                if mutation == 'market': market['generatedAt'] = '2026-11-08T00:00:00Z'
                if mutation == 'status': market['stocks'][0]['quoteStatus'] = 'stale'
                if mutation == 'core': market['quoteRefresh']['errors'] = [{'symbol':'SPY', 'message':'offline'}]
                if mutation == 'date': market['stocks'][0]['history'].pop()
                if mutation == 'issue': kw['market_issue'] = '行情刷新失败'
                item = build(market, holdings, **kw)['items'][0]
                self.assertEqual(item['decision']['kind'], 'verify')
                self.assertFalse(any(s['triggered'] for s in item['scenarios']))
                self.assertTrue(any(v['value'] is not None for v in item['levels']))

    def test_browser_expiry_uses_earliest_underlying_account_source(self):
        market, holdings = fixture()
        holdings['retrievalTimes'] = {'positions': NOW.isoformat(),
                                     'account_summary': '2026-11-09T14:01:00+00:00'}
        result = build(market, holdings)
        self.assertEqual(result['account'].get('effectiveAsOf'), '2026-11-09T14:01:00+00:00')
        holdings['retrievalTimes']['account_summary'] = '2026-11-09T13:59:00+00:00'
        result = build(market, holdings)
        self.assertFalse(result['account']['fresh'])
        self.assertIsNone(result['account'].get('effectiveAsOf'))

    def test_cap_reduce_quantity_uses_broker_price_and_never_exceeds_held(self):
        market, holdings = fixture()
        holdings['account_summary'].update(net_liquidation=4000, gross_position_value=1657)
        item = build(market, holdings)['items'][0]
        # Single-name cap = $1000; $657 / $165.70 needs four whole shares.
        self.assertEqual(item['decision']['kind'], 'review_reduce')
        self.assertEqual(scenario(item, 'reduce')['quantity'], 4)
        self.assertEqual(scenario(item, 'reduce')['referencePrice'], 165.7)
        holdings['positions'][0]['position'] = .5
        holdings['positions'][0]['market_value'] = 82.85
        holdings['account_summary'].update(net_liquidation=10, gross_position_value=82.85)
        item = build(market, holdings)['items'][0]
        self.assertLessEqual(scenario(item, 'reduce')['quantity'], .5)

    def test_fresh_generation_cannot_relabel_old_daily_prices_as_current(self):
        market, holdings = fixture()
        for stock in [*market['stocks'], market['benchmark']]:
            for row in stock['history']:
                row['date'] = (date.fromisoformat(row['date'])-timedelta(days=28)).isoformat()
            stock['asOf'] = stock['history'][-1]['date']
        market['marketAsOf'] = market['benchmark']['asOf']
        item = build(market, holdings)['items'][0]
        self.assertEqual(item['decision']['kind'], 'verify')
        self.assertFalse(any(s['triggered'] for s in item['scenarios']))
        self.assertEqual(item['dataStatus']['marketAsOf'], '2026-10-12')
        self.assertIsNotNone(item['levels'][0]['value'])

    def test_missing_recent_weekday_close_requires_calendar_verification(self):
        market, holdings = fixture()
        for stock in [*market['stocks'], market['benchmark']]:
            stock['history'].pop()
            stock['asOf'] = stock['history'][-1]['date']
        market['marketAsOf'] = market['benchmark']['asOf']
        self.assertEqual(market['marketAsOf'], '2026-11-06')
        item = build(market, holdings)['items'][0]
        self.assertEqual(item['decision']['kind'], 'verify')
        self.assertFalse(any(s['triggered'] for s in item['scenarios']))

    def test_technical_exit_is_current_rule_review_without_fabricated_stop_or_entry_date(self):
        market, holdings = fixture()
        market['stocks'][0]['history'][-1].update(open=110, close=110, high=111, low=109)
        item = build(market, holdings)['items'][0]
        self.assertEqual(item['decision']['kind'], 'review_exit')
        self.assertTrue(scenario(item, 'exit')['triggered'])
        self.assertTrue(any(v['label'] == '参考防守价（收盘 − 2ATR）' for v in item['levels']))
        self.assertTrue(any('原始' in w and '止损' in w for w in item['warnings']))
        self.assertIsNone(scenario(item, 'exit')['quantity'])

    def test_fx_short_etf_and_contract_mismatch_require_identity_review(self):
        for mutation in ('fx', 'short', 'etf', 'contract'):
            market, holdings = fixture()
            if mutation == 'fx': holdings['positions'][0]['currency'] = 'HKD'
            if mutation == 'short': holdings['positions'][0]['position'] = -10
            if mutation == 'etf': market['stocks'][0]['quoteType'] = 'ETF'
            if mutation == 'contract': market['stocks'][0]['contract_id'] = 999
            item = build(market, holdings)['items'][0]
            self.assertFalse(scenario(item, 'add')['triggered'])
            self.assertFalse(item['dataStatus']['technicalEligible'])

    def test_in_progress_bar_cannot_drive_an_exit(self):
        market, holdings = fixture()
        market['stocks'][0]['history'].append(dict(date='2026-11-10', open=1, high=1, low=1, close=1, volume=1))
        item = build(market, holdings)['items'][0]
        self.assertFalse(scenario(item, 'exit')['triggered'])
        self.assertEqual(item['dataStatus']['marketAsOf'], '2026-11-09')

    def test_rotation_remains_weekly_conditional_and_disable_add_is_respected(self):
        market, holdings = fixture()
        result = build(market, holdings, template_id='rotation')
        self.assertIn('每周', scenario(result['items'][0], 'add')['timing'])
        self.assertFalse(scenario(result['items'][0], 'add')['triggered'])
        item = build(market, holdings, allow_profit_add=False)['items'][0]
        self.assertFalse(scenario(item, 'add')['triggered'])

    def test_duplicate_contracts_merge_once_without_mutating_inputs_or_emitting_nan(self):
        market, holdings = fixture()
        holdings['positions'].append(deepcopy(holdings['positions'][0]))
        original = deepcopy((market, holdings))
        result = build(market, holdings)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['identity']['quantity'], 20)
        self.assertEqual((market, holdings), original)
        json.dumps(result, allow_nan=False)

    def test_invalid_settings_rejected_and_empty_holdings_remain_empty(self):
        market, holdings = fixture(())
        self.assertEqual(build(market, holdings)['items'], [])
        for kwargs in ({'template_id':'missing'}, {'risk_profile':'missing'}, {'allow_profit_add':'true'}):
            with self.assertRaises(ValueError): build(market, holdings, **kwargs)


if __name__ == '__main__': unittest.main()
