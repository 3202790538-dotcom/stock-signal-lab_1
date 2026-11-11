import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import engine

class MarketDataTests(unittest.TestCase):
    def test_yahoo_identity_metadata_survives_normalization_and_calculation(self):
        days=pd.date_range('2026-11-05T14:30:00Z', periods=2)
        payload={'chart':{'result':[{'meta':{'symbol':'AAPL','currency':'USD','longName':'Apple Inc.',
                 'fullExchangeName':'NasdaqGS','exchangeName':'NMS','instrumentType':'EQUITY'},
                 'timestamp':[int(x.timestamp()) for x in days],
                 'indicators':{'quote':[{'open':[100,101],'high':[102,103],'low':[99,100],
                                        'close':[101,102],'volume':[100,200]}]}}]}}
        frame=engine.normalize_yahoo('AAPL',payload)
        result=engine.calculate({'AAPL':frame,'SPY':frame.copy()})
        apple=result['stocks'][0]
        self.assertEqual((apple['name'],apple.get('exchange'),apple.get('quoteType')),
                         ('Apple Inc.','NasdaqGS','EQUITY'))
        self.assertEqual((apple.get('quoteStatus'),apple.get('quoteError')),('ready',None))

    def test_missing_completed_close_is_reported_without_inventing_a_price(self):
        days=pd.to_datetime(['2026-11-06T14:30:00Z','2026-11-09T14:30:00Z'])
        payload={'chart':{'result':[{'meta':{'symbol':'NVDA','currency':'USD'},'timestamp':[int(x.timestamp()) for x in days],
                 'indicators':{'quote':[{'open':[191,173],'high':[195,180],'low':[185,170],'close':[186.64,None],'volume':[100,200]}]}}]}}
        after=datetime(2026,11,9,20,0,tzinfo=ZoneInfo('America/New_York'))
        before=datetime(2026,11,9,12,0,tzinfo=ZoneInfo('America/New_York'))
        frame=engine.normalize_yahoo('NVDA',payload,now=after)
        self.assertEqual(str(frame.index.max().date()),'2026-11-06')
        self.assertEqual(frame.close.iloc[-1],186.64)
        self.assertEqual(frame.attrs.get('marketWarnings'),[{'symbol':'NVDA','date':'2026-11-09','missingFields':['close']}])
        self.assertEqual(engine.normalize_yahoo('NVDA',payload,now=before).attrs.get('marketWarnings'),[])

    def test_incomplete_tail_marks_quote_stale_until_completed_fields_arrive(self):
        days=pd.to_datetime(['2026-11-05T14:30:00Z','2026-11-06T14:30:00Z','2026-11-09T14:30:00Z'])
        after=datetime(2026,11,9,20,0,tzinfo=ZoneInfo('America/New_York'))
        before=datetime(2026,11,9,12,0,tzinfo=ZoneInfo('America/New_York'))
        for absent, labels in [(['close'], ['收盘价']), (['open','low'], ['开盘价','最低价'])]:
            with self.subTest(absent=absent):
                quote={'open':[100,101,102],'high':[102,103,104],'low':[99,100,101],
                       'close':[101,102,103],'volume':[1000,2000,3000]}
                for field in absent:quote[field][-1]=None
                payload={'chart':{'result':[{'meta':{'symbol':'NVDA','currency':'USD','regularMarketPrice':999},
                         'timestamp':[int(day.timestamp()) for day in days], 'indicators':{'quote':[quote]}}]}}
                frame=engine.normalize_yahoo('NVDA',payload,now=after)
                self.assertEqual(frame.attrs.get('quoteStatus'),'stale')
                reason=frame.attrs.get('quoteError') or ''
                for text in ['2026-11-09','2026-11-06',*labels]:self.assertIn(text,reason)
                self.assertEqual(frame.close.tolist(),[101,102])
                calculated=engine.calculate({'NVDA':frame,'SPY':frame.copy()})
                self.assertEqual(calculated['stocks'][0]['quoteStatus'],'stale')
                self.assertEqual(calculated['stocks'][0]['quoteError'],reason)
                self.assertEqual(calculated['stocks'][0]['close'],102)
                in_progress=engine.normalize_yahoo('NVDA',payload,now=before)
                self.assertEqual((in_progress.attrs['quoteStatus'],in_progress.attrs['quoteError']),('ready',None))
                for field in absent:quote[field][-1]={'open':102,'low':101,'close':103}[field]
                recovered=engine.normalize_yahoo('NVDA',payload,now=after)
                self.assertEqual((recovered.attrs['quoteStatus'],recovered.attrs['quoteError']),('ready',None))
                self.assertEqual(recovered.close.iloc[-1],103)

    def test_missing_volume_is_not_reported_as_zero_trading(self):
        day=int(pd.Timestamp('2026-11-06T14:30:00Z').timestamp())
        payload={'chart':{'result':[{'meta':{'symbol':'NVDA','currency':'USD'},'timestamp':[day],
                 'indicators':{'quote':[{'open':[100],'high':[101],'low':[99],'close':[100],'volume':[None]}]}}]}}
        frame=engine.normalize_yahoo('NVDA',payload)
        self.assertTrue(pd.isna(frame.volume.iloc[-1]))

    def test_short_history_does_not_invent_long_window_or_return(self):
        dates=pd.bdate_range('2026-07-10',periods=80)
        frame=pd.DataFrame({'open':range(100,180),'high':range(102,182),'low':range(99,179),'close':range(101,181),'volume':[1000000]*80},index=dates)
        frame.attrs['marketWarnings']=[{'symbol':'SKHY','date':'2026-11-09','missingFields':['close']}]
        spy=frame.copy();spy.attrs={}
        result=engine.calculate({'SKHY':frame,'SPY':spy})
        self.assertEqual(len(result['benchmark']['history']),80)
        self.assertEqual(result['benchmark']['history'][-1]['close'],180)
        self.assertEqual(result['benchmark']['history'][-1]['volume'],1000000)
        self.assertEqual(result.get('marketWarnings'),[{'symbol':'SKHY','date':'2026-11-09','missingFields':['close']}])
        stock=result['stocks'][0]
        self.assertIsNone(stock['ma200'])
        self.assertAlmostEqual(stock['ma20'],170.5,places=3)
        self.assertAlmostEqual(stock['ret20'],180/160-1,places=6)
        self.assertEqual(stock['bars'],80)

    def test_invalid_empty_market_response_is_rejected(self):
        with self.assertRaises(ValueError):
            engine.normalize_yahoo('NVDA',{'chart':{'result':None,'error':{'description':'unavailable'}}})

    def test_malformed_provider_shapes_raise_a_recoverable_validation_error(self):
        for payload in (None, [], {'chart': None}, {'chart': {'result': [None]}},
                        {'chart': {'result': [{'meta': None}]}},
                        {'chart': {'result': [{'meta': {'symbol': 'NVDA', 'currency': 'USD'}, 'indicators': None}]}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                engine.normalize_yahoo('NVDA', payload)

    def test_nonfinite_and_nonnumeric_provider_prices_are_rejected_before_calculation(self):
        stamp = int(pd.Timestamp('2026-11-06T14:30:00Z').timestamp())
        for bad in (json.loads('1e999'), -float('inf'), float('nan'), True, '100'):
            for field in ('open', 'high', 'low', 'close'):
                quote = {'open': [100], 'high': [101], 'low': [99], 'close': [100], 'volume': [1000]}
                quote[field] = [bad]
                payload = {'chart': {'result': [{'meta': {'symbol': 'NVDA', 'currency': 'USD'},
                           'timestamp': [stamp], 'indicators': {'quote': [quote]}}]}}
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    engine.normalize_yahoo('NVDA', payload)

    def test_ticker_reuse_excludes_old_etf(self):
        days=pd.to_datetime(['2026-04-01T13:30:00Z','2026-06-12T13:30:00Z','2026-06-15T13:30:00Z'])
        payload={'chart':{'result':[{'meta':{'symbol':'SPCX','currency':'USD'},'timestamp':[int(x.timestamp()) for x in days], 'indicators':{'quote':[{'open':[20,160,161],'high':[21,161,162],'low':[19,159,160],'close':[20,160,161],'volume':[100,100,100]}]}}]}}
        f=engine.normalize_yahoo('SPCX',payload)
        self.assertEqual(str(f.index.min().date()),'2026-06-12')
        self.assertEqual(len(f),2)

    def test_in_progress_session_is_not_reported_as_a_completed_close(self):
        days=pd.to_datetime(['2026-11-06T14:30:00Z','2026-11-09T14:30:00Z'])
        payload={'chart':{'result':[{'meta':{'symbol':'NVDA','currency':'USD'},'timestamp':[int(x.timestamp()) for x in days], 'indicators':{'quote':[{'open':[200,205],'high':[210,210],'low':[198,200],'close':[208,206],'volume':[100,100]}]}}]}}
        before=datetime(2026,11,9,12,0,tzinfo=ZoneInfo('America/New_York'))
        after=datetime(2026,11,9,16,5,tzinfo=ZoneInfo('America/New_York'))
        self.assertEqual(str(engine.normalize_yahoo('NVDA',payload,now=before).index.max().date()),'2026-11-06')
        self.assertEqual(str(engine.normalize_yahoo('NVDA',payload,now=after).index.max().date()),'2026-11-09')


class PartialRefreshTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)
        (self.path/'holdings.json').write_text(json.dumps({'positions':[], 'account_summary':{}}))
        (self.path/'ibkr-watchlists.json').write_text(json.dumps({'source':'IBKR','importedAt':'2026-11-10',
            'lists':[{'id':'10','name':'Favorites','instruments':[
                {'contract_id_ex':'1','contract_description':'AAPL'},
                {'contract_id_ex':'2','contract_description':'ORCL'}]}]}))

    def quote(self,symbol):
        dates=pd.bdate_range('2026-09-01',periods=30)
        frame=pd.DataFrame({'open':range(100,130),'high':range(102,132),'low':range(99,129),
                            'close':range(101,131),'volume':[1000]*30},index=dates)
        frame.attrs.update({'name':symbol+' Company','exchange':'NYSE','quoteType':'EQUITY','currency':'USD'})
        return frame

    def test_failed_and_one_bar_imports_do_not_abort_base_stocks_or_create_fake_stock(self):
        def fetch(symbol):
            if symbol=='AAPL':raise OSError('offline')
            if symbol=='ORCL':return self.quote(symbol).iloc[:1]
            return self.quote(symbol)
        with patch.object(engine,'download',side_effect=fetch):
            result=engine.refresh(self.path)
        self.assertEqual([s['symbol'] for s in result['stocks']],['NVDA','SPCX','MRVL','AVGO','SKHY','MU','GOOG'])
        errors={e['symbol']:e for e in result['quoteRefresh']['errors']}
        self.assertEqual(set(errors),{'AAPL','ORCL'})
        self.assertFalse(any(e['usedCache'] for e in errors.values()))
        self.assertEqual(result['watchlists']['counts']['unavailable'],2)
        self.assertGreaterEqual(result['quoteRefresh']['durationSeconds'],0)
        self.assertEqual(json.loads((self.path/'dashboard.json').read_text())['watchlists']['uniqueCount'],2)

    def test_optional_cached_quotes_are_stale_without_falsely_flagging_core_data(self):
        with patch.object(engine,'download',side_effect=self.quote):
            engine.refresh(self.path)
        def fetch(symbol):
            if symbol=='AAPL':raise TimeoutError('offline')
            return self.quote(symbol)
        with patch.object(engine,'download',side_effect=fetch):
            result=engine.refresh(self.path)
        apple=next(s for s in result['stocks'] if s['symbol']=='AAPL')
        self.assertEqual(apple['quoteStatus'],'stale')
        self.assertTrue(apple['quoteError'])
        self.assertEqual(apple['exchange'],'NYSE')
        self.assertEqual(apple['bars'],30)
        self.assertEqual(result['watchlists']['counts']['stale'],1)
        self.assertEqual(result['watchlists']['counts']['ready'],1)
        self.assertEqual(result['marketWarnings'],[])
        self.assertNotIn('AAPL',result['quoteRefresh']['updatedSymbols'])

    def test_malformed_optional_provider_response_does_not_abort_core_refresh(self):
        def fetch(symbol):
            if symbol == 'AAPL':
                return engine.normalize_yahoo(symbol, {'chart': None})
            return self.quote(symbol)
        with patch.object(engine, 'download', side_effect=fetch):
            result = engine.refresh(self.path)
        self.assertEqual([e['symbol'] for e in result['quoteRefresh']['errors']], ['AAPL'])
        self.assertEqual(result['watchlists']['counts']['unavailable'], 1)
        self.assertIn('NVDA', result['quoteRefresh']['updatedSymbols'])
        self.assertEqual(result['marketWarnings'], [])

    def test_infinite_optional_provider_price_does_not_abort_core_refresh(self):
        stamps = [int(day.timestamp()) for day in pd.to_datetime(['2026-11-05T14:30:00Z', '2026-11-06T14:30:00Z'])]
        payload = {'chart': {'result': [{'meta': {'symbol': 'AAPL', 'currency': 'USD'},
                   'timestamp': stamps, 'indicators': {'quote': [{'open': [100, 101], 'high': [102, json.loads('1e999')],
                   'low': [99, 100], 'close': [101, 102], 'volume': [1000, 2000]}]}}]}}
        def fetch(symbol):
            if symbol == 'AAPL':
                return engine.normalize_yahoo(symbol, payload)
            return self.quote(symbol)
        with patch.object(engine, 'download', side_effect=fetch):
            result = engine.refresh(self.path)
        self.assertEqual([e['symbol'] for e in result['quoteRefresh']['errors']], ['AAPL'])
        self.assertFalse(result['quoteRefresh']['errors'][0]['usedCache'])
        self.assertIn('NVDA', result['quoteRefresh']['updatedSymbols'])

    def test_stale_core_and_benchmark_remain_flagged_even_with_new_dashboard_timestamp(self):
        with patch.object(engine,'download',side_effect=self.quote):
            engine.refresh(self.path)
        def fetch(symbol):
            if symbol in ('NVDA','SPY'):raise OSError('offline')
            return self.quote(symbol)
        with patch.object(engine,'download',side_effect=fetch):
            result=engine.refresh(self.path)
        self.assertEqual(next(s for s in result['stocks'] if s['symbol']=='NVDA')['quoteStatus'],'stale')
        self.assertEqual(result['benchmark']['quoteStatus'],'stale')
        self.assertEqual({w['symbol'] for w in result['marketWarnings'] if 'dataRefresh' in w['missingFields']},
                         {'NVDA','SPY'})
        self.assertTrue(all(e['usedCache'] for e in result['quoteRefresh']['errors']))

    def test_missing_benchmark_without_cache_keeps_last_dashboard_file_untouched(self):
        (self.path/'dashboard.json').write_text('{"generatedAt":"previous"}')
        def fetch(symbol):
            if symbol=='SPY':raise OSError('offline')
            return self.quote(symbol)
        with patch.object(engine,'download',side_effect=fetch),self.assertRaises(ValueError):
            engine.refresh(self.path)
        self.assertEqual(json.loads((self.path/'dashboard.json').read_text()),{'generatedAt':'previous'})

    def test_imported_stock_order_and_portfolio_membership_are_independent(self):
        holdings={'positions':[{'contract_description':'AAPL','position':1,'market_value':20},
                               {'contract_description':'NVDA','position':1,'market_value':30}],
                  'account_summary':{'net_liquidation':100}}
        (self.path/'holdings.json').write_text(json.dumps(holdings))
        with patch.object(engine,'download',side_effect=self.quote):
            result=engine.refresh(self.path)
        self.assertEqual([s['symbol'] for s in result['stocks']],
                         ['NVDA','SPCX','MRVL','AVGO','SKHY','MU','GOOG','AAPL','ORCL'])
        self.assertEqual([p['symbol'] for p in result['portfolio']['positions']],['NVDA'])
        self.assertEqual(result['portfolio']['sevenMarketValue'],30)
        self.assertEqual(result['portfolioAnalytics']['correlation']['symbols'],
                         ['NVDA','SPCX','MRVL','AVGO','SKHY','MU','GOOG'])

if __name__=='__main__':
    unittest.main()
