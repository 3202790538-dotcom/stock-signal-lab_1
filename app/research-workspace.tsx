"use client";
import {useState} from "react";
import {CompanyIntelligence} from "./company-intelligence";
import {MultiPeriod,ResearchPanel,type Fundamental,type ResearchStock} from "./research-panel";
import "./research-workspace.css";

type WorkspaceStock=ResearchStock&{quoteType?:string;quoteStatus?:'ready'|'stale';quoteError?:string|null;exchange?:string};
export function ResearchWorkspace({stocks,selected,onSelect,fundamentals}:{stocks:WorkspaceStock[];selected:string;onSelect:(symbol:string)=>void;fundamentals:Fundamental[]}){
 const[section,setSection]=useState<'financials'|'news'|'technical'>('financials');
 const stock=stocks.find(item=>item.symbol===selected)||stocks[0];
 if(!stock)return <section className="panel"><p className="research-inner">尚无有效日线可用于研究。请先刷新行情。</p></section>;
 const nonEquity=['ETF','MUTUALFUND','INDEX','CURRENCY','CRYPTOCURRENCY','FUTURE','OPTION'].includes(stock.quoteType?.toUpperCase()||'');
 return <div className="company-workspace"><div className="research-symbol-selector"><label>研究标的<select aria-label="选择研究股票" value={stock.symbol} onChange={event=>onSelect(event.target.value)}>{stocks.map(item=><option key={item.symbol} value={item.symbol}>{item.symbol} · {item.name}{item.quoteType==='ETF'?' · ETF':''}</option>)}</select></label><span>{stock.exchange||'交易所未提供'} · USD · {stocks.length} 个有行情标的{stock.quoteStatus==='stale'?` · 缓存截至 ${stock.asOf}`:''}</span></div><div className="company-workspace-tabs" aria-label="研究内容">{([['financials','自动财报与估值'],['news','新闻与公告'],['technical','走势与研究存档']] as const).map(([key,label])=><button key={key} className={section===key?'active':''} aria-pressed={section===key} onClick={()=>setSection(key)}>{label}</button>)}</div>
 {nonEquity&&section==='financials'?<section className="panel research-capability"><h2>{stock.symbol} · {stock.quoteType==='ETF'?'ETF / 基金':'非普通股票'}</h2><p>企业季度财报、每股收益和公司市盈率不适用于此类标的。当前提供公开日线、技术指标与相关新闻；基金持仓、净值和费用率尚未接入。</p><button onClick={()=>setSection('technical')}>查看走势与历史表现</button></section>:section==='technical'?nonEquity?<><MultiPeriod stock={stock}/><section className="panel research-capability"><h2>{stock.symbol} · 技术研究</h2><p>{stock.research?.longTrend.description||'有效日线样本不足，暂不提供长期趋势判断。'}</p><p>日线图表、均线与波动指标可在市场观察的“行情与走势”中查看。此标的不使用企业 EPS 估值模型。</p></section></>:<div className="workspace-technical"><ResearchPanel stocks={stocks} selected={stock.symbol} onSelect={onSelect} fundamentals={fundamentals}/></div>:<CompanyIntelligence key={stock.symbol} symbol={stock.symbol} section={section}/>}
 </div>;
}
