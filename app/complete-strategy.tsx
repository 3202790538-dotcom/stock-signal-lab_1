"use client";

import {useEffect, useRef, useState} from "react";
import {Activity, BookOpen, Check, ChevronRight, FlaskConical, LoaderCircle, RefreshCw, ShieldCheck, X} from "lucide-react";
import {OperationLedger} from "./complete-strategy-ledger";
import {useClientReady} from "./use-client-ready";
import {readCompletePreferences,COMPLETE_PREFERENCES_KEY} from "./strategy-preferences";
import {gateStudySignals} from "./market-availability";
import {type Catalog, type Metrics, type ProfileId, type Rules, type Study, type TemplateId, finite, money, num, parameterText, pct} from "./complete-strategy-types";
import "./complete-strategy.css";

const templates = [{id: "breakout" as const, label: "趋势突破", description: "趋势向上时，等待放量突破此前高点。", number: "01"}, {id: "pullback" as const, label: "趋势回调", description: "趋势延续时，等待回踩均线后的再启动。", number: "02"}, {id: "rotation" as const, label: "动量轮动", description: "每周比较相对动量，保留排名靠前的股票。", number: "03"}];
const profiles = [{id: "conservative" as const, label: "保守"}, {id: "balanced" as const, label: "均衡"}, {id: "aggressive" as const, label: "进取"}];
const beijingTime = (value?: string) => {
  if (!value) return "未提供更新时间";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "更新时间格式无效" : new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false}).format(date);
};
const nextModeNames: Record<string, string> = {normal: "恢复本档限额", reduced: "一级降风险，现有与新仓限额减半", paused: "二级暂停，全部退出并进入冷静期", terminated: "超过回撤目标，全部退出并停止本窗口交易", recovery: "开始新一轮半档恢复观察"};
const STORAGE_KEY = COMPLETE_PREFERENCES_KEY;
const errorMessage = (payload: unknown, fallback: string) => {
  if (payload && typeof payload === "object") {
    const data = payload as {detail?: unknown; error?: unknown};
    if (typeof data.detail === "string") return data.detail;
    if (typeof data.error === "string") return data.error;
  }
  return fallback;
};

