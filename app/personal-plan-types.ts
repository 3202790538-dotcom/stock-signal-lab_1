export type PersonalSettings = {
  accountSource: 'broker' | 'manual';
  allocationPct: number;
  targetReturnPct: number;
  maxDrawdownPct: number;
  templateId: 'long_term' | 'pullback' | 'rotation';
  allowProfitAdd: boolean;
  reserveCashPct: number;
};
export type SettingsDraft = Omit<PersonalSettings, 'allocationPct' | 'targetReturnPct' | 'maxDrawdownPct' | 'reserveCashPct'> & {
  allocationPct: string; targetReturnPct: string; maxDrawdownPct: string; reserveCashPct: string;
};
export type ManualPosition = {symbol: string; quantity: number; averageCost: number; markPrice?: number | null; stopPrice?: number | null; sector: 'semiconductor' | 'other' | 'unknown'};
export type ManualAccount = {cash: number; positions: ManualPosition[]; updatedAt: string | null};
export type ManualPositionDraft = {symbol: string; quantity: string; averageCost: string; markPrice: string; stopPrice: string; sector: ManualPosition['sector']};
export type DailySyncStatus = {
  running: boolean; lastAttemptAt: string | null; lastSuccessAt: string | null; nextRunAt: string | null;
  channels: Record<string, {status: 'idle' | 'running' | 'success' | 'skipped' | 'error'; lastAttemptAt: string | null; lastSuccessAt: string | null; lastError: string | null; updatedCount: number}>;
};
export const defaultSettings: PersonalSettings = {accountSource: 'broker', allocationPct: 100, targetReturnPct: 50, maxDrawdownPct: 30, templateId: 'long_term', allowProfitAdd: true, reserveCashPct: 0};
export const settingsDraft = (value: PersonalSettings): SettingsDraft => ({...value, allocationPct: String(value.allocationPct), targetReturnPct: String(value.targetReturnPct), maxDrawdownPct: String(value.maxDrawdownPct), reserveCashPct: String(value.reserveCashPct)});
export const manualDraft = (value: ManualPosition): ManualPositionDraft => ({symbol:value.symbol, quantity:String(value.quantity), averageCost:String(value.averageCost), markPrice:value.markPrice == null ? '' : String(value.markPrice), stopPrice:value.stopPrice == null ? '' : String(value.stopPrice), sector:value.sector});
const numeric = (value: string, label: string, min = -Infinity, max = Infinity) => {
  if (!value.trim() || !Number.isFinite(Number(value))) throw new Error(`${label}必须是有效数字。`);
  const result=Number(value);
  if (result < min || result > max) throw new Error(`${label}须在 ${min}–${max} 之间。`);
  return result;
};
export function parseSettingsDraft(value: SettingsDraft): PersonalSettings {
  if (!['broker','manual'].includes(value.accountSource) || !['long_term','pullback','rotation'].includes(value.templateId) || typeof value.allowProfitAdd !== 'boolean') throw new Error('账户来源或策略设置无效。');
  return {...value, allocationPct:numeric(value.allocationPct,'策略资金比例',0,100), targetReturnPct:numeric(value.targetReturnPct,'年度收益目标',0,200), maxDrawdownPct:numeric(value.maxDrawdownPct,'最大回撤容忍',1,80), reserveCashPct:numeric(value.reserveCashPct,'现金保留比例',0,100)};
}
export function parseManualDraft(cash: string, rows: ManualPositionDraft[]): Omit<ManualAccount,'updatedAt'> {
  const symbols=new Set<string>();
  const positions=rows.map((row,index) => {
    const label=`第 ${index+1} 行`;const symbol=row.symbol.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9-]{0,14}$/.test(symbol)) throw new Error(`${label}请输入有效美股代码，例如 AAPL、BRK-B。`);
    if (symbols.has(symbol)) throw new Error(`${label}代码 ${symbol} 重复，请合并为一个持仓。`);
    symbols.add(symbol);
    if (!['semiconductor','other','unknown'].includes(row.sector)) throw new Error(`${label}行业分类无效。`);
    const positive=(value:string,name:string) => {const parsed=numeric(value,`${label}${name}`);if(parsed<=0)throw new Error(`${label}${name}必须大于 0。`);return parsed;};
    return {symbol,quantity:positive(row.quantity,'股数'),averageCost:positive(row.averageCost,'平均成本'),...(row.markPrice.trim()?{markPrice:positive(row.markPrice,'手工估值价')}:{}),...(row.stopPrice.trim()?{stopPrice:positive(row.stopPrice,'计划止损价')}:{}),sector:row.sector};
  });
  return {cash:numeric(cash,'美元现金'),positions};
}
export const personalError = (value: unknown): string => {
  if (value instanceof Error) return value.message;
  if (value && typeof value === 'object') {
    const body=value as {error?:unknown;detail?:unknown};
    if (typeof body.error === 'string') return body.error;
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((entry: {msg?:string;loc?:unknown[]}) => `${entry.loc?.join('.') || ''} ${entry.msg || '输入无效'}`).join('；');
  }
  return '服务暂时不可用，请稍后重试。';
};
export async function personalRequest<T>(path: string, init: RequestInit = {}, fetcher: typeof fetch = fetch): Promise<T> {
  init.signal?.throwIfAborted();
  const response=await fetcher(path,{cache:'no-store',...init,headers:{'Content-Type':'application/json',...init.headers}});
  let payload: unknown;
  try {payload=await response.json();} catch {throw new Error(`服务响应格式异常（HTTP ${response.status}），请重试。`);}
  init.signal?.throwIfAborted();
  if (!response.ok) throw new Error(personalError(payload));
  if (payload === null || typeof payload !== 'object') throw new Error('服务响应格式异常，请重试。');
  return payload as T;
}
export const formatNumber = (value: number | null | undefined, digits=2) => typeof value==='number' && Number.isFinite(value) ? value.toLocaleString('zh-CN',{maximumFractionDigits:digits}) : '待核验';
export const formatMoney = (value: number | null | undefined, hidden=false, digits=0) => hidden ? '••••••' : typeof value==='number' && Number.isFinite(value) ? `$${formatNumber(value,digits)}` : '待核验';
export const formatTime = (value: string | null | undefined) => {
  if (!value) return '尚无记录';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date=new Date(value);
  return Number.isNaN(date.getTime()) ? '时间待核验' : date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
};
export type PersonalPlanItem = {
  id: string; symbol: string; contractId: string | number | null; origin: 'holding' | 'watchlist'; name: string; groupIds: string[];
  holding: {quantity: number | null; averageCost: number | null; marketPrice: number | null; marketValue: number | null; stopPrice: number | null} | null;
  status: 'verify' | 'hold' | 'reduce' | 'exit' | 'candidate' | 'conditional' | 'blocked' | 'wait'; label: string; rationale: string;
  conditions: {label: string; passed: boolean | null; observed?: unknown; required?: unknown}[];
  blockReasons: string[]; marketAsOf: string | null;
  entry: {referencePrice: number | null; lowerPrice: number | null; upperPrice: number | null; proposedStop: number | null; riskPerShare: number | null} | null;
  quantity: number | null; quantityStatus: 'unknown' | 'zero' | 'conditional' | 'not_applicable'; theoreticalMaxQuantity: number | null;
  sizingLimits: {label: string; quantity: number | null}[];
  reduction: {quantity: number | null; referencePrice: number | null; reason: string} | null;
  nextReview: string; cancelConditions: string[]; warnings: string[];
  verification?: {state: 'ready' | 'unsupported' | 'insufficient_history' | 'needs_data' | 'account_required' | 'ranking_incomplete'; availableBars: number; requiredBars: number; source?: {source?:string; url?:string; checkedAt?:string}};
};
export type PersonalPlanData = {
  version: string | number; generatedAt: string; settings: PersonalSettings;
  freshness: {marketAsOf: string | null; marketFresh: boolean; accountFresh: boolean; expiresAt: string | null};
  account: {source: 'broker' | 'manual'; currency: string | null; asOf: string | null; netLiquidation: number | null; cash: number | null; availableFunds: number | null; excessLiquidity: number | null; grossPositionValue: number | null; openRiskValue: number | null; openRiskStatus: 'unknown' | 'planned' | 'empty'; currentDrawdownPct: number | null};
  allocation: {allocationPct: number; allocatedCapital: number | null; reserveCashValue: number | null; committedCapital: number | null; remainingCapital: number | null; overAllocatedCapital: number | null; tradeRiskPct: number; tradeRiskBudget: number | null; openRiskCapPct: number; openRiskBudget: number | null; annualTargetValue: number | null; drawdownBudgetValue: number | null};
  summary: {headline: string; holdingCount: number; candidateCount: number; conditionalCount: number; blockedCount: number; plannedBuyValue: number | null; plannedRiskValue: number | null; remainingCash: number | null; blockReasons: string[]};
  items: PersonalPlanItem[];
  rules: {label: string; entry: string[]; exit: string[]; sizing: string[]; timing: string};
  validation: {status: 'unvalidated'; targetReturnPct: number; maxDrawdownPct: number; conclusion: string; warnings: string[]};
  warnings: string[];
};

