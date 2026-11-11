import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from fastapi.testclient import TestClient
import server

HEADERS={'host':'127.0.0.1:18765','origin':'http://127.0.0.1:5173'}

class StrategyApiTests(unittest.TestCase):
    def test_real_study_api_returns_only_research_data(self):
        from test_strategy import fixture
        with TemporaryDirectory() as folder,TestClient(server.app) as client,patch.object(server,'DATA',Path(folder)):
            (Path(folder)/'dashboard.json').write_text(json.dumps({'stocks':fixture(),'generatedAt':'2026-11-10T01:00:00Z','portfolio':{'privateSentinel':'account-only'}}))
            response=client.post('/api/strategy-study',json={'targetReturnPct':50,'maxDrawdownPct':30},headers=HEADERS)
            self.assertEqual(response.status_code,200)
            payload=response.json()
            self.assertEqual(payload['targets'],{'annualReturnPct':50,'maxDrawdownPct':30})
            self.assertEqual(payload['candidateCount'],36)
            self.assertLess(payload['training']['endDate'],payload['validation']['startDate'])
            self.assertNotIn('privateSentinel',response.text)

    def test_invalid_targets_rejected_before_reading_private_cache(self):
        with TestClient(server.app) as client:
            for body in ({'targetReturnPct':-1},{'maxDrawdownPct':81},{'targetReturnPct':201},{'maxDrawdownPct':0},{'order':True}):
                with self.subTest(body=body):
                    response=client.post('/api/strategy-study',json=body,headers=HEADERS)
                    self.assertEqual(response.status_code,422)

    def test_missing_market_history_reports_unavailable(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client,patch.object(server,'DATA',Path(folder)):
            response=client.post('/api/strategy-study',json={},headers=HEADERS)
            self.assertEqual(response.status_code,503)
            self.assertIn('error',response.json())

    def test_opening_second_page_cannot_start_duplicate_price_job(self):
        server.refresh_lock.acquire()
        try:
            with TestClient(server.app) as client:
                response=client.post('/api/refresh',headers=HEADERS)
                self.assertEqual(response.status_code,409)
        finally:server.refresh_lock.release()
