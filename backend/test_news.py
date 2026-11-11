from datetime import datetime,timezone
import unittest
import news

class NewsTests(unittest.TestCase):
    def test_imported_short_ticker_does_not_match_another_company_substring(self):
        rss='''<rss><channel>
        <item><title>Apple earnings</title><link>https://example.com/apple</link><pubDate>Mon, 09 Nov 2026 09:00:00 GMT</pubDate></item>
        <item><title>APP earnings</title><link>https://example.com/app</link><pubDate>Mon, 09 Nov 2026 09:00:00 GMT</pubDate></item>
        </channel></rss>'''
        rows=news.parse_rss('APP',rss,now=datetime(2026,11,9,10,tzinfo=timezone.utc),strict_symbol=True)
        self.assertEqual([r['title'] for r in rows],['APP earnings'])

    def test_company_ir_announcement_is_not_attributed_to_sec(self):
        rows=news.merge_filings('SKHY',[],[{'date':'2026-10-28','form':'IR','title':'Quarter results','url':'https://news.skhynix.com/en/q3-2026-business-results/'}])
        self.assertEqual(rows[0]['source'],'SK hynix 官方 IR')
        self.assertNotIn('监管披露',rows[0]['summary'])

    def test_selected_company_feed_excludes_unrelated_articles_and_labels_mentions(self):
        rss="""<rss><channel>
        <item><title>Nvidia earnings</title><link>https://example.com/n</link><pubDate>Mon, 09 Nov 2026 09:00:00 GMT</pubDate></item>
        <item><title>Argenx biotech</title><description>Biotech research</description><link>https://example.com/a</link><pubDate>Mon, 09 Nov 2026 09:00:00 GMT</pubDate></item>
        <item><title>Semiconductor peers</title><description>Comparison with Nvidia</description><link>https://example.com/p</link><pubDate>Mon, 09 Nov 2026 09:00:00 GMT</pubDate></item>
        </channel></rss>"""
        rows=news.parse_rss('NVDA',rss,now=datetime(2026,11,9,10,tzinfo=timezone.utc),strict_symbol=True)
        self.assertEqual([r['title'] for r in rows],['Nvidia earnings','Semiconductor peers'])
        self.assertEqual(rows[0]['relevance'],'direct')
        self.assertEqual(rows[1]['relevance'],'mention')

    def test_rss_sorting_deduplication_dates_and_safe_links(self):
        rss="""<rss><channel>
        <item><title>Old earnings story</title><link>https://example.com/a?utm_source=rss</link><pubDate>Fri, 06 Nov 2026 12:00:00 GMT</pubDate></item>
        <item><title>New capacity and capex</title><link>https://example.com/b</link><pubDate>Mon, 09 Nov 2026 09:00:00 GMT</pubDate></item>
        <item><title>Duplicate</title><link>https://example.com/b?utm_medium=feed</link><pubDate>Mon, 09 Nov 2026 09:00:00 GMT</pubDate></item>
        <item><title>Bad link</title><link>javascript:alert(1)</link><pubDate>Mon, 09 Nov 2026 09:00:00 GMT</pubDate></item>
        <item><title>No date</title><link>https://example.com/c</link></item>
        <item><title>Future story</title><link>https://example.com/d</link><pubDate>Tue, 10 Nov 2026 09:00:00 GMT</pubDate></item>
        </channel></rss>"""
        rows=news.parse_rss('NVDA',rss,now=datetime(2026,11,9,10,tzinfo=timezone.utc))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['title'],'New capacity and capex')
        self.assertEqual(rows[0]['publishedAt'],'2026-11-09T09:00:00+00:00')
        self.assertIn('资本开支 / 自由现金流',rows[0]['impacts'])
        self.assertEqual(rows[0]['impactBasis'],'rule')
        self.assertNotIn('reported growth',rows[0]['summary'])

    def test_html_error_page_is_not_an_empty_successful_feed(self):
        with self.assertRaises(ValueError):news.parse_rss('NVDA','<html><body>Unavailable</body></html>')

    def test_filings_merge_uses_actual_filing_date_and_preserves_sources(self):
        rows=news.merge_filings('NVDA',[],[{'date':'2026-11-06','form':'10-Q','title':'Quarterly report','url':'https://www.sec.gov/Archives/example.htm','accession':'a'}])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['kind'],'filing')
        self.assertEqual(rows[0]['publishedAt'],'2026-11-06')
        self.assertEqual(rows[0]['form'],'10-Q')
        self.assertIn('营收 / 利润',rows[0]['impacts'])
        self.assertEqual(rows[0]['impactBasis'],'filing')

    def test_missing_or_unsafe_filing_is_not_rendered_as_a_valid_event(self):
        rows=news.merge_filings('NVDA',[],[{'date':'bad','form':'8-K','url':'https://www.sec.gov/a'},{'date':'2026-11-06','form':'8-K','url':'data:bad'}])
        self.assertEqual(rows,[])
