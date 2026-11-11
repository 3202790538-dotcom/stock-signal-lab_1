"""Source-linked public RSS headlines and SEC filing records, no AI inference."""
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import html
import re
import urllib.request
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
import xml.etree.ElementTree as ET
from watchlists import safe_quote_symbol

SYMBOLS={'NVDA','SPCX','MRVL','AVGO','SKHY','MU','GOOG'}
ALIASES={
    'NVDA':r'\bnvda\b|nvidia|jensen huang',
    'SPCX':r'\bspcx\b|spacex|space exploration|starlink|starship',
    'MRVL':r'\bmrvl\b|marvell',
    'AVGO':r'\bavgo\b|broadcom',
    'SKHY':r'\bskhy\b|hynix|sk海力士|海力士',
    'MU':r'\bmu\b|micron|美光',
    'GOOG':r'\bgoogl?\b|google|alphabet',
}
IMPACT_RULES=[
    (r'earnings|revenue|sales|profit|results|guidance|财报|营收','营收 / 利润'),
    (r'capex|capital expend|capacity|factory|fabs?|cash flow|资本开支','资本开支 / 自由现金流'),
    (r'chip|hbm|memory|data cent|artificial intelligence|\bai\b|存储','产品需求 / 毛利率'),
    (r'export|tariff|ban|regulat|antitrust|lawsuit|出口|监管','监管 / 业务可达市场'),
    (r'ceo|board|governance|investigat|管理层','管理层 / 公司治理'),
    (r'valuat|price target|rating|upgrade|downgrade|\bpe\b|\bp/e\b|估值','估值 / 市场预期'),
    (r'launch|satellite|starlink|space|卫星','发射 / 连接业务进展'),
    (r'buyback|dividend|debt|融资|分红','资本结构 / 股东回报'),
]

def clean_text(value):
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]*>',' ',value or ''))).strip()

def safe_url(value):
    if not isinstance(value,str) or any(ord(c)<32 for c in value):return None
    try:
        p=urlsplit(value.strip())
        if p.scheme not in ('http','https') or not p.hostname or p.username:return None
        params=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                if not k.lower().startswith('utm_') and k not in ('.tsrc','guccounter')]
        return urlunsplit((p.scheme,p.netloc,p.path,urlencode(params),''))
    except ValueError:return None

def impacts_for(text):
    found=[label for pattern,label in IMPACT_RULES if re.search(pattern,text,re.I)]
    return found[:4] or ['经营事件 / 市场预期']

def parse_rss(symbol,content,now=None,strict_symbol=False):
    try:root=ET.fromstring(content)
    except ET.ParseError as exc:raise ValueError('新闻源未返回有效 RSS') from exc
    if root.tag.lower()!='rss' or root.find('channel') is None:
        raise ValueError('新闻源返回了网页或不支持的格式')
    now=now or datetime.now(timezone.utc);items=[];seen=set()
    for item in root.findall('./channel/item'):
        title=clean_text(item.findtext('title'));link=safe_url(item.findtext('link'))
        if not title or not link or link in seen:continue
        try:
            published=parsedate_to_datetime(item.findtext('pubDate') or '')
            if published.tzinfo is None:continue
            published=published.astimezone(timezone.utc)
            if published>now:continue
        except (TypeError,ValueError,OverflowError):continue
        description=clean_text(item.findtext('description'))
        pattern=ALIASES.get(symbol,r'(?<![A-Za-z0-9])'+re.escape(symbol)+r'(?![A-Za-z0-9])')
        relevance='direct' if re.search(pattern,title,re.I) else 'mention'
        if strict_symbol and relevance=='mention' and not re.search(pattern,description,re.I):continue
        seen.add(link)
        publisher=clean_text(item.findtext('source')) or (urlsplit(link).hostname.removeprefix('www.')+' · Yahoo RSS')
        labels=impacts_for(title+' '+description)
        items.append({'id':sha256(link.encode()).hexdigest()[:20],'symbol':symbol,
                      'title':title[:500],'publishedAt':published.isoformat(),'source':publisher[:100],
                      'url':link,'kind':'news','summary':'按标题与 RSS 摘要关键词，可能涉及：'+'、'.join(labels)+'。需结合原文核对。',
                      'impacts':labels,'impactBasis':'rule','relevance':relevance})
    return sorted(items,key=lambda item:item['publishedAt'],reverse=True)[:30]

def fetch_news(symbol):
    if not safe_quote_symbol(symbol):raise ValueError('不支持的股票代码')
    url='https://feeds.finance.yahoo.com/rss/2.0/headline?'+urlencode({'s':symbol,'region':'US','lang':'en-US'})
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'application/rss+xml,application/xml'})
    with urllib.request.urlopen(request,timeout=15) as response:
        raw=response.read(4_000_001)
        if len(raw)>4_000_000:raise ValueError('新闻响应过大')
    return parse_rss(symbol,raw,strict_symbol=True)

def merge_filings(symbol,items,filings):
    output=list(items);seen={item.get('url') for item in items}
    for filing in filings or []:
        link=safe_url(filing.get('url'));date=filing.get('date');form=filing.get('form','')
        if not link or link in seen or not isinstance(date,str):continue
        try:
            parsed=datetime.fromisoformat(date.replace('Z','+00:00'))
            if parsed.date()>datetime.now(timezone.utc).date():continue
        except ValueError:continue
        seen.add(link)
        host=urlsplit(link).hostname or ''
        is_sec=host=='sec.gov' or host.endswith('.sec.gov')
        source='SEC EDGAR' if is_sec else 'SK hynix 官方 IR' if host=='news.skhynix.com' else '公告来源 · '+host
        labels=['营收 / 利润','现金流 / 资产负债'] if form in ('10-Q','10-K','20-F','40-F') else ['重大经营事件 / 财务披露']
        output.append({'id':'sec-'+str(filing.get('accession') or sha256(link.encode()).hexdigest()[:16]),
                       'symbol':symbol,'title':clean_text(filing.get('title') or form+' 监管公告'),
                       'publishedAt':date,'source':source,'url':link,'kind':'filing','form':form,
                       'summary':('该记录为监管披露' if is_sec else '该记录为公司公告')+'，指标标签按文件类型归类；具体内容请查阅原始文件。',
                       'impacts':labels,'impactBasis':'filing'})
    return sorted(output,key=lambda item:item['publishedAt'],reverse=True)[:60]