/** Reject truncated/HTML gateway responses before React attempts to render nested plan rows. */
export function parsePersonalPlanResponse(value: unknown): PersonalPlanData {
  const fail=()=>{throw new Error('计划数据格式不完整，请重新计算。');};
  const record=(entry:unknown):entry is Record<string,unknown>=>Boolean(entry)&&typeof entry==='object'&&!Array.isArray(entry);
  const strings=(entry:unknown)=>Array.isArray(entry)&&entry.every(item=>typeof item==='string');
  if(!record(value))return fail();
  for(const field of ['settings','freshness','account','allocation','summary','validation','rules'])if(!record(value[field]))return fail();
  const plan=value as unknown as PersonalPlanData;
  if(typeof plan.generatedAt!=='string'||!Array.isArray(plan.items)||!strings(plan.warnings)||!strings(plan.summary.blockReasons)||!strings(plan.validation.warnings)||!strings(plan.rules.entry)||!strings(plan.rules.exit)||!strings(plan.rules.sizing)||typeof plan.freshness.marketFresh!=='boolean'||typeof plan.freshness.accountFresh!=='boolean')return fail();
  if(typeof plan.summary.headline!=='string'||typeof plan.validation.conclusion!=='string'||typeof plan.rules.label!=='string'||typeof plan.rules.timing!=='string')return fail();
  for(const item of plan.items){
    if(!record(item)||typeof item.id!=='string'||typeof item.symbol!=='string'||typeof item.rationale!=='string'||typeof item.nextReview!=='string'||!Array.isArray(item.conditions)||!Array.isArray(item.sizingLimits)||!strings(item.blockReasons)||!strings(item.cancelConditions)||!strings(item.warnings))return fail();
    if(item.conditions.some(condition=>!record(condition)||typeof condition.label!=='string')||item.sizingLimits.some(limit=>!record(limit)||typeof limit.label!=='string'))return fail();
  }
  return plan;
}
