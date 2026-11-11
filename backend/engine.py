"""Daily-bar research; Qlib factors, explicit data age, no forecast scores."""
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import concurrent.futures
import json
import tempfile
import subprocess
import time
import urllib.request
import numpy as np
import pandas as pd
from risk import build_account_risk
from analytics import enrich_research
from watchlists import quote_symbols, watchlist_status, safe_quote_symbol

ORDER=['NVDA','SPCX','MRVL','AVGO','SKHY','MU','GOOG']
NAMES={'NVDA':('NVIDIA','AI 计算'),'SPCX':('SpaceX','航天 · 连接 · AI'),'MRVL':('Marvell Technology','定制芯片 · 互连'),'AVGO':('Broadcom','半导体 · 软件'),'SKHY':('SK hynix ADR','HBM · 存储'),'MU':('Micron Technology','存储芯片'),'GOOG':('Alphabet Class C','搜索 · 云 · AI'),'SPY':('S&P 500 ETF','市场基准')}
EXP={'close':'$close','ma20':'Mean($close,20)','ma50':'Mean($close,50)','ma200':'Mean($close,200)',
     'ret20':'$close/Ref($close,20)-1','ret60':'$close/Ref($close,60)-1',
     'volatility':'Std($close/Ref($close,1)-1,20)*15.8745078664'}
RULES=[{'name':'趋势走强','description':'收盘价 > 20日均线 > 50日均线；描述当前结构，不代表上涨概率。'},
       {'name':'修复观察','description':'收盘高于20日或50日均线，但尚未形成完整的短期多头排列。'},
       {'name':'趋势偏弱','description':'收盘同时低于20日和50日均线。'},
       {'name':'动能区间','description':'RSI14采用Wilder方法；低于30或高于70只是常用区间，不单独触发买卖。'}]

def finite(v):
    if v is None or isinstance(v,bool):return None
    try:v=float(v)
    except (TypeError,ValueError,OverflowError):return None
    return v if np.isfinite(v) else None

def normalize_yahoo(symbol,payload,now=None):
    chart=payload.get('chart') if isinstance(payload,dict) else None
    if not isinstance(chart,dict) or chart.get('error'):
        raise ValueError(f'{symbol} 行情源没有返回有效数据')
    results=chart.get('result')
    if not isinstance(results,list) or not results or not isinstance(results[0],dict):
        raise ValueError(f'{symbol} 行情源没有返回有效数据')
    r=results[0];meta=r.get('meta')
    if (not isinstance(meta,dict) or not isinstance(meta.get('symbol'),str)
            or meta['symbol'].upper()!=symbol or meta.get('currency')!='USD'):
        raise ValueError(f'{symbol} 证券代码或计价货币不匹配')
    indicators=r.get('indicators');timestamps=r.get('timestamp')
    quotes=indicators.get('quote') if isinstance(indicators,dict) else None
    if (not isinstance(timestamps,list) or not timestamps or not isinstance(quotes,list)
            or not quotes or not isinstance(quotes[0],dict)
            or any(not isinstance(quotes[0].get(k),list) or len(quotes[0][k])!=len(timestamps)
                   for k in ('open','high','low','close','volume'))):
        raise ValueError(f'{symbol} 行情源日线格式不正确')
    dates=pd.to_datetime(timestamps,unit='s',utc=True).tz_convert('America/New_York').tz_localize(None).normalize()
    q=quotes[0]
    # JSON numbers such as 1e999 decode to infinity. Reject invalid supplied
    # prices here so one provider response cannot poison the shared Qlib run.
    # None remains an absent field for the completed-bar warnings below.
    if any(value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or finite(value) is None)
           for key in ('open','high','low','close') for value in q[key]):
        raise ValueError(f'{symbol} 行情源包含非有限数值或无效价格')
    frame=pd.DataFrame({k:q.get(k,[]) for k in ['open','high','low','close','volume']},index=dates)
    frame=frame.sort_index()
    frame=frame[~frame.index.duplicated(keep='last')]
    market_now=(now or datetime.now(timezone.utc)).astimezone(ZoneInfo('America/New_York'))
    today=pd.Timestamp(market_now.date())
    frame=frame[frame.index<=today]
    if market_now.hour<16:frame=frame[frame.index<today]
    if symbol=='SPCX':frame=frame[frame.index>=pd.Timestamp('2026-06-12')]
    if symbol=='SKHY':frame=frame[frame.index>=pd.Timestamp('2026-07-10')]
    missing=frame[['open','high','low','close']].isna()
    incomplete=frame[missing.any(axis=1)]
    frame=frame.dropna(subset=['open','high','low','close'])
    if frame.empty or (frame[['open','high','low','close']]<=0).any().any():
        raise ValueError(f'{symbol} 返回空行情或无效价格')
    frame.attrs['marketWarnings']=[{'symbol':symbol,'date':date.strftime('%Y-%m-%d'),
                                  'missingFields':[k for k in missing.columns if missing.loc[date,k]]}
                                 for date in incomplete.index if date>frame.index.max()]
    warnings=frame.attrs['marketWarnings']
    quote_error=None
    if warnings:
        labels={'open':'开盘价','high':'最高价','low':'最低价','close':'收盘价'}
        missing_details='；'.join(f"{warning['date']} 日线的"+'、'.join(labels[field] for field in warning['missingFields'])
                                 for warning in warnings)
        quote_error=f"行情源尚未提供 {missing_details}；当前使用截至 {frame.index.max():%Y-%m-%d} 的最近完整日线。"
    frame['volume']=pd.to_numeric(frame.volume,errors='coerce')
    frame.loc[frame.volume<0,'volume']=np.nan
    meta=r.get('meta',{})
    frame.attrs.update({'name':meta.get('longName') or meta.get('shortName') or NAMES.get(symbol,(symbol,''))[0],
                        'exchange':meta.get('fullExchangeName') or meta.get('exchangeName') or '',
                        'quoteType':meta.get('instrumentType') or meta.get('quoteType') or 'UNKNOWN',
                        'currency':'USD','quoteStatus':'stale' if warnings else 'ready','quoteError':quote_error})
    return frame

