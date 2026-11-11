"use client";

import { useEffect, useState } from "react";
import { Activity, ArrowRight, CheckCircle2, CircleHelp, FlaskConical, LoaderCircle, RefreshCw, SlidersHorizontal } from "lucide-react";
import "./strategy-lab.css";
import {useClientReady} from "./use-client-ready";
import {readTargetPreferences,TARGET_PREFERENCES_KEY} from "./strategy-preferences";

type Metrics = {
  startDate?: string; endDate?: string; annualReturnPct: number | null;
  totalReturnPct: number | null; maxDrawdownPct: number | null; meetsTargets?: boolean;
  closedTradeCount?: number; winRatePct?: number | null;
};
type Study = {
  targets: { annualReturnPct: number; maxDrawdownPct: number };
  parameters: { lookback: number; holdingDays: number; exposurePct: number; symbols: string[]; feeBps: number; slippageBps: number };
  training: Metrics; validation: Metrics; benchmark: Metrics;
  equityCurve: { date: string; strategy: number; benchmark: number }[];
  candidateCount: number; warnings: string[]; conclusion: string; asOf: string;
};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const format = (value: number | null | undefined, digits = 1) => finite(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits }) : "—";
const percent = (value: number | null | undefined) => finite(value) ? `${format(value)}%` : "—";
const signed = (value: number | null | undefined) => finite(value) ? `${value > 0 ? "+" : ""}${format(value)}%` : "—";

