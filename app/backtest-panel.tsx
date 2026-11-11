"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Activity, ArrowUpRight, ChevronDown, Clock3, FlaskConical, LoaderCircle, Play } from "lucide-react";
import "./backtest-panel.css";

type Fields = { lookback: string; holdingDays: string; feeBps: string; slippageBps: string; initialCapital: string; startDate: string; endDate: string };
type RunRequest = { symbol: string; lookback: number; holdingDays: number; feeBps: number; slippageBps: number; initialCapital: number; startDate?: string; endDate?: string };
type Trade = {
  signalDate: string; entryDate: string; entryPrice: number; entryReferencePrice: number;
  quantity: number; entryCapital: number; entryFee: number; entrySlippage: number;
  exitDate: string; exitPrice: number; exitReferencePrice: number; exitFee: number;
  exitSlippage: number; netPnl: number; returnPct: number; totalCosts: number; sessions: number;
};
type OpenPosition = Pick<Trade, "signalDate" | "entryDate" | "entryPrice" | "quantity" | "entryFee" | "entrySlippage" | "returnPct" | "totalCosts" | "sessions"> & {
  markDate: string; markPrice: number; marketValue: number; unrealizedPnl: number; remainingSessions: number;
};
type Point = { date: string; equity: number; benchmark: number };
type Result = {
  symbol?: string; currency?: string; parameters: Omit<RunRequest, "symbol">;
  sample: { startDate: string; endDate: string; bars: number; warmupBars: number; historyStartDate: string; historyEndDate: string };
  metrics: {
    totalReturnPct: number; benchmarkReturnPct: number; excessReturnPct: number;
    maxDrawdownPct: number; closedTradeCount: number; wins: number; losses: number;
    breakeven: number; winRatePct: number | null; payoffRatio: number | null;
    profitFactor: number | null; exposureFraction: number; finalEquity: number; totalCosts: number;
  };
  curve: Point[]; trades: Trade[]; openPosition: OpenPosition | null;
  rules: string[]; warnings: string[];
};
const defaults: Fields = { lookback: "20", holdingDays: "10", feeBps: "5", slippageBps: "5", initialCapital: "10000", startDate: "", endDate: "" };
const number = (value: number | null, digits = 2) => value === null || !Number.isFinite(value) ? "—" : value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
const pct = (value: number | null) => value === null ? "—" : `${value > 0 ? "+" : ""}${number(value)}%`;
const tone = (value: number) => value > 0 ? "up" : value < 0 ? "down" : "";
const requestFor = (symbol: string, fields: Fields): RunRequest => ({
  symbol, lookback: Number(fields.lookback), holdingDays: Number(fields.holdingDays),
  feeBps: Number(fields.feeBps), slippageBps: Number(fields.slippageBps), initialCapital: Number(fields.initialCapital),
  ...(fields.startDate ? { startDate: fields.startDate } : {}), ...(fields.endDate ? { endDate: fields.endDate } : {}),
});

