"""Auditable SEC company research. No API key, forecasts, or stored financial values.

EdgarTools supplies SEC company facts and filing metadata. Duration arithmetic is
kept here because income quarters and cash-flow YTD periods must not be confused.
FinanceToolkit's published ratio models calculate the margins and multiples.
Sources: https://edgartools.readthedocs.io/en/latest/api/company/
https://github.com/JerBouma/FinanceToolkit/tree/v2.3.2/financetoolkit/ratios
"""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
import json
import math
import re
import time
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse


PEERS = {
    'NVDA': ('AMD', 'AVGO'), 'MRVL': ('AVGO', 'AMD'),
    'AVGO': ('MRVL', 'NVDA'), 'MU': ('WDC', 'SNDK'),
    'GOOG': ('META', 'MSFT'), 'GOOGL': ('META', 'MSFT'),
    'SPCX': ('RKLB', 'ASTS'), 'SKHY': ('MU',),
}
NAMES = {'NVDA':'NVIDIA', 'AMD':'Advanced Micro Devices', 'AVGO':'Broadcom',
         'MRVL':'Marvell Technology', 'MU':'Micron Technology', 'WDC':'Western Digital',
         'SNDK':'Sandisk', 'GOOG':'Alphabet', 'GOOGL':'Alphabet', 'META':'Meta Platforms',
         'MSFT':'Microsoft', 'SPCX':'SpaceX', 'RKLB':'Rocket Lab', 'ASTS':'AST SpaceMobile',
         'SKHY':'SK hynix ADR'}
CONCEPTS = {
    'revenue': ('RevenueFromContractWithCustomerExcludingAssessedTax',
                'RevenueFromContractWithCustomerIncludingAssessedTax', 'Revenues',
                'SalesRevenueNet', 'SalesRevenueGoodsNet', 'Revenue'),
    'netIncome': ('NetIncomeLoss', 'ProfitLoss', 'ProfitLossAttributableToOwnersOfParent'),
    'operatingCashFlow': ('NetCashProvidedByUsedInOperatingActivities',
                          'CashFlowsFromUsedInOperatingActivities'),
    'capex': ('PaymentsToAcquirePropertyPlantAndEquipment',
              'PaymentsToAcquireProductiveAssets',
              'PurchaseOfPropertyPlantAndEquipment', 'PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets'),
    'eps': ('EarningsPerShareDiluted', 'DilutedEarningsLossPerShare'),
}
METRICS = ('revenue','netIncome','operatingCashFlow','capex','eps')
FORMS = {'10-Q','10-Q/A','10-K','10-K/A','20-F','20-F/A','40-F','40-F/A'}


def _number(value):
    if value is None or isinstance(value, bool): return None
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (TypeError,ValueError): return None


def _date(value):
    if value is None: return None
    try: return date.fromisoformat(str(value)[:10])
    except ValueError: return None


def _stamp(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _source(cik, accession):
    accession=str(accession or '')
    if not cik or not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accession): return None
    return f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/{accession}-index.html'


def _unit(unit, eps=False):
    value=str(unit or '').upper().replace('ISO4217:','').strip()
    if eps:
        value=value.replace(' PER SHARE','/SHARES')
        match=re.fullmatch(r'([A-Z]{3})/(?:SHARES|SHARE)',value)
        return match[1] if match else None
    return value if re.fullmatch(r'[A-Z]{3}',value) and value not in {'NAN'} else None


