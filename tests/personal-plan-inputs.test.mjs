import assert from 'node:assert/strict';
import test from 'node:test';
import * as input from '../app/personal-plan-types.ts';

const settings = {accountSource:'broker',allocationPct:'100',targetReturnPct:'50',maxDrawdownPct:'30',templateId:'long_term',allowProfitAdd:true,reserveCashPct:'0'};
const row = {symbol:'nvda',quantity:'2.5',averageCost:'100',markPrice:'',stopPrice:'90',sector:'semiconductor'};

test('settings allow full-account zero allocation and reject blank/nonfinite or out-of-bound risk inputs', () => {
  assert.equal(typeof input.parseSettingsDraft, 'function');
  assert.equal(input.parseSettingsDraft({...settings,allocationPct:'0'}).allocationPct, 0);
  for (const [key,value] of [['allocationPct',''],['allocationPct','101'],['maxDrawdownPct','0'],['maxDrawdownPct','81'],['targetReturnPct','Infinity'],['targetReturnPct','201'],['reserveCashPct','-1']]) {
    assert.throws(() => input.parseSettingsDraft({...settings,[key]:value}));
  }
});

test('manual draft preserves negative cash and fractional holdings; optional blank prices stay absent', () => {
  assert.equal(typeof input.parseManualDraft, 'function');
  const result = input.parseManualDraft('-3.5',[row]);
  assert.equal(result.cash,-3.5);
  assert.deepEqual(result.positions,[{symbol:'NVDA',quantity:2.5,averageCost:100,stopPrice:90,sector:'semiconductor'}]);
});

test('manual input rejects partial rows, invalid prices and duplicate normalized ticker identities', () => {
  assert.equal(typeof input.parseManualDraft, 'function');
  for (const rows of [[{...row,symbol:''}],[{...row,symbol:'BRK.B'}],[{...row,quantity:''}],[{...row,averageCost:'0'}],[{...row,markPrice:'NaN'}],[{...row,stopPrice:'-1'}],[row,{...row,symbol:'NVDA'}]]) {
    assert.throws(() => input.parseManualDraft('100',rows));
  }
  assert.throws(() => input.parseManualDraft('',[]));
});

test('API errors retain actionable validation text and malformed JSON is not silently accepted', async () => {
  assert.equal(typeof input.personalRequest, 'function');
  const signal=new AbortController().signal;
  await assert.rejects(input.personalRequest('/test',{signal},async()=>new Response(JSON.stringify({error:'第 2 行代码重复'}),{status:422})),/代码重复/);
  await assert.rejects(input.personalRequest('/test',{signal},async()=>new Response('<html/>',{status:200})),/格式/);
});

test('aborted request cannot return stale payload even if fetch ignores abort', async () => {
  assert.equal(typeof input.personalRequest, 'function');
  const controller=new AbortController();
  await assert.rejects(input.personalRequest('/test',{signal:controller.signal},async()=>{controller.abort();return new Response('{}');}),{name:'AbortError'});
});

test('a malformed plan cannot replace the currently rendered plan', () => {
  assert.equal(typeof input.parsePersonalPlanResponse,'function');
  for(const value of [null,{}, {items:[]}, {freshness:{marketFresh:true,accountFresh:true},account:{},allocation:{},summary:{blockReasons:[]},validation:{warnings:[]},rules:{entry:[],exit:[],sizing:[]},warnings:[],items:[null]}]) {
    assert.throws(()=>input.parsePersonalPlanResponse(value),/计划数据格式/);
  }
});
