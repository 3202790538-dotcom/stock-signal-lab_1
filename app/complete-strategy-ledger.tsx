"use client";
import {useState} from "react";
import {type Condition, type Decision, type Ledger, type Position, type Rules, type Metrics, money, num, pct} from "./complete-strategy-types";

function observed(value: Condition["value"]) {
  return typeof value === "number" ? num(value, 3) : typeof value === "boolean" ? value ? "是" : "否" : value ?? "—";
}
export function Conditions({conditions}: {conditions: Condition[]}) {
  if (!conditions?.length) return <p className="cs-note">此记录没有单项条件；请查看决策原因。</p>;
  return <div className="cs-table-scroll" tabIndex={0} aria-label="条件观察值，可横向滚动"><table><thead><tr><th>判断条件</th><th>当时观察值</th><th>要求 / 阈值</th><th>满足</th></tr></thead><tbody>{conditions.map((condition, i) => <tr key={i}><td>{condition.label}</td><td>{observed(condition.value)}</td><td>{observed(condition.threshold)}</td><td>{condition.passed ? "是" : "否"}</td></tr>)}</tbody></table></div>;
}
const fillReasonNames: Record<string, string> = {
  entry: "满足信号后开盘买入", intraday_stop: "盘中触发有效止损", open_stop: "开盘跳空触发止损", holding_limit: "达到最长持有期限", trend_exit: "收盘确认跌破趋势均线", rotation_rank: "轮动排名落榜", risk_pause: "组合二级暂停，全部退出", drawdown_termination: "超过总回撤目标，终止窗口交易", single_cap: "单股超限减仓", sector_cap: "半导体行业超限减仓", gross_cap: "组合总仓位超限减仓", open_risk_cap: "未平仓初始风险超限减仓",
};
const reasonText = (reason: string) => fillReasonNames[reason] || reason;
function TradeDetail({position: p, rules, identity}: {position: Position; rules: Rules; identity: string}) {
  return <details className="cs-trade"><summary><span><b>{p.symbol}</b><span>{p.entryDate} → {p.exitDate || "期末未平仓"}</span></span><span><b>{money(p.netPnl)}</b><span>{p.exitDate ? "完整交易净盈亏" : `剩余 ${num(p.quantity, 0)} 股`} · 展开明细</span></span></summary>
    <div className="cs-trade-body"><p className="cs-note">{identity} · {rules.templateLabel} · 规则 {rules.version} · 头寸 {p.id}</p>
    <dl className="cs-facts"><div><dt>信号收盘日</dt><dd>{p.signalDate}</dd></div><div><dt>模拟买入日</dt><dd>{p.entryDate}</dd></div><div><dt>成交价 / 初始数量</dt><dd>{money(p.entryPrice)} / {num(p.initialQuantity, 0)} 股</dd></div><div><dt>初始止损 → 当前止损</dt><dd>{money(p.initialStop)} → {money(p.stop)}</dd></div><div><dt>已实现净盈亏</dt><dd>{money(p.realizedPnl)}</dd></div><div><dt>未实现净盈亏</dt><dd>{money(p.unrealizedPnl)}</dd></div><div><dt>买入手续费 / 滑点成本</dt><dd>{money(p.entryFee)} / {money(p.entrySlippage)}</dd></div><div><dt>最终退出原因</dt><dd>{p.exitReason ? reasonText(p.exitReason) : "未完全退出，按期末收盘估值"}</dd></div></dl>
    <h4>信号条件 · 观察值与阈值</h4><Conditions conditions={p.conditions}/>
    <h4>如何算出买入股数</h4><p className="cs-note">股数 = 向下取整（风险预算、单股 / 行业 / 总仓位、未平仓风险、可用现金、成交量限额所允许股数的最小值）。每股初始风险 = 成交价 − 初始止损；移动止损不会释放初始风险预算。</p>
    <dl className="cs-facts"><div><dt>计算时策略净值</dt><dd>{money(p.sizing.equity)}</dd></div><div><dt>本笔风险预算</dt><dd>{money(p.sizing.riskBudget)}</dd></div><div><dt>初始每股止损距离</dt><dd>{money(p.initialRiskPerShare)}</dd></div><div><dt>风险预算 ÷ 每股距离</dt><dd>{num(p.initialRiskPerShare > 0 ? p.sizing.riskBudget / p.initialRiskPerShare : null, 3)} 股</dd></div></dl>
    <div className="cs-table-scroll" tabIndex={0} aria-label="仓位数量限额"><table><thead><tr><th>数量限制</th><th>此项允许股数</th></tr></thead><tbody>{p.sizing.limits.map((limit, i) => <tr key={i}><td>{limit.label}</td><td>{num(limit.shares, 3)}</td></tr>)}</tbody></table></div><p className="cs-highlight">最限制数量的条件：{p.sizing.binding}；最终买入 {num(p.sizing.quantity, 0)} 股。</p>
    <h4>每次成交 · 减仓与完全退出归入同一笔交易</h4><div className="cs-table-scroll" tabIndex={0} aria-label="模拟成交流水，可横向滚动"><table><thead><tr><th>日期</th><th>方向</th><th>股数</th><th>参考价</th><th>成交价</th><th>手续费</th><th>滑点成本</th><th>原因</th></tr></thead><tbody>{p.fills.map((fill, i) => <tr key={i}><td>{fill.date}</td><td>{fill.side.toLowerCase() === "buy" ? "买入" : "卖出"}</td><td>{num(fill.quantity, 0)}</td><td>{money(fill.referencePrice)}</td><td>{money(fill.price)}</td><td>{money(fill.fee)}</td><td>{money(fill.slippage)}</td><td>{reasonText(fill.reason)}</td></tr>)}</tbody></table></div>
    <details className="cs-inner-details"><summary>止损调整记录 · {p.stopHistory.length} 条</summary><p className="cs-note">收盘更新的止损下一交易日生效；开盘跳空可能超过止损预算。</p><div className="cs-table-scroll cs-long-table" tabIndex={0} aria-label="移动止损记录"><table><thead><tr><th>更新日期</th><th>原止损</th><th>更新后止损</th><th>原因</th></tr></thead><tbody>{p.stopHistory.map((stop, i) => <tr key={i}><td>{stop.date}</td><td>{money(stop.previous)}</td><td>{money(stop.stop)}</td><td>{stop.reason}</td></tr>)}</tbody></table></div></details>
    <p className="cs-note">手续费已计入净盈亏，滑点已经反映在成交价中，不再次扣除。期末未平仓不计入胜率，也不扣未发生的退出费。</p></div>
  </details>;
}
const statusName: Record<string, string> = {conditional: "待周度执行确认", candidate: "下一日候选", eligible: "条件满足", skipped: "跳过", rejected: "未满足", entered: "已模拟买入", filled: "已模拟成交", held: "已有持仓", pending: "等待下一交易日", no_signal: "没有信号", blocked: "暂停买入", signal: "信号成立"};
function DecisionDetail({decision}: {decision: Decision}) {
  return <details className="cs-decision"><summary><time>{decision.date}</time><b>{decision.symbol || "组合"}</b><span>{statusName[decision.status] || decision.status}</span><span>{decision.reason}</span></summary><div className="cs-trade-body"><Conditions conditions={decision.conditions}/></div></details>;
}
export function OperationLedger({ledger, metrics, rules, full, asOf}: {ledger: Ledger; metrics: Metrics; rules: Rules; full: boolean; asOf: string}) {
  const [tab, setTab] = useState("open");
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState("");
  const identity = `${full ? "全段模拟（含训练期）" : "后段验证"} ${metrics.startDate} — ${metrics.endDate}，数据截至 ${asOf}`;
  const tabs = [{id: "open", name: "期末持仓", count: ledger.openPositions.length}, {id: "trades", name: "已平仓交易", count: ledger.trades.length}, {id: "events", name: "风控事件", count: ledger.events.length}, {id: "decisions", name: "信号与跳过原因", count: ledger.decisions.length}, ...(full ? [{id: "latest", name: "最新信号检查", count: ledger.latestSignals?.length || 0}] : [])];
  const positions = tab === "trades" ? [...ledger.trades].reverse() : ledger.openPositions;
  const decisions = tab === "latest" ? ledger.latestSignals || [] : [...ledger.decisions].reverse();
  const matches = (item: unknown) => JSON.stringify(item).toLowerCase().includes(filter.toLowerCase());
  const filteredPositions = positions.filter(matches), filteredDecisions = decisions.filter(matches), events = [...ledger.events].reverse().filter(matches);
  const count = tab === "open" || tab === "trades" ? filteredPositions.length : tab === "events" ? events.length : filteredDecisions.length;
  const maxPage = Math.max(0, Math.ceil(count / 8) - 1), currentPage = Math.min(page, maxPage);
  const slice = <T,>(items: T[]) => items.slice(currentPage * 8, currentPage * 8 + 8);
  return <section className="cs-ledger" aria-label="模拟操作明细"><div className="cs-subhead"><h3>操作明细</h3><span className="cs-tag">{full ? "全段模拟 · 包含训练期" : "后段历史验证"}</span></div><p className="cs-note">{identity}。点击记录可核对条件、股数、费用和止损；这些是系统模拟记录，不是 IBKR 历史委托。</p>
    <div className="cs-segmented cs-ledger-tabs" role="group" aria-label="操作记录类别">{tabs.map(item => <button key={item.id} aria-pressed={tab === item.id} onClick={() => {setTab(item.id); setPage(0); setFilter("");}}>{item.name}<span>{item.count}</span></button>)}</div>
    <label className="cs-search">筛选本类记录<input type="search" placeholder="股票代码、日期或原因" value={filter} onChange={event => {setFilter(event.target.value); setPage(0);}}/></label>
    {tab === "latest" && <p className="cs-highlight">截至最后一个已完成交易日逐股检查，记录数量不是买入信号数量。只有条件满足的才进入候选，下一交易日仍须检查成交及额度；未提交实际订单。</p>}
    {tab === "open" && <p className="cs-note">{full ? "这是按所选参数从全段起点运行到最新日期的模拟持仓。" : "这是验证窗口独立账户的期末持仓。"}实际 IBKR 持仓请查看账户页并手动同步。</p>}
    {!count && <div className="cs-empty">{filter ? "没有匹配的记录。" : tab === "trades" ? "此窗口没有完整平仓交易。可查看持仓、信号与跳过原因；胜率和盈亏比尚无可用样本。" : tab === "open" ? "期末没有模拟持仓，资金保留为现金。可查看信号与跳过原因。" : tab === "events" ? "此窗口没有账户级风控事件。" : "没有可显示的决策记录。"}</div>}
    {(tab === "open" || tab === "trades") && slice(filteredPositions).map(p => <TradeDetail key={p.id} position={p} rules={rules} identity={identity}/>)}
    {tab === "events" && slice(events).map((event, i) => <article className="cs-event" key={`${event.date}-${i}`}><div><time>{event.date}</time><b>{event.label}</b></div><p>{event.description}</p><span>触发净值 {money(event.equity)} · 回撤 {pct(event.drawdownPct)}{event.thresholdPct !== undefined ? ` · 阈值 ${pct(event.thresholdPct)}` : ""}</span></article>)}
    {(tab === "decisions" || tab === "latest") && slice(filteredDecisions).map((decision, i) => <DecisionDetail key={`${decision.date}-${decision.symbol}-${i}`} decision={decision}/>)}
    {count > 8 && <nav className="cs-pagination" aria-label="操作明细分页"><button disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>上一页</button><span>{currentPage + 1} / {maxPage + 1} 页 · {count} 条</span><button disabled={currentPage >= maxPage} onClick={() => setPage(currentPage + 1)}>下一页</button></nav>}
  </section>;
}
