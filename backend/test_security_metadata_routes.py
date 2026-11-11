import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import server
from security_metadata import save_metadata, sec_record
from test_runtime_optimization import dashboard_fixture


class SecurityMetadataRoutesTests(unittest.TestCase):
    def test_public_metadata_reaches_dashboard_and_plan_without_changing_manual_account(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            market = dashboard_fixture()
            quote = market['stocks'][0]
            quote.update(symbol='NVDA', currency='USD', quoteType='EQUITY', quoteStatus='ready')
            (path/'dashboard.json').write_text(json.dumps(market))
            (path/'holdings.json').write_text(json.dumps({'positions':[], 'account_summary':{}}))
            save_metadata(path, {'NVDA':sec_record(quote, {'cik':1045810, 'name':'NVIDIA',
                'tickers':['NVDA'], 'sic':'3674', 'sicDescription':'Semiconductors'})})
            headers={'host':'127.0.0.1:18765'}
            with patch.object(server, 'DATA', path), TestClient(server.app) as client:
                client.put('/api/manual-account', json={'cash':1000, 'positions':[
                    {'symbol':'NVDA','quantity':1,'averageCost':80,'sector':'unknown'}]}, headers=headers)
                before = (path/'manual-account.json').read_bytes()
                settings=client.get('/api/personal-settings', headers=headers).json()
                settings['accountSource']='manual'
                client.put('/api/personal-settings', json=settings, headers=headers)
                response=client.get('/api/dashboard', headers=headers)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['stocks'][0].get('sectorClass'),'semiconductor')
                plan=client.post('/api/personal-plan', json=settings, headers=headers).json()
                self.assertFalse(any('行业资料尚未导入' in reason for reason in plan['items'][0]['blockReasons']))
                self.assertIn('SEC',plan['items'][0]['verification']['source']['source'])
                self.assertEqual((path/'manual-account.json').read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
