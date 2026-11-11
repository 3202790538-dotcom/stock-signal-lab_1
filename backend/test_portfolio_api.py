import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import server

HEADERS = {'host': '127.0.0.1:18765', 'origin': 'http://127.0.0.1:5173'}


class PortfolioApiTests(unittest.TestCase):
    def test_missing_spy_history_requests_refresh(self):
        with TemporaryDirectory() as folder, TestClient(server.app) as client, patch.object(server, 'DATA', Path(folder)):
            response = client.post('/api/portfolio-study', json={}, headers=HEADERS)
            self.assertEqual(response.status_code, 503)
            self.assertIn('刷新', response.json()['error'])

    def test_invalid_options_rejected(self):
        with TestClient(server.app) as client:
            for body in ({'templateId': 'magic'}, {'riskProfile': 'unlimited'}, {'targetReturnPct': -1},
                         {'targetReturnPct': 201}, {'targetReturnPct': True}, {'order': 'buy'}):
                self.assertEqual(client.post('/api/portfolio-study', json=body, headers=HEADERS).status_code, 422)

    def test_rules_endpoint_contains_no_account_data(self):
        with TestClient(server.app) as client:
            response = client.get('/api/strategy-rules', headers=HEADERS)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()['ruleSets']), 9)

    def test_endpoint_passes_only_public_history(self):
        with TemporaryDirectory() as folder, TestClient(server.app) as client, patch.object(server, 'DATA', Path(folder)):
            stocks = [{'symbol': 'NVDA', 'history': []}]
            spy = [{'date': '2026-01-01'}]
            (Path(folder)/'dashboard.json').write_text(json.dumps({'stocks': stocks, 'benchmark': {'history': spy},
                'portfolio': {'secretAccount': 'do-not-send'}, 'accountRisk': {'secret': True}, 'generatedAt': 'stamp'}))
            with patch('portfolio_study.build_portfolio_study', return_value={'status': 'research_only'}) as build:
                response = client.post('/api/portfolio-study', json={}, headers=HEADERS)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(build.call_args.args, (stocks, spy))
                self.assertNotIn('secret', response.text)
                self.assertEqual(response.json()['marketGeneratedAt'], 'stamp')

    def test_failed_refresh_or_incomplete_data_cannot_publish_fresh_candidates(self):
        for last_error, warnings, stamp in [
            ('刷新失败', [], datetime.now(timezone.utc).isoformat()),
            (None, [{'symbol': 'NVDA', 'date': '2026-11-09', 'missingFields': ['close']}], datetime.now(timezone.utc).isoformat()),
            (None, [], '2020-01-01T00:00:00Z'),
        ]:
            with self.subTest(last_error=last_error, warnings=warnings, stamp=stamp):
                with TemporaryDirectory() as folder, TestClient(server.app) as client, patch.object(server, 'DATA', Path(folder)), patch.object(server, 'last_error', last_error):
                    (Path(folder)/'dashboard.json').write_text(json.dumps({'stocks': [{'symbol': 'NVDA'}],
                        'benchmark': {'history': [{'date': '2026-01-01'}]}, 'marketWarnings': warnings, 'generatedAt': stamp}))
                    fake = {'status': 'research_only', 'simulation': {'latestSignals': [{'status': 'candidate'}], 'state': {}}, 'warnings': []}
                    with patch('portfolio_study.build_portfolio_study', return_value=fake):
                        data = client.post('/api/portfolio-study', json={}, headers=HEADERS).json()
                        self.assertFalse(data['marketSnapshotStatus']['signalsAllowed'])
                        self.assertEqual(data['simulation']['latestSignals'], [])
                        self.assertTrue(data['marketSnapshotStatus']['reason'])


if __name__ == '__main__': unittest.main()
