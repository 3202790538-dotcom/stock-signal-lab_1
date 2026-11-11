import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import server
from test_runtime_optimization import dashboard_fixture

HEADERS = {'host': '127.0.0.1:18765'}


class PersonalRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.patcher = patch.object(server, 'DATA', self.path)
        self.patcher.start()
        self.client = TestClient(server.app)
        market = dashboard_fixture()
        market['stocks'][0].update(currency='USD', quoteType='EQUITY', quoteStatus='ready')
        market['portfolioAnalytics'] = {'concentration': {'top3Pct': 99, 'positionCount': 7},
                                       'correlation': {'symbols': ['NVDA'], 'values': [[1]]}}
        (self.path/'dashboard.json').write_text(json.dumps(market))
        self.broker = {'source':'IBKR', 'asOf':'2026-11-10T01:00:00+00:00',
                       'account_summary':{'currency':'USD','net_liquidation':1000,'total_cash_value':1000,
                                          'gross_position_value':0}, 'positions':[], 'balances':[]}
        (self.path/'holdings.json').write_text(json.dumps(self.broker))

    def tearDown(self):
        self.client.close()
        self.patcher.stop()
        self.temp.cleanup()

    def test_manual_save_and_source_switch_change_view_without_touching_broker(self):
        original = (self.path/'holdings.json').read_bytes()
        body = {'cash':200, 'positions':[{'symbol':'NVDA','quantity':2,'averageCost':80,
                                        'stopPrice':90,'sector':'semiconductor'}]}
        saved = self.client.put('/api/manual-account', json=body, headers=HEADERS)
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()['updatedAt'])
        settings = self.client.get('/api/personal-settings', headers=HEADERS).json()
        settings.update(accountSource='manual', allocationPct=50)
        self.assertEqual(self.client.put('/api/personal-settings', json=settings, headers=HEADERS).status_code, 200)
        dashboard = self.client.get('/api/dashboard', headers=HEADERS).json()
        self.assertEqual(dashboard['portfolio']['source'], 'manual')
        self.assertEqual(dashboard['portfolio']['netLiquidation'], 402)
        self.assertEqual(dashboard['portfolio']['positions'][0]['quantity'], 2)
        self.assertEqual(dashboard['portfolioAnalytics']['concentration']['positionCount'], 1)
        self.assertAlmostEqual(dashboard['portfolioAnalytics']['concentration']['top3Pct'], 202/402*100)
        self.assertEqual(dashboard['portfolioAnalytics']['correlation']['symbols'], ['NVDA'])
        plan = self.client.post('/api/personal-plan', json=settings, headers=HEADERS)
        self.assertEqual(plan.status_code, 200)
        self.assertEqual(plan.json()['allocation']['allocatedCapital'], 201)
        self.assertEqual(plan.json()['summary']['holdingCount'], 1)
        self.assertEqual((self.path/'holdings.json').read_bytes(), original)

    def test_invalid_inputs_do_not_replace_saved_account_or_settings(self):
        good = {'cash':500, 'positions':[]}
        self.client.put('/api/manual-account', json=good, headers=HEADERS)
        original = (self.path/'manual-account.json').read_bytes()
        for bad in ({'cash':'500','positions':[]}, {'cash':True,'positions':[]},
                    {'cash':500,'positions':[{'symbol':'../bad','quantity':1,'averageCost':2}]}):
            self.assertEqual(self.client.put('/api/manual-account', json=bad, headers=HEADERS).status_code, 422)
        self.assertEqual((self.path/'manual-account.json').read_bytes(), original)
        for bad in ({'allocationPct':101}, {'maxDrawdownPct':0}, {'allocationPct':True}, {'unexpected':1}):
            self.assertEqual(self.client.put('/api/personal-settings', json=bad, headers=HEADERS).status_code, 422)
        self.assertFalse((self.path/'personal-settings.json').exists())

    def test_cross_origin_put_is_rejected_before_private_data_changes(self):
        response = self.client.put('/api/manual-account', json={'cash':500,'positions':[]},
                                   headers={**HEADERS,'origin':'https://untrusted.example'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse((self.path/'manual-account.json').exists())

    def test_loading_daily_status_does_not_trigger_provider_calls(self):
        with patch.object(server.daily_sync, 'status', return_value={'running':False}) as status, \
                patch.object(server, 'refresh', side_effect=AssertionError('GET must not refresh')):
            response = self.client.get('/api/daily-sync-status', headers=HEADERS)
        self.assertEqual(response.json(), {'running':False})
        status.assert_called_once()


if __name__ == '__main__':
    unittest.main()
