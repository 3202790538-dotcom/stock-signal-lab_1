"use client";

import {Fragment, useEffect, useId, useRef, useState} from 'react';
import {ArrowRight, Check, SlidersHorizontal, ListChecks, ChevronDown, ChevronRight, CircleHelp, Clock3, RefreshCw, Search, ShieldCheck, Target, Wallet} from 'lucide-react';
import {defaultSettings, formatMoney, formatNumber, formatTime, parseSettingsDraft, parsePersonalPlanResponse, personalError, personalRequest, settingsDraft, type DailySyncStatus, type PersonalPlanData, type PersonalPlanItem, type PersonalSettings, type SettingsDraft} from './personal-plan-types';
import {planGroupCounts,planGroupLabels,visiblePlanItems,type PlanGroup} from './personal-plan-view';
import './personal-plan.css';
import './personal-plan-flow.css';

type Props={marketStamp:string;marketIssue?:string;hidden:boolean;onShowResearch:(symbol:string)=>void;onShowAccount:()=>void;onShowBacktest:()=>void};
const strategyLabels={long_term:'长线趋势',pullback:'趋势回踩',rotation:'月度动量轮动'};
const strategyDescriptions={long_term:'每日收盘检查：趋势向上、放量突破，再核对买入条件。',pullback:'每日收盘检查：上涨趋势中的回踩与重新走强。',rotation:'月初复核动量排名；完整候选池未核实前暂停新仓。'};
const protectedText=(value:string,hidden:boolean)=>hidden?value.replace(/(SMA\d+|\d+(?=\s*根完整日线)|(?:200|209|126|120|60|50|20|14|10)(?=\s*(?:根|日|个交易日|天)))|-?\d[\d,]*(?:\.\d+)?%?/g,(match,technical)=>technical||'••'):value;
const observation=(value:unknown):string=>value===null||value===undefined?'待核验':typeof value==='boolean'?value?'是':'否':typeof value==='number'?formatNumber(value,4):typeof value==='object'?JSON.stringify(value):String(value);

function PlanRowDetails({item,hidden,stale}:{item:PersonalPlanItem;hidden:boolean;stale:boolean}){
  const proof=item.verification;
  const evidenceUrl=proof?.source?.url;
  const evidenceLink=evidenceUrl&&/^https:\/\/(?:[a-z0-9-]+\.)?(?:sec\.gov|finance\.yahoo\.com|nasdaq\.com|nasdaqtrader\.com)\//i.test(evidenceUrl)?evidenceUrl:null;
  return <div className="pp-row-details"><p className="pp-row-conclusion"><b>本行判断</b> {protectedText(item.rationale,hidden)}</p>{stale&&<p className="pp-inline-reason">数据未通过时效核验，下列条件为快照参考，股数与资金计算暂不展示。</p>}
    {proof&&<p className="pp-note">已检查 {proof.availableBars} 根完整日线 · 本策略需要 {proof.requiredBars} 根{proof.source&&<> · {evidenceLink?<a href={evidenceLink} target="_blank" rel="noreferrer">{proof.source.source||'查看核验来源'}</a>:proof.source.source||'公开证券资料'} · 核验时间 {formatTime(proof.source.checkedAt)}</>}</p>}
    <div className="pp-detail-grid"><section><h4>触发条件</h4><ul className="pp-check-list">{item.conditions.map((condition,index)=><li key={index}><span className={`pp-check ${condition.passed===null?'unknown':condition.passed?'pass':'fail'}`} aria-label={condition.passed===null?'待核验':condition.passed?'满足':'未满足'}>{condition.passed===null?<CircleHelp size={15}/>:condition.passed?<Check size={15}/>:<span>—</span>}</span><div><b>{protectedText(condition.label,hidden)}</b>{(condition.observed!==undefined||condition.required!==undefined)&&<small>快照 {hidden?'••••':observation(condition.observed)} · 要求 {hidden?'••••':observation(condition.required)}</small>}</div></li>)}</ul></section>
      <section><h4>股数与资金限制</h4><dl className="pp-detail-facts">{item.sizingLimits.map((limit,index)=><div key={index}><dt>{protectedText(limit.label,hidden)}</dt><dd>{hidden?'••••':stale?'待重新核验':limit.quantity===null?'待核验':`${formatNumber(limit.quantity,6)} 股`}</dd></div>)}<div><dt>单项理论最大数量</dt><dd>{hidden?'••••':stale?'待重新核验':formatNumber(item.theoreticalMaxQuantity,6)}</dd></div><div><dt>参考止损</dt><dd>{stale?'待重新核验':formatMoney(item.entry?.proposedStop,hidden,2)}</dd></div><div><dt>每股计划风险</dt><dd>{stale?'待重新核验':formatMoney(item.entry?.riskPerShare,hidden,2)}</dd></div></dl><p className="pp-note">最终股数按排序依次占用共同资金与风险额度；理论最大值不可相加。拟卖出资金不提前用于买入。</p>{item.reduction&&!stale&&<p className="pp-inline-reason">{protectedText(item.reduction.reason,hidden)} · 条件减持 {hidden?'••••':formatNumber(item.reduction.quantity,6)} 股，参考 {formatMoney(item.reduction.referencePrice,hidden,2)}</p>}</section></div>
    {item.blockReasons.length>0&&<div className="pp-detail-reasons"><b>当前限制</b><ul>{item.blockReasons.map((reason,index)=><li key={index}>{protectedText(reason,hidden)}</li>)}</ul></div>}
    <div className="pp-detail-grid pp-detail-bottom"><section><h4>下次复核</h4><p>{protectedText(item.nextReview,hidden)}</p><p className="pp-note">本标的完整日线截至 {formatTime(item.marketAsOf)}。参考止损是条件计划，不是已下达委托。</p></section><section><h4>取消或重新计算</h4><ul>{item.cancelConditions.map((condition,index)=><li key={index}>{protectedText(condition,hidden)}</li>)}</ul></section></div>
    {item.warnings.length>0&&<ul className="pp-note">{item.warnings.map((warning,index)=><li key={index}>{protectedText(warning,hidden)}</li>)}</ul>}
  </div>;
}

