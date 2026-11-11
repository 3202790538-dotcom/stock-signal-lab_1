"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ArrowUpRight, Building2, ChartNoAxesCombined, FileText, LoaderCircle, Newspaper, RefreshCw } from "lucide-react";
import "./company-intelligence.css";

type RemoteStatus = "ready" | "empty" | "error" | "unconfigured" | "not_applicable";
type Quarter = {
  periodStart?: string;
  periodEnd: string;
  fiscalLabel: string;
  revenue: number | null;
  netIncome: number | null;
  operatingCashFlow: number | null;
  capex: number | null;
  freeCashFlow: number | null;
  currency: string;
  source: string;
  sourceUrl?: string;
  filedAt: string | null;
  derived?: boolean;
};
type Annual = Quarter & { periodType?: "annual" };
type Peer = {
  symbol: string;
  name: string;
  currency: string;
  priceAsOf: string | null;
  periodEnd: string | null;
  marketCap: number | null;
  pe: number | null;
  ps: number | null;
  pfcf: number | null;
  status: string;
  source: string;
  sourceUrl?: string;
  sharesAsOf?: string;
  sharesSource?: string | null;
  filingSource?: string | null;
  retrievedAt?: string;
};
type Company = {
  symbol: string;
  name: string;
  currency: string;
  status: "ready" | "partial" | "unavailable";
  provider: string;
  retrievedAt: string;
  quarters: Quarter[];
  annuals?: Annual[];
  ttm: { revenue: number | null; netIncome: number | null; freeCashFlow: number | null; netMargin: number | null; fcfMargin: number | null; eps: number | null; periodEnd: string | null } | null;
  valuation: { price: number | null; priceAsOf: string | null; marketCap: number | null; pe: number | null; ps: number | null; pfcf: number | null; source: string; currency?: string; method?: string; sharesAsOf?: string; sharesSource?: string | null; filingSource?: string | null } | null;
  peers: Peer[];
  filings: { date: string; form: string; title: string; url: string; accession: string }[];
  warnings: string[];
  calculator: string;
};
type NewsItem = {
  id: string;
  symbol: string;
  title: string;
  publishedAt: string;
  source: string;
  url: string;
  kind: "filing" | "news";
  summary: string;
  impacts: string[];
  impactBasis: "rule" | "filing";
  relevance?: "direct" | "mention";
  form?: string;
};
type Envelope<T> = T & { status: RemoteStatus; lastError?: string; updatedAt?: string; stale?: boolean; sourceUrl?:string; sourceLabel?:string };
type FinancialData = { company: Company | null };
type NewsData = { items: NewsItem[] };
type ResourceState<T> = { symbol: string; value: Envelope<T>; loading: boolean };

const emptyFinancials: FinancialData = { company: null };
const emptyNews: NewsData = { items: [] };
const validStatuses = new Set<RemoteStatus>(["ready", "empty", "error", "unconfigured", "not_applicable"]);

function mergeFinancials(next: Envelope<FinancialData>, previous: Envelope<FinancialData>): Envelope<FinancialData> {
  return { ...next, company: next.company ?? (next.status === "error" ? previous.company : null), updatedAt: next.updatedAt || (next.status === "error" ? previous.updatedAt : undefined) };
}
function mergeNews(next: Envelope<NewsData>, previous: Envelope<NewsData>): Envelope<NewsData> {
  const items = Array.isArray(next.items) ? next.items : [];
  return { ...next, items: next.status === "error" && !items.length ? previous.items : items, updatedAt: next.updatedAt || (next.status === "error" ? previous.updatedAt : undefined) };
}

