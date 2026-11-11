import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
import server

HEADERS = {'host': '127.0.0.1:18765'}


def dashboard_fixture():
    bars = [{'date': '2026-11-06', 'open': 99, 'high': 101, 'low': 98, 'close': 100, 'volume': 1000,
             'ma20': None, 'ma50': None, 'ma200': None},
            {'date': '2026-11-09', 'open': 100, 'high': 102, 'low': 99, 'close': 101, 'volume': None,
             'ma20': None, 'ma50': None, 'ma200': None}]
    return {'generatedAt': '2026-11-10T01:00:00+00:00', 'marketAsOf': '2026-11-09',
            'marketSource': 'fixture', 'engine': 'fixture',
            'stocks': [{'symbol': 'NVDA', 'name': 'Example company', 'sector': '', 'asOf': '2026-11-09',
                        'close': 101, 'changePct': 1, 'ret20': None, 'ret60': None, 'ma20': None, 'ma50': None,
                        'ma200': None, 'rsi': None, 'volatility': None, 'atr': None, 'relative20': None,
                        'bars': 2, 'status': 'weak', 'signals': [], 'history': bars, 'observation': 'fixture'}],
            'benchmark': {'symbol': 'SPY', 'asOf': '2026-11-09', 'close': 101, 'changePct': 1, 'history': bars},
            'portfolio': {'asOf': '未提供', 'netLiquidation': None, 'cash': None, 'leverage': None,
                          'sevenMarketValue': None, 'sevenPnl': None, 'positions': []},
            'fundamentals': [], 'rules': [], 'sources': []}