def download(symbol):
    # Unofficial public chart endpoint; used only for this personal local trial.
    if not safe_quote_symbol(symbol):raise ValueError('不支持的行情代码')
    url=f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d&includePrePost=false'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=15) as r:payload=json.load(r)
    return normalize_yahoo(symbol,payload)

def rsi14(close):
    if len(close)<15:return pd.Series(np.nan,index=close.index)
    delta=close.diff();g=delta.clip(lower=0);l=-delta.clip(upper=0)
    g.iloc[14]=g.iloc[1:15].mean();l.iloc[14]=l.iloc[1:15].mean()
    gain=g.iloc[14:].ewm(alpha=1/14,adjust=False).mean()
    loss=l.iloc[14:].ewm(alpha=1/14,adjust=False).mean()
    value=100-100/(1+gain/loss)
    value[(gain==0)&(loss==0)]=50
    return value.reindex(close.index)

def calculate(frames):
    import qlib
    from qlib.data import D
    if 'SPY' not in frames or any(len(f)<2 for f in frames.values()):
        raise ValueError('需要市场基准和至少两个有效交易日')
    # Same on-disk layout as Qlib's official dump_bin.py; regenerate per run.
    calendar=pd.DatetimeIndex(sorted(set().union(*(set(f.index) for f in frames.values()))))
    output=[]
    with tempfile.TemporaryDirectory(prefix='northstar-qlib-') as tmp:
        root=Path(tmp);(root/'calendars').mkdir();(root/'instruments').mkdir();(root/'features').mkdir()
        (root/'calendars/day.txt').write_text('\n'.join(d.strftime('%Y-%m-%d') for d in calendar)+'\n')
        instruments=[]
        for symbol,f in frames.items():
            dates=calendar[(calendar>=f.index[0])&(calendar<=f.index[-1])]
            data=f.reindex(dates);start=calendar.get_loc(dates[0])
            p=root/'features'/symbol.lower();p.mkdir()
            for k in ['open','high','low','close','volume']:
                np.hstack([start,data[k].to_numpy()]).astype('<f').tofile(p/f'{k}.day.bin')
            instruments.append(f'{symbol}\t{f.index[0]:%Y-%m-%d}\t{f.index[-1]:%Y-%m-%d}')
        (root/'instruments/all.txt').write_text('\n'.join(instruments)+'\n')
        qlib.init(provider_uri=str(root),region='us',kernels=1,joblib_backend='threading',expression_cache=None,dataset_cache=None,clear_mem_cache=True,logging_level=40)
        panel=D.features(list(frames),list(EXP.values()),start_time=calendar[0],end_time=calendar[-1]);panel.columns=list(EXP)
        for symbol,original in frames.items():
            f=panel.xs(symbol,level='instrument').reindex(original.index).copy();count=np.arange(1,len(f)+1)
            for w in [20,50,200]:f.loc[count<w,f'ma{w}']=np.nan
            for w in [20,60]:f.loc[count<=w,f'ret{w}']=np.nan
            f.loc[count<21,'volatility']=np.nan
            # Cross-check the actual Qlib output against raw-price arithmetic.
            for w in [20,50,200]:np.testing.assert_allclose(f[f'ma{w}'],original.close.rolling(w).mean(),rtol=3e-6,equal_nan=True)
            for w in [20,60]:np.testing.assert_allclose(f[f'ret{w}'],original.close.pct_change(w),rtol=3e-4,atol=3e-7,equal_nan=True)
            if symbol=='SPY':continue
            last=f.iloc[-1];close=float(original.close.iloc[-1]);prev=float(original.close.iloc[-2])
            rsi=finite(rsi14(original.close).iloc[-1]);ma20=finite(last.ma20);ma50=finite(last.ma50)
            status='strong' if ma20 and ma50 and close>ma20>ma50 else 'recovering' if ((ma20 and close>ma20) or (ma50 and close>ma50)) else 'weak'
            signals=[{'id':'trend','label':{'strong':'趋势走强','recovering':'修复观察','weak':'趋势偏弱'}[status],'tone':'positive' if status=='strong' else 'warning' if status=='weak' else 'neutral','description':{'strong':'收盘价高于20日均线，20日均线高于50日均线。','recovering':'价格收复部分短期均线，尚未形成完整的短期多头排列。','weak':'收盘价低于20日和50日均线。'}[status]}]
            if rsi is not None and (rsi<30 or rsi>70):signals.append({'id':'rsi','label':'动能超卖' if rsi<30 else '动能偏热','tone':'warning','description':'RSI进入常用极值区间，可以延续，不等于反转确认。'})
            if len(f)<200:signals.append({'id':'short','label':'长期样本不足','tone':'neutral','description':f'仅有{len(f)}个交易日，200日均线不计算。'})
            tr=pd.concat([original.high-original.low,(original.high-original.close.shift()).abs(),(original.low-original.close.shift()).abs()],axis=1).max(axis=1)
            bad=(original.high<original[['open','close','low']].max(axis=1))|(original.low>original[['open','close','high']].min(axis=1))
            atr=None if bad.tail(14).any() else finite(tr.rolling(14).mean().iloc[-1])
            history=[]
            for date,row in original.iterrows():
                h={'date':date.strftime('%Y-%m-%d'),**{k:finite(row[k]) for k in original.columns}}
                h.update({k:finite(f.loc[date,k]) for k in ['ma20','ma50','ma200']});history.append(h)
            spy=frames['SPY'].close.reindex(original.index).pct_change(20)
            rel=finite((original.close.pct_change(20).iloc[-1]-spy.iloc[-1])*100)
            observation=f"当前结构为{signals[0]['label']}。"+(f'收复20日均线（${ma20:.2f}）可作为一个观察条件。' if status=='weak' and ma20 else '结合50日均线与后续收盘确认趋势是否延续。')
            output.append({'symbol':symbol,'name':original.attrs.get('name') or NAMES.get(symbol,(symbol,''))[0],
                'sector':NAMES.get(symbol,('',''))[1],'currency':'USD','asOf':history[-1]['date'],
                'exchange':original.attrs.get('exchange') or '', 'quoteType':original.attrs.get('quoteType') or 'UNKNOWN',
                'quoteStatus':original.attrs.get('quoteStatus','ready'),'quoteError':original.attrs.get('quoteError'),
                'close':close,'previousClose':prev,'changePct':(close/prev-1)*100,'ret20':finite(last.ret20),'ret60':finite(last.ret60),
                'ma20':ma20,'ma50':ma50,'ma200':finite(last.ma200),'rsi':rsi,'volatility':finite(last.volatility),'atr':atr,'relative20':rel,'bars':len(f),'status':status,'signals':signals,'history':history,'observation':observation})
    output.sort(key=lambda s:ORDER.index(s['symbol']) if s['symbol'] in ORDER else 99)
    spy=frames['SPY'].close
    return {'stocks':output,'marketWarnings':[w for f in frames.values() for w in f.attrs.get('marketWarnings',[])],
            'benchmark':{'symbol':'SPY','name':frames['SPY'].attrs.get('name') or 'S&P 500 ETF',
                         'exchange':frames['SPY'].attrs.get('exchange') or '',
                         'quoteType':frames['SPY'].attrs.get('quoteType') or 'UNKNOWN','currency':'USD',
                         'asOf':spy.index[-1].strftime('%Y-%m-%d'),
                         'quoteStatus':frames['SPY'].attrs.get('quoteStatus','ready'),
                         'quoteError':frames['SPY'].attrs.get('quoteError'),
                         'close':float(spy.iloc[-1]),'changePct':float((spy.iloc[-1]/spy.iloc[-2]-1)*100),'ret20':finite(spy.pct_change(20).iloc[-1]),
                         'history':[{'date':day.strftime('%Y-%m-%d'),**{k:finite(row[k]) for k in ['open','high','low','close','volume']}}
                                    for day,row in frames['SPY'].iterrows()]}}