function useRemoteResource<T>(symbol: string, endpoint: string, empty: T, merge: (next: Envelope<T>, previous: Envelope<T>) => Envelope<T>, onRefreshed?: () => void, enabled = true) {
  const [state, setState] = useState<ResourceState<T>>({ symbol, value: { ...empty, status: "empty" }, loading: true });
  const controller = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const automaticallyRequested = useRef(new Map<string, number>());
  const afterRefresh = useRef(onRefreshed);

  useEffect(() => { afterRefresh.current = onRefreshed; }, [onRefreshed]);

  const load = useCallback(async (refresh = false, autoFetch = true) => {
    if (!enabled) return;
    controller.current?.abort();
    const activeController = new AbortController();
    controller.current = activeController;
    const request = ++sequence.current;
    const isCurrent = () => !activeController.signal.aborted && sequence.current === request;
    async function requestData(post: boolean): Promise<Envelope<T>> {
      const response = await fetch(post ? `${endpoint}/refresh` : `${endpoint}?symbol=${encodeURIComponent(symbol)}`, {
        method: post ? "POST" : "GET",
        ...(post ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol }) } : {}),
        cache: "no-store",
        signal: activeController.signal,
      });
      const payload = await response.json() as Partial<Envelope<T>>;
      if (!payload || !validStatuses.has(payload.status as RemoteStatus)) {
        throw new Error(response.ok ? "数据响应格式异常，请重试。" : `请求失败（${response.status}），请重试。`);
      }
      return {
        ...empty,
        ...payload,
        status: response.ok || payload.status === "unconfigured" ? payload.status as RemoteStatus : "error",
        ...(!response.ok ? { lastError: payload.lastError || `请求失败（${response.status}），请重试。` } : {}),
      } as Envelope<T>;
    }

    try {
      let result = await requestData(refresh);
      if (!isCurrent()) return;
      let didRefresh = refresh;
      if (!refresh && autoFetch && (result.status === "empty" || (result.status === "ready" && result.stale)) && Date.now() - (automaticallyRequested.current.get(symbol) || 0) > 15 * 60 * 1000) {
        automaticallyRequested.current.set(symbol, Date.now());
        setState(previous => ({ symbol, value: merge(result, previous.symbol === symbol ? previous.value : { ...empty, status: "empty" }), loading: true }));
        result = await requestData(true);
        didRefresh = true;
      }
      if (!isCurrent()) return;
      setState(previous => ({ symbol, value: merge(result, previous.symbol === symbol ? previous.value : { ...empty, status: "empty" }), loading: false }));
      if (didRefresh && result.status === "ready") afterRefresh.current?.();
    } catch (error) {
      if (!isCurrent()) return;
      const message = error instanceof Error ? error.message : "网络请求失败，请稍后重试。";
      setState(previous => ({ symbol, value: { ...(previous.symbol === symbol ? previous.value : { ...empty, status: "empty" as RemoteStatus }), status: "error", lastError: message }, loading: false }));
    } finally {
      if (sequence.current === request) controller.current = null;
    }
  }, [symbol, endpoint, empty, merge, enabled]);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    void Promise.resolve().then(() => { if (active) void load(); });
    const onFocus = () => { if (active && !controller.current) void load(); };
    window.addEventListener("focus", onFocus);
    return () => { active = false; sequence.current += 1; controller.current?.abort(); window.removeEventListener("focus", onFocus); };
  }, [load, enabled]);

  const current: ResourceState<T> = state.symbol === symbol ? state : { symbol, value: { ...empty, status: "empty" }, loading: true };
  const reload = useCallback(() => {
    if (!enabled) return;
    setState(previous => ({ ...previous, loading: true }));
    void load(false, false);
  }, [load, enabled]);
  const refresh = useCallback(() => {
    if (!enabled) return;
    setState(previous => ({ ...previous, loading: true }));
    void load(true);
  }, [load, enabled]);
  return { ...current, reload, refresh };
}

function numeric(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
function currencyCode(value: string | null | undefined): string {
  const code = value?.trim().toUpperCase() || "";
  if (!/^[A-Z]{3}$/.test(code)) return "币种未提供";
  try { new Intl.NumberFormat("zh-CN", { style: "currency", currency: code }); return code; }
  catch { return "币种未提供"; }
}
function amount(value: number | null | undefined, currency?: string, includeCurrency = true): string {
  if (!numeric(value)) return "—";
  const absolute = Math.abs(value);
  const divisor = absolute >= 1_000_000_000_000 ? 1_000_000_000_000 : absolute >= 100_000_000 ? 100_000_000 : absolute >= 10_000 ? 10_000 : 1;
  const unit = divisor === 1_000_000_000_000 ? "万亿" : divisor === 100_000_000 ? "亿" : divisor === 10_000 ? "万" : "";
  const formatted = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value / divisor);
  return `${formatted}${unit}${includeCurrency ? ` ${currencyCode(currency)}` : ""}`;
}
function multiple(value: number | null | undefined): string {
  return numeric(value) ? `${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}×` : "—";
}
function percentage(value: number | null | undefined): string {
  return numeric(value) ? `${(value * 100).toFixed(2)}%` : "—";
}
function dateLabel(value: string | null | undefined): string {
  if (!value) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}
