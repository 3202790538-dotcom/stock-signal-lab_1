"""Synthetic SEC facts exercise period arithmetic, units and missing-data rules."""
import unittest
from unittest.mock import patch

import financials


def fact(metric, start, end, value, *, unit='USD', fp='Q1', fy=2025,
         filed='2025-11-01', accession='0000000001-25-000001', **extra):
    concepts = {'revenue': 'RevenueFromContractWithCustomerExcludingAssessedTax',
                'netIncome': 'NetIncomeLoss',
                'operatingCashFlow': 'NetCashProvidedByUsedInOperatingActivities',
                'capex': 'PaymentsToAcquirePropertyPlantAndEquipment',
                'eps': 'EarningsPerShareDiluted'}
    return dict(concept='us-gaap:'+concepts[metric], period_start=start, period_end=end,
                numeric_value=value, unit=unit, fiscal_year=fy, fiscal_period=fp,
                filing_date=filed, form_type='10-K' if fp=='FY' else '10-Q',
                accession=accession, **extra)


def quarter(start, end, value=100, currency='USD'):
    return dict(periodStart=start, periodEnd=end, revenue=value, netIncome=value/5,
                operatingCashFlow=value/3, capex=value/10, freeCashFlow=value/4,
                eps=1, currency=currency)


class NormalizationTests(unittest.TestCase):
    # Removing duration subtraction would mislabel YTD cash as quarterly cash.
    def test_ytd_cash_is_differenced_and_q4_requires_exact_three_quarters(self):
        rows=[]
        for metric, values in [('revenue',[100,300,600,1000]),
                               ('netIncome',[10,30,60,100]),
                               ('operatingCashFlow',[15,40,80,130]),
                               ('capex',[5,10,20,30])]:
            for end,val,fp in zip(['2025-03-31','2025-06-30','2025-09-30','2025-12-31'],values,['Q1','Q2','Q3','FY']):
                rows.append(fact(metric,'2025-01-01',end,val,fp=fp,filed='2026-02-01'))
        q=financials.normalize_facts(rows,cik=1)
        self.assertEqual([x['revenue'] for x in q],[100,200,300,400])
        self.assertEqual([x['operatingCashFlow'] for x in q],[15,25,40,50])
        self.assertEqual(q[-1]['freeCashFlow'],40)
        self.assertTrue(q[-1]['derived'])
        self.assertEqual(q[-1]['fiscalLabel'],'FY2025 Q4')
        self.assertIn('sec.gov/Archives/edgar/data/1/',q[-1]['source'])
        self.assertEqual(q[-1]['metricSources']['revenue']['method'],'annual-minus-three-quarters')

    def test_annual_does_not_create_quarters_without_exact_coverage(self):
        rows=[fact('revenue','2025-01-01','2025-03-30',100),
              fact('revenue','2025-04-01','2025-06-30',100,fp='Q2'),
              fact('revenue','2025-07-01','2025-09-30',100,fp='Q3'),
              fact('revenue','2025-01-01','2025-12-31',450,fp='FY')]
        q=financials.normalize_facts(rows,cik=1)
        self.assertEqual(len(q),3)
        self.assertFalse(any(x['periodEnd']=='2025-12-31' for x in q))

    def test_missing_cash_or_capex_stays_null_and_units_are_not_scaled_twice(self):
        q=financials.normalize_facts([fact('revenue','2025-01-01','2025-03-31',1000000,scale=1000000),
                                     fact('netIncome','2025-01-01','2025-03-31',0)],cik=1)[0]
        self.assertEqual(q['revenue'],1000000)
        self.assertEqual(q['netIncome'],0)
        self.assertIsNone(q['capex'])
        self.assertIsNone(q['freeCashFlow'])

    def test_wrong_units_and_dimensioned_facts_are_excluded(self):
        rows=[fact('revenue','2025-01-01','2025-03-31',10,unit='shares'),
              fact('revenue','2025-01-01','2025-03-31',700,dimensions={'Product':'Chip'}),
              fact('revenue','2025-01-01','2025-03-31',100)]
        self.assertEqual(financials.normalize_facts(rows,cik=1)[0]['revenue'],100)

    def test_later_changed_quarter_cannot_be_subtracted_from_older_ytd(self):
        rows=[fact('revenue','2025-01-01','2025-03-31',100,filed='2025-05-01'),
              fact('revenue','2025-01-01','2025-06-30',300,fp='Q2',filed='2025-08-01'),
              fact('revenue','2025-01-01','2025-03-31',120,filed='2026-05-01')]
        q=financials.normalize_facts(rows,cik=1)
        self.assertEqual(q[0]['revenue'],120)
        self.assertFalse(any(x['periodEnd']=='2025-06-30' for x in q))

    def test_unchanged_later_comparison_uses_original_asof_source_for_derivation(self):
        rows=[fact('revenue','2025-01-01','2025-03-31',100,filed='2025-05-01'),
              fact('revenue','2025-01-01','2025-06-30',300,fp='Q2',filed='2025-08-01'),
              fact('revenue','2025-01-01','2025-03-31',100,filed='2026-05-01')]
        q=financials.normalize_facts(rows,cik=1)[-1]
        self.assertEqual(q['revenue'],200)
        inputs=q['metricSources']['revenue']['inputs']
        self.assertTrue(all(x['filedAt']<=q['filedAt'] for x in inputs))
        self.assertEqual(q['filedAt'],'2025-08-01')

    def test_latest_restatement_wins_without_duplicate_quarters(self):
        rows=[fact('revenue','2025-01-01','2025-03-31',100,filed='2025-05-01'),
              fact('revenue','2025-01-01','2025-03-31',120,filed='2026-05-01')]
        q=financials.normalize_facts(rows,cik=1)
        self.assertEqual(len(q),1)
        self.assertEqual(q[0]['revenue'],120)
        self.assertEqual(q[0]['filedAt'],'2026-05-01')

    def test_fiscal_label_uses_actual_period_not_comparative_report_year(self):
        rows=[fact('revenue','2024-02-01','2025-01-31',1000,fp='FY',fy=2025,filed='2025-03-01'),
              fact('revenue','2024-02-01','2025-01-31',1000,fp='FY',fy=2026,filed='2026-03-01'),
              fact('revenue','2024-02-01','2024-04-30',200,fp='FY',fy=2026,filed='2026-03-01')]
        self.assertEqual(financials.normalize_facts(rows,cik=1)[0]['fiscalLabel'],'FY2025 Q1')

    def test_foreign_annual_fallback_is_not_a_quarter_or_ttm(self):
        rows=[dict(fact('revenue','2025-01-01','2025-12-31',900,unit='EUR',fp='FY'),
                   form_type='20-F')]
        self.assertEqual(financials.normalize_facts(rows,cik=1),[])
        annuals=financials.normalize_annual_facts(rows,cik=1)
        self.assertEqual(annuals[0]['periodType'],'annual')
        self.assertEqual(annuals[0]['fiscalLabel'],'FY2025')
        self.assertEqual(annuals[0]['revenue'],900)
        self.assertIsNone(financials.calculate_ttm(annuals))

    def test_ytd_cannot_subtract_a_different_fiscal_start_or_currency(self):
        rows=[fact('operatingCashFlow','2025-01-02','2025-03-31',10),
              fact('operatingCashFlow','2025-01-01','2025-06-30',60,fp='Q2'),
              fact('operatingCashFlow','2025-01-01','2025-03-31',20,unit='EUR')]
        q=financials.normalize_facts(rows,cik=1)
        self.assertFalse(any(x['periodEnd']=='2025-06-30' for x in q))

    def test_eps_is_not_derived_by_subtracting_annual_eps(self):
        rows=[fact('eps','2025-01-01',end,value,unit='USD/shares',fp=fp) for end,value,fp in
              [('2025-03-31',1,'Q1'),('2025-06-30',3,'Q2'),('2025-09-30',5,'Q3'),('2025-12-31',8,'FY')]]
        q=financials.normalize_facts(rows,cik=1)
        self.assertEqual(len(q),1)
        self.assertEqual(q[0]['eps'],1)

    def test_cross_currency_metrics_are_not_joined(self):
        rows=[fact('revenue','2025-01-01','2025-03-31',100,unit='USD'),
              fact('netIncome','2025-01-01','2025-03-31',1000,unit='KRW')]
        q=financials.normalize_facts(rows,cik=1)
        self.assertEqual(len(q),1)
        self.assertIsNone(q[0]['netIncome'])