function PlanTable({items,holdings,hidden,stale,onShowResearch}:{items:PersonalPlanItem[];holdings:boolean;hidden:boolean;stale:boolean;onShowResearch:(symbol:string)=>void}){
  const [expanded,setExpanded]=useState<string[]>([]);
  const [query,setQuery]=useState('');
  const [filter,setFilter]=useState<PlanGroup>('all');
  const [page,setPage]=useState(1);
  const prefix=useId();
  const counts=planGroupCounts(items,stale);
  const filtered=visiblePlanItems(items,filter,query,stale);
  const pageSize=12;
  const currentPage=Math.min(page,Math.max(1,Math.ceil(filtered.length/pageSize)));
  const shown=filtered.slice((currentPage-1)*pageSize,currentPage*pageSize);
  const selectFilter=(group:PlanGroup)=>{setFilter(group);setPage(1);};
  return <section className="pp-plan-section" aria-label={holdings?'持仓行动':'自选候选'}>
    <div className="pp-action-overview" role="group" aria-label={holdings?'持仓行动筛选':'自选行动筛选'}>{(Object.keys(planGroupLabels) as Exclude<PlanGroup,'all'>[]).map(group=><button key={group} className={`pp-action-count pp-action-${group} ${filter===group?'selected':''}`} aria-pressed={filter===group} onClick={()=>selectFilter(filter===group?'all':group)}><span>{planGroupLabels[group]}</span><strong>{counts[group]}<small> 项</small></strong><ChevronRight size={16}/></button>)}</div>
    <div className="pp-panel pp-plan-table"><div className="pp-section-heading"><div><h2>{holdings?<Wallet size={18}/>:<Target size={18}/>} {holdings?'持仓行动':'自选候选'}</h2><p>{stale?'当前数据待重新核验，原行动数量已暂停展示。':holdings?'先看需要复核的持仓，再查看新机会。':'按条件查看机会；股数已经过共享资金与风险约束。'}</p></div><div className="pp-table-controls"><button className={`pp-filter-all ${filter==='all'?'selected':''}`} aria-pressed={filter==='all'} onClick={()=>selectFilter('all')}>全部 {items.length}</button><label className="pp-search"><Search size={15}/><input aria-label={holdings?'搜索持仓计划':'筛选计划候选'} placeholder="代码或公司" value={query} onChange={event=>{setQuery(event.target.value);setPage(1);}}/></label></div></div>
    <div className="pp-table-scroll"><table className="pp-table"><thead><tr><th>股票</th>{holdings&&<th>当前持仓</th>}<th>条件判断 · 为什么</th><th>参考价格</th><th>计划数量</th><th>操作明细</th></tr></thead><tbody>{shown.map(item=>{
      const open=expanded.includes(item.id);const detailsId=`${prefix}-${item.id}`;
      const quantity=item.reduction?item.reduction.quantity:item.quantity;
      const reducing=item.status==='exit'||item.status==='reduce';
      return <Fragment key={item.id}><tr><td>{item.marketAsOf?<button className="pp-symbol" aria-label={`${item.symbol} 股票研究`} onClick={()=>onShowResearch(item.symbol)}>{item.symbol}<ArrowRight size={13}/></button>:<span className="pp-symbol">{item.symbol}</span>}<small className="pp-cell-sub pp-company-name" title={item.name}>{item.name||'名称未提供'}</small><small className="pp-cell-sub">日线 {item.marketAsOf||'缺失'}</small></td>{holdings&&<td><b>{hidden?'••••':formatNumber(item.holding?.quantity,6)} 股</b><small className="pp-cell-sub">{formatMoney(item.holding?.marketValue,hidden)}</small></td>}<td className="pp-action-cell"><span className={`pp-badge pp-status-${stale?'verify':item.status}`}>{stale?'等待重新核验':item.label}</span><p>{protectedText(item.rationale,hidden)}</p></td><td><b>{stale?'待重新核验':reducing?formatMoney(item.reduction?.referencePrice,false,2):item.entry?`${formatMoney(item.entry.lowerPrice,false,2)} – ${formatMoney(item.entry.upperPrice,false,2)}`:'尚无区间'}</b><small className="pp-cell-sub">{reducing?'减持参考':'买入参考区间'}</small>{!reducing&&item.entry&&<small className="pp-cell-sub">计划止损 {stale?'待重新核验':formatMoney(item.entry.proposedStop,false,2)}</small>}</td><td className="pp-quantity"><strong>{hidden?'••••':stale?'待重算':quantity===null?'待核验':item.quantityStatus==='not_applicable'&&!item.reduction?'—':formatNumber(quantity,6)}</strong><small className="pp-cell-sub">{stale?'暂停数量':item.reduction?'减持 / 退出':item.quantityStatus==='conditional'?'条件买入':quantity===0?'暂不买入':item.quantityStatus==='not_applicable'?'仅持有复核':'额度未确认'}</small></td><td><button className="pp-expand pp-details-button" aria-label={`${open?'收起':'展开'} ${item.symbol} 条件与计算`} aria-expanded={open} aria-controls={detailsId} onClick={()=>setExpanded(current=>open?current.filter(id=>id!==item.id):[...current,item.id])}>{open?'收起':'查看规则'}{open?<ChevronDown size={15}/>:<ChevronRight size={15}/>}</button></td></tr>{open&&<tr className="pp-detail-row" id={detailsId}><td colSpan={holdings?6:5}><PlanRowDetails item={item} hidden={hidden} stale={stale}/></td></tr>}</Fragment>;
    })}</tbody></table>{shown.length===0&&<div className="pp-empty-row"><ListChecks size={23}/><p>{items.length===0?(holdings?'尚无持仓，先到“账户与设置”填写或选择账户。':'暂未导入自选，可到股票研究查看清单。'):filter==='conditional'?'当前没有通过全部条件的买入计划。':filter==='review'?'当前没有减持或退出复核项。':'当前筛选没有匹配项。'}</p>{items.length>0&&(filter!=='all'||query)&&<button className="pp-link" onClick={()=>{selectFilter('all');setQuery('');}}>查看全部</button>}</div>}</div>
    <div className="pp-table-bottom"><span>显示排序不改变已分配股数，多个理论上限不可相加。</span>{filtered.length>pageSize&&<div className="pp-pagination"><span>{currentPage} / {Math.ceil(filtered.length/pageSize)}</span><button className="pp-button" disabled={currentPage===1} onClick={()=>setPage(currentPage-1)}>上一页</button><button className="pp-button" disabled={currentPage*pageSize>=filtered.length} onClick={()=>setPage(currentPage+1)}>下一页</button></div>}</div>
    </div>
  </section>;
}