function timestamp(value: string | null | undefined, filing = false): string {
  if (!value) return "时间未提供";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return `${value}${filing ? " · 披露日" : " · 日期"}`;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未提供" : `${new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date)} 北京时间`;
}
function safeUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  try { const url = new URL(value); return url.protocol === "https:" || url.protocol === "http:" ? url.href : undefined; }
  catch { return undefined; }
}
function SourceLink({ source, url, label }: { source?: string; url?: string; label?: string }) {
  const href = safeUrl(url) || safeUrl(source);
  return href ? <a className="ci-source-link" href={href} target="_blank" rel="noopener noreferrer">{label || "原始披露"}<ArrowUpRight size={12} /></a> : <span className="ci-muted">{source || "来源未提供"}</span>;
}
function StatusBadge({ status, loading }: { status: RemoteStatus; loading: boolean }) {
  const labels: Record<RemoteStatus, string> = { ready: "已获取", empty: "暂无缓存", error: "更新失败", unconfigured: "等待配置", not_applicable: "此类财报不适用" };
  return <span className={`ci-status ci-status-${status}`} role="status">{loading ? <><LoaderCircle size={11} className="spin" />获取中</> : labels[status]}</span>;
}
function ResourceNotice({ status, error, cached, updatedAt, sourceUrl, sourceLabel }: { status: RemoteStatus; error?: string; cached: boolean; updatedAt?: string; sourceUrl?: string; sourceLabel?: string }) {
  if (status === "not_applicable") return <div className="ci-message"><p>{error || "该产品不适用普通公司的季度财报分析。"}</p>{sourceUrl&&<SourceLink url={sourceUrl} label={sourceLabel||"核验来源"}/>}</div>;
  if (status === "unconfigured") return <div className="ci-message">{error || "需要配置 SEC 联系邮箱。配置完成后点击“重新加载”。"}</div>;
  if (status !== "error") return null;
  return <div className="ci-message ci-message-error" role="status"><p>{error || "暂时无法更新，请稍后重试。"}</p>{cached && <p>继续显示最近一次获取的数据{updatedAt ? ` · ${timestamp(updatedAt)}` : ""}。</p>}</div>;
}
function RefreshButton({ loading, unconfigured, onClick }: { loading: boolean; unconfigured: boolean; onClick: () => void }) {
  return <button type="button" className="ci-refresh" disabled={loading} onClick={onClick}><RefreshCw size={12} className={loading ? "spin" : undefined} />{loading ? "获取中" : unconfigured ? "重新加载" : "更新数据"}</button>;
}

const chartMetrics = [{ key: "revenue", label: "营收" }, { key: "netIncome", label: "净利润" }, { key: "freeCashFlow", label: "自由现金流" }] as const;
type ChartMetric = typeof chartMetrics[number]["key"];