function EquityCurve({ result, amount }: { result: Result; amount: (value: number, digits?: number) => string }) {
  const [cursor, setCursor] = useState(result.curve.length - 1);
  const points = result.curve;
  const index = Math.min(Math.max(cursor, 0), points.length - 1);
  const point = points[index];
  const values = [result.parameters.initialCapital, ...points.flatMap(p => [p.equity, p.benchmark])];
  const low = Math.min(...values), high = Math.max(...values);
  const pad = Math.max((high - low) * .13, high * .01, .01);
  const floor = Math.max(0, low - pad), ceiling = high + pad;
  const width = 1000, height = 245, left = 76, right = 21, top = 20, bottom = 31;
  const x = (i: number) => left + i / Math.max(points.length - 1, 1) * (width - left - right);
  const y = (value: number) => top + (ceiling - value) / (ceiling - floor) * (height - top - bottom);
  const path = (key: "equity" | "benchmark") => points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(p[key]).toFixed(2)}`).join(" ");
  return <section className="panel bt-curve">
    <div className="panel-head"><h2><Activity size={16} />资金曲线</h2><div className="bt-legend"><span><i />突破策略</span><span><i />买入持有</span></div></div>
    <div className="bt-readout"><span>{point.date}</span><strong>策略 {amount(point.equity)}</strong><span>基准 {amount(point.benchmark)}</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="每日收盘净值，紫色为突破策略，蓝色为买入持有基准" className="bt-svg">
      <desc>样本 {result.sample.startDate} 至 {result.sample.endDate}。策略期末净值 {amount(result.metrics.finalEquity)}。可使用下方滑块逐日查看。</desc>
      {[0, 1, 2, 3].map(i => {
        const value = floor + (ceiling - floor) * i / 3;
        return <g key={i}><line x1={left} x2={width - right} y1={y(value)} y2={y(value)} className="bt-gridline" /><text x={left - 10} y={y(value) + 4} textAnchor="end">{number(value, 0)}</text></g>;
      })}
      <line x1={left} x2={width - right} y1={y(result.parameters.initialCapital)} y2={y(result.parameters.initialCapital)} className="bt-baseline" />
      <path d={path("benchmark")} className="bt-benchmark-path" /><path d={path("equity")} className="bt-equity-path" />
      <line x1={x(index)} x2={x(index)} y1={top} y2={height - bottom} className="bt-cursor" />
      <circle cx={x(index)} cy={y(point.benchmark)} r={4} className="bt-benchmark-dot" /><circle cx={x(index)} cy={y(point.equity)} r={4.5} className="bt-equity-dot" />
      <text x={left} y={height - 8}>{points[0].date}</text><text x={width - right} y={height - 8} textAnchor="end">{points.length > 1 ? points[points.length - 1].date : ""}</text>
    </svg>
    <label className="bt-scrubber"><span>逐日查看</span><input aria-label="查看净值日" type="range" min={0} max={Math.max(0, points.length - 1)} value={index} onChange={e => setCursor(Number(e.target.value))} /><time>{point.date}</time></label>
    <p className="bt-chart-note">基准在样本首个可交易日开盘买入，最后收盘假设清仓；两条曲线采用相同手续费与滑点。策略未完成持仓按收盘市值保留。</p>
  </section>;
}

export function BacktestPanel({ symbols, selected, onSelect, marketStamp }: { symbols: string[]; selected: string; onSelect: (symbol: string) => void; marketStamp: string }) {
  const [fields, setFields] = useState<Fields>(defaults);
  const [runState, setRunState] = useState<{ request: RunRequest; marketStamp: string; result: Result | null; pending: boolean; error: string } | null>(null);
  // A new symbol or market snapshot invalidates results before the next effect runs.
  // Draft controls are deliberately independent, so refresh preserves edited dates/parameters.
  const currentRun = runState?.request.symbol === selected && runState.marketStamp === marketStamp ? runState : null;
  const result = currentRun?.result ?? null;
  const submitted = currentRun?.request ?? null;
  const loading = Boolean(selected) && (!currentRun || currentRun.pending);
  const error = currentRun?.error ?? "";
  const controller = useRef<AbortController | null>(null);
  const latestFields = useRef(fields);
  useEffect(() => { latestFields.current = fields; }, [fields]);
  const load = useCallback(async (body: RunRequest) => {
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    const current = { request: body, marketStamp, result: null, pending: true, error: "" };
    setRunState(current);
    try {
      const response = await fetch("/api/backtest", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: abort.signal });
      const payload: unknown = await response.json();
      const data = (payload && typeof payload === "object" ? payload : {}) as Partial<Result> & { error?: unknown; detail?: unknown };
      if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : typeof data.detail === "string" ? data.detail : "回测暂时无法完成，请检查参数或稍后重试。");
      if (!Array.isArray(data.curve) || data.curve.length === 0 || !data.metrics) throw new Error("回测未返回有效样本，请调整日期或回看天数。");
      if (data.symbol && data.symbol !== body.symbol) throw new Error("回测返回的标的与当前请求不一致，请重试。");
      if (!abort.signal.aborted) setRunState({ ...current, result: data as Result, pending: false });
    } catch (failure) {
      if (!abort.signal.aborted) setRunState({ ...current, pending: false, error: failure instanceof Error ? failure.message : "无法连接本机回测服务。" });
    }
  }, [marketStamp]);
  useEffect(() => {
    if (selected) void load(requestFor(selected, latestFields.current));
    return () => controller.current?.abort();
  }, [selected, load]);
  const update = (key: keyof Fields, value: string) => setFields(previous => ({ ...previous, [key]: value }));
  const run = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); void load(requestFor(selected, fields)); };
  const dirty = submitted && JSON.stringify(requestFor(selected, fields)) !== JSON.stringify(submitted);
  const currency = result?.currency || "USD";
  const amount = (value: number, digits = 0) => {
    try { return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value); }
    catch { return `${number(value, digits)} ${currency}`; }
  };
  const m = result?.metrics;
  return <div className="backtest-workspace">
    <section className="panel bt-settings">
      <div className="panel-head"><h2><FlaskConical size={17} />突破波段回测</h2><span className="bt-tag">日线 · 单只股票 · 只做多</span></div>
      <p className="bt-intro">用明确规则检验历史走势：收盘突破前 N 日最高价，次日开盘买入，持有固定交易日。</p>
      <form onSubmit={run} className="bt-form">
        <label className="bt-symbol">股票<select value={selected} onChange={e => onSelect(e.target.value)} required>{symbols.map(symbol => <option key={symbol} value={symbol}>{symbol}</option>)}</select></label>
        <label>突破回看<input type="number" min={5} max={120} step={1} required value={fields.lookback} onChange={e => update("lookback", e.target.value)} /><small>5–120 个交易日</small></label>
        <label>持有周期<input type="number" min={1} max={60} step={1} required value={fields.holdingDays} onChange={e => update("holdingDays", e.target.value)} /><small>入场日计为第 1 天</small></label>
        <label>单边手续费<input type="number" min={0} max={100} step="any" required value={fields.feeBps} onChange={e => update("feeBps", e.target.value)} /><small>bps · 1 bps = 0.01%</small></label>
        <label>单边滑点<input type="number" min={0} max={100} step="any" required value={fields.slippageBps} onChange={e => update("slippageBps", e.target.value)} /><small>bps · 每次买入和卖出</small></label>
        <label>初始资金<input type="number" min={0.01} max={1e12} step="any" required value={fields.initialCapital} onChange={e => update("initialCapital", e.target.value)} /><small>使用股票行情币种</small></label>
        <label>开始日期<input type="date" value={fields.startDate} max={fields.endDate || undefined} onChange={e => update("startDate", e.target.value)} /><small>可留空 · 使用此前数据预热</small></label>
        <label>结束日期<input type="date" value={fields.endDate} min={fields.startDate || undefined} onChange={e => update("endDate", e.target.value)} /><small>可留空 · 截至已有行情末日</small></label>
        <button className="bt-run" type="submit" disabled={loading || !selected}>{loading ? <LoaderCircle size={14} className="spin" /> : <Play size={13} />} {loading ? "计算中" : "运行回测"}</button>
      </form>
      <div className="bt-form-foot"><span>全额现金 · 无杠杆 · 不重叠持仓</span><span role="status">{dirty && !loading ? "参数已修改，运行后更新结果" : "结果以本机历史行情为准"}</span></div>
    </section>
    {error && <div role="alert" className="bt-error"><strong>本次回测未完成</strong><p>{error}</p></div>}
    {loading && <div className="bt-loading" role="status"><LoaderCircle size={20} className="spin" /><span>正在逐日计算成交、费用与净值…</span></div>}
    {result && m && <>
      <div className="bt-sample"><span><Clock3 size={13} />{result.symbol || submitted?.symbol} · {result.sample.startDate} — {result.sample.endDate}</span><span>{result.sample.bars} 个交易日 · 预热 {result.sample.warmupBars} 根日线 · {currency}</span></div>
      <div className="bt-kpis">
        <article className="bt-kpi bt-kpi-blue"><span>策略净收益</span><strong className={tone(m.totalReturnPct)}>{pct(m.totalReturnPct)}</strong><small>期末净值 {amount(m.finalEquity)}</small></article>
        <article className="bt-kpi bt-kpi-lilac"><span>相对买入持有</span><strong className={tone(m.excessReturnPct)}>{pct(m.excessReturnPct)}</strong><small>基准净收益 {pct(m.benchmarkReturnPct)}</small></article>
        <article className="bt-kpi bt-kpi-peach"><span>最大回撤</span><strong className={tone(m.maxDrawdownPct)}>{pct(m.maxDrawdownPct)}</strong><small>从每日净值峰值计算</small></article>
        <article className="bt-kpi"><span>已平仓胜率</span><strong>{m.winRatePct === null ? "—" : `${number(m.winRatePct, 1)}%`}</strong><small>{m.closedTradeCount} 笔平仓 · 胜 {m.wins} / 负 {m.losses} / 平 {m.breakeven}</small></article>
      </div>
      <div className="bt-stats">
        <div><span>平均盈亏比</span><strong>{number(m.payoffRatio)}<small>{m.payoffRatio !== null ? "×" : "样本不足"}</small></strong></div>
        <div><span>利润因子</span><strong>{number(m.profitFactor)}<small>{m.profitFactor !== null ? "×" : "无可用亏损样本"}</small></strong></div>
        <div><span>持仓时间占比</span><strong>{number(m.exposureFraction * 100, 1)}<small>%</small></strong></div>
        <div><span>费用与滑点合计</span><strong>{amount(m.totalCosts, 2)}</strong></div>
      </div>
      <EquityCurve key={JSON.stringify([submitted, marketStamp])} result={result} amount={amount} />
      {result.openPosition && <section className="panel bt-open"><div className="panel-head"><h2><Clock3 size={15} />期末未完成持仓</h2><span className="bt-tag">计入净值 · 不计胜率</span></div><div className="bt-open-data"><span>买入 <b>{result.openPosition.entryDate}</b></span><span>成交价 <b>{amount(result.openPosition.entryPrice, 2)}</b></span><span>已持有 <b>{result.openPosition.sessions} / {result.parameters.holdingDays} 日</b></span><span>收盘市值 <b>{amount(result.openPosition.marketValue, 2)}</b></span><span>浮动净损益 <b className={tone(result.openPosition.unrealizedPnl)}>{amount(result.openPosition.unrealizedPnl, 2)}</b></span></div><p>以 {result.openPosition.markDate} 收盘价 {amount(result.openPosition.markPrice, 2)} 标记市值，包含已付买入费用；未假设卖出，也未扣未来退出费用。</p></section>}
      <section className="panel bt-ledger"><div className="panel-head"><h2><ArrowUpRight size={16} />已平仓交易</h2><small>{m.closedTradeCount} 笔 · 按成交日期排列</small></div>
        {result.trades.length ? <div className="bt-ledger-scroll"><table><thead><tr><th>信号 / 买入日期</th><th>卖出日期</th><th>买入价</th><th>卖出价</th><th>持有日</th><th>净损益</th><th>净收益</th><th>双边手续费</th><th>滑点成本</th></tr></thead><tbody>{result.trades.map(trade => <tr key={trade.entryDate}><td><b>{trade.entryDate}</b><small>信号 {trade.signalDate}</small></td><td>{trade.exitDate}</td><td>{number(trade.entryPrice)}</td><td>{number(trade.exitPrice)}</td><td>{trade.sessions}</td><td className={tone(trade.netPnl)}>{amount(trade.netPnl, 2)}</td><td className={tone(trade.returnPct)}>{pct(trade.returnPct)}</td><td>{amount(trade.entryFee + trade.exitFee, 2)}</td><td>{amount(trade.entrySlippage + trade.exitSlippage, 2)}</td></tr>)}</tbody></table></div> : <div className="bt-empty">此窗口没有已平仓交易。{result.openPosition ? "期末持仓单独列示，等待满足持有周期。" : "尚未出现符合规则且可在次日执行的突破信号。"}</div>}
        <p className="bt-ledger-note">成交价已包含滑点；净损益同时扣除双边手续费。滑点成本为成交价相对原始开盘 / 收盘价格的差额估计。</p>
      </section>
      <details className="panel bt-method"><summary><span>计算规则与数据边界</span><ChevronDown size={16} /></summary><div className="bt-method-body"><ol>{result.rules.map(rule => <li key={rule}>{rule}</li>)}</ol><ul>{result.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul><p>原始行情范围 {result.sample.historyStartDate} — {result.sample.historyEndDate}；仅在选定区间入场，开始日前的数据只用于信号预热。胜率、盈亏比等均为历史描述。</p></div></details>
      <p className="bt-caution">历史回测不代表未来表现。少量交易、当前股票池及未复权数据会影响结论；请结合交易明细判断规则是否适合继续研究。</p>
    </>}
  </div>;
}