def _clean_facts(records, cik):
    """SEC values are already in their declared unit; scale is display metadata."""
    cleaned=[]
    for raw in records:
        concept=str(raw.get('concept','')).split(':')[-1]
        metric=next((m for m, concepts in CONCEPTS.items() if concept in concepts),None)
        if metric is None or raw.get('dimensions'): continue
        if raw.get('form_type') and raw['form_type'] not in FORMS: continue
        start,end=_date(raw.get('period_start')),_date(raw.get('period_end'))
        value=_number(raw.get('numeric_value',raw.get('value')))
        currency=_unit(raw.get('unit'),metric=='eps')
        if not start or not end or value is None or not currency: continue
        days=(end-start).days+1
        if not 75<=days<=380: continue
        cleaned.append(dict(metric=metric,concept=concept,start=start,end=end,value=value,
                            currency=currency,days=days,filedAt=str(raw.get('filing_date') or '')[:10],
                            fiscalYear=int(raw.get('fiscal_year') or 0),fp=str(raw.get('fiscal_period') or ''),
                            source=_source(cik,raw.get('accession')),accession=str(raw.get('accession') or ''),
                            form=raw.get('form_type',''),derived=False,method='reported',inputs=[]))
    # Dedupe exact duration, metric and currency: the latest amended/restated fact wins.
    selected={}
    for row in cleaned:
        key=(row['metric'],row['start'],row['end'],row['currency'])
        rank=(row['filedAt'],-CONCEPTS[row['metric']].index(row['concept']))
        old=selected.get(key)
        if old is None or rank>(old['filedAt'],-CONCEPTS[old['metric']].index(old['concept'])):
            selected[key]=row
    return list(selected.values()),cleaned


def _provenance(row):
    return {k:row[k] for k in ('source','filedAt','accession','concept','method')} | {
        'periodStart':str(row['start']),'periodEnd':str(row['end']),
        'currency':row['currency'], 'unit':row['currency']+'/share' if row['metric']=='eps' else row['currency'],
        'value':row['value'],'inputs':row.get('inputs',[])}


def _derived(total, pieces, start, method):
    row=dict(total)
    row.update(start=start,days=(total['end']-start).days+1,
               value=total['value']-sum(p['value'] for p in pieces),derived=True,
               filedAt=max(r['filedAt'] for r in [total,*pieces]),
               method=method,inputs=[_provenance(total)]+[_provenance(p) for p in pieces])
    return row



def _asof_input(row,asof,raw):
    """Resolve an unchanged value to evidence available by the total's filing.

    If a later amendment changed a component but the aggregate has no matching
    amendment, subtracting those vintages is ambiguous and is withheld.
    """
    if not asof: return None
    if row['filedAt'] and row['filedAt']<=asof: return row
    if not row['derived']:
        candidates=[r for r in raw if r['metric']==row['metric'] and
                    r['currency']==row['currency'] and r['start']==row['start'] and
                    r['end']==row['end'] and r['filedAt'] and r['filedAt']<=asof]
        if not candidates: return None
        available=max(candidates,key=lambda r:(r['filedAt'],-CONCEPTS[r['metric']].index(r['concept'])))
        return available if available['value']==row['value'] else None
    resolved=[]
    for source in row['inputs']:
        template=dict(source,metric=row['metric'],start=_date(source['periodStart']),
                      end=_date(source['periodEnd']),derived=source['method']!='reported')
        component=_asof_input(template,asof,raw)
        if component is None: return None
        resolved.append(component)
    if not resolved: return None
    available=_derived(resolved[0],resolved[1:],row['start'],row['method'])
    return available if available['value']==row['value'] else None

def _fiscal_label(start,end,raw):
    # SEC fy/fp describe the REPORT, including comparative facts. Use the earliest
    # annual filing covering the actual dates, then the current YTD fiscal anchor.
    anchors=defaultdict(list)
    for row in raw:
        if 330<=row['days']<=380 and row['fiscalYear'] and row['fp']=='FY':
            anchors[(row['start'],row['end'])].append(row)
    for (a,b),values in sorted(anchors.items(),key=lambda x:x[0][1]):
        if a<=start<=end<=b:
            origin=min(values,key=lambda x:x['filedAt'])
            q=min(4,max(1,round(((end-a).days+1)/((b-a).days+1)*4)))
            return f'FY{origin["fiscalYear"]} Q{q}'
    contemporaneous=[r for r in raw if r['end']==end and r['fiscalYear'] and
                     r['fp'] in {'Q1','Q2','Q3','Q4'} and _date(r['filedAt']) and
                     0<=(_date(r['filedAt'])-end).days<=180]
    if contemporaneous:
        origin=min(contemporaneous,key=lambda x:x['filedAt'])
        return f'FY{origin["fiscalYear"]} {origin["fp"]}'
    return f'{start.isoformat()} – {end.isoformat()}'