function PlanSummary({plan,hidden,onShowAccount}:{plan:PersonalPlanData;hidden:boolean;onShowAccount:()=>void}){
  const {account,allocation,summary}=plan;
  return <section className="pp-compact-account" aria-label="账户分配与风险摘要">
    <div className="pp-account-metrics"><div className="pp-metric"><span>全账户净资产</span><strong>{formatMoney(account.netLiquidation,hidden)}</strong><small>{account.source==='manual'?'手工账户':'IBKR 快照'} · 现金 {formatMoney(account.cash,hidden)}</small></div><div className="pp-metric"><span>计划投入 · {allocation.allocationPct}%</span><strong>{formatMoney(allocation.allocatedCapital,hidden)}</strong><small>已有仓位 {formatMoney(allocation.committedCapital,hidden)}</small></div><div className="pp-metric"><span>剩余策略额度</span><strong>{formatMoney(allocation.remainingCapital,hidden)}</strong><small>还需通过现金与风险检查</small></div><div className="pp-metric pp-metric-risk"><span>持仓计划止损风险</span><strong>{account.openRiskStatus==='unknown'?'待核验':formatMoney(account.openRiskValue,hidden)}</strong><small>{account.openRiskStatus==='unknown'?'请补齐持仓计划止损':`单笔预算 ${formatMoney(allocation.tradeRiskBudget,hidden)}`}</small></div></div>
    {summary.blockReasons.length>0&&<div className="pp-constraint-line"><details><summary><ShieldCheck size={15}/><span>{protectedText(summary.blockReasons[0],hidden)}</span><b>查看 {summary.blockReasons.length} 项限制</b></summary><ul>{summary.blockReasons.map((reason,index)=><li key={index}>{protectedText(reason,hidden)}</li>)}</ul></details><button className="pp-link" onClick={onShowAccount}>核对账户<ChevronRight size={14}/></button></div>}
  </section>;
}