function ValidationCurve({ study }: { study: Study }) {
  const points = study.equityCurve;
  const [cursor, setCursor] = useState(points.length - 1);
  const index = Math.min(Math.max(cursor, 0), points.length - 1);
  const point = points[index];
  if (!point) return null;
  const values = points.flatMap(p => [p.strategy, p.benchmark]);
  const min = Math.min(...values), max = Math.max(...values), padding = Math.max((max - min) * .12, max * .01, 1);
  const low = Math.max(0, min - padding), high = max + padding;
  const width = 960, height = 245, left = 78, right = 24, top = 18, bottom = 33;
  const x = (i: number) => left + i / Math.max(points.length - 1, 1) * (width - left - right);
  const y = (value: number) => top + (high - value) / (high - low) * (height - top - bottom);
  const path = (key: "strategy" | "benchmark") => points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(p[key]).toFixed(2)}`).join(" ");
  return <div className="sl-curve">
    <div className="sl-subhead"><h3><Activity size={17} />后段历史验证 · 净值曲线</h3><div className="sl-legend"><span><i />所选策略</span><span><i />买入持有</span></div></div>
    <div className="sl-readout"><time>{point.date}</time><strong>策略 {format(point.strategy, 2)}</strong><span>基准 {format(point.benchmark, 2)}</span></div>
    <div className="sl-chart-scroll"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="所选策略与同窗买入持有的历史验证净值曲线">
      <desc>{points[0].date} 至 {points[points.length - 1].date}。紫色为所选策略，蓝色为买入持有；选择策略时没有使用这一时段的结果。</desc>
      {[0, 1, 2, 3].map(i => { const value = low + (high - low) * i / 3; return <g key={i}><line className="sl-chart-grid" x1={left} x2={width - right} y1={y(value)} y2={y(value)} /><text x={left - 11} y={y(value) + 4} textAnchor="end">{format(value, 0)}</text></g>; })}
      <path className="sl-path sl-path-benchmark" d={path("benchmark")} /><path className="sl-path" d={path("strategy")} />
      <line className="sl-chart-cursor" x1={x(index)} x2={x(index)} y1={top} y2={height - bottom} /><circle cx={x(index)} cy={y(point.strategy)} r="4" className="sl-chart-dot" />
      <text x={left} y={height - 8}>{points[0].date}</text><text x={width - right} y={height - 8} textAnchor="end">{points[points.length - 1].date}</text>
    </svg></div>
    <label className="sl-scrubber"><span>逐日查看</span><input aria-label="策略验证净值日期" type="range" min={0} max={points.length - 1} value={index} onChange={event => setCursor(Number(event.target.value))} /><time>{point.date}</time></label>
    <p className="sl-note">两者在同一验证时段从独立初始资金开始，均计入费用。基准全额买入持有，策略按筛选出的资金比例运行，剩余初始资金不计利息。</p>
  </div>;
}

export function StrategyLab({ marketStamp }: { marketStamp: string }) {
  const ready = useClientReady();
  return ready ? <StrategyLabContent marketStamp={marketStamp}/> : <div className="workspace-loading" role="status">正在载入研究偏好…</div>;
}

function StrategyLabContent({ marketStamp }: { marketStamp: string }) {
  const [initial] = useState(readTargetPreferences);
  const [returnTarget, setReturnTarget] = useState(initial.returnTarget);
  const [drawdownTarget, setDrawdownTarget] = useState(initial.drawdownTarget);
  const restored = true;
  const [retry, setRetry] = useState(0);
  const [stored, setStored] = useState<{ key: string; study: Study } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const annual = returnTarget.trim() === "" ? NaN : Number(returnTarget);
  const drawdown = drawdownTarget.trim() === "" ? NaN : Number(drawdownTarget);
  const valid = finite(annual) && annual >= 0 && annual <= 200 && finite(drawdown) && drawdown >= 1 && drawdown <= 80;
  const requestKey = JSON.stringify([returnTarget, drawdownTarget, marketStamp, retry]);
  const result = stored?.key === requestKey ? stored.study : null;
  const error = failure?.key === requestKey ? failure.message : "";
  const loading = (!restored || valid) && !result && !error;

  useEffect(() => {
    if (!restored || !valid) return;
    try { localStorage.setItem(TARGET_PREFERENCES_KEY, JSON.stringify({ targetReturnPct: annual, maxDrawdownPct: drawdown })); } catch { /* Keep the active controls usable. */ }
  }, [annual, drawdown, valid, restored]);

  useEffect(() => {
    if (!restored || !valid) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch("/api/strategy-study", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ targetReturnPct: annual, maxDrawdownPct: drawdown }), signal: controller.signal });
        const payload: unknown = await response.json();
        const data = (payload && typeof payload === "object" ? payload : {}) as Partial<Study> & { error?: unknown; detail?: unknown };
        if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : typeof data.detail === "string" ? data.detail : "策略研究暂时无法完成，请稍后重试。");
        if (!data.parameters || !data.training || !data.validation || !data.benchmark || !data.targets ||
          !Array.isArray(data.equityCurve) || data.equityCurve.length === 0 ||
          data.equityCurve.some(point => !finite(point.strategy) || !finite(point.benchmark)) ||
          data.targets.annualReturnPct !== annual || data.targets.maxDrawdownPct !== drawdown) throw new Error("未收到与当前目标匹配的完整结果，请重新计算。");
        if (!controller.signal.aborted) setStored({ key: requestKey, study: data as Study });
      } catch (reason) {
        if (!controller.signal.aborted) setFailure({ key: requestKey, message: reason instanceof Error ? reason.message : "无法连接策略研究服务。" });
      }
    }, 600);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [annual, drawdown, valid, requestKey, restored]);

  return <section className="strategy-lab panel" aria-labelledby="strategy-lab-title">
    <div className="sl-head"><div><div className="sl-eyebrow"><FlaskConical size={15} />量化策略实验室</div><h2 id="strategy-lab-title">先设目标，再检验策略</h2><p>调整收益目标与回撤容忍度，自动重新筛选规则和初始资金比例。</p></div><span className="sl-mode">研究模式 · 未连接自动下单</span></div>
    <div className="sl-targets">
      <div className="sl-target sl-target-return"><label htmlFor="strategy-return-target">目标年化收益<span>研究目标</span></label><div className="sl-target-value"><input id="strategy-return-target" type="number" min={0} max={200} step={1} value={returnTarget} onChange={event => setReturnTarget(event.target.value)} /><b>%</b></div><input aria-label="调整目标年化收益" type="range" min={0} max={200} step={1} value={finite(annual) ? Math.min(200, Math.max(0, annual)) : 0} onChange={event => setReturnTarget(event.target.value)} /><div className="sl-range-labels"><span>0%</span><span>200%</span></div></div>
      <div className="sl-target sl-target-risk"><label htmlFor="strategy-drawdown-target">可接受最大回撤<span>研究约束</span></label><div className="sl-target-value"><input id="strategy-drawdown-target" type="number" min={1} max={80} step={1} value={drawdownTarget} onChange={event => setDrawdownTarget(event.target.value)} /><b>%</b></div><input aria-label="调整可接受最大回撤" type="range" min={1} max={80} step={1} value={finite(drawdown) ? Math.min(80, Math.max(1, drawdown)) : 1} onChange={event => setDrawdownTarget(event.target.value)} /><div className="sl-range-labels"><span>1%</span><span>80%</span></div></div>
      <div className="sl-target-guide"><SlidersHorizontal size={22} /><strong>参数会重选，收益不能预定</strong><p>使用前段历史选择突破周期、持有天数和资金比例，再看后段历史是否达标。达不到目标时会如实显示。</p><span>目标回撤不是自动止损线，也不保证未来损失上限。</span></div>
    </div>
    {restored && !valid && <p className="sl-error" role="alert">请输入 0–200% 的年化收益目标，以及 1–80% 的回撤容忍度。</p>}
    {loading && <div className="sl-loading" role="status"><LoaderCircle size={20} className="spin" /><span>正在按当前目标筛选参数并计算历史验证…</span></div>}
    {error && <div className="sl-error" role="alert"><div><strong>本次策略研究未完成</strong><p>{error}</p></div><button onClick={() => setRetry(value => value + 1)}><RefreshCw size={15} />重新计算</button></div>}
    {result && <div className="sl-results">
      <div className={`sl-verdict ${result.training.meetsTargets && result.validation.meetsTargets ? "sl-verdict-met" : ""}`} role="status">{result.training.meetsTargets && result.validation.meetsTargets ? <CheckCircle2 size={21} /> : <CircleHelp size={21} />}<div><strong>{result.training.meetsTargets && result.validation.meetsTargets ? "前后两段历史均达到目标 · 仍需模拟验证" : "未通过前后两段历史目标检验"}</strong><p>{result.conclusion}</p></div></div>
      <div className="sl-rule"><div><span>前段历史选出的规则</span><h3>{result.parameters.lookback} 日突破 <ArrowRight size={18} />持有 {result.parameters.holdingDays} 个交易日</h3><p>收盘确认后，下一交易日开盘入场。各股票独立执行，不重叠持仓。</p></div><div className="sl-allocation"><span>初始策略资金</span><strong>{percent(result.parameters.exposurePct)}</strong><small>其余初始资金保留现金 · 无杠杆</small></div></div>
      <div className="sl-universe"><span>{result.parameters.symbols.join(" · ")}</span><span>等额分配至各股票 · {result.candidateCount} 组候选参数</span><span>单边手续费 {format(result.parameters.feeBps)} bps + 滑点 {format(result.parameters.slippageBps)} bps</span></div>
      <div className="sl-metrics">
        <div><span>验证期收益折算年化</span><strong className={finite(result.validation.annualReturnPct) && result.validation.annualReturnPct < 0 ? "sl-negative" : ""}>{signed(result.validation.annualReturnPct)}</strong><small>目标 {percent(result.targets.annualReturnPct)} · 按 252 交易日折算，非未来预测</small></div>
        <div><span>验证期最大回撤</span><strong>{percent(result.validation.maxDrawdownPct)}</strong><small>容忍度 {percent(result.targets.maxDrawdownPct)}</small></div>
        <div><span>验证期累计收益</span><strong className={finite(result.validation.totalReturnPct) && result.validation.totalReturnPct < 0 ? "sl-negative" : ""}>{signed(result.validation.totalReturnPct)}</strong><small>{result.validation.startDate} — {result.validation.endDate}</small></div>
      </div>
      <div className="sl-comparison-scroll"><table aria-label="策略训练与历史验证结果"><thead><tr><th>评价区间</th><th>年化收益</th><th>累计收益</th><th>最大回撤</th><th>两个目标均满足</th></tr></thead><tbody>
        <tr><td><b>前段训练 · 用于选参数</b><small>{result.training.startDate} — {result.training.endDate}</small></td><td>{signed(result.training.annualReturnPct)}</td><td>{signed(result.training.totalReturnPct)}</td><td>{percent(result.training.maxDrawdownPct)}</td><td>{result.training.meetsTargets ? "是" : "否"}</td></tr>
        <tr className="sl-validation-row"><td><b>后段验证 · 所选策略</b><small>{result.validation.startDate} — {result.validation.endDate}</small></td><td>{signed(result.validation.annualReturnPct)}</td><td>{signed(result.validation.totalReturnPct)}</td><td>{percent(result.validation.maxDrawdownPct)}</td><td>{result.validation.meetsTargets ? "是" : "否"}</td></tr>
        <tr><td><b>同窗基准 · 全额买入持有</b><small>同一股票组合 · 不参与参数选择</small></td><td>{signed(result.benchmark.annualReturnPct)}</td><td>{signed(result.benchmark.totalReturnPct)}</td><td>{percent(result.benchmark.maxDrawdownPct)}</td><td>{finite(result.benchmark.annualReturnPct) && finite(result.benchmark.maxDrawdownPct) ? result.benchmark.annualReturnPct >= result.targets.annualReturnPct && result.benchmark.maxDrawdownPct <= result.targets.maxDrawdownPct ? "是" : "否" : "—"}</td></tr>
      </tbody></table></div>
      {finite(result.validation.closedTradeCount) && <p className="sl-trades">验证期已平仓 {result.validation.closedTradeCount} 笔{finite(result.validation.winRatePct) ? ` · 逐笔胜率 ${percent(result.validation.winRatePct)}` : ""}。未完成持仓按期末市值计入净值。</p>}
      <ValidationCurve key={requestKey} study={result} />
      <details className="sl-method"><summary>研究口径与局限</summary><p>收益目标用于筛选候选规则；没有合适的组合时，显示最接近目标的研究候选，而不是承诺能达标。调整目标会重新选择参数，后段验证结果不参与本次选择。</p><ul>{(result.warnings || []).map((warning, index) => <li key={index}>{warning}</li>)}</ul><p>历史验证不等于未来收益。频繁依据验证结果修改目标，也可能造成过拟合。此处未连接自动下单。</p></details>
    </div>}
  </section>;
}