def _assemble(period_rows,raw,limit):
    grouped=defaultdict(dict)
    for row in period_rows:
        grouped[(row['start'],row['end'],row['currency'])][row['metric']]=row
    # A period has one reporting currency. Prefer revenue, never join other units.
    periods=defaultdict(list)
    for (start,end,currency),metrics in grouped.items():
        periods[(start,end)].append((currency,metrics))
    output=[]
    for (start,end),choices in sorted(periods.items()):
        currency,metrics=max(choices,key=lambda x:('revenue' in x[1],len(x[1])))
        main=metrics.get('revenue') or next(iter(metrics.values()))
        item={'periodStart':str(start),'periodEnd':str(end),'fiscalLabel':_fiscal_label(start,end,raw),
              'currency':currency,'source':main['source'],
              'filedAt':max(r['filedAt'] for r in metrics.values()),
              'derived':any(r['derived'] for r in metrics.values()),
              'metricSources':{m:_provenance(r) for m,r in metrics.items()}}
        for metric in METRICS: item[metric]=metrics.get(metric,{}).get('value')
        if item['capex'] is not None: item['capex']=abs(item['capex'])
        ocf,capex=item['operatingCashFlow'],item['capex']
        item['freeCashFlow']=ocf-capex if ocf is not None and capex is not None else None
        if item['freeCashFlow'] is not None:
            item['metricSources']['freeCashFlow']={'method':'operating-cash-flow-minus-capex',
                    'currency':currency,'unit':currency,
                    'inputs':[item['metricSources']['operatingCashFlow'],item['metricSources']['capex']]}
        output.append(item)
    # Conflicting duration boundaries are left visible to the TTM validator.
    return sorted(output,key=lambda q:q['periodEnd'])[-limit:]


def normalize_facts(records, cik=0, limit=8):
    """Return exact quarters oldest first. Annuals and YTD are never called quarters.

    Q2/Q3 require matching YTD starts and exact quarter-duration subtraction.
    Q4 requires annual minus three nonoverlapping, gapless quarter periods.
    EPS is not additive under changing weighted shares, so no derived EPS.
    """
    rows,raw=_clean_facts(records,cik)
    direct=[r for r in rows if 75<=r['days']<=105]
    for total in sorted(rows,key=lambda x:x['days']):
        if total['metric']=='eps' or not 150<=total['days']<=290: continue
        previous=[r for r in rows if r['metric']==total['metric'] and
                  r['currency']==total['currency'] and r['start']==total['start'] and
                  75<=(total['end']-r['end']).days<=105 and r['end']<total['end']]
        if previous:
            prior=_asof_input(max(previous,key=lambda x:x['end']),total['filedAt'],raw)
            if prior is None: continue
            start=prior['end']+timedelta(days=1)
            if not any(r['metric']==total['metric'] and r['currency']==total['currency'] and
                       r['start']==start and r['end']==total['end'] for r in direct):
                direct.append(_derived(total,[prior],start,'ytd-difference'))
    for total in rows:
        if total['metric']=='eps' or not 330<=total['days']<=380: continue
        pieces=[];cursor=total['start']
        for _ in range(3):
            candidates=[r for r in direct if r['metric']==total['metric'] and
                        r['currency']==total['currency'] and r['start']==cursor and r['end']<total['end']]
            if not candidates: break
            chosen=_asof_input(min(candidates,key=lambda x:x['end']),total['filedAt'],raw)
            if chosen is None: break
            pieces.append(chosen)
            cursor=chosen['end']+timedelta(days=1)
        if len(pieces)==3 and 75<=(total['end']-cursor).days+1<=105:
            if not any(r['metric']==total['metric'] and r['currency']==total['currency'] and
                       r['start']==cursor and r['end']==total['end'] for r in direct):
                direct.append(_derived(total,pieces,cursor,'annual-minus-three-quarters'))
    return _assemble(direct,raw,limit)


def normalize_annual_facts(records,cik=0,limit=3):
    rows,raw=_clean_facts(records,cik)
    annuals=_assemble([r for r in rows if 330<=r['days']<=380],raw,limit)
    for item in annuals:
        item['fiscalLabel']=item['fiscalLabel'].replace(' Q4','')
        item['periodType']='annual'
    return annuals