function PlanRiskDetails({plan,hidden,stale,onShowBacktest}:{plan:PersonalPlanData;hidden:boolean;stale:boolean;onShowBacktest:()=>void}){
 const {account,allocation,validation,summary}=plan;
 return <details className="pp-panel pp-risk-details"><summary><ShieldCheck size={17}/><span>资金约束与目标说明</span><small>风险预算、账户日期与验证范围</small><ChevronDown size={16}/></summary><div className="pp-risk-details-body"><div className="pp-risk-strip"><span>每笔风险预算 <b>{formatMoney(allocation.tradeRiskBudget,hidden)}</b> · {allocation.tradeRiskPct}%</span><span>持仓计划风险上限 <b>{formatMoney(allocation.openRiskBudget,hidden)}</b></span><span>预留现金 <b>{formatMoney(allocation.reserveCashValue,hidden)}</b></span><span>已分配条件买入 <b>{stale?'待重新核验':formatMoney(summary.plannedBuyValue,hidden)}</b></span><span>实际历史回撤 <b>{hidden?'••••':account.currentDrawdownPct===null?'未核验':`${account.currentDrawdownPct}%`}</b></span></div><p className="pp-note">{account.source==='manual'?'手工账户 · 假设填写后未发生交易':'IBKR 手动快照'} · 账户记录 {formatTime(account.asOf)} · {account.currency||'币种待核验'}。止损风险为计划值，不代表券商已有委托。</p><div className="pp-target-line"><span className="pp-badge pp-status-wait">目标尚未验证</span><p>年度目标 {validation.targetReturnPct}% · {formatMoney(allocation.annualTargetValue,hidden)} <span className="pp-divider">/</span>回撤容忍 {validation.maxDrawdownPct}% · {formatMoney(allocation.drawdownBudgetValue,hidden)}</p><button className="pp-link" onClick={onShowBacktest}>查看历史研究<ChevronRight size={15}/></button></div><p>{protectedText(validation.conclusion,hidden)}</p><ul>{validation.warnings.map((warning,index)=><li key={index}>{protectedText(warning,hidden)}</li>)}</ul><p className="pp-note">目标金额以分配资金计算；回撤容忍不保证实际亏损上限，旧回测不验证当前账户的新规则。</p></div></details>;
}

