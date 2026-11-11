"use client";

import {useEffect, useRef, useState} from 'react';
import {ArrowRight, Check, CircleHelp, Database, Plus, Save, ShieldCheck, Trash2, Undo2, Wallet} from 'lucide-react';
import {formatTime, manualDraft, parseManualDraft, personalError, personalRequest, type ManualAccount, type ManualPositionDraft, type PersonalSettings} from './personal-plan-types';
import './personal-plan.css';
import './personal-account-flow.css';

type Props={marketStamp:string; hidden:boolean; active?:boolean; onChanged:()=>void; onShowPlan?:()=>void};
type Row=ManualPositionDraft & {key:number};
const blankRow=(key:number):Row=>({key,symbol:'',quantity:'',averageCost:'',markPrice:'',stopPrice:'',sector:'unknown'});

export function PersonalAccount({hidden,active=true,onChanged,onShowPlan}:Props){
  const [settings,setSettings]=useState<PersonalSettings|null>(null);
  const [cash,setCash]=useState('0');
  const [rows,setRows]=useState<Row[]>([]);
  const [updatedAt,setUpdatedAt]=useState<string|null>(null);
  const [savedAccount,setSavedAccount]=useState<ManualAccount|null>(null);
  const [ready,setReady]=useState(false);
  const [loadError,setLoadError]=useState('');
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [busy,setBusy]=useState(false);
  const [sourceBusy,setSourceBusy]=useState(false);
  const [dirty,setDirty]=useState(false);
  const [retry,setRetry]=useState(0);
  const dirtyRef=useRef(false);
  const sequence=useRef(0);
  const readController=useRef<AbortController|null>(null);
  const saveController=useRef<AbortController|null>(null);
  const sourceController=useRef<AbortController|null>(null);

  useEffect(()=>{
    if(!active||dirtyRef.current)return;
    const controller=new AbortController();
    readController.current=controller;
    Promise.all([personalRequest<PersonalSettings>('/api/personal-settings',{signal:controller.signal}),personalRequest<ManualAccount>('/api/manual-account',{signal:controller.signal})]).then(([saved,account])=>{
      if(controller.signal.aborted||dirtyRef.current)return;
      if(typeof account.cash!=='number'||!Number.isFinite(account.cash)||!Array.isArray(account.positions))throw new Error('手工账户数据格式异常，请重新读取。');
      const nextRows=account.positions.map(position=>({...manualDraft(position),key:++sequence.current}));
      setSettings(saved);setCash(String(account.cash));setRows(nextRows);setUpdatedAt(account.updatedAt);setSavedAccount(account);setReady(true);setLoadError('');
    }).catch(reason=>{if(!controller.signal.aborted)setLoadError(personalError(reason));});
    return()=>controller.abort();
  },[active,retry]);
  useEffect(()=>()=>{saveController.current?.abort();sourceController.current?.abort();},[]);

  const markChanged=()=>{readController.current?.abort();dirtyRef.current=true;setDirty(true);setNotice('');setError('');};
  const changeRow=(key:number,field:keyof ManualPositionDraft,value:string)=>{
    setRows(current=>current.map(row=>row.key===key?{...row,[field]:value}:row));markChanged();
  };
  const discard=()=>{
    if(!savedAccount)return;
    readController.current?.abort();
    setCash(String(savedAccount.cash));setRows(savedAccount.positions.map(position=>({...manualDraft(position),key:++sequence.current})));
    dirtyRef.current=false;setDirty(false);setError('');setNotice('已恢复上次保存的内容。');
  };
  const changeSource=async(source:PersonalSettings['accountSource'],showPlan=false)=>{
    if(!settings||sourceBusy||busy||dirtyRef.current)return;
    if(source==='manual'&&!updatedAt){setError('请先填写并保存手工账户，再用于生成计划。');return;}
    if(source===settings.accountSource&&!showPlan)return;
    readController.current?.abort();
    sourceController.current?.abort();const controller=new AbortController();sourceController.current=controller;
    setSourceBusy(true);setError('');setNotice('');
    try{
      const latest=await personalRequest<PersonalSettings>('/api/personal-settings',{signal:controller.signal});
      const saved=latest.accountSource===source?latest:await personalRequest<PersonalSettings>('/api/personal-settings',{method:'PUT',body:JSON.stringify({...latest,accountSource:source}),signal:controller.signal});
      if(controller.signal.aborted)return;
      setSettings(saved);setNotice(`当前计划已使用${source==='manual'?'手工账户':'IBKR 快照'}。`);onChanged();
      if(showPlan)onShowPlan?.();
    }catch(reason){if(!controller.signal.aborted)setError(personalError(reason));}
    finally{if(!controller.signal.aborted)setSourceBusy(false);}
  };
  const save=async()=>{
    readController.current?.abort();
    setError('');setNotice('');
    let payload:Omit<ManualAccount,'updatedAt'>;
    try{payload=parseManualDraft(cash,rows);}catch(reason){setError(personalError(reason));return;}
    saveController.current?.abort();const controller=new AbortController();saveController.current=controller;setBusy(true);
    try{
      const account=await personalRequest<ManualAccount>('/api/manual-account',{method:'PUT',body:JSON.stringify(payload),signal:controller.signal});
      if(controller.signal.aborted)return;
      if(typeof account.cash!=='number'||!Number.isFinite(account.cash)||!Array.isArray(account.positions))throw new Error('手工账户保存响应格式异常，输入已保留，请核对后重试。');
      const nextRows=account.positions.map(position=>({...manualDraft(position),key:++sequence.current}));
      setCash(String(account.cash));setRows(nextRows);setUpdatedAt(account.updatedAt);setSavedAccount(account);dirtyRef.current=false;setDirty(false);
      setNotice(settings?.accountSource==='manual'?'已保存，可以查看更新后的计划。':'已保存手工账户。点击“使用此账户生成计划”后生效。');onChanged();
    }catch(reason){if(!controller.signal.aborted)setError(personalError(reason));}
    finally{if(!controller.signal.aborted)setBusy(false);}
  };

  const sourceManual=settings?.accountSource==='manual';
  const validRows=rows.filter(row=>/^[A-Z][A-Z0-9-]{0,14}$/.test(row.symbol.trim().toUpperCase())&&Number.isFinite(Number(row.quantity))&&Number(row.quantity)>0&&Number.isFinite(Number(row.averageCost))&&Number(row.averageCost)>0).length;
  const missingStops=rows.filter(row=>row.symbol.trim()&&!row.stopPrice.trim()).length;
  const unknownSectors=rows.filter(row=>row.symbol.trim()&&row.sector==='unknown').length;
  const nextDisabled=busy||sourceBusy||dirty||!updatedAt;

  if(!ready)return <section className="pp-panel pp-empty" aria-busy={!loadError}>{loadError?<><p role="alert">{loadError}</p><button className="pp-button" onClick={()=>setRetry(value=>value+1)}>重新读取账户</button></>:<p role="status">正在读取账户来源与手工持仓…</p>}</section>;
  return <div className="personal-workspace account-flow">
    <section className="pp-panel af-source" aria-labelledby="pp-source-heading">
      <div className="af-source-top"><div><span className="af-eyebrow">PLAN ACCOUNT</span><h2 id="pp-source-heading">计划使用哪个账户</h2></div><div className="af-source-switch" role="group" aria-label="计划账户来源"><button className={!sourceManual?'selected':''} aria-pressed={!sourceManual} disabled={sourceBusy||busy||dirty} onClick={()=>void changeSource('broker')}><Database size={17}/>IBKR 快照{!sourceManual&&<Check size={15}/>}</button><button className={sourceManual?'selected':''} aria-pressed={sourceManual} disabled={sourceBusy||busy||dirty||!updatedAt} title={!updatedAt?'先在下方保存手工账户':undefined} onClick={()=>void changeSource('manual')}><Wallet size={17}/>手工账户{sourceManual&&<Check size={15}/>}</button></div></div>
      <div className="af-source-bottom"><p>{sourceManual?'正在使用下方已保存的现金与持仓。成交后请更新，行情每天重估。':'正在使用 IBKR 人工同步快照。下方可单独填写手工账户，保存后再选择使用。'}</p><span><ShieldCheck size={14}/>两份账户独立保存</span></div>
      {dirty&&<p className="af-source-hint">有未保存修改，先保存或撤销后再切换账户。</p>}
    </section>
    {(error||loadError)&&<div className="pp-alert" role="alert">{error||loadError} <span>当前输入已保留。</span>{loadError&&!dirty&&<button className="pp-link" onClick={()=>setRetry(value=>value+1)}>重试读取</button>}</div>}
    {notice&&<div className="pp-notice af-notice" role="status"><Check size={17}/>{notice}</div>}
    <section className="pp-panel af-editor" aria-labelledby="pp-manual-heading">
      <div className="af-editor-head"><div><h2 id="pp-manual-heading">手工现金与持仓</h2><p>填写当前账户，保存后用于计算资金和风险。</p></div><span className={`af-save-state ${dirty?'is-dirty':''}`}><i/>{dirty?'有未保存修改':updatedAt?`已保存 · ${formatTime(updatedAt)}`:'尚未保存'}</span></div>
      <fieldset className="pp-manual-fields" disabled={busy||sourceBusy}>
        <div className="af-cash-step"><div className="af-step"><span>01</span><div><h3>填写现金余额</h3><p>仅美元账户；负值表示借款，会限制新增仓位。</p></div></div><label className="af-cash-input"><span>美元现金 · USD</span><input aria-label="手工美元现金" type={hidden?'password':'text'} inputMode="decimal" value={cash} onChange={event=>{setCash(event.target.value);markChanged();}} autoComplete="off"/></label></div>
        <div className="af-holdings-head"><div className="af-step"><span>02</span><div><h3>核对持仓明细 <small>{rows.length} 项</small></h3><p>必填代码、股数、平均成本；其他字段可稍后补充。</p></div></div><button className="pp-button" onClick={()=>{setRows(current=>[...current,blankRow(++sequence.current)]);markChanged();}}><Plus size={16}/>添加持仓</button></div>
        <div className="pp-table-scroll"><table className="pp-table pp-editor"><thead><tr><th>股票代码</th><th>持有股数</th><th>平均成本 · $</th><th><span className="af-th-help" title="可留空。优先使用有效日线；缺少行情时才采用你填写的估值价。">估值备用价 · $ <CircleHelp size={13}/></span><small>选填</small></th><th><span className="af-th-help" title="你计划退出的位置，用于估算止损风险，不代表券商已挂出止损委托。">计划止损 · $ <CircleHelp size={13}/></span><small>选填</small></th><th>行业归属</th><th><span className="pp-sr-only">删除</span></th></tr></thead><tbody>{rows.map((row,index)=><tr key={row.key}><td><input aria-label={`第 ${index+1} 行股票代码`} value={row.symbol} onChange={event=>changeRow(row.key,'symbol',event.target.value)} placeholder="如 AAPL" autoCapitalize="characters" spellCheck={false}/></td>{(['quantity','averageCost','markPrice','stopPrice'] as const).map(field=><td key={field}><input aria-label={`第 ${index+1} 行${({quantity:'股数',averageCost:'平均成本',markPrice:'手工估值价',stopPrice:'计划止损价'})[field]}`} type={hidden?'password':'text'} inputMode="decimal" value={row[field]} onChange={event=>changeRow(row.key,field,event.target.value)} placeholder={field==='markPrice'?'优先日线':field==='stopPrice'?'待补充':''} autoComplete="off"/></td>)}<td><select aria-label={`第 ${index+1} 行行业`} value={row.sector} onChange={event=>changeRow(row.key,'sector',event.target.value)}><option value="unknown">自动核验</option><option value="semiconductor">半导体</option><option value="other">其他行业</option></select></td><td><button className="pp-icon-button" aria-label={`删除第 ${index+1} 行 ${row.symbol||'持仓'}`} onClick={()=>{setRows(current=>current.filter(value=>value.key!==row.key));markChanged();}}><Trash2 size={17}/></button></td></tr>)}</tbody></table>{rows.length===0&&<div className="af-empty"><Wallet size={23}/><p>还没有填写持仓</p><span>可以保存为纯现金账户，或点击“添加持仓”。</span></div>}</div>
        {rows.length>0&&<div className="af-completion"><span><Check size={14}/>{validRows} / {rows.length} 项已填基础信息</span>{missingStops>0&&<span>{missingStops} 项未填止损</span>}{unknownSectors>0&&<span>{unknownSectors} 项行业使用公开资料核验</span>}<small>未手填行业时使用已核实的公开资料；缺少止损或基金行业敞口仍会限制新仓。</small></div>}
        <div className="af-save-bar"><span>{dirty?'修改仅在保存后生效。':'保存手工账户不会改变 IBKR 快照。'}</span><div>{dirty&&<button className="pp-button af-reset" onClick={discard}><Undo2 size={15}/>撤销修改</button>}<button className="pp-button pp-primary" onClick={()=>void save()}><Save size={16}/>{busy?'保存中…':'保存手工账户'}</button>{onShowPlan&&<button className="pp-button af-next" disabled={nextDisabled} title={dirty?'先保存当前修改':!updatedAt?'先保存手工账户':undefined} onClick={()=>void changeSource('manual',true)}>{sourceBusy?'正在切换…':sourceManual?'查看我的计划':'使用此账户生成计划'}<ArrowRight size={16}/></button>}</div></div>
      </fieldset>
    </section>
    <details className="af-guide"><summary><CircleHelp size={15}/>怎么填写，以及保存后做什么</summary><div className="af-guide-grid"><section><h4>保存 → 设置策略 → 查看计划</h4><p>保存后点击“查看我的计划”，调整账户投入比例、策略与风险目标，再看每只股票的条件说明。软件只生成计划，不会下单。</p></section><section><h4>估值与止损的区别</h4><p>估值备用价只在日线不可用时使用；无法估值会暂停数量计算。计划止损用于估算风险，留空保持未知，不视为零，也不代表券商已有止损单。</p></section><section><h4>成交后记得更新</h4><p>每天更新行情不会改变现金、股数和成本。手工账户假设填写后未发生其他交易；目前支持美元多头，暂不支持空头及非美元持仓。</p></section></div></details>
  </div>;
}