def calculate_ttm(quarters):
    """Metric-specific sums require the latest four exact contiguous quarters."""
    q=sorted(quarters,key=lambda x:x['periodEnd'])[-4:]
    if len(q)!=4 or len({x.get('currency') for x in q})!=1 or not q[0].get('currency'): return None
    for i,item in enumerate(q):
        a,b=_date(item.get('periodStart')),_date(item.get('periodEnd'))
        if not a or not b or not 75<=(b-a).days+1<=105: return None
        if i and a!=_date(q[i-1]['periodEnd'])+timedelta(days=1): return None
    total={'periodStart':q[0]['periodStart'],'periodEnd':q[-1]['periodEnd'],'currency':q[0]['currency'],
           'quarters':4,'epsCaveat':'Sum of four reported diluted quarter EPS; weighted shares and stock splits can affect comparability. No quarter × 4 or derived Q4 EPS.'}
    for metric in ('revenue','netIncome','operatingCashFlow','capex','freeCashFlow','eps'):
        values=[_number(x.get(metric)) for x in q]
        total[metric]=sum(values) if all(v is not None for v in values) else None
    from financetoolkit.ratios import profitability_model
    revenue=total['revenue']
    for metric,out in (('netIncome','netMargin'),('freeCashFlow','fcfMargin')):
        value=total[metric]
        # The same transparent net-income/revenue formula applies to FCF/revenue.
        total[out]=float(profitability_model.get_net_profit_margin(value,revenue)) if revenue and revenue>0 and value is not None else None
    return total


def calculate_valuation(ttm,quote,currency=None,adr=False):
    """Use issuer market cap / actual TTM; withhold nonpositive denominators."""
    if not quote: return None
    value={k:quote.get(k) for k in ('price','priceAsOf','marketCap','source','currency','sharesAsOf','sharesSource','method')}
    value.update(pe=None,ps=None,pfcf=None,periodEnd=ttm.get('periodEnd') if ttm else None)
    cap=_number(quote.get('marketCap'))
    if not ttm or cap is None or cap<=0 or adr or quote.get('currency')!=currency: return value
    from financetoolkit.ratios import valuation_model
    for denominator,key,calculator in (
        ('netIncome','pe',valuation_model.get_price_to_earnings_ratio),
        ('revenue','ps',valuation_model.get_price_to_sales_ratio),
        ('freeCashFlow','pfcf',valuation_model.get_price_to_free_cash_flow_ratio)):
        amount=_number(ttm.get(denominator))
        # For P/E, marketCap/netIncome is algebraically issuer price/issuer EPS.
        if amount is not None and amount>0: value[key]=float(calculator(cap,amount))
    return value


@lru_cache(maxsize=48)
def _load_sec_cached(symbol,identity,bucket):
    from edgar import Company,set_identity
    set_identity(identity if identity.startswith('Northstar Research ') else 'Northstar Research '+identity)
    company=Company(symbol)
    facts=company.get_facts()
    records=facts.to_dataframe(include_metadata=True,pit_mode=True).to_dict('records') if facts else []
    filings=[]
    for filing in company.get_filings(form=['10-Q','10-K','20-F','40-F']).head(8):
        filings.append({'date':str(filing.filing_date),'form':filing.form,
                        'title':f'{company.name} · {filing.form}', 'url':filing.url,
                        'accession':filing.accession_no})
    return {'name':company.name,'cik':company.cik,'records':records,'filings':filings,'retrievedAt':_stamp()}


