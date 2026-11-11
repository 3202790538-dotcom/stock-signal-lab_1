import type {ProfileId, TemplateId} from "./complete-strategy-types";

/** Re-evaluate cached authorization against the clock without changing the underlying plan. */
export function getHoldingsFreshness(accountAsOf: string | null | undefined, marketStamp: string | null | undefined, now: number) {
  const deadline = (value: string | null | undefined) => {
    if (!value || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
    const stamp = Date.parse(value);
    return Number.isFinite(now) && Number.isFinite(stamp) && stamp <= now && now - stamp <= 86_400_000 ? stamp + 86_400_001 : null;
  };
  const accountDeadline = deadline(accountAsOf);
  const marketDeadline = deadline(marketStamp);
  const future = [accountDeadline, marketDeadline].filter((value): value is number => value !== null);
  return {accountFresh: accountDeadline !== null, marketFresh: marketDeadline !== null, nextExpiry: future.length ? Math.min(...future) : null};
}

export type HoldingsPlanRequest = {
  templateId: TemplateId;
  riskProfile: ProfileId;
  allowProfitAdd: boolean;
};

export function parseHoldingsPlanPreferences(raw: string | null, fallback: HoldingsPlanRequest): HoldingsPlanRequest {
  try {
    const value: unknown = JSON.parse(raw || "null");
    if (!value || typeof value !== "object" || Array.isArray(value)) return fallback;
    const saved = value as Record<string, unknown>;
    if (typeof saved.templateId !== "string" || !["breakout", "pullback", "rotation"].includes(saved.templateId) || typeof saved.riskProfile !== "string" || !["conservative", "balanced", "aggressive"].includes(saved.riskProfile) || typeof saved.allowProfitAdd !== "boolean") return fallback;
    return {templateId: saved.templateId as TemplateId, riskProfile: saved.riskProfile as ProfileId, allowProfitAdd: saved.allowProfitAdd};
  } catch {return fallback;}
}

export type HoldingsDecisionKind = "verify" | "review_exit" | "review_reduce" | "hold" | "watch_add";
export type HoldingsDataIssue = {code: string; label: string; action: string};
export type HoldingsCheckValue = string | number | boolean | null | {[key: string]: HoldingsCheckValue};
export type HoldingsCheck = {
  label: string;
  passed: boolean | null;
  observed: HoldingsCheckValue;
  required: HoldingsCheckValue;
};
export type HoldingsScenario = {
  id: "hold" | "add" | "reduce" | "exit";
  title: string;
  triggered: boolean;
  conditions: string[];
  timing: string;
  referencePrice: number | null;
  quantity: number | null;
  quantityNote: string;
};
export type HoldingsPlanItem = {
  identity: {
    key: string;
    symbol: string;
    contractId: string | number | null;
    name: string;
    currency: string | null;
    assetClass: string | null;
    instrumentType: string;
    quantity: number | null;
    averageCost: number | null;
    brokerPrice: number | null;
    brokerMarketValue: number | null;
    unrealizedPnl: number | null;
    weightPct: number | null;
    semiGroup: boolean;
  };
  decision: {kind: HoldingsDecisionKind; label: string; timing: string; summary: string};
  checks: HoldingsCheck[];
  levels: {label: string; value: number | null; explanation: string}[];
  scenarios: HoldingsScenario[];
  warnings: string[];
  dataStatus: {
    kind: "ready" | "risk_only" | "verify";
    label: string;
    bars: number;
    marketAsOf: string | null;
    accountFresh: boolean;
    marketFresh: boolean;
    technicalEligible: boolean;
    issues?: HoldingsDataIssue[];
  };
};

export type HoldingsPlanData = {
  version: string;
  generatedAt: string;
  accountAsOf: string;
  marketAsOf: string;
  expectedMarketAsOf?: string;
  dataIssues?: HoldingsDataIssue[];
  settings: HoldingsPlanRequest & {
    templateLabel: string;
    riskLabel: string;
    parameters: Record<string, number>;
    caps: {grossCapPct: number; singleCapPct: number; sectorCapPct: number; tradeRiskPct: number; openRiskCapPct: number};
  };
  account: {
    currency: string | null;
    netLiquidation: number | null;
    cash: number | null;
    availableFunds: number | null;
    excessLiquidity: number | null;
    grossPositionValue: number | null;
    grossExposurePct: number | null;
    semiconductorValue: number | null;
    semiconductorExposurePct: number | null;
    grossCapValue: number | null;
    grossExcessValue: number | null;
    semiconductorCapValue: number | null;
    semiconductorExcessValue: number | null;
    openRiskConfirmed: boolean;
    asOf: string;
    effectiveAsOf?: string | null;
    fresh: boolean;
  };
  summary: {
    positionCount: number;
    counts: Record<HoldingsDecisionKind, number>;
    addBlocked: boolean;
    addBlockReasons: string[];
    headline: string;
  };
  items: HoldingsPlanItem[];
  warnings: string[];
  sources: {name: string; asOf: string | null; detail: string}[];
};

const rowIssueCopy: Record<string, {label: string; action: string}> = {
  account_stale: {label: "账户快照已过期", action: "人工同步账户后，再刷新计划。"},
  account_timestamp_invalid: {label: "账户时间未确认", action: "人工核对账户同步时间。"},
  market_stale: {label: "行情快照已过期", action: "刷新行情后，再查看计划。"},
  market_timestamp_invalid: {label: "行情时间未确认", action: "刷新行情并核对更新时间。"},
  latest_bar_missing: {label: "缺最新日线", action: "刷新行情；完整日线恢复后重算。"},
  market_refresh_issue: {label: "行情更新未完成", action: "刷新行情；仍异常时展开检查。"},
  core_quote_issue: {label: "行情有缺项", action: "刷新行情；缺项补齐后重算。"},
  benchmark_quote_stale: {label: "基准行情已过期", action: "刷新基准行情后重算。"},
  position_quote_issue: {label: "个股行情异常", action: "刷新行情；仍异常时展开检查。"},
  position_date_mismatch: {label: "行情日期不一致", action: "刷新行情并核对交易日。"},
  position_quote_missing: {label: "缺个股行情", action: "刷新行情；仍缺失时展开检查。"},
  identity_mismatch: {label: "合约信息不一致", action: "核对合约、币种与头寸。"},
  broker_value_inconsistent: {label: "账户估值不一致", action: "人工核对数量、报价与市值。"},
  history_missing: {label: "缺历史日线", action: "刷新行情并检查历史覆盖。"},
  history_insufficient: {label: "日线样本不足", action: "等待样本累积，先复核账户风险。"},
  history_invalid: {label: "日线数据异常", action: "展开缺项，核对价格数据。"},
  history_volume_invalid: {label: "成交量数据异常", action: "刷新行情并核对成交量。"},
  history_calendar_mismatch: {label: "交易日未对齐", action: "核对个股与基准交易日。"},
  etf_risk_only: {label: "仅支持账户风险复核", action: "展开查看持仓与账户限制。"},
  instrument_unverified: {label: "证券类型未确认", action: "人工核对合约与证券类型。"},
};

/** Compact row copy; preserve the complete issue for banners and expanded evidence. */
export function getHoldingsRowState(item: Pick<HoldingsPlanItem, "decision" | "dataStatus">, currentIssue?: HoldingsDataIssue) {
  const blocked = Boolean(currentIssue) || item.dataStatus.kind === "verify" || item.decision.kind === "verify";
  const issue = blocked ? currentIssue || item.dataStatus.issues?.[0] || {code: "details_required", label: "资料尚未完整", action: "展开本项，查看缺失数据与逐项检查。"} : undefined;
  const compact = issue ? rowIssueCopy[issue.code] : undefined;
  const label = issue ? compact?.label || "数据需确认" : item.decision.label;
  const summary = issue ? currentIssue && issue.code !== "latest_bar_missing" ? "先处理上方数据提示，再刷新计划。" : compact?.action || "展开本项，查看完整原因与处理方法。" : item.decision.summary;
  return {blocked, kind: blocked ? "verify" as const : item.decision.kind, label, summary, issue};
}
