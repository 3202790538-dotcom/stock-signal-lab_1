import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import server

HEADERS={'host':'127.0.0.1:18765','origin':'http://127.0.0.1:5173'}
class ResearchApiTests(unittest.TestCase):
    def test_verified_closed_end_fund_is_inapplicable_without_running_company_worker(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client:
            path=Path(folder)
            (path/'dashboard.json').write_text(json.dumps({'stocks':[{
                'symbol':'DXYZ','name':'Destiny Tech100 Inc.','quoteType':'EQUITY','currency':'USD'}]}))
            (path/'ibkr-watchlists.json').write_text(json.dumps({'lists':[{'id':'favorites','instruments':[
                {'contract_id_ex':'123','contract_description':'DXYZ'}]}]}))
            with patch.object(server,'DATA',path),patch.object(server.subprocess,'run') as work:
                response=client.post('/api/company-research/refresh',json={'symbol':'DXYZ'},headers=HEADERS)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['status'],'not_applicable')
                self.assertIn('封闭式基金',response.json()['lastError'])
                self.assertEqual(response.json()['sourceUrl'],'https://destiny.xyz/tech100')
                self.assertIsNone(response.json()['company'])
                work.assert_not_called()

    def test_fund_override_does_not_match_a_reused_or_unknown_company_name(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)
            (path/'dashboard.json').write_text(json.dumps({'stocks':[{
                'symbol':'DXYZ','name':'Different Issuer Inc.','quoteType':'EQUITY','currency':'USD'}]}))
            with patch.object(server,'DATA',path):
                self.assertIsNone(server.company_inapplicable('DXYZ'))

    def test_known_etf_is_not_applicable_while_unresolved_identity_still_needs_configuration(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)
            (path/'dashboard.json').write_text(json.dumps({'stocks':[{
                'symbol':'QQQ','name':'Invesco QQQ Trust','quoteType':'ETF','currency':'USD'}]}))
            with patch.object(server,'DATA',path):
                self.assertEqual(server.company_inapplicable('QQQ')['status'],'not_applicable')
                self.assertEqual(server.company_inapplicable('UNRESOLVED')['status'],'unconfigured')

    def test_unavailable_payload_with_filings_cannot_replace_valid_financials(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client:
            path=Path(folder)
            (path/'company-NVDA.json').write_text(json.dumps({'company':{'symbol':'NVDA','quarters':[{'revenue':123}]},'updatedAt':'2026-11-01','status':'ready'}))
            failed={'symbol':'NVDA','status':'unavailable','quarters':[],'annuals':[],'valuation':None,'filings':[{'date':'2026-11-06'}]}
            with patch.object(server,'DATA',path),patch.object(server.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(failed),stderr='')):
                result=client.post('/api/company-research/refresh',json={'symbol':'NVDA'},headers=HEADERS)
                self.assertEqual(result.status_code,502)
                self.assertEqual(result.json()['company']['quarters'][0]['revenue'],123)
                self.assertEqual(result.json()['updatedAt'],'2026-11-01')

    def test_old_research_is_marked_stale_without_deleting_last_good_cache(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client:
            path=Path(folder)
            (path/'company-NVDA.json').write_text(json.dumps({'company':{'symbol':'NVDA','quarters':[{'revenue':123}]},'updatedAt':'2020-01-01T00:00:00Z','status':'ready'}))
            with patch.object(server,'DATA',path):
                data=client.get('/api/company-research?symbol=NVDA',headers=HEADERS).json()
                self.assertTrue(data['stale'])
                self.assertEqual(data['company']['quarters'][0]['revenue'],123)

    def test_company_refresh_preserves_last_good_data_on_worker_failure(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client:
            path=Path(folder)
            (path/'company-NVDA.json').write_text(json.dumps({'company':{'symbol':'NVDA','quarters':[{'revenue':123}]},'updatedAt':'2026-11-01'}))
            with patch.object(server,'DATA',path),patch.object(server.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='provider unavailable')):
                r=client.post('/api/company-research/refresh',json={'symbol':'NVDA'},headers=HEADERS)
                self.assertEqual(r.status_code,502)
                self.assertEqual(r.json()['company']['quarters'][0]['revenue'],123)
                self.assertEqual(r.json()['updatedAt'],'2026-11-01')
                self.assertEqual(r.json()['status'],'error')

    def test_company_refresh_caches_returned_data_for_only_requested_symbol(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client:
            company={'symbol':'NVDA','status':'ready','quarters':[{'revenue':123}],'filings':[]}
            with patch.object(server,'DATA',Path(folder)),patch.object(server.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(company),stderr='')):
                r=client.post('/api/company-research/refresh',json={'symbol':'NVDA'},headers=HEADERS)
                self.assertEqual(r.status_code,200)
                self.assertEqual(r.json()['company']['symbol'],'NVDA')
                other=client.get('/api/company-research?symbol=MU',headers=HEADERS)
                self.assertIsNone(other.json()['company'])

    def test_invalid_symbol_and_invalid_backtest_cost_are_rejected_before_work(self):
        with TestClient(server.app) as client,patch.object(server.subprocess,'run') as work:
            for route,body in [('/api/company-research/refresh',{'symbol':'../../holdings'}),('/api/news/refresh',{'symbol':'BAD'}),('/api/backtest',{'symbol':'NVDA','feeBps':-1})]:
                with self.subTest(route=route):
                    self.assertEqual(client.post(route,json=body,headers=HEADERS).status_code,422)
            work.assert_not_called()

    def test_news_failure_keeps_cached_items_and_does_not_fabricate_empty_success(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client:
            path=Path(folder)
            (path/'news-NVDA.json').write_text(json.dumps({'items':[{'id':'a','url':'https://example.com/a','publishedAt':'2026-11-06','kind':'news'}],'updatedAt':'2026-11-06'}))
            with patch.object(server,'DATA',path),patch.object(server,'fetch_news',side_effect=OSError('source failure')):
                r=client.post('/api/news/refresh',json={'symbol':'NVDA'},headers=HEADERS)
                self.assertEqual(r.status_code,502)
                self.assertEqual(r.json()['items'][0]['id'],'a')
                self.assertEqual(r.json()['updatedAt'],'2026-11-06')