def load_portfolio(path):
    h=json.loads(path.read_text());risk=build_account_risk(h);positions=[];nav=risk['netLiquidation']
    for p in h.get('positions') or []:
        sym=p.get('contract_description')
        if sym not in ORDER:continue
        quantity,average,value,pnl=[finite(p.get(k)) for k in ['position','average_price','market_value','unrealized_pnl']]
        cost=finite(abs(quantity*average)) if quantity is not None and average is not None else None
        positions.append({'symbol':sym,'quantity':quantity,'averagePrice':average,'marketPrice':finite(p.get('market_price')),'marketValue':value,
            'pnl':pnl,'returnPct':finite(pnl/cost*100) if pnl is not None and cost is not None and cost>0 else None,
            'weightPct':finite(value/nav*100) if value is not None and nav is not None and nav>0 else None})
    positions.sort(key=lambda p:ORDER.index(p['symbol']))
    total=lambda key:finite(sum(p[key] for p in positions)) if all(p[key] is not None for p in positions) else None
    return {'asOf':h.get('asOf') or '未提供','source':'IBKR','netLiquidation':nav,'cash':risk['cash'],'leverage':risk['leverage'],
        'sevenMarketValue':total('marketValue'),'sevenPnl':total('pnl'),'positions':positions}

def enrich_dashboard(result,data_dir):
    """Combine independent completed-bar data and authorized broker snapshot."""
    data_dir=Path(data_dir)
    result['portfolio']=load_portfolio(data_dir/'holdings.json')
    snapshot=json.loads((data_dir/'holdings.json').read_text())
    result['accountRisk']=build_account_risk(snapshot)
    result['fundamentals']=json.loads((data_dir/'fundamentals.json').read_text()) if (data_dir/'fundamentals.json').exists() else []
    result['watchlists']=watchlist_status(data_dir,[*result.get('stocks',[]),result.get('benchmark') or {}],
                                          (result.get('quoteRefresh') or {}).get('errors',[]))
    return enrich_research(result)