def _load_sec_company(symbol,identity):
    return _load_sec_cached(symbol,identity,int(time.time()//21600))


def _shares(records):
    candidates=[r for r in records if str(r.get('concept','')).split(':')[-1] in
                {'EntityCommonStockSharesOutstanding','CommonStockSharesOutstanding'}
                and not r.get('dimensions') and str(r.get('unit','')).lower()=='shares' and _number(r.get('numeric_value',r.get('value'))) is not None]
    if not candidates: return None,None,None
    row=max(candidates,key=lambda r:(str(r.get('period_end')),str(r.get('filing_date'))))
    return _number(row.get('numeric_value',row.get('value'))),str(row.get('period_end'))[:10],row.get('accession')


@lru_cache(maxsize=48)
def _chart_quote(symbol,bucket):
    """A single bounded Yahoo chart request, only when dashboard has no price."""
    if not re.fullmatch(r'[A-Z0-9.^=-]{1,16}',symbol): return None
    req=Request(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d',
                headers={'User-Agent':'Mozilla/5.0 Northstar Local Research'})
    with urlopen(req,timeout=8) as response: data=json.load(response)
    results=data.get('chart',{}).get('result') or []
    if not results: return None
    meta=results[0].get('meta',{})
    stamp=meta.get('regularMarketTime')
    return {'price':_number(meta.get('regularMarketPrice')),'currency':meta.get('currency'),
            'priceAsOf':datetime.fromtimestamp(stamp,timezone.utc).isoformat() if stamp else None,
            'source':'Yahoo Finance chart · current market price'}


def _quote(symbol,market_prices,records,warnings,cik=0):
    raw=(market_prices or {}).get(symbol)
    if isinstance(raw,(float,int)): raw={'price':raw}
    if raw:
        raw=dict(raw)
        quote={'price':_number(raw.get('price',raw.get('close'))),
               'priceAsOf':raw.get('priceAsOf',raw.get('asOf')),
               'currency':raw.get('currency'), 'marketCap':_number(raw.get('marketCap')),
               'source':raw.get('source') or 'Yahoo Finance · dashboard daily close'}
    else:
        try: quote=_chart_quote(symbol,int(time.time()//3600))
        except Exception as exc:
            warnings.append(f'{symbol} 市价不可用（{type(exc).__name__}）；估值留空。');return None
    if not quote or quote.get('price') is None or not quote.get('priceAsOf'): return None
    if quote.get('marketCap') is None:
        shares,asof,accession=_shares(records)
        # These securities require a verified ADR ratio or all-class issuer cap.
        if shares and symbol not in {'SKHY','GOOG','GOOGL'}:
            quote['marketCap']=quote['price']*shares
            quote['sharesAsOf']=asof
            quote['sharesSource']=_source(cik,accession)
            quote['method']='price × SEC reported shares; market-cap estimate'
            quote['source']+=' + SEC reported shares'
            warnings.append(f'市值估算使用 {asof} SEC 股数，未调整此后拆股/回购/增发；估值分母为实际 TTM。')
        elif symbol in {'GOOG','GOOGL'}:
            warnings.append('Alphabet 含多类股票；未取得可核验的全类别市值，估值倍数留空。')
    return quote


def _safe_error(exc,identity):
    message=str(exc).replace(identity,'[SEC identity]') if identity else str(exc)
    message=re.sub(r'[\w.+-]+@[\w.-]+','[email]',message)
    return f'{type(exc).__name__}: {message[:220]}'



SKHYNIX_ARCHIVE = 'https://news.skhynix.com/en/category/ir/'


def _quarter_key(text):
    text=str(text).upper().replace('’',"'").replace('‘',"'")
    match=re.search(r'(?:([1-4])Q|Q([1-4]))[\s\'-]*(20\d{2}|\d{2})(?!\d)',text)
    if match:
        return (int(match[3]) if len(match[3])==4 else 2000+int(match[3]),int(match[1] or match[2]))
    return None


def _release_order(text):
    quarter=_quarter_key(text)
    if quarter: return quarter
    annual=re.search(r'FY\s*(20\d{2}|\d{2})(?!\d)',str(text),re.I)
    if annual: return (int(annual[1]) if len(annual[1])==4 else 2000+int(annual[1]),4)
    return (0,0)


def discover_skhynix_releases(html,base_url=SKHYNIX_ARCHIVE):
    """Only return observed official links, latest fiscal period first."""
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(html,'html.parser');found={}
    for anchor in soup.select('a[href]'):
        url=urljoin(base_url,anchor['href']);parts=urlparse(url)
        url=parts._replace(query='',fragment='').geturl()
        if parts.scheme!='https' or parts.hostname!='news.skhynix.com': continue
        label=anchor.get_text(' ',strip=True)
        if not (re.search(r'SK\s+hynix.*Financial\s+Results',label,re.I) or
                re.search(r'(?:business-results|announces-.*financial-results)',parts.path,re.I)): continue
        order=_release_order(label+' '+parts.path)
        if order==(0,0): continue
        found[url]=order
    return sorted(found,key=lambda u:found[u],reverse=True)


def _table_grid(table):
    """Expand rowspan/colspan before attaching numeric cells to fiscal headers."""
    grid={};height=0;width=0
    for row_index,tr in enumerate(x for x in table.find_all('tr') if x.find_parent('table') is table):
        column=0
        for cell in tr.find_all(['td','th'],recursive=False):
            while (row_index,column) in grid: column+=1
            text=cell.get_text(' ',strip=True)
            rowspan=min(10,max(1,int(cell.get('rowspan',1))))
            colspan=min(20,max(1,int(cell.get('colspan',1))))
            for r in range(rowspan):
                for c in range(colspan): grid[(row_index+r,column+c)]=text
            column+=colspan;width=max(width,column)
        height=max(height,row_index+1)
    return [[grid.get((r,c),'') for c in range(width)] for r in range(height)]


def _table_number(text):
    token=str(text).strip().replace(',','').replace('−','-')
    if not re.fullmatch(r'\(?-?\d+(?:\.\d+)?\)?',token): return None
    if token.startswith('(') and token.endswith(')'): token='-'+token[1:-1]
    return _number(token)


def parse_skhynix_release(html,source_url):
    """Parse explicit KRW earnings-table quarters, including labeled comparisons.

    Annual FY columns are excluded. Narrative cash balances, future investment,
    shareholder-return policies and percent changes never become flow metrics.
    """
    from bs4 import BeautifulSoup
    if urlparse(source_url).hostname!='news.skhynix.com': raise ValueError('Unverified IR source host')
    soup=BeautifulSoup(html,'html.parser');article=soup.select_one('article.post')
    if article is None: raise ValueError('Official IR article markup not found')
    title=article.select_one('.post-title')
    title=title.get_text(' ',strip=True) if title else ''
    if not re.search(r'SK\s+hynix.*Financial\s+Results',title,re.I):
        raise ValueError('Official earnings headline not found')
    published=None
    for li in article.select('li'):
        try: published=datetime.strptime(li.get_text(' ',strip=True),'%B %d, %Y').date();break
        except ValueError: pass
    if published is None: raise ValueError('Official release publication date not found')
    periods={}
    for table in article.select('table'):
        if table.find('table') is not None: continue  # outer layout table
        outer=table.find_parent('table') or table
        unit_text=outer.get_text(' ',strip=True)
        unit=re.search(r'Unit\s*:\s*(Billion|Million|Trillion)\s+KRW\b',unit_text,re.I)
        if unit is None: continue
        scale={'billion':1e9,'million':1e6,'trillion':1e12}[unit[1].lower()]
        grid=_table_grid(table);headers={}
        for cells in grid:
            label=re.sub(r'\s+',' ',cells[0]).strip().lower() if cells else ''
            metric={'revenue':'revenue','revenues':'revenue','net income':'netIncome'}.get(label)
            if metric is None:
                for c,text in enumerate(cells):
                    # Exact quarter tokens only, never percent change or FY25.
                    if re.fullmatch(r'(?:[1-4]Q|Q[1-4])[\s\'’‘-]*(?:20\d{2}|\d{2})',text,re.I):
                        headers[c]=_quarter_key(text)
                continue
            for column,(year,quarter) in headers.items():
                month=quarter*3
                end=date(year,month,monthrange(year,month)[1]);start=date(year,month-2,1)
                if end>published or (published-end).days>800: continue
                amount=_table_number(cells[column]) if column<len(cells) else None
                if amount is None: continue
                period=periods.setdefault(str(end),{'periodStart':str(start),'periodEnd':str(end),
                    'fiscalLabel':f'FY{year} Q{quarter}','currency':'KRW','source':source_url,
                    'filedAt':str(published),'derived':False,'preliminary':True,
                    'accountingStandard':'consolidated K-IFRS','metricSources':{},
                    'revenue':None,'netIncome':None,'operatingCashFlow':None,
                    'capex':None,'freeCashFlow':None,'eps':None})
                period[metric]=float(Decimal(str(amount))*Decimal(str(scale)))
                period['metricSources'][metric]={'source':source_url,'filedAt':str(published),
                    'method':'official-IR-quarter-table','periodStart':str(start),'periodEnd':str(end),
                    'currency':'KRW','unit':'KRW','reportedUnit':unit[1]+' KRW','reportedValue':amount}
    result=[p for p in periods.values() if p['revenue'] is not None]
    if not result: raise ValueError('No explicit quarterly KRW revenue table found')
    return sorted(result,key=lambda p:p['periodEnd'])


def _fetch_ir_html(url):
    parts=urlparse(url)
    if parts.scheme!='https' or parts.hostname!='news.skhynix.com': raise ValueError('Unverified IR URL')
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 Northstar Local Research'})
    with urlopen(req,timeout=8) as response:
        if urlparse(response.url).hostname!='news.skhynix.com': raise ValueError('IR redirected away from official host')
        return response.read(2_000_000).decode('utf-8')


@lru_cache(maxsize=4)
def _load_skhynix_cached(bucket):
    """At most five HTTP requests: observed archive plus four observed releases."""
    archive=_fetch_ir_html(SKHYNIX_ARCHIVE)
    pending=discover_skhynix_releases(archive);visited=set();periods={};filings=[];warnings=[]
    if not pending: raise ValueError('Official IR archive contains no recognizable financial-results links')
    for _ in range(4):
        pending=[url for url in pending if url not in visited]
        if not pending: break
        # Discovery has already sorted each page; prioritize actual fiscal period.
        pending.sort(key=_release_order,reverse=True)
        url=pending.pop(0);visited.add(url)
        try:
            html=_fetch_ir_html(url)
            quarters=parse_skhynix_release(html,url)
            for period in quarters:
                old=periods.get(period['periodEnd'])
                if old is None or period['filedAt']>old['filedAt']: periods[period['periodEnd']]=period
            latest=max(quarters,key=lambda p:p['periodEnd'])
            filings.append({'date':latest['filedAt'],'form':'IR release',
                            'title':'SK hynix · '+latest['fiscalLabel']+' earnings release',
                            'url':url,'accession':None})
            pending+=discover_skhynix_releases(html,url)
            if len(periods)>=8: break
        except Exception as exc:
            warnings.append(f'官方 IR 页面读取/结构校验未完成（{url}）：{type(exc).__name__}: {str(exc)[:150]}')
    if not periods: raise ValueError('; '.join(warnings) or 'No usable official quarterly reports')
    return {'quarters':sorted(periods.values(),key=lambda p:p['periodEnd'])[-8:],
            'filings':filings,'warnings':warnings,'retrievedAt':_stamp()}


def _build_skhynix(result,market_prices):
    result['provider']='SK hynix official IR · K-IFRS'
    result['warnings'].append('SKHY 为美元 ADR；财务采用母公司合并 K-IFRS 韩元口径。ADR/母股比例与汇率映射未验证，估值倍数留空。')
    try:
        data=_load_skhynix_cached(int(time.time()//21600))
        result.update(quarters=data['quarters'],filings=data['filings'],retrievedAt=data['retrievedAt'],
                      currency='KRW',status='partial')
        result['warnings']+=data['warnings']
        result['warnings'].append('官方初步业绩表只提供营收/净利润；经营现金流、资本开支、自由现金流与 EPS 留空。比较季度采用该公告明确列示的数值。')
        result['ttm']=calculate_ttm(result['quarters'])
        quote=_quote('SKHY',market_prices,[],result['warnings'])
        result['valuation']=calculate_valuation(result['ttm'],quote,currency='KRW',adr=True)
        if result['valuation']:
            result['valuation']['marketCap']=None
            result['valuation']['method']='ADR ratio / currency mapping unverified; ratios unavailable'
    except Exception as exc:
        result['warnings'].append('SK hynix 官方 IR 自动读取未完成：'+_safe_error(exc,''))
    return result

def _build_single(symbol,identity,market_prices):
    result={'symbol':symbol,'name':NAMES.get(symbol,symbol),'currency':None,'status':'unavailable',
            'provider':'EdgarTools · SEC EDGAR','retrievedAt':_stamp(),'quarters':[],
            'annuals':[],'ttm':None,'valuation':None,'peers':[],'filings':[],
            'warnings':[],'calculator':'FinanceToolkit'}
    if symbol=='SKHY': return _build_skhynix(result,market_prices)
    if not identity or '@' not in identity:
        result['warnings'].append('SEC 访问需要已配置的真实联系邮箱。');return result
    try:
        data=_load_sec_company(symbol,identity)
        result.update(name=data['name'],filings=data['filings'],retrievedAt=data.get('retrievedAt',result['retrievedAt']))
        records=data['records']
        result['quarters']=normalize_facts(records,data['cik'])
        result['annuals']=normalize_annual_facts(records,data['cik'])
        if result['quarters']:
            result['currency']=result['quarters'][-1]['currency']
            result['ttm']=calculate_ttm(result['quarters'])
        elif result['annuals']:
            result['currency']=result['annuals'][-1]['currency']
            result['warnings'].append('SEC 仅有可核验年度数据；年度回退单列展示，未伪造季度或 TTM。')
        else:
            result['warnings'].append('SEC 未提供可核验标准 XBRL 期间；缺失值留空。')
        if result['quarters'] and result['ttm'] is None:
            result['warnings'].append('不足四个连续同币种季度，TTM 与相应估值留空。')
        if result['ttm'] and result['ttm']['eps'] is None:
            result['warnings'].append('未取得四季可比的已报稀释 EPS；不以单季乘四或年度减季度推算。')
        quote=_quote(symbol,market_prices,records,result['warnings'],cik=data['cik'])
        result['valuation']=calculate_valuation(result['ttm'],quote,currency=result['currency'])
        complete=(len(result['quarters'])==8 and result['ttm'] and
                  all(result['ttm'].get(k) is not None for k in ('revenue','netIncome','freeCashFlow')) and
                  result['valuation'] and result['valuation'].get('marketCap') is not None)
        result['status']='ready' if complete else ('partial' if result['quarters'] or result['annuals'] else 'unavailable')
    except Exception as exc:
        result['warnings'].append('SEC 自动读取未完成：'+_safe_error(exc,identity))
        result['status']='partial' if result['quarters'] or result['annuals'] else 'unavailable'
    return result


def build_company_research(symbol: str, identity: str, market_prices: dict|None=None) -> dict:
    """Selected issuer plus at most two named peers; no market-wide ranking.

    The HTTP/cache layer may persist the JSON. Library in-memory facts cache is
    six hours; every response records retrieval, filing, period and price dates.
    """
    symbol=symbol.strip().upper()
    peers=PEERS.get(symbol,())[:2]
    with ThreadPoolExecutor(max_workers=3,thread_name_prefix='sec-research') as executor:
        jobs={s:executor.submit(_build_single,s,identity,market_prices) for s in (symbol,*peers)}
        result=jobs[symbol].result()
        for peer in peers:
            item=jobs[peer].result();valuation=item.get('valuation') or {}
            result['peers'].append({'symbol':peer,'name':item['name'],'currency':item['currency'],
                 'priceAsOf':valuation.get('priceAsOf'),'periodEnd':(item.get('ttm') or {}).get('periodEnd'),
                 'marketCap':valuation.get('marketCap'),'pe':valuation.get('pe'),'ps':valuation.get('ps'),
                 'pfcf':valuation.get('pfcf'),'status':item['status'],
                 'source':valuation.get('source') or item['provider'],
                 'filingSource':item['filings'][0]['url'] if item['filings'] else None,
                 'retrievedAt':item['retrievedAt'],'warnings':item['warnings'],
                 'method':valuation.get('method'),'sharesAsOf':valuation.get('sharesAsOf'),
                 'sharesSource':valuation.get('sharesSource')})
    if symbol=='MU':
        result['warnings'].append('WDC / SNDK 为存储产业链参照；HDD、NAND 与 DRAM 业务和分拆历史不同，倍数不能直接作高低排名。')
    if symbol=='SPCX':
        result['warnings'].append('RKLB / ASTS 仅为航天主题参照，业务、规模与盈利阶段不同，不构成可直接比较的同业排名。')
    if symbol=='SKHY':
        result['warnings'].append('MU 仅供存储行业参照；ADR 与母股币种/单位未校验，不能据此排名。')
    if any(p['status']=='unavailable' for p in result['peers']):
        result['warnings'].append('部分同业数据暂不可用；仅展示实际成功取得的来源，不作完整市场排名。')
    return result
