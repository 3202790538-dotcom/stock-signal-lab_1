import copy
import math
import unittest
from unittest.mock import patch

from portfolio_study import build_portfolio_study, prepare_history, rank_candidate, buy_hold
from strategy_rules import candidate_parameters, get_profile, rules_catalog


class RuleStudyTests(unittest.TestCase):
    def test_risk_goal_never_overrides_caps(self):
        p = get_profile('aggressive')
        self.assertEqual((p['tradeRiskPct'], p['singleCapPct'], p['sectorCapPct']), (.75, 25, 60))
        p['grossCapPct'] = 900
        self.assertEqual(get_profile('aggressive')['grossCapPct'], 100)
        for key in ('breakout', 'pullback', 'rotation'):
            self.assertEqual(len(candidate_parameters(key)), 6)
        for bad in ('unknown', None):
            with self.assertRaises(ValueError): get_profile(bad)

    def test_selection_prioritizes_risk_before_return(self):
        candidates = [
            {'annualReturnPct': 100, 'maxDrawdownPct': 31},
            {'annualReturnPct': 49, 'maxDrawdownPct': 15},
            {'annualReturnPct': 51, 'maxDrawdownPct': 20},
            {'annualReturnPct': 60, 'maxDrawdownPct': 18},
        ]
        self.assertIs(min(candidates, key=lambda m: rank_candidate(m, 50, 30)), candidates[3])
        self.assertIs(min(candidates, key=lambda m: rank_candidate(m, 90, 30)), candidates[3])

    def test_invalid_targets_and_missing_spy_are_rejected(self):
        for value in (True, float('nan'), float('inf'), -1, 201):
            with self.assertRaises(ValueError):
                build_portfolio_study([], [], target_return_pct=value)
        with self.assertRaisesRegex(ValueError, 'SPY|历史'):
            prepare_history([], [])

    def test_catalog_and_rule_sets_complete(self):
        catalog = rules_catalog()
        self.assertEqual(len(catalog['ruleSets']), 9)
        for rules in catalog['ruleSets'].values():
            self.assertTrue(rules['version'])
            self.assertGreaterEqual(len(rules['sections']), 6)
            self.assertTrue(rules['warnings'])

    def test_alignment_volume_and_ohlc_fail_closed(self):
        from test_strategy import fixture
        stocks = fixture()
        spy = copy.deepcopy(stocks[0]['history'])
        self.assertEqual(len(prepare_history(stocks, spy)[2]), 420)
        stocks[0]['history'][220]['volume'] = None
        with self.assertRaisesRegex(ValueError, '成交量'): prepare_history(stocks, spy)
        stocks = fixture()
        del stocks[0]['history'][220]
        with self.assertRaisesRegex(ValueError, '日期一致'): prepare_history(stocks, spy)
        stocks = fixture()
        stocks[0]['history'].pop()
        with self.assertRaisesRegex(ValueError, '最新交易日'): prepare_history(stocks, spy)

    def test_benchmark_same_mark_to_market_and_entry_costs(self):
        rows = [{'date': '2026-01-01', 'open': 100, 'close': 100},
                {'date': '2026-01-02', 'open': 100, 'close': 110}]
        metrics, curve = buy_hold([rows], '2026-01-01', '2026-01-02', 'SPY')
        quantity = 10000/(100*1.0005*1.0005)
        self.assertAlmostEqual(metrics['finalEquity'], quantity*110)
        self.assertAlmostEqual(metrics['totalCosts'], 10000-quantity*100)
        self.assertEqual(metrics['closedTradeCount'], 0)
        self.assertEqual(metrics['openPositionCount'], 1)

    def test_real_selection_cannot_see_validation_and_validation_resets_cash(self):
        from test_strategy import fixture
        from portfolio_backtest import run_portfolio
        stocks = fixture()
        for stock in stocks:
            for i, row in enumerate(stock['history']):
                row['volume'] = 2_000_000 if i % 15 == 0 else 1_000_000
        spy = copy.deepcopy(stocks[0]['history'])
        for i, row in enumerate(spy):
            price = 100+i*.03
            row.update(open=price, high=price+.2, low=price-.2, close=price)
        calls = []
        def recorded(histories, market, **kwargs):
            calls.append((kwargs, histories, market))
            return run_portfolio(histories, market, **kwargs)
        with patch('portfolio_backtest.run_portfolio', side_effect=recorded):
            first = build_portfolio_study(stocks, spy, template_id='rotation')
        boundary = first['validation']['startDate']
        for _, histories, market in calls[:6]:
            self.assertTrue(all(r['date'] < boundary for rows in histories.values() for r in rows))
            self.assertTrue(all(r['date'] < boundary for r in market))
        changed = copy.deepcopy(stocks)
        for stock in changed:
            for row in stock['history']:
                if row['date'] >= boundary:
                    for key in ('open', 'high', 'low', 'close'): row[key] *= 1.8
        second = build_portfolio_study(changed, spy, template_id='rotation')
        self.assertEqual(first['parameters'], second['parameters'])
        self.assertEqual(first['training'], second['training'])
        direct = run_portfolio({s['symbol']: s['history'] for s in stocks}, spy,
            template_id='rotation', parameters=first['parameters'], start_date=boundary, end_date=first['asOf'])
        self.assertEqual(first['validation']['finalEquity'], direct['metrics']['finalEquity'])
        self.assertEqual(first['validationDetails']['trades'], direct['trades'])
        self.assertEqual(first['candidateCount'], 6)
        self.assertEqual(first['validation']['sessions'], len(first['equityCurve']))
        for benchmark in first['benchmarks'].values():
            self.assertEqual(benchmark['startDate'], boundary)
            self.assertEqual(benchmark['endDate'], first['asOf'])


if __name__ == '__main__': unittest.main()
