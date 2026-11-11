"""Public classifications must be sourced, current, and bound to quote identity."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import importlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

NOW = datetime(2026, 11, 11, 3, tzinfo=timezone.utc)


def quote(symbol='AAPL', name='Apple Inc.', kind='EQUITY'):
    return dict(symbol=symbol, name=name, quoteType=kind, quoteStatus='ready', currency='USD', sector='display theme')


def sec(symbol='AAPL', sic='3571'):
    return dict(cik=320193, name='Apple Inc.', tickers=[symbol], sic=sic,
                sicDescription='Electronic Computers', entityType='operating')


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('security_metadata'), 'public metadata resolver is missing')
        self.api = importlib.import_module('security_metadata')

    def test_sec_sector_is_evidenced_and_unknown_is_not_other(self):
        item = self.api.sec_record(quote(), sec(), now=NOW)
        self.assertEqual(item['sectorClass'], 'other')
        self.assertEqual(item['evidence']['url'], 'https://data.sec.gov/submissions/CIK0000320193.json')
        self.assertEqual(item['evidence']['checkedAt'], NOW.isoformat())
        for code in ['3674', '3559']:
            self.assertEqual(self.api.sec_record(quote(), sec(sic=code), now=NOW)['sectorClass'], 'semiconductor')
        for code in ['', '0', '6770', '9999', 'abcd']:
            self.assertEqual(self.api.sec_record(quote(), sec(sic=code), now=NOW)['sectorClass'], 'unknown')

    def test_ticker_mismatch_cannot_be_marked_verified(self):
        with self.assertRaises(ValueError):
            self.api.sec_record(quote('SPCX', 'Space Exploration Technologies Corp.'), sec(), now=NOW)

    def test_funds_have_unknown_sector_exposure(self):
        q = quote('QQQ', 'Invesco QQQ Trust', 'ETF')
        item = self.api.fund_record(q, now=NOW)
        self.assertEqual(item['securityKind'], 'fund')
        self.assertEqual(item['sectorClass'], 'unknown')
        c = sec(); c['entityType'] = 'investment'; c['sic'] = ''
        self.assertEqual(self.api.sec_record(quote(), c, now=NOW)['securityKind'], 'fund')

    def test_enrichment_preserves_input_display_sector_and_rejects_stale_identity(self):
        with TemporaryDirectory() as directory:
            item = self.api.sec_record(quote(), sec(), now=NOW)
            self.api.save_metadata(directory, {'AAPL': item})
            market = {'stocks': [quote()], 'generatedAt': NOW.isoformat()}
            before = deepcopy(market)
            enriched = self.api.enrich_market(market, directory, now=NOW)
            self.assertEqual(market, before)
            self.assertEqual(enriched['stocks'][0]['sector'], 'display theme')
            self.assertEqual(enriched['stocks'][0]['sectorClass'], 'other')
            self.assertIn('url', enriched['stocks'][0]['metadataEvidence'])
            market['stocks'][0]['name'] = 'Different issuer'
            self.assertNotIn('sectorClass', self.api.enrich_market(market, directory, now=NOW)['stocks'][0])
            market['stocks'][0] = quote()
            self.assertNotIn('sectorClass', self.api.enrich_market(market, directory, now=NOW+timedelta(days=31))['stocks'][0])

    def test_failed_refresh_preserves_last_valid_evidence_without_fabrication(self):
        with TemporaryDirectory() as directory:
            previous = self.api.sec_record(quote(), sec(), now=NOW)
            self.api.save_metadata(directory, {'AAPL':previous})
            def unavailable(_symbol): raise RuntimeError('private exception detail')
            result = self.api.verify_metadata({'stocks':[quote(), quote('ZZZ', 'Unknown company')]}, directory,
                        fetch_sec=unavailable, now=NOW+timedelta(days=1))
            self.assertEqual(result['records']['AAPL']['evidence'], previous['evidence'])
            self.assertEqual(result['records']['AAPL']['status'], 'verified')
            self.assertEqual(result['records']['ZZZ']['status'], 'error')
            self.assertEqual(result['records']['ZZZ']['sectorClass'], 'unknown')
            self.assertNotIn('private exception detail', json.dumps(result))

    def test_bad_cache_does_not_supply_unverified_classification(self):
        with TemporaryDirectory() as directory:
            item = self.api.sec_record(quote(), sec(), now=NOW)
            item.pop('evidence')
            self.api.save_metadata(directory, {'AAPL': item})
            enriched = self.api.enrich_market({'stocks':[quote()]}, directory, now=NOW)
            self.assertNotIn('sectorClass', enriched['stocks'][0])
            item = self.api.sec_record(quote(), sec(), now=NOW)
            item.pop('sectorClass')
            self.api.save_metadata(directory, {'AAPL': item})
            self.assertNotIn('sectorClass', self.api.enrich_market({'stocks':[quote()]}, directory, now=NOW)['stocks'][0])


if __name__ == '__main__': unittest.main()
