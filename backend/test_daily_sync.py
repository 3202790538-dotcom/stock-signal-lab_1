"""Daily service scheduling with real persisted attempts and injected providers."""
from datetime import datetime, timezone
import importlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest


def at(day, hour=2):
    return datetime(2026, 11, day, hour, tzinfo=timezone.utc)


class DailySyncTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('daily_sync'), 'daily scheduler is not implemented')
        self.api = importlib.import_module('daily_sync')
        self.folder = TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.events = []

    def service(self, **callbacks):
        return self.api.DailySync(self.path,
            callbacks.get('refresh_market', lambda: self.events.append('market')),
            callbacks.get('refresh_company', lambda s: self.events.append('company:'+s)),
            callbacks.get('refresh_news', lambda s: self.events.append('news:'+s)))

    def write(self, name, value):
        (self.path / name).write_text(json.dumps(value))

    def holdings(self):
        self.write('holdings.json', {'positions': [dict(contract_description=s, position=q,
                   currency='USD', asset_class='STK') for s, q in [('AAPL', 2), ('SPY', 1), ('MSFT', 0)]]})
        self.write('dashboard.json', {'stocks': [dict(symbol=s, currency='USD', quoteType=t)
                   for s, t in [('AAPL', 'EQUITY'), ('SPY', 'ETF'), ('MSFT', 'EQUITY'), ('NVDA', 'EQUITY')]]})

    def test_startup_catchup_persists_attempt_and_prevents_same_date_restart(self):
        service = self.service()
        self.assertTrue(service.run_due(now=at(11, 2), startup=True))
        self.assertFalse(self.service().run_due(now=at(11, 4), startup=True))
        self.assertEqual(self.events, ['market'])
        status = service.status(now=at(11, 4))
        self.assertEqual(status['lastAttemptAt'], at(11, 2).isoformat())
        self.assertEqual(status['nextRunAt'], '2026-11-12T09:00:00+08:00')
        self.assertTrue(service.run_due(now=at(12, 1)))

    def test_startup_before_nine_does_not_consume_post_us_close_daily_cycle(self):
        service = self.service()
        self.assertTrue(service.run_due(now=at(11, 0), startup=True))
        self.assertEqual(service.status(now=at(11, 0))['nextRunAt'], '2026-11-11T09:00:00+08:00')
        self.assertFalse(self.service().run_due(now=at(11, 0), startup=True))
        self.assertTrue(service.run_due(now=at(11, 1)))
        self.assertFalse(self.service().run_due(now=at(11, 4), startup=True))
        self.assertEqual(self.events, ['market', 'market'])

    def test_normal_tick_waits_until_shanghai_nine_and_runs_once(self):
        service = self.service()
        self.assertFalse(service.run_due(now=at(11, 0)))
        self.assertTrue(service.run_due(now=at(11, 1)))
        self.assertFalse(service.run_due(now=at(11, 2)))
        self.assertEqual(self.events, ['market'])

    def test_failed_channel_preserves_success_and_does_not_auto_retry_same_day(self):
        service = self.service()
        service.run_due(now=at(10))
        def fail(): raise RuntimeError('provider temporarily failed')
        failed = self.service(refresh_market=fail)
        self.assertTrue(failed.run_due(now=at(11)))
        status = failed.status(now=at(11))
        self.assertFalse(status['running'])
        self.assertEqual(status['lastSuccessAt'], at(10).isoformat())
        self.assertEqual(status['channels']['market']['lastSuccessAt'], at(10).isoformat())
        self.assertEqual(status['channels']['market']['status'], 'error')
        self.assertTrue(status['channels']['market']['lastError'])
        self.assertFalse(self.service(refresh_market=fail).run_due(now=at(11, 3), startup=True))

    def test_research_and_news_only_for_actual_ordinary_holdings_and_stale_channels(self):
        self.holdings()
        self.write('company-AAPL.json', {'company': {'symbol': 'AAPL'}, 'updatedAt': at(10, 20).isoformat()})
        self.write('news-AAPL.json', {'items': [], 'updatedAt': at(10, 20).isoformat()})
        self.service().run_due(now=at(11))
        self.assertEqual(self.events, ['market', 'news:AAPL'])

    def test_manual_source_research_uses_manual_positions_not_broker_or_watchlist(self):
        self.holdings()
        self.write('personal-settings.json', {'accountSource': 'manual'})
        self.write('manual-account.json', {'cash': 100, 'positions': [dict(symbol='NVDA', quantity=1,
                   averageCost=100, sector='semiconductor')], 'updatedAt': at(11).isoformat()})
        self.service().run_due(now=at(11))
        self.assertEqual(self.events, ['market', 'company:NVDA', 'news:NVDA'])

    def test_verified_watchlist_equities_follow_holdings_without_duplicates_or_etfs(self):
        self.holdings()
        self.write('ibkr-watchlists.json', {'lists': [{'id': 'favorites', 'instruments': [
            {'contract_id_ex': '2', 'contract_description': 'NVDA'},
            {'contract_id_ex': '1', 'contract_description': 'AAPL'},
            {'contract_id_ex': '3', 'contract_description': 'SPY'},
            {'contract_id_ex': '4', 'contract_description': 'MISSING'},
            {'contract_id_ex': '2', 'contract_description': 'NVDA'}]}]})
        self.service().run_due(now=at(11))
        self.assertEqual(self.events, ['market', 'company:AAPL', 'company:NVDA', 'news:AAPL', 'news:NVDA'])

    def test_closed_end_fund_skips_corporate_research_but_preserves_news(self):
        self.write('holdings.json', {'positions':[dict(contract_description='DXYZ',position=1,
                   currency='USD',asset_class='STK')]})
        self.write('dashboard.json', {'stocks':[dict(symbol='DXYZ',name='Destiny Tech100 Inc.',
                   currency='USD',quoteType='EQUITY')]})
        service=self.service()
        service.run_due(now=at(11))
        self.assertEqual(self.events,['market','news:DXYZ'])
        state=service.status(now=at(11))
        self.assertEqual(state['channels']['company']['status'],'skipped')
        self.assertIsNone(state['channels']['company']['lastError'])
        self.assertEqual(state['channels']['news']['status'],'success')

    def test_fund_exclusion_requires_matching_identity_or_sourced_classification(self):
        unknown=dict(symbol='DXYZ',name='Different Issuer Inc.',currency='USD',quoteType='EQUITY')
        self.assertIsNone(self.api.fund_research_exclusion(unknown))
        self.assertIsNone(self.api.fund_research_exclusion({**unknown,'name':'Destiny Tech100 Inc.','currency':'CAD'}))
        self.assertIsNone(self.api.fund_research_exclusion({**unknown,'securityKind':'fund'}))
        verified={**unknown,'securityKind':'fund','metadataEvidence':{
            'source':'SEC company submissions','url':'https://data.sec.gov/submissions/CIK0000000001.json'}}
        self.assertIsNotNone(self.api.fund_research_exclusion(verified))

    def test_partial_market_errors_are_recorded_before_research_completes(self):
        self.write('dashboard.json', {'stocks': [], 'quoteRefresh': {
            'updatedSymbols': ['AAPL'], 'errors': [{'symbol': 'NVDA', 'message': 'unavailable'}]}})
        service = self.service()
        service.run_due(now=at(11))
        status = service.status(now=at(11))
        self.assertEqual(status['channels']['market']['status'], 'error')
        self.assertIn('NVDA', status['channels']['market']['lastError'])
        self.assertIsNone(status['lastSuccessAt'])

    def test_incomplete_completed_bars_keep_last_success_and_report_partial_count(self):
        service = self.service()
        service.run_due(now=at(10))
        self.write('dashboard.json', {'stocks': [], 'quoteRefresh': {
            'updatedSymbols': ['NVDA'], 'errors': []}, 'marketWarnings': [
            {'symbol': 'NVDA', 'date': '2026-11-10', 'missingFields': ['close']}]})
        service.run_due(now=at(11))
        status = service.status(now=at(11))
        channel = status['channels']['market']
        self.assertEqual(channel['status'], 'error')
        self.assertEqual(channel['updatedCount'], 1)
        self.assertIn('NVDA', channel['lastError'])
        self.assertEqual(channel['lastSuccessAt'], at(10).isoformat())
        self.assertEqual(status['lastSuccessAt'], at(10).isoformat())

    def test_channel_failure_keeps_other_channels_running_and_records_symbol_error(self):
        self.holdings()
        service = self.service(refresh_company=lambda s: {'status': 'error', 'lastError': '财报暂不可用'})
        service.run_due(now=at(11))
        status = service.status(now=at(11))
        self.assertEqual(status['channels']['company']['status'], 'error')
        self.assertEqual(status['channels']['news']['status'], 'success')
        self.assertEqual(self.events, ['market', 'news:AAPL'])

    def test_concurrent_call_cannot_duplicate_running_update_and_status_stays_small(self):
        entered, release = Event(), Event()
        def refresh(): entered.set(); release.wait(3)
        service = self.service(refresh_market=refresh)
        worker = Thread(target=lambda: service.run_due(now=at(11)))
        worker.start(); self.addCleanup(lambda: (release.set(), worker.join(3)))
        self.assertTrue(entered.wait(1))
        self.assertFalse(service.run_due(now=at(11)))
        status = service.status(now=at(11))
        self.assertTrue(status['running'])
        self.assertNotIn('stocks', status)
        self.assertNotIn('positions', status)
        release.set(); worker.join(3)
        self.assertFalse(service.status(now=at(11))['running'])

    def test_separate_scheduler_instances_share_the_same_exclusion_lock(self):
        entered, release = Event(), Event()
        def refresh(): entered.set(); release.wait(3)
        first = self.service(refresh_market=refresh)
        second = self.service()
        worker = Thread(target=lambda: first.run_due(now=at(11)))
        worker.start(); self.addCleanup(lambda: (release.set(), worker.join(3)))
        self.assertTrue(entered.wait(1))
        self.assertFalse(second.run_due(now=at(11)))
        self.assertEqual(self.events, [])
        release.set(); worker.join(3)
        self.assertFalse(second.run_due(now=at(11)))

    def test_start_is_opt_in_and_stop_ends_background_scheduling(self):
        entered = Event()
        service = self.api.DailySync(self.path, entered.set, lambda s: None, lambda s: None,
                                    clock=lambda: at(11), interval_seconds=.01)
        self.assertFalse(entered.is_set())
        self.assertFalse(service.status(now=at(11))['running'])
        service.start(); self.addCleanup(service.stop)
        self.assertTrue(entered.wait(1))
        service.stop(timeout=2)
        self.assertFalse(service.status(now=at(11))['running'])
        self.assertIsNotNone(service.status(now=at(11))['lastSuccessAt'])


if __name__ == '__main__': unittest.main()
