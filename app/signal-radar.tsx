"use client";

import { ChevronRight, Radar } from "lucide-react";
import type { Stock } from "./dashboard-types";
import { color, money, pct } from "./dashboard-format";
import { Spark } from "./market-chart";
import "./signal-radar.css";

const labels: Record<string, string> = { strong: "趋势走强", recovering: "修复观察", weak: "趋势偏弱" };

export function SignalRadar({ stocks, groupNames, onSelect }: {
  stocks: Stock[];
  groupNames: (symbol: string) => string;
  onSelect: (symbol: string) => void;
}) {
  return <div className="signal-radar" aria-label="信号雷达卡片">
    {stocks.map(stock => {
      const groups = groupNames(stock.symbol);
      const metadata = `${stock.exchange || "交易所未提供"} · USD${groups ? ` · ${groups}` : ""}`;
      return <article className="radar-card" key={stock.symbol}>
        <button className="radar-main" aria-label={`查看 ${stock.symbol} 走势和指标`} onClick={() => onSelect(stock.symbol)}>
          <span className="radar-heading"><strong>{stock.symbol}</strong><span className={`pill ${stock.status}`}>{labels[stock.status]}</span><ChevronRight size={15}/></span>
          <span className="radar-company" title={stock.name}>{stock.name}</span>
          <span className="radar-meta" title={metadata}>{metadata}</span>
          {stock.quoteStatus === "stale" && <span className="radar-stale" title={stock.quoteError || "本轮未取得新行情"}>缓存行情 · {stock.asOf}</span>}
          <span className="radar-price"><strong>{money(stock.close)}</strong><span className={color(stock.changePct)}>{pct(stock.changePct, 1)}</span><Spark stock={stock}/></span>
          <span className="radar-metrics"><span>20日 <b className={color(stock.ret20)}>{pct(stock.ret20)}</b></span><span>RSI <b>{stock.rsi === null ? "—" : stock.rsi.toFixed(1)}</b></span></span>
          <span className="radar-tags">{stock.signals.map(signal => <span className={signal.tone} key={signal.id}>{signal.label}</span>)}</span>
        </button>
        <details className="radar-evidence">
          <summary aria-label={`${stock.symbol} 的完整信号依据`}><span>信号依据 · {stock.signals.length} 条</span><time dateTime={stock.asOf}>{stock.asOf} 收盘</time></summary>
          <div className="radar-reasons">{stock.signals.map(signal => <div key={signal.id}><b>{signal.label}</b><p>{signal.description}</p></div>)}{!stock.signals.length && <p>当前没有额外规则信号。</p>}</div>
        </details>
      </article>;
    })}
    {!stocks.length && <div className="empty"><Radar/><h3>当前没有符合条件的信号</h3><p>筛选结果基于实际行情。</p></div>}
  </div>;
}
