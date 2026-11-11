from pathlib import Path
from threading import Lock
from datetime import datetime,timezone
from typing import Literal,Annotated
import json, subprocess, sys
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel,Field,ConfigDict,AfterValidator
from news import fetch_news,merge_filings
from watchlists import quote_symbols,watchlist_status,safe_quote_symbol,load_registry
from personal_account import load_settings,selected_holdings,build_account_view
from analytics import _concentration
from runtime_cache import JsonFileCache,ResultCache
from dashboard_validation import valid_dashboard
from security_metadata import enrich_market
import uvicorn

BASE=Path(__file__).resolve().parent
DATA=BASE/'data'
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(GZipMiddleware,minimum_size=1000,compresslevel=5)
file_cache=JsonFileCache()
study_cache=ResultCache()
dashboard_validation_cache=ResultCache(max_entries=4)
refresh_lock=Lock()
last_error=None
research_lock=Lock()
CORE_SYMBOLS=('NVDA','SPCX','MRVL','AVGO','SKHY','MU','GOOG')
news_locks={symbol:Lock() for symbol in CORE_SYMBOLS}

def registered_symbol(value):
    if not safe_quote_symbol(value) or value not in quote_symbols(DATA,CORE_SYMBOLS):
        raise ValueError('股票未导入或代码不受支持')
    return value

Symbol=Annotated[str,AfterValidator(registered_symbol)]

class SymbolRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    symbol:Symbol

class BacktestRequest(SymbolRequest):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    lookback:int=Field(20,ge=5,le=120)
    holdingDays:int=Field(10,ge=1,le=60)
    feeBps:float=Field(5,ge=0,le=100)
    slippageBps:float=Field(5,ge=0,le=100)
    initialCapital:float=Field(10000,gt=0,le=1000000000)
    startDate:str|None=None
    endDate:str|None=None

class StrategyStudyRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    targetReturnPct:float=Field(50,ge=0,le=200)
    maxDrawdownPct:float=Field(30,ge=1,le=80)

class PortfolioStudyRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    templateId:Literal['breakout','pullback','rotation']='breakout'
    riskProfile:Literal['conservative','balanced','aggressive']='aggressive'
    targetReturnPct:float=Field(50,ge=0,le=200,strict=True)

class HoldingsPlanRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    templateId:Literal['breakout','pullback','rotation']='breakout'
    riskProfile:Literal['conservative','balanced','aggressive']='aggressive'
    allowProfitAdd:bool=Field(True,strict=True)

def read_cache(path):
    return file_cache.read(path)

def save_cache(path,value):
    path.parent.mkdir(exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False))
    temp.replace(path)

def cache_is_stale(stamp,hours):
    try:
        parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if parsed.tzinfo is None:return True
        age=(datetime.now(timezone.utc)-parsed).total_seconds()
        return age<0 or age>hours*3600
    except (AttributeError,TypeError,ValueError):return True

def company_inapplicable(symbol):
    from daily_sync import fund_research_exclusion
    market=enrich_market(file_cache.snapshot(DATA/'dashboard.json')[1],DATA)
    stock=next((s for s in market.get('stocks',[]) if s.get('symbol')==symbol),{})
    fund_reason=fund_research_exclusion(stock)
    if fund_reason:
        return {'company':None,'status':'not_applicable','updatedAt':None,'stale':False,**fund_reason}
    quote_type=stock.get('quoteType')
    if quote_type and quote_type not in ('EQUITY','UNKNOWN'):
        reason='ETF、基金等产品不适用企业季度财报与公司估值；可查看行情、新闻和单股回测。'
        return {'company':None,'status':'not_applicable','updatedAt':None,'lastError':reason,'stale':False}
    elif symbol not in CORE_SYMBOLS and quote_type!='EQUITY':
        reason='该标的的市场与证券类型尚未核验，请先更新行情；财报暂不自动获取。'
    else:return None
    return {'company':None,'status':'unconfigured','updatedAt':None,'lastError':reason,'stale':False}

def company_data(symbol):
    inapplicable=company_inapplicable(symbol)
    if inapplicable:return inapplicable
    cached=read_cache(DATA/f'company-{symbol}.json')
    return {'company':cached.get('company'),'status':cached.get('status','ready' if cached.get('company') else 'empty'),
            'updatedAt':cached.get('updatedAt'),'lastError':cached.get('lastError'),'stale':cache_is_stale(cached.get('updatedAt'),12)}

def news_data(symbol):
    cached=read_cache(DATA/f'news-{symbol}.json')
    company=company_data(symbol).get('company') or {}
    return {'items':merge_filings(symbol,cached.get('items',[]),company.get('filings',[])),
            'status':cached.get('status','ready' if cached.get('items') else 'empty'),
            'updatedAt':cached.get('updatedAt'),'lastError':cached.get('lastError'),'stale':cache_is_stale(cached.get('updatedAt'),.25)}