function RuleSummary({rules, selected}: {rules: Rules; selected: boolean}) {
  const p = rules.profile;
  return <div className="cs-rule-summary"><div className="cs-subhead"><h3>{selected ? "实际生效的研究规则" : "基础规则预览 · 尚未选参"}</h3><span className="cs-tag">{rules.templateLabel} / {p.label}</span></div><p className="cs-parameters">{parameterText(rules.parameters)}</p><p className="cs-note">{selected ? "以下参数由前段训练选出，同时用于后段验证与全段模拟。" : "这是规则目录中的默认参数；重新计算完成后将显示训练选出的实际参数。"}规则版本 {rules.version}</p>
    <dl className="cs-caps"><div><dt>回撤研究目标</dt><dd>{pct(p.drawdownPct)}</dd></div><div><dt>每笔止损预算</dt><dd>{pct(p.tradeRiskPct)}</dd></div><div><dt>总持仓上限</dt><dd>{pct(p.grossCapPct)}</dd></div><div><dt>单股 / 半导体上限</dt><dd>{pct(p.singleCapPct)} / {pct(p.sectorCapPct)}</dd></div><div><dt>未平仓初始风险上限</dt><dd>{pct(p.openRiskCapPct)}</dd></div><div><dt>最多同时持仓</dt><dd>{p.maxPositions} 只</dd></div></dl>
    <p className="cs-note">以上为所选档位的基础限额。降风险或恢复过渡期按半档执行；逐笔实际数量与限额见操作明细。回撤目标与初始止损预算都不是可保证的最大亏损。</p>
  </div>;
}
function RuleSections({rules}: {rules: Rules}) {
  return <div className="cs-rule-sections">{rules.sections.map((section, i) => <details key={`${section.title}-${i}`}><summary>{section.title}</summary><ul>{section.items.map((item, j) => <li key={j}>{item}</li>)}</ul></details>)}</div>;
}
function Guide({rules, selected, close}: {rules: Rules | null; selected: boolean; close: () => void}) {
  const riskBudget = rules ? 10000 * rules.profile.tradeRiskPct / 100 : null;
  return <><div className="cs-dialog-head"><div><span className="cs-eyebrow">完整策略 · 操作指南</span><h2 id="complete-guide-title">使用说明</h2></div><button type="button" aria-label="关闭使用说明" onClick={close} autoFocus><X size={20}/><span>关闭</span></button></div>
    <div className="cs-guide-body"><section><h3>怎么使用</h3><ol><li>选择一套策略模板，再选择风险档位。收益目标默认 50%，可输入 0–200%；它用于训练筛选，不能提高风险额度。</li><li>选择变化后会自动重新回测。计算期间上一组成绩立即隐藏；结果显示训练选出的真实参数。</li><li>先读后段历史验证，与 SPY、股票池等额持有及双倍成本对照。前 70% 训练只用于选参，后 30% 验证从独立的 10,000 美元现金开始。</li><li>在「操作明细」选择已平仓交易、期末持仓或跳过原因，展开记录检查条件、股数、成交费及止损调整。</li><li>全段模拟包含训练期，仅解释当前模拟状态。最新候选等待下一交易日，尚未成交。行情由本机自动更新；IBKR 账户和持仓仍需手动同步。</li></ol></section>
    <section><h3>这些术语是什么意思</h3><dl className="cs-glossary"><div><dt>SMA 简单移动平均</dt><dd>过去 N 个交易日收盘价的平均数；SMA20、50、200 分别描述较短、中、长期价格趋势。</dd></div><div><dt>ATR14 日均真实波幅</dt><dd>每天取「最高−最低、最高与前收盘的差、最低与前收盘的差」绝对值的最大值，再平均最近 14 日。它衡量波动，不是预计涨幅。</dd></div><div><dt>R63 / 动量</dt><dd>最近 63 个交易日的收盘涨幅；股票 R63 大于 SPY R63 表示在这一窗口涨幅更高。</dd></div><div><dt>回撤</dt><dd>净值相对此前高点的跌幅。本轮风控高点用于暂停与恢复，整段历史高点用于总回撤；恢复不会抹掉历史亏损。</dd></div><div><dt>bps 基点</dt><dd>1 bps = 0.01%。默认单边手续费 5 bps、滑点 5 bps，成本压力测试把两者分别翻倍。</dd></div></dl></section>
    {rules ? <section><RuleSummary rules={rules} selected={selected}/><h3>股数计算 · 以当前档位举例</h3><p>假设净值 10,000 美元、买入成交价 100 美元、信号日 ATR14 = 2 美元：初始止损为 100 − 2 × 2 = 96 美元，每股初始风险 4 美元。</p><p>当前「{rules.profile.label}」基础档每笔预算为 {pct(rules.profile.tradeRiskPct)}，即 {money(riskBudget)}；风险预算允许 {num((riskBudget || 0) / 4, 2)} 股，最终仍须取所有额度允许股数的最小值并向下取整。实际现金、行业限额、总仓位和半档状态可能让数量更少。</p><p className="cs-note">举例仅解释公式，不是交易建议或真实成交。股数 = floor(min(每笔风险预算 ÷ 止损距离，各项剩余市值额度 ÷ 成交价，剩余初始风险额度 ÷ 止损距离，现金 ÷ 含手续费买入价，前20日平均量 × 0.1%))。</p><h3>后端规则定义 · 买卖、止损与暂停恢复</h3><RuleSections rules={rules}/></section> : <p className="cs-notice">规则目录尚未载入。连接研究服务后会显示所选模板的完整规则和限额。</p>}
    <section><h3>如何阅读操作记录</h3><p>信号日是条件成立的收盘日，模拟买入通常是下一交易日开盘。一次入场到全部退出算一笔完整交易，减仓记在同一头寸的成交流水中。开盘计划退出、止损及减仓先处理，再按排序依次买入，盘中卖出款不能反过来用于当天开盘买入。</p><p>初始止损在买入日生效；移动止损只向上调整，并从下一交易日起生效。没有固定百分比止盈。现金不足、行业额度不足、跳空过滤、冷静期或条件未满足均会留下决策原因。</p><p>已实现与未实现盈亏分列；只用完全平仓交易计算胜率。跳空、滑点与费用可能使实际模拟损失大于初始风险预算。</p></section>
    <section className="cs-notice"><h3>研究、模拟和真实账户的边界</h3><p>原始价格探索回测，未处理拆股和分红。固定关注股池 NVDA、MRVL、AVGO、MU、GOOG 有选择偏差；SPCX、SKHY 不以较早同名代码历史补齐。研究结果固定为 research_only。</p><p>反复调整目标后，验证期已经被观察，不是封存样本外结果。少于 30 笔平仓样本时统计不充分。历史达标不等于未来盈利。</p><p>当前 IBKR 接口只有账户与持仓快照，没有真实历史委托和成交流水。本页不下单；模拟记录不能当作你的真实操作记录。</p></section></div></>;
}
function ValidationCurve({study}: {study: Study}) {
  const points = study.equityCurve;
  const [cursor, setCursor] = useState(points.length - 1);
  const index = Math.max(0, Math.min(cursor, points.length - 1)), point = points[index];
  if (!point) return <p className="cs-empty">没有可用的验证资金曲线。</p>;
  const values = points.flatMap(p => [p.strategy, p.spy, p.basket]);
  const low = Math.min(...values), high = Math.max(...values), pad = Math.max((high - low) * .12, high * .01, 1), floor = Math.max(0, low - pad), ceiling = high + pad;
  const width = 920, height = 245, left = 76, right = 22, top = 18, bottom = 30;
  const x = (i: number) => left + i / Math.max(1, points.length - 1) * (width - left - right);
  const y = (v: number) => top + (ceiling - v) / (ceiling - floor) * (height - top - bottom);
  const path = (key: "strategy" | "spy" | "basket") => points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(p[key]).toFixed(2)}`).join(" ");
  return <section className="cs-chart"><div className="cs-subhead"><h3><Activity size={17}/>后段验证 · 资金曲线</h3><div className="cs-legend"><span>● 策略</span><span>● SPY</span><span>● 股票池</span></div></div><div className="cs-readout"><time>{point.date}</time><strong>策略 {money(point.strategy)}</strong><span>SPY {money(point.spy)}</span><span>股票池 {money(point.basket)}</span></div>
    <div className="cs-chart-scroll"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="后段验证策略与 SPY、股票池基准净值曲线" onPointerMove={event => {const bounds = event.currentTarget.getBoundingClientRect(); setCursor(Math.max(0, Math.min(points.length - 1, Math.round(((event.clientX - bounds.left) / bounds.width * width - left) / (width - left - right) * (points.length - 1)))));}}><desc>{points[0].date} 至 {points.at(-1)?.date}，单位美元。鼠标移动或下方键盘滑块可逐日查看三条净值。</desc>{[0, 1, 2, 3].map(i => {const value = floor + (ceiling - floor) * i / 3; return <g key={i}><line className="cs-gridline" x1={left} x2={width - right} y1={y(value)} y2={y(value)}/><text x={left - 10} y={y(value) + 4} textAnchor="end">{num(value, 0)}</text></g>;})}<path className="cs-line cs-line-spy" d={path("spy")}/><path className="cs-line cs-line-basket" d={path("basket")}/><path className="cs-line" d={path("strategy")}/><line className="cs-cursor" x1={x(index)} x2={x(index)} y1={top} y2={height - bottom}/><circle cx={x(index)} cy={y(point.strategy)} r={4} fill="#79529d"/><text x={left} y={height - 7}>{points[0].date}</text><text x={width - right} y={height - 7} textAnchor="end">{points.at(-1)?.date}</text></svg></div>
    <label className="cs-scrubber"><span>逐日查看</span><input aria-label="后段验证曲线日期" type="range" min={0} max={points.length - 1} value={index} onChange={event => setCursor(Number(event.target.value))}/><time>{point.date}</time></label><p className="cs-note">三者同窗、同初始资金、同费用口径，期末按收盘估值。基准全额买入持有；策略可留现金，风险承担不同。低回撤本身不证明选股能力。</p></section>;
}
function Comparison({study}: {study: Study}) {
  const rows: [string, Metrics][] = [["前段训练 · 用于选参", study.training], ["后段验证 · 所选策略", study.validation], ["同窗基准 · SPY 买入持有", study.benchmarks.spy], ["同窗基准 · 股票池等额持有", study.benchmarks.basket], ["同窗压力 · 手续费和滑点各翻倍", study.costStress]];
  return <div className="cs-table-scroll" tabIndex={0} aria-label="训练验证基准和成本压力比较，可横向滚动"><table className="cs-comparison"><thead><tr><th>区间与口径</th><th>年化收益</th><th>累计收益</th><th>最大回撤</th><th>费用合计</th><th>年化 / 回撤目标</th></tr></thead><tbody>{rows.map(([label, metric], i) => <tr key={label} className={i === 1 ? "cs-selected-row" : ""}><td><b>{label}</b><small>{metric.startDate} — {metric.endDate} · {metric.sessions} 日</small></td><td>{pct(metric.annualReturnPct)}</td><td>{pct(metric.totalReturnPct)}</td><td>{pct(metric.maxDrawdownPct)}</td><td>{money(metric.totalCosts)}</td><td>{metric.meetsTargets !== undefined ? metric.meetsTargets ? "均达到" : "未同时达到" : "仅供比较"}</td></tr>)}</tbody></table></div>;
}
function StudyResults({study, requestKey}: {study: Study; requestKey: string}) {
  const [scope, setScope] = useState("validation");
  const m = study.validation, state = study.simulation.state;
  const bothMet = study.training.meetsTargets && study.validation.meetsTargets;
  const full = scope === "full";
  return <div className="cs-results"><div className={`cs-verdict ${bothMet ? "cs-verdict-met" : ""}`} role="status"><ShieldCheck size={22}/><div><strong>{bothMet ? "训练与验证均达到目标 · 仍属历史研究" : "尚未通过训练与验证的双重目标检验"}</strong><p>{study.conclusion}</p></div></div>
    <div className="cs-meta"><span>数据截至 {study.asOf}</span><span title={study.marketGeneratedAt}>行情更新（北京时间）{beijingTime(study.marketGeneratedAt)}</span><span>{study.candidateCount} 组训练候选 · {study.trainingQualifiedCount} 组训练同时达标</span></div>
    <RuleSummary rules={study.rules} selected/><details className="cs-rules"><summary>展开完整买卖规则、仓位公式与暂停恢复条件</summary><RuleSections rules={study.rules}/></details>
    <div className="cs-metrics"><div><span>验证期折算年化</span><strong>{pct(m.annualReturnPct)}</strong><small>目标 {pct(study.targets.annualReturnPct)} · 按 252 日折算</small></div><div><span>验证期最大回撤</span><strong>{pct(m.maxDrawdownPct)}</strong><small>研究目标 {pct(study.targets.maxDrawdownPct)}</small></div><div><span>验证期累计收益</span><strong>{pct(m.totalReturnPct)}</strong><small>{m.startDate} — {m.endDate}</small></div></div>
    <Comparison study={study}/><dl className="cs-statline"><div><dt>完全平仓</dt><dd>{m.closedTradeCount} 笔</dd></div><div><dt>逐笔胜率</dt><dd>{pct(m.winRatePct)}</dd></div><div><dt>平均盈亏比</dt><dd>{num(m.payoffRatio)}</dd></div><div><dt>利润因子</dt><dd>{num(m.profitFactor)}</dd></div><div><dt>平均现金占比</dt><dd>{pct(m.averageCashPct)}</dd></div><div><dt>换手率</dt><dd>{pct(m.turnoverPct)}</dd></div><div><dt>风控事件</dt><dd>{m.riskEventCount} 次</dd></div><div><dt>期末持仓</dt><dd>{m.openPositionCount} 只</dd></div></dl>
    {m.closedTradeCount < 30 && <p className="cs-notice">验证期仅 {m.closedTradeCount} 笔完全平仓交易，少于 30 笔，统计样本不充分。未平仓不计入胜率；「—」表示无法计算，不能视为 0。</p>}
    <ValidationCurve study={study}/><section className="cs-current"><div className="cs-subhead"><h3>当前模拟状态</h3><span className="cs-tag">全段模拟 · 包含训练期</span></div><p>按当前所选参数从完整研究区间起点顺序运行，不能作为后段验证成绩，也不是实际 IBKR 持仓。</p>{study.marketSnapshotStatus?.signalsAllowed === false && <div className="cs-notice cs-snapshot-warning" role="status"><strong>暂停生成最新候选</strong><p>{study.marketSnapshotStatus.reason || "当前行情状态不允许生成新信号，请更新行情。"}</p><p>以下保留历史研究结果，数据截至 {study.asOf}。</p></div>}<dl className="cs-facts"><div><dt>账户状态</dt><dd>{state.label}</dd>{state.nextMode && <dd className="cs-next-mode">下一交易日：{nextModeNames[state.nextMode] || state.nextMode}</dd>}</div><div><dt>模拟净值 / 现金</dt><dd>{money(state.equity)} / {money(state.cash)}</dd></div><div><dt>本轮 / 总历史当前回撤</dt><dd>{pct(state.cycleDrawdownPct)} / {pct(state.globalDrawdownPct)}</dd></div><div><dt>模拟时段</dt><dd>{study.simulation.metrics.startDate} — {study.simulation.metrics.endDate}</dd></div></dl></section>
    <div className="cs-scope"><span>查看哪一段的操作记录</span><div className="cs-segmented" role="group" aria-label="操作记录研究窗口"><button aria-pressed={!full} onClick={() => setScope("validation")}>后段验证</button><button aria-pressed={full} onClick={() => setScope("full")}>全段模拟（含训练）</button></div></div>
    <OperationLedger key={`${requestKey}-${scope}`} ledger={full ? study.simulation : study.validationDetails} metrics={full ? study.simulation.metrics : study.validation} rules={study.rules} full={full} asOf={study.asOf}/>
    <details className="cs-method"><summary>数据口径、排除记录与研究局限</summary><p>前 200 根日线仅预热指标；共同日期的后续区间按 70% / 30% 划分训练与验证。仅训练选择本模板的 6 组预定义参数。两段各自从 10,000 美元现金开始，验证初始没有持仓。</p><p>期末不强制清仓，不扣尚未发生的退出费。现金不计利息，不融资；成本未包含税、真实券商最低佣金及市场冲击。反复调整目标后的验证期不是封存样本外数据。</p><ul>{[...new Set([...(study.warnings || []), ...(study.rules.warnings || [])])].map((warning, i) => <li key={i}>{warning}</li>)}</ul>{study.excluded?.length > 0 && <><h4>未纳入研究的股票</h4><ul>{study.excluded.map((entry, i) => <li key={i}>{typeof entry === "string" ? entry : `${entry.symbol}：${entry.reason}`}</li>)}</ul></>}<p>规则 {study.version} · 状态 {study.status} · 数据截至 {study.asOf}。</p></details>
  </div>;
}

export function CompleteStrategy({marketStamp,marketIssue=""}: {marketStamp: string;marketIssue?:string}) {
  const ready = useClientReady();
  return ready ? <CompleteStrategyContent marketStamp={marketStamp} marketIssue={marketIssue}/> : <div className="workspace-loading" role="status">正在载入策略偏好…</div>;
}

function CompleteStrategyContent({marketStamp,marketIssue}: {marketStamp: string;marketIssue:string}) {
  const [initial] = useState(readCompletePreferences);
  const [templateId, setTemplateId] = useState<TemplateId>(initial.templateId);
  const [riskProfile, setRiskProfile] = useState<ProfileId>(initial.riskProfile);
  const [target, setTarget] = useState(initial.target);
  const restored = true;
  const [retry, setRetry] = useState(0);
  const [catalogRetry, setCatalogRetry] = useState(0);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [stored, setStored] = useState<{key: string; study: Study} | null>(null);
  const [failure, setFailure] = useState<{key: string; message: string} | null>(null);
  const guide = useRef<HTMLDialogElement>(null);
  const annual = target.trim() ? Number(target) : NaN;
  const valid = finite(annual) && annual >= 0 && annual <= 200;
  const requestKey = JSON.stringify([templateId, riskProfile, target, marketStamp, retry]);
  const result = stored?.key === requestKey ? gateStudySignals(stored.study,marketIssue) : null;
  const error = failure?.key === requestKey ? failure.message : "";
  const pending = (!restored || valid) && !result && !error;
  const baseRules = catalog?.ruleSets[`${templateId}:${riskProfile}`] || null;
  const activeRules = result?.rules || baseRules;
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/strategy-rules", {signal: controller.signal}).then(async response => {
      const data: unknown = await response.json();
      if (!response.ok) throw new Error(errorMessage(data, "规则目录加载失败，请重试。"));
      const value = data as Catalog;
      if (!value.ruleSets || !Array.isArray(value.templates) || !Array.isArray(value.profiles)) throw new Error("规则目录格式不完整。");
      if (!controller.signal.aborted) {setCatalog(value); setCatalogError("");}
    }).catch(reason => {if (!controller.signal.aborted) setCatalogError(reason instanceof Error ? reason.message : "无法读取规则目录。");});
    return () => controller.abort();
  }, [catalogRetry]);
  useEffect(() => {
    if (!restored || !valid) return;
    try {localStorage.setItem(STORAGE_KEY, JSON.stringify({templateId, riskProfile, targetReturnPct: annual}));} catch { /* Keep controls functional without local storage. */ }
  }, [restored, valid, annual, templateId, riskProfile]);
  useEffect(() => {
    if (!restored || !valid) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch("/api/portfolio-study", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({templateId, riskProfile, targetReturnPct: annual}), signal: controller.signal});
        const payload: unknown = await response.json();
        if (!response.ok) throw new Error(errorMessage(payload, "研究服务暂时不可用。请更新行情后重新计算。"));
        const study = payload as Study;
        const profileId = typeof study.riskProfile === "object" ? study.riskProfile?.id : study.riskProfile;
        if (study.templateId !== templateId || profileId !== riskProfile || study.targets?.annualReturnPct !== annual || !study.training || !study.validation || !study.rules?.sections || !study.benchmarks?.spy || !study.benchmarks?.basket || !study.costStress || !study.simulation?.metrics || !study.simulation?.state || !study.validationDetails || !Array.isArray(study.equityCurve) || study.equityCurve.some(point => !finite(point.strategy) || !finite(point.spy) || !finite(point.basket))) throw new Error("没有收到与当前选择匹配的完整研究结果，请重新计算。");
        if (!controller.signal.aborted) setStored({key: requestKey, study});
      } catch (reason) {if (!controller.signal.aborted) setFailure({key: requestKey, message: reason instanceof Error ? reason.message : "无法连接本机研究服务。"});}
    }, 600);
    return () => {window.clearTimeout(timer); controller.abort();};
  }, [restored, valid, requestKey, annual, templateId, riskProfile]);
  return <section className="complete-strategy panel" aria-labelledby="complete-strategy-title"><div className="cs-head"><div><span className="cs-eyebrow"><FlaskConical size={16}/>完整量化规则 · 历史研究</span><h2 id="complete-strategy-title">选一套规则，看清每次操作</h2><p>从买入条件到退出与风控，使用同一个现金账户逐日模拟。</p></div><button className="cs-guide-button" onClick={() => guide.current?.showModal()}><BookOpen size={18}/>使用说明</button></div>
    <div className="cs-notice cs-data-warning"><strong>原始价格探索回测，未处理拆股和分红</strong><span>research_only · 没有自动下单 · 与实际 IBKR 账户独立</span></div>
    <div className="cs-template-grid" role="group" aria-label="完整策略模板">{templates.map(template => {const definition = catalog?.templates.find(item => item.id === template.id); return <button key={template.id} className={`cs-template cs-template-${template.id}`} aria-pressed={templateId === template.id} onClick={() => setTemplateId(template.id)}><span className="cs-template-top"><span>{template.number}</span>{templateId === template.id ? <Check size={18}/> : <ChevronRight size={18}/>}</span><strong>{definition?.label || template.label}</strong><p>{definition?.description || template.description}</p></button>;})}</div>
    <div className="cs-controls"><fieldset><legend>风险档位 <span>同时约束仓位、初始风险和暂停阈值</span></legend><div className="cs-profile-grid">{profiles.map(profile => {const definition = catalog?.profiles.find(item => item.id === profile.id); return <button key={profile.id} aria-pressed={riskProfile === profile.id} onClick={() => setRiskProfile(profile.id)}><strong>{definition?.label || profile.label}</strong><span>{definition ? `回撤目标 ${pct(definition.drawdownPct)}` : "风险限额载入中"}</span></button>;})}</div></fieldset><div className="cs-return-target"><label htmlFor="complete-return-target">目标年化收益 <span>训练筛选目标</span></label><div><input id="complete-return-target" type="number" min={0} max={200} step={1} value={target} onChange={event => setTarget(event.target.value)} aria-invalid={restored && !valid} aria-describedby="complete-target-help"/><b>%</b></div><p id="complete-target-help">0–200%；目标越高也不能突破风险限额。</p></div></div>
    <p className="cs-autosave">选择会保存在本机，修改后自动重新计算。每个模板只在 6 组预定义参数中用训练数据选择，不能保证达到收益目标。</p>
    {catalogError && <div className="cs-error" role="alert"><span>基础规则目录：{catalogError}</span><button onClick={() => setCatalogRetry(value => value + 1)}>重试规则目录</button></div>}
    {!result && baseRules && <details className="cs-rules"><summary>查看当前模板与档位的基础规则（非本次选参结果）</summary><RuleSummary rules={baseRules} selected={false}/><RuleSections rules={baseRules}/></details>}
    {restored && !valid && <p className="cs-error" role="alert">请输入 0–200% 的目标年化收益。有效选择后会自动重新计算。</p>}
    {pending && <div className="cs-loading" role="status"><LoaderCircle className="spin" size={23}/><div><strong>正在按当前选择重新研究…</strong><p>比较训练候选，再计算验证、基准、成本压力和全段操作明细。</p></div></div>}
    {error && <div className="cs-error" role="alert"><div><strong>本次研究未完成</strong><p>{error}</p><p>未生成新的交易候选，旧结果不会作为当前选择的结果展示。</p>{stored && <p>上次完成的历史研究数据截至 {stored.study.asOf}，行情更新时间（北京时间）{beijingTime(stored.study.marketGeneratedAt)}。当前仅保留时间标记。</p>}</div><button onClick={() => setRetry(value => value + 1)}><RefreshCw size={16}/>重新计算</button></div>}
    {result && <StudyResults key={requestKey} study={result} requestKey={requestKey}/>}
    <dialog ref={guide} className="cs-guide-dialog" aria-labelledby="complete-guide-title" onClick={event => {if (event.target === event.currentTarget) {const bounds = event.currentTarget.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) guide.current?.close();}}}><Guide rules={activeRules} selected={Boolean(result)} close={() => guide.current?.close()}/></dialog>
  </section>;
}
