import type {Data} from './dashboard-types';

const fieldNames:Record<string,string>={open:'开盘价',high:'最高价',low:'最低价',close:'收盘价',dataRefresh:'本轮取数'};
export function marketDataHealth(data:Data){
  const rows=new Map<string,{symbol:string;asOf:string|null;reasons:Set<string>}>();
  const dates=[...new Set(data.stocks.map(stock=>stock.asOf).filter(Boolean))].sort();
  const quotes=new Map(data.stocks.map(stock=>[stock.symbol,stock]));
  const add=(symbol:string,reason:string)=>{
    const row=rows.get(symbol)||{symbol,asOf:symbol==='SPY'?data.benchmark.asOf||null:quotes.get(symbol)?.asOf||null,reasons:new Set<string>()};
    row.reasons.add(reason);rows.set(symbol,row);
  };
  for(const warning of data.marketWarnings||[]){
    const fields=warning.missingFields.filter(field=>field!=='dataRefresh');
    if(fields.length)add(warning.symbol,`${warning.date} 缺少${fields.map(field=>fieldNames[field]||field).join('、')}`);
    if(warning.missingFields.includes('dataRefresh'))add(warning.symbol,'本轮行情获取失败');
  }
  for(const error of data.quoteRefresh?.errors||[])add(error.symbol,error.message);
  for(const stock of data.stocks){
    if(stock.quoteStatus==='stale')add(stock.symbol,stock.quoteError||'本轮未取得新行情，沿用历史数据');
    if(!rows.has(stock.symbol)&&data.benchmark.asOf&&stock.asOf<data.benchmark.asOf)add(stock.symbol,`日线尚未与基准 ${data.benchmark.asOf} 对齐`);
  }
  return {
    dateLabel:dates.length>1?`${dates[0]} – ${dates.at(-1)}`:dates[0]||data.marketAsOf,
    totalCount:new Set([...quotes.keys(),...[...rows.keys()].filter(symbol=>symbol!=='SPY')]).size,
    availableCount:data.stocks.filter(stock=>!rows.has(stock.symbol)).length,
    benchmarkIssue:rows.has('SPY'),
    issues:[...rows.values()].map(row=>({...row,reason:[...row.reasons].join('；')})),
  };
}