export function PersonalPlan({marketStamp,marketIssue='',hidden,onShowResearch,onShowAccount,onShowBacktest}:Props){
  const [saved,setSaved]=useState<PersonalSettings|null>(null);
  const [settingsOpen,setSettingsOpen]=useState(false);
  const [scope,setScope]=useState<'holding'|'watchlist'>('holding');
  const tabPrefix=useId();
  const [draft,setDraft]=useState<SettingsDraft>(settingsDraft(defaultSettings));
  const [loadError,setLoadError]=useState('');
  const [saveError,setSaveError]=useState('');
  const [saving,setSaving]=useState(false);
  const [revision,setRevision]=useState(0);
  const [loadRevision,setLoadRevision]=useState(0);
  const [sync,setSync]=useState<DailySyncStatus|null>(null);
  const [syncError,setSyncError]=useState('');
  const [result,setResult]=useState<{key:string;data:PersonalPlanData}|null>(null);
  const [requestError,setRequestError]=useState<{key:string;message:string}|null>(null);
  const [expiredKey,setExpiredKey]=useState('');
  const saveController=useRef<AbortController|null>(null);
  const dirty=saved!==null&&JSON.stringify(draft)!==JSON.stringify(settingsDraft(saved));
  const key=saved?JSON.stringify(saved)+'|'+marketStamp+'|'+revision+'|'+(sync?.lastSuccessAt||'')+'|'+(sync?.running?'updating':'ready')+'|'+marketIssue:'';
  const plan=result?.key===key?result.data:null;
  const planError=requestError?.key===key?requestError.message:'';

  useEffect(()=>{
    const controller=new AbortController();
    personalRequest<PersonalSettings>('/api/personal-settings',{signal:controller.signal}).then(value=>{if(!controller.signal.aborted){setSaved(value);setDraft(settingsDraft(value));setLoadError('');}}).catch(reason=>{if(!controller.signal.aborted)setLoadError(personalError(reason));});
    return()=>controller.abort();
  },[loadRevision]);
  useEffect(()=>{
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{
      try{const value=await personalRequest<DailySyncStatus>('/api/daily-sync-status',{signal:controller.signal});if(!controller.signal.aborted){setSync(value);setSyncError('');}}
      catch(reason){if(!controller.signal.aborted)setSyncError(personalError(reason));}
      finally{if(!controller.signal.aborted)timer=setTimeout(()=>void poll(),30000);}
    };
    void poll();return()=>{controller.abort();clearTimeout(timer);};
  },[]);
  useEffect(()=>{
    if(!saved||!key)return;
    const controller=new AbortController();
    personalRequest<PersonalPlanData>('/api/personal-plan',{method:'POST',body:JSON.stringify(saved),signal:controller.signal}).then(value=>{
      if(!controller.signal.aborted)setResult({key,data:parsePersonalPlanResponse(value)});
    }).catch(reason=>{if(!controller.signal.aborted)setRequestError({key,message:personalError(reason)});});
    return()=>controller.abort();
  },[saved,key]);
  useEffect(()=>{
    if(!plan?.freshness?.expiresAt)return;
    const time=Date.parse(plan.freshness.expiresAt);if(!Number.isFinite(time))return;
    const timer=setTimeout(()=>setExpiredKey(key),Math.min(2147483647,Math.max(0,time-Date.now()+1)));
    return()=>clearTimeout(timer);
  },[key,plan]);
  useEffect(()=>()=>saveController.current?.abort(),[]);

  const setField=<K extends keyof SettingsDraft>(field:K,value:SettingsDraft[K])=>{setDraft(current=>({...current,[field]:value}));setSaveError('');};
  const save=async()=>{
    setSaveError('');let settings:PersonalSettings;
    try{settings=parseSettingsDraft(draft);}catch(reason){setSaveError(personalError(reason));return;}
    saveController.current?.abort();const controller=new AbortController();saveController.current=controller;setSaving(true);
    try{
      const latest=await personalRequest<PersonalSettings>('/api/personal-settings',{signal:controller.signal});
      const value=await personalRequest<PersonalSettings>('/api/personal-settings',{method:'PUT',body:JSON.stringify({...settings,accountSource:latest.accountSource}),signal:controller.signal});
      if(controller.signal.aborted)return;
      setSaved(value);setDraft(settingsDraft(value));setRevision(current=>current+1);setSettingsOpen(false);
    }catch(reason){if(!controller.signal.aborted)setSaveError(personalError(reason));}
    finally{if(!controller.signal.aborted)setSaving(false);}
  };
  const currentIssue=marketIssue||(sync?.channels?.market?.status==='running'?'日线正在更新，完成前暂停条件股数。':'');
  const stale=expiredKey===key||Boolean(currentIssue);
  const freshnessBlocked=Boolean(plan&&(!plan.freshness?.marketFresh||!plan.freshness?.accountFresh));
  const researchFailures=Object.entries(sync?.channels||{}).filter(([name,channel])=>(name==='company'||name==='news')&&channel.status==='error');
  const marketUpdateText=sync?.channels?.market?.status==='running'?'行情更新中':`最近获取 ${formatTime(marketStamp)}`;
  return <div className="personal-workspace personal-plan-flow">
    <section className="pp-strategy-bar" aria-label="当前策略"><div className="pp-strategy-identity"><span className="pp-strategy-icon"><Target size={22}/></span><div><h2>{saved?strategyLabels[saved.templateId]:'正在读取策略'}<span>规则待验证</span></h2><p>{saved?strategyDescriptions[saved.templateId]:'读取账户与已保存参数'}</p></div></div><div className="pp-strategy-config">{saved&&<><span>投入 <b>{saved.allocationPct}%</b></span><span>收益目标 <b>{saved.targetReturnPct}%</b></span><span>回撤容忍 <b>{saved.maxDrawdownPct}%</b></span></>}<button className={`pp-button ${settingsOpen?'selected':''}`} aria-expanded={settingsOpen} aria-controls={`${tabPrefix}-settings`} disabled={!saved||saving} onClick={()=>setSettingsOpen(value=>dirty?true:!value)}><SlidersHorizontal size={15}/>{settingsOpen?'调整中':'调整策略'}</button></div></section>
    <div id={`${tabPrefix}-settings`} hidden={!settingsOpen}>
    <section className="pp-panel pp-settings" aria-label="个人计划设置"><div className="pp-editor-title"><div><h3>调整交易偏好</h3><p>修改后保存生效；收益目标不会扩大仓位或绕过风控。</p></div><button className="pp-link" disabled={saving} onClick={()=>{if(saved)setDraft(settingsDraft(saved));setSaveError('');setSettingsOpen(false);}}>取消修改</button></div><form onSubmit={event=>{event.preventDefault();void save();}}><fieldset disabled={!saved||saving}><div className="pp-settings-row"><label>策略模板<select aria-label="个人策略模板" value={draft.templateId} onChange={event=>setField('templateId',event.target.value as PersonalSettings['templateId'])}>{Object.entries(strategyLabels).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label><label>账户资金比例 <span>%</span><input aria-label="策略使用全账户资金比例" type="number" min="0" max="100" step="any" value={draft.allocationPct} onChange={event=>setField('allocationPct',event.target.value)}/></label><label>年度收益目标 <span>%</span><input aria-label="年度收益目标" type="number" min="0" max="200" step="any" value={draft.targetReturnPct} onChange={event=>setField('targetReturnPct',event.target.value)}/></label><label>最大回撤容忍 <span>%</span><input aria-label="最大回撤容忍" type="number" min="1" max="80" step="any" value={draft.maxDrawdownPct} onChange={event=>setField('maxDrawdownPct',event.target.value)}/></label><label className="pp-checkbox"><input type="checkbox" checked={draft.allowProfitAdd} onChange={event=>setField('allowProfitAdd',event.target.checked)}/>允许盈利加仓</label><button className="pp-button pp-primary" type="submit">{saving?'保存中…':'保存并生成计划'}</button></div><details className="pp-settings-more"><summary>现金保留与规则说明</summary><div><label>保留现金占净资产 <input aria-label="现金保留比例" type="number" min="0" max="100" step="any" value={draft.reserveCashPct} onChange={event=>setField('reserveCashPct',event.target.value)}/>%</label><p>资金比例可设为 0–100%；已有持仓始终计入全账户占用。只有盈利持仓可在通过所有条件后追加，不借入新资金。</p></div></details></fieldset></form>{saveError&&<p className="pp-inline-error" role="alert">{saveError}</p>}</section>
    </div>
    <div className="pp-freshness"><span><Clock3 size={14}/>{sync?.running?'每日更新进行中':`每日更新 · 下次 ${formatTime(sync?.nextRunAt)}`}</span><span>{marketUpdateText}</span><button className="pp-link" onClick={onShowAccount}>{saved?.accountSource==='manual'?'手工账户':'IBKR 快照'}<ChevronRight size={14}/></button>{(syncError||researchFailures.length>0)&&<details className="pp-sync-detail"><summary>资料更新详情{researchFailures.length?` · ${researchFailures.length} 类未完成`:''}</summary><div>{syncError&&<p>{syncError}</p>}{researchFailures.map(([name,channel])=><p key={name}>{name==='company'?'公司资料':'持仓新闻'}：{channel.lastError||'更新失败'}；本轮更新 {channel.updatedCount} 项 · 最近尝试 {formatTime(channel.lastAttemptAt)}</p>)}</div></details>}</div>
    {loadError&&<div className="pp-alert" role="alert">{loadError}<button className="pp-button" onClick={()=>setLoadRevision(value=>value+1)}>重新读取设置</button></div>}
    {dirty||saving?<section className="pp-panel pp-empty" role="status"><Target size={24}/><h3>{saving?'正在保存并重新计算':'设置尚未应用'}</h3><p>保存后按新的资金比例、目标和风险限制生成计划。</p></section>:planError?<section className="pp-panel pp-empty"><p role="alert">{planError}</p><button className="pp-button" onClick={()=>setRevision(value=>value+1)}>重试计划计算</button></section>:!plan?<section className="pp-panel pp-empty" aria-busy={!loadError}><p role="status">{loadError?'设置读取失败，暂未生成计划。':'正在核对全账户、持仓和自选条件…'}</p></section>:<>
      {(stale||freshnessBlocked)&&<div className="pp-alert" role="status"><span>{currentIssue|| (expiredKey===key?'此计划已到复核时间，条件股数暂不展示。':!plan.freshness?.accountFresh?'账户快照需要更新；下方按每项核验结果限制条件股数。':'日线尚未完成新鲜度核验；下方列出各标的具体限制。')}</span><button className="pp-link" onClick={()=>setRevision(value=>value+1)}><RefreshCw size={14}/>重新核验</button></div>}
      <PlanSummary plan={plan} hidden={hidden} onShowAccount={onShowAccount}/>
      <div className="pp-focus-navigation"><div className="pp-focus-tabs" role="tablist" aria-label="计划范围"><button role="tab" id={`${tabPrefix}-holding-tab`} aria-selected={scope==='holding'} aria-controls={`${tabPrefix}-list`} onClick={()=>setScope('holding')}><Wallet size={17}/>我的持仓 <b>{plan.summary.holdingCount}</b></button><button role="tab" id={`${tabPrefix}-watchlist-tab`} aria-selected={scope==='watchlist'} aria-controls={`${tabPrefix}-list`} onClick={()=>setScope('watchlist')}><Search size={17}/>自选机会 <b>{plan.summary.candidateCount}</b></button></div><button className="pp-link" onClick={()=>setRevision(value=>value+1)}><RefreshCw size={14}/>重新核验计划</button></div>
      <div role="tabpanel" id={`${tabPrefix}-list`} aria-labelledby={`${tabPrefix}-${scope}-tab`}><PlanTable key={scope} items={plan.items.filter(item=>item.origin===scope)} holdings={scope==='holding'} hidden={hidden} stale={stale} onShowResearch={onShowResearch}/></div>
      <p className="pp-validation-note"><CircleHelp size={15}/><span>条件计划需人工复核；新规则尚未独立回测，收益目标与回撤容忍均非保证。</span><button className="pp-link" onClick={onShowBacktest}>查看历史研究<ChevronRight size={14}/></button></p>
      <PlanRiskDetails plan={plan} hidden={hidden} stale={stale} onShowBacktest={onShowBacktest}/>
      <details className="pp-panel pp-rule-guide"><summary><ShieldCheck size={18}/>{plan.rules.label} · 入场、退出和执行规则</summary><div className="pp-detail-grid"><section><h4>入场</h4><ul>{plan.rules.entry.map((rule,index)=><li key={index}>{rule}</li>)}</ul><h4>退出</h4><ul>{plan.rules.exit.map((rule,index)=><li key={index}>{rule}</li>)}</ul></section><section><h4>资金与时机</h4><ul>{plan.rules.sizing.map((rule,index)=><li key={index}>{protectedText(rule,hidden)}</li>)}</ul><p>{plan.rules.timing}</p></section></div>{plan.warnings.length>0&&<ul>{plan.warnings.map((warning,index)=><li key={index}>{protectedText(warning,hidden)}</li>)}</ul>}</details>
      <p className="pp-footnote">所有数量、价格与时机均为条件计划。使用已完成日线，下一常规交易时段核对价格、现有委托和账户后自行决定；本工具不会下单。计划生成于 {formatTime(plan.generatedAt)}。</p>
    </>}
  </div>;
}
