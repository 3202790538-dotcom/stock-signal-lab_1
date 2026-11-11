import assert from 'node:assert/strict';
import {test} from 'node:test';
import {marketDataHealth} from '../app/market-data-health.ts';
import {dashboardFixture} from './fixtures/dashboard.mjs';

test('mixed quote dates show a range and count delayed symbols without calling every quote stale',()=>{
 const data=dashboardFixture();
 data.stocks=[{...data.stocks[0],symbol:'NVDA',asOf:'2026-11-10'}, {...data.stocks[0],symbol:'GOOG',asOf:'2026-11-09'}];
 data.benchmark.asOf='2026-11-10';
 data.marketWarnings=[{symbol:'GOOG',date:'2026-11-10',missingFields:['close']}];
 const result=marketDataHealth(data);
 assert.equal(result.dateLabel,'2026-11-09 – 2026-11-10');
 assert.equal(result.totalCount,2);
 assert.equal(result.availableCount,1);
 assert.equal(result.issues.length,1);
 assert.match(result.issues[0].reason,/2026-11-10.*收盘价/);
 assert.equal(result.issues[0].asOf,'2026-11-09');
 assert.equal(data.marketWarnings.length,1);
});

test('duplicate missing fields and refresh failures share one symbol entry; missing quotes remain visible',()=>{
 const data=dashboardFixture();
 data.stocks[0].quoteStatus='stale';
 data.marketWarnings=[{symbol:'NVDA',date:'2026-11-10',missingFields:['close']}];
 data.quoteRefresh={errors:[{symbol:'NVDA',message:'取数失败',usedCache:true},{symbol:'NEW',message:'暂无有效行情',usedCache:false}],updatedSymbols:[]};
 const result=marketDataHealth(data);
 assert.equal(result.issues.length,2);
 assert.equal(result.totalCount,2);
 assert.equal(result.availableCount,0);
 assert.equal(result.issues.find(x=>x.symbol==='NEW').asOf,null);
 assert.match(result.issues[0].reason,/取数失败/);
});

test('an incomplete benchmark is flagged separately and healthy single-date data stays concise',()=>{
 const data=dashboardFixture();
 assert.equal(marketDataHealth(data).dateLabel,'2026-11-09');
 assert.equal(marketDataHealth(data).issues.length,0);
 data.marketWarnings=[{symbol:'SPY',date:'2026-11-10',missingFields:['close']}];
 assert.equal(marketDataHealth(data).benchmarkIssue,true);
});
