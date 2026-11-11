"""Check train-only selection, portfolio arithmetic, and sample boundaries."""
import copy
from datetime import date, timedelta
import json
import math
import unittest

from strategy import build_strategy_study, _curve_metrics, _aggregate
from backtest import run_backtest


def fixture(count=420):
    days=[];day=date(2024,1,2)
    while len(days)<count:
        if day.weekday()<5:days.append(day.isoformat())
        day+=timedelta(days=1)
    stocks=[]
    for index,symbol in enumerate(['NVDA','MRVL','AVGO','MU','GOOG']):
        history=[]
        for i,day in enumerate(days):
            close=100+i*.14+5*math.sin(i/13+index*.2)
            history.append({'date':day,'open':close-.15,'high':close+.3,'low':close-.4,'close':close,'volume':10000})
        stocks.append({'symbol':symbol,'history':history})
    return stocks


class StrategyTests(unittest.TestCase):
    def test_validation_prices_cannot_change_selected_parameters_or_training(self):
        original=fixture()
        first=build_strategy_study(original)
        changed=copy.deepcopy(original)
        boundary=first['validation']['startDate']
        for stock in changed:
            for row in stock['history']:
                if row['date']>=boundary:
                    for key in ('open','high','low','close'):row[key]*=2
        second=build_strategy_study(changed)
        self.assertEqual(first['parameters'],second['parameters'])
        self.assertEqual(first['training'],second['training'])
        self.assertNotEqual(first['validation']['totalReturnPct'],second['validation']['totalReturnPct'])
        self.assertEqual(first['candidateCount'],36)

    def test_aggregate_cash_fraction_scales_real_net_pnl_and_costs(self):
        stock=fixture()[0]
        result=run_backtest(stock['history'],lookback=20,holding_days=10,start_date=stock['history'][61]['date'])
        full=_aggregate([result],1)
        half=_aggregate([result],.5)
        self.assertAlmostEqual(half['metrics']['totalReturnPct'],full['metrics']['totalReturnPct']/2)
        self.assertAlmostEqual(half['metrics']['totalCosts'],full['metrics']['totalCosts']/2)
        for a,b in zip(full['curve'],half['curve']):
            self.assertAlmostEqual(b['equity'],10000+(a['equity']-10000)/2)
        self.assertEqual(half['metrics']['closedTradeCount'],len(result['trades']))

    def test_daily_aggregate_drawdown_and_annualization_are_not_averaged_ratios(self):
        values=[10000]*250+[8000,11000]
        stats=_curve_metrics([{'date':str(i),'equity':v} for i,v in enumerate(values)])
        self.assertAlmostEqual(stats['totalReturnPct'],10)
        self.assertAlmostEqual(stats['annualReturnPct'],10)
        self.assertAlmostEqual(stats['maxDrawdownPct'],20)
        opposite=[{'curve':[{'date':'a','equity':8000,'benchmark':8000},{'date':'b','equity':10000,'benchmark':10000}],
                   'trades':[],'openPosition':None,'metrics':{'totalCosts':0}},
                  {'curve':[{'date':'a','equity':12000,'benchmark':12000},{'date':'b','equity':10000,'benchmark':10000}],
                   'trades':[],'openPosition':None,'metrics':{'totalCosts':0}}]
        aggregate=_aggregate(opposite,1)
        self.assertEqual(aggregate['metrics']['maxDrawdownPct'],0)

    def test_all_candidates_share_window_and_validation_has_cash_reset(self):
        stocks=fixture()
        study=build_strategy_study(stocks)
        self.assertEqual(study['training']['startDate'],stocks[0]['history'][61]['date'])
        self.assertLess(study['training']['endDate'],study['validation']['startDate'])
        self.assertEqual(study['equityCurve'][0]['date'],study['validation']['startDate'])
        params=study['parameters']
        direct=run_backtest(stocks[0]['history'],lookback=params['lookback'],holding_days=params['holdingDays'],
                            start_date=study['validation']['startDate'],end_date=study['validation']['endDate'])
        first=study['validation']['perSymbol'][0]
        self.assertEqual(first['symbol'],'NVDA')
        self.assertAlmostEqual(first['totalReturnPct'],direct['metrics']['totalReturnPct'])
        self.assertTrue(all(t['entryDate']>=study['validation']['startDate'] for t in direct['trades']))
        self.assertEqual(study['benchmark']['startDate'],study['validation']['startDate'])
        self.assertEqual(study['benchmark']['endDate'],study['validation']['endDate'])
        json.dumps(study,allow_nan=False)

    def test_missing_or_misaligned_history_is_rejected_not_filled(self):
        stocks=fixture(349)
        with self.assertRaises(ValueError):build_strategy_study(stocks)
        stocks=fixture();stocks[2]['history'].pop(100)
        with self.assertRaises(ValueError):build_strategy_study(stocks)
        with self.assertRaises(ValueError):build_strategy_study(fixture()[:-1])

    def test_short_listings_are_explicitly_excluded(self):
        stocks=fixture()
        stocks.extend([{'symbol':'SPCX','history':stocks[0]['history'][-100:]},
                       {'symbol':'SKHY','history':stocks[0]['history'][-80:]}])
        study=build_strategy_study(stocks)
        self.assertEqual(study['parameters']['symbols'],['NVDA','MRVL','AVGO','MU','GOOG'])
        self.assertEqual([s['symbol'] for s in study['excluded']],['SPCX','SKHY'])

    def test_invalid_targets_do_not_trigger_a_study(self):
        for annual,drawdown in [(float('nan'),30),(50,0),(50,81),(201,30),(True,30)]:
            with self.subTest(annual=annual,drawdown=drawdown),self.assertRaises(ValueError):
                build_strategy_study(fixture(),annual,drawdown)

    def test_unreachable_target_is_reported_without_faking_a_qualified_candidate(self):
        low=build_strategy_study(fixture(),0,80)
        high=build_strategy_study(fixture(),200,80)
        self.assertTrue(low['training']['meetsTargets'])
        self.assertFalse(high['training']['meetsTargets'])
        self.assertFalse(high['validation']['meetsTargets'])
        self.assertEqual(high['trainingQualifiedCount'],0)
        self.assertEqual(high['status'],'research_only')
        self.assertNotEqual(low['parameters'],high['parameters'])


if __name__=='__main__':unittest.main()
