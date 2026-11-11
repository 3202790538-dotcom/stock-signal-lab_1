import json
import unittest

from risk import build_account_risk, estimate_interest


def account_snapshot():
    return {
        'asOf': '2026-11-09T09:00:00Z',
        'account_summary': {
            'currency': 'USD', 'net_liquidation': 100000,
            'equity_with_loan_value': 95000, 'gross_position_value': 150000,
            'total_cash_value': -50000, 'initial_margin': 35000,
            'maintenance_margin': 30000, 'available_funds': 59990,
            'excess_liquidity': 64990, 'buying_power': 239960, 'leverage': '1.5',
        },
        'positions': [
            {'contract_description': 'NVDA', 'asset_class': 'STK',
             'currency': 'USD', 'position': 100, 'market_value': 150000,
             'market_price': 1500, 'average_price': 1200, 'unrealized_pnl': 30000},
        ],
        'balances': [
            {'currency': 'BASE', 'cash_balance': -50000, 'settled_cash': -50000,
             'exchange_rate': 1},
            {'currency': 'USD', 'cash_balance': -50000, 'settled_cash': -49000,
             'exchange_rate': 1},
        ],
    }


class AccountRiskTests(unittest.TestCase):
    def test_positions_from_a_separate_snapshot_show_gross_value_discrepancy(self):
        snapshot = account_snapshot()
        snapshot['positions'][0]['market_value'] = 151000
        risk = build_account_risk(snapshot)
        self.assertEqual(risk['reconciliation']['positionsGrossValue'], 151000)
        self.assertEqual(risk['reconciliation']['grossValueDifference'], 1000)
        self.assertEqual(risk['stockExposure'], 151000)
        self.assertTrue(any('持仓市值' in warning for warning in risk['warnings']))

    def test_uses_broker_reported_buffer_without_overwriting_rounding_difference(self):
        risk = build_account_risk(account_snapshot())
        self.assertEqual(risk['excessLiquidity'], 64990)
        self.assertEqual(risk['availableFunds'], 59990)
        self.assertEqual(risk['reconciliation']['computedExcess'], 65000)
        self.assertEqual(risk['reconciliation']['difference'], -10)
        self.assertEqual(risk['reconciliation']['computedAvailable'], 60000)
        self.assertEqual(risk['reconciliation']['availableDifference'], -10)
        self.assertAlmostEqual(risk['cushionPct'], 64.99)
        self.assertAlmostEqual(risk['maintenanceUsagePct'], 31.5789473684)
        self.assertEqual(risk['cashDebtRatioPct'], 50)

    def test_stress_changes_equity_and_excess_but_holds_margin_requirement_fixed(self):
        risk = build_account_risk(account_snapshot())
        scenario = next(s for s in risk['scenarios'] if s['shockPct'] == -20)
        self.assertEqual(scenario['pnl'], -30000)
        self.assertEqual(scenario['netLiquidation'], 70000)
        self.assertEqual(scenario['equityWithLoanValue'], 65000)
        self.assertEqual(scenario['excessLiquidity'], 34990)
        self.assertAlmostEqual(scenario['cushionPct'], 49.9857142857)
        self.assertAlmostEqual(scenario['leverage'], 120000 / 70000)
        self.assertEqual(risk['stressCoveragePct'], 100)

    def test_does_not_double_count_base_cash_and_uses_settled_cash_for_interest_debit(self):
        risk = build_account_risk(account_snapshot())
        self.assertEqual(len(risk['cashBalances']), 1)
        cash = risk['cashBalances'][0]
        self.assertEqual(cash['currency'], 'USD')
        self.assertEqual(cash['debit'], 49000)
        self.assertEqual(cash['baseDebit'], 49000)
        self.assertEqual(cash['currentDebit'], 50000)
        self.assertTrue(risk['debtByCurrencyAvailable'])

    def test_foreign_currency_debits_are_not_offset_by_positive_cash_elsewhere(self):
        snapshot = account_snapshot()
        snapshot['balances'] = [
            {'currency': 'BASE', 'cash_balance': 100, 'settled_cash': 100, 'exchange_rate': 1},
            {'currency': 'USD', 'cash_balance': 1200, 'settled_cash': 1200, 'exchange_rate': 1},
            {'currency': 'EUR', 'cash_balance': -1000, 'settled_cash': -900, 'exchange_rate': 1.1},
        ]
        risk = build_account_risk(snapshot)
        self.assertEqual(risk['cashBalances'][0]['debit'], 0)
        self.assertEqual(risk['cashBalances'][1]['debit'], 900)
        self.assertAlmostEqual(risk['cashBalances'][1]['baseDebit'], 990)

    def test_stress_converts_foreign_stocks_and_preserves_short_position_sign(self):
        snapshot = account_snapshot()
        snapshot['account_summary']['gross_position_value'] = 144000
        snapshot['positions'] = [
            {'contract_description': 'EUR_STOCK', 'asset_class': 'STK', 'currency': 'EUR',
             'position': 100, 'market_value': 120000, 'market_price': 1200},
            {'contract_description': 'SHORT', 'asset_class': 'STK', 'currency': 'USD',
             'position': -100, 'market_value': -12000, 'market_price': 120},
        ]
        snapshot['balances'].append({'currency': 'EUR', 'cash_balance': 0,
                                     'settled_cash': 0, 'exchange_rate': 1.1})
        risk = build_account_risk(snapshot)
        self.assertAlmostEqual(risk['stockExposure'], 120000)
        self.assertAlmostEqual(risk['allPositions'][0]['baseMarketValue'], 132000)
        self.assertEqual(risk['allPositions'][1]['weightPct'], -12)
        scenario = next(s for s in risk['scenarios'] if s['shockPct'] == -10)
        self.assertAlmostEqual(scenario['pnl'], -12000)
        self.assertAlmostEqual(scenario['leverage'], 129600 / 88000)

    def test_derivatives_and_unknown_exchange_rates_are_reported_as_uncovered(self):
        snapshot = account_snapshot()
        snapshot['account_summary']['gross_position_value'] = 200000
        snapshot['positions'].extend([
            {'contract_description': 'NVDA CALL', 'asset_class': 'OPT', 'currency': 'USD',
             'position': 1, 'market_value': 10000, 'market_price': 100},
            {'contract_description': 'FOREIGN', 'asset_class': 'STK', 'currency': 'JPY',
             'position': 100, 'market_value': 40000, 'market_price': 400},
        ])
        risk = build_account_risk(snapshot)
        self.assertEqual(risk['stressCoveragePct'], 75)
        self.assertEqual(risk['stockExposure'], 150000)
        self.assertEqual(len(risk['omittedPositions']), 2)
        self.assertIsNone(risk['allPositions'][2]['baseMarketValue'])
        self.assertFalse(risk['allPositions'][1]['covered'])
        self.assertTrue(risk['warnings'])

    def test_missing_balances_and_positions_are_unknown_rather_than_zero_debt_and_zero_risk(self):
        risk = build_account_risk({'account_summary': {'currency': 'USD'}})
        self.assertFalse(risk['cashBalancesAvailable'])
        self.assertFalse(risk['debtByCurrencyAvailable'])
        self.assertFalse(risk['positionsAvailable'])
        self.assertIsNone(risk['stockExposure'])
        self.assertIsNone(risk['cashDebtRatioPct'])
        self.assertEqual(risk['scenarios'], [])
        self.assertIsNone(risk['excessLiquidity'])

    def test_missing_settled_cash_does_not_claim_an_interest_principal(self):
        snapshot = account_snapshot()
        snapshot['balances'] = [{'currency': 'USD', 'cash_balance': -50000}]
        risk = build_account_risk(snapshot)
        self.assertIsNone(risk['cashBalances'][0]['debit'])
        self.assertEqual(risk['cashBalances'][0]['currentDebit'], 50000)
        self.assertFalse(risk['debtByCurrencyAvailable'])

    def test_zero_or_negative_equity_never_produces_infinite_ratios(self):
        for nav in (0, -5000):
            snapshot = account_snapshot()
            snapshot['account_summary']['net_liquidation'] = nav
            snapshot['account_summary']['equity_with_loan_value'] = 0
            snapshot['account_summary']['leverage'] = '1.5'
            risk = build_account_risk(snapshot)
            self.assertIsNone(risk['cushionPct'])
            self.assertIsNone(risk['maintenanceUsagePct'])
            self.assertIsNone(risk['cashDebtRatioPct'])
            self.assertIsNone(risk['leverage'])
            self.assertIsNone(risk['scenarios'][0]['cushionPct'])
            json.dumps(risk, allow_nan=False)

    def test_only_derives_missing_margin_fields_when_both_components_are_known(self):
        snapshot = account_snapshot()
        del snapshot['account_summary']['excess_liquidity']
        del snapshot['account_summary']['available_funds']
        risk = build_account_risk(snapshot)
        self.assertEqual(risk['excessLiquidity'], 65000)
        self.assertEqual(risk['availableFunds'], 60000)
        self.assertEqual(risk['excessLiquiditySource'], 'derived')
        self.assertEqual(risk['availableFundsSource'], 'derived')
        del snapshot['account_summary']['maintenance_margin']
        risk = build_account_risk(snapshot)
        self.assertIsNone(risk['excessLiquidity'])
        self.assertEqual(risk['excessLiquiditySource'], 'missing')

    def test_non_finite_source_numbers_remain_unknown_and_can_be_serialized(self):
        snapshot = account_snapshot()
        snapshot['account_summary']['buying_power'] = float('inf')
        snapshot['account_summary']['leverage'] = float('nan')
        snapshot['positions'][0]['market_price'] = float('nan')
        snapshot['balances'][1]['settled_cash'] = float('nan')
        risk = build_account_risk(snapshot)
        self.assertIsNone(risk['buyingPower'])
        self.assertEqual(risk['leverage'], 1.5)
        self.assertIsNone(risk['allPositions'][0]['marketPrice'])
        self.assertIsNone(risk['cashBalances'][0]['debit'])
        json.dumps(risk, allow_nan=False)


class InterestTests(unittest.TestCase):
    def test_simple_daily_interest_uses_the_selected_day_count_and_rate_in_percent(self):
        self.assertEqual(estimate_interest(48000, 6, 30), 240)
        self.assertAlmostEqual(estimate_interest(48000, 6, 30, basis=365), 236.7123287671)
        self.assertEqual(estimate_interest(0, 6, 30), 0)

    def test_invalid_or_non_finite_interest_inputs_are_rejected(self):
        for args in [(-1, 6, 30), (100, -1, 30), (100, 6, -1),
                     (float('nan'), 6, 30), (100, float('inf'), 30),
                     (100, 6, float('nan')), (True, 6, 30)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                estimate_interest(*args)
        with self.assertRaises(ValueError):
            estimate_interest(100, 6, 30, basis=0)


if __name__ == '__main__':
    unittest.main()
