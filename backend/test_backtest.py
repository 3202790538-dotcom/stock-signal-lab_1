"""Behavioral checks for execution timing, cash accounting and honest samples."""
import copy
from datetime import date, timedelta
import json
import unittest

from backtest import run_backtest


def bars(prices):
    """Tuples are (open, high, low, close); dates are consecutive sessions."""
    return [dict(date=(date(2026, 1, 1) + timedelta(days=i)).isoformat(),
                 open=o, high=h, low=l, close=c, volume=1000)
            for i, (o, h, l, c) in enumerate(prices)]


def breakout_tail(tail):
    return bars([(10, 11, 9, 10)] * 5 + [(10, 13, 9, 12)] + tail)


class BacktestTests(unittest.TestCase):
    def run_case(self, history, **kwargs):
        result = run_backtest(history, lookback=5, **kwargs)
        self.assertIsInstance(result, dict, 'backtest must produce an auditable result')
        return result

    def test_entry_uses_next_open_and_signal_excludes_its_own_high(self):
        # Including current high would suppress the breakout; signal close fill
        # would incorrectly buy at 12 instead of the following 20 open.
        history = breakout_tail([(20, 22, 19, 21)])
        result = self.run_case(history, holding_days=1, fee_bps=0, slippage_bps=0)
        trade = result['trades'][0]
        self.assertEqual(trade['signalDate'], '2026-01-06')
        self.assertEqual(trade['entryDate'], '2026-01-07')
        self.assertEqual(trade['entryPrice'], 20)
        self.assertEqual(trade['exitPrice'], 21)
        self.assertAlmostEqual(trade['netPnl'], 500)

    def test_equal_prior_high_does_not_trigger(self):
        history = bars([(10, 11, 9, 10)] * 5 + [(10, 12, 9, 11)] * 3)
        result = self.run_case(history)
        self.assertEqual(result['trades'], [])
        self.assertIsNone(result['openPosition'])

    def test_fees_and_slippage_debit_both_sides_and_entry_equity(self):
        result = self.run_case(breakout_tail([(100, 105, 95, 100), (100, 115, 95, 110)]),
                               holding_days=2, fee_bps=100, slippage_bps=100)
        trade = result['trades'][0]
        # 10,000 / (101 * 1.01) = 98.02960494 shares;
        # exit = 110 * .99, commission = exit gross * .01.
        self.assertAlmostEqual(trade['quantity'], 98.0296049406921)
        self.assertAlmostEqual(trade['entryPrice'], 101)
        self.assertAlmostEqual(trade['entryFee'], 99.009900990099)
        self.assertAlmostEqual(trade['exitPrice'], 108.9)
        self.assertAlmostEqual(trade['exitFee'], 106.754239780414)
        self.assertAlmostEqual(trade['netPnl'], 568.669738260956)
        self.assertAlmostEqual(trade['totalCosts'], 411.626311145966)
        self.assertAlmostEqual(result['curve'][0]['equity'], 9802.96049406921)
        self.assertAlmostEqual(result['metrics']['maxDrawdownPct'], -1.9703950593079)

    def test_holding_ten_sessions_counts_entry_session_and_avoids_overlap(self):
        history = breakout_tail([(20 + i, 21 + i, 19 + i, 21 + i) for i in range(22)])
        result = self.run_case(history, holding_days=10, fee_bps=0, slippage_bps=0)
        self.assertEqual(result['trades'][0]['entryDate'], '2026-01-07')
        self.assertEqual(result['trades'][0]['exitDate'], '2026-01-16')
        self.assertEqual(result['trades'][0]['sessions'], 10)
        self.assertEqual(result['trades'][1]['entryDate'], '2026-01-17')
        self.assertEqual(result['trades'][1]['exitDate'], '2026-01-26')
        self.assertEqual(result['metrics']['closedTradeCount'], 2)
        self.assertEqual(result['openPosition']['sessions'], 2)
        self.assertEqual(result['metrics']['exposureFraction'], 1)

    def test_unfinished_trade_stays_open_and_is_marked_to_close(self):
        result = self.run_case(breakout_tail([(20, 22, 19, 21), (21, 24, 20, 23)]),
                               holding_days=10, fee_bps=0, slippage_bps=0)
        self.assertEqual(result['trades'], [])
        self.assertEqual(result['openPosition']['markDate'], '2026-01-08')
        self.assertEqual(result['openPosition']['marketValue'], 11500)
        self.assertEqual(result['openPosition']['unrealizedPnl'], 1500)
        self.assertEqual(result['metrics']['finalEquity'], 11500)
        self.assertIsNone(result['metrics']['winRatePct'])

    def test_end_date_blocks_future_signals_exits_and_ohlc(self):
        history = breakout_tail([(20, 22, 19, 21), (21, 24, 20, 23), (23, 25, 22, 24)])
        original = copy.deepcopy(history)
        truncated = self.run_case(history[:8], holding_days=3, fee_bps=0, slippage_bps=0)
        history[-1]['close'] = float('nan')  # Future bar is outside the request.
        filtered = self.run_case(history, holding_days=3, fee_bps=0, slippage_bps=0,
                                 end_date='2026-01-08')
        self.assertEqual(filtered['curve'], truncated['curve'])
        self.assertEqual(filtered['trades'], [])
        self.assertEqual(filtered['openPosition'], truncated['openPosition'])
        self.assertEqual(original[0], history[0])

    def test_start_date_uses_warmup_without_entering_early(self):
        history = breakout_tail([(20, 22, 19, 21), (21, 24, 20, 23), (23, 25, 22, 24)])
        result = self.run_case(history, start_date='2026-01-08', holding_days=1,
                               fee_bps=0, slippage_bps=0)
        self.assertEqual(result['curve'][0]['date'], '2026-01-08')
        self.assertEqual(result['trades'][0]['entryDate'], '2026-01-08')
        self.assertEqual(result['benchmark']['entryDate'], '2026-01-08')
        self.assertEqual(result['benchmark']['entryPrice'], 21)
        self.assertTrue(all(t['entryDate'] >= '2026-01-08' for t in result['trades']))

    def test_benchmark_starts_at_first_eligible_open_with_same_costs(self):
        result = self.run_case(breakout_tail([(100, 105, 95, 100), (100, 115, 95, 110)]),
                               holding_days=2, fee_bps=100, slippage_bps=100)
        self.assertEqual(result['benchmark']['entryDate'], '2026-01-07')
        self.assertEqual(result['benchmark']['valuation'], 'hypothetical_liquidation_at_last_close')
        self.assertAlmostEqual(result['metrics']['benchmarkReturnPct'], 5.68669738260956)
        self.assertAlmostEqual(result['metrics']['excessReturnPct'], 0)
        self.assertEqual(result['curve'][-1]['benchmark'], result['metrics']['finalEquity'])

    def test_no_signals_keeps_cash_and_undefined_statistics_null(self):
        result = self.run_case(bars([(10, 11, 9, 10)] * 12))
        metrics = result['metrics']
        self.assertEqual(metrics['totalReturnPct'], 0)
        self.assertEqual(metrics['finalEquity'], 10000)
        self.assertEqual(metrics['closedTradeCount'], 0)
        self.assertEqual(metrics['exposureFraction'], 0)
        self.assertEqual(metrics['maxDrawdownPct'], 0)
        for key in ('winRatePct', 'payoffRatio', 'profitFactor'):
            self.assertIsNone(metrics[key])
        json.dumps(result, allow_nan=False)

    def test_all_wins_do_not_emit_infinite_ratios(self):
        result = self.run_case(breakout_tail([(20, 23, 19, 22)]),
                               holding_days=1, fee_bps=0, slippage_bps=0)
        self.assertEqual(result['metrics']['winRatePct'], 100)
        self.assertIsNone(result['metrics']['payoffRatio'])
        self.assertIsNone(result['metrics']['profitFactor'])
        json.dumps(result, allow_nan=False)

    def test_losses_are_net_of_costs_and_cash_never_borrows(self):
        result = self.run_case(breakout_tail([(20, 21, 19, 20)]), holding_days=1)
        self.assertEqual(result['metrics']['losses'], 1)
        self.assertEqual(result['metrics']['wins'], 0)
        self.assertEqual(result['metrics']['winRatePct'], 0)
        self.assertLess(result['metrics']['totalReturnPct'], 0)
        self.assertGreaterEqual(result['trades'][0]['cashAfterEntry'], 0)

    def test_mixed_trade_ratios_use_net_closed_pnl_and_distinct_denominators(self):
        result = self.run_case(breakout_tail([(20, 21, 19, 21),
                                              (30, 33, 29, 33), (40, 41, 35, 36)]),
                               holding_days=1, fee_bps=0, slippage_bps=0)
        self.assertEqual([trade['netPnl'] for trade in result['trades']], [500, 1050, -1155])
        self.assertAlmostEqual(result['metrics']['payoffRatio'], .670995670995671)
        self.assertAlmostEqual(result['metrics']['profitFactor'], 1.34199134199134)
        self.assertAlmostEqual(result['metrics']['winRatePct'], 66.6666666666667)
        self.assertAlmostEqual(result['metrics']['maxDrawdownPct'], -10)

    def test_zero_cost_unchanged_trade_counts_as_breakeven(self):
        result = self.run_case(breakout_tail([(20, 21, 19, 20)]),
                               holding_days=1, fee_bps=0, slippage_bps=0)
        self.assertEqual(result['metrics']['breakeven'], 1)
        self.assertEqual(result['metrics']['winRatePct'], 0)

    def test_future_changes_cannot_change_past_equity_or_entries(self):
        history = breakout_tail([(20, 22, 19, 21), (21, 24, 20, 23), (23, 25, 22, 24)])
        before = self.run_case(history, holding_days=10, fee_bps=0, slippage_bps=0)
        history[-1] = {**history[-1], 'open': 100, 'high': 120, 'low': 90, 'close': 110}
        after = self.run_case(history, holding_days=10, fee_bps=0, slippage_bps=0)
        self.assertEqual([p['equity'] for p in before['curve'][:-1]],
                         [p['equity'] for p in after['curve'][:-1]])
        self.assertEqual(before['openPosition']['entryDate'], after['openPosition']['entryDate'])
        self.assertEqual(before['openPosition']['entryPrice'], after['openPosition']['entryPrice'])

    def test_invalid_parameters_are_rejected(self):
        history = bars([(10, 11, 9, 10)] * 130)
        cases = [dict(lookback=4), dict(lookback=121), dict(lookback=5.5),
                 dict(holding_days=0), dict(holding_days=61), dict(holding_days=True),
                 dict(fee_bps=-1), dict(fee_bps=101), dict(fee_bps=float('nan')),
                 dict(slippage_bps=float('inf')), dict(slippage_bps=-1),
                 dict(initial_capital=0), dict(initial_capital=float('inf')),
                 dict(initial_capital=10 ** 400),
                 dict(start_date='2026-02-30'), dict(end_date='not-a-date'),
                 dict(start_date='2026-02-01', end_date='2026-01-01')]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_backtest(history, **kwargs)

    def test_missing_nonfinite_or_inconsistent_ohlc_is_rejected(self):
        baseline = bars([(10, 11, 9, 10)] * 9)
        for patch in [dict(open=None), dict(close=float('nan')), dict(high=9),
                      dict(low=11), dict(open=-1), dict(close=0)]:
            history = copy.deepcopy(baseline)
            history[6].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.run_case(history)
        del baseline[6]['open']
        with self.assertRaises(ValueError):
            self.run_case(baseline)

    def test_duplicate_or_unordered_sessions_are_rejected(self):
        baseline = bars([(10, 11, 9, 10)] * 9)
        for previous in (6, 8):
            history = copy.deepcopy(baseline)
            history[7]['date'] = history[previous]['date']
            with self.subTest(previous=previous), self.assertRaises(ValueError):
                self.run_case(history)

    def test_insufficient_history_or_empty_window_is_rejected(self):
        for history, kwargs in [(bars([(10, 11, 9, 10)] * 6), {}),
                                (bars([(10, 11, 9, 10)] * 9), {'start_date': '2030-01-01'})]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_case(history, **kwargs)


if __name__ == '__main__':
    unittest.main()
