"""Manual-account isolation, strict inputs and conservative valuation."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

NOW = datetime(2026, 11, 11, 2, tzinfo=timezone.utc)


def account():
    return {'cash': 1000, 'positions': [dict(symbol='AAPL', quantity=2.5,
            averageCost=100, markPrice=110, stopPrice=90, sector='other')]}


def market():
    return {'generatedAt': NOW.isoformat(), 'stocks': [dict(symbol='AAPL', currency='USD',
            quoteType='EQUITY', quoteStatus='ready', asOf='2026-11-10', close=120,
            history=[dict(date='2026-11-10', open=118, high=121, low=117, close=120)])]}


class PersonalAccountTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('personal_account'), 'personal account is not implemented')
        self.api = importlib.import_module('personal_account')
        self.folder = TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)

    def test_default_source_stays_broker_and_manual_saves_never_modify_it(self):
        broker = {'source': 'IBKR', 'asOf': '2026-11-10T00:00:00Z',
                  'account_summary': {'net_liquidation': 5000}, 'positions': [], 'balances': []}
        raw = json.dumps(broker)
        (self.path / 'holdings.json').write_text(raw)
        self.api.save_manual_account(self.path, account(), now=NOW)
        settings = self.api.load_settings(self.path)
        self.assertEqual(settings['accountSource'], 'broker')
        self.assertEqual(self.api.selected_holdings(self.path, market(), settings), broker)
        self.assertEqual((self.path / 'holdings.json').read_text(), raw)
        settings.update(accountSource='manual', allocationPct=0)
        self.api.save_settings(self.path, settings)
        actual = self.api.selected_holdings(self.path, market(), settings)
        self.assertEqual(actual['source'], 'manual')
        self.assertEqual(actual['positions'][0]['position'], 2.5)
        self.assertEqual(self.api.load_settings(self.path)['allocationPct'], 0)

    def test_manual_daily_valuation_stop_sector_and_negative_cash_are_explicit(self):
        value = account(); value['cash'] = -20
        self.api.save_manual_account(self.path, value, now=NOW)
        result = self.api.selected_holdings(self.path, market(), {'accountSource': 'manual'})
        row = result['positions'][0]
        self.assertEqual((row['market_price'], row['market_value'], row['unrealized_pnl']), (120, 300, 50))
        self.assertEqual((row['stop_price'], row['sector']), (90, 'other'))
        self.assertEqual(row['valuationSource'], 'daily_close')
        self.assertEqual(row['valuationAsOf'], '2026-11-10')
        self.assertEqual(result['account_summary']['net_liquidation'], 280)
        self.assertEqual(result['account_summary']['total_cash_value'], -20)
        self.assertIsNone(result['account_summary'].get('high_water_mark'))
        self.assertEqual(result['asOf'], NOW.isoformat())
        self.assertEqual(result['manualUpdatedAt'], NOW.isoformat())

    def test_account_view_uses_selected_source_and_preserves_seven_stock_scope(self):
        value = account(); value['positions'].append(dict(symbol='NVDA', quantity=1,
                averageCost=100, markPrice=200, sector='semiconductor'))
        self.api.save_manual_account(self.path, value, now=NOW)
        raw = self.api.selected_holdings(self.path, market(), {'accountSource': 'manual'})
        result = self.api.build_account_view(raw)
        self.assertEqual(result['portfolio']['source'], 'manual')
        self.assertEqual(result['portfolio']['netLiquidation'], 1500)
        self.assertEqual(result['portfolio']['sevenMarketValue'], 200)
        self.assertEqual(result['portfolio']['positions'][0]['symbol'], 'NVDA')
        self.assertEqual(len(result['accountRisk']['allPositions']), 2)

    def test_invalid_or_ambiguous_quote_falls_back_to_mark_not_cost(self):
        self.api.save_manual_account(self.path, account(), now=NOW)
        for change in ({'currency': 'EUR'}, {'quoteStatus': 'stale'}, {'close': float('nan')},
                       {'asOf': '2099-01-01'}, {'asOf': '2026-11-10', 'close': 999}):
            with self.subTest(change=change):
                quotes = market(); quotes['stocks'][0].update(change)
                row = self.api.selected_holdings(self.path, quotes, {'accountSource': 'manual'})['positions'][0]
                self.assertEqual(row['market_price'], 110)
                self.assertEqual(row['valuationSource'], 'manual_mark')
        quotes = market(); quotes['stocks'].append(deepcopy(quotes['stocks'][0]))
        self.assertEqual(self.api.selected_holdings(self.path, quotes, {'accountSource': 'manual'})['positions'][0]['market_price'], 110)

    def test_missing_valuation_keeps_row_and_nav_unknown_even_with_known_cash(self):
        value = account(); value['positions'][0].pop('markPrice'); value['positions'][0].pop('stopPrice')
        self.api.save_manual_account(self.path, value, now=NOW)
        result = self.api.selected_holdings(self.path, {}, {'accountSource': 'manual'})
        self.assertEqual(len(result['positions']), 1)
        row = result['positions'][0]
        for field in ('market_price', 'market_value', 'unrealized_pnl', 'stop_price'):
            self.assertIsNone(row[field])
        self.assertIsNone(result['account_summary']['net_liquidation'])
        self.assertIsNone(result['account_summary']['gross_position_value'])
        self.assertEqual(result['account_summary']['total_cash_value'], 1000)

    def test_invalid_settings_do_not_replace_saved_values(self):
        original = self.api.save_settings(self.path, {'allocationPct': 25})
        for change in ({'allocationPct': True}, {'allocationPct': '25'}, {'allocationPct': 101},
                       {'maxDrawdownPct': 0}, {'targetReturnPct': float('inf')},
                       {'templateId': 'breakout'}, {'accountSource': 'remote'}, {'allowProfitAdd': 'false'},
                       {'reserveCashPct': -1}, {'surprise': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.api.save_settings(self.path, {**original, **change})
            self.assertEqual(self.api.load_settings(self.path), original)

    def test_invalid_manual_rows_do_not_replace_saved_account(self):
        original = self.api.save_manual_account(self.path, account(), now=NOW)
        changes = [{'quantity': 0}, {'quantity': -1}, {'quantity': True}, {'quantity': '2'},
                   {'averageCost': 0}, {'markPrice': float('nan')}, {'stopPrice': -1},
                   {'symbol': '../AAPL'}, {'symbol': 'USD.CNH'}, {'sector': 'finance'}, {'extra': 0}]
        for change in changes:
            value = account(); value['positions'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.api.save_manual_account(self.path, value)
        duplicate = account(); duplicate['positions'].append(deepcopy(duplicate['positions'][0]))
        with self.assertRaises(ValueError): self.api.save_manual_account(self.path, duplicate)
        for cash in ('1000', True, float('inf')):
            with self.assertRaises(ValueError): self.api.save_manual_account(self.path, {**account(), 'cash': cash})
        self.assertEqual(self.api.load_manual_account(self.path), original)

    def test_missing_manual_account_is_empty_but_corruption_is_not_silently_empty(self):
        self.assertEqual(self.api.load_manual_account(self.path), {'cash': 0, 'positions': [], 'updatedAt': None})
        (self.path / 'manual-account.json').write_text('{bad')
        with self.assertRaises(ValueError): self.api.load_manual_account(self.path)


if __name__ == '__main__': unittest.main()
