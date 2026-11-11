"use client";
import {useState} from "react";
import {CompleteStrategy} from "./complete-strategy";
import {StrategyLab} from "./strategy-lab";
import {BacktestPanel} from "./backtest-panel";

export type BacktestMode = "complete" | "strategy" | "single";

export function BacktestWorkspace({symbols,selected,onSelect,marketStamp,initialMode="complete",marketIssue=""}:{symbols:string[];selected:string;onSelect:(symbol:string)=>void;marketStamp:string;initialMode?:BacktestMode;marketIssue?:string}){
 const [mode,setMode]=useState<BacktestMode>(initialMode);
 return <div>
  <div className="workspace-backtest-navigation"><div className="strategy-switch" role="group" aria-label="回测模式">
   <button aria-pressed={mode==="complete"} onClick={()=>setMode("complete")}>完整策略</button>
   <button aria-pressed={mode==="single"} onClick={()=>setMode("single")}>单股波段回测</button>
  </div><details className="workspace-advanced"><summary>进阶研究{mode==="strategy"?" · 当前为参数实验":""}</summary><p>比较历史参数组合，不直接生成真实账户订单。</p><button aria-pressed={mode==="strategy"} onClick={()=>setMode("strategy")}>目标参数实验</button></details></div>
  {mode==="complete"?<CompleteStrategy marketStamp={marketStamp} marketIssue={marketIssue}/>:mode==="strategy"?<StrategyLab marketStamp={marketStamp}/>:<BacktestPanel marketStamp={marketStamp} symbols={symbols} selected={selected} onSelect={onSelect}/>}
 </div>;
}