function QuarterChart({ company }: { company: Company }) {
  const [metric, setMetric] = useState<ChartMetric>("revenue");
  const chartId = useId();
  const currency = currencyCode(company.currency);
  const quarters = [...(company.quarters || [])].filter(q => currencyCode(q.currency || company.currency) === currency).sort((a, b) => a.periodEnd.localeCompare(b.periodEnd)).slice(-8);
  const values = quarters.map(q => q[metric]).filter(numeric);
  const min = Math.min(0, ...values), max = Math.max(0, ...values);
  const lower = min === 0 && max === 0 ? -1 : min;
  const upper = min === 0 && max === 0 ? 1 : max;
  const width = 640, height = 204, left = 74, right = 16, top = 17, bottom = 162;
  const y = (value: number) => top + (upper - value) / (upper - lower) * (bottom - top);
  const baseline = y(0);
  const slot = (width - left - right) / Math.max(quarters.length, 1);
  const ticks = [...new Set([upper, 0, lower])];
  return <div className="ci-subpanel ci-quarter-chart"><div className="ci-subhead"><div><h3><ChartNoAxesCombined size={14} />季度趋势</h3><p>{currency} · 最近 {quarters.length} 个可用季度</p></div><div className="ci-segment" aria-label="季度图指标">{chartMetrics.map(item => <button type="button" key={item.key} aria-pressed={metric === item.key} className={metric === item.key ? "active" : undefined} onClick={() => setMetric(item.key)}>{item.label}</button>)}</div></div>
    {values.length ? <div className="ci-chart-scroll"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={chartId}><title id={chartId}>{company.symbol} 季度{chartMetrics.find(item => item.key === metric)?.label}，单位 {currency}；详细数值见季度明细</title>{ticks.map(tick => <g key={tick}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className={tick === 0 ? "ci-chart-baseline" : "ci-chart-grid"} /><text x={left - 9} y={y(tick) + 3} textAnchor="end" className="ci-chart-tick">{amount(tick, currency, false)}</text></g>)}{quarters.map((quarter, index) => {
      const value = quarter[metric], x = left + slot * index + slot * .23, barWidth = slot * .54;
      return <g key={`${quarter.periodEnd}-${index}`}>{numeric(value) ? <rect x={x} y={Math.min(y(value), baseline) - (value === 0 ? 1 : 0)} width={barWidth} height={Math.max(Math.abs(y(value) - baseline), 2)} rx={3} className={value < 0 ? "ci-chart-bar ci-chart-negative" : "ci-chart-bar"}><title>{quarter.fiscalLabel || quarter.periodEnd}：{amount(value, currency)}</title></rect> : <text x={x + barWidth / 2} y={baseline - 8} textAnchor="middle" className="ci-chart-tick">—</text>}<text x={x + barWidth / 2} y={height - 17} textAnchor="middle" className="ci-chart-tick">{quarter.periodEnd.slice(0, 7)}</text></g>;
    })}</svg></div> : <p className="ci-inline-empty">该指标暂无可用季度数据。</p>}
    <p className="ci-note">横线为零值，负值位于横线下方；缺失季度不补零。不同币种分别披露，不做汇率换算。</p>
  </div>;
}

function PeerSources({ peer }: { peer: Peer }) {
  const sharesUrl = safeUrl(peer.sharesSource);
  const filingUrl = safeUrl(peer.filingSource);
  const sameFiling = Boolean(sharesUrl && sharesUrl === filingUrl);
  return <>
    {filingUrl && <SourceLink url={filingUrl} label={sameFiling ? "SEC 披露 / 股数" : "SEC 披露原文"} />}
    {sharesUrl && !sameFiling && <small><SourceLink url={sharesUrl} label="SEC 股数原文" /></small>}
    {!sharesUrl && !filingUrl && <SourceLink source={peer.source} url={peer.sourceUrl} label="查看来源" />}
    <small>股数日期 {dateLabel(peer.sharesAsOf)}</small>
    <small title={timestamp(peer.retrievedAt)}>获取 {dateLabel(peer.retrievedAt)}</small>
    {peer.status !== "ready" && <small>{peer.status === "partial" ? "部分数据" : "数据不全"}</small>}
  </>;
}

