import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import server

HEADERS={'host':'127.0.0.1:18765','origin':'http://127.0.0.1:5173'}


def snapshot(path):
    data={'source':'IBKR connector','importedAt':'2026-11-10T10:00:00Z','lists':[
        {'id':'10','name':'Favorites','instruments':[{'contract_id_ex':'265598','contract_description':'AAPL'},
          {'contract_id_ex':'320227571','contract_description':'QQQ'}]}]}
    (path/'ibkr-watchlists.json').write_text(json.dumps(data))


class WatchlistApiTests(unittest.TestCase):
    def test_imported_stock_backtest_uses_its_own_history(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client:
            path=Path(folder);snapshot(path)
            history=[{'date':'2026-11-09','close':123}]
            (path/'dashboard.json').write_text(json.dumps({'stocks':[{'symbol':'AAPL','history':history}]}))
            with patch.object(server,'DATA',path),patch('backtest.run_backtest',return_value={'trades':[]}) as run:
                response=client.post('/api/backtest',json={'symbol':'AAPL'},headers=HEADERS)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['symbol'],'AAPL')
                self.assertEqual(run.call_args.args[0],history)

    def test_legacy_core_unknown_type_keeps_financial_cache_available(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client:
            path=Path(folder)
            (path/'dashboard.json').write_text(json.dumps({'stocks':[{'symbol':'NVDA','quoteType':'UNKNOWN'}]}))
            (path/'company-NVDA.json').write_text(json.dumps({'company':{'symbol':'NVDA'},'status':'ready'}))
            with patch.object(server,'DATA',path):
                result=client.get('/api/company-research?symbol=NVDA',headers=HEADERS).json()
                self.assertEqual(result['status'],'ready')
                self.assertEqual(result['company']['symbol'],'NVDA')

    def test_registered_stock_can_load_and_refresh_research(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client:
            path=Path(folder);snapshot(path)
            (path/'dashboard.json').write_text(json.dumps({'stocks':[{'symbol':'AAPL','quoteType':'EQUITY'}]}))
            company={'symbol':'AAPL','status':'ready','quarters':[{'revenue':123}],'filings':[]}
            with patch.object(server,'DATA',path),patch.object(server.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(company))):
                response=client.post('/api/company-research/refresh',json={'symbol':'AAPL'},headers=HEADERS)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['company']['symbol'],'AAPL')
                self.assertEqual(client.get('/api/news?symbol=AAPL',headers=HEADERS).status_code,200)

    def test_etf_reports_inapplicable_company_financials_but_news_works(self):
        with TemporaryDirectory() as folder,TestClient(server.app) as client:
            path=Path(folder);snapshot(path)
            (path/'dashboard.json').write_text(json.dumps({'stocks':[{'symbol':'QQQ','quoteType':'ETF'}]}))
            with patch.object(server,'DATA',path),patch.object(server.subprocess,'run') as worker,patch.object(server,'fetch_news',return_value=[]):
                for method in ('GET','POST'):
                    response=(client.get('/api/company-research?symbol=QQQ',headers=HEADERS) if method=='GET'
                              else client.post('/api/company-research/refresh',json={'symbol':'QQQ'},headers=HEADERS))
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.json()['status'],'not_applicable')
                    self.assertFalse(response.json()['stale'])
                worker.assert_not_called()
                self.assertEqual(client.post('/api/news/refresh',json={'symbol':'QQQ'},headers=HEADERS).status_code,200)

    def test_core_stale_quote_blocks_new_candidates_but_optional_error_does_not(self):
        for symbol,allowed in [('NVDA',False),('AAPL',True)]:
            with self.subTest(symbol=symbol),TemporaryDirectory() as folder,TestClient(server.app) as client:
                path=Path(folder);snapshot(path)
                data={'stocks':[{'symbol':'NVDA','quoteStatus':'ready'}],'benchmark':{'history':[{}]},
                      'generatedAt':datetime.now(timezone.utc).isoformat(),'quoteRefresh':{'errors':[{'symbol':symbol,'usedCache':True,'message':'failed'}]}}
                (path/'dashboard.json').write_text(json.dumps(data))
                result={'simulation':{'state':{},'latestSignals':[{'status':'candidate'}]},'warnings':[]}
                with patch.object(server,'DATA',path),patch.object(server,'last_error',None),patch('portfolio_study.build_portfolio_study',return_value=result):
                    payload=client.post('/api/portfolio-study',json={},headers=HEADERS).json()
                    self.assertEqual(payload['marketSnapshotStatus']['signalsAllowed'],allowed)


if __name__=='__main__':unittest.main()
