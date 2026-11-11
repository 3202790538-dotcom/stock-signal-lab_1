import json
from pathlib import Path
import tempfile
import unittest
import engine

class DashboardEnrichmentTests(unittest.TestCase):
    def test_legacy_portfolio_uses_unknown_fields_and_derives_only_valid_leverage(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'holdings.json'
            data={'asOf':'2026-11-09','account_summary':{'net_liquidation':100,'gross_position_value':150},
                  'positions':[{'contract_description':'NVDA','position':2,'average_price':10,'market_price':None,'market_value':None,'unrealized_pnl':None}]}
            path.write_text(json.dumps(data))
            result=engine.load_portfolio(path)
            self.assertEqual(result['leverage'],1.5)
            self.assertIsNone(result['cash'])
            self.assertIsNone(result['sevenMarketValue'])
            self.assertIsNone(result['sevenPnl'])
            self.assertIsNone(result['positions'][0]['weightPct'])
            del data['account_summary']['net_liquidation']
            data['account_summary']['leverage']=None
            path.write_text(json.dumps(data))
            result=engine.load_portfolio(path)
            self.assertIsNone(result['netLiquidation'])
            self.assertIsNone(result['leverage'])

    def test_cached_market_gets_complete_account_risk_and_research_with_independent_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            snapshot={'asOf':'2026-11-09T09:00:00+00:00',
                      'account_summary':{'currency':'USD','net_liquidation':100,'total_cash_value':-50,'leverage':'1.5',
                                        'equity_with_loan_value':100,'maintenance_margin':40,'initial_margin':50,
                                        'excess_liquidity':60,'available_funds':50,'gross_position_value':150},
                      'positions':[{'contract_description':'NVDA','position':1,'average_price':100,'market_price':120,
                                    'market_value':120,'unrealized_pnl':20,'currency':'USD','asset_class':'STK'},
                                   {'contract_description':'OTHER','position':1,'market_value':30,'asset_class':'STK','currency':'USD'}],
                      'balances':[{'currency':'USD','cash_balance':-50,'settled_cash':-50}]}
            (folder/'holdings.json').write_text(json.dumps(snapshot))
            (folder/'fundamentals.json').write_text('[]')
            market={'marketAsOf':'2026-11-06','stocks':[{'symbol':'NVDA','history':[]}],
                    'portfolio':{'asOf':'old','positions':[],'netLiquidation':1}}
            result=engine.enrich_dashboard(market,folder)
            self.assertEqual(result['marketAsOf'],'2026-11-06')
            self.assertEqual(result['accountRisk']['asOf'],snapshot['asOf'])
            self.assertEqual(len(result['accountRisk']['allPositions']),2)
            self.assertEqual(len(result['portfolio']['positions']),1)
            self.assertEqual(result['accountRisk']['stockExposure'],150)
            self.assertEqual(result['stocks'][0]['research']['returns']['d252'],None)
            self.assertIn('portfolioAnalytics',result)
            json.dumps(result,allow_nan=False)