def _cached_frame(symbol,cached,message):
    """Recover only validated USD OHLC bars and label the entire quote stale."""
    item=(cached.get('benchmark') or {}) if symbol=='SPY' else next(
        (s for s in cached.get('stocks',[]) if isinstance(s,dict) and s.get('symbol')==symbol),{})
    if item.get('symbol')!=symbol or item.get('currency','USD')!='USD':return None
    try:
        frame=pd.DataFrame(item.get('history') or [])
        frame.index=pd.to_datetime(frame.pop('date'),errors='coerce')
        frame=frame.loc[frame.index.notna(),['open','high','low','close','volume']].copy()
        frame=frame.apply(pd.to_numeric,errors='coerce').replace([np.inf,-np.inf],np.nan)
        frame=frame.dropna(subset=['open','high','low','close']).sort_index()
        frame=frame[~frame.index.duplicated(keep='last')]
        frame=frame[(frame[['open','high','low','close']]>0).all(axis=1)]
        if symbol=='SPCX':frame=frame[frame.index>=pd.Timestamp('2026-06-12')]
        if symbol=='SKHY':frame=frame[frame.index>=pd.Timestamp('2026-07-10')]
        if len(frame)<2:return None
        frame.loc[frame.volume<0,'volume']=np.nan
        frame.attrs.update({'name':item.get('name') or NAMES.get(symbol,(symbol,''))[0],
                            'exchange':item.get('exchange') or '', 'quoteType':item.get('quoteType') or 'UNKNOWN',
                            'currency':'USD','quoteStatus':'stale','quoteError':message,
                            'marketWarnings':[w for w in cached.get('marketWarnings',[])
                                              if w.get('symbol')==symbol and 'dataRefresh' not in w.get('missingFields',[])]})
        return frame
    except (ValueError,TypeError,KeyError,AttributeError):return None


