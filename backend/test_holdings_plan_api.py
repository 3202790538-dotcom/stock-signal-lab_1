import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import server
from watchlists import quote_symbols, load_registry

HEADERS = {'host': '127.0.0.1:18765'}


class HoldingsIntegrationTests(unittest.TestCase):
    def test_actual_usd_stocks_join_quotes_without_changing_imported_list(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            positions = [
                {'contract_description': 'IBKR', 'position': 3.5805, 'currency': 'USD', 'asset_class': 'STK'},
                {'contract_description': 'NVDA', 'position': 100, 'currency': 'USD', 'asset_class': 'STK'},
                {'contract_description': 'SHORT', 'position': -2, 'currency': 'USD', 'asset_class': 'STK'},
                {'contract_description': 'ZERO', 'position': 0, 'currency': 'USD', 'asset_class': 'STK'},
                {'contract_description': 'FOREIGN', 'position': 2, 'currency': 'JPY', 'asset_class': 'STK'},
                {'contract_description': 'USD.CNH', 'position': 2, 'currency': 'USD', 'asset_class': 'CASH'},
                {'contract_description': '../BAD', 'position': 2, 'currency': 'USD', 'asset_class': 'STK'},
                {'contract_description': 'BAD', 'position': True, 'currency': 'USD', 'asset_class': 'STK'},
                {'contract_description': 'NAN', 'position': float('nan'), 'currency': 'USD', 'asset_class': 'STK'},
                None,
            ]
            (path/'holdings.json').write_text(json.dumps({'positions': positions}))
            self.assertEqual(quote_symbols(path, ['NVDA']), ['NVDA', 'IBKR', 'SHORT'])
            self.assertEqual(load_registry(path)['uniqueCount'], 0)
            for malformed in ('{', '[]', '{"positions": {"bad": 1}}'):
                (path/'holdings.json').write_text(malformed)
                self.assertEqual(quote_symbols(path, ['NVDA']), ['NVDA'])

    def test_plan_endpoint_validates_options_and_requires_account(self):
        with TemporaryDirectory() as folder, patch.object(server, 'DATA', Path(folder)), TestClient(server.app) as client:
            for body in ({'templateId':'unknown'}, {'riskProfile':'unknown'}, {'allowProfitAdd':'yes'}, {'extra':1}):
                self.assertEqual(client.post('/api/holdings-plan', json=body, headers=HEADERS).status_code, 422)
            response = client.post('/api/holdings-plan', json={}, headers=HEADERS)
            self.assertEqual(response.status_code, 503)
            self.assertIn('账户', response.json()['error'])

    def test_plan_reads_updated_positions_and_suppresses_busy_or_failed_market(self):
        from functools import partial
        from holdings_plan import build_holdings_plan
        from test_holdings_plan import fixture, NOW
        market, holdings = fixture()
        with TemporaryDirectory() as folder, patch.object(server, 'DATA', Path(folder)), TestClient(server.app) as client:
            path = Path(folder)
            (path/'dashboard.json').write_text(json.dumps(market))
            (path/'holdings.json').write_text(json.dumps(holdings))
            with patch('holdings_plan.build_holdings_plan', partial(build_holdings_plan, now=NOW)):
                response = client.post('/api/holdings-plan', json={}, headers=HEADERS)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['items'][0]['decision']['kind'], 'watch_add')
                holdings['positions'][0].update(position=5, market_value=828.5)
                (path/'holdings.json').write_text(json.dumps(holdings))
                self.assertEqual(client.post('/api/holdings-plan', json={}, headers=HEADERS).json()['items'][0]['identity']['quantity'], 5)
                server.refresh_lock.acquire()
                try:
                    blocked = client.post('/api/holdings-plan', json={}, headers=HEADERS).json()
                finally:
                    server.refresh_lock.release()
                self.assertEqual(blocked['items'][0]['decision']['kind'], 'verify')
                with patch.object(server, 'last_error', 'provider unavailable'):
                    blocked = client.post('/api/holdings-plan', json={}, headers=HEADERS).json()
                    self.assertFalse(any(row['triggered'] for row in blocked['items'][0]['scenarios']))


if __name__ == '__main__':
    unittest.main()