function PeerComparison({ company }: { company: Company }) {
  const valuation = company.valuation;
  const own: Peer = {
    symbol: company.symbol, name: company.name, currency: valuation?.currency || company.currency,
    priceAsOf: valuation?.priceAsOf || null, periodEnd: company.ttm?.periodEnd || null,
    marketCap: valuation?.marketCap ?? null, pe: valuation?.pe ?? null, ps: valuation?.ps ?? null,
    pfcf: valuation?.pfcf ?? null, status: company.status, source: valuation?.source || company.provider,
    sharesAsOf: valuation?.sharesAsOf, sharesSource: valuation?.sharesSource,
    filingSource: valuation?.filingSource, retrievedAt: company.retrievedAt,
  };
  const peers = (company.peers || []).filter(peer => peer.symbol !== company.symbol);
  return <div className="ci-subpanel ci-peers">
    <div className="ci-subhead"><div><h3><Building2 size={14} />同行估值</h3><p>本股与同行 · TTM 口径 · 估算值</p></div></div>
    <div className="table-scroll ci-table-scroll"><table><thead><tr><th>公司 / 币种</th><th>估算市值</th><th>估算 P/E</th><th>估算 P/S</th><th>估算 P/FCF</th><th>价格日期 / 财报期末</th><th>原始披露 / 股数日期</th></tr></thead><tbody>
      {[own, ...peers].map((peer, index) => <tr key={`${peer.symbol}-${index}`} className={index === 0 ? "ci-current-company" : undefined}>
        <td><b>{peer.symbol}{index === 0 && <span className="ci-own-tag">本股</span>}</b><small>{peer.name || peer.symbol} · {currencyCode(peer.currency)}</small></td>
        <td>{amount(peer.marketCap, peer.currency)}</td><td>{multiple(peer.pe)}</td><td>{multiple(peer.ps)}</td><td>{multiple(peer.pfcf)}</td>
        <td title={timestamp(peer.priceAsOf)}>{dateLabel(peer.priceAsOf)}<small>{dateLabel(peer.periodEnd)}</small></td>
        <td><PeerSources peer={peer} /></td>
      </tr>)}
    </tbody></table></div>
    <p className="ci-note">估算市值按所列价格 × SEC 已披露股数计算，并非官方实时市值；股数和价格可能不在同一天。估算倍数使用该市值与 TTM 财务数据，— 表示缺失或不适用。各公司币种和财报期末可能不同。{!peers.length && " 暂无可用同行数据。"}</p>
  </div>;
}

function QuarterLedger({ company }: { company: Company }) {
  const annualFallback = !company.quarters?.length && Boolean(company.annuals?.length);
  const periods = [...(annualFallback ? company.annuals || [] : company.quarters || [])].sort((a, b) => b.periodEnd.localeCompare(a.periodEnd));
  return <div className="ci-subpanel">
    <div className="ci-subhead"><div><h3>{annualFallback ? "年度财务明细" : "季度财务明细"}</h3><p>{annualFallback ? "年度原值 · 未拆分为季度，未作为 TTM" : "单季数据 · 按财报期末倒序"}</p></div><span className="ci-count">{periods.length} {annualFallback ? "个年度" : "个季度"}</span></div>
    {periods.length ? <div className="table-scroll ci-table-scroll"><table className="ci-quarter-table"><thead><tr><th>{annualFallback ? "年度期间" : "财报期末"}</th><th>营收</th><th>净利润</th><th>经营现金流</th><th>资本开支</th><th>自由现金流</th><th>币种</th><th>披露日期 / 来源</th></tr></thead><tbody>
      {periods.map((period, index) => <tr key={`${period.periodEnd}-${index}`}>
        <td><b>{period.periodEnd}</b><small>{period.fiscalLabel}{annualFallback ? " · 年度" : period.derived ? " · 差额推导" : ""}</small>{annualFallback && period.periodStart && <small>起始 {period.periodStart}</small>}</td>
        {(["revenue", "netIncome", "operatingCashFlow", "capex", "freeCashFlow"] as const).map(key => <td key={key} className={numeric(period[key]) && period[key] < 0 ? "ci-negative" : undefined}>{amount(period[key], period.currency || company.currency, false)}</td>)}
        <td>{currencyCode(period.currency || company.currency)}</td><td>{dateLabel(period.filedAt)}<small><SourceLink source={period.source} url={period.sourceUrl} /></small></td>
      </tr>)}
    </tbody></table></div> : <p className="ci-inline-empty">暂无可核实的季度财务数据。</p>}
    <p className="ci-note">金额以每行原始币种计，万亿 = 1,000,000,000,000，亿 = 100,000,000，万 = 10,000。自由现金流采用来源所列经营现金流与资本开支口径；{annualFallback ? "仅有年度数据时单列原始年度金额，不推算季度或 TTM。" : "差额推导季度已单独标记。"}</p>
  </div>;
}