class TTMAndValuationTests(unittest.TestCase):
    def setUp(self):
        self.q=[quarter('2025-01-01','2025-03-31'),quarter('2025-04-01','2025-06-30'),
                quarter('2025-07-01','2025-09-30'),quarter('2025-10-01','2025-12-31')]

    def test_ttm_uses_actual_four_quarters_and_toolkit_margin(self):
        from financetoolkit.ratios import profitability_model
        with patch.object(profitability_model,'get_net_profit_margin',wraps=profitability_model.get_net_profit_margin) as calculator:
            t=financials.calculate_ttm(self.q)
        self.assertTrue(calculator.called)
        self.assertEqual(t['revenue'],400)
        self.assertAlmostEqual(t['netMargin'],.2)
        self.assertEqual(t['eps'],4)
        self.assertIn('split',t['epsCaveat'])

    def test_ttm_rejects_gaps_overlaps_currencies_and_short_history(self):
        self.assertIsNone(financials.calculate_ttm(self.q[:3]))
        for changed in ({'periodStart':'2025-07-02'},{'periodStart':'2025-06-30'},{'currency':'KRW'}):
            broken=[dict(x) for x in self.q];broken[2].update(changed)
            self.assertIsNone(financials.calculate_ttm(broken))

    def test_ttm_missing_one_metric_keeps_that_sum_null(self):
        self.q[1]['freeCashFlow']=None
        t=financials.calculate_ttm(self.q)
        self.assertEqual(t['revenue'],400)
        self.assertIsNone(t['freeCashFlow'])
        self.assertIsNone(t['fcfMargin'])

    def test_valuation_nonpositive_denominator_currency_and_adr_are_null(self):
        t=financials.calculate_ttm(self.q);t['netIncome']=-1;t['freeCashFlow']=0
        quote={'price':10,'priceAsOf':'2026-01-01','currency':'USD','marketCap':1000,'source':'Yahoo Finance'}
        v=financials.calculate_valuation(t,quote,currency='USD')
        self.assertIsNone(v['pe']);self.assertIsNone(v['pfcf']);self.assertEqual(v['ps'],2.5)
        self.assertIsNone(financials.calculate_valuation(t,quote,currency='KRW')['ps'])
        self.assertIsNone(financials.calculate_valuation(t,quote,currency='USD',adr=True)['pe'])

    def test_dimensioned_share_class_cannot_replace_whole_issuer_shares(self):
        records=[dict(concept='dei:EntityCommonStockSharesOutstanding',unit='shares',numeric_value=100,
                      period_end='2025-09-01',filing_date='2025-10-01'),
                 dict(concept='dei:EntityCommonStockSharesOutstanding',unit='shares',numeric_value=10,
                      period_end='2025-10-01',filing_date='2025-11-01',dimensions={'Class':'B'})]
        quote=financials._quote('TEST',{'TEST':{'close':10,'asOf':'2025-11-01','currency':'USD'}},records,[])
        self.assertEqual(quote['marketCap'],1000)
        self.assertEqual(quote['sharesAsOf'],'2025-09-01')

    def test_estimated_market_cap_keeps_actual_share_filing_source(self):
        records=[dict(concept='dei:EntityCommonStockSharesOutstanding', unit='shares',
                      numeric_value=100,period_end='2025-10-01',filing_date='2025-10-10',
                      accession='0000000001-25-000001')]
        warnings=[]
        q=financials._quote('TEST',{'TEST':{'close':10,'asOf':'2025-11-01','currency':'USD'}},records,warnings,cik=1)
        self.assertEqual(q['marketCap'],1000)
        self.assertEqual(q['sharesAsOf'],'2025-10-01')
        self.assertIn('/1/000000000125000001/',q['sharesSource'])
        self.assertTrue(any('拆股' in x for x in warnings))

    def test_unavailable_provider_is_transparent_and_never_returns_mock_values(self):
        with patch.object(financials,'_load_sec_company',side_effect=RuntimeError('upstream blocked')):
            r=financials.build_company_research('NVDA','Research test@example.invalid',market_prices={})
        self.assertEqual(r['status'],'unavailable')
        self.assertEqual(r['quarters'],[])
        self.assertTrue(any('upstream blocked' in x for x in r['warnings']))
        self.assertIsNone(r['valuation'])


