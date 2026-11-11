"use client";

import {Fragment, useEffect, useId, useRef, useState} from "react";
import {Check, ChevronDown, ChevronRight, CircleHelp, Clock3, EyeOff, LoaderCircle, RefreshCw, ShieldCheck, ShieldX} from "lucide-react";
import {readCompletePreferences} from "./strategy-preferences";
import {useClientReady} from "./use-client-ready";
import {parameterText, type ProfileId, type TemplateId} from "./complete-strategy-types";
import type {HoldingsCheck, HoldingsDataIssue, HoldingsDecisionKind, HoldingsPlanData, HoldingsPlanItem, HoldingsPlanRequest, HoldingsScenario} from "./holdings-plan-types";
import {getHoldingsFreshness, getHoldingsRowState, parseHoldingsPlanPreferences} from "./holdings-plan-types";
import "./holdings-plan.css";

type Props = {marketStamp: string; marketIssue?: string; hidden: boolean; onRefreshMarket?: () => void; onShowRisk?: () => void};
const mask = "••••";
const preferencesKey = "northstar-holdings-plan-v1";
let preferenceFallback: string | null = null;
const readPreferences = () => {
  const fallback = {...readCompletePreferences(), allowProfitAdd: true};
  try {return parseHoldingsPlanPreferences(preferenceFallback ?? localStorage.getItem(preferencesKey), fallback);}
  catch {return parseHoldingsPlanPreferences(preferenceFallback, fallback);}
};
const decisionLabels: Record<HoldingsDecisionKind, string> = {verify: "数据未齐", review_exit: "复核退出", review_reduce: "复核减仓", hold: "继续观察", watch_add: "观察盈利加仓"};
const number = (value: number | null | undefined, digits = 2) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", {maximumFractionDigits: digits}) : "—";
const percent = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) ? `${number(value, 1)}%` : "—";
const amount = (value: number | null | undefined, currency: string | null = "USD", digits = 2) => typeof value === "number" && Number.isFinite(value) ? `${currency === "USD" ? "$" : `${currency || "币种待核验"} `}${number(value, digits)}` : "—";
const timestamp = (value: string | null | undefined) => {
  if (!value) return "未提供";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间待核验" : date.toLocaleString("zh-CN", {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false});
};
const checkFieldNames: Record<string, string> = {accountFresh: "账户新鲜", marketFresh: "行情新鲜", availableFunds: "可用资金", excessLiquidity: "超额流动性"};
const checkValue = (value: HoldingsCheck["observed"]): string => value === null ? "未核验" : typeof value === "boolean" ? value ? "是" : "否" : typeof value === "number" ? number(value, 4) : typeof value === "object" ? Object.entries(value).map(([key, entry]) => `${checkFieldNames[key] || key} ${checkValue(entry)}`).join(" · ") : value;
const instrumentNames: Record<string, string> = {stock: "普通股票", etf: "ETF", leveraged_etf_2x: "2 倍杠杆 ETF", unverified: "合约待核验"};
const errorText = (value: unknown) => {
  if (value && typeof value === "object") {
    const payload = value as {detail?: unknown; error?: unknown};
    if (typeof payload.detail === "string") return payload.detail;
    if (typeof payload.error === "string") return payload.error;
  }
  return "持仓计划暂时无法取得，请稍后重试。";
};

