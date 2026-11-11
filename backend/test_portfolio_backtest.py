"""Deterministic financial examples; never download prices or access accounts."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from portfolio_backtest import run_portfolio, _RiskState


def bars(count=235, start=80, step=.2):
    result = []
    day = date(2024, 1, 1)
    while len(result) < count:
        if day.weekday() < 5:
            price = start + len(result) * step
            result.append(dict(date=day.isoformat(), open=price-step, high=price+.1,
                               low=price-step-.1, close=price, volume=1_000_000))
        day += timedelta(days=1)
    return result


def fixture(count=235, symbols=('NVDA',)):
    histories = {symbol: bars(count) for symbol in symbols}
    for history in histories.values():
        history[199]['volume'] = 2_000_000
    return histories, bars(count, 100, .05)


def run(histories, spy, **kwargs):
    return run_portfolio(histories, spy, fee_bps=0, slippage_bps=0, **kwargs)


class PortfolioTests(unittest.TestCase):
    def test_entry_day_stop_and_cash_reconcile(self):
        histories, spy = fixture()
        histories['NVDA'][200]['low'] = 110
        result = run(histories, spy)
        trade = result['trades'][0]
        self.assertEqual(trade['entryDate'], trade['exitDate'])
        self.assertEqual(trade['exitReason'], 'intraday_stop')
        self.assertEqual(trade['quantity'], 0)
        self.assertAlmostEqual(result['metrics']['finalEquity'], 10000 + trade['netPnl'])
        self.assertEqual(result['metrics']['closedTradeCount'], 1)

    def test_gap_filter_has_no_fill_and_latest_candidates_are_unfilled(self):
        histories, spy = fixture(201)
        histories['NVDA'][200].update(open=125, high=126, close=125, volume=2_000_000)
        result = run(histories, spy)
        self.assertEqual(result['trades'] + result['openPositions'], [])
        self.assertTrue(any(d['reasonCode'] == 'entry_gap' for d in result['decisions']))
        self.assertEqual(result['latestSignals'][0]['status'], 'candidate')
        self.assertEqual(result['latestSignals'][0]['date'], spy[-1]['date'])

    def test_stop_gap_uses_open_and_exceeds_budget(self):
        histories, spy = fixture(202)
        histories['NVDA'][201].update(open=90, high=91, low=89, close=90)
        result = run(histories, spy)
        trade = result['trades'][0]
        self.assertEqual(trade['exitReason'], 'open_stop')
        self.assertEqual(trade['fills'][-1]['referencePrice'], 90)
        self.assertLess(trade['netPnl'], -trade['sizing']['riskBudget'])

    def test_trailing_update_is_effective_next_day_only(self):
        histories, spy = fixture(202)
        histories['NVDA'][200].update(high=130, low=119.8, close=130)
        histories['NVDA'][201].update(open=130, high=131, low=120, close=130)
        result = run(histories, spy)
        trade = result['trades'][0]
        self.assertEqual(trade['exitDate'], spy[201]['date'])
        self.assertGreater(trade['stopHistory'][0]['stop'], 120)
        self.assertEqual(trade['fills'][-1]['referencePrice'], trade['stopHistory'][0]['stop'])

    def test_time_exit_uses_following_open(self):
        histories, spy = fixture(222)
        result = run(histories, spy)
        trade = result['trades'][0]
        self.assertEqual(trade['exitReason'], 'holding_limit')
        self.assertEqual(trade['exitDate'], spy[220]['date'])
        self.assertEqual(trade['fills'][-1]['referencePrice'], histories['NVDA'][220]['open'])

    def test_shared_cash_sector_cap_and_integer_positions(self):
        histories, spy = fixture(201, ('NVDA', 'MRVL', 'AVGO', 'MU', 'GOOG'))
        result = run(histories, spy)
        positions = result['openPositions']
        self.assertGreater(len(positions), 1)
        self.assertGreaterEqual(result['state']['cash'], 0)
        self.assertTrue(all(type(p['quantity']) is int for p in positions))
        sector = sum(p['quantity']*120 for p in positions if p['symbol'] != 'GOOG')
        self.assertLessEqual(sector, 6000)
        self.assertLessEqual(sum(p['quantity']*120 for p in positions), 8500)
        self.assertEqual(positions[0]['symbol'], 'AVGO')

    def test_partial_trim_remains_one_completed_trade_and_fees_reconcile(self):
        histories, spy = fixture(222)
        for i in range(201, 222):
            price = 150 + (i-201)*.2
            histories['NVDA'][i].update(open=price, high=price+.1, low=price-.1, close=price)
        result = run_portfolio(histories, spy)
        self.assertEqual(result['metrics']['closedTradeCount'], 1)
        trade = result['trades'][0]
        self.assertGreater(len(trade['fills']), 2)
        self.assertTrue(any(f['reason'] == 'single_cap' for f in trade['fills']))
        self.assertEqual(sum(f['quantity'] for f in trade['fills'][1:]), trade['initialQuantity'])
        self.assertAlmostEqual(result['metrics']['finalEquity'], 10000 + trade['netPnl'])
        self.assertAlmostEqual(result['metrics']['totalCosts'], sum(f['fee']+f['slippage'] for f in trade['fills']))

    def test_no_future_price_changes_affect_prior_fills(self):
        histories, spy = fixture()
        original = run(histories, spy, end_date=spy[209]['date'])
        changed = deepcopy(histories)
        for bar in changed['NVDA'][210:]:
            bar.update(open=1, high=1, low=1, close=1, volume=0)
        self.assertEqual(original, run(changed, spy, end_date=spy[209]['date']))

    def test_missing_volume_or_calendar_is_rejected(self):
        histories, spy = fixture()
        del histories['NVDA'][3]['volume']
        with self.assertRaisesRegex(ValueError, 'volume'):
            run(histories, spy)
        histories, spy = fixture()
        histories['NVDA'].pop(3)
        with self.assertRaisesRegex(ValueError, '日期'):
            run(histories, spy)

    def test_rotation_only_buys_first_session_of_week(self):
        histories, spy = fixture(220, ('NVDA', 'GOOG'))
        result = run(histories, spy, template_id='rotation')
        self.assertGreater(len(result['trades']) + len(result['openPositions']), 0)
        for p in result['trades'] + result['openPositions']:
            self.assertEqual(date.fromisoformat(p['entryDate']).weekday(), 0)

    def test_intraday_sector_release_cannot_fund_same_morning_entry(self):
        histories, spy = fixture(202, ('NVDA', 'MRVL', 'AVGO', 'MU'))
        histories['MU'][199]['volume'] = 1_000_000
        histories['MU'][200]['volume'] = 2_000_000
        histories['NVDA'][201]['low'] = 110
        result = run(histories, spy)
        self.assertTrue(any(t['symbol'] == 'NVDA' and t['exitDate'] == spy[201]['date'] for t in result['trades']))
        self.assertFalse(any(t['symbol'] == 'MU' for t in result['trades'] + result['openPositions']))
        skipped = [d for d in result['decisions'] if d['symbol'] == 'MU' and d['date'] == spy[200]['date']][0]
        self.assertEqual(skipped['reasonCode'], 'insufficient_capacity')
        self.assertEqual(skipped['sizing']['binding'], '半导体行业额度')

    def test_five_complete_sessions_after_exit_before_reentry(self):
        histories, spy = fixture(207)
        for bar in histories['NVDA'][199:]:
            bar['volume'] = 2_000_000
        histories['NVDA'][200]['low'] = 110
        result = run(histories, spy)
        positions = result['trades'] + result['openPositions']
        self.assertEqual([p['entryDate'] for p in positions], [spy[200]['date'], spy[206]['date']])

    def test_pullback_touch_is_required_and_volume_threshold_differs(self):
        histories, spy = fixture(202)
        histories['NVDA'][197]['low'] = 116
        histories['NVDA'][199]['volume'] = 1_000_000
        result = run(histories, spy, template_id='pullback')
        self.assertEqual(result['openPositions'][0]['entryDate'], spy[200]['date'])
        self.assertEqual(run(histories, spy)['openPositions'], [])
        histories['NVDA'][197]['low'] = 119.1
        self.assertEqual(run(histories, spy, template_id='pullback')['openPositions'], [])

    def test_end_marks_open_position_without_fictitious_sell_fee(self):
        histories, spy = fixture(201)
        result = run_portfolio(histories, spy)
        p = result['openPositions'][0]
        self.assertEqual(result['metrics']['closedTradeCount'], 0)
        self.assertIsNone(result['metrics']['winRatePct'])
        self.assertEqual(len(p['fills']), 1)
        self.assertAlmostEqual(result['metrics']['finalEquity'], 10000 + p['netPnl'])
        self.assertAlmostEqual(result['metrics']['totalCosts'], p['entryFee'] + p['entrySlippage'])

    def test_trailing_highest_close_excludes_entry_open(self):
        histories, spy = fixture(224)
        # Broad warmup ATR creates room for closes below entry and later narrower ATR.
        for bar in histories['NVDA'][186:200]:
            bar['low'] -= 4
        for i in range(200,224):
            histories['NVDA'][i].update(open=119.8, high=119.9, low=119.4, close=119.5)
        result = run(histories, spy, parameters={'lookback':20, 'holdingDays':40})
        p = (result['openPositions'] + result['trades'])[0]
        self.assertAlmostEqual(p['stop'], 118.0)

    def test_reduced_risk_trims_existing_initial_risk_after_other_caps(self):
        histories, spy = fixture(203, ('NVDA', 'MRVL', 'AVGO', 'MU'))
        for rows in histories.values():
            for bar in rows[186:200]:
                bar['low'] = bar['close']-3.6
        histories['AVGO'][201].update(open=10, high=11, low=9, close=10)
        result = run(histories, spy)
        self.assertEqual(result['state']['mode'], 'reduced')
        self.assertTrue(any(f['reason'] == 'open_risk_cap'
                            for p in result['openPositions'] for f in p['fills']))
        initial_risk = sum(p['quantity']*p['initialRiskPerShare'] for p in result['openPositions'])
        self.assertLessEqual(initial_risk, result['state']['equity']*.015)
        self.assertGreater(result['state']['globalDrawdownPct'], 10)
        self.assertEqual(result['metrics']['closedTradeCount'], 1)

    def test_rotation_weak_spy_does_not_clear_retained_rankings(self):
        histories, spy = fixture(207, ('NVDA', 'GOOG'))
        spy[204].update(open=90, high=91, low=89, close=90)
        result = run(histories, spy, template_id='rotation')
        self.assertEqual(len(result['openPositions']), 2)
        self.assertEqual(result['trades'], [])

    def test_latest_weekly_eligibility_is_conditional_without_future_calendar(self):
        histories, spy = fixture(204, ('NVDA', 'GOOG'))
        # A fresh window starts on Thursday; the unknown next session could be
        # Friday or Monday after a holiday, so no inferred exchange calendar.
        result = run(histories, spy, template_id='rotation', start_date=spy[-1]['date'])
        self.assertTrue(all(d['status'] == 'conditional' for d in result['latestSignals']))
        self.assertTrue(all(d['reasonCode'] == 'weekly_conditional' for d in result['latestSignals']))

    def test_entry_stop_above_raw_open_uses_open_reference(self):
        rows, spy = bars(201, 100, .001), bars(201, 100, .00001)
        for history in (rows, spy):
            for bar in history:
                bar['high'] = bar['close']+.0001
                bar['low'] = bar['open']-.0001
        rows[199]['volume'] = 2_000_000
        result = run_portfolio({'NVDA': rows}, spy)
        trade = result['trades'][0]
        self.assertGreater(trade['initialStop'], rows[200]['open'])
        self.assertEqual(trade['fills'][-1]['referencePrice'], rows[200]['open'])
        self.assertEqual(trade['exitReason'], 'open_stop')

    def test_market_pause_and_resume_are_explained_as_account_events(self):
        histories, spy = fixture(204)
        spy[201].update(open=90, high=91, low=89, close=90)
        result = run(histories, spy)
        event_types = [e['type'] for e in result['events']]
        self.assertIn('market_pause', event_types)
        self.assertIn('market_resume', event_types)

    def test_weak_market_blocks_buys(self):
        histories, spy = fixture(201)
        spy[199].update(open=90, low=89, high=91, close=90)
        result = run(histories, spy)
        self.assertEqual(result['openPositions'], [])
        self.assertTrue(any(d['reasonCode'] == 'market_filter' for d in result['decisions']))


class RiskStateTests(unittest.TestCase):
    def test_reduce_pause_recovery_and_global_peak_are_distinct(self):
        risk = _RiskState(30, 10000)
        risk.observe(0, '2025-01-01', 8900, 5)
        risk.at_open(1, '2025-01-02', 8900)
        self.assertEqual(risk.multiplier, .5)
        risk.observe(1, '2025-01-02', 7900, 5)
        self.assertEqual(risk.at_open(2, '2025-01-03', 7900), 'risk_pause')
        for i in range(2, 12):
            risk.observe(i, f'2025-01-{i+1:02}', 7900, 5)
            if i < 11:
                risk.at_open(i+1, f'2025-01-{i+2:02}', 7900)
        risk.at_open(12, '2025-01-13', 7900)
        self.assertEqual(risk.mode, 'recovery')
        self.assertEqual(risk.cycle_drawdown(7900), 0)
        self.assertAlmostEqual(risk.global_drawdown(7900), 21)
        risk.observe(12, '2025-01-13', 6900, 5)
        self.assertEqual(risk.at_open(13, '2025-01-14', 6900), 'drawdown_termination')
        self.assertEqual(risk.mode, 'terminated')

    def test_recovery_hit_reduction_threshold_restarts_five_day_guard(self):
        risk = _RiskState(30, 10000)
        risk.mode, risk.cycle_peak, risk.global_peak, risk.started = 'recovery', 10000, 10000, 0
        risk.observe(3, '2025-01-04', 8900, 5)
        risk.at_open(4, '2025-01-05', 8900)
        self.assertEqual(risk.mode, 'reduced')
        risk.observe(4, '2025-01-05', 9700, 5)
        risk.at_open(5, '2025-01-06', 9700)
        self.assertEqual(risk.mode, 'reduced')

    def test_reduced_caps_require_five_full_sessions_before_recovery(self):
        risk = _RiskState(30, 10000)
        risk.observe(0, '2025-01-01', 8900, 5)
        for i in range(1, 6):
            risk.at_open(i, f'2025-01-{i+1:02}', 9700)
            self.assertEqual(risk.mode, 'reduced')
            risk.observe(i, f'2025-01-{i+1:02}', 9700, 5)
        risk.at_open(6, '2025-01-07', 9700)
        self.assertEqual(risk.mode, 'normal')


if __name__ == '__main__':
    unittest.main()