class RuntimeOptimizationTests(unittest.TestCase):
    def test_refresh_status_does_not_read_market_or_account_files(self):
        with TestClient(server.app) as client, patch.object(Path, 'read_text', side_effect=AssertionError('unexpected file read')):
            with patch.object(server, 'last_error', 'provider unavailable'):
                server.refresh_lock.acquire()
                try:
                    response = client.get('/api/refresh-status', headers=HEADERS)
                finally:
                    server.refresh_lock.release()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'running': True, 'lastError': 'provider unavailable'})
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_dashboard_compression_preserves_payload_and_cache_envelope(self):
        with TemporaryDirectory() as folder, TestClient(server.app) as client, patch.object(server, 'DATA', Path(folder)):
            path = Path(folder)/'dashboard.json'
            original = {**dashboard_fixture(), 'padding': 'quote data ' * 1000}
            path.write_text(json.dumps(original))
            compressed = client.get('/api/dashboard', headers={**HEADERS, 'accept-encoding': 'gzip'})
            plain = client.get('/api/dashboard', headers={**HEADERS, 'accept-encoding': 'identity'})
            self.assertEqual(compressed.status_code, 200)
            self.assertEqual(compressed.headers.get('content-encoding'), 'gzip')
            self.assertEqual(compressed.json(), plain.json())
            self.assertLess(int(compressed.headers['content-length']), len(plain.content))
            self.assertEqual(server.read_cache(path), original)

    def test_invalid_dashboard_is_recoverable_and_replacement_is_visible(self):
        with TemporaryDirectory() as folder, TestClient(server.app, raise_server_exceptions=False) as client, patch.object(server, 'DATA', Path(folder)):
            path = Path(folder)/'dashboard.json'
            for value in ('{', '[]', 'null'):
                path.write_text(value)
                response = client.get('/api/dashboard', headers=HEADERS)
                self.assertEqual(response.status_code, 503)
                self.assertIn('行情', response.json()['error'])
            path.write_text(json.dumps(dashboard_fixture()))
            self.assertEqual(client.get('/api/dashboard', headers=HEADERS).json()['marketAsOf'], '2026-11-09')

    def test_incomplete_dashboard_envelopes_fail_closed_without_crashing(self):
        invalid = [{'stocks': []}]
        for key, value in (('stocks', None), ('stocks', [None]), ('stocks', [{}]), ('stocks', []),
                           ('portfolio', {}), ('portfolio', {'positions': None}), ('fundamentals', None),
                           ('fundamentals', [None]), ('rules', [None]), ('sources', [{}]),
                           ('benchmark', None), ('benchmark', {'close': '100', 'changePct': 1}),
                           ('generatedAt', 'invalid'), ('marketAsOf', '2026-02-30')):
            invalid.append({**dashboard_fixture(), key: value})
        bad_history = dashboard_fixture()
        bad_history['stocks'][0]['history'] = [None]
        invalid.append(bad_history)
        bad_signal = dashboard_fixture()
        bad_signal['stocks'][0]['signals'] = [None]
        invalid.append(bad_signal)
        bad_position = dashboard_fixture()
        bad_position['portfolio']['positions'] = [None]
        invalid.append(bad_position)
        with TemporaryDirectory() as folder, TestClient(server.app, raise_server_exceptions=False) as client, patch.object(server, 'DATA', Path(folder)):
            path = Path(folder)/'dashboard.json'
            for value in invalid:
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    response = client.get('/api/dashboard', headers=HEADERS)
                    self.assertEqual(response.status_code, 503)
                    self.assertIn('行情', response.json()['error'])
            path.write_text(json.dumps(dashboard_fixture()))
            response = client.get('/api/dashboard', headers=HEADERS)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['portfolio']['positions'], [])

    def test_dashboard_validation_is_reused_until_the_snapshot_changes(self):
        with TemporaryDirectory() as folder, TestClient(server.app) as client, patch.object(server, 'DATA', Path(folder)):
            path = Path(folder)/'dashboard.json'
            original = dashboard_fixture()
            path.write_text(json.dumps(original))
            with patch.object(server, 'valid_dashboard', wraps=server.valid_dashboard) as validate:
                for _ in range(2):
                    self.assertEqual(client.get('/api/dashboard', headers=HEADERS).status_code, 200)
                self.assertEqual(validate.call_count, 1)
                original['portfolio']['positions'] = None
                path.write_text(json.dumps(original))
                for _ in range(2):
                    self.assertEqual(client.get('/api/dashboard', headers=HEADERS).status_code, 503)
                self.assertEqual(validate.call_count, 2)

    def test_unchanged_file_is_read_once_and_atomic_same_size_replacement_invalidates(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'news-NVDA.json'
            path.write_text('{"items":[{"title":"one"}]}')
            original_read = Path.read_text
            reads = []
            def read(file, *args, **kwargs):
                reads.append(file)
                return original_read(file, *args, **kwargs)
            with patch.object(Path, 'read_text', read):
                first = server.read_cache(path)
                first['items'][0]['title'] = 'caller mutation'
                self.assertEqual(server.read_cache(path)['items'][0]['title'], 'one')
                self.assertEqual(len(reads), 1)
                stamp = path.stat()
                replacement = path.with_suffix('.tmp')
                replacement.write_text('{"items":[{"title":"two"}]}')
                os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                replacement.replace(path)
                self.assertEqual(server.read_cache(path)['items'][0]['title'], 'two')
                self.assertEqual(len(reads), 2)
            path.unlink()
            self.assertEqual(server.read_cache(path), {})

    def test_portfolio_cache_rechecks_freshness_and_never_mutates_cached_candidates(self):
        with TemporaryDirectory() as folder, TestClient(server.app) as client, patch.object(server, 'DATA', Path(folder)), patch.object(server, 'last_error', None):
            path = Path(folder)/'dashboard.json'
            path.write_text(json.dumps({'stocks': [{'symbol': 'NVDA'}], 'benchmark': {'history': [{'date': '2026-01-01'}]},
                                       'generatedAt': datetime.now(timezone.utc).isoformat()}))
            result = {'status': 'research_only', 'simulation': {'latestSignals': [{'status': 'candidate'}], 'state': {}}, 'warnings': []}
            with patch('portfolio_study.build_portfolio_study', return_value=result) as build:
                ready = client.post('/api/portfolio-study', json={}, headers=HEADERS).json()
                server.refresh_lock.acquire()
                try:
                    running = client.post('/api/portfolio-study', json={}, headers=HEADERS).json()
                finally:
                    server.refresh_lock.release()
                with patch.object(server, 'last_error', 'refresh failed'):
                    failed = client.post('/api/portfolio-study', json={}, headers=HEADERS).json()
                with patch.object(server, 'cache_is_stale', return_value=True):
                    expired = client.post('/api/portfolio-study', json={}, headers=HEADERS).json()
                again = client.post('/api/portfolio-study', json={}, headers=HEADERS).json()
                self.assertTrue(ready['marketSnapshotStatus']['signalsAllowed'])
                self.assertFalse(running['marketSnapshotStatus']['signalsAllowed'])
                self.assertEqual(running['simulation']['latestSignals'], [])
                self.assertFalse(failed['marketSnapshotStatus']['signalsAllowed'])
                self.assertEqual(failed['simulation']['latestSignals'], [])
                self.assertFalse(expired['marketSnapshotStatus']['signalsAllowed'])
                self.assertEqual(expired['simulation']['latestSignals'], [])
                self.assertEqual(again['simulation']['latestSignals'], [{'status': 'candidate'}])
                self.assertEqual(again['warnings'], [])
                self.assertEqual(build.call_count, 1)

    def test_study_cache_tracks_request_parameters_and_market_replacement(self):
        with TemporaryDirectory() as folder, TestClient(server.app) as client, patch.object(server, 'DATA', Path(folder)):
            path = Path(folder)/'dashboard.json'
            path.write_text('{"stocks":[{"symbol":"NVDA"}],"generatedAt":"first"}')
            with patch('strategy.build_strategy_study', return_value={'status': 'research_only'}) as build:
                for params in ({}, {}, {'targetReturnPct': 60}):
                    self.assertEqual(client.post('/api/strategy-study', json=params, headers=HEADERS).status_code, 200)
                self.assertEqual(build.call_count, 2)
                path.write_text('{"stocks":[{"symbol":"NVDA"}],"generatedAt":"second"}')
                response = client.post('/api/strategy-study', json={}, headers=HEADERS)
                self.assertEqual(response.json()['marketGeneratedAt'], 'second')
                self.assertEqual(build.call_count, 3)


class CacheSafetyTests(unittest.TestCase):
    def test_in_place_same_size_rewrite_with_restored_mtime_invalidates_cached_value(self):
        from runtime_cache import JsonFileCache
        cache = JsonFileCache()
        with TemporaryDirectory() as folder:
            path = Path(folder)/'data.json'
            path.write_text('{"version":"old"}')
            self.assertEqual(cache.read(path), {'version': 'old'})
            stamp = path.stat()
            path.write_text('{"version":"new"}')
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertEqual(path.stat().st_ino, stamp.st_ino)
            self.assertEqual(cache.read(path), {'version': 'new'})

    def test_result_cache_evicts_old_work_and_does_not_retain_failed_builds(self):
        from runtime_cache import ResultCache
        cache = ResultCache(max_entries=2)
        calls = []
        def build(key):
            calls.append(key)
            return {'value': [key]}
        for key in ('a', 'b', 'a', 'c', 'b'):
            value = cache.get_or_build(key, lambda: build(key))
            self.assertEqual(value, {'value': [key]})
            value['value'].clear()
        self.assertEqual(calls, ['a', 'b', 'c', 'b'])
        with self.assertRaises(ValueError):
            cache.get_or_build('bad', lambda: (_ for _ in ()).throw(ValueError('unavailable')))
        self.assertEqual(cache.get_or_build('bad', lambda: {'recovered': True}), {'recovered': True})

    def test_concurrent_identical_studies_build_once_and_return_private_results(self):
        from runtime_cache import ResultCache
        cache = ResultCache()
        barrier = Barrier(4)
        calls = []
        def build():
            calls.append('built')
            Event().wait(.03)
            return {'signals': ['candidate']}
        def request(_):
            barrier.wait()
            return cache.get_or_build('study', build)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(request, range(4)))
        results[0]['signals'].clear()
        self.assertEqual(calls, ['built'])
        self.assertEqual(results[1:], [{'signals': ['candidate']}] * 3)

    def test_file_cache_evicts_old_files_and_retries_replacement_during_read(self):
        from runtime_cache import JsonFileCache
        cache = JsonFileCache(max_entries=2)
        with TemporaryDirectory() as folder:
            paths = [Path(folder)/f'{name}.json' for name in ('a', 'b', 'c')]
            for path in paths:
                path.write_text('{"version":"old"}')
            original_read = Path.read_text
            reads = []
            def read(path, *args, **kwargs):
                reads.append(path.name)
                return original_read(path, *args, **kwargs)
            with patch.object(Path, 'read_text', read):
                for index in (0, 1, 0, 2, 1):
                    self.assertEqual(cache.read(paths[index]), {'version': 'old'})
            self.assertEqual(reads, ['a.json', 'b.json', 'c.json', 'b.json'])
            changed = False
            def replace_during_read(path, *args, **kwargs):
                nonlocal changed
                value = original_read(path, *args, **kwargs)
                if not changed:
                    changed = True
                    temp = path.with_suffix('.tmp')
                    temp.write_text('{"version":"new"}')
                    temp.replace(path)
                return value
            with patch.object(Path, 'read_text', replace_during_read):
                self.assertEqual(cache.read(paths[0]), {'version': 'new'})
            self.assertEqual(cache.read(paths[0]), {'version': 'new'})


if __name__ == '__main__': unittest.main()