function PlanGuide({onRefreshMarket, onShowRisk}: Pick<Props, "onRefreshMarket" | "onShowRisk">) {
  return <section className="hp-guide" id="hp-use-guide" aria-labelledby="hp-guide-heading"><h3 id="hp-guide-heading">持仓计划怎么用</h3><ol>
    <li><strong>先看两个日期</strong><p>账户是人工同步的持仓快照，日线是已完成交易日。账户过时要人工同步，行情缺失或过时则刷新行情。</p></li>
    <li><strong>固定一套规则</strong><p>选定模板与风险档后持续按同一口径复核，不为当天结果频繁切换。允许盈利加仓也始终禁止亏损补仓。</p></li>
    <li><strong>先检查账户限制</strong><p>先看现金、总敞口与集中度。有融资或超限时先复核风险；技术条件通过也不会绕过账户限制。</p></li>
    <li><strong>展开单股四种情景</strong><p>分别看持有、加仓、减仓、退出。按“当前条件 / 下一步”补齐缺项，情景数量不能相加。</p></li>
    <li><strong>交易前再确认</strong><p>收盘后确认条件，下一常规交易时段核对实时价格和已有订单。“复核退出”表示条件触发，需要确认原计划，绝不等于立即全仓卖出。</p></li>
    <li><strong>手动处理后同步</strong><p>自行决定并在券商手动处理；成交后重新人工同步账户，再刷新计划。页面不会自动执行任何交易。</p></li>
  </ol><dl className="hp-guide-terms"><div><dt>“待核验 / 数据未齐”是什么？</dt><dd>缺少可靠输入，相关候选暂停。现在每行会直接写明缺项与下一步；历史不足、合约不支持无法仅靠刷新解决。</dd></div><div><dt>“缺止损信息”是什么？</dt><dd>真实止损与全账户未平仓风险未确认，无法算加仓股数；它本身不会封锁可用的持有、退出或减仓复核。参考防守价不是已有止损单。</dd></div><div><dt>动量轮动怎样用？</dt><dd>每周首个常规交易时段检查完整候选池排名。当前计划尚未核验周排名，不产生轮动加仓候选。</dd></div><div><dt>三种更新有何区别？</dt><dd>刷新计划只重算已有快照，刷新行情只更新公开行情。账户同步需在本次对话请求人工处理，页面暂无账户同步或止损确认按钮。</dd></div></dl>
    <div className="hp-guide-footer"><p>时分按北京时间显示，日线日期按美股交易日；未收盘日线不用于信号。历史回测与本页实际账户计划分别使用各自的数据范围。</p><div className="hp-action-links">{onRefreshMarket && <button className="hp-text-button" onClick={onRefreshMarket}><RefreshCw size={16}/>刷新行情</button>}{onShowRisk && <button className="hp-text-button" onClick={onShowRisk}><ShieldCheck size={16}/>查看融资与账户</button>}</div></div></section>;
}

