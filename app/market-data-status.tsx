"use client";
import {useEffect,useRef} from 'react';
import {ChevronDown,Clock3,X} from 'lucide-react';
import type {Data} from './dashboard-types';
import {when} from './dashboard-format';
import {marketDataHealth} from './market-data-health';
import './market-data-status.css';

export function MarketDataStatus({data}:{data:Data}){
  const root=useRef<HTMLDetailsElement>(null);
  const health=marketDataHealth(data);
  useEffect(()=>{
    const close=(event:PointerEvent)=>{if(root.current&&!root.current.contains(event.target as Node))root.current.open=false;};
    document.addEventListener('pointerdown',close);return()=>document.removeEventListener('pointerdown',close);
  },[]);
  return <details className="market-health" ref={root} onKeyDown={event=>{if(event.key==='Escape'&&root.current){root.current.open=false;root.current.querySelector('summary')?.focus();}}}>
    <summary aria-label={`行情状态，日线 ${health.dateLabel}，${health.issues.length} 个标的待补齐`}><Clock3 size={14}/><span>日线 {health.dateLabel}</span>{health.issues.length>0&&<b>{health.issues.length} 待补齐</b>}<ChevronDown size={14}/></summary>
    <section className="market-health-panel" aria-label="行情更新明细">
      <header><div><h2>行情状态</h2><p>{health.availableCount} / {health.totalCount} 个行情标的通过本轮数据检查</p></div><button aria-label="关闭行情状态" onClick={()=>{if(root.current)root.current.open=false;}}><X size={17}/></button></header>
      <p className="market-health-time">本轮获取 {when(data.generatedAt)} · 基准 {data.benchmark.asOf||'未提供'}</p>
      {health.issues.length>0?<><p className="market-health-note">待补齐标的沿用各自最近的完整日线；相关交易条件暂不通过核验。其他标的按各自数据检查结果计算。</p>{health.benchmarkIssue&&<p className="market-health-benchmark">SPY 基准也有缺项，依赖市场趋势的入场条件暂不可用。</p>}<div className="market-health-list">{health.issues.map(issue=><div key={issue.symbol}><b>{issue.symbol}</b><span>{issue.reason}<small>{issue.asOf?`当前可用日线 ${issue.asOf}`:'暂无有效日线'}</small></span></div>)}</div><p className="market-health-footnote">每天自动更新。缺项可能由数据源延迟提供造成，连续刷新不一定能补齐。</p></>:<p className="market-health-note">本轮未发现字段缺项或取数失败。价格为已完成日线，交易前仍需核对实际报价。</p>}
    </section>
  </details>;
}
