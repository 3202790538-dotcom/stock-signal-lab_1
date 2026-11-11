"use client";

import { useState } from "react";
import { Calculator, Check, ChevronDown, ChevronUp, CircleHelp, Landmark, ShieldCheck } from "lucide-react";
import "./risk-panel.css";

type Amount = number | null;
type CashBalance = {
  currency: string; cash: Amount; settledCash: Amount; debit: Amount;
  currentDebit?: Amount; exchangeRate: Amount; baseDebit: Amount;
};
type RiskPosition = {
  symbol: string; assetClass: string; currency: string; quantity: Amount;
  marketValue: Amount; baseMarketValue: Amount; marketPrice: Amount;
  pnl: Amount; weightPct: Amount; covered: boolean;
};
export type AccountRisk = {
  asOf: string; currency: string; netLiquidation: Amount; equityWithLoanValue: Amount;
  initialMargin: Amount; maintenanceMargin: Amount; availableFunds: Amount;
  excessLiquidity: Amount; buyingPower: Amount; grossPositionValue: Amount;
  cash: Amount; leverage: Amount; cushionPct: Amount; maintenanceUsagePct: Amount;
  cashDebtRatioPct: Amount; stockExposure: Amount; stressCoveragePct: Amount;
  cashBalancesAvailable: boolean; positionsAvailable: boolean; debtByCurrencyAvailable: boolean;
  allPositions: RiskPosition[]; cashBalances: CashBalance[];
  omittedPositions: { symbol: string; assetClass: string; currency: string; reason: string }[];
  excessLiquiditySource?: string; availableFundsSource?: string;
  reconciliation: { computedExcess: Amount; difference: Amount; computedAvailable: Amount; availableDifference: Amount;positionsGrossValue?:Amount;grossValueDifference?:Amount };
  warnings: string[];
  scenarios: { shockPct: number; pnl: Amount; netLiquidation: Amount; equityWithLoanValue: Amount; excessLiquidity: Amount; cushionPct: Amount; leverage: Amount }[];
};

const valid = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const fmt = (value: Amount, currency: string, digits = 0) => {
  if (!valid(value)) return "—";
  try { return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value); }
  catch { return `${value.toLocaleString("en-US", { maximumFractionDigits: digits })} ${currency}`; }
};
const percent = (value: Amount, digits = 1) => valid(value) ? `${value.toFixed(digits)}%` : "—";
const snapshotTime = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未提供" : date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
};

