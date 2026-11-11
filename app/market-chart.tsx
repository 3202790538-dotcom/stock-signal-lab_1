"use client";

import { memo, useId, useMemo, useState } from "react";
import { color, money } from "./dashboard-format";
import type { Stock } from "./dashboard-types";
import { chartGeometry, sparkPath } from "./market-chart-model";

const windows = { "1M": 22, "3M": 66, "6M": 126, "1Y": 252 } as const;
const averages = [["ma200", "#af97d8"], ["ma50", "#75a8f0"], ["ma20", "#d7b66f"]] as const;
type Range = keyof typeof windows;

export const Spark = memo(function Spark({ stock }: { stock: Stock }) {
  return <svg viewBox="0 0 100 30" className={`spark ${color(stock.ret20)}`} aria-hidden="true"><path fill="none" stroke="currentColor" strokeWidth="1.6" d={sparkPath(stock.history)} /></svg>;
});

export function Chart({ stock }: { stock: Stock }) {
  const [range, setRange] = useState<Range>("3M");
  const [showAverages, setShowAverages] = useState(true);
  const [hover, setHover] = useState<number | null>(null);
  const gradient = useId();
  const data = useMemo(() => stock.history.slice(-windows[range]), [stock.history, range]);
  // Pointer movement only updates the readout/cursor, leaving line generation cached.
  const geometry = useMemo(() => chartGeometry(data, showAverages), [data, showAverages]);
  const { x, y, low, high, paths, tickIndices, volumeMax } = geometry;
  const index = Math.max(0, Math.min(hover ?? data.length - 1, data.length - 1));
  const active = data[index];
  if (!active) return <div className="chart"><p className="chart-note">暂无有效日线，请刷新行情后查看走势。</p></div>;

  return <div className="chart">
    <div className="chart-toolbar">
      <div className="legend"><span><i className="mint" />收盘价</span>{showAverages && <><span><i className="gold" />MA20</span><span><i className="blue" />MA50</span>{stock.ma200 !== null && <span><i className="purple" />MA200</span>}</>}</div>
      <div className="ranges">{(Object.keys(windows) as Range[]).map(value => <button key={value} aria-pressed={value === range} className={value === range ? "active" : ""} onClick={() => { setRange(value); setHover(null); }}>{value}</button>)}<button aria-pressed={showAverages} className={showAverages ? "active" : ""} onClick={() => setShowAverages(!showAverages)}>均线</button></div>
    </div>
    <div className="readout"><span>{active.date}</span><span>收 {money(active.close)}</span><span>高 {money(active.high)}</span><span>低 {money(active.low)}</span></div>
    <svg viewBox="0 0 900 325" className="price-chart" role="img" tabIndex={0} aria-label={`${stock.symbol} ${range}价格走势图，左右方向键查看每日行情`} onPointerMove={event => {
      const rect = event.currentTarget.getBoundingClientRect();
      const pointerX = (event.clientX - rect.left) / rect.width * 900;
      setHover(Math.max(0, Math.min(data.length - 1, Math.round((pointerX - 10) / 810 * (data.length - 1)))));
    }} onPointerLeave={() => setHover(null)} onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      setHover(event.key === "Home" ? 0 : event.key === "End" ? data.length - 1 : Math.max(0, Math.min(data.length - 1, index + (event.key === "ArrowLeft" ? -1 : 1))));
    }}>
      <defs><linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#a68cce" stopOpacity=".18" /><stop offset="100%" stopColor="#a68cce" stopOpacity="0" /></linearGradient></defs>
      {[0, 1, 2, 3, 4].map(tick => {
        const value = high - (high - low) * tick / 4;
        return <g key={tick}><line x1="10" x2="825" y1={y(value)} y2={y(value)} stroke="#cbd7e1" strokeDasharray="3 5" /><text x="838" y={y(value) + 4} fill="var(--muted)" fontSize="14">{value.toFixed(2)}</text></g>;
      })}
      <path d={`${paths.close} L${x(data.length - 1)},275 L10,275 Z`} fill={`url(#${gradient})`} />
      {showAverages && averages.map(([key, stroke]) => <path key={key} d={paths[key]} fill="none" stroke={stroke} strokeWidth="1.6" />)}
      <path d={paths.close} fill="none" stroke="#7860ab" strokeWidth="2.5" />
      {tickIndices.map(tick => <text key={tick} x={x(tick)} y="310" fill="var(--muted)" fontSize="14" textAnchor={tick === 0 ? "start" : tick === data.length - 1 ? "end" : "middle"}>{data[tick].date.slice(5)}</text>)}
      {(hover !== null || data.length === 1) && <g><line x1={x(index)} x2={x(index)} y1="20" y2="275" stroke="#7e9a8f" strokeDasharray="4 4" /><circle cx={x(index)} cy={y(active.close)} r="4" fill="#65dcbc" stroke="#162b24" strokeWidth="3" /></g>}
    </svg>
    <div className="volume-label">成交量 <span>{active.volume === null ? "—" : (active.volume / 1e6).toFixed(2) + "M"}</span></div>
    <svg viewBox="0 0 900 48" className="volume-chart" role="img" aria-label="成交量柱状图">{data.map((bar, tick) => bar.volume === null ? null : <rect key={bar.date} x={x(tick)} y={46 - bar.volume / volumeMax * 42} width={Math.max(2, 810 / data.length * .65)} height={bar.volume / volumeMax * 42} fill={bar.close >= bar.open ? "#86b5aa" : "#dd9e9c"} rx="1" />)}</svg>
  </div>;
}