function AccountGate({plan, hidden, blocked, onShowRisk}: {plan: HoldingsPlanData; hidden: boolean; blocked: boolean; onShowRisk?: () => void}) {
  const {account, settings, summary} = plan;
  const money = (value: number | null) => hidden ? mask : amount(value, account.currency, 0);
  const borrowing = account.cash !== null && account.cash < 0;
  return <section className={`hp-account ${summary.addBlocked || blocked ? "hp-account-blocked" : ""}`} aria-labelledby="hp-account-heading">
    <div className="hp-section-head"><h2 id="hp-account-heading"><ShieldCheck size={21}/>账户限制</h2><div className="hp-action-links">{onShowRisk && <button className="hp-text-button" onClick={onShowRisk}>融资与账户明细<ChevronRight size={16}/></button>}<span className={`hp-badge ${summary.addBlocked || blocked ? "hp-badge-peach" : "hp-badge-blue"}`}>{blocked ? "数据未齐 · 暂停候选" : summary.addBlocked ? "加仓已暂停" : "继续检查个股条件"}</span></div></div>
    <div className="hp-account-grid">
      <div className="hp-account-stat hp-stat-blue"><span>净资产</span><strong>{money(account.netLiquidation)}</strong><small>{account.currency} · IBKR 快照</small></div>
      <div className="hp-account-stat hp-stat-peach"><span>现金 / 融资状态</span><strong>{money(account.cash)}</strong><small>{borrowing ? "现金为负，存在融资借款" : "现金余额仍需与币种明细核对"}</small></div>
      <div className="hp-account-stat hp-stat-lilac"><span>总敞口 / 本档上限</span><strong>{hidden ? mask : percent(account.grossExposurePct)} <em>/ {percent(settings.caps.grossCapPct)}</em></strong><small>超额名义市值 {money(account.grossExcessValue)}</small></div>
      <div className="hp-account-stat hp-stat-blue"><span>半导体相关 / 本档上限</span><strong>{hidden ? mask : percent(account.semiconductorExposurePct)} <em>/ {percent(settings.caps.sectorCapPct)}</em></strong><small>超额名义市值 {money(account.semiconductorExcessValue)}</small></div>
    </div>
    <dl className="hp-account-facts"><div><dt>可用资金</dt><dd>{money(account.availableFunds)}</dd></div><div><dt>超额流动性</dt><dd>{money(account.excessLiquidity)}</dd></div><div><dt>单股上限</dt><dd>{percent(settings.caps.singleCapPct)} 净资产</dd></div><div><dt>加仓数量所需资料</dt><dd>{account.openRiskConfirmed ? "止损与持仓风险已核验" : "缺真实止损与持仓风险资料"}</dd></div></dl>
    {summary.addBlockReasons.length > 0 && <details className="hp-gate-reasons"><summary>查看 {summary.addBlockReasons.length} 项加仓限制</summary><ul>{summary.addBlockReasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></details>}
  </section>;
}

function Scenario({scenario, hidden, currency, blocked, rotation, openRiskConfirmed}: {scenario: HoldingsScenario; hidden: boolean; currency: string | null; blocked: boolean; rotation: boolean; openRiskConfirmed: boolean}) {
  const rankingMissing = scenario.id === "add" && rotation;
  const names = {hold: "持有", add: "盈利加仓", reduce: "减仓", exit: "退出"};
  const quantity = blocked ? "数据未齐" : rankingMissing ? "待周排名" : scenario.id === "add" && scenario.quantity === null ? openRiskConfirmed ? "尚未计算" : "缺止损信息" : number(scenario.quantity, 6);
  return <section className={`hp-scenario hp-scenario-${scenario.id}`}>
    <div className="hp-scenario-title"><h4>{names[scenario.id]}</h4><span className={scenario.triggered && !blocked && !rankingMissing ? "hp-condition-hit" : ""}>{blocked ? "数据未齐" : rankingMissing ? "周排名未确认" : scenario.triggered ? "条件已触发" : "等待条件"}</span></div>
    <ul>{scenario.conditions.map((condition, index) => <li key={index}>{condition}</li>)}</ul>
    <div className="hp-scenario-values"><div><span>参考价</span><b>{blocked ? "暂停参考" : amount(scenario.referencePrice, currency)}</b></div><div><span>{scenario.id === "add" ? "加仓股数" : scenario.id === "reduce" ? "减仓参考数量" : "参考数量"}</span><b>{hidden ? mask : quantity}</b></div></div>
    <p className="hp-scenario-note">{scenario.quantityNote}</p>{rankingMissing && <p className="hp-scenario-timing">每周首个常规交易时段，先核对完整候选池排名。</p>}
  </section>;
}

function PositionDetails({item, hidden, blocked, issue, rotation, openRiskConfirmed}: {item: HoldingsPlanItem; hidden: boolean; blocked: boolean; issue?: HoldingsDataIssue; rotation: boolean; openRiskConfirmed: boolean}) {
  return <div className="hp-position-details">
    <div className="hp-detail-intro"><strong>{item.identity.symbol} · 四种情景</strong><span>分别评估，减仓与退出数量不能相加</span></div>
    {issue && <p className="hp-inline-warning"><strong>{issue.label}</strong> · {issue.action} 当前仅保留规则说明。</p>}
    <div className="hp-scenarios">{item.scenarios.map(scenario => <Scenario key={scenario.id} scenario={scenario} hidden={hidden} currency={item.identity.currency} blocked={blocked} rotation={rotation} openRiskConfirmed={openRiskConfirmed}/>)}</div>
    <p className="hp-detail-timing"><Clock3 size={15}/>{blocked ? "补齐上述数据后，再确认条件与时机。" : item.decision.timing}</p>
    <details className="hp-evidence"><summary>逐项检查与价格指标</summary><div className="hp-detail-columns"><section><h4>条件逐项核对</h4><div className="hp-checks">{item.checks.map((check, index) => <div className="hp-check" key={index}><span className={`hp-check-icon ${blocked || check.passed === null ? "hp-check-unknown" : check.passed ? "hp-check-pass" : "hp-check-fail"}`} aria-label={blocked || check.passed === null ? "未确认" : check.passed ? "满足" : "未满足"}>{blocked || check.passed === null ? <CircleHelp size={16}/> : check.passed ? <Check size={16}/> : <ShieldX size={16}/>}</span><div><b>{check.label}</b><p>当前：{hidden ? mask : checkValue(check.observed)}<br/>要求：{hidden ? mask : checkValue(check.required)}</p></div></div>)}</div></section>
      <section><h4>价格与指标参考</h4><dl className="hp-levels">{item.levels.map((level, index) => <div key={index}><dt>{level.label}<small>{level.explanation}</small></dt><dd>{blocked ? "暂停参考" : number(level.value, 4)}</dd></div>)}</dl><p className="hp-note">参考防守价 = 已完成日线收盘 − 2 × ATR14，只是新设计划的参考，不代表已有止损委托。</p></section></div></details>
    <details className="hp-data-gaps"><summary>数据缺项与执行边界 · {number(item.dataStatus.bars, 0)} 根日线</summary><p>{item.dataStatus.label} · 日线截至 {timestamp(item.dataStatus.marketAsOf)}</p>{Boolean(item.dataStatus.issues?.length) && <ul>{item.dataStatus.issues?.map((dataIssue, index) => <li key={index}><strong>{dataIssue.label}</strong>：{dataIssue.action}</li>)}</ul>}<ul>{item.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul><p>原始入场日期、入场以来最高价、真实止损及连续账户净值尚未完整核验，因此不推定持有期退出、真实移动止损、账户历史回撤或冷却期。</p></details>
  </div>;
}

function HoldingsTable({plan, hidden, currentIssue}: {plan: HoldingsPlanData; hidden: boolean; currentIssue?: HoldingsDataIssue}) {
  const [expanded, setExpanded] = useState<string[]>([]);
  const idPrefix = useId();
  const allExpanded = plan.items.length > 0 && expanded.length === plan.items.length;
  const toggle = (key: string) => setExpanded(previous => previous.includes(key) ? previous.filter(value => value !== key) : [...previous, key]);
  const blocked = Boolean(currentIssue);
  return <section className="hp-table-panel" aria-labelledby="hp-table-heading"><div className="hp-section-head"><div><h2 id="hp-table-heading">逐项持仓计划 <span>{plan.summary.positionCount} 项</span></h2><p>范围仅含 IBKR 非零实际头寸；数量与成本来自账户快照。</p></div><button className="hp-text-button" onClick={() => setExpanded(allExpanded ? [] : plan.items.map(item => item.identity.key))}>{allExpanded ? "收起全部条件" : "展开全部条件"}<ChevronDown size={17}/></button></div>
    <div className="hp-counts">{(Object.entries(decisionLabels) as [HoldingsDecisionKind, string][]).map(([kind, label]) => <span key={kind} className={`hp-count hp-count-${kind}`}>{label}<b>{blocked ? kind === "verify" ? plan.summary.positionCount : 0 : plan.summary.counts[kind]}</b></span>)}</div>
    <div className="hp-table-scroll" tabIndex={0} role="region" aria-label="实际持仓条件表，可横向滚动"><table className="hp-table"><thead><tr><th scope="col">持仓标的</th><th scope="col">数量 / 成本</th><th scope="col">券商价 / 市值</th><th scope="col">日线收盘 / 日期</th><th scope="col">净值占比</th><th scope="col">当前条件 / 下一步</th><th scope="col"><span className="hp-sr-only">展开条件</span></th></tr></thead><tbody>{plan.items.map((item, index) => {
      const position = item.identity;
      const open = expanded.includes(position.key);
      const row = getHoldingsRowState(item, currentIssue);
      const waiting = row.blocked;
      const kind = row.kind;
      const close = item.levels.find(level => /收盘/.test(level.label));
      const detailId = `${idPrefix}-position-${index}`;
      return <Fragment key={position.key}><tr className={open ? "hp-row-expanded" : ""}><th scope="row"><button className="hp-symbol" onClick={() => toggle(position.key)} aria-expanded={open} aria-controls={open ? detailId : undefined}><strong>{position.symbol}</strong><small>{position.name || position.instrumentType}</small></button><span className="hp-instrument">{position.currency || "币种待核验"} · {instrumentNames[position.instrumentType] || position.assetClass || "类型待核验"}</span></th><td><b>{hidden ? mask : number(position.quantity, 6)}{!hidden && <small className="hp-inline-unit"> 股</small>}</b><small>成本 {hidden ? mask : amount(position.averageCost, position.currency)}</small></td><td><b>{amount(position.brokerPrice, position.currency)}</b><small>市值 {hidden ? mask : amount(position.brokerMarketValue, position.currency, 0)}</small></td><td><b>{amount(close?.value, position.currency)}</b><small>{timestamp(item.dataStatus.marketAsOf)}</small></td><td><b>{hidden ? mask : percent(position.weightPct)}</b><small>上限 {percent(plan.settings.caps.singleCapPct)}</small></td><td className="hp-decision-cell"><span className={`hp-decision hp-decision-${kind}`}>{row.label}</span><p>{waiting ? `下一步：${row.summary}` : row.summary}</p></td><td><button className="hp-expand" aria-label={`${open ? "收起" : "展开"} ${position.symbol} 的持有、加仓、减仓与退出条件`} aria-expanded={open} aria-controls={open ? detailId : undefined} onClick={() => toggle(position.key)}>{open ? <ChevronDown size={19}/> : <ChevronRight size={19}/>}</button></td></tr>{open && <tr className="hp-details-row"><td colSpan={7}><div id={detailId}><PositionDetails item={item} hidden={hidden} blocked={waiting} issue={row.issue} rotation={plan.settings.templateId === "rotation"} openRiskConfirmed={plan.account.openRiskConfirmed}/></div></td></tr>}</Fragment>;
    })}</tbody></table>{plan.items.length === 0 && <p className="hp-empty">当前账户快照没有非零实际头寸。</p>}</div>
  </section>;
}

function PlanResult({request, marketStamp, marketIssue, hidden, retry, onRefreshMarket, onShowRisk}: {request: HoldingsPlanRequest; marketStamp: string; marketIssue: string; hidden: boolean; retry: () => void} & Pick<Props, "onRefreshMarket" | "onShowRisk">) {
  const [plan, setPlan] = useState<HoldingsPlanData | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now);
  const sequence = useRef(0);
  const {templateId, riskProfile, allowProfitAdd} = request;
  const accountAsOf = plan?.account.effectiveAsOf || plan?.accountAsOf;
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const recheck = () => {
      clearTimeout(timer);
      const current = Date.now();
      setNow(current);
      const next = getHoldingsFreshness(accountAsOf, marketStamp, current).nextExpiry;
      if (next !== null) timer = setTimeout(recheck, Math.max(1, next - current));
    };
    timer = setTimeout(recheck, 0);
    window.addEventListener("focus", recheck);
    document.addEventListener("visibilitychange", recheck);
    return () => {clearTimeout(timer); window.removeEventListener("focus", recheck); document.removeEventListener("visibilitychange", recheck);};
  }, [accountAsOf, marketStamp]);
  useEffect(() => {
    const controller = new AbortController();
    const current = ++sequence.current;
    fetch("/api/holdings-plan", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({templateId, riskProfile, allowProfitAdd}), signal: controller.signal}).then(async response => {
      const payload: unknown = await response.json();
      if (!response.ok) throw new Error(errorText(payload));
      const data = payload as HoldingsPlanData;
      if (!data || !Array.isArray(data.items) || !data.account || !data.settings || !data.summary || !Array.isArray(data.warnings) || !Array.isArray(data.sources)) throw new Error("持仓计划数据不完整，请重新获取。");
      if (!controller.signal.aborted && sequence.current === current) {setNow(Date.now()); setPlan(data);}
    }).catch(cause => {
      if (!controller.signal.aborted && sequence.current === current) setError(cause instanceof Error ? cause.message : "持仓计划获取失败。");
    });
    return () => {controller.abort();};
  }, [templateId, riskProfile, allowProfitAdd]);
  if (error) return <div className="hp-state hp-error" role="alert"><ShieldX size={25}/><div><strong>计划获取失败，候选已暂停</strong><p>{error}</p><p>本次未生成操作候选；重新获取成功后再查看条件。</p></div><button className="hp-button" onClick={retry}><RefreshCw size={17}/>重试计划</button></div>;
  if (!plan) return <div className="hp-state hp-loading" role="status" aria-busy="true"><LoaderCircle className="spin" size={24}/><div><strong>正在重新检查持仓与行情，旧候选已暂停</strong><p>{marketIssue || "账户限额、行情有效性和四种操作条件会一并检查。"}</p></div></div>;
  const freshness = getHoldingsFreshness(accountAsOf, marketStamp, now);
  const accountExpired = !freshness.accountFresh || !plan.account.fresh;
  const marketExpired = !freshness.marketFresh;
  const liveIssues: HoldingsDataIssue[] = [
    ...(marketIssue ? [{code: "market_refresh_issue", label: "行情同步未完成", action: marketIssue}] : []),
    ...(accountExpired ? [{code: "account_stale", label: "账户快照过时或时间未确认", action: "在本次对话请求人工同步 IBKR，再刷新计划；页面不能同步账户。"}] : []),
    ...(marketExpired ? [{code: "market_stale", label: "行情快照过时或时间未确认", action: "刷新行情后，再查看持仓计划。"}] : []),
    ...(plan.dataIssues || []),
  ];
  const issues = liveIssues.filter((issue, index) => liveIssues.findIndex(entry => entry.code === issue.code) === index);
  const currentIssue = issues[0];
  const blocked = Boolean(currentIssue);
  return <div className="hp-results">
    <div className="hp-data-dates"><span><Clock3 size={15}/>账户快照 <b>{timestamp(plan.accountAsOf)}</b>{accountExpired && <em>待同步</em>}</span><span>完整日线 <b>{timestamp(plan.marketAsOf)}</b>{marketExpired && <em>快照已过时</em>}</span><span>计划计算 <b>{timestamp(plan.generatedAt)}</b></span></div>
    {issues.length > 0 && <div className="hp-inline-warning hp-issues" role="status"><div><strong>先补齐数据，再判断操作条件</strong><ul>{issues.map(issue => <li key={issue.code}><b>{issue.label}</b>：{issue.action}</li>)}</ul></div>{onRefreshMarket && issues.some(issue => !issue.code.startsWith("account_")) && <button className="hp-text-button" onClick={onRefreshMarket}><RefreshCw size={16}/>刷新行情</button>}</div>}
    <AccountGate plan={plan} hidden={hidden} blocked={blocked} onShowRisk={onShowRisk}/>
    <HoldingsTable plan={plan} hidden={hidden} currentIssue={currentIssue}/>
    <details className="hp-method"><summary>规则参数、数据来源与计划边界</summary><div><p>本次采用 {plan.settings.templateLabel} · {plan.settings.riskLabel}。{parameterText(plan.settings.parameters)}。规则版本 {plan.version}。</p><p>单笔风险预算 {percent(plan.settings.caps.tradeRiskPct)}，未平仓初始风险上限 {percent(plan.settings.caps.openRiskCapPct)}。实际止损及已有持仓风险未核验前，不给出加仓数量。这些限额不能保证最大亏损。</p><ul>{plan.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul><div className="hp-sources">{plan.sources.map((source, index) => <div key={index}><b>{source.name}</b><span>{timestamp(source.asOf)}</span><p>{source.detail}</p></div>)}</div><p>账户敞口以净资产为分母；超过 100% 代表名义持仓高于净资产。半导体分组含 NVDA、MRVL、AVGO、MU、SKHY、SKUU，SKUU 是杠杆产品，名义市值不等于相同风险。禁止负现金时加仓是本计划的风险规则，不代表 IBKR 禁止所有融资交易。</p><p>本计划用于按明确条件复核实际持仓，不推定这些旧持仓原本按所选策略建仓，也不把原五股回测成绩当作全部实际持仓或盈利加仓规则的验证结果。</p></div></details>
  </div>;
}

