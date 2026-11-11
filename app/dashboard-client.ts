import type { Data } from "./dashboard-types";

type RequestOptions = {
  fetcher?: typeof fetch;
  wait?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
  attempts?: number;
};

const record=(value:unknown):value is Record<string,unknown>=>Boolean(value)&&typeof value==='object'&&!Array.isArray(value);
const finite=(value:unknown)=>typeof value==='number'&&Number.isFinite(value);
const nullable=(value:unknown)=>value===null||finite(value);
const strings=(value:Record<string,unknown>,keys:string[])=>keys.every(key=>typeof value[key]==='string');
const records=(value:unknown,check:(item:Record<string,unknown>)=>boolean)=>Array.isArray(value)&&value.every(item=>record(item)&&check(item));

function isDashboard(value:unknown):value is Data{
 if(!record(value)||!strings(value,['generatedAt','marketAsOf','marketSource','engine']))return false;
 const portfolio=value.portfolio,benchmark=value.benchmark;
 if(!record(portfolio)||!strings(portfolio,['asOf'])||!['netLiquidation','cash','leverage','sevenMarketValue','sevenPnl'].every(key=>nullable(portfolio[key]))||
    !records(portfolio.positions,item=>strings(item,['symbol'])&&['quantity','averagePrice','marketPrice','marketValue','pnl','returnPct','weightPct'].every(key=>nullable(item[key]))))return false;
 if(!record(benchmark)||!finite(benchmark.close)||!finite(benchmark.changePct))return false;
 if(!records(value.fundamentals,item=>strings(item,['symbol','asOf','summary','source']))||
    !records(value.sources,item=>strings(item,['name','status','asOf','detail']))||!records(value.rules,item=>strings(item,['name','description'])))return false;
 return Array.isArray(value.stocks)&&value.stocks.length>0&&records(value.stocks,stock=>
  strings(stock,['symbol','name','sector','asOf','status','observation'])&&finite(stock.close)&&finite(stock.changePct)&&
  ['ret20','ret60','ma20','ma50','ma200','rsi','volatility','atr','relative20'].every(key=>nullable(stock[key]))&&
  records(stock.signals,signal=>strings(signal,['id','label','tone','description']))&&Array.isArray(stock.history)&&stock.history.length>0&&
  records(stock.history,bar=>strings(bar,['date'])&&['open','high','low','close'].every(key=>finite(bar[key]))&&['volume','ma20','ma50','ma200'].every(key=>nullable(bar[key]))));
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export async function readDashboard(signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<Data> {
  signal.throwIfAborted();
  const response = await fetcher("/api/dashboard", { cache: "no-store", signal });
  let data:unknown;
  try{data=await response.json()}catch{throw new Error("行情数据格式异常，请刷新行情后重试。");}
  if (!response.ok) throw new Error(record(data)&&typeof data.error==='string'?data.error:"读取本机数据失败，请重试。");
  if (!isDashboard(data)) throw new Error("行情数据格式异常，请刷新行情后重试。");
  return data;
}

// Refresh status is intentionally separate from the complete price-history payload.
export async function refreshDashboard(signal: AbortSignal, options: RequestOptions = {}): Promise<Data> {
  const { fetcher = fetch, wait = abortableDelay, attempts = 125 } = options;
  signal.throwIfAborted();
  const response = await fetcher("/api/refresh", { method: "POST", signal });
  if (response.status === 409 || response.status === 202) {
    let completed = false;
    for (let attempt = 0; attempt < attempts; attempt++) {
      await wait(2000, signal);
      signal.throwIfAborted();
      const statusResponse = await fetcher("/api/refresh-status", { cache: "no-store", signal });
      const status = await statusResponse.json() as { running?: boolean; lastError?: string | null; error?: string };
      if (!statusResponse.ok || typeof status.running !== "boolean") throw new Error(status.error || "无法读取行情同步进度，请稍后重试。");
      if (!status.running) {
        if (status.lastError) throw new Error(status.lastError);
        completed = true;
        break;
      }
    }
    if (!completed) throw new Error("后台仍在获取行情，请稍后重试。");
  } else {
    const result = await response.json() as { ok?: boolean; error?: string };
    if (!response.ok || !result.ok) throw new Error(result.error || "行情刷新失败，保留上次数据。");
  }
  return readDashboard(signal, fetcher);
}

export function refreshNotice(data: Data): string {
  if (data.quoteRefresh?.errors.length) return `行情刷新结束：${data.quoteRefresh.errors.length} 个标的获取失败，缓存或缺失原因见清单说明。持仓数量与现金仍由所选账户来源提供。`;
  if (data.marketWarnings?.length) return "行情已重新获取，但数据源仍有缺项，可在右上角“行情状态”查看明细。持仓数量和现金不会随行情刷新而改变。";
  return `行情同步完成：有效日线截至 ${data.marketAsOf}。软件每天自动更新一次，也可随时手动刷新；账户持仓需单独维护。`;
}
