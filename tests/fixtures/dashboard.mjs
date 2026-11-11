export function dashboardFixture(){
 const history=['2026-11-06','2026-11-09'].map(date=>({date,open:100,high:102,low:99,close:101,volume:1000,ma20:null,ma50:null,ma200:null}));
 return {stocks:[{symbol:'NVDA',name:'NVIDIA',sector:'Semiconductors',currency:'USD',asOf:'2026-11-09',close:101,previousClose:100,changePct:1,
  ret20:null,ret60:null,ma20:null,ma50:null,ma200:null,rsi:null,volatility:null,atr:null,relative20:null,bars:2,status:'weak',signals:[],history,observation:'Historical data'}],
  generatedAt:'2026-11-10T10:00:00Z',marketAsOf:'2026-11-09',marketSource:'Yahoo Finance',engine:'Qlib',
  portfolio:{asOf:'2026-11-10T10:00:00Z',netLiquidation:null,cash:null,leverage:null,sevenMarketValue:null,sevenPnl:null,positions:[]},
  benchmark:{close:100,changePct:1},fundamentals:[],sources:[],rules:[]};
}