function AutoFinancials({ symbol, resource }: { symbol: string; resource: ReturnType<typeof useRemoteResource<FinancialData>> }) {
  const { value, loading, refresh } = resource;
  const company = value.company;
  const hasData = Boolean(company);
  const ttm = company?.ttm;
  const financialLabel = company?.status === "partial" ? "部分财务可用" : company?.status === "unavailable" ? "财务数据尚不可用" : "自动财务与同行比较";
  return <section className="panel ci-panel" aria-label={`${symbol} 自动财务`} aria-busy={loading}><div className="ci-panel-head"><div><h2><ChartNoAxesCombined size={16} />自动财务 <span>{symbol}</span></h2><p>{company?.name ? `${company.name} · ` : ""}{financialLabel}</p></div><div className="ci-head-actions"><StatusBadge status={value.status} loading={loading} />{value.status!=="not_applicable"&&<RefreshButton loading={loading} unconfigured={value.status === "unconfigured"} onClick={refresh} />}</div></div><div className="ci-panel-body"><ResourceNotice status={value.status} error={value.lastError} cached={hasData} updatedAt={value.updatedAt} sourceUrl={value.sourceUrl} sourceLabel={value.sourceLabel} />
    {company ? <><div className="ci-finance-summary"><span>{ttm ? `近 12 个月 TTM · 截至 ${dateLabel(ttm.periodEnd)}` : !company.quarters?.length && company.annuals?.length ? "暂无完整 TTM · 已披露年度数据另列" : "暂无完整 TTM"}</span><span>财务 {currencyCode(company.currency)}{numeric(company.valuation?.price) ? ` · 股价 ${amount(company.valuation?.price, company.valuation?.currency || company.currency)} · ${dateLabel(company.valuation?.priceAsOf)}` : ""}</span></div><div className="ci-metric-grid">{[{ label: "TTM 营收", value: amount(ttm?.revenue, company.currency), raw: ttm?.revenue }, { label: "TTM 净利润", value: amount(ttm?.netIncome, company.currency), raw: ttm?.netIncome }, { label: "TTM 自由现金流", value: amount(ttm?.freeCashFlow, company.currency), raw: ttm?.freeCashFlow }, { label: "TTM 净利率", value: percentage(ttm?.netMargin), raw: ttm?.netMargin }].map(metric => <div className="ci-metric" key={metric.label}><span>{metric.label}</span><strong className={numeric(metric.raw) && metric.raw < 0 ? "ci-negative" : undefined}>{metric.value}</strong></div>)}</div><div className="ci-finance-columns"><QuarterChart key={symbol} company={company} /><PeerComparison company={company} /></div><QuarterLedger company={company} />{Boolean(company.warnings?.length) && <details className="ci-warnings"><summary>数据口径与缺失说明 · {company.warnings.length}</summary><ul>{company.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul></details>}<p className="ci-note ci-provenance">数据来源：{company.provider || "—"} · 计算：{company.calculator || "FinanceToolkit"} · 获取时间：{timestamp(company.retrievedAt || value.updatedAt)}</p></> : value.status === "not_applicable" ? null : <div className="ci-empty-state"><FileText size={24} /><p>{loading ? "正在获取财报、季度指标与同行数据…" : value.status === "unconfigured" ? "配置完成后可自动获取财报。" : "暂无自动财务数据。"}</p>{!loading && value.status !== "unconfigured" && <button type="button" onClick={refresh}>重新获取</button>}</div>}
  </div></section>;
}

function NewsTimeline({ symbol, resource }: { symbol: string; resource: ReturnType<typeof useRemoteResource<NewsData>> }) {
  const [filter, setFilter] = useState<"all" | "news" | "filing">("all");
  const { value, loading, refresh } = resource;
  const items = [...value.items].filter(item => item.symbol === symbol).sort((a, b) => {
    const timeA = Date.parse(a.publishedAt), timeB = Date.parse(b.publishedAt);
    return (Number.isNaN(timeB) ? 0 : timeB) - (Number.isNaN(timeA) ? 0 : timeA);
  });
  const filtered = items.filter(item => filter === "all" || item.kind === filter);
  return <section className="panel ci-panel" aria-label={`${symbol} 新闻与公告时间线`} aria-busy={loading}><div className="ci-panel-head"><div><h2><Newspaper size={16} />新闻与公告 <span>{symbol}</span></h2><p>{value.updatedAt ? `更新于 ${timestamp(value.updatedAt)}` : "按发布时间倒序 · 保留原始来源"}</p></div><div className="ci-head-actions"><StatusBadge status={value.status} loading={loading} />{value.status!=="not_applicable"&&<RefreshButton loading={loading} unconfigured={value.status === "unconfigured"} onClick={refresh} />}</div></div><div className="ci-panel-body"><ResourceNotice status={value.status} error={value.lastError} cached={items.length > 0} updatedAt={value.updatedAt} /><div className="ci-news-toolbar"><div className="ci-segment" aria-label="新闻类型筛选">{([{ key: "all", label: "全部" }, { key: "news", label: "新闻" }, { key: "filing", label: "公告" }] as const).map(item => <button type="button" key={item.key} aria-pressed={filter === item.key} className={filter === item.key ? "active" : undefined} onClick={() => setFilter(item.key)}>{item.label}<span>{item.key === "all" ? items.length : items.filter(news => news.kind === item.key).length}</span></button>)}</div><span className="ci-note">新闻显示北京时间；公告仅列已披露日期</span></div><p className="ci-news-method">影响标签根据标题关键词或公告类型匹配，表示可能需要跟踪的指标，不代表已核实的经营影响或股价因果关系。</p>
    {filtered.length ? <ol className="ci-timeline">{filtered.map(item => {
      const href = safeUrl(item.url);
      return <li className="ci-news-item" key={item.id}><div className={`ci-news-dot ${item.kind === "filing" ? "ci-filing-dot" : ""}`} aria-hidden="true" /><article><div className="ci-news-meta"><time dateTime={item.publishedAt}>{timestamp(item.publishedAt, item.kind === "filing")}</time><span className={`ci-kind-tag ${item.kind === "filing" ? "ci-filing-tag" : ""}`}>{item.kind === "filing" ? item.form || "公告" : "新闻"}</span>{item.kind === "news" && item.relevance === "mention" && <span className="ci-kind-tag">摘要提及</span>}{href ? <a href={href} target="_blank" rel="noopener noreferrer" className="ci-news-source">{item.source || "原始来源"}<ArrowUpRight size={11} /></a> : <span className="ci-muted">{item.source || "来源未提供"}</span>}</div><h3>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{item.title}<ArrowUpRight size={13} /></a> : item.title}</h3>{item.summary && <p className="ci-news-summary"><span>{item.impactBasis === "filing" ? "公告类型说明" : "关键词规则提示"}</span>{item.summary}</p>}{Boolean(item.impacts?.length) && <div className="ci-impact-list" aria-label="规则匹配的可能关注指标">{item.impacts.map((impact, index) => <span key={`${index}-${impact}`}>{impact}</span>)}</div>}</article></li>;
    })}</ol> : <div className="ci-empty-state"><Newspaper size={24} /><p>{loading ? "正在获取新闻与公告…" : filter === "all" ? "暂无可用新闻或公告。" : filter === "filing" ? "暂无可用公告。" : "暂无可用新闻。"}</p>{!loading && value.status !== "unconfigured" && <button type="button" onClick={refresh}>重新获取</button>}</div>}
  </div></section>;
}

type IntelligenceSection = "all" | "financials" | "news";

function SelectedCompanyIntelligence({ symbol, section }: { symbol: string; section: IntelligenceSection }) {
  const news = useRemoteResource(symbol, "/api/news", emptyNews, mergeNews, undefined, section !== "financials");
  const financials = useRemoteResource(symbol, "/api/company-research", emptyFinancials, mergeFinancials, news.reload, section !== "news");
  return <div className="company-intelligence">{section !== "news" && <AutoFinancials symbol={symbol} resource={financials} />}{section !== "financials" && <NewsTimeline key={symbol} symbol={symbol} resource={news} />}</div>;
}

export function CompanyIntelligence({ symbol, section = "all" }: { symbol: string; section?: IntelligenceSection }) {
  return <SelectedCompanyIntelligence key={symbol} symbol={symbol} section={section} />;
}