function HoldingsPlanContent({marketStamp, marketIssue = "", hidden, onRefreshMarket, onShowRisk}: Props) {
  const [initial] = useState(readPreferences);
  const [templateId, setTemplateId] = useState<TemplateId>(initial.templateId);
  const [riskProfile, setRiskProfile] = useState<ProfileId>(initial.riskProfile);
  const [allowProfitAdd, setAllowProfitAdd] = useState(initial.allowProfitAdd);
  const [revision, setRevision] = useState(0);
  const [guideOpen, setGuideOpen] = useState(false);
  useEffect(() => {
    const saved = JSON.stringify({templateId, riskProfile, allowProfitAdd});
    preferenceFallback = saved;
    try {localStorage.setItem(preferencesKey, saved);} catch { /* Keep in-memory choices when storage is unavailable. */ }
  }, [templateId, riskProfile, allowProfitAdd]);
  // A new mounted result cannot display a response from before a refresh or failed market update.
  const requestKey = JSON.stringify([templateId, riskProfile, allowProfitAdd, marketStamp, marketIssue, revision]);
  const retry = () => setRevision(previous => previous + 1);
  return <section className="hp-workspace" aria-label="实际持仓行动计划">
    <div className="hp-intro hp-intro-compact"><div><h2>实际持仓计划</h2><p>收盘后检查条件 · 禁止亏损补仓 · 不会自动下单</p></div><button className="hp-text-button" aria-expanded={guideOpen} aria-controls={guideOpen ? "hp-use-guide" : undefined} onClick={() => setGuideOpen(value => !value)}><CircleHelp size={18}/>{guideOpen ? "收起使用说明" : "怎么用"}</button></div>
    {guideOpen && <PlanGuide onRefreshMarket={onRefreshMarket} onShowRisk={onShowRisk}/>}
    <div className="hp-controls"><label htmlFor="hp-template">计划模板<select id="hp-template" value={templateId} onChange={event => setTemplateId(event.target.value as TemplateId)}><option value="breakout">趋势突破</option><option value="pullback">趋势回调</option><option value="rotation">动量轮动</option></select></label><label htmlFor="hp-risk">风险档位<select id="hp-risk" value={riskProfile} onChange={event => setRiskProfile(event.target.value as ProfileId)}><option value="conservative">保守</option><option value="balanced">均衡</option><option value="aggressive">进取</option></select></label><label className="hp-profit-toggle"><input type="checkbox" checked={allowProfitAdd} onChange={event => setAllowProfitAdd(event.target.checked)}/><span>允许盈利后顺势加仓<small>仍须同时通过账户、行情和止损核验</small></span></label><button className="hp-button" onClick={retry}><RefreshCw size={17}/>刷新计划</button></div>
    <div className="hp-control-notes"><span>刷新计划仅重算已有快照 · 时分为北京时间，日线日期为美股交易日</span>{hidden && <span><EyeOff size={15}/>数量与金额已隐藏</span>}</div>
    <PlanResult key={requestKey} request={{templateId, riskProfile, allowProfitAdd}} marketStamp={marketStamp} marketIssue={marketIssue} hidden={hidden} retry={retry} onRefreshMarket={onRefreshMarket} onShowRisk={onShowRisk}/>
  </section>;
}

export function HoldingsPlan(props: Props) {
  const ready = useClientReady();
  return ready ? <HoldingsPlanContent {...props}/> : <section className="hp-workspace hp-state" role="status" aria-busy="true">正在载入持仓计划偏好…</section>;
}