def refresh(data_dir):
    started=time.monotonic()
    data_dir=Path(data_dir);data_dir.mkdir(exist_ok=True)
    symbols=quote_symbols(data_dir,ORDER)
    requested=list(dict.fromkeys([*symbols,'SPY']))
    try:
        cached=json.loads((data_dir/'dashboard.json').read_text())
        if not isinstance(cached,dict):cached={}
    except (OSError,ValueError):cached={}
    frames,failures,updated={}, {}, set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        jobs={pool.submit(download,symbol):symbol for symbol in requested}
        for job in concurrent.futures.as_completed(jobs):
            symbol=jobs[job]
            try:
                frame=job.result()
                if len(frame)<2:raise ValueError('至少需要两个有效交易日，暂不生成价格分析。')
                frames[symbol]=frame
                updated.add(symbol)
            except (OSError,ValueError,TypeError,KeyError,IndexError) as exc:
                message='有效日线少于两个交易日。' if isinstance(exc,ValueError) and '两个' in str(exc) else '公开日线暂时无法获取或验证。'
                fallback=_cached_frame(symbol,cached,message)
                if fallback is not None:frames[symbol]=fallback
                failures[symbol]={'symbol':symbol,'message':message,'usedCache':fallback is not None}
    if 'SPY' not in frames:raise ValueError('市场基准 SPY 无有效行情或可用缓存，已保留上次完整快照。')
    frames={symbol:frames[symbol] for symbol in requested if symbol in frames}
    result=calculate(frames)
    result['quoteRefresh']={'errors':[failures[s] for s in requested if s in failures],
                            'updatedSymbols':[s for s in requested if s in updated]}
    core={'NVDA','MRVL','AVGO','MU','GOOG','SPY'}
    for symbol in requested:
        if symbol in failures and symbol in core:
            frame=frames.get(symbol)
            result['marketWarnings'].append({'symbol':symbol,
                'date':frame.index[-1].strftime('%Y-%m-%d') if frame is not None else datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                'missingFields':['dataRefresh']})
    now=datetime.now(timezone.utc).isoformat();market_date=min((s['asOf'] for s in result['stocks']),default=result['benchmark']['asOf'])
    portfolio=load_portfolio(data_dir/'holdings.json')
    fundamentals=json.loads((data_dir/'fundamentals.json').read_text()) if (data_dir/'fundamentals.json').exists() else []
    result.update({'generatedAt':now,'marketAsOf':market_date,'marketSource':'Yahoo Finance','engine':'Qlib 0.9.8','stockOrder':[s['symbol'] for s in result['stocks']],'portfolio':portfolio,
        'fundamentals':fundamentals,'rules':RULES,'refresh':{'running':False,'lastError':None},'sources':[
        {'name':'公开日线行情','status':'available','asOf':market_date,'detail':'Yahoo Finance 公开图表接口；个人试用，非实时逐笔推送。点击刷新会重新获取，不保证接口长期稳定。'},
        {'name':'IBKR 持仓','status':'snapshot','asOf':portfolio['asOf'],'detail':'经授权读取的账户快照。行情刷新不会更新股数、成本或账户净值。'},
        {'name':'Qlib 指标','status':'available','asOf':now,'detail':'根据日线重新计算；均线、阶段收益、波动率与原始价格交叉核对。没有训练预测模型。'},
        {'name':'基本面研究','status':'archived','asOf':'2026-11-01','detail':'此前的公司/SEC研究摘要，保留原始链接和研究日期；不代表最新公告。'}]})
    enrich_dashboard(result,data_dir)
    result['sources'].append({'name':'账户风控与融资','status':'snapshot','asOf':portfolio['asOf'],'detail':'完整 IBKR 账户、全部已返回头寸与币种现金快照；压力测试为固定保证金金额下的股票变动假设。年利率手动填写。行情刷新不会更新账户。'})
    result['quoteRefresh']['durationSeconds']=round(time.monotonic()-started,3)
    text=json.dumps(result,ensure_ascii=False,allow_nan=False)
    temp=data_dir/'dashboard.tmp';temp.write_text(text);temp.replace(data_dir/'dashboard.json')
    # Refresh public issuer metadata in the existing research environment. A
    # metadata outage must not discard valid prices or touch personal accounts.
    research_python=Path(__file__).resolve().parents[1]/'.venv-research/bin/python'
    worker=Path(__file__).resolve().parents[1]/'scripts/verify-security-metadata.py'
    if research_python.exists() and (data_dir/'research-settings.json').exists():
        try:
            subprocess.run([str(research_python),str(worker),str(data_dir.resolve())],
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=90,check=True)
        except (OSError,subprocess.TimeoutExpired,subprocess.CalledProcessError):
            pass  # Cached metadata keeps its own verification date and expires.
    return result

if __name__=='__main__':
    result=refresh(Path(__file__).parent/'data')
    print(json.dumps({'ok':True,'marketAsOf':result['marketAsOf'],'symbols':len(result['stocks'])}))