# Synthetic HTML mirrors official table structure; numbers are test-only values.
SKHYNIX_FIXTURE = """<html><article class="post"><h2 class="post-title">
SK hynix Announces 1Q26 Financial Results</h2><ul><li>April 22, 2026</li></ul>
<table><tr><td><div>Unit: Billion KRW</div><table>
<tr><td rowspan="2"></td><td rowspan="2">1Q26</td><td colspan="2">QoQ</td><td colspan="2">YoY</td></tr>
<tr><td>4Q25</td><td>Change</td><td>1Q25</td><td>Change</td></tr>
<tr><td>Revenue</td><td>1,234.5</td><td>900.0</td><td>37%</td><td>800.0</td><td>54%</td></tr>
<tr><td>Net Income</td><td>123.4</td><td>(20.5)</td><td>100%</td><td>-10.0</td><td>100%</td></tr>
</table></td></tr></table><p>Cash balance 55 trillion won; plans to return 50% of FCF.</p>
</article></html>"""


class SkHynixOfficialIRTests(unittest.TestCase):
    def test_span_headers_comparative_quarters_and_krw_billion_units(self):
        quarters=financials.parse_skhynix_release(SKHYNIX_FIXTURE,'https://news.skhynix.com/en/example-results/')
        self.assertEqual(len(quarters),3)
        current=quarters[-1]
        self.assertEqual(current['fiscalLabel'],'FY2026 Q1')
        self.assertEqual(current['periodStart'],'2026-01-01')
        self.assertEqual(current['periodEnd'],'2026-03-31')
        self.assertEqual(current['filedAt'],'2026-04-22')
        self.assertEqual(current['currency'],'KRW')
        self.assertEqual(current['revenue'],1234500000000)
        self.assertEqual(current['netIncome'],123400000000)
        self.assertIsNone(current['operatingCashFlow'])
        self.assertIsNone(current['capex'])
        self.assertIsNone(current['freeCashFlow'])
        self.assertEqual(quarters[1]['netIncome'],-20500000000)
        self.assertEqual(quarters[0]['netIncome'],-10000000000)
        self.assertFalse(current['derived'])
        self.assertIn('KRW',current['metricSources']['revenue']['unit'])

    def test_annual_headers_are_not_interpreted_as_quarters(self):
        annual=SKHYNIX_FIXTURE.replace('1Q26','FY25').replace('4Q25','FY24').replace('1Q25','Change')
        with self.assertRaisesRegex(ValueError,'quarter'):
            financials.parse_skhynix_release(annual,'https://news.skhynix.com/en/annual/')

    def test_krw_scaling_preserves_reported_decimal_without_binary_roundoff(self):
        html=SKHYNIX_FIXTURE.replace('1,234.5','32,826.7')
        q=financials.parse_skhynix_release(html,'https://news.skhynix.com/en/example-results/')[-1]
        self.assertEqual(q['revenue'],32826700000000)

    def test_missing_units_date_or_wrong_currency_fail_closed(self):
        for html in (SKHYNIX_FIXTURE.replace('Unit: Billion KRW','Unit: Billion USD'),
                     SKHYNIX_FIXTURE.replace('April 22, 2026','date unknown'),
                     SKHYNIX_FIXTURE.replace('Unit: Billion KRW','')):
            with self.assertRaises(ValueError):
                financials.parse_skhynix_release(html,'https://news.skhynix.com/en/example-results/')

    def test_discovery_uses_observed_official_links_and_orders_latest_first(self):
        html="""<a href="https://untrusted.example/3q26-financial-results/">SK hynix Announces 3Q26 Financial Results</a>
        <a href="/en/q1-2026-business-results/">SK hynix Announces 1Q26 Financial Results</a>
        <a href="/en/annual2025/">SK hynix Announces FY25 Financial Results</a>
        <a href="/en/q3-2026-earnings-conference-call/">Q3 2026 Conference Call</a>"""
        links=financials.discover_skhynix_releases(html,'https://news.skhynix.com/en/category/ir/')
        self.assertEqual([u for u in links],['https://news.skhynix.com/en/q1-2026-business-results/',
                                            'https://news.skhynix.com/en/annual2025/'])

    def test_release_discovery_deduplicates_same_page_anchor_fragments(self):
        url='https://news.skhynix.com/en/q3-2026-business-results/'
        html='<a href="#main-content">Skip content</a><a href="'+url+'">SK hynix Announces 3Q26 Financial Results</a>'
        self.assertEqual(financials.discover_skhynix_releases(html,url),[url])

    def test_ir_fallback_keeps_adr_quote_usd_and_all_ratios_unknown(self):
        q=financials.parse_skhynix_release(SKHYNIX_FIXTURE,'https://news.skhynix.com/en/example-results/')
        data={'quarters':q,'filings':[],'warnings':[],'retrievedAt':'2026-11-09T00:00:00Z'}
        with patch.object(financials,'_load_skhynix_cached',return_value=data):
            result=financials._build_single('SKHY','test@example.invalid',
                         {'SKHY':{'close':10,'asOf':'2026-11-06','currency':'USD'}})
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['currency'],'KRW')
        self.assertEqual(result['valuation']['currency'],'USD')
        for key in ('marketCap','pe','ps','pfcf'): self.assertIsNone(result['valuation'][key])


if __name__=='__main__': unittest.main()
