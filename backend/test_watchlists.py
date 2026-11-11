import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def snapshot():
    return {'source': 'IBKR', 'importedAt': '2026-11-10T08:00:00+00:00',
            'lists': [
                {'id': '10', 'name': 'Favorites', 'hash': 123, 'instruments': [
                    {'contract_id_ex': '1', 'contract_description': 'AAPL'},
                    {'contract_id_ex': '2', 'contract_description': 'ORCL'},
                    {'contract_id_ex': '14017175', 'contract_description': '6981'},
                    {'contract_id_ex': '113342317@IDEALPRO', 'contract_description': 'USD.CNH'}]},
                {'id': '100', 'name': 'AI', 'instruments': [
                    {'contract_id_ex': '2', 'contract_description': 'ORCL'}]}],
            'resolvedContracts': {
                '14017175': {'symbol': '6981', 'name': '村田制作所', 'exchange': 'TSEJ',
                             'country': 'JP', 'securityType': 'STK'},
                '113342317@IDEALPRO': {'symbol': 'USD.CNH', 'name': '美元 / 离岸人民币',
                                      'exchange': 'IDEALPRO', 'securityType': 'CASH'}}}


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('watchlists'), 'Watchlist registry is not implemented')
        self.registry = importlib.import_module('watchlists')
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.write(snapshot())

    def write(self, value):
        (self.path / 'ibkr-watchlists.json').write_text(json.dumps(value))

    def test_duplicate_contract_retains_both_groups_without_exposing_raw_snapshot(self):
        result = self.registry.load_registry(self.path)
        self.assertEqual(result['uniqueCount'], 4)
        self.assertEqual([(g['id'], g['count']) for g in result['groups']], [('10', 4), ('100', 1)])
        oracle = next(i for i in result['instruments'] if i['symbol'] == 'ORCL')
        self.assertEqual(oracle['contractId'], '2')
        self.assertEqual(oracle['groupIds'], ['10', '100'])
        self.assertEqual(result['groups'][1]['contractIds'], ['2'])
        self.assertEqual(result['groups'][0]['symbols'], ['AAPL', 'ORCL', '6981', 'USD.CNH'])
        self.assertEqual(result['counts'], {'ready': 0, 'stale': 0, 'unavailable': 0, 'unsupported': 2, 'pending': 2})
        self.assertNotIn('resolvedContracts', result)
        self.assertNotIn('hash', result['groups'][0])

    def test_foreign_stock_and_fx_remain_visible_but_are_not_quote_symbols(self):
        result = self.registry.load_registry(self.path)
        excluded = [i for i in result['instruments'] if not i['supported']]
        self.assertEqual([i['symbol'] for i in excluded], ['6981', 'USD.CNH'])
        self.assertEqual([i['market'] for i in excluded], ['TSEJ', 'IDEALPRO'])
        self.assertTrue(all(i['status'] == 'unsupported' and i['reason'] and i['quoteSymbol'] is None for i in excluded))
        self.assertEqual(self.registry.quote_symbols(self.path, ['NVDA', 'AAPL']), ['NVDA', 'AAPL', 'ORCL'])

    def test_contract_identity_is_not_collapsed_by_shared_symbol(self):
        value = snapshot()
        value['lists'][0]['instruments'].append({'contract_id_ex': '99', 'contract_description': 'AAPL'})
        self.write(value)
        result = self.registry.load_registry(self.path)
        self.assertEqual(result['uniqueCount'], 5)
        self.assertEqual([i['contractId'] for i in result['instruments'] if i['symbol'] == 'AAPL'], ['1', '99'])
        self.assertEqual(self.registry.quote_symbols(self.path, []), ['AAPL', 'ORCL'])

    def test_unsafe_description_never_becomes_a_quote_path(self):
        value = snapshot()
        value['lists'][0]['instruments'].append({'contract_id_ex': '5', 'contract_description': '../../settings'})
        self.write(value)
        self.assertEqual(self.registry.quote_symbols(self.path, []), ['AAPL', 'ORCL'])
        self.assertEqual(self.registry.load_registry(self.path)['instruments'][-1]['status'], 'unsupported')
        for symbol in ['../ABC', 'A/B', 'A?x=y', 'A\nB', '', 'USD.CNH']:
            self.assertFalse(self.registry.safe_quote_symbol(symbol))
        self.assertTrue(self.registry.safe_quote_symbol('BRK-B'))

    def test_failed_quotes_have_unavailable_or_stale_status_and_yahoo_metadata(self):
        stocks = [{'symbol': 'AAPL', 'name': 'Apple Inc.', 'exchange': 'NasdaqGS', 'currency': 'USD',
                   'asOf': '2026-11-09', 'quoteStatus': 'stale', 'quoteError': 'temporarily unavailable'}]
        errors = [{'symbol': 'AAPL', 'message': 'temporarily unavailable', 'usedCache': True},
                  {'symbol': 'ORCL', 'message': 'no bars', 'usedCache': False}]
        result = self.registry.watchlist_status(self.path, stocks, errors)
        apple, oracle = result['instruments'][:2]
        self.assertEqual((apple['status'], apple['name'], apple['market'], apple['asOf']),
                         ('stale', 'Apple Inc.', 'NasdaqGS', '2026-11-09'))
        self.assertEqual((oracle['status'], oracle['reason'], oracle['asOf']), ('unavailable', 'no bars', None))
        self.assertEqual(result['counts']['stale'], 1)
        self.assertEqual(result['counts']['unavailable'], 1)
        self.assertEqual(result['counts']['unsupported'], 2)

    def test_successful_quote_becomes_ready_and_missing_snapshot_preserves_base_universe(self):
        result = self.registry.watchlist_status(self.path, [{'symbol': 'AAPL', 'asOf': '2026-11-09'}])
        self.assertEqual(result['instruments'][0]['status'], 'ready')
        (self.path / 'ibkr-watchlists.json').unlink()
        self.assertEqual(self.registry.load_registry(self.path)['instruments'], [])
        self.assertEqual(self.registry.quote_symbols(self.path, ['NVDA', 'MU']), ['NVDA', 'MU'])

    def test_manual_symbols_join_quotes_without_changing_broker_registry(self):
        original = (self.path / 'ibkr-watchlists.json').read_text()
        (self.path / 'manual-account.json').write_text(json.dumps({'cash': 100, 'positions': [
            {'symbol': 'MSFT', 'quantity': 2}, {'symbol': 'AAPL', 'quantity': 1},
            {'symbol': '../BAD', 'quantity': 3}, {'symbol': 'ZERO', 'quantity': 0},
            {'symbol': 'BOOL', 'quantity': True}]}))
        self.assertEqual(self.registry.quote_symbols(self.path, ['NVDA']), ['NVDA', 'AAPL', 'ORCL', 'MSFT'])
        self.assertEqual((self.path / 'ibkr-watchlists.json').read_text(), original)
        self.assertEqual(self.registry.load_registry(self.path)['uniqueCount'], 4)


if __name__ == '__main__':
    unittest.main()