export function RiskPanel({ risk, hidden, accountSource }: { risk: AccountRisk; hidden: boolean; accountSource?: string }) {
  const [drop, setDrop] = useState(10);
  const [uplift, setUplift] = useState(0);
  const [rate, setRate] = useState("");
  const [days, setDays] = useState("30");
  const [currency, setCurrency] = useState("USD");
  const [basis, setBasis] = useState(360);
  const [showMethod, setShowMethod] = useState(false);
  const balances = (risk.cashBalances || []).filter(item => item.currency !== "BASE");
  const selectedBalance = balances.find(item => item.currency === currency) || balances[0];
  const unit = risk.currency || "USD";
  const amount = (value: Amount, curr = unit, digits = 0) => hidden ? "••••••" : fmt(value, curr, digits);
  const ratio = (value: Amount) => hidden ? "•••" : percent(value);
  const number = (value: Amount, digits = 2) => hidden ? "•••" : valid(value) ? value.toLocaleString("en-US", { maximumFractionDigits: digits }) : "—";
  const sourceName = (value?: string) => value === "derived" ? "根据快照字段推导" : value === "missing" ? "字段未提供" : accountSource === "manual" ? "手工账户估值" : "IBKR 返回值";
  const simulate = (loss: number) => {
    const pnl = valid(risk.stockExposure) ? -risk.stockExposure * loss / 100 : null;
    const nav = valid(pnl) && valid(risk.netLiquidation) ? risk.netLiquidation + pnl : null;
    const extraMargin = valid(risk.maintenanceMargin) ? risk.maintenanceMargin * uplift / 100 : null;
    const excess = valid(pnl) && valid(risk.excessLiquidity) && valid(extraMargin) ? risk.excessLiquidity + pnl - extraMargin : null;
    return { pnl, nav, excess, cushion: valid(excess) && valid(nav) && nav > 0 ? excess / nav * 100 : null };
  };
  const custom = simulate(drop);
  const rowLosses = [...new Set([0, 5, 10, 20, 30, drop])].sort((a, b) => a - b);
  const principal = selectedBalance?.debit ?? null;
  const annualRate = rate.trim() === "" ? null : Number(rate);
  const dayCount = days.trim() === "" ? null : Number(days);
  const rateValid = valid(annualRate) && annualRate >= 0 && annualRate <= 100;
  const daysValid = valid(dayCount) && Number.isInteger(dayCount) && dayCount >= 1 && dayCount <= 3650;
  const interest = valid(principal) && principal >= 0 && rateValid && daysValid ? principal * annualRate / 100 * dayCount / basis : null;
  const annualInterest = valid(principal) && rateValid ? principal * annualRate / 100 : null;
  const dailyInterest = valid(annualInterest) ? annualInterest / basis : null;
  const deficit = valid(custom.excess) && custom.excess < 0;
  const check = risk.reconciliation;

  return <div className="margin-risk">
    <div className="margin-risk-caption"><span><ShieldCheck size={15} />账户风控 · {unit}</span><small>{accountSource === "manual" ? "手工持仓确认" : "IBKR 快照"} {snapshotTime(risk.asOf)}（北京时间）· {accountSource === "manual" ? "持仓与现金需手动维护，缺少的保证金字段留空" : "刷新行情不会同步账户"}</small></div>

    <div className="margin-risk-tiles">
      <div className="margin-risk-tile margin-risk-blue"><span>剩余流动性</span><strong className={!hidden && valid(risk.excessLiquidity) && risk.excessLiquidity < 0 ? "down" : ""}>{amount(risk.excessLiquidity)}</strong><small>{sourceName(risk.excessLiquiditySource)}</small></div>
      <div className="margin-risk-tile margin-risk-lilac"><span>保证金缓冲率</span><strong>{ratio(risk.cushionPct)}</strong><small>剩余流动性 / 账户净资产</small></div>
      <div className="margin-risk-tile margin-risk-peach"><span>维持保证金</span><strong>{amount(risk.maintenanceMargin)}</strong><small>占可贷款权益 {ratio(risk.maintenanceUsagePct)}</small></div>
      <div className="margin-risk-tile margin-risk-white"><span>总持仓杠杆</span><strong>{hidden ? "•••" : valid(risk.leverage) ? `${risk.leverage.toFixed(2)}×` : "—"}</strong><small>总持仓价值 / 账户净资产</small></div>
    </div>

    <div className="margin-risk-upper">
      <section className="panel margin-risk-balances">
        <div className="panel-head"><h2><Landmark size={16} />账户资金</h2><small>账户级 · {unit}</small></div>
        <dl className="margin-risk-facts">
          {[["账户净资产", risk.netLiquidation], ["可贷款权益", risk.equityWithLoanValue], ["初始保证金", risk.initialMargin], ["可用资金", risk.availableFunds], ["总持仓价值", risk.grossPositionValue], ["现金净额", risk.cash], ["购买力", risk.buyingPower]].map(([label, value]) => <div key={String(label)}><dt>{String(label)}</dt><dd>{amount(value as Amount)}</dd></div>)}
          <div><dt>净现金负债 / 净资产</dt><dd>{ratio(risk.cashDebtRatioPct)}</dd></div>
        </dl>
        <div className="margin-risk-footnote">购买力包含融资额度；现金净额是各币种折算后的净额。下方按币种查看已结算借款，避免净额抵消。</div>
      </section>

      <section className="panel margin-risk-interest">
        <div className="panel-head"><h2><Calculator size={16} />融资利息估算</h2><small>填写实际年利率</small></div>
        <div className="margin-risk-inputs">
          <label>借款币种<select aria-label="融资借款币种" value={selectedBalance?.currency || ""} onChange={event => { setCurrency(event.target.value); setBasis(event.target.value === "USD" ? 360 : 365); }} disabled={!balances.length}>{balances.length ? balances.map(item => <option key={item.currency} value={item.currency}>{item.currency}</option>) : <option value="">暂无币种数据</option>}</select></label>
          <label>年利率（%）<input type="number" min="0" max="100" step="0.01" aria-label="融资年利率" inputMode="decimal" placeholder="输入年利率" value={rate} onChange={event => setRate(event.target.value)} /></label>
          <label>计息天数<input type="number" min="1" max="3650" step="1" aria-label="融资计息天数" inputMode="numeric" value={days} onChange={event => setDays(event.target.value)} /></label>
          <label>年计息基数<select aria-label="年计息基数" value={basis} onChange={event => setBasis(Number(event.target.value))}><option value={360}>360 天</option><option value={365}>365 天</option></select></label>
        </div>
        <div className="margin-risk-interest-base"><span>已结算现金负额 · 估算本金</span><b>{amount(principal, selectedBalance?.currency || unit, 2)}</b></div>
        <div className="margin-risk-interest-total"><span>所选天数预计利息</span><strong>{amount(interest, selectedBalance?.currency || unit, 2)}</strong></div>
        <div className="margin-risk-interest-breakdown"><span>每日 {amount(dailyInterest, selectedBalance?.currency || unit, 2)}</span><span>年化 {amount(annualInterest, selectedBalance?.currency || unit, 2)}</span></div>
        {!rateValid && rate !== "" && <p className="margin-risk-validation" role="status">请输入 0–100 之间的有限年利率。</p>}
        {!daysValid && <p className="margin-risk-validation" role="status">请输入 1–3650 之间的整数天数。</p>}
        <p className="margin-risk-footnote">本金 × 年利率 × 天数 / 基数，假设本金与利率不变。利率未自动接入；IBKR 分档利率、计息调整及最终账单可能不同。<a href="https://www.interactivebrokers.com/en/trading/margin-rates.php" target="_blank" rel="noreferrer">查询官方费率 ↗</a></p>
      </section>
    </div>

    <section className="panel margin-risk-stress">
      <div className="panel-head"><h2>融资压力测试</h2><small>股票市值 / 券商总持仓价值 {ratio(risk.stressCoveragePct)}</small></div>
      <div className="margin-risk-stress-body">
        <div className="margin-risk-sliders">
          <label htmlFor="account-stock-drop"><span>已覆盖股票同步下跌<b>{drop}%</b></span><input id="account-stock-drop" aria-label="股票同步下跌幅度" type="range" min="0" max="50" step="1" value={drop} onChange={event => setDrop(Number(event.target.value))} /><small><span>0%</span><span>50%</span></small></label>
          <label htmlFor="account-margin-uplift"><span>维持保证金金额提高<b>{uplift}%</b></span><input id="account-margin-uplift" aria-label="维持保证金金额提高幅度" type="range" min="0" max="100" step="5" value={uplift} onChange={event => setUplift(Number(event.target.value))} /><small><span>保持当前金额</span><span>提高一倍</span></small></label>
        </div>
        <div className={`margin-risk-result ${!hidden && deficit ? "margin-risk-deficit" : ""}`} aria-live="polite">
          <div><span>情景净资产</span><strong>{amount(custom.nav)}</strong></div>
          <div><span>情景剩余流动性</span><strong>{amount(custom.excess)}</strong></div>
          <div><span>情景保证金缓冲率</span><strong>{ratio(custom.cushion)}</strong></div>
          {!hidden && deficit && <p>此假设下剩余流动性为负。</p>}
        </div>
      </div>
      <p className="margin-risk-stress-note"><CircleHelp size={14} /><span>股票按市值线性估算损益，其他头寸与汇率保持不变。维持保证金先固定为当前金额，再增加所选比例；实际要求会随行情、集中度及券商规则变化。缓冲率不是股价可承受跌幅，此处不预测实际强平价。</span></p>
      {!valid(risk.stockExposure) && <p className="margin-risk-validation">缺少可覆盖股票敞口，暂不能计算下跌情景。</p>}
      {risk.omittedPositions?.length > 0 && <p className="margin-risk-validation">以下头寸未纳入价格冲击：{risk.omittedPositions.map(item => `${item.symbol}（${item.reason}）`).join("、")}。</p>}
      <div className="table-scroll margin-risk-scenarios"><table><thead><tr><th>股票同步下跌</th><th>估算损益</th><th>情景净资产</th><th>剩余流动性</th><th>缓冲率</th></tr></thead><tbody>{rowLosses.map(loss => { const scenario = simulate(loss); return <tr key={loss} className={loss === drop ? "margin-risk-selected" : ""}><td>{loss === 0 ? "当前价格" : `−${loss}%`}{loss === drop && <span className="margin-risk-row-tag">所选</span>}</td><td className={!hidden && valid(scenario.pnl) && scenario.pnl < 0 ? "down" : ""}>{amount(scenario.pnl)}</td><td>{amount(scenario.nav)}</td><td className={!hidden && valid(scenario.excess) && scenario.excess < 0 ? "down" : ""}>{amount(scenario.excess)}</td><td>{ratio(scenario.cushion)}</td></tr>; })}</tbody></table></div>
    </section>

    <section className="panel margin-risk-currency">
      <div className="panel-head"><h2>各币种现金与借款</h2><small>BASE 汇总已排除</small></div>
      {risk.cashBalancesAvailable && balances.length > 0 ? <div className="table-scroll"><table><thead><tr><th>币种</th><th>现金余额</th><th>已结算现金</th><th>已结算借款</th><th>借款折 {unit}</th></tr></thead><tbody>{balances.map(item => <tr key={item.currency}><td>{item.currency}</td><td>{amount(item.cash, item.currency, 2)}</td><td>{amount(item.settledCash, item.currency, 2)}</td><td>{amount(item.debit, item.currency, 2)}</td><td>{amount(item.baseDebit, unit, 2)}</td></tr>)}</tbody></table></div> : <div className="margin-risk-empty">暂未取得分币种现金余额，不能据此判断没有融资借款。</div>}
      {risk.cashBalancesAvailable && !risk.debtByCurrencyAvailable && <p className="margin-risk-footnote">部分币种缺少已结算现金；该币种计息本金与折算借款留空。</p>}
    </section>

    <section className="panel margin-risk-positions">
      <div className="panel-head"><h2>全账户头寸</h2><small>账户风控包含全部已返回头寸</small></div>
      {risk.positionsAvailable && risk.allPositions?.length > 0 ? <div className="table-scroll"><table><thead><tr><th>头寸</th><th>类别 / 币种</th><th>数量</th><th>市值（原币）</th><th>市值折 {unit}</th><th>占净资产</th><th>压力覆盖</th></tr></thead><tbody>{risk.allPositions.map((position, index) => <tr key={`${position.symbol}-${index}`}><td><b>{position.symbol}</b></td><td>{position.assetClass} · {position.currency}</td><td>{number(position.quantity, 4)}</td><td>{amount(position.marketValue, position.currency)}</td><td>{amount(position.baseMarketValue)}</td><td>{ratio(position.weightPct)}</td><td><span className={`margin-risk-coverage ${position.covered ? "" : "margin-risk-uncovered"}`}>{position.covered ? <><Check size={12} />已覆盖</> : "未覆盖"}</span></td></tr>)}</tbody></table></div> : <div className="margin-risk-empty">{risk.positionsAvailable ? "账户快照未返回开放头寸。" : "尚未取得完整账户头寸。"}</div>}
    </section>

    <section className="panel margin-risk-method">
      <button className="margin-risk-method-toggle" onClick={() => setShowMethod(!showMethod)} aria-expanded={showMethod}><span><CircleHelp size={15} />计算口径与数据检查</span>{showMethod ? <ChevronUp size={16} /> : <ChevronDown size={16} />}</button>
      {showMethod && <div className="margin-risk-method-body">
        <p>证券账户：剩余流动性 = 可贷款权益 − 维持保证金；可用资金 = 可贷款权益 − 初始保证金。保证金缓冲率 = 剩余流动性 / 净资产。存在商品或其他账户分区时，字段口径可能不同；本页保留券商返回值。</p>
        <dl className="margin-risk-checks"><div><dt>剩余流动性推导值</dt><dd>{amount(check?.computedExcess ?? null, unit, 2)}</dd></div><div><dt>与返回值的差额</dt><dd>{amount(check?.difference ?? null, unit, 2)}</dd></div><div><dt>可用资金推导值</dt><dd>{amount(check?.computedAvailable ?? null, unit, 2)}</dd></div><div><dt>与返回值的差额</dt><dd>{amount(check?.availableDifference ?? null, unit, 2)}</dd></div></dl>
        <dl className="margin-risk-checks"><div><dt>逐项持仓市值合计</dt><dd>{amount(check?.positionsGrossValue ?? null, unit, 2)}</dd></div><div><dt>与券商总持仓值差额</dt><dd>{amount(check?.grossValueDifference ?? null, unit, 2)}</dd></div></dl>
        <p>剩余流动性来源：{sourceName(risk.excessLiquiditySource)}；可用资金来源：{sourceName(risk.availableFundsSource)}。现金负债比仅描述净现金负额，不替代分币种借款总额。</p>
        {!!risk.warnings?.length && (hidden ? <p>金额已隐藏；关闭隐私模式后可查看账户数据检查提示。</p> : <ul>{risk.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>)}
        <div className="margin-risk-source-links"><a href="https://www.ibkrguides.com/ibkrdesktop/available-for-trading.htm" target="_blank" rel="noreferrer">IBKR 账户字段说明 ↗</a><a href="https://www.interactivebrokers.com/campus/glossary-terms/margin-alerts/" target="_blank" rel="noreferrer">IBKR 缓冲率公式 ↗</a></div>
      </div>}
    </section>
  </div>;
}