@app.middleware('http')
async def local_only(request:Request,call_next):
    if request.headers.get('host','').split(':')[0] not in ('127.0.0.1','localhost'):
        return JSONResponse({'error':'仅限本机访问'},status_code=403)
    if request.method in ('POST','PUT','PATCH','DELETE') and request.headers.get('origin') not in (None,'http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:18765','http://localhost:18765'):
        return JSONResponse({'error':'来源不受支持'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    return response

@app.get('/api/dashboard')
def dashboard():
    path=DATA/'dashboard.json'
    signature,cached=file_cache.snapshot(path)
    if not dashboard_validation_cache.get_or_build(signature,lambda:valid_dashboard(cached)):
        return JSONResponse({'error':'行情快照尚未准备、结构不完整或无法读取，请刷新行情后重试'},status_code=503)
    # Only the envelope changes here; preserve shared nested historical bars.
    d=enrich_market(cached,DATA);d['refresh']={'running':refresh_lock.locked(),'lastError':last_error}
    try:
        settings=load_settings(DATA)
        selected=selected_holdings(DATA,cached,settings)
        d.update(build_account_view(selected))
        if isinstance(d.get('portfolioAnalytics'),dict):
            d['portfolioAnalytics']={**d['portfolioAnalytics'],'concentration':_concentration(d['portfolio'])}
        source_label='手工账户' if settings['accountSource']=='manual' else 'IBKR 持仓'
        d['sources']=[{**source,'name':source_label,'asOf':selected.get('asOf') or '尚未填写',
                      'detail':'当前选择的账户；手工持仓与 IBKR 快照独立保存。日线更新只更新估值，不代表持仓已与券商重新同步。'}
                      if source.get('name') in ('IBKR 持仓','手工账户') else
                      {**source,'asOf':selected.get('asOf') or '尚未填写'}
                      if source.get('name')=='账户风控与融资' else source
                      for source in d.get('sources',[])]
    except (ValueError,TypeError):
        return JSONResponse({'error':'账户设置或手工持仓无法读取，请到“账户与设置”核对保存内容。'},status_code=503)
    d['watchlists']=watchlist_status(DATA,[*d.get('stocks',[]),d.get('benchmark') or {}],(d.get('quoteRefresh') or {}).get('errors',[]))
    symbols=quote_symbols(DATA,CORE_SYMBOLS)
    companies=[read_cache(DATA/f'company-{s}.json') for s in symbols]
    reports=[c for c in companies if c.get('company')]
    headlines=[read_cache(DATA/f'news-{s}.json') for s in symbols]
    news_dates=[n.get('updatedAt') for n in headlines if n.get('updatedAt')]
    financial_dates=[c.get('updatedAt') for c in reports if c.get('updatedAt')]
    d['sources']=[s for s in d.get('sources',[]) if s.get('name') not in ('自动财报与估值','新闻与公告','波段规则回测','IBKR 自选名单')]+[
        {'name':'IBKR 自选名单','status':'available' if d['watchlists']['uniqueCount'] else 'unavailable',
         'asOf':d['watchlists'].get('importedAt') or '未导入',
         'detail':f"已导入 {d['watchlists']['uniqueCount']} 个独立合约，保留原分组。名单是导入时快照；行情由公开数据源单独更新。"},
        {'name':'自动财报与估值','status':'available' if reports else 'unavailable','asOf':max(financial_dates,default='未获取'),
         'detail':'EdgarTools 读取 SEC；SKHY 使用公司官方韩元财报；FinanceToolkit 计算比率。进入研究页检查12小时缓存，缺失留空，同行倍数为注明股数日期的估算。'},
        {'name':'新闻与公告','status':'available' if news_dates else 'unavailable','asOf':max(news_dates,default='未获取'),
         'detail':'Yahoo Finance RSS 新闻与 SEC 披露记录，保留发布时间和原始链接。进入研究页检查15分钟新闻缓存；指标标签为关键词归类。'},
        {'name':'波段规则回测','status':'available','asOf':d.get('marketAsOf','未获取'),
         'detail':'基于已完成日线，收盘确认突破、下一交易日开盘入场；每笔计入手续费及滑点，持有天数可调。使用未复权价格，未建模分红及拆股。'}]
    return JSONResponse(d)

@app.get('/api/refresh-status')
def refresh_status():
    return {'running':refresh_lock.locked(),'lastError':last_error}

@app.post('/api/refresh')
def refresh():
    global last_error
    if not refresh_lock.acquire(blocking=False):return JSONResponse({'ok':False,'error':'已有行情刷新任务，请稍后再试'},status_code=409)
    try:
        p=subprocess.run([sys.executable,str(BASE/'engine.py')],cwd=BASE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=240)
        if p.returncode:
            last_error='行情源暂时无法更新，已保留上一次有效数据。请稍后再试。'
            return JSONResponse({'ok':False,'error':last_error},status_code=502)
        last_error=None
        return {'ok':True}
    except (subprocess.TimeoutExpired,OSError):
        last_error='行情刷新超时，已保留上一次有效数据。'
        return JSONResponse({'ok':False,'error':last_error},status_code=504)
    finally:refresh_lock.release()

@app.get('/api/health')
def health():return {'ok':True,'service':'northstar-local'}

@app.get('/api/company-research')
def company_research(symbol:Symbol):return company_data(symbol)

@app.post('/api/company-research/refresh')
def refresh_company(body:SymbolRequest):
    symbol=body.symbol
    inapplicable=company_inapplicable(symbol)
    if inapplicable:return inapplicable
    if not research_lock.acquire(blocking=False):
        return JSONResponse({**company_data(symbol),'error':'正在更新另一份财报，请稍后重试。'},status_code=409)
    try:
        python=BASE.parent/'.venv-research/bin/python'
        if not python.exists():
            return JSONResponse({**company_data(symbol),'status':'unconfigured','lastError':'财报依赖环境尚未安装。'},status_code=503)
        p=subprocess.run([str(python),str(BASE/'research_worker.py'),symbol,str(DATA)],cwd=BASE,
                         stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=120)
        if p.returncode:raise ValueError('provider failed')
        company=json.loads(p.stdout)
        if company.get('status')=='unavailable' or company.get('symbol')!=symbol or not any(company.get(k) for k in ['quarters','annuals','valuation']):
            raise ValueError('empty or mismatched research')
        result={'company':company,'status':'ready','updatedAt':datetime.now(timezone.utc).isoformat(),'lastError':None}
        save_cache(DATA/f'company-{symbol}.json',result)
        return result
    except (ValueError,OSError,subprocess.TimeoutExpired):
        result={**company_data(symbol),'status':'error','lastError':'财报源暂时无法更新，已保留上次有效数据。可稍后重新获取。'}
        save_cache(DATA/f'company-{symbol}.json',result)
        return JSONResponse(result,status_code=502)
    finally:research_lock.release()

@app.get('/api/news')
def company_news(symbol:Symbol):return news_data(symbol)

@app.post('/api/news/refresh')
def refresh_news(body:SymbolRequest):
    symbol=body.symbol;lock=news_locks.setdefault(symbol,Lock())
    if not lock.acquire(blocking=False):
        return JSONResponse({**news_data(symbol),'error':'该股票新闻正在获取。'},status_code=409)
    try:
        items=fetch_news(symbol)
        result={'items':items,'status':'ready','updatedAt':datetime.now(timezone.utc).isoformat(),'lastError':None}
        save_cache(DATA/f'news-{symbol}.json',result)
        return news_data(symbol)
    except (ValueError,OSError,TimeoutError):
        result=read_cache(DATA/f'news-{symbol}.json')
        result.update({'status':'error','lastError':'新闻源暂时无法更新，已保留上次有效记录。'})
        save_cache(DATA/f'news-{symbol}.json',result)
        return JSONResponse(news_data(symbol),status_code=502)
    finally:lock.release()

@app.post('/api/backtest')
def backtest(body:BacktestRequest):
    from backtest import run_backtest
    dashboard=file_cache.snapshot(DATA/'dashboard.json')[1]
    stock=next((s for s in dashboard.get('stocks',[]) if s.get('symbol')==body.symbol),None)
    if not stock:return JSONResponse({'error':'该股票的历史行情尚未准备。'},status_code=503)
    try:
        result=run_backtest(stock['history'],lookback=body.lookback,holding_days=body.holdingDays,
                            fee_bps=body.feeBps,slippage_bps=body.slippageBps,initial_capital=body.initialCapital,
                            start_date=body.startDate,end_date=body.endDate)
        return {**result,'symbol':body.symbol,'currency':stock.get('currency','USD')}
    except (ValueError,TypeError) as exc:
        return JSONResponse({'error':str(exc)},status_code=422)

@app.post('/api/strategy-study')
def strategy_study(body:StrategyStudyRequest):
    signature,market=file_cache.snapshot(DATA/'dashboard.json')
    if not market.get('stocks'):
        return JSONResponse({'error':'历史行情尚未准备，请先刷新行情。'},status_code=503)
    from strategy import build_strategy_study
    try:
        key=('strategy',signature,body.targetReturnPct,body.maxDrawdownPct)
        result=study_cache.get_or_build(key,lambda:build_strategy_study(
            market['stocks'],target_return_pct=body.targetReturnPct,max_drawdown_pct=body.maxDrawdownPct))
        return JSONResponse({**result,'marketGeneratedAt':market.get('generatedAt')})
    except (ValueError,TypeError) as exc:
        return JSONResponse({'error':str(exc)},status_code=422)

@app.get('/api/strategy-rules')
def strategy_rules():
    from strategy_rules import rules_catalog
    return rules_catalog()

@app.post('/api/holdings-plan')
def holdings_plan(body:HoldingsPlanRequest):
    holdings=read_cache(DATA/'holdings.json')
    if not isinstance(holdings.get('positions'),list):
        return JSONResponse({'error':'尚未取得账户持仓快照，请先手动同步 IBKR 账户。'},status_code=503)
    market=file_cache.snapshot(DATA/'dashboard.json')[1]
    from holdings_plan import build_holdings_plan
    reason=last_error or ('行情正在同步，完成前暂不生成操作候选' if refresh_lock.locked() else None)
    # Evaluate each request: account and market freshness must never be cached.
    result=build_holdings_plan(market,holdings,template_id=body.templateId,
                              risk_profile=body.riskProfile,allow_profit_add=body.allowProfitAdd,
                              market_issue=reason)
    return JSONResponse(result)

@app.post('/api/portfolio-study')
def portfolio_study(body:PortfolioStudyRequest):
    signature,market=file_cache.snapshot(DATA/'dashboard.json')
    spy=(market.get('benchmark') or {}).get('history')
    if not market.get('stocks') or not spy:
        return JSONResponse({'error':'完整策略需要 SPY 历史行情，请先刷新行情后重试。'},status_code=503)
    from portfolio_study import build_portfolio_study
    try:
        key=('portfolio',signature,body.templateId,body.riskProfile,body.targetReturnPct)
        result=study_cache.get_or_build(key,lambda:build_portfolio_study(
            market['stocks'],spy,template_id=body.templateId,
            risk_profile=body.riskProfile,target_return_pct=body.targetReturnPct))
        relevant={'NVDA','MRVL','AVGO','MU','GOOG','SPY'}
        incomplete=any(w.get('symbol') in relevant for w in market.get('marketWarnings',[]) if isinstance(w,dict))
        failed=any(e.get('symbol') in relevant for e in (market.get('quoteRefresh') or {}).get('errors',[]) if isinstance(e,dict))
        stale_core=any(s.get('symbol') in relevant and s.get('quoteStatus')=='stale' for s in market.get('stocks',[]))
        stale_core=stale_core or (market.get('benchmark') or {}).get('quoteStatus')=='stale'
        reason=(last_error or ('行情正在同步，完成前暂不生成新候选' if refresh_lock.locked() else None)
                or ('研究股票或 SPY 更新失败，暂时保留缓存行情，请刷新后再查看候选' if failed or stale_core else None)
                or ('研究股票或 SPY 有未完成的行情字段，请刷新后再查看候选' if incomplete else None)
                or ('行情快照超过24小时未成功同步，请刷新行情' if cache_is_stale(market.get('generatedAt'),24) else None))
        if reason:
            if result.get('simulation'):
                result['simulation']['latestSignals']=[]
                result['simulation']['state']={**result['simulation'].get('state',{}),'dataStatus':'stale',
                                               'label':'行情未就绪，仅保留截至历史日期的模拟记录'}
            result['warnings']=[f'暂不生成新候选：{reason}',*result.get('warnings',[])]
        return JSONResponse({**result,'marketGeneratedAt':market.get('generatedAt'),
                'marketSnapshotStatus':{'signalsAllowed':not bool(reason),'reason':reason}})
    except (ValueError,TypeError) as exc:
        return JSONResponse({'error':str(exc)},status_code=422)

from daily_sync import DailySync
from personal_routes import personal_router


def _daily_result(value):
    if isinstance(value,JSONResponse):
        if value.status_code >= 400:
            raise RuntimeError('数据源更新未完成')
        return json.loads(value.body)
    return value


daily_sync = DailySync(
    DATA,
    lambda: _daily_result(refresh()),
    lambda symbol: _daily_result(refresh_company(SymbolRequest(symbol=symbol))),
    lambda symbol: _daily_result(refresh_news(SymbolRequest(symbol=symbol))),
)
app.include_router(personal_router(
    lambda: DATA,
    lambda: enrich_market(read_cache(DATA/'dashboard.json'),DATA),
    lambda: load_registry(DATA),
    lambda: last_error or ('日线正在更新，请完成后重新计算计划' if refresh_lock.locked() else None),
    lambda: daily_sync.status(),
))


if __name__=='__main__':
    daily_sync.start()
    try:
        uvicorn.run(app,host='127.0.0.1',port=18765,log_level='warning')
    finally:
        daily_sync.stop()
