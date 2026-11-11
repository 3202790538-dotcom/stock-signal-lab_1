import json
import unittest

import pandas as pd

import analytics


def stock(symbol, prices, dates=None, volume=None):
    dates = dates if dates is not None else pd.bdate_range('2025-01-02', periods=len(prices))
    volume = volume if volume is not None else [100] * len(prices)
    return {'symbol': symbol, 'history': [
        {'date': str(d.date()), 'open': p, 'high': p + 1, 'low': p - 1,
         'close': p, 'volume': v}
        for d, p, v in zip(dates, prices, volume)
    ]}


def dashboard(stocks, positions=None, nav=1000):
    return {'stocks': stocks, 'portfolio': {'positions': positions or [], 'netLiquidation': nav}}


class ResearchAnalyticsTests(unittest.TestCase):
    # A missing full-window guard would fabricate 1y data for new listings.
    def test_short_history_masks_incomplete_windows(self):
        result = dashboard([stock('SPCX', list(range(100, 180)))])
        self.assertIs(analytics.enrich_research(result), result)
        r = result['stocks'][0]['research']
        self.assertAlmostEqual(r['returns']['d5'], 179 / 174 - 1)
        self.assertIsNone(r['returns']['d120'])
        self.assertIsNone(r['returns']['d252'])
        self.assertIsNone(r['levels']['high252'])
        self.assertIsNone(r['levels']['low252'])
        self.assertIsNone(r['levels']['distanceHigh252'])
        self.assertEqual(r['levels']['high60'], 180)
        self.assertEqual(r['levels']['low20'], 159)
        self.assertEqual(r['longTrend']['status'], 'insufficient')
        self.assertIsNone(r['longTrend']['priceVsMa200'])

    # Using the entire sample instead of 252 observations hides a recent peak.
    def test_drawdown_uses_latest_252_bars_and_real_volume_baseline(self):
        prices = [1000] * 8 + [100] * 250 + [120, 90]
        volumes = [100] * (len(prices) - 1) + [250]
        result = analytics.enrich_research(dashboard([stock('NVDA', prices, volume=volumes)]))
        r = result['stocks'][0]['research']
        self.assertEqual(r['drawdown']['bars'], 252)
        self.assertAlmostEqual(r['drawdown']['current'], -.25)
        self.assertAlmostEqual(r['drawdown']['max'], -.25)
        self.assertEqual(r['levels']['high252'], 121)
        self.assertAlmostEqual(r['volumeRatio'], 2.5)
        self.assertEqual(r['longTrend']['status'], 'below')

    # Quantiles are not loss probabilities and positive returns cannot be losses.
    def test_all_positive_returns_have_zero_var_and_es(self):
        prices = [100 * 1.01 ** i for i in range(81)]
        result = analytics.enrich_research(dashboard([stock('NVDA', prices)]))
        risk = result['stocks'][0]['research']['risk']
        self.assertEqual(risk['observations'], 80)
        self.assertEqual(risk['historicalVar95'], 0)
        self.assertEqual(risk['historicalEs95'], 0)

    # Incorrect ES averaging of the whole sample would miss the severe left tail.
    def test_var_and_es_measure_observed_left_tail(self):
        returns = [.01] * 75 + [-.05, -.10, -.20, -.30, -.40]
        prices = [100.0]
        for r in returns:
            prices.append(prices[-1] * (1 + r))
        risk = analytics.enrich_research(dashboard([stock('NVDA', prices)]))['stocks'][0]['research']['risk']
        self.assertAlmostEqual(risk['historicalVar95'], .0525)
        self.assertAlmostEqual(risk['historicalEs95'], .25)
        short = analytics.enrich_research(dashboard([stock('NVDA', prices[:60])]))['stocks'][0]['research']['risk']
        self.assertIsNone(short['historicalVar95'])
        self.assertIsNone(short['historicalEs95'])
        self.assertEqual(short['observations'], 59)

    # Aligning prices by array position or treating missing dates as zero is wrong.
    def test_correlations_use_shared_return_intervals_and_minimum_sample(self):
        dates = pd.bdate_range('2025-01-02', periods=82)
        returns = [.01, -.02, .03, -.01] * 21
        a = [100.0]
        b = [200.0]
        for r in returns[:81]:
            a.append(a[-1] * (1 + r))
            b.append(b[-1] * (1 + 2 * r))
        remove = 40
        sparse_b = stock('SPCX', b[:remove] + b[remove + 1:], dates.delete(remove))
        data = dashboard([stock('NVDA', a, dates), sparse_b, stock('MU', [100] * 82, dates), stock('SKHY', a[:30], dates[:30])])
        corr = analytics.enrich_research(data)['portfolioAnalytics']['correlation']
        self.assertEqual(corr['symbols'], ['NVDA', 'SPCX', 'MU', 'SKHY'])
        self.assertEqual(corr['observations'][0][1], 79)
        self.assertAlmostEqual(corr['values'][0][1], 1)
        self.assertIsNone(corr['values'][0][2])
        self.assertIsNone(corr['values'][0][3])
        self.assertEqual(corr['observations'][0][3], 29)

    def test_imported_symbols_get_research_without_expanding_portfolio_correlation_scope(self):
        prices=[100+i for i in range(82)]
        result=analytics.enrich_research(dashboard([stock('NVDA',prices),stock('AAPL',prices),stock('ORCL',prices)]))
        self.assertEqual(result['portfolioAnalytics']['correlation']['symbols'],['NVDA'])
        self.assertIsNotNone(result['stocks'][1]['research']['returns']['d20'])
        self.assertIsNotNone(result['stocks'][2]['research']['returns']['d20'])

    # Floating point noise must not give constant-return series a spurious correlation.
    def test_nearly_constant_daily_returns_have_no_correlation(self):
        a = [100 * 1.01 ** i for i in range(81)]
        b = [200 * 1.02 ** i for i in range(81)]
        corr = analytics.enrich_research(dashboard([stock('NVDA', a), stock('MU', b)]))['portfolioAnalytics']['correlation']
        self.assertEqual(corr['observations'][0][1], 80)
        self.assertIsNone(corr['values'][0][1])

    # Keeping more than 126 aligned returns would overweight stale co-movement.
    def test_correlation_caps_each_pair_at_126_shared_return_dates(self):
        prices = [100.0]
        for i in range(300):
            prices.append(prices[-1] * (1 + (.01 if i % 2 else -.02)))
        corr = analytics.enrich_research(dashboard([stock('NVDA', prices), stock('MU', prices)]))['portfolioAnalytics']['correlation']
        self.assertEqual(corr['observations'][0][1], 126)
        self.assertAlmostEqual(corr['values'][0][1], 1)

    # Future outcomes without a full horizon must remain unavailable, never zero.
    def test_signal_history_reports_completed_forward_windows_only(self):
        prices = [100 + i for i in range(80)] + [80] * 10 + [200] * 3
        r = analytics.enrich_research(dashboard([stock('NVDA', prices)]))['stocks'][0]['research']
        events = r['signalHistory']
        self.assertEqual([e['status'] for e in events], ['weak', 'recovering'])
        self.assertEqual(events[0]['date'], '2025-04-24')
        self.assertEqual(events[0]['return5'], 0)
        self.assertIsNone(events[0]['return20'])
        self.assertIsNone(events[-1]['return5'])
        self.assertIsNone(events[-1]['return20'])
        self.assertIn('非策略回测', r['signalHistoryNote'])

    # Concentration must be relative to account NAV, not renormalized to 100%.
    def test_concentration_keeps_leveraged_nav_weights_and_handles_nonpositive_nav(self):
        positions = [
            {'symbol': 'NVDA', 'marketValue': 700},
            {'symbol': 'MU', 'marketValue': 300},
            {'symbol': 'GOOG', 'marketValue': 400},
            {'symbol': 'SPCX', 'marketValue': 100},
        ]
        data = dashboard([], positions, nav=1000)
        c = analytics.enrich_research(data)['portfolioAnalytics']['concentration']
        self.assertEqual(c['top3Pct'], 140)
        self.assertEqual(c['grossWeightPct'], 150)
        self.assertEqual(c['semiconductorPct'], 100)
        data['portfolio']['netLiquidation'] = 0
        c = analytics.enrich_research(data)['portfolioAnalytics']['concentration']
        self.assertIsNone(c['top3Pct'])
        self.assertIsNone(c['grossWeightPct'])
        self.assertIsNone(c['semiconductorPct'])

    # Unknown market value cannot silently shrink a reported exposure percentage.
    def test_missing_position_values_make_relevant_concentration_unknown(self):
        for unknown in [None, float('nan'), float('inf'), True]:
            with self.subTest(unknown=unknown):
                positions = [{'symbol': 'NVDA', 'marketValue': unknown}, {'symbol': 'MU', 'marketValue': 100}]
                c = analytics.enrich_research(dashboard([], positions))['portfolioAnalytics']['concentration']
                self.assertIsNone(c['grossWeightPct'])
                self.assertIsNone(c['top3Pct'])
                self.assertIsNone(c['semiconductorPct'])
                json.dumps(c, allow_nan=False)
        positions = [{'symbol': 'GOOG', 'marketValue': None}, {'symbol': 'MU', 'marketValue': 100}]
        c = analytics.enrich_research(dashboard([], positions))['portfolioAnalytics']['concentration']
        self.assertIsNone(c['grossWeightPct'])
        self.assertIsNone(c['top3Pct'])
        self.assertEqual(c['semiconductorPct'], 10)

    # An explicit empty position list is zero exposure; an unavailable list is unknown.
    def test_empty_holdings_are_zero_but_missing_holdings_and_boolean_nav_are_unknown(self):
        c = analytics.enrich_research(dashboard([]))['portfolioAnalytics']['concentration']
        self.assertEqual(c['grossWeightPct'], 0)
        self.assertEqual(c['top3Pct'], 0)
        self.assertEqual(c['semiconductorPct'], 0)
        for portfolio in [{'netLiquidation': 1000}, {'netLiquidation': 1000, 'positions': None},
                          {'netLiquidation': True, 'positions': []}]:
            c = analytics.enrich_research({'stocks': [], 'portfolio': portfolio})['portfolioAnalytics']['concentration']
            self.assertIsNone(c['grossWeightPct'])
            self.assertIsNone(c['top3Pct'])
            self.assertIsNone(c['semiconductorPct'])

    # Invalid/missing input must not serialize NaN or Infinity into dashboard JSON.
    def test_empty_and_invalid_input_is_json_safe(self):
        data = dashboard([{'symbol': 'EMPTY', 'history': []}, {'symbol': 'BAD', 'history': [
            {'date': '2025-01-02', 'close': None},
            {'date': '2025-01-03', 'close': float('nan')},
        ]}])
        result = analytics.enrich_research(data)
        self.assertIsNone(result['stocks'][0]['research']['drawdown']['current'])
        self.assertIsNone(result['stocks'][1]['research']['returns']['d5'])
        # Existing raw input may itself be invalid; the new research must be safe.
        for s in result['stocks']:
            json.dumps(s['research'], allow_nan=False)
        json.dumps(result['portfolioAnalytics'], allow_nan=False)


if __name__ == '__main__':
    unittest.main()
